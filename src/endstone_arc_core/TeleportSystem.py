# -*- coding: utf-8 -*-
"""传送系统：公共传送点、玩家 Home、死亡回归、随机传送、TPA/TPHERE 数据与执行逻辑"""
import math
import random
import time
from typing import Dict, Any, Optional, List, Tuple


def format_dimension_name(dimension: str) -> str:
    """将完整维度名称转换为 execute 命令所需格式"""
    dimension_mapping = {
        "minecraft:overworld": "overworld",
        "minecraft:the_nether": "the_nether",
        "minecraft:the_end": "the_end",
        "Overworld": "overworld",
        "TheNether": "the_nether",
        "TheEnd": "the_end",
        "overworld": "overworld",
        "the_nether": "the_nether",
        "the_end": "the_end",
        "nether": "the_nether",
        "end": "the_end",
    }
    if dimension in dimension_mapping:
        return dimension_mapping[dimension]
    if ":" in dimension:
        return dimension.split(":")[1]
    return dimension


def generate_tp_command_to_position(
    player_name: str, position: tuple, dimension: str = "overworld"
) -> str:
    formatted_name = f'"{player_name}"' if " " in player_name else player_name
    formatted_dimension = format_dimension_name(dimension)
    return f'execute in {formatted_dimension} run tp {formatted_name} {" ".join([str(int(_)) for _ in position])}'


def generate_tp_command_to_player(
    player_name: str,
    target_player_name: str,
    dimension: str = "overworld",
) -> str:
    formatted_player = f'"{player_name}"' if " " in player_name else player_name
    formatted_target = (
        f'"{target_player_name}"' if " " in target_player_name else target_player_name
    )
    formatted_dimension = format_dimension_name(dimension)
    return f"execute in {formatted_dimension} run tp {formatted_player} {formatted_target}"


class TeleportSystem:
    """传送系统：数据表、死亡位置、传送请求、费用配置、执行传送命令"""

    def __init__(self, database_manager, setting_manager, logger=None):
        self.db = database_manager
        self.setting_manager = setting_manager
        self.logger = logger
        self.server = None

        self.player_death_locations: Dict[str, Dict[str, Any]] = {}
        self.teleport_requests: Dict[str, Dict[str, Any]] = {}

        self._load_config()

    def _load_config(self):
        """从配置加载传送相关参数"""
        raw = self.setting_manager.GetSetting("MAX_PLAYER_HOME_NUM")
        try:
            self.max_player_home_num = int(raw)
        except (ValueError, TypeError):
            self.max_player_home_num = 3

        raw = self.setting_manager.GetSetting("ENABLE_RANDOM_TELEPORT")
        if raw is None:
            self.enable_random_teleport = True
        else:
            try:
                self.enable_random_teleport = (
                    str(raw).lower() in ["true", "1", "yes"]
                )
            except (ValueError, AttributeError):
                self.enable_random_teleport = True

        for key, default in [
            ("RANDOM_TELEPORT_CENTER_X", 0),
            ("RANDOM_TELEPORT_CENTER_Z", 0),
            ("RANDOM_TELEPORT_RADIUS", 5000),
        ]:
            raw = self.setting_manager.GetSetting(key)
            try:
                setattr(self, key.lower(), int(raw))
            except (ValueError, TypeError):
                setattr(self, key.lower(), default)

        self.teleport_cost_public_warp = self._parse_cost(
            "TELEPORT_COST_PUBLIC_WARP", 0
        )
        self.teleport_cost_home = self._parse_cost("TELEPORT_COST_HOME", 0)
        self.teleport_cost_land = self._parse_cost("TELEPORT_COST_LAND", 0)
        self.teleport_cost_death_location = self._parse_cost(
            "TELEPORT_COST_DEATH_LOCATION", 0
        )
        self.teleport_cost_random = self._parse_cost("TELEPORT_COST_RANDOM", 100)
        self.teleport_cost_player = self._parse_cost("TELEPORT_COST_PLAYER", 50)

    def _parse_cost(self, key: str, default: int) -> int:
        raw = self.setting_manager.GetSetting(key)
        try:
            return int(raw)
        except (ValueError, TypeError):
            return default

    def reload_config(self):
        """重新从配置加载传送相关参数（配置重载时调用）"""
        self._load_config()

    def set_server(self, server):
        """设置服务器引用（用于执行传送命令），插件 on_enable 时调用"""
        self.server = server

    def set_logger(self, logger):
        self.logger = logger

    def _log(self, level: str, message: str):
        if self.logger:
            if level == "error":
                self.logger.error(message)
            else:
                self.logger.info(message)
        else:
            print(f"[{level.upper()}] {message}")

    # ---------- 表初始化 ----------
    def init_teleport_tables(self) -> bool:
        try:
            warp_fields = {
                "warp_id": "INTEGER PRIMARY KEY AUTOINCREMENT",
                "warp_name": "TEXT NOT NULL UNIQUE",
                "dimension": "TEXT NOT NULL",
                "x": "REAL NOT NULL",
                "y": "REAL NOT NULL",
                "z": "REAL NOT NULL",
                "created_by": "TEXT NOT NULL",
                "created_time": "INTEGER NOT NULL",
            }
            home_fields = {
                "home_id": "INTEGER PRIMARY KEY AUTOINCREMENT",
                "owner_xuid": "TEXT NOT NULL",
                "home_name": "TEXT NOT NULL",
                "dimension": "TEXT NOT NULL",
                "x": "REAL NOT NULL",
                "y": "REAL NOT NULL",
                "z": "REAL NOT NULL",
                "created_time": "INTEGER NOT NULL",
            }
            return self.db.create_table("public_warps", warp_fields) and self.db.create_table(
                "player_homes", home_fields
            )
        except Exception as e:
            self._log("error", f"Init teleport tables error: {str(e)}")
            return False

    # ---------- 公共传送点 ----------
    def create_public_warp(
        self,
        warp_name: str,
        dimension: str,
        x: float,
        y: float,
        z: float,
        creator_xuid: str,
    ) -> bool:
        try:
            warp_data = {
                "warp_name": warp_name,
                "dimension": dimension,
                "x": x,
                "y": y,
                "z": z,
                "created_by": creator_xuid,
                "created_time": int(time.time()),
            }
            return self.db.insert("public_warps", warp_data)
        except Exception as e:
            self._log("error", f"Create public warp error: {str(e)}")
            return False

    def delete_public_warp(self, warp_name: str) -> bool:
        try:
            return self.db.delete("public_warps", "warp_name = ?", (warp_name,))
        except Exception as e:
            self._log("error", f"Delete public warp error: {str(e)}")
            return False

    def get_public_warp(self, warp_name: str) -> Optional[Dict[str, Any]]:
        try:
            return self.db.query_one(
                "SELECT * FROM public_warps WHERE warp_name = ?", (warp_name,)
            )
        except Exception as e:
            self._log("error", f"Get public warp error: {str(e)}")
            return None

    def get_all_public_warps(self) -> Dict[str, Dict[str, Any]]:
        try:
            results = self.db.query_all(
                "SELECT * FROM public_warps ORDER BY warp_name"
            )
            return {row["warp_name"]: dict(row) for row in results}
        except Exception as e:
            self._log("error", f"Get all public warps error: {str(e)}")
            return {}

    def public_warp_exists(self, warp_name: str) -> bool:
        return self.get_public_warp(warp_name) is not None

    # ---------- 玩家 Home ----------
    def create_player_home(
        self,
        owner_xuid: str,
        home_name: str,
        dimension: str,
        x: float,
        y: float,
        z: float,
    ) -> bool:
        try:
            home_data = {
                "owner_xuid": owner_xuid,
                "home_name": home_name,
                "dimension": dimension,
                "x": x,
                "y": y,
                "z": z,
                "created_time": int(time.time()),
            }
            return self.db.insert("player_homes", home_data)
        except Exception as e:
            self._log("error", f"Create player home error: {str(e)}")
            return False

    def delete_player_home(self, owner_xuid: str, home_name: str) -> bool:
        try:
            return self.db.delete(
                "player_homes",
                "owner_xuid = ? AND home_name = ?",
                (owner_xuid, home_name),
            )
        except Exception as e:
            self._log("error", f"Delete player home error: {str(e)}")
            return False

    def get_player_home(
        self, owner_xuid: str, home_name: str
    ) -> Optional[Dict[str, Any]]:
        try:
            return self.db.query_one(
                "SELECT * FROM player_homes WHERE owner_xuid = ? AND home_name = ?",
                (owner_xuid, home_name),
            )
        except Exception as e:
            self._log("error", f"Get player home error: {str(e)}")
            return None

    def get_player_homes(self, owner_xuid: str) -> Dict[str, Dict[str, Any]]:
        try:
            results = self.db.query_all(
                "SELECT * FROM player_homes WHERE owner_xuid = ? ORDER BY home_name",
                (owner_xuid,),
            )
            return {row["home_name"]: dict(row) for row in results}
        except Exception as e:
            self._log("error", f"Get player homes error: {str(e)}")
            return {}

    def get_player_home_count(self, owner_xuid: str) -> int:
        try:
            result = self.db.query_one(
                "SELECT COUNT(*) as count FROM player_homes WHERE owner_xuid = ?",
                (owner_xuid,),
            )
            return result["count"] if result else 0
        except Exception as e:
            self._log("error", f"Get player home count error: {str(e)}")
            return 0

    def player_home_exists(self, owner_xuid: str, home_name: str) -> bool:
        return self.get_player_home(owner_xuid, home_name) is not None

    # ---------- 死亡位置 ----------
    def record_death_location(
        self, player_name: str, dimension: str, x: float, y: float, z: float
    ):
        self.player_death_locations[player_name] = {
            "dimension": dimension,
            "x": x,
            "y": y,
            "z": z,
        }

    def get_death_location(self, player_name: str) -> Optional[Dict[str, Any]]:
        return self.player_death_locations.get(player_name)

    def has_death_location(self, player_name: str) -> bool:
        return player_name in self.player_death_locations

    def clear_death_location(self, player_name: str):
        if player_name in self.player_death_locations:
            del self.player_death_locations[player_name]

    # ---------- 传送请求 ----------
    def add_request(
        self, target_name: str, request_type: str, sender_name: str
    ) -> bool:
        if target_name in self.teleport_requests:
            return False
        self.teleport_requests[target_name] = {
            "type": request_type,
            "sender": sender_name,
            "expire_time": time.time() + 60,
        }
        return True

    def get_request(self, target_name: str) -> Optional[Dict[str, Any]]:
        return self.teleport_requests.get(target_name)

    def remove_request(self, target_name: str):
        if target_name in self.teleport_requests:
            del self.teleport_requests[target_name]

    def get_pending_requests_for_player(self, player_name: str) -> List[Dict[str, Any]]:
        pending = []
        if player_name in self.teleport_requests:
            req = self.teleport_requests[player_name]
            if req["expire_time"] > time.time():
                pending.append(req)
            else:
                del self.teleport_requests[player_name]
        return pending

    def cleanup_expired_requests(self):
        now = time.time()
        expired = [
            name
            for name, req in self.teleport_requests.items()
            if req["expire_time"] <= now
        ]
        for name in expired:
            del self.teleport_requests[name]

    # ---------- 执行传送 ----------
    def execute_teleport_to_position(
        self, player_name: str, position: Tuple[float, float, float], dimension: str
    ):
        if not self.server:
            return
        cmd = generate_tp_command_to_position(player_name, position, dimension)
        self.server.dispatch_command(self.server.command_sender, cmd)

    def execute_teleport_to_player(
        self,
        player_name: str,
        target_player_name: str,
        target_dimension: str,
    ):
        if not self.server:
            return
        cmd = generate_tp_command_to_player(
            player_name, target_player_name, target_dimension
        )
        self.server.dispatch_command(self.server.command_sender, cmd)

    def get_random_teleport_position(self) -> Tuple[int, int, int]:
        angle = random.uniform(0, 2 * math.pi)
        radius = getattr(self, "random_teleport_radius", 5000)
        center_x = getattr(self, "random_teleport_center_x", 0)
        center_z = getattr(self, "random_teleport_center_z", 0)
        distance = random.uniform(0, radius)
        x = center_x + int(distance * math.cos(angle))
        z = center_z + int(distance * math.sin(angle))
        return (x, 256, z)

    def apply_slow_falling_effect(self, player_name: str):
        if not self.server:
            return
        try:
            self.server.dispatch_command(
                self.server.command_sender,
                f'effect "{player_name}" slow_falling 10 255 true',
            )
        except Exception as e:
            self._log("error", f"Failed to apply slow falling effect: {str(e)}")
