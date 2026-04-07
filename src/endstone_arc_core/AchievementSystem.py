# -*- coding: utf-8 -*-
import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from endstone import Player


class AchievementSystem:
    """
    成就系统（轻量、可配置）：
    - 统计：击杀总数、按生物类型击杀数；破坏方块总数、按方块类型破坏数
    - 配置：OP 可配置成就条件（stat_key + required_count -> unlock_title）
    - 联动：达成后调用插件的 api_unlock_title（头衔系统负责奖励发放）
    """

    def __init__(self, database_manager, title_system, language_manager, unlock_title_func):
        self.database_manager = database_manager
        self.title_system = title_system
        self.language_manager = language_manager
        self.unlock_title_func = unlock_title_func

        self._table_def = "achievement_definitions"
        self._table_stats = "player_achievement_stats"
        self._table_unlocked = "player_achievement_unlocked"

    def ensure_tables(self) -> bool:
        try:
            self.database_manager.execute(
                "CREATE TABLE IF NOT EXISTS " + self._table_def + " ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "name TEXT NOT NULL, "
                "stat_key TEXT NOT NULL, "
                "required_count INTEGER NOT NULL, "
                "unlock_title TEXT NOT NULL, "
                "enabled INTEGER DEFAULT 1"
                ")"
            )
            self.database_manager.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_achievement_def_unique "
                "ON " + self._table_def + " (stat_key, required_count, unlock_title)"
            )
            self.database_manager.execute(
                "CREATE TABLE IF NOT EXISTS " + self._table_stats + " ("
                "xuid TEXT NOT NULL, "
                "stat_key TEXT NOT NULL, "
                "count INTEGER NOT NULL DEFAULT 0, "
                "PRIMARY KEY (xuid, stat_key)"
                ")"
            )
            self.database_manager.execute(
                "CREATE TABLE IF NOT EXISTS " + self._table_unlocked + " ("
                "xuid TEXT NOT NULL, "
                "achievement_id INTEGER NOT NULL, "
                "unlocked_at TEXT, "
                "UNIQUE (xuid, achievement_id)"
                ")"
            )
            return True
        except Exception:
            return False

    @staticmethod
    def _safe_int(value: Any, default_value: int = 0) -> int:
        try:
            return int(value)
        except Exception:
            return default_value

    def _xuid(self, player: Player) -> str:
        return str(player.xuid)

    def _get_stat_count(self, xuid: str, stat_key: str) -> int:
        row = self.database_manager.query_one(
            "SELECT count FROM " + self._table_stats + " WHERE xuid = ? AND stat_key = ?",
            (xuid, stat_key),
        )
        if not row:
            return 0
        return self._safe_int(row.get("count", 0), 0)

    def _inc_stat(self, xuid: str, stat_key: str, delta: int = 1) -> int:
        delta = self._safe_int(delta, 1)
        if delta <= 0:
            return self._get_stat_count(xuid, stat_key)
        self.database_manager.execute(
            "INSERT OR IGNORE INTO " + self._table_stats + " (xuid, stat_key, count) VALUES (?, ?, 0)",
            (xuid, stat_key),
        )
        self.database_manager.execute(
            "UPDATE " + self._table_stats + " SET count = count + ? WHERE xuid = ? AND stat_key = ?",
            (delta, xuid, stat_key),
        )
        return self._get_stat_count(xuid, stat_key)

    def _is_unlocked(self, xuid: str, achievement_id: int) -> bool:
        row = self.database_manager.query_one(
            "SELECT 1 FROM " + self._table_unlocked + " WHERE xuid = ? AND achievement_id = ?",
            (xuid, int(achievement_id)),
        )
        return row is not None

    def _mark_unlocked(self, xuid: str, achievement_id: int) -> None:
        now_iso = datetime.now().isoformat()
        self.database_manager.execute(
            "INSERT OR IGNORE INTO " + self._table_unlocked + " (xuid, achievement_id, unlocked_at) VALUES (?, ?, ?)",
            (xuid, int(achievement_id), now_iso),
        )

    def _check_and_unlock_by_stat_keys(self, player: Player, stat_keys: List[str]) -> None:
        xuid = self._xuid(player)
        stat_keys = [k for k in (stat_keys or []) if k]
        if not stat_keys:
            return

        placeholders = ",".join(["?"] * len(stat_keys))
        defs = self.database_manager.query_all(
            "SELECT id, name, stat_key, required_count, unlock_title, enabled "
            "FROM " + self._table_def + " WHERE enabled = 1 AND stat_key IN (" + placeholders + ")",
            tuple(stat_keys),
        )
        if not defs:
            return

        for d in defs:
            achievement_id = self._safe_int(d.get("id"), 0)
            if achievement_id <= 0:
                continue
            if self._is_unlocked(xuid, achievement_id):
                continue

            stat_key = (d.get("stat_key") or "").strip()
            required_count = self._safe_int(d.get("required_count"), 0)
            unlock_title = (d.get("unlock_title") or "").strip()
            if not stat_key or required_count <= 0 or not unlock_title:
                continue

            current_count = self._get_stat_count(xuid, stat_key)
            if current_count < required_count:
                continue

            self.title_system.ensure_title_definition(unlock_title)
            ok = False
            try:
                ok = bool(self.unlock_title_func(player, unlock_title))
            except Exception:
                ok = False

            if ok:
                self._mark_unlocked(xuid, achievement_id)
                try:
                    msg = self.language_manager.GetText("ACHIEVEMENT_UNLOCKED_HINT")
                    if msg:
                        player.send_message(msg.format(unlock_title))
                except Exception:
                    pass

    # ---------- 统计入口 ----------
    def record_kill(self, player: Player, entity_type: str) -> None:
        if not player or not entity_type:
            return
        entity_type = str(entity_type)
        xuid = self._xuid(player)

        self._inc_stat(xuid, "kill_total", 1)
        self._inc_stat(xuid, f"kill:{entity_type}", 1)
        self._check_and_unlock_by_stat_keys(player, ["kill_total", f"kill:{entity_type}"])

    def record_block_break(self, player: Player, block_id: str) -> None:
        if not player or not block_id:
            return
        block_id = str(block_id)
        xuid = self._xuid(player)

        self._inc_stat(xuid, "block_break_total", 1)
        self._inc_stat(xuid, f"block_break:{block_id}", 1)
        self._check_and_unlock_by_stat_keys(player, ["block_break_total", f"block_break:{block_id}"])

    # ---------- OP 配置 CRUD ----------
    def list_definitions(self) -> List[Dict[str, Any]]:
        return self.database_manager.query_all(
            "SELECT id, name, stat_key, required_count, unlock_title, enabled "
            "FROM " + self._table_def + " ORDER BY enabled DESC, required_count ASC, id ASC",
            (),
        )

    def get_definition(self, achievement_id: int) -> Optional[Dict[str, Any]]:
        return self.database_manager.query_one(
            "SELECT id, name, stat_key, required_count, unlock_title, enabled "
            "FROM " + self._table_def + " WHERE id = ?",
            (int(achievement_id),),
        )

    def upsert_definition(
        self,
        name: str,
        stat_key: str,
        required_count: int,
        unlock_title: str,
        enabled: bool = True,
        achievement_id: Optional[int] = None,
    ) -> bool:
        name = (name or "").strip()
        stat_key = (stat_key or "").strip()
        unlock_title = (unlock_title or "").strip()
        required_count = self._safe_int(required_count, 0)
        enabled_int = 1 if enabled else 0
        if not name or not stat_key or not unlock_title or required_count <= 0:
            return False

        if achievement_id is None:
            return self.database_manager.execute(
                "INSERT OR REPLACE INTO " + self._table_def + " (name, stat_key, required_count, unlock_title, enabled) "
                "VALUES (?, ?, ?, ?, ?)",
                (name, stat_key, required_count, unlock_title, enabled_int),
            )

        return self.database_manager.execute(
            "UPDATE " + self._table_def + " SET name = ?, stat_key = ?, required_count = ?, unlock_title = ?, enabled = ? "
            "WHERE id = ?",
            (name, stat_key, required_count, unlock_title, enabled_int, int(achievement_id)),
        )

    def set_enabled(self, achievement_id: int, enabled: bool) -> bool:
        enabled_int = 1 if enabled else 0
        return self.database_manager.execute(
            "UPDATE " + self._table_def + " SET enabled = ? WHERE id = ?",
            (enabled_int, int(achievement_id)),
        )

    def delete_definition(self, achievement_id: int) -> bool:
        return self.database_manager.execute(
            "DELETE FROM " + self._table_def + " WHERE id = ?",
            (int(achievement_id),),
        )

