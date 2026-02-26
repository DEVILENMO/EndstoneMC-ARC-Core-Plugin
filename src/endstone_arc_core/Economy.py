# -*- coding: utf-8 -*-
"""经济系统逻辑：金钱存储、增减、排行等（基于 XUID，精确到分）"""
from typing import Optional, List, Dict, Any


class Economy:
    """经济系统：负责 player_economy 表及金钱相关数据逻辑，不包含 UI 与通知。"""

    def __init__(self, database_manager, setting_manager, logger=None):
        self.db = database_manager
        self.setting_manager = setting_manager
        self.logger = logger

    def set_logger(self, logger):
        """设置日志记录器（插件 on_enable 后调用）"""
        self.logger = logger

    def _log(self, level: str, message: str):
        if self.logger:
            if level == "error":
                self.logger.error(message)
            elif level == "warning":
                self.logger.warning(message)
            else:
                self.logger.info(message)
        else:
            print(f"[{level.upper()}] {message}")

    @staticmethod
    def round_money(value: float) -> float:
        """将金额四舍五入到分（两位小数）"""
        return round(float(value), 2)

    def format_money_display(self, value: float) -> str:
        """格式化金额用于界面显示（始终两位小数）"""
        return "%.2f" % self.round_money(value)

    def init_economy_table(self) -> bool:
        """初始化经济系统表格（money 使用 REAL，支持小数到分）"""
        fields = {
            "xuid": "TEXT PRIMARY KEY",
            "money": "REAL NOT NULL DEFAULT 0",
        }
        return self.db.create_table("player_economy", fields)

    def upgrade_player_economy_table_to_float(self) -> bool:
        """若 player_economy 表中 money 列为 INTEGER，则迁移为 REAL（仅执行一次）"""
        try:
            if not self.db.table_exists("player_economy"):
                return True
            columns_info = self.db.query_all("PRAGMA table_info(player_economy)")
            money_type = None
            for col in columns_info:
                if col.get("name") == "money":
                    money_type = str(col.get("type", "")).upper()
                    break
            if money_type != "INTEGER":
                return True
            self.db.execute(
                "CREATE TABLE player_economy_new (xuid TEXT PRIMARY KEY, money REAL NOT NULL DEFAULT 0)"
            )
            self.db.execute(
                "INSERT INTO player_economy_new (xuid, money) SELECT xuid, CAST(money AS REAL) FROM player_economy"
            )
            self.db.execute("DROP TABLE player_economy")
            self.db.execute("ALTER TABLE player_economy_new RENAME TO player_economy")
            print("[ARC Core]Upgraded player_economy money column to REAL (float).")
            return True
        except Exception as e:
            print(f"[ARC Core]Upgrade player_economy to float error: {str(e)}")
            return False

    def _get_init_money(self) -> float:
        """从配置读取初始金钱"""
        raw = self.setting_manager.GetSetting("PLAYER_INIT_MONEY_NUM")
        try:
            return self.round_money(float(raw))
        except (ValueError, TypeError):
            return 0.0

    def get_player_money_by_xuid(self, xuid: str) -> float:
        """
        按 XUID 获取玩家金钱；若记录不存在则创建并返回初始金钱。
        :return: 金钱数量（精确到分）
        """
        try:
            result = self.db.query_one(
                "SELECT money FROM player_economy WHERE xuid = ?", (xuid,)
            )
            if result is None:
                init_money = self._get_init_money()
                self.db.insert("player_economy", {"xuid": xuid, "money": init_money})
                return init_money
            return self.round_money(result["money"])
        except Exception as e:
            self._log("error", f"[ARC Core]Get player money error: {str(e)}")
            return 0.0

    def set_player_money_by_xuid(self, xuid: str, amount: float) -> bool:
        """按 XUID 设置玩家金钱（仅数据，不通知）"""
        try:
            amount = self.round_money(amount)
            return self.db.update(
                table="player_economy",
                data={"money": amount},
                where="xuid = ?",
                params=(xuid,),
            )
        except Exception as e:
            self._log("error", f"[ARC Core]Set player money error: {str(e)}")
            return False

    def increase_player_money_by_xuid(self, xuid: str, amount: float) -> bool:
        """按 XUID 增加玩家金钱（仅数据，不通知）"""
        amount = abs(self.round_money(amount))
        if amount <= 0:
            return True
        current = self.get_player_money_by_xuid(xuid)
        new_money = self.round_money(current + amount)
        return self.set_player_money_by_xuid(xuid, new_money)

    def decrease_player_money_by_xuid(self, xuid: str, amount: float) -> bool:
        """按 XUID 减少玩家金钱（仅数据，不通知）"""
        amount = abs(self.round_money(amount))
        if amount <= 0:
            return True
        current = self.get_player_money_by_xuid(xuid)
        new_money = self.round_money(current - amount)
        return self.set_player_money_by_xuid(xuid, new_money)

    def change_player_money_by_xuid(
        self, xuid: str, money_to_change: float
    ) -> bool:
        """按 XUID 改变玩家金钱（正增负减，仅数据）"""
        m = self.round_money(money_to_change)
        if m == 0:
            return True
        if m > 0:
            return self.increase_player_money_by_xuid(xuid, m)
        return self.decrease_player_money_by_xuid(xuid, abs(m))

    def judge_if_player_has_enough_money_by_xuid(
        self, xuid: str, amount: float
    ) -> bool:
        """按 XUID 判断玩家是否有足够金钱"""
        return self.get_player_money_by_xuid(xuid) >= abs(
            self.round_money(amount)
        )

    def get_top_richest_xuids(self, top_count: int) -> List[Dict[str, Any]]:
        """获取金钱最多的玩家列表，每项为 {'xuid': str, 'money': float}"""
        try:
            return self.db.query_all(
                "SELECT xuid, money FROM player_economy ORDER BY money DESC LIMIT ?",
                (top_count,),
            )
        except Exception as e:
            self._log("error", f"[ARC Core]Get top richest players error: {str(e)}")
            return []

    def get_player_money_rank_by_xuid(self, xuid: str) -> Optional[int]:
        """按 XUID 获取玩家金钱排名（从 1 开始）"""
        try:
            result = self.db.query_one(
                """
                WITH RankedPlayers AS (
                    SELECT xuid, money,
                    ROW_NUMBER() OVER (ORDER BY money DESC) as rank
                    FROM player_economy
                )
                SELECT rank FROM RankedPlayers WHERE xuid = ?
                """,
                (xuid,),
            )
            return result["rank"] if result else None
        except Exception as e:
            self._log(
                "error", f"[ARC Core]Get player money rank error: {str(e)}"
            )
            return None

    def init_player_economy_by_xuid(self, xuid: str) -> bool:
        """按 XUID 初始化玩家经济记录（若已存在则跳过）"""
        try:
            existing = self.db.query_one(
                "SELECT xuid FROM player_economy WHERE xuid = ?", (xuid,)
            )
            if existing:
                return True
            init_money = self._get_init_money()
            return self.db.insert(
                "player_economy", {"xuid": xuid, "money": init_money}
            )
        except Exception as e:
            self._log(
                "error",
                f"[ARC Core]Init player economy info error: {str(e)}",
            )
            return False

    def get_richest_one(self) -> Optional[Dict[str, Any]]:
        """获取最富有的一名玩家，返回 {'xuid': str, 'money': float} 或 None"""
        try:
            return self.db.query_one(
                "SELECT xuid, money FROM player_economy ORDER BY money DESC LIMIT 1"
            )
        except Exception as e:
            self._log(
                "error", f"[ARC Core]Get richest player error: {str(e)}"
            )
            return None

    def get_poorest_one(self) -> Optional[Dict[str, Any]]:
        """获取最贫穷的一名玩家，返回 {'xuid': str, 'money': float} 或 None"""
        try:
            return self.db.query_one(
                "SELECT xuid, money FROM player_economy ORDER BY money ASC LIMIT 1"
            )
        except Exception as e:
            self._log(
                "error", f"[ARC Core]Get poorest player error: {str(e)}"
            )
            return None

    def get_all_money_raw(self) -> List[Dict[str, Any]]:
        """获取所有玩家的金钱原始数据 [{'xuid': str, 'money': float}, ...]"""
        try:
            return self.db.query_all(
                "SELECT xuid, money FROM player_economy"
            )
        except Exception as e:
            self._log("error", f"[ARC Core]Get all money data error: {str(e)}")
            return []
