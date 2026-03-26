# -*- coding: utf-8 -*-
"""头衔系统：稀有度、介绍、解锁时间、解锁奖励；玩家解锁/佩戴，聊天展示。"""
import json
from datetime import datetime
from typing import List, Optional, Dict, Any

from endstone import Player


# 稀有度 -> MC 颜色码（§0-§f）
RARITY_COLORS = {
    "普通": "§f",
    "稀有": "§9",
    "史诗": "§d",
    "传奇": "§6",
    "神话": "§c",
}
DEFAULT_RARITY = "普通"


class TitleSystem:
    """头衔系统：头衔定义（稀有度、介绍、奖励）、解锁时间、佩戴与聊天展示。"""

    def __init__(self, database_manager, setting_manager):
        self.database_manager = database_manager
        self.setting_manager = setting_manager
        self._table_def = "title_definitions"
        self._table_unlock_time = "player_title_unlock_time"
        self._table_extra = "player_title_extra"
        self._table_equipped = "player_title_equipped"

    def ensure_tables(self) -> bool:
        """创建头衔相关表"""
        try:
            self.database_manager.execute(
                "CREATE TABLE IF NOT EXISTS " + self._table_def + " (title TEXT PRIMARY KEY, rarity TEXT, description TEXT, reward_money REAL DEFAULT 0, reward_items TEXT DEFAULT '[]')"
            )
            self.database_manager.execute(
                "CREATE TABLE IF NOT EXISTS " + self._table_unlock_time + " (xuid TEXT NOT NULL, title TEXT NOT NULL, unlocked_at TEXT, UNIQUE(xuid, title))"
            )
            self.database_manager.execute(
                "CREATE TABLE IF NOT EXISTS " + self._table_extra + " (xuid TEXT NOT NULL, title TEXT NOT NULL, UNIQUE(xuid, title))"
            )
            self.database_manager.execute(
                "CREATE TABLE IF NOT EXISTS " + self._table_equipped + " (xuid TEXT PRIMARY KEY, title TEXT)"
            )
            self._seed_default_title_definitions()
            return True
        except Exception:
            return False

    def _seed_default_title_definitions(self) -> None:
        """确保配置中的默认头衔和 OP 头衔在 title_definitions 中存在（默认稀有度、空介绍、无奖励）。"""
        for t in self.get_default_titles():
            self.ensure_title_definition(t, DEFAULT_RARITY, "", 0.0, [])
        op_t = self.get_op_title()
        if op_t:
            self.ensure_title_definition(op_t, DEFAULT_RARITY, "", 0.0, [])

    def ensure_title_definition(self, title: str, rarity: str = DEFAULT_RARITY, description: str = "", reward_money: float = 0.0, reward_items: Optional[List] = None) -> bool:
        """若头衔不存在则插入默认定义。"""
        if not title or not title.strip():
            return False
        title = title.strip()
        if reward_items is None:
            reward_items = []
        try:
            self.database_manager.execute(
                "INSERT OR IGNORE INTO " + self._table_def + " (title, rarity, description, reward_money, reward_items) VALUES (?, ?, ?, ?, ?)",
                (title, rarity, description, reward_money, json.dumps(reward_items, ensure_ascii=False))
            )
            return True
        except Exception:
            return False

    def get_title_definition(self, title: str) -> Optional[Dict[str, Any]]:
        """获取头衔定义：rarity, description, reward_money, reward_items。"""
        row = self.database_manager.query_one(
            "SELECT title, rarity, description, reward_money, reward_items FROM " + self._table_def + " WHERE title = ?",
            (title.strip(),)
        )
        if not row:
            return None
        reward_items = []
        if row.get("reward_items"):
            try:
                reward_items = json.loads(row["reward_items"])
            except Exception:
                pass
        return {
            "title": row["title"],
            "rarity": row.get("rarity") or DEFAULT_RARITY,
            "description": row.get("description") or "",
            "reward_money": float(row.get("reward_money") or 0),
            "reward_items": reward_items,
        }

    def set_title_definition(self, title: str, rarity: str, description: str, reward_money: float, reward_items: List) -> bool:
        """更新头衔定义（OP 编辑或创建新头衔）。"""
        if not title or not title.strip():
            return False
        title = title.strip()
        try:
            self.database_manager.execute(
                "INSERT OR REPLACE INTO " + self._table_def + " (title, rarity, description, reward_money, reward_items) VALUES (?, ?, ?, ?, ?)",
                (title, rarity, description, reward_money, json.dumps(reward_items, ensure_ascii=False))
            )
            return True
        except Exception:
            return False

    def rename_title(self, old_title: str, new_title: str) -> bool:
        """重命名头衔：同时更新定义/解锁记录/佩戴记录。"""
        try:
            old_title = (old_title or "").strip()
            new_title = (new_title or "").strip()
            if not old_title or not new_title:
                return False
            if old_title == new_title:
                return True

            old_defn = self.get_title_definition(old_title)
            if not old_defn:
                return False

            # 新名字已存在则不允许（防止覆盖/数据冲突）
            if self.get_title_definition(new_title):
                return False

            # 复制旧定义到新定义
            ok = self.set_title_definition(
                new_title,
                old_defn.get("rarity") or DEFAULT_RARITY,
                old_defn.get("description") or "",
                float(old_defn.get("reward_money") or 0.0),
                old_defn.get("reward_items") or [],
            )
            if not ok:
                return False

            # 同步玩家解锁记录
            self.database_manager.execute(
                f"UPDATE {self._table_unlock_time} SET title = ? WHERE title = ?",
                (new_title, old_title),
            )
            self.database_manager.execute(
                f"UPDATE {self._table_extra} SET title = ? WHERE title = ?",
                (new_title, old_title),
            )

            # 同步玩家佩戴记录
            self.database_manager.execute(
                f"UPDATE {self._table_equipped} SET title = ? WHERE title = ?",
                (new_title, old_title),
            )

            # 删除旧定义（玩家记录已迁移到新名字）
            self.database_manager.execute(
                f"DELETE FROM {self._table_def} WHERE title = ?",
                (old_title,),
            )

            return True
        except Exception:
            return False

    def get_all_title_names(self) -> List[str]:
        """所有头衔名：配置默认 + OP 头衔 + 数据库中自定义头衔（去重）。"""
        default = self.get_default_titles()
        op_t = self.get_op_title()
        rows = self.database_manager.query_all("SELECT title FROM " + self._table_def, ())
        db_titles = [r["title"] for r in rows if r.get("title")]
        seen = set()
        result = []
        for t in default + ([op_t] if op_t else []) + db_titles:
            if t and t not in seen:
                result.append(t)
                seen.add(t)
        return result

    def get_title_rarity_color(self, title: str) -> str:
        """根据头衔稀有度返回 MC 颜色码，如 §f、§9。"""
        defn = self.get_title_definition(title)
        rarity = (defn.get("rarity") or DEFAULT_RARITY) if defn else DEFAULT_RARITY
        return RARITY_COLORS.get(rarity, "§f")

    def _get_default_titles_raw(self) -> str:
        raw = self.setting_manager.GetSetting("DEFAULT_TITLE")
        return (raw or "").strip()

    def get_default_titles(self) -> List[str]:
        """配置中的默认头衔列表（逗号分隔）。"""
        raw = self._get_default_titles_raw()
        if not raw:
            return []
        return [t.strip() for t in raw.split(",") if t.strip()]

    def get_op_title(self) -> Optional[str]:
        """配置中的 OP 专属头衔（仅一个）。"""
        raw = self.setting_manager.GetSetting("OP_TITLE")
        if not raw or not str(raw).strip():
            return None
        return str(raw).strip()

    def _xuid(self, player: Player) -> str:
        return str(player.xuid)

    def _ensure_unlock_time_from_extra(self, xuid: str) -> None:
        """兼容：若 player_title_unlock_time 无该玩家记录但 player_title_extra 有，则迁移并补时间。"""
        rows_extra = self.database_manager.query_all(
            "SELECT title FROM " + self._table_extra + " WHERE xuid = ?", (xuid,)
        )
        if not rows_extra:
            return
        now_iso = datetime.now().isoformat()
        for r in rows_extra:
            t = r.get("title")
            if not t:
                continue
            self.database_manager.execute(
                "INSERT OR IGNORE INTO " + self._table_unlock_time + " (xuid, title, unlocked_at) VALUES (?, ?, ?)",
                (xuid, t, now_iso)
            )

    def get_unlocked_titles(self, player: Player) -> List[str]:
        """玩家当前拥有的全部头衔（来自 player_title_unlock_time；无记录时从 player_title_extra 迁移）。"""
        xuid = self._xuid(player)
        self._ensure_unlock_time_from_extra(xuid)
        rows = self.database_manager.query_all(
            "SELECT title FROM " + self._table_unlock_time + " WHERE xuid = ? ORDER BY title",
            (xuid,)
        )
        return [r["title"] for r in rows if r.get("title")]

    def get_title_unlock_time(self, player: Player, title: str) -> Optional[str]:
        """玩家某头衔的解锁时间（ISO 字符串），未解锁返回 None。"""
        row = self.database_manager.query_one(
            "SELECT unlocked_at FROM " + self._table_unlock_time + " WHERE xuid = ? AND title = ?",
            (self._xuid(player), title.strip())
        )
        return row.get("unlocked_at") if row else None

    def get_equipped_title(self, player: Player) -> Optional[str]:
        """当前佩戴的头衔，未佩戴返回 None。"""
        row = self.database_manager.query_one(
            "SELECT title FROM " + self._table_equipped + " WHERE xuid = ?",
            (self._xuid(player),)
        )
        if not row or row.get("title") is None:
            return None
        return row["title"]

    def set_equipped_title(self, player: Player, title: Optional[str]) -> bool:
        """设置佩戴头衔。仅当 title 在解锁列表中或为 None 时才允许。"""
        xuid = self._xuid(player)
        if title is None or title == "":
            return self.database_manager.execute(
                "DELETE FROM " + self._table_equipped + " WHERE xuid = ?", (xuid,)
            )
        unlocked = self.get_unlocked_titles(player)
        if title not in unlocked:
            return False
        self.database_manager.execute("DELETE FROM " + self._table_equipped + " WHERE xuid = ?", (xuid,))
        return self.database_manager.execute(
            "INSERT INTO " + self._table_equipped + " (xuid, title) VALUES (?, ?)", (xuid, title)
        )

    def unlock_title(self, player: Player, title: str) -> bool:
        """为玩家解锁头衔（API）。"""
        if not title or not title.strip():
            return False
        return self.unlock_title_by_xuid(self._xuid(player), title.strip())

    def unlock_title_by_xuid(self, xuid: str, title: str, unlocked_at: Optional[str] = None) -> bool:
        """按 xuid 为玩家解锁头衔，并记录解锁时间。"""
        if not xuid or not title or not title.strip():
            return False
        title = title.strip()
        if unlocked_at is None:
            unlocked_at = datetime.now().isoformat()
        try:
            self.database_manager.execute(
                "INSERT OR REPLACE INTO " + self._table_unlock_time + " (xuid, title, unlocked_at) VALUES (?, ?, ?)",
                (xuid, title, unlocked_at)
            )
            return True
        except Exception:
            return False

    def on_player_join(self, player: Player) -> None:
        """进服时：1) 确保默认头衔与 OP 头衔有解锁记录（无则插入，时间为当前）；2) 若非 OP 且佩戴 OP 头衔则解除佩戴。"""
        xuid = self._xuid(player)
        self._ensure_unlock_time_from_extra(xuid)
        default_titles = self.get_default_titles()
        op_title = self.get_op_title()
        to_ensure = list(default_titles)
        if op_title and getattr(player, "is_op", False):
            to_ensure.append(op_title)
        now_iso = datetime.now().isoformat()
        for t in to_ensure:
            if not t:
                continue
            self.ensure_title_definition(t)
            self.database_manager.execute(
                "INSERT OR IGNORE INTO " + self._table_unlock_time + " (xuid, title, unlocked_at) VALUES (?, ?, ?)",
                (xuid, t, now_iso)
            )
        if op_title and not getattr(player, "is_op", False):
            equipped = self.get_equipped_title(player)
            if equipped == op_title:
                self.set_equipped_title(player, None)

    def format_chat_message(self, player: Player, original_message: str) -> str:
        """根据佩戴头衔格式化聊天（含稀有度颜色）。返回形如 §l§f[头衔]玩家名§r(时间)：\\n消息。"""
        equipped = self.get_equipped_title(player)
        name = player.name
        if equipped:
            color = self.get_title_rarity_color(equipped)
            return "§l" + color + "[" + equipped + "]" + name + "§r: " + original_message
        return name + ": " + original_message
