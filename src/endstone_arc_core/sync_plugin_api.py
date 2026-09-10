# -*- coding: utf-8 -*-
"""第三方插件跨服同步：命名空间注册、逻辑表名与回调分发。"""
from __future__ import annotations

import re
import threading
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

# 逻辑表名分隔符：plugin_id:table
LOGICAL_SEP = ":"
# 中心物理表前缀，避免与内置表冲突
PHYS_PREFIX = "psync_"

_NS_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_TABLE_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# 允许的 SQL 列类型片段（宽松，供 CREATE TABLE）
_COL_TYPE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_ ]*(\(\s*\d+\s*(,\s*\d+\s*)?\))?$")

# on_apply(namespace, table, op, data) -> bool
ApplyCallback = Callable[[str, str, str, Dict[str, Any]], bool]


class PluginSyncError(ValueError):
    """注册或同步参数不合法。"""


def validate_plugin_id(plugin_id: str) -> str:
    pid = str(plugin_id or "").strip().lower()
    if not _NS_RE.fullmatch(pid):
        raise PluginSyncError(f"invalid plugin_id: {plugin_id!r}")
    if pid == "core" or pid.startswith("psync"):
        raise PluginSyncError(f"reserved plugin_id: {pid!r}")
    return pid


def validate_table_name(table: str) -> str:
    name = str(table or "").strip().lower()
    if not _TABLE_RE.fullmatch(name):
        raise PluginSyncError(f"invalid table name: {table!r}")
    return name


def make_logical_name(plugin_id: str, table: str) -> str:
    return f"{validate_plugin_id(plugin_id)}{LOGICAL_SEP}{validate_table_name(table)}"


def parse_logical_name(logical: str) -> Optional[Tuple[str, str]]:
    raw = str(logical or "").strip()
    if LOGICAL_SEP not in raw:
        return None
    pid, _, table = raw.partition(LOGICAL_SEP)
    try:
        return validate_plugin_id(pid), validate_table_name(table)
    except PluginSyncError:
        return None


def is_plugin_logical_name(name: Any) -> bool:
    return isinstance(name, str) and LOGICAL_SEP in name


def physical_table_name(plugin_id: str, table: str) -> str:
    pid = validate_plugin_id(plugin_id)
    t = validate_table_name(table)
    return f"{PHYS_PREFIX}{pid}_{t}"


def physical_from_logical(logical: str) -> Optional[str]:
    parsed = parse_logical_name(logical)
    if not parsed:
        return None
    return physical_table_name(parsed[0], parsed[1])


def normalize_fields(fields: Any) -> Dict[str, str]:
    if not isinstance(fields, dict) or not fields:
        raise PluginSyncError("table fields must be a non-empty dict")
    out: Dict[str, str] = {}
    for col, typedef in fields.items():
        c = str(col).strip()
        t = " ".join(str(typedef or "").split())
        if not _IDENT_RE.fullmatch(c):
            raise PluginSyncError(f"invalid column name: {col!r}")
        if not t or not _COL_TYPE_RE.fullmatch(t):
            raise PluginSyncError(f"invalid column type for {c}: {typedef!r}")
        out[c] = t
    return out


def normalize_primary_keys(primary_keys: Any, fields: Dict[str, str]) -> List[str]:
    pks_raw = primary_keys or []
    if not isinstance(pks_raw, (list, tuple)) or not pks_raw:
        raise PluginSyncError("primary_keys required")
    pks: List[str] = []
    for pk in pks_raw:
        pk_s = str(pk).strip()
        if pk_s not in fields:
            raise PluginSyncError(f"primary key {pk_s!r} not in fields")
        pks.append(pk_s)
    return pks


def build_create_table_sql(
    physical: str, fields: Dict[str, str], primary_keys: List[str]
) -> str:
    """生成 CREATE TABLE IF NOT EXISTS；主键落到列或表级约束。"""
    if not physical or not _IDENT_RE.fullmatch(physical):
        raise PluginSyncError(f"invalid physical table: {physical!r}")
    fields = normalize_fields(fields)
    pks = normalize_primary_keys(primary_keys, fields)
    has_inline_pk = any("PRIMARY KEY" in t.upper() for t in fields.values())
    parts: List[str] = []
    for col, typedef in fields.items():
        if not has_inline_pk and pks == [col]:
            parts.append(f"{col} {typedef} PRIMARY KEY")
        else:
            parts.append(f"{col} {typedef}")
    if not has_inline_pk and len(pks) > 1:
        parts.append("PRIMARY KEY (" + ", ".join(pks) + ")")
    return "CREATE TABLE IF NOT EXISTS " + physical + " (" + ", ".join(parts) + ")"


def normalize_table_defs(tables: Any) -> Dict[str, Dict[str, Any]]:
    if not isinstance(tables, dict) or not tables:
        raise PluginSyncError("tables must be a non-empty dict")
    out: Dict[str, Dict[str, Any]] = {}
    for table, meta in tables.items():
        name = validate_table_name(table)
        if not isinstance(meta, dict):
            raise PluginSyncError(f"table meta for {table!r} must be a dict")
        fields = normalize_fields(meta.get("fields"))
        pks = normalize_primary_keys(meta.get("primary_keys"), fields)
        out[name] = {"fields": fields, "primary_keys": pks}
    return out


class PluginNamespace:
    __slots__ = ("plugin_id", "tables", "on_apply")

    def __init__(self, plugin_id: str, tables: Dict[str, Dict[str, Any]], on_apply: ApplyCallback):
        self.plugin_id = plugin_id
        self.tables = tables
        self.on_apply = on_apply


class PluginSyncRegistry:
    """线程安全的插件同步命名空间注册表。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._namespaces: Dict[str, PluginNamespace] = {}

    def register(
        self,
        plugin_id: str,
        tables: Any,
        on_apply: ApplyCallback,
    ) -> Dict[str, Any]:
        pid = validate_plugin_id(plugin_id)
        if not callable(on_apply):
            raise PluginSyncError("on_apply must be callable")
        defs = normalize_table_defs(tables)
        with self._lock:
            self._namespaces[pid] = PluginNamespace(pid, defs, on_apply)
        return {
            "plugin_id": pid,
            "tables": sorted(defs.keys()),
            "logical_tables": sorted(f"{pid}{LOGICAL_SEP}{t}" for t in defs),
        }

    def unregister(self, plugin_id: str) -> bool:
        pid = str(plugin_id or "").strip().lower()
        with self._lock:
            return self._namespaces.pop(pid, None) is not None

    def get(self, plugin_id: str) -> Optional[PluginNamespace]:
        pid = str(plugin_id or "").strip().lower()
        with self._lock:
            return self._namespaces.get(pid)

    def list_namespaces(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [
                {
                    "plugin_id": ns.plugin_id,
                    "tables": sorted(ns.tables.keys()),
                }
                for ns in self._namespaces.values()
            ]

    def logical_tables(self) -> Set[str]:
        with self._lock:
            out: Set[str] = set()
            for ns in self._namespaces.values():
                for t in ns.tables:
                    out.add(f"{ns.plugin_id}{LOGICAL_SEP}{t}")
            return out

    def table_fields(self, logical: str) -> Optional[Dict[str, str]]:
        parsed = parse_logical_name(logical)
        if not parsed:
            return None
        pid, table = parsed
        with self._lock:
            ns = self._namespaces.get(pid)
            if not ns or table not in ns.tables:
                return None
            return dict(ns.tables[table]["fields"])

    def auth_plugin_tables_payload(self) -> List[Dict[str, Any]]:
        """认证请求里的 plugin_tables 列表（含建表字段）。"""
        with self._lock:
            items: List[Dict[str, Any]] = []
            for ns in self._namespaces.values():
                for table, meta in ns.tables.items():
                    items.append(
                        {
                            "name": f"{ns.plugin_id}{LOGICAL_SEP}{table}",
                            "fields": dict(meta["fields"]),
                            "primary_keys": list(meta["primary_keys"]),
                        }
                    )
            return items

    def dispatch_apply(self, logical: str, op: str, data: Dict[str, Any]) -> bool:
        parsed = parse_logical_name(logical)
        if not parsed:
            return False
        pid, table = parsed
        with self._lock:
            ns = self._namespaces.get(pid)
            cb = ns.on_apply if ns else None
        if cb is None:
            return False
        try:
            return bool(cb(pid, table, op, data or {}))
        except Exception:
            return False

    def clear(self) -> None:
        with self._lock:
            self._namespaces.clear()


def select_all_physical_rows(db, physical: str) -> List[Dict[str, Any]]:
    """中心/本地对已存在物理表全量 SELECT *。"""
    if not physical or not _IDENT_RE.fullmatch(physical):
        return []
    return db.query_all("SELECT * FROM " + physical, ()) or []


def query_physical_rows(
    db, physical: str, where: str, params: Tuple[Any, ...]
) -> List[Dict[str, Any]]:
    if (
        not physical
        or not _IDENT_RE.fullmatch(physical)
        or not where
        or ";" in where
    ):
        return []
    sql = "SELECT * FROM " + physical + " WHERE " + where  # nosec B608
    return db.query_all(sql, tuple(params or ())) or []
