# -*- coding: utf-8 -*-
import importlib.util
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src" / "endstone_arc_core"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # 让包内相对导入可用：先把 endstone_arc_core 挂到 sys.path
    import sys

    src = str(_SRC.parent)
    if src not in sys.path:
        sys.path.insert(0, src)
    spec.loader.exec_module(mod)
    return mod


sync_plugin_api = _load("sync_plugin_api_under_test", _SRC / "sync_plugin_api.py")
sync_protocol = _load("sync_protocol_under_test", _SRC / "sync_protocol.py")


class PluginNameTests(unittest.TestCase):
    def test_logical_and_physical(self):
        logical = sync_plugin_api.make_logical_name("arc_guild", "guilds")
        self.assertEqual(logical, "arc_guild:guilds")
        self.assertEqual(
            sync_plugin_api.physical_from_logical(logical), "psync_arc_guild_guilds"
        )

    def test_reject_bad_names(self):
        with self.assertRaises(sync_plugin_api.PluginSyncError):
            sync_plugin_api.make_logical_name("Core", "t")
        with self.assertRaises(sync_plugin_api.PluginSyncError):
            sync_plugin_api.make_logical_name("ok", "Bad-Name")
        self.assertIsNone(sync_plugin_api.parse_logical_name("player_economy"))
        self.assertIsNone(sync_plugin_api.parse_logical_name("x:"))

    def test_is_plugin_logical(self):
        self.assertTrue(sync_plugin_api.is_plugin_logical_name("a:b"))
        self.assertFalse(sync_plugin_api.is_plugin_logical_name("guilds"))
        self.assertFalse(sync_plugin_api.is_plugin_logical_name(None))


class RegistryTests(unittest.TestCase):
    def test_register_and_dispatch(self):
        reg = sync_plugin_api.PluginSyncRegistry()
        calls = []

        def on_apply(ns, table, op, data):
            calls.append((ns, table, op, data))
            return True

        info = reg.register(
            "demo",
            {
                "items": {
                    "fields": {"id": "INTEGER PRIMARY KEY", "name": "TEXT"},
                    "primary_keys": ["id"],
                }
            },
            on_apply,
        )
        self.assertTrue(info["plugin_id"] == "demo")
        self.assertIn("demo:items", reg.logical_tables())
        self.assertEqual(
            reg.table_fields("demo:items")["name"], "TEXT"
        )
        payload = reg.auth_plugin_tables_payload()
        self.assertEqual(payload[0]["name"], "demo:items")
        self.assertTrue(reg.dispatch_apply("demo:items", "upsert", {"id": 1}))
        self.assertEqual(calls[0][0], "demo")
        self.assertEqual(calls[0][2], "upsert")
        self.assertTrue(reg.unregister("demo"))
        self.assertFalse(reg.dispatch_apply("demo:items", "upsert", {}))

    def test_fields_validation(self):
        reg = sync_plugin_api.PluginSyncRegistry()
        with self.assertRaises(sync_plugin_api.PluginSyncError):
            reg.register(
                "demo",
                {"t": {"fields": {"id": "INTEGER"}, "primary_keys": []}},
                lambda *a: True,
            )
        with self.assertRaises(sync_plugin_api.PluginSyncError):
            reg.register(
                "demo",
                {
                    "t": {
                        "fields": {"id": "INTEGER; DROP TABLE"},
                        "primary_keys": ["id"],
                    }
                },
                lambda *a: True,
            )


class ProtocolV4Tests(unittest.TestCase):
    def test_version(self):
        self.assertGreaterEqual(sync_protocol.PROTOCOL_VERSION, 4)

    def test_auth_and_data_request_carry_plugin_fields(self):
        raw = sync_protocol.build_auth_request(
            "s1",
            "n1",
            "key",
            ["player_economy"],
            plugin_tables=[
                {
                    "name": "demo:items",
                    "fields": {"id": "INTEGER PRIMARY KEY"},
                }
            ],
        )
        msg_type, data = sync_protocol.decode_message(raw)
        self.assertEqual(int(msg_type), int(sync_protocol.SyncMessageType.AUTH_REQUEST))
        self.assertEqual(data["plugin_tables"][0]["name"], "demo:items")

        raw2 = sync_protocol.build_data_request(
            sync_protocol.SyncMessageType.INSERT_REQUEST,
            0,
            {"id": 1},
            seq=9,
            table_name="demo:items",
        )
        _, data2 = sync_protocol.decode_message(raw2)
        self.assertEqual(data2["table_name"], "demo:items")
        self.assertEqual(data2["seq"], 9)

        raw3 = sync_protocol.build_push_notify(
            0, "insert", {"id": 1}, table_name="demo:items"
        )
        _, data3 = sync_protocol.decode_message(raw3)
        self.assertEqual(data3["table_name"], "demo:items")

        raw4 = sync_protocol.build_full_sync_request(0, table_name="demo:items")
        _, data4 = sync_protocol.decode_message(raw4)
        self.assertEqual(data4["table_name"], "demo:items")


class FakeDB:
    def __init__(self):
        self.tables = {}
        self.rows = {}

    def table_exists(self, name):
        return name in self.tables

    def create_table(self, name, fields):
        self.tables[name] = dict(fields)
        self.rows.setdefault(name, [])
        return True

    def upsert(self, name, row):
        rows = self.rows.setdefault(name, [])
        for i, r in enumerate(rows):
            if r.get("id") == row.get("id"):
                rows[i] = dict(row)
                return True
        rows.append(dict(row))
        return True

    def query_all(self, sql, params=()):
        # 仅支持 SELECT * FROM t
        sql_l = " ".join(sql.split()).lower()
        if sql_l.startswith("select * from "):
            t = sql_l.split("from ", 1)[1].split()[0]
            return list(self.rows.get(t, []))
        return []

    def suppress_write_notify(self):
        from contextlib import nullcontext

        return nullcontext()


class HubPluginTableTests(unittest.TestCase):
    def test_ensure_and_apply_via_helpers(self):
        db = FakeDB()
        logical = "demo:items"
        fields = {"id": "INTEGER PRIMARY KEY", "name": "TEXT"}
        phys = sync_plugin_api.physical_from_logical(logical)
        db.create_table(phys, fields)
        db.upsert(phys, {"id": 1, "name": "a"})
        rows = sync_plugin_api.select_all_physical_rows(db, phys)
        self.assertEqual(len(rows), 1)
        q = sync_plugin_api.query_physical_rows(db, phys, "id = ?", (1,))
        self.assertEqual(q[0]["name"], "a")
        self.assertEqual(
            sync_plugin_api.query_physical_rows(db, "bad;name", "id = ?", (1,)), []
        )

    def test_create_sql_single_pk(self):
        sql = sync_plugin_api.build_create_table_sql(
            "psync_demo_items",
            {"id": "INTEGER", "name": "TEXT"},
            ["id"],
        )
        self.assertIn("id INTEGER PRIMARY KEY", sql)
        self.assertIn("name TEXT", sql)
        self.assertTrue(sql.startswith("CREATE TABLE IF NOT EXISTS psync_demo_items"))

    def test_create_sql_composite_pk(self):
        sql = sync_plugin_api.build_create_table_sql(
            "psync_demo_pair",
            {"a": "INTEGER", "b": "INTEGER", "note": "TEXT"},
            ["a", "b"],
        )
        self.assertIn("PRIMARY KEY (a, b)", sql)

    def test_create_sql_rejects_injection_type(self):
        with self.assertRaises(sync_plugin_api.PluginSyncError):
            sync_plugin_api.build_create_table_sql(
                "psync_demo_items",
                {"id": "INTEGER); DROP TABLE player_economy; --"},
                ["id"],
            )

    def test_normalize_fields_rejects_semicolon(self):
        with self.assertRaises(sync_plugin_api.PluginSyncError):
            sync_plugin_api.normalize_fields(
                {"id": "INTEGER PRIMARY KEY, evil TEXT); DELETE FROM guilds; --"}
            )


if __name__ == "__main__":
    unittest.main()
