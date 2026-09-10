import hashlib
import json
import math
import random
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

from endstone import ColorFormat, Player, GameMode
from endstone.form import ActionForm, TextInput, ModalForm, Label, Dropdown
from endstone.command import Command, CommandSender
from endstone.event import event_handler, PlayerJoinEvent, PlayerQuitEvent, PlayerRespawnEvent, BlockBreakEvent, BlockPlaceEvent, PlayerDeathEvent, PlayerInteractEvent, ActorExplodeEvent, PlayerInteractActorEvent, ActorDamageEvent, ActorDeathEvent, ActorSpawnEvent, PlayerChatEvent, PlayerDropItemEvent, PlayerPickupItemEvent, PlayerItemHeldEvent, PlayerItemConsumeEvent, PlayerTeleportEvent, PlayerCommandEvent, ServerCommandEvent, PlayerGameModeChangeEvent
from endstone.plugin import Plugin

from endstone_arc_core.DatabaseManager import DatabaseManager
from endstone_arc_core.Economy import Economy
from endstone_arc_core.LanguageManager import LanguageManager
from endstone_arc_core.SettingManager import SettingManager
from endstone_arc_core.TeleportSystem import TeleportSystem, generate_tp_command_to_position
from endstone_arc_core.mc_command_format import format_mc_command_player_name
from endstone_arc_core.dimension_utils import (
    get_dimension_id,
    translate_dimension_display,
    migrate_spawn_locations_dimensions,
)
from endstone_arc_core.LandSystem import LandSystem
from endstone_arc_core.TitleSystem import TitleSystem, DEFAULT_RARITY
from endstone_arc_core.ConditionalTitle import ConditionalTitleManager, RichestTitleProvider
from endstone_arc_core.GuildSystem import (
    GuildSystem,
    ROLE_OWNER,
    ROLE_MANAGER,
    ROLE_MEMBER,
    SIZE_TIERS,
    SIZE_TIER_SMALL,
    SIZE_TIER_MEDIUM,
    SIZE_TIER_LARGE,
    strip_mc_color_codes as guild_strip_mc_color_codes,
)
from endstone_arc_core.EntityDisplayNameManager import EntityDisplayNameManager
from endstone_arc_core.KillRewardConfig import KillRewardConfig, normalize_entity_type_id
from endstone_arc_core.PlayerActivityStats import PlayerActivityStats
from endstone_arc_core.arc_error_log import append_arc_error_log, format_context_lines
from endstone_arc_core.sky_eye_log import SkyEyeStore, format_sky_eye_records, prune_sky_eye_logs
from endstone_arc_core.sync_server import SyncServer
from endstone_arc_core.sync_client import SyncClient
from endstone_arc_core.sync_config import ALL_SHARED_SETTING_KEYS, resolve_sync_consumer_mode
from endstone_arc_core.SidebarSystem import SidebarSystem
from endstone_arc_core import bedrock_glyphs

MAIN_PATH = 'plugins/ARCCore'
# 旧版一次性导入占位 uuid 前缀（进服时覆写为真实 unique_id）
RECOVERED_UUID_PREFIX = "recovered-"
# 天眼系统：独立 SQLite + 兼容清理旧按日 txt
SKY_EYE_LOG_DIR_NAME = 'sky_eye'
SKY_EYE_DB_NAME = 'skyeye.db'
# 公会浏览列表每页按钮数量（避免表单按钮过多）
GUILD_BROWSE_PAGE_SIZE = 18
# 聊天/展示名前缀：内置公会与头衔（priority 越小越靠前，最低 0）
CHAT_PREFIX_GUILD = "guild"
CHAT_PREFIX_TITLE = "title"
CHAT_PREFIX_PRIORITY_GUILD = 2
CHAT_PREFIX_PRIORITY_TITLE = 3


class ARCCorePlugin(Plugin):
    api_version = "0.10"
    # 交互圈地：同一次点击 EndStone 可能连发多次 PlayerInteractEvent；上次成功记录选点后的最短间隔（秒），用于防抖
    _land_creation_pick_debounce_sec = 0.12

    commands = {
        "updatespawnpos": {
            "description": "Update spawn position of current dimension.",
            "usages": ["/updatespawnpos"],
            "permissions": ["arc_core.command.op"],
        },
        "arc": {
            "description": "ARC menu; subcommands: op, land, tp, bank, guild.",
            "usages": ["/arc", "/arc op", "/arc land", "/arc tp", "/arc bank", "/arc guild"],
            "permissions": ["arc_core.command.common"],
        },
        "suicide":
            {
            "description": "Kill yourself.",
            "usages": ["/suicide"],
            "permissions": ["arc_core.command.common"],
        },
        "spawn":
        {
            "description": "Teleport to spawn position.",
            "usages": ["/spawn"],
            "permissions": ["arc_core.command.common"],
        },
        "land": {
            "description": "Land helpers: pos1, pos2, buy.",
            "usages": ["/land pos1", "/land pos2", "/land buy"],
            "permissions": ["arc_core.command.common"],
        },
        "pos1": {
            "description": "OP only: record current position as coordinate 1.",
            "usages": ["/pos1"],
            "permissions": ["arc_core.command.op"],
        },
        "pos2": {
            "description": "OP only: record current position as coordinate 2 and open OP panel.",
            "usages": ["/pos2"],
            "permissions": ["arc_core.command.op"],
        },
        "connecttoserver": {
            "description": "Cross-server: no args opens picker; else transfer by server name.",
            "usages": ["/connecttoserver", "/connecttoserver <server_name: str>"],
            "permissions": ["arc_core.command.common"],
        },
        "sidebar": {
            "description": "Sidebar: on/off, next/prev, lock/unlock, list.",
            "usages": [
                "/sidebar",
                "/sidebar on",
                "/sidebar off",
                "/sidebar next",
                "/sidebar prev",
                "/sidebar lock",
                "/sidebar lock <page: str>",
                "/sidebar unlock",
                "/sidebar list",
            ],
            "permissions": ["arc_core.command.common"],
        },
        "sb": {
            "description": "Alias of /sidebar.",
            "usages": [
                "/sb",
                "/sb on",
                "/sb off",
                "/sb next",
                "/sb prev",
                "/sb lock",
                "/sb lock <page: str>",
                "/sb unlock",
                "/sb list",
            ],
            "permissions": ["arc_core.command.common"],
        },
        "tpa": {
            "description": "Accept or deny the latest teleport request.",
            "usages": ["/tpa accept", "/tpa deny"],
            "permissions": ["arc_core.command.common"],
        },
    }
    permissions = {
        "arc_core.command.common": {
            "description": "Commands for all players (arc, suicide, spawn, land, connecttoserver, sidebar, tpa, /arc guild).",
            "default": True,
        },
        "arc_core.command.op": {
            "description": "OP only: updatespawnpos, pos1, pos2; /arc op still checks is_op in handler.",
            "default": "op",
        },
    }

    def __init__(self):
        # 在__init__中不能使用self.logger打印，因为self.logger还没有初始化
        super().__init__()
        self.setting_manager = SettingManager()
        default_language_dode = self.setting_manager.GetSetting('DEFAULT_LANGUAGE_CODE')
        self.language_manager = LanguageManager(default_language_dode if default_language_dode is not None else 'ZH-CN')
        self.database_manager = DatabaseManager(Path(MAIN_PATH) / self.setting_manager.GetSetting('DATABASE_PATH'))
        self.sky_eye_store = SkyEyeStore(Path(MAIN_PATH) / SKY_EYE_LOG_DIR_NAME / SKY_EYE_DB_NAME)
        self._sky_eye_hand_cache: dict[str, str] = {}
        self._sky_eye_land_meta: dict[int, tuple] = {}

        # 跨服数据消费方式：远程客户端 / 无
        self._sync_consumer_mode = resolve_sync_consumer_mode(self.setting_manager)

        self.economy = Economy(self.database_manager, self.setting_manager)
        self.teleport_system = TeleportSystem(self.database_manager, self.setting_manager)
        self.land_system = LandSystem(self.database_manager, self.setting_manager)
        self.title_system = TitleSystem(self.database_manager, self.setting_manager)
        self.conditional_titles = ConditionalTitleManager(
            self.database_manager,
            self.title_system,
            can_compute=self._can_compute_conditional_titles,
            find_online_by_xuid=self._find_online_player_by_xuid,
            update_name_tag=self._update_player_name_tag,
            grant_unlock_reward=None,
        )
        self.entity_display_name_manager = EntityDisplayNameManager(Path(MAIN_PATH), logger=None)
        self.kill_reward_config = KillRewardConfig(Path(MAIN_PATH), logger=None)
        self.kill_reward_guild_contrib_ratio = self._load_kill_reward_guild_contrib_ratio()
        self.activity_stats = PlayerActivityStats(self.database_manager, logger=None)
        self.guild_system = GuildSystem(
            self.database_manager,
            self.setting_manager,
            self.economy,
            on_money_changed=self._update_richest_title_if_needed,
        )
        self.sidebar_system = SidebarSystem(self)
        # 主菜单按钮注册表：button_id -> {text, on_click, priority, visible}
        self._main_menu_buttons: Dict[str, dict] = {}
        self._main_menu_buttons_lock = threading.Lock()
        # 聊天/展示名前缀：prefix_name -> {priority}；玩家自定义文本 xuid -> {name -> text}
        self._chat_prefixes: Dict[str, dict] = {}
        self._chat_prefixes_lock = threading.Lock()
        self._player_chat_prefixes: Dict[str, Dict[str, str]] = {}
        self._player_chat_prefixes_lock = threading.Lock()
        try:
            self.economy.set_balance_changed_callback(
                self._on_economy_balance_changed_for_sidebar
            )
        except Exception:
            pass
        self.init_database()
        self._arc_error_log_path = str(Path(MAIN_PATH) / "error_log.txt")

        # 跨服数据同步服务（在 on_enable 中启动，确保 logger 可用）
        self.sync_server: Optional[SyncServer] = None
        self._play_session_start: dict = {}
        self.sync_client: Optional[SyncClient] = None

        # 注册首富条件头衔（state 已在 init_database 中 ensure）
        self.conditional_titles.register(
            RichestTitleProvider(
                self.database_manager,
                self.title_system,
                get_title_name=self._get_richest_title_name,
                hide_op_in_ranking=lambda: bool(
                    getattr(self, "hide_op_in_money_ranking", True)
                ),
            )
        )

        self.if_protect_spawn = self.setting_manager.GetSetting('IF_PROTECT_SPAWN')
        if self.if_protect_spawn is None:
            self.if_protect_spawn = False
        self.spawn_pos_dict = self.get_all_spawn_locations()
        self.spawn_protect_range = self.setting_manager.GetSetting('SPAWN_PROTECT_RANGE')
        self.spawn_protect_range = self.setting_manager.GetSetting('SPAWN_PROTECT_RANGE')
        if self.spawn_protect_range is None:
            self.spawn_protect_range = 8
        else:
            try:
                self.spawn_protect_range = int(self.spawn_protect_range)
            except ValueError:
                self.spawn_protect_range = 8

        # 敏感操作（转账、创建/管理领地等）：本会话验证密码一次，退出游戏前有效
        self.player_sensitive_password_verified: Dict[str, bool] = {}
        # 尚未设置密码的玩家：完成注册后需继续的敏感操作（关闭注册窗时走 on_cancel）
        self._pending_sensitive_action_by_player: Dict[str, Dict[str, Callable[[Player], None]]] = {}
        # 天眼：仅 PlayerJoin 完成后才追踪（进服加载期 GameModeChange 等会崩）
        self._sky_eye_ready_xuids: Set[str] = set()

        # 玩家圈地
        self.land_min_distance = self.setting_manager.GetSetting('MIN_LAND_DISTANCE')
        try:
            self.land_min_distance = int(self.land_min_distance)
        except (ValueError, TypeError):
            self.land_min_distance = 0
        self.land_price = self.setting_manager.GetSetting('LAND_PRICE')
        try:
            self.land_price = int(self.land_price)
        except (ValueError, TypeError):
            self.land_price = 100
        self.land_sell_refund_coefficient = self.setting_manager.GetSetting('LAND_SELL_REFUND_COEFFICIENT')
        try:
            self.land_sell_refund_coefficient = float(self.land_sell_refund_coefficient)
        except (ValueError, TypeError):
            self.land_sell_refund_coefficient = 0.9
        self.land_sale_vat_rate = self.setting_manager.GetSetting('LAND_SALE_VAT_RATE')
        try:
            self.land_sale_vat_rate = float(self.land_sale_vat_rate)
            if self.land_sale_vat_rate < 0:
                self.land_sale_vat_rate = 0.0
            elif self.land_sale_vat_rate > 1.0:
                self.land_sale_vat_rate = 1.0
        except (ValueError, TypeError):
            self.land_sale_vat_rate = 0.1
        self.land_min_size = self.setting_manager.GetSetting('LAND_MIN_SIZE')
        try:
            self.land_min_size = int(self.land_min_size)
        except (ValueError, TypeError):
            self.land_min_size = 5  # 默认最小尺寸为5
        self._land_only_place_block_ids = frozenset()
        self._disabled_block_ids = frozenset()
        self._refresh_disabled_blocks()
        self._refresh_land_only_place_blocks()
        self.player_new_land_creation_info = {}  # {name: {'dimension': str, 'min_x': int, 'max_x': int, 'min_y': int, 'max_y': int, 'min_z': int, 'max_z': int}}
        self.player_land_pos1 = {}  # {name: {'dimension': str, 'x': int, 'y': int, 'z': int}} 暂存 /land pos1
        # 交互式圈地：rect_a → rect_b → y_min → y_max，值为 dict(step=..., dimension=..., ...)
        self.player_land_creation_pick: Dict[str, Dict[str, Any]] = {}
        # 上次成功写入一次选点的时间，用于防抖（与 player_land_creation_pick 同步清理）
        self.player_land_pick_last_event_ts: Dict[str, float] = {}
        # 领地边界粒子：按 xuid（无则 name）追踪分 tick 任务；退出时取消，避免对失效 Player 调 spawn_particle 崩服
        self._land_particle_task_by_key: Dict[str, Any] = {}
        self._land_particle_gen_by_key: Dict[str, int] = {}

        # OP坐标记录与上次执行指令（空输入时重复执行）
        self.op_coordinate1_dict = {}
        self.op_coordinate2_dict = {}
        self.op_last_command_dict = {}

        # OP 调试模式（开启后触发方块/生物相关事件时向该 OP 发送调试信息）
        self.op_debug_mode = set()

        # 玩家出入领地
        self.player_in_land_id_dict = {}
        
        # 多线程位置检测相关
        self.position_thread = None
        self.position_thread_running = False
        self.position_thread_lock = threading.Lock()
        self.position_check_interval = 0.5  # 每0.5秒检查一次，比原来的1.25秒更快


        # 公告系统
        self.broadcast_messages = []  # 存储公告消息列表
        self.current_broadcast_index = 0  # 当前公告索引
        self.broadcast_interval = self.setting_manager.GetSetting('BROADCAST_INTERVAL')
        try:
            self.broadcast_interval = int(self.broadcast_interval)
        except (ValueError, TypeError):
            self.broadcast_interval = 300  # 默认5分钟（300秒）
        self.small_horn_price_per_hour = self.setting_manager.GetSetting('SMALL_HORN_PRICE_PER_HOUR')
        try:
            self.small_horn_price_per_hour = int(self.small_horn_price_per_hour)
            if self.small_horn_price_per_hour < 0:
                self.small_horn_price_per_hour = 60
        except (ValueError, TypeError):
            self.small_horn_price_per_hour = 60

        # 新人欢迎系统（文件只在启动/OP 重载时读入内存）
        self.newbie_welcome_file = Path(MAIN_PATH) / "newbie_welcome.txt"
        self.newbie_commands_file = Path(MAIN_PATH) / "newbie_commands.txt"
        self.newbie_welcome_text = ""
        self.newbie_command_lines: List[str] = []
        self._ensure_newbie_files_exist()
        self._load_newbie_files()

        # 金钱排行榜设置
        self.hide_op_in_money_ranking = self.setting_manager.GetSetting('HIDE_OP_IN_MONEY_RANKING')
        if self.hide_op_in_money_ranking is None:
            self.hide_op_in_money_ranking = True
        else:
            try:
                self.hide_op_in_money_ranking = self.hide_op_in_money_ranking.lower() in ['true', '1', 'yes']
            except (ValueError, AttributeError):
                self.hide_op_in_money_ranking = True

        # 清道夫系统变量初始化
        self.enable_cleaner = False
        self.cleaner_interval = 600

        # 性能检测应急关服（current_mspt 超阈值则 /stop）
        self.enable_mspt_emergency_shutdown = False
        self.mspt_emergency_shutdown_limit = 100.0
        self._mspt_emergency_missing_attr_logged = False

    def on_load(self) -> None:
        self.logger.info(f"{ColorFormat.YELLOW}[ARC Core]Plugin loaded!")

    def _safe_log(self, level: str, message: str):
        """
        安全的日志记录方法，在logger未初始化时使用print
        :param level: 日志级别 (info, warning, error)
        :param message: 日志消息
        """
        if hasattr(self, 'logger') and self.logger is not None:
            if level.lower() == 'info':
                self.logger.info(message)
            elif level.lower() == 'warning':
                self.logger.warning(message)
            elif level.lower() == 'error':
                self.logger.error(message)
            else:
                self.logger.info(message)
        else:
            # 如果logger未初始化，使用print
            print(f"[{level.upper()}] {message}")

    def _ensure_newbie_files_exist(self):
        """确保新人欢迎相关文件存在"""
        try:
            # 确保目录存在
            Path(MAIN_PATH).mkdir(exist_ok=True)
            
            # 创建新人欢迎消息文件
            if not self.newbie_welcome_file.exists():
                default_welcome = "欢迎来到我们的服务器！\n希望你在这里玩得愉快！\n如有疑问请联系管理员。"
                self.newbie_welcome_file.write_text(default_welcome, encoding='utf-8')
                # 在__init__期间不能使用self.logger，使用print代替
                print(f"[ARC Core]Created default newbie welcome file: {self.newbie_welcome_file}")
            
            # 创建新人指令文件
            if not self.newbie_commands_file.exists():
                default_commands = (
                    "# 新人指令文件\n# 每行一个指令，{player} 会被替换为玩家名称"
                    "（若名称含空格会自动加双引号）\n# 示例：\n# gamemode 0 {player}\n"
                    "# give {player} minecraft:bread 16\n# clear {player}"
                )
                self.newbie_commands_file.write_text(default_commands, encoding='utf-8')
                # 在__init__期间不能使用self.logger，使用print代替
                print(f"[ARC Core]Created default newbie commands file: {self.newbie_commands_file}")
                
        except Exception as e:
            # 在__init__期间不能使用self.logger，使用print代替
            print(f"[ARC Core]Failed to create newbie files: {str(e)}")

    def _load_newbie_files(self) -> None:
        """把 newbie_welcome.txt / newbie_commands.txt 读进内存；OP 重载时再调一次。"""
        try:
            if self.newbie_welcome_file.exists():
                self.newbie_welcome_text = self.newbie_welcome_file.read_text(
                    encoding="utf-8"
                )
            else:
                self.newbie_welcome_text = ""
        except Exception as e:
            self.newbie_welcome_text = ""
            self._safe_log("error", f"[ARC Core]Load newbie welcome error: {e}")

        try:
            if self.newbie_commands_file.exists():
                raw = self.newbie_commands_file.read_text(encoding="utf-8")
            else:
                raw = ""
            lines: List[str] = []
            for line in raw.split("\n"):
                line = line.strip()
                if line and not line.startswith("#"):
                    lines.append(line)
            self.newbie_command_lines = lines
        except Exception as e:
            self.newbie_command_lines = []
            self._safe_log("error", f"[ARC Core]Load newbie commands error: {e}")

    def _send_newbie_welcome_message(self, player: Player):
        """发送新人欢迎消息（使用内存缓存，不读盘）。"""
        try:
            welcome_content = (self.newbie_welcome_text or "").strip()
            if welcome_content:
                for message in welcome_content.split("\n"):
                    if message.strip():
                        player.send_message(f"§e[欢迎] §f{message.strip()}")
                self.logger.info(f"[ARC Core]Sent welcome message to new player: {player.name}")
            else:
                self.logger.warning(
                    f"[ARC Core]Welcome text is empty: {self.newbie_welcome_file}"
                )
        except Exception as e:
            self.logger.error(f"[ARC Core]Failed to send welcome message to {player.name}: {str(e)}")

    def _execute_newbie_commands(self, player: Player):
        """执行新人指令（使用内存缓存，不读盘）。"""
        try:
            if not self.newbie_command_lines:
                self.logger.warning(
                    f"[ARC Core]Newbie commands are empty: {self.newbie_commands_file}"
                )
                return
            executed_count = 0
            for line in self.newbie_command_lines:
                command = line.replace(
                    "{player}", format_mc_command_player_name(player.name)
                )
                try:
                    self.server.dispatch_command(self.server.command_sender, command)
                    executed_count += 1
                    self.logger.info(
                        f"[ARC Core]Executed newbie command for {player.name}: {command}"
                    )
                except Exception as cmd_e:
                    self.logger.error(
                        f"[ARC Core]Failed to execute command '{command}' for {player.name}: {str(cmd_e)}"
                    )
            if executed_count > 0:
                self.logger.info(
                    f"[ARC Core]Executed {executed_count} newbie commands for {player.name}"
                )
        except Exception as e:
            self.logger.error(f"[ARC Core]Failed to execute newbie commands for {player.name}: {str(e)}")

    def on_enable(self) -> None:
        self.register_events(self)
        self.logger.info(f"{ColorFormat.YELLOW}[ARC Core]Plugin enabled!")
        self._init_sync_service()
        self.economy.set_logger(self.logger)

        def _on_arc_persistent_error(error_code: str, detail: str, exc):
            self._arc_persistent_error(error_code, detail, exc)

        self.economy.set_persistent_error_callback(_on_arc_persistent_error)
        self.land_system.set_persistent_error_callback(_on_arc_persistent_error)
        self.teleport_system.set_server(self.server)
        self.teleport_system.set_logger(self.logger)
        self.land_system.set_logger(self.logger)
        self.land_system.reload_config()
        self.entity_display_name_manager.logger = self.logger
        self.kill_reward_config.logger = self.logger
        self.activity_stats.logger = self.logger
        try:
            self.activity_stats.start_writer()
        except Exception:
            pass

        # 初始化公告系统和清道夫系统
        self._load_broadcast_messages()
        self._init_cleaner_system()
        self._init_mspt_emergency_shutdown_settings()

        # 启动多线程位置检测系统（领地系统开启时）
        self._sync_land_position_thread()

        # Scheduler tasks
        # 移除了原有的 player_position_listener，现在使用多线程方式
        self.server.scheduler.run_task(self, self.teleport_system.cleanup_expired_requests, delay=0, period=100)  # 每5秒清理一次过期请求
        
        # 公告系统定时任务
        if self.broadcast_messages:
            broadcast_period = self.broadcast_interval * 20  # 转换为ticks (1秒 = 20 ticks)
            self.server.scheduler.run_task(self, self.send_broadcast_message, delay=broadcast_period, period=broadcast_period)
            self.logger.info(f"[ARC Core]Broadcast system started, interval: {self.broadcast_interval} seconds")
        small_horn_period = 10 * 60 * 20  # 每10分钟
        self.server.scheduler.run_task(self, self.send_small_horn_messages, delay=small_horn_period, period=small_horn_period)
        self.logger.info("[ARC Core]Small horn system started, interval: 600 seconds")

        # 清道夫系统定时任务
        if self.enable_cleaner:
            cleaner_period = self.cleaner_interval * 20  # 转换为ticks
            self.server.scheduler.run_task(self, self.start_cleaner_warning, delay=cleaner_period, period=cleaner_period)
            self.logger.info(f"[ARC Core]Cleaner system started, interval: {self.cleaner_interval} seconds")

        # 性能检测应急关服：每 10 秒检查一次；关闭时回调立即返回（重载配置后可即时生效）
        self.server.scheduler.run_task(
            self, self._mspt_emergency_shutdown_tick, delay=200, period=200
        )
        
        # 别踩白块接入
        self.dtwt_plugin = self.server.plugin_manager.get_plugin('arc_dtwt')
        print('[ARC Core]DTWT plugin loaded:', self.dtwt_plugin is not None)

        # 首富条件头衔：权威服启动时与财富榜核对（从服 no-op）
        try:
            self._ensure_richest_title_definition()
            self._update_richest_title_if_needed()
        except Exception:
            pass

        # 天眼：启动时若已开启则立即按保留天数清理 SQLite 与旧 txt
        if self._sky_eye_setting_bool("ENABLE_SKY_EYE", True):
            try:
                self.sky_eye_store.prune(self._sky_eye_retention_days())
                prune_sky_eye_logs(
                    Path(MAIN_PATH) / SKY_EYE_LOG_DIR_NAME,
                    self._sky_eye_retention_days(),
                )
            except Exception:
                pass
            try:
                def _sky_eye_prune_tick():
                    try:
                        if not self._sky_eye_setting_bool("ENABLE_SKY_EYE", True):
                            return
                        store = getattr(self, "sky_eye_store", None)
                        if store is not None:
                            store.maybe_prune(self._sky_eye_retention_days())
                    except Exception:
                        pass

                self.server.scheduler.run_task(
                    self, _sky_eye_prune_tick, delay=72000, period=72000
                )
            except Exception:
                pass
            # 热重载后已在线玩家补标可追踪，避免指令漏记
            try:
                for online in list(self.server.online_players or []):
                    self._sky_eye_mark_ready(online)
            except Exception:
                pass

        # 侧边栏总控：定时刷新 + 多页轮播
        try:
            self.sidebar_system.reload_config()
            if self.sidebar_system.enabled:
                self.sidebar_system.start()
                period = max(1, int(getattr(self.sidebar_system, "TICK_PERIOD", 5)))
                self.server.scheduler.run_task(
                    self, self.sidebar_system.tick, delay=period, period=period
                )
                self.logger.info(
                    f"[ARC Core]Sidebar system started, refresh={self.sidebar_system.refresh_ticks} ticks/player "
                    f"(join-staggered, scheduler every {period} ticks), "
                    f"switch={self.sidebar_system.switch_interval}s, "
                    f"join_delay={self.sidebar_system.join_delay_ticks} ticks"
                )
                # 热重载时给已在线玩家补建侧边栏
                for online in list(self.server.online_players or []):
                    try:
                        self.sidebar_system.on_player_join(online)
                    except Exception:
                        pass
        except Exception as e:
            self.logger.error(f"[ARC Core]Sidebar system start error: {e}")

        # 主菜单内置按钮（优先级从 3 起，签到为 0/99）
        try:
            self._register_core_main_menu_buttons()
        except Exception as e:
            self.logger.error(f"[ARC Core]Register core main menu buttons error: {e}")

        # 聊天/展示名前缀（公会 2、头衔 3；外部插件可再注册）
        try:
            self._register_core_chat_prefixes()
        except Exception as e:
            self.logger.error(f"[ARC Core]Register core chat prefixes error: {e}")

    def _init_sync_service(self) -> None:
        """初始化跨服数据同步：同步中心（可选）与远程客户端（与文件路径互斥）。"""
        enable_sync_server = self.setting_manager.GetSetting('ENABLE_SYNC_SERVER')
        if enable_sync_server and enable_sync_server.lower() == 'true':
            sync_port = self.setting_manager.GetSetting('SYNC_SERVER_PORT')
            sync_auth_key = self.setting_manager.GetSetting('SYNC_SERVER_AUTH_KEY') or ""
            try:
                sync_port = int(sync_port) if sync_port else 19999
            except (ValueError, TypeError):
                sync_port = 19999

            self.sync_server = SyncServer(
                database_manager=self.database_manager,
                setting_manager=self.setting_manager,
                auth_key=sync_auth_key,
                bind_port=sync_port,
                logger=self.logger,
                on_economy_mutated=self._schedule_richest_title_refresh,
            )
            if self.sync_server.start():
                self.logger.info(f"[ARC Core] Sync server started on port {sync_port}")
            else:
                self.logger.error("[ARC Core] Failed to start sync server")

        if self._sync_consumer_mode == "client":
            self.sync_client = SyncClient(
                database_manager=self.database_manager,
                setting_manager=self.setting_manager,
                logger=self.logger,
            )
            self.sync_client.set_settings_callback(self._apply_synced_settings)
            if self.sync_client.start():
                self.logger.info(
                    "[ARC Core] Sync client started "
                    "(auto-reconnect if sync host is down)"
                )
            else:
                self.logger.error("[ARC Core] Failed to start sync client")

        self.setting_manager.add_change_listener(self._on_local_setting_changed)

        # 本地写库上行/主机写库广播（签到、经济、称号、公会等）
        self.database_manager.set_write_notifier(self._on_db_write_for_sync)

    def _apply_synced_settings(self, settings: Dict[str, str]) -> None:
        """从服收到同步中心玩法配置后写入本地并刷新缓存。"""
        def apply():
            try:
                changed = self.setting_manager.ApplySettings(settings)
                if changed:
                    self._reapply_cached_settings()
                    self.logger.info(
                        f"[ARC Core] Applied {changed} gameplay setting(s) from sync host"
                    )
            except Exception as e:
                self.logger.error(f"[ARC Core] Apply synced settings error: {e}")

        try:
            self.server.scheduler.run_task(self, apply)
        except Exception:
            apply()

    def _on_local_setting_changed(self, key: str, value: str) -> None:
        """主服改玩法配置则推送；从服改同一批键则立刻向主服重新拉取覆盖。"""
        if key not in ALL_SHARED_SETTING_KEYS:
            return
        if self.sync_server and self.sync_server.is_running():
            self.sync_server.broadcast_settings({key: value})
            return
        if self.sync_client and self.sync_client.is_running():
            self.sync_client.request_settings()

    def on_disable(self) -> None:
        try:
            self._settle_all_online_playtime()
        except Exception as e:
            if self.logger:
                self.logger.error(f"[ARC Core] Settle playtime on disable error: {e}")
        # 停止位置检测线程
        self.stop_position_thread()
        # 停止跨服同步服务
        if self.sync_client:
            self.sync_client.stop()
            self.logger.info("[ARC Core] Sync client stopped.")
        if self.sync_server and self.sync_server.is_running():
            self.sync_server.stop()
            self.logger.info("[ARC Core] Sync server stopped.")
        try:
            if getattr(self, "activity_stats", None) is not None:
                self.activity_stats.stop_writer()
        except Exception:
            pass
        try:
            if getattr(self, "sky_eye_store", None) is not None:
                self.sky_eye_store.close()
        except Exception:
            pass
        try:
            if getattr(self, "sidebar_system", None) is not None:
                self.sidebar_system.shutdown()
        except Exception:
            pass
        self.logger.info(f"{ColorFormat.YELLOW}[ARC Core]Plugin disabled!")

    def _arc_persistent_error(
        self,
        error_code: str,
        detail: str,
        exception=None,
        extra_context: Optional[Dict[str, Any]] = None,
    ) -> None:
        """写入 error_log.txt 并打 logger（不向玩家发消息）。"""
        append_arc_error_log(
            self._arc_error_log_path,
            error_code,
            detail,
            exception,
            format_context_lines(extra_context),
        )
        if self.logger:
            suffix = f" | {exception}" if exception else ""
            self.logger.error(
                f"{ColorFormat.RED}[ARC Core][{error_code}] {detail}{suffix}"
            )

    def report_arc_error(
        self,
        error_code: str,
        detail: str,
        player_for_notify=None,
        exception=None,
        **extra_context,
    ) -> None:
        """恶性错误：落盘 + 控制台 + 可选通知在线玩家（带错误代码）。"""
        ctx = extra_context if extra_context else None
        self._arc_persistent_error(error_code, detail, exception, ctx)
        if player_for_notify is not None:
            try:
                msg = self.language_manager.GetText("SYSTEM_ERROR_WITH_CODE")
                if not msg or not str(msg).strip():
                    msg = (
                        "[弧光核心]系统异常 ({0})，请将错误代码告知管理员，"
                        "并将 plugins/ARCCore/error_log.txt 中的对应记录发给弧光核心作者。"
                    )
                player_for_notify.send_message(msg.format(error_code))
            except Exception:
                pass

    def on_command(self, sender: CommandSender, command: Command, args: list[str]) -> bool:
        if command.name == 'updatespawnpos':
            if not isinstance(sender, Player):
                sender.send_message(f'[ARC Core]This command only works for players.')
                return True
            dimension_name = get_dimension_id(sender.location.dimension)
            new_spawn_pos = (sender.location.x, sender.location.y, sender.location.z)
            r = self.update_spawn_location(dimension_name, new_spawn_pos)
            if r:
                self.spawn_pos_dict[dimension_name] = new_spawn_pos
                sender.send_message(self.language_manager.GetText('UPDATE_SPAWN_POS_SUCCESSFUL').format(dimension_name, new_spawn_pos))
            else:
                sender.send_message(self.language_manager.GetText('UPDATE_SPAWN_POS_FAILED'))
            return True
        if command.name == "arc":
            player = self._resolve_player_for_command_sender(sender)
            if player is None:
                return True
            if args and len(args) >= 1:
                head = args[0].lower()
                if head == "op":
                    if not player.is_op:
                        player.send_message(self.language_manager.GetText("OP_PANEL_NO_PERMISSION"))
                        return True
                    self.show_op_main_panel(player)
                    return True
                if head == "land":
                    self.show_land_main_menu(player)
                    return True
                if head == "tp":
                    self.show_teleport_menu(player)
                    return True
                if head == "bank":
                    self.show_bank_main_menu(player)
                    return True
                if head == "guild":
                    self.show_guild_main_menu(player)
                    return True
            self.show_main_menu(player)
            return True
        if command.name == "suicide":
            if not isinstance(sender, Player):
                sender.send_message(f'[ARC Core]This command only works for players.')
                return True
            self.server.dispatch_command(
                self.server.command_sender,
                f'kill {format_mc_command_player_name(sender.name)}',
            )
            self.server.broadcast_message(self.language_manager.GetText('PLAYER_SUICIDE_MESSAGE').format(sender.name))
            return True
        if command.name == "spawn":
            if not isinstance(sender, Player):
                sender.send_message(f'[ARC Core]This command only works for players.')
                return True
            spawn_xyz = self.spawn_pos_dict.get(get_dimension_id(sender.location.dimension))
            if spawn_xyz is not None:
                self.server.dispatch_command(
                    self.server.command_sender,
                    f'tp {format_mc_command_player_name(sender.name)} '
                    f'{int(spawn_xyz[0])} {int(spawn_xyz[1])} {int(spawn_xyz[2])}',
                )
                sender.send_message(self.language_manager.GetText('PLAYER_TELEPORTED_TO_SPAWN_HINT'))
            else:
                sender.send_message(self.language_manager.GetText('NO_SPAWN_POSITION_SET_MESSAGE'))
            return True
        if command.name == "land":
            player = self._resolve_player_for_command_sender(sender)
            if player is None:
                return True
            if not args:
                player.send_message(self.language_manager.GetText("LAND_COMMAND_USAGE"))
                return True
            sub = args[0].lower()
            if sub == "pos1":
                self._run_land_pos1_for_player(player)
                return True
            if sub == "pos2":
                self._run_land_pos2_for_player(player)
                return True
            if sub == "buy":
                self._run_land_buy_for_player(player)
                return True
            player.send_message(self.language_manager.GetText("LAND_COMMAND_USAGE"))
            return True
        if command.name == "tpa":
            return self._handle_tpa_command(sender, args)
        if command.name == 'pos1':
            if not isinstance(sender, Player):
                sender.send_message(f'[ARC Core]This command only works for players.')
                return True
            if not sender.is_op:
                sender.send_message(self.language_manager.GetText('OP_PANEL_NO_PERMISSION'))
                return True
            self.record_coordinate_1(sender)
            sender.send_message(self.language_manager.GetText('POS1_RECORDED'))
            return True
        if command.name == 'pos2':
            if not isinstance(sender, Player):
                sender.send_message(f'[ARC Core]This command only works for players.')
                return True
            if not sender.is_op:
                sender.send_message(self.language_manager.GetText('OP_PANEL_NO_PERMISSION'))
                return True
            self.record_coordinate_2(sender)
            return True
        if command.name == 'connecttoserver':
            sender_type_name = type(sender).__name__
            sender_name = str(getattr(sender, 'name', '') or '')
            sender_has_is_player = hasattr(sender, 'is_player')
            sender_is_player_attr = getattr(sender, 'is_player', None)
            sender_has_xuid = hasattr(sender, 'xuid')
            args_text = " ".join(args) if args else ""
            if not isinstance(sender, Player):
                fallback_player = self._resolve_player_from_sender_name(sender_name)
                self._safe_log(
                    "warning",
                    (
                        "[ARC Core][connecttoserver debug] non-player sender blocked: "
                        f"type={sender_type_name}, name={sender_name or '-'}, "
                        f"has_is_player={sender_has_is_player}, is_player={sender_is_player_attr}, "
                        f"has_xuid={sender_has_xuid}, args={args_text or '-'}, "
                        f"fallback_player={getattr(fallback_player, 'name', '-')}"
                    ),
                )
                if fallback_player is None:
                    return True
                sender = fallback_player
            self._safe_log(
                "info",
                (
                    "[ARC Core][connecttoserver debug] player sender accepted: "
                    f"name={sender.name}, xuid={sender.xuid}, args={args_text or '-'}"
                ),
            )
            if not self.teleport_system.enable_teleport_cross_server:
                self._notify_teleport_feature_disabled(sender)
                return True
            if not args or not str(" ".join(args)).strip():
                self.show_cross_server_menu(sender, on_close=self.show_main_menu)
                return True
            server_name = " ".join(args).strip()
            target = self._get_cross_server_target_by_name(server_name)
            if not target:
                sender.send_message(self.language_manager.GetText('CONNECT_TO_SERVER_NOT_FOUND').format(server_name))
                return True
            self.transfer_player_to_server(
                sender,
                str(target.get('server_host') or ''),
                int(target.get('server_port') or 19132),
                str(target.get('server_name') or server_name),
            )
            return True
        if command.name in ("sidebar", "sb"):
            return self._handle_sidebar_command(sender, args)
        return False

    def _handle_sidebar_command(self, sender: CommandSender, args: list[str]) -> bool:
        player = self._resolve_player_for_command_sender(sender)
        if player is None:
            if not isinstance(sender, Player):
                sender.send_message("[ARC Core]This command only works for players.")
            return True
        if not getattr(self, "sidebar_system", None) or not self.sidebar_system.enabled:
            msg = self.language_manager.GetText("SIDEBAR_DISABLED") or "[弧光核心]侧边栏功能未启用。"
            player.send_message(msg)
            return True

        def _t(key: str, fallback: str) -> str:
            text = self.language_manager.GetText(key)
            return text if text and str(text).strip() else fallback

        sub = (args[0].lower() if args else "").strip()
        rest = args[1:] if len(args) > 1 else []

        if not sub or sub in ("toggle", "switch"):
            was_on = self.sidebar_system.is_enabled_for(player)
            self.sidebar_system.toggle_enabled(player)
            if was_on:
                player.send_message(_t("SIDEBAR_OFF", "[弧光核心]侧边栏已关闭。"))
            else:
                player.send_message(_t("SIDEBAR_ON", "[弧光核心]侧边栏已开启。"))
            return True
        if sub == "on":
            self.sidebar_system.set_enabled(player, True)
            player.send_message(_t("SIDEBAR_ON", "[弧光核心]侧边栏已开启。"))
            return True
        if sub == "off":
            self.sidebar_system.set_enabled(player, False)
            player.send_message(_t("SIDEBAR_OFF", "[弧光核心]侧边栏已关闭。"))
            return True
        if sub == "next":
            page = self.sidebar_system.flip_page(player, 1)
            if page is None:
                player.send_message(_t("SIDEBAR_NO_PAGE", "[弧光核心]当前没有可显示的侧边栏页面。"))
            else:
                player.send_message(
                    _t("SIDEBAR_FLIPPED", "[弧光核心]已切换到：{0}").format(page.title)
                )
            return True
        if sub == "prev":
            page = self.sidebar_system.flip_page(player, -1)
            if page is None:
                player.send_message(_t("SIDEBAR_NO_PAGE", "[弧光核心]当前没有可显示的侧边栏页面。"))
            else:
                player.send_message(
                    _t("SIDEBAR_FLIPPED", "[弧光核心]已切换到：{0}").format(page.title)
                )
            return True
        if sub == "lock":
            ref = " ".join(rest).strip() if rest else ""
            page = self.sidebar_system.lock_page(player, ref)
            if page is None:
                player.send_message(_t("SIDEBAR_NO_PAGE", "[弧光核心]当前没有可显示的侧边栏页面。"))
            else:
                player.send_message(
                    _t("SIDEBAR_LOCKED", "[弧光核心]已锁定页面：{0}").format(page.title)
                )
            return True
        if sub == "unlock":
            self.sidebar_system.unlock_page(player)
            player.send_message(_t("SIDEBAR_UNLOCKED", "[弧光核心]已解除页面锁定，恢复自动翻页。"))
            return True
        if sub == "list":
            pages = self.sidebar_system.get_visible_pages_for_player(player)
            if not pages:
                player.send_message(_t("SIDEBAR_NO_PAGE", "[弧光核心]当前没有可显示的侧边栏页面。"))
                return True
            cur = self.sidebar_system.get_current_page(player)
            lines = []
            for i, p in enumerate(pages, start=1):
                mark = " §a◀" if cur is not None and p.page_id == cur.page_id else ""
                lines.append(f"§e{i}. §f{p.title} §7({p.page_id}){mark}")
            header = _t("SIDEBAR_PAGE_LIST", "[弧光核心]侧边栏页面列表：")
            player.send_message(header + "\n" + "\n".join(lines))
            return True

        player.send_message(
            _t(
                "SIDEBAR_USAGE",
                "[弧光核心]用法：/sidebar [on|off|next|prev|lock|unlock|list]",
            )
        )
        return True

    @staticmethod
    def _strip_minecraft_format_codes(text: str) -> str:
        return re.sub(r"§.", "", str(text or ""))

    def _extract_player_name_from_sender_name(self, sender_name: str) -> str:
        normalized_sender_name = str(sender_name or "").strip()
        if not normalized_sender_name:
            return ""
        if "§r" in normalized_sender_name:
            normalized_sender_name = normalized_sender_name.rsplit("§r", 1)[-1]
        return self._strip_minecraft_format_codes(normalized_sender_name).strip()

    def _resolve_player_from_sender_name(self, sender_name: str) -> Optional[Player]:
        extracted_player_name = self._extract_player_name_from_sender_name(sender_name)
        if not extracted_player_name:
            return None
        try:
            resolved_player = self.server.get_player(extracted_player_name)
            if resolved_player is not None:
                return resolved_player
        except Exception:
            pass
        for online_player in list(getattr(self.server, "online_players", []) or []):
            online_player_name = str(getattr(online_player, "name", "") or "").strip()
            if online_player_name.lower() == extracted_player_name.lower():
                return online_player
        return None

    def _resolve_player_for_command_sender(self, sender: CommandSender) -> Optional[Player]:
        """真实玩家直接返回；控制台/命令方块等从 sender 名称解析在线玩家（与 connecttoserver 一致）。"""
        if isinstance(sender, Player):
            return sender
        return self._resolve_player_from_sender_name(str(getattr(sender, "name", "") or ""))

    def _run_land_pos1_for_player(self, player: Player) -> None:
        if not self._ensure_land_claim_allowed(player):
            return
        self.require_sensitive_password_verified(
            player,
            self._run_land_pos1_for_player_verified,
            on_cancel=None,
        )

    def _run_land_pos1_for_player_verified(self, player: Player) -> None:
        self.player_land_pos1[player.name] = {
            "dimension": get_dimension_id(player.location.dimension),
            "x": math.floor(player.location.x),
            "y": math.floor(player.location.y),
            "z": math.floor(player.location.z),
        }
        pos = self.player_land_pos1[player.name]
        player.send_message(
            self.language_manager.GetText("CREATE_NEW_LAND_POS1_SET").format(
                pos["dimension"],
                (pos["x"], pos["y"], pos["z"]),
            )
        )

    def _run_land_pos2_for_player(self, player: Player) -> None:
        if not self._ensure_land_claim_allowed(player):
            return
        self.require_sensitive_password_verified(
            player,
            self._run_land_pos2_for_player_verified,
            on_cancel=None,
        )

    def _run_land_pos2_for_player_verified(self, player: Player) -> None:
        if player.name not in self.player_land_pos1:
            player.send_message(self.language_manager.GetText("CREATE_NEW_LAND_POS2_SET_FAIL_POS1_NOT_SET"))
            return
        pos1 = self.player_land_pos1[player.name]
        if get_dimension_id(player.location.dimension) != pos1["dimension"]:
            player.send_message(self.language_manager.GetText("CREATE_NEW_LAND_POS2_SET_FAIL_DIMENSION_CHANGED"))
            return
        x2 = math.floor(player.location.x)
        y2 = math.floor(player.location.y)
        z2 = math.floor(player.location.z)
        self.player_new_land_creation_info[player.name] = {
            "dimension": pos1["dimension"],
            "min_x": min(pos1["x"], x2),
            "max_x": max(pos1["x"], x2),
            "min_y": min(pos1["y"], y2),
            "max_y": max(pos1["y"], y2),
            "min_z": min(pos1["z"], z2),
            "max_z": max(pos1["z"], z2),
        }
        del self.player_land_pos1[player.name]
        player.send_message(self.language_manager.GetText("CREATE_NEW_LAND_POS2_SET").format((x2, y2, z2)))
        self._visualize_pending_land(player)
        self.show_pending_land_purchase_panel(player)

    def _run_land_buy_for_player(self, player: Player) -> None:
        if not self._ensure_land_claim_allowed(player):
            return
        self.require_sensitive_password_verified(
            player,
            self._run_land_buy_for_player_verified,
            on_cancel=None,
        )

    def _run_land_buy_for_player_verified(self, player: Player) -> None:
        self._execute_land_buy(player)

    def _broadcast_text(self, text: str) -> None:
        """Broadcast non-empty chat text. Endstone rejects empty messages."""
        msg = str(text or "").strip()
        if msg:
            self.server.broadcast_message(msg)

    def _send_text(self, player, text: str) -> None:
        """Send non-empty chat text to one player."""
        msg = str(text or "").strip()
        if player is not None and msg:
            player.send_message(msg)

    # Event handlers
    @event_handler
    def on_player_join(self, event: PlayerJoinEvent):
        player = event.player
        # 在玩家加入时立即初始化玩家数据（基本信息和经济数据）
        success, is_new_player = self.ensure_player_data_initialized(player)

        # 如果是新玩家，执行新人欢迎功能
        if is_new_player and success:
            self._send_newbie_welcome_message(player)
            self._execute_newbie_commands(player)

        self._broadcast_text(
            self.language_manager.GetText('PLAYER_JOIN_MESSAGE').format(player.name)
        )
        self.player_sensitive_password_verified.pop(player.name, None)
        self._pending_sensitive_action_by_player.pop(player.name, None)

        # Join 完成后再开始追踪；此前 GameModeChange 等加载事件一律忽略
        self._sky_eye_mark_ready(player)

        join_delay_ticks = 40
        if getattr(self, "sidebar_system", None) is not None:
            join_delay_ticks = max(1, int(self.sidebar_system.join_delay_ticks))

        def _join_hints(p):
            self._send_text(p, self.language_manager.GetText('PLAYER_JOIN_HINT'))
            try:
                player_xuid = str(p.xuid)
                pending_info = self.database_manager.query_one(
                    "SELECT pending_invite_reward_times FROM player_basic_info WHERE xuid = ?",
                    (player_xuid,),
                )
                if pending_info is not None:
                    try:
                        pending_times = int(
                            pending_info.get('pending_invite_reward_times', 0) or 0
                        )
                    except (ValueError, TypeError):
                        pending_times = 0
                    if pending_times > 0:
                        p.send_message(
                            self.language_manager.GetText(
                                'INVITE_REWARD_PENDING_HINT'
                            ).format(pending_times)
                        )
            except Exception as e:
                self.logger.error(
                    f"{ColorFormat.RED}[ARC Core]Check pending invite rewards on join error: {str(e)}"
                )

        def _join_sky_eye(p):
            try:
                join_loc = getattr(p, "location", None)
                if join_loc is not None and getattr(join_loc, "dimension", None) is not None:
                    self._sky_eye_append(
                        "PlayerJoin",
                        p,
                        get_dimension_id(join_loc.dimension),
                        float(join_loc.x),
                        float(join_loc.y),
                        float(join_loc.z),
                        detail=f"new_player={bool(is_new_player and success)}",
                    )
            except Exception:
                pass

        def _join_sidebar(p):
            try:
                if getattr(self, "sidebar_system", None) is not None:
                    self.sidebar_system.on_player_join(p)
            except Exception:
                pass

        self.run_player_task(
            player,
            lambda p: (
                self.title_system.on_player_join(p),
                self._auto_equip_highest_title_if_needed(p),
                self._update_player_name_tag(p),
            ),
            delay=2,
        )
        self.run_player_task(player, self._record_player_join_playtime, delay=4)
        self.run_player_task(player, _join_hints, delay=6)
        self.run_player_task(player, _join_sky_eye, delay=8)
        self.run_player_task(player, _join_sidebar, delay=join_delay_ticks)
        self.run_player_task(
            player,
            self._show_join_arc_main_menu_with_delay,
            delay=20,
        )

    def _sky_eye_player_key(self, player: Optional[Player]) -> str:
        if player is None:
            return ""
        xuid = str(getattr(player, "xuid", "") or "").strip()
        if xuid:
            return xuid
        return str(getattr(player, "name", "") or "").strip()

    def _sky_eye_mark_ready(self, player: Optional[Player]) -> None:
        key = self._sky_eye_player_key(player)
        if key:
            self._sky_eye_ready_xuids.add(key)

    def _sky_eye_clear_ready(self, player: Optional[Player]) -> None:
        key = self._sky_eye_player_key(player)
        if key:
            self._sky_eye_ready_xuids.discard(key)

    def _sky_eye_is_ready(self, player: Optional[Player]) -> bool:
        key = self._sky_eye_player_key(player)
        return bool(key) and key in self._sky_eye_ready_xuids

    def _show_join_arc_main_menu_with_delay(self, player: Player):
        """进服后延迟弹出主菜单一次（与配置无关）；玩家可关闭表单，随时可用 /arc 再次打开。"""
        self.show_main_menu(player)

    @event_handler
    def on_player_respawn(self, event: PlayerRespawnEvent):
        """玩家重生后重新设置 name_tag 为 [头衔]名字，防止重生后头顶名被重置。"""
        self._update_player_name_tag(event.player)

    @event_handler
    def on_player_chat(self, event: PlayerChatEvent):
        """远古 QQ 风格：首行 [头衔]玩家名(年.月.日-时:分)：，下一行消息内容。取消原事件并自行广播。"""
        try:
            raw_message = event.message
            now = datetime.now()
            time_str = f"{now.year}.{now.month}.{now.day}-{now.hour}:{now.minute:02d}"
            equipped = self.title_system.get_equipped_title(event.player)
            name = event.player.name
            base = self.format_player_display_label_with_guild(
                name, equipped, str(event.player.xuid)
            )
            line1 = f"{base}({time_str})："
            formatted = line1 + "\n" + raw_message
            event.is_cancelled = True
            self.server.broadcast_message(formatted)
            try:
                if not self._sky_eye_is_ready(event.player):
                    return
                chat_loc = getattr(event.player, "location", None)
                if chat_loc is not None and getattr(chat_loc, "dimension", None) is not None:
                    self._sky_eye_append(
                        "PlayerChat",
                        event.player,
                        get_dimension_id(chat_loc.dimension),
                        float(chat_loc.x),
                        float(chat_loc.y),
                        float(chat_loc.z),
                        detail=str(raw_message or "")[:300],
                    )
            except Exception:
                pass

        except Exception as e:
            if self.logger:
                self.logger.error(f"[ARC Core]Chat title format error: {e}")

    @event_handler
    def on_player_command(self, event: PlayerCommandEvent):
        try:
            player = event.player
            if not self._sky_eye_is_ready(player):
                return
            raw = str(getattr(event, "command", "") or "").strip()
            if not raw:
                return
            cmd = raw if raw.startswith("/") else f"/{raw}"
            loc = getattr(player, "location", None)
            if loc is not None and getattr(loc, "dimension", None) is not None:
                self._sky_eye_append(
                    "PlayerCommand",
                    player,
                    get_dimension_id(loc.dimension),
                    float(loc.x),
                    float(loc.y),
                    float(loc.z),
                    detail=cmd[:300],
                )
            else:
                self.api_sky_eye_log(
                    "PlayerCommand",
                    player=player,
                    detail=cmd[:300],
                )
        except Exception:
            pass

    @event_handler
    def on_server_command(self, event: ServerCommandEvent):
        try:
            raw = str(getattr(event, "command", "") or "").strip()
            if not raw:
                return
            cmd = raw if raw.startswith("/") else f"/{raw}"
            sender = getattr(event, "sender", None)
            sender_name = str(getattr(sender, "name", "") or "Console").strip() or "Console"
            # 天星/插件 dispatch 的指令由 AI Helper 另行记 AgentCommand，这里只记控制台真人输入
            if sender_name.lower() in {"server", "console", "rcon"}:
                self.api_sky_eye_log(
                    "ConsoleCommand",
                    player_name=sender_name,
                    detail=cmd[:300],
                    resolve_online=False,
                )
        except Exception:
            pass

    @event_handler
    def on_player_gamemode_change(self, event: PlayerGameModeChangeEvent):
        """仅 PlayerJoin 完成后记录；进服加载期触发则直接忽略（读 location 会崩服）。"""
        try:
            player = event.player
            if not self._sky_eye_is_ready(player):
                return
            new_mode = getattr(event, "new_game_mode", None)
            if new_mode is None:
                new_mode = getattr(event, "game_mode", None)
            mode_text = str(getattr(new_mode, "name", None) or new_mode or "?")
            detail = f"gamemode={mode_text}"
            loc = getattr(player, "location", None)
            if loc is not None and getattr(loc, "dimension", None) is not None:
                self._sky_eye_append(
                    "GameModeChange",
                    player,
                    get_dimension_id(loc.dimension),
                    float(loc.x),
                    float(loc.y),
                    float(loc.z),
                    detail=detail,
                )
            else:
                self.api_sky_eye_log(
                    "GameModeChange",
                    player_name=str(getattr(player, "name", "") or "").strip() or "?",
                    player_xuid=str(getattr(player, "xuid", "") or "").strip(),
                    detail=detail,
                    resolve_online=False,
                )
        except Exception:
            pass

    @event_handler
    def on_player_quit(self, event: PlayerQuitEvent):
        player = event.player
        player_name = str(getattr(player, "name", "") or "")
        player_xuid = str(getattr(player, "xuid", "") or "")
        quit_dimension = ""
        quit_x = quit_y = quit_z = 0.0
        try:
            quit_loc = getattr(player, "location", None)
            if quit_loc is not None and getattr(quit_loc, "dimension", None) is not None:
                quit_dimension = get_dimension_id(quit_loc.dimension)
                quit_x = float(quit_loc.x)
                quit_y = float(quit_loc.y)
                quit_z = float(quit_loc.z)
        except Exception:
            pass

        self._sky_eye_clear_ready(player)
        self._broadcast_text(
            self.language_manager.GetText('PLAYER_QUIT_MESSAGE').format(player_name)
        )
        self.player_sensitive_password_verified.pop(player_name, None)
        self._pending_sensitive_action_by_player.pop(player_name, None)

        # 线程安全地清理玩家领地位置记录
        with self.position_thread_lock:
            if player_name in self.player_in_land_id_dict:
                del self.player_in_land_id_dict[player_name]
        self.player_land_creation_pick.pop(player_name, None)
        self.player_land_pick_last_event_ts.pop(player_name, None)
        self._cancel_land_particle_boundary(player_xuid, player_name)
        try:
            if getattr(self, "sidebar_system", None) is not None:
                self.sidebar_system.on_player_quit(player)
        except Exception:
            pass
        try:
            xuid = str(getattr(player, "xuid", "") or "").strip()
            if xuid:
                self._sky_eye_hand_cache.pop(xuid, None)
        except Exception:
            pass

        def _deferred_quit_work(
            xuid=player_xuid,
            name=player_name,
            dimension=quit_dimension,
            px=quit_x,
            py=quit_y,
            pz=quit_z,
        ) -> None:
            try:
                if dimension:
                    self.api_sky_eye_log(
                        "PlayerQuit",
                        player_name=name,
                        player_xuid=xuid,
                        dimension=dimension,
                        x=px,
                        y=py,
                        z=pz,
                        resolve_online=False,
                    )
            except Exception:
                pass
            try:
                self._record_player_quit_playtime(
                    player=None, xuid=xuid, name=name
                )
            except Exception:
                pass

        self.server.scheduler.run_task(self, _deferred_quit_work, delay=1)

    @event_handler
    def on_block_break(self, event: BlockBreakEvent):
        block_loc = event.block.location
        target_desc = getattr(event.block, 'identifier', getattr(event.block, 'type', 'block'))
        self._sky_eye_append(
            "BlockBreak",
            event.player,
            get_dimension_id(block_loc.dimension),
            float(block_loc.x),
            float(block_loc.y),
            float(block_loc.z),
            detail=f"block={target_desc}",
        )
        self._send_op_debug_message(
            event.player, 'BlockBreak', str(target_desc),
            get_dimension_id(block_loc.dimension), block_loc.x, block_loc.y, block_loc.z
        )
        if event.player.is_op:
            self._activity_record_block_break(event.player, target_desc)
            return

        if self.dtwt_plugin is not None and self.dtwt_plugin.api_judge_if_start_block(event.block.location.x, event.block.location.y, event.block.location.z, get_dimension_id(event.block.dimension)):
            # print('DTWT block break, ignore')
            self._activity_record_block_break(event.player, target_desc)
            return

        if not self.land_operation_check(event.player, get_dimension_id(event.block.location.dimension),
                                    (event.block.location.x, event.block.location.y, event.block.location.z)):
            event.is_cancelled = True
        if not event.is_cancelled and self._is_frame_block(event.block):
            dim = get_dimension_id(event.block.location.dimension)
            bpos = (
                event.block.location.x,
                event.block.location.y,
                event.block.location.z,
            )
            if not self.check_land_permission(event.player, dim, bpos, "frame"):
                event.is_cancelled = True
        if not self.spawn_protect_check(event.player, get_dimension_id(event.block.location.dimension),
                                    (event.block.location.x, event.block.location.y, event.block.location.z)):
            event.is_cancelled = True

        if not getattr(event, "is_cancelled", False):
            self._activity_record_block_break(event.player, target_desc)
        return

    def _is_frame_block(self, block) -> bool:
        """是否属于与 allow_frame 联动的方块（见 PUBLIC_LAND_INTERACT_BLOCK_BLACKLIST，默认含展示框与各材质展示架）"""
        bid = str(
            getattr(block, 'identifier', None) or getattr(block, 'type', None) or ''
        ).lower()
        if not bid:
            return False
        return bid in self.land_system.get_public_land_interact_block_blacklist()

    def _arc_block_type_descriptor(self, block) -> Optional[str]:
        """从 Block 解析类型 ID（官方 Block API 以 type 为准；identifier / data.type 作补充）"""
        if block is None:
            return None
        raw_type = getattr(block, 'type', None)
        if raw_type is not None:
            text = str(raw_type).strip()
            if text:
                return text
        ident = getattr(block, 'identifier', None)
        if ident is not None:
            text = str(ident).strip()
            if text:
                return text
        data = getattr(block, 'data', None)
        if data is not None:
            dt = getattr(data, 'type', None)
            if dt is not None:
                text = str(dt).strip()
                if text:
                    return text
        return None

    @staticmethod
    def _arc_is_air_block_type(desc: Optional[str]) -> bool:
        if not desc:
            return True
        normalized = str(desc).lower().strip()
        return normalized in ('air', 'minecraft:air')

    def _arc_block_place_event_location(self, event: BlockPlaceEvent):
        """放置坐标优先取 block_placed（与官方 BlockPlaceEvent 一致），否则 BlockEvent.block。"""
        placed = getattr(event, 'block_placed', None)
        if placed is not None and getattr(placed, 'location', None) is not None:
            return placed.location
        fallback = getattr(event, 'block', None)
        if fallback is not None and getattr(fallback, 'location', None) is not None:
            return fallback.location
        return None

    def _arc_placed_block_type_from_place_event(self, event: BlockPlaceEvent) -> str:
        """
        BlockPlaceEvent：block_placed 为「放置后的方块」；若绑定层仍为空气则依次尝试
        block_placed_state、主手物品 ItemType.id、BlockEvent.block。
        """
        placed = getattr(event, 'block_placed', None)
        desc = self._arc_block_type_descriptor(placed)
        if desc is not None and not self._arc_is_air_block_type(desc):
            return desc

        placed_state = getattr(event, 'block_placed_state', None)
        if placed_state is not None:
            st_type = getattr(placed_state, 'type', None)
            if st_type is not None:
                text = str(st_type).strip()
                if text and not self._arc_is_air_block_type(text):
                    return text
            state_block = getattr(placed_state, 'block', None)
            desc = self._arc_block_type_descriptor(state_block)
            if desc is not None and not self._arc_is_air_block_type(desc):
                return desc

        player = getattr(event, 'player', None)
        if player is not None:
            inv = getattr(player, 'inventory', None)
            if inv is not None:
                stack = getattr(inv, 'item_in_main_hand', None)
                if stack is not None:
                    item_type = getattr(stack, 'type', None)
                    if item_type is not None:
                        item_id = getattr(item_type, 'id', None)
                        if item_id:
                            return str(item_id)
                        if isinstance(item_type, str) and item_type.strip():
                            return item_type.strip()

        eb = getattr(event, 'block', None)
        desc = self._arc_block_type_descriptor(eb)
        if desc is not None:
            return desc
        return 'block'

    @event_handler
    def on_block_place(self, event: BlockPlaceEvent):
        block_loc = self._arc_block_place_event_location(event)
        if block_loc is None:
            return
        target_desc = self._arc_placed_block_type_from_place_event(event)
        self._sky_eye_append(
            "BlockPlace",
            event.player,
            get_dimension_id(block_loc.dimension),
            float(block_loc.x),
            float(block_loc.y),
            float(block_loc.z),
            detail=f"block={target_desc}",
        )
        self._send_op_debug_message(
            event.player, 'BlockPlace', str(target_desc),
            get_dimension_id(block_loc.dimension), block_loc.x, block_loc.y, block_loc.z
        )
        if event.player.is_op:
            self._activity_record_block_place(event.player, target_desc)
            return
        dimension_name = get_dimension_id(block_loc.dimension)
        place_pos = (block_loc.x, block_loc.y, block_loc.z)
        if self._is_disabled_block(target_desc):
            event.is_cancelled = True
            event.player.send_message(self.language_manager.GetText("DISABLED_BLOCK_DENIED_HINT"))
            return
        if not self.land_only_place_wilderness_check(
            event.player,
            dimension_name,
            place_pos,
            target_desc,
        ):
            event.is_cancelled = True
        if not self.land_operation_check(event.player, dimension_name, place_pos):
            event.is_cancelled = True
        if not self.spawn_protect_check(event.player, dimension_name, place_pos):
            event.is_cancelled = True
        if not getattr(event, "is_cancelled", False):
            self._activity_record_block_place(event.player, target_desc)
        return
    
    @event_handler
    def on_player_death(self, event: PlayerDeathEvent):
        try:
            loc = event.player.location
            death_cause = self._get_death_cause(event)
            killer_name = ""
            killer_xuid = ""
            killer_type = ""
            try:
                killer = self._sky_eye_resolve_killer_actor(event)
                killer_name, killer_xuid, killer_type = self._sky_eye_identity(killer)
            except Exception:
                pass
            detail = f"cause={death_cause}" if death_cause else "cause=-"
            if killer_name:
                detail = f"{detail};killer={killer_name}"
            try:
                hp, max_hp = self._sky_eye_read_health(event.player)
                if hp:
                    detail = f"{detail};hp={hp}"
                if max_hp:
                    detail = f"{detail};max_hp={max_hp}"
            except Exception:
                pass
            try:
                armor = self._sky_eye_format_equipment(event.player)
                if armor and armor != "-":
                    detail = f"{detail};armor={armor}"
            except Exception:
                pass
            try:
                inv_text = self._sky_eye_format_inventory(event.player)
                if inv_text and inv_text != "-":
                    detail = f"{detail};inv={inv_text}"
            except Exception:
                pass
            self._sky_eye_append(
                "PlayerDeath",
                event.player,
                get_dimension_id(loc.dimension),
                float(loc.x),
                float(loc.y),
                float(loc.z),
                detail=detail,
                target_name=killer_name,
                target_xuid=killer_xuid,
                target_type=killer_type,
            )
        except Exception:
            pass

        # 记录玩家死亡位置
        self.teleport_system.record_death_location(
            event.player.name,
            get_dimension_id(event.player.location.dimension),
            event.player.location.x,
            event.player.location.y,
            event.player.location.z,
        )
        event.player.send_message(self.language_manager.GetText('DEATH_LOCATION_RECORDED'))
        
        # 发送死亡播报
        self._send_death_broadcast(event)

        # 玩家击杀玩家：计入活动统计（含 kill:minecraft:player）
        try:
            killer = self._sky_eye_resolve_killer_actor(event)
            if killer is not None and (
                isinstance(killer, Player)
                or getattr(killer, "type", None) == "minecraft:player"
            ):
                kxuid = str(getattr(killer, "xuid", "") or "").strip()
                if kxuid:
                    self.activity_stats.record_kill(kxuid, "minecraft:player")
                    self._notify_achievement_activity(kxuid, "kill", "minecraft:player")
        except Exception:
            pass

    @event_handler
    def on_player_interact(self, event: PlayerInteractEvent):
        """处理玩家交互事件，保护领地免受非法交互"""
        try:
            # 玩家或OP判定
            if not hasattr(event, 'player') or event.player is None:
                return
            try:
                if getattr(event, "has_block", False):
                    block = getattr(event, "block", None)
                    if block is not None and getattr(block, "location", None) is not None:
                        bl = block.location
                        if getattr(bl, "dimension", None) is not None:
                            dim_name = get_dimension_id(bl.dimension)
                        else:
                            ploc = getattr(event.player, "location", None)
                            dim_name = (
                                get_dimension_id(ploc.dimension)
                                if ploc is not None and getattr(ploc, "dimension", None) is not None
                                else ""
                            )
                        block_target_desc = str(
                            getattr(block, "identifier", getattr(block, "type", "block"))
                        )
                        self._sky_eye_append(
                            "BlockInteract",
                            event.player,
                            dim_name,
                            float(bl.x),
                            float(bl.y),
                            float(bl.z),
                            detail=f"block={block_target_desc}",
                        )
                    else:
                        pl = event.player.location
                        self._sky_eye_append(
                            "BlockInteract",
                            event.player,
                            get_dimension_id(pl.dimension),
                            float(pl.x),
                            float(pl.y),
                            float(pl.z),
                            detail="block=?",
                        )
                else:
                    pl = event.player.location
                    self._sky_eye_append(
                        "AirInteract",
                        event.player,
                        get_dimension_id(pl.dimension),
                        float(pl.x),
                        float(pl.y),
                        float(pl.z),
                        detail="no_block",
                    )
            except Exception:
                pass
            if getattr(event, 'has_block', False) and self._try_consume_land_creation_pick(event):
                return
            # 调试模式：有方块时发送方块交互信息
            if getattr(event, 'has_block', False):
                block = getattr(event, 'block', None)
                if block is not None and hasattr(block, 'location') and block.location is not None:
                    bl = block.location
                    target_desc = getattr(block, 'identifier', getattr(block, 'type', 'block'))
                    if hasattr(bl, 'dimension') and bl.dimension:
                        dim_name = get_dimension_id(bl.dimension)
                    else:
                        dim_name = get_dimension_id(getattr(event.player.location, 'dimension', None))
                    self._send_op_debug_message(event.player, 'BlockInteract', str(target_desc), dim_name, bl.x, bl.y, bl.z)
            if getattr(event.player, 'is_op', False):
                return

            # 只检查有方块的交互事件
            if not getattr(event, 'has_block', False):
                return

            block = getattr(event, 'block', None)
            if block is None or not hasattr(block, 'location') or block.location is None:
                return

            block_location = block.location

            # DTWT 设施判定（若可用）
            try:
                if (
                    self.dtwt_plugin is not None and
                    getattr(block, 'dimension', None) is not None and
                    self.dtwt_plugin.api_judge_if_start_block(block_location.x, block_location.y, block_location.z, get_dimension_id(block.dimension))
                ):
                    return
            except Exception:
                # 外部插件异常不影响主流程
                pass

            # 维度与坐标
            if getattr(block, 'dimension', None) is not None:
                dimension = get_dimension_id(block.dimension)
            else:
                # 回退到玩家维度
                dimension = get_dimension_id(event.player.location.dimension) if hasattr(event.player, 'location') and event.player.location else ''

            pos = (block_location.x, block_location.y, block_location.z)

            # 检查是否在领地内且不是领地主人
            block_target_desc = str(
                getattr(block, 'identifier', getattr(block, 'type', 'block'))
            )
            if self._is_disabled_block(block_target_desc):
                event.is_cancelled = True
                event.player.send_message(self.language_manager.GetText("DISABLED_BLOCK_DENIED_HINT"))
                return
            if not self.land_interact_check(event.player, dimension, pos):
                event.is_cancelled = True
            elif self._is_frame_block(block):
                if not self.check_land_permission(event.player, dimension, pos, "frame"):
                    event.is_cancelled = True
        except Exception as e:
            pass
            # self.logger.error(f"[ARC Core] on_player_interact error: {str(e)}")

    def _suppress_explosion_block_break(self, event: ActorExplodeEvent, reason: str) -> None:
        """禁止爆炸破坏方块，尽量保留实体伤害；无法清空 block_list 时回退为取消整次爆炸。"""
        try:
            if not hasattr(event, "block_list"):
                event.is_cancelled = True
                return
            try:
                event.block_list = []
            except Exception as assign_err:
                self.logger.warning(
                    f"[ARC Core] clear explosion block_list failed ({reason}): {assign_err}; cancel event"
                )
                event.is_cancelled = True
                return
            # 写回后抽查：若仍非空则视为未生效，回退取消
            try:
                remaining = event.block_list
                if remaining is not None and len(remaining) > 0:
                    self.logger.warning(
                        f"[ARC Core] explosion block_list still non-empty after clear ({reason}); cancel event"
                    )
                    event.is_cancelled = True
            except Exception:
                # 无法验证时保持已清空状态，不二次 cancel
                pass
        except Exception as e:
            self.logger.warning(
                f"[ARC Core] suppress explosion blocks failed ({reason}): {e}; cancel event"
            )
            try:
                event.is_cancelled = True
            except Exception:
                pass

    @event_handler
    def on_actor_explode(self, event: ActorExplodeEvent):
        """处理爆炸事件：全局/领地保护优先清空破坏列表（保留伤害），失败则取消整次爆炸。"""
        try:
            # 安全检查：确保 event.location 存在
            if not hasattr(event, 'location') or event.location is None:
                self.logger.warning("[ARC Core] on_actor_explode: event.location is None, skipping")
                return

            explosion_location = event.location
            dimension = get_dimension_id(explosion_location.dimension)

            # 全局拦截方块破坏（默认开启）：清空 block_list，保留实体伤害
            if self._sky_eye_setting_bool("BLOCK_ALL_EXPLOSIONS", True):
                self._suppress_explosion_block_break(event, "BLOCK_ALL_EXPLOSIONS")
                return

            # 爆炸中心落在禁止爆炸的领地：整片破坏列表清空（与旧 cancel 同级保护，但保留伤害）
            ex = math.floor(explosion_location.x)
            ey = math.floor(explosion_location.y)
            ez = math.floor(explosion_location.z)
            if not self.is_land_explosion_allowed_at(dimension, ex, ez, ey):
                self._suppress_explosion_block_break(event, f"land@{ex},{ey},{ez}")
                return
            
            # 安全检查：确保 block_list 存在且可迭代
            if not hasattr(event, 'block_list') or event.block_list is None:
                # block_list 为 None 或不存在时，直接返回（让爆炸正常处理）
                return

            # 安全检查：block_list 必须是可迭代的对象
            block_list = event.block_list
            if not isinstance(block_list, (list, tuple, set)):
                self.logger.warning("[ARC Core] on_actor_explode: block_list is not iterable, skipping")
                return

            keep_coords = []  # 需保留（允许被炸）的方块坐标 (x, y, z)
            for block in block_list:
                try:
                    # Block.x/y/z 是 int，直接读取坐标（避免构造 Location）
                    bx, by, bz = block.x, block.y, block.z
                except (AttributeError, TypeError, ValueError):
                    # 无法读取坐标的方块按保护处理（不保留 → 不被炸）
                    continue
                try:
                    if not self.is_land_explosion_allowed_at(dimension, bx, bz, by):
                        continue
                    keep_coords.append((bx, by, bz))
                except (AttributeError, TypeError, ValueError):
                    continue

            # 仅在确实移除了方块时才写回（无变化时跳过 setter）
            if len(keep_coords) < len(block_list):
                try:
                    dim_obj = explosion_location.dimension
                    fresh_blocks = [dim_obj.get_block_at(x, y, z) for (x, y, z) in keep_coords]
                    event.block_list = fresh_blocks
                except Exception as e:
                    # 写回失败则回退为整体取消，确保领地不被破坏
                    self.logger.warning(f"[ARC Core] Failed to rebuild block_list: {str(e)}")
                    event.is_cancelled = True
            
        except Exception as e:
            self.logger.error(f"Handle actor explode event error: {str(e)}")
            # 外层异常时宁可取消爆炸，避免保护失效导致破坏方块
            try:
                event.is_cancelled = True
            except Exception:
                pass

    @event_handler
    def on_player_interact_actor(self, event: PlayerInteractActorEvent):
        """处理玩家与生物交互事件，保护领地内生物免受非法交互"""
        actor_location = event.actor.location
        target_desc = getattr(event.actor, 'identifier', getattr(event.actor, 'type', 'actor'))
        self._sky_eye_append(
            "ActorInteract",
            event.player,
            get_dimension_id(actor_location.dimension),
            float(actor_location.x),
            float(actor_location.y),
            float(actor_location.z),
            detail=f"actor={target_desc}",
        )
        self._send_op_debug_message(
            event.player, 'ActorInteract', str(target_desc),
            get_dimension_id(actor_location.dimension), actor_location.x, actor_location.y, actor_location.z
        )
        # OP玩家跳过检查
        if event.player.is_op:
            return

        # 获取生物位置
        actor_location = event.actor.location
        dimension = get_dimension_id(actor_location.dimension)
        ax = math.floor(actor_location.x)
        ay = math.floor(actor_location.y)
        az = math.floor(actor_location.z)
        if not self.check_land_permission(
            event.player, dimension, (ax, ay, az), "actor_interact"
        ):
            event.is_cancelled = True

    @event_handler
    def on_actor_damage(self, event: ActorDamageEvent):
        """处理生物受伤事件，保护领地内生物免受攻击"""
        # 检查攻击者是否为玩家
        attacker = event.damage_source.actor
        if attacker is None or attacker.type != "minecraft:player":
            return

        if self._sky_eye_setting_bool("SKY_EYE_LOG_ACTOR_DAMAGE", True):
            actor_location = event.actor.location
            target_name, target_xuid, target_type = self._sky_eye_identity(event.actor)
            target_desc = target_type if target_type not in ("", "player") else (
                getattr(event.actor, "identifier", getattr(event.actor, "type", "actor"))
            )
            if target_name:
                target_desc = target_name if target_type == "player" else f"{target_name}"
            detail_parts = [f"target={target_desc}"]
            try:
                damage = float(getattr(event, "damage", 0) or 0)
                detail_parts.append(f"damage={damage:.2f}".rstrip("0").rstrip("."))
            except Exception:
                damage = None
            hp_before, max_hp = self._sky_eye_read_health(event.actor)
            if hp_before:
                detail_parts.append(f"hp_before={hp_before}")
                if damage is not None:
                    try:
                        hp_after_val = max(0.0, float(hp_before) - float(damage))
                        hp_after = f"{hp_after_val:.1f}".rstrip("0").rstrip(".")
                        detail_parts.append(f"hp_after={hp_after}")
                    except Exception:
                        pass
            if max_hp:
                detail_parts.append(f"max_hp={max_hp}")
            cause = self._sky_eye_damage_cause(getattr(event, "damage_source", None))
            if cause:
                detail_parts.append(f"cause={cause}")
            # PvP：附带被打方当时身上装备，便于核对「全套保护」是否属实
            if target_type == "player":
                try:
                    victim_armor = self._sky_eye_format_equipment(event.actor)
                    if victim_armor and victim_armor != "-":
                        detail_parts.append(f"victim_armor={victim_armor}")
                except Exception:
                    pass
            self._sky_eye_append(
                "ActorDamage",
                attacker,
                get_dimension_id(actor_location.dimension),
                float(actor_location.x),
                float(actor_location.y),
                float(actor_location.z),
                detail=";".join(detail_parts),
                target_name=target_name,
                target_xuid=target_xuid,
                target_type=target_type if target_type else str(target_desc),
            )
            self._send_op_debug_message(
                attacker, 'ActorDamage', str(target_desc),
                get_dimension_id(actor_location.dimension), actor_location.x, actor_location.y, actor_location.z
            )
        # 如果玩家是op则不判断
        if attacker.is_op:
            return

        # 获取被攻击生物位置
        actor_location = event.actor.location
        dimension = get_dimension_id(actor_location.dimension)
        ax = math.floor(actor_location.x)
        ay = math.floor(actor_location.y)
        az = math.floor(actor_location.z)
        if not self.check_land_permission(
            attacker, dimension, (ax, ay, az), "actor_damage", damaged_actor=event.actor
        ):
            event.is_cancelled = True

    @event_handler
    def on_actor_spawn(self, event: ActorSpawnEvent):
        """领地内按全局黑/白名单拦截生物生成（默认仅公共领地且看开关；scope=all 时任意领地）。"""
        try:
            actor = event.actor
            if actor is None or isinstance(actor, Player):
                return
            if not self._is_land_system_enabled():
                return
            loc = actor.location
            if loc is None:
                return
            dimension = get_dimension_id(loc.dimension)
            ax = math.floor(loc.x)
            ay = math.floor(loc.y)
            az = math.floor(loc.z)
            land_id = self.get_land_at_pos(dimension, ax, az, ay)
            if land_id is None:
                return
            land_info = self.get_land_info(land_id)
            if not land_info:
                return
            scope_all = self.land_system.is_block_actor_spawn_all_lands()
            if not scope_all and not self.is_public_land(land_id):
                return
            # public：尊重各公共领地 block_actor_spawn；all：任意领地直接按名单拦截
            land_enabled = True if scope_all else bool(land_info.get("block_actor_spawn", False))
            actor_type = getattr(actor, "type", None) or getattr(actor, "identifier", None) or ""
            if self.land_system.should_block_public_land_actor_spawn(
                land_enabled,
                actor_type,
            ):
                event.is_cancelled = True
        except Exception as e:
            self.logger.error(f"Handle actor spawn event error: {str(e)}")

    @event_handler
    def on_actor_death(self, event: ActorDeathEvent):
        """统计玩家击杀生物：击杀总数 + 按生物类型击杀数；并发放击杀赏金。"""
        try:
            damage_source = getattr(event, "damage_source", None)
            killer = getattr(damage_source, "actor", None) if damage_source is not None else None
            if killer is None:
                return
            if getattr(killer, "type", None) != "minecraft:player":
                return

            dead_actor = getattr(event, "actor", None)
            if dead_actor is None:
                return
            if getattr(dead_actor, "type", None) == "minecraft:player":
                return

            dead_type = getattr(dead_actor, "type", None) or getattr(dead_actor, "identifier", None) or ""
            if not dead_type:
                return

            killer_xuid = str(getattr(killer, "xuid", "") or "").strip()
            killer_name = str(getattr(killer, "name", "") or "").strip()
            dead_type_key = normalize_entity_type_id(str(dead_type))
            if not killer_xuid:
                return
            self._schedule_actor_kill_processing(killer_xuid, killer_name, dead_type_key)
        except Exception:
            return

    def _schedule_actor_kill_processing(
        self, killer_xuid: str, killer_name: str, dead_type_key: str
    ) -> None:
        """延迟 1 tick 处理击杀统计与赏金，避免 Death 事件同 tick 阻塞主线程。"""
        def _process():
            self._process_actor_kill_reward(killer_xuid, killer_name, dead_type_key)

        try:
            self.server.scheduler.run_task(self, _process, delay=1)
        except Exception:
            try:
                _process()
            except Exception:
                pass

    def _process_actor_kill_reward(
        self, killer_xuid: str, killer_name: str, dead_type_key: str
    ) -> None:
        try:
            if killer_xuid:
                self.activity_stats.record_kill(killer_xuid, dead_type_key)
                self._notify_achievement_activity(killer_xuid, "kill", dead_type_key)
        except Exception:
            pass

        try:
            reward = self.kill_reward_config.get_reward_and_ensure_key(dead_type_key)
            if reward <= 0:
                return
            killer = self._find_online_player_by_xuid(killer_xuid)
            if killer is None and killer_name:
                killer = self.server.get_player(killer_name)
            pay_name = killer_name
            if killer is not None:
                pay_name = str(getattr(killer, "name", "") or killer_name)
            if not pay_name:
                return
            if self.increase_player_money_by_name(
                pay_name, reward, notify=False, defer_richest=True
            ):
                display_name = self.entity_display_name_manager.get_display_name_for_entity_type(
                    dead_type_key
                )
                if killer is not None:
                    killer.send_message(
                        self.language_manager.GetText("KILL_REWARD_MESSAGE").format(
                            display_name,
                            self._format_money_display(reward),
                        )
                    )
                    self._grant_kill_guild_contribution(killer, reward)
        except Exception:
            pass

    def _activity_record_block_break(self, player: Player, block_desc) -> None:
        try:
            xuid = str(getattr(player, "xuid", "") or "").strip()
            if not xuid:
                return
            block_id = self.activity_stats.normalize_block_id(str(block_desc or ""))
            if not block_id:
                return
            self.activity_stats.record_block_break(xuid, block_id)
            self._notify_achievement_activity(xuid, "break", block_id)
        except Exception:
            pass

    def _activity_record_block_place(self, player: Player, block_desc) -> None:
        try:
            xuid = str(getattr(player, "xuid", "") or "").strip()
            if not xuid:
                return
            block_id = self.activity_stats.normalize_block_id(str(block_desc or ""))
            if not block_id:
                return
            self.activity_stats.record_block_place(xuid, block_id)
            self._notify_achievement_activity(xuid, "place", block_id)
        except Exception:
            pass

    def _notify_achievement_activity(
        self, xuid: str, stat_kind: str, target_id: str
    ) -> None:
        """活动统计写入后通知 arc_achievement 检查相关成就（delay=0，不阻塞当前事件）。"""
        xs = str(xuid or "").strip()
        kind = str(stat_kind or "").strip().lower()
        tid = str(target_id or "").strip()
        if not xs or not kind or not tid:
            return

        def _run():
            try:
                plug = self.server.plugin_manager.get_plugin("arc_achievement")
                if plug is None:
                    return
                fn = getattr(plug, "api_notify_activity_stat", None)
                if callable(fn):
                    fn(xs, kind, tid)
            except Exception:
                pass

        try:
            self.server.scheduler.run_task(self, _run, delay=0)
        except Exception:
            try:
                _run()
            except Exception:
                pass

    @event_handler
    def on_player_drop_item(self, event: PlayerDropItemEvent):
        try:
            player = event.player
            if player is None:
                return
            item_text = self._sky_eye_format_item_stack(getattr(event, "item", None))
            dim, x, y, z = self._sky_eye_player_location(player)
            self._sky_eye_append("ItemDrop", player, dim, x, y, z, detail=f"item={item_text}")
        except Exception:
            pass

    @event_handler
    def on_player_pickup_item(self, event: PlayerPickupItemEvent):
        try:
            player = event.player
            if player is None:
                return
            item_obj = getattr(event, "item", None)
            stack = getattr(item_obj, "item_stack", None) if item_obj is not None else None
            item_text = self._sky_eye_format_item_stack(stack)
            if item_text in ("empty", "unknown") and item_obj is not None:
                item_text = str(getattr(item_obj, "type", "") or item_obj)
            dim, x, y, z = self._sky_eye_player_location(player)
            self._sky_eye_append("ItemPickup", player, dim, x, y, z, detail=f"item={item_text}")
        except Exception:
            pass

    @event_handler
    def on_player_item_held(self, event: PlayerItemHeldEvent):
        try:
            player = event.player
            if player is None:
                return
            new_slot = int(getattr(event, "new_slot", -1))
            prev_slot = int(getattr(event, "previous_slot", -1))
            inv = getattr(player, "inventory", None)
            stack = None
            if inv is not None and new_slot >= 0:
                getter = getattr(inv, "get_item", None)
                if callable(getter):
                    stack = getter(new_slot)
            item_text = self._sky_eye_format_item_stack(stack)
            dim, x, y, z = self._sky_eye_player_location(player)
            self._sky_eye_append(
                "ItemHeldChange",
                player,
                dim,
                x,
                y,
                z,
                detail=f"slot {prev_slot}->{new_slot};item={item_text}",
                hand=item_text,
            )
        except Exception:
            pass

    @event_handler
    def on_player_item_consume(self, event: PlayerItemConsumeEvent):
        try:
            player = event.player
            if player is None:
                return
            item_text = self._sky_eye_format_item_stack(getattr(event, "item", None))
            dim, x, y, z = self._sky_eye_player_location(player)
            self._sky_eye_append("ItemConsume", player, dim, x, y, z, detail=f"item={item_text}")
        except Exception:
            pass

    @event_handler
    def on_player_teleport(self, event: PlayerTeleportEvent):
        try:
            player = event.player
            if player is None:
                return
            from_loc = getattr(event, "from_location", None) or getattr(event, "from", None)
            to_loc = getattr(event, "to_location", None) or getattr(event, "to", None)
            if from_loc is None or to_loc is None:
                return
            from_dim = get_dimension_id(getattr(from_loc, "dimension", None)) if getattr(from_loc, "dimension", None) is not None else "-"
            to_dim = get_dimension_id(getattr(to_loc, "dimension", None)) if getattr(to_loc, "dimension", None) is not None else "-"
            detail = (
                f"from={from_dim}({float(from_loc.x):.1f},{float(from_loc.y):.1f},{float(from_loc.z):.1f})"
                f";to={to_dim}({float(to_loc.x):.1f},{float(to_loc.y):.1f},{float(to_loc.z):.1f})"
            )
            self._sky_eye_append(
                "PlayerTeleport",
                player,
                to_dim,
                float(to_loc.x),
                float(to_loc.y),
                float(to_loc.z),
                detail=detail,
            )
        except Exception:
            pass

    def _load_kill_reward_guild_contrib_ratio(self) -> float:
        """读取 KILL_REWARD_GUILD_CONTRIB_RATIO；非法/缺省按 0 处理。"""
        raw = self.setting_manager.GetSetting("KILL_REWARD_GUILD_CONTRIB_RATIO")
        try:
            ratio = float(str(raw).strip())
        except (ValueError, TypeError, AttributeError):
            ratio = 0.0
        if ratio < 0:
            ratio = 0.0
        return ratio

    def _grant_kill_guild_contribution(self, killer, reward: float) -> None:
        """按 KILL_REWARD_GUILD_CONTRIB_RATIO 把击杀金钱奖励折算为公会贡献点；未加入公会则跳过。"""
        try:
            ratio = float(getattr(self, "kill_reward_guild_contrib_ratio", 0.0) or 0.0)
            if ratio <= 0 or reward <= 0:
                return
            points = int(float(reward) * ratio)
            if points <= 0:
                return
            xuid = str(getattr(killer, "xuid", "") or "")
            if not xuid:
                return
            ok_gc, err_gc, info_gc = self.guild_system.add_contribution_by_xuid(xuid, points)
            if ok_gc:
                tmpl = self.language_manager.GetText("KILL_REWARD_GUILD_CONTRIB_HINT")
                if not (tmpl and str(tmpl).strip()):
                    tmpl = "[弧光核心]获得公会贡献点 +{0}（我的：{1}，公会：{2}）。"
                try:
                    killer.send_message(
                        tmpl.format(
                            int(points),
                            int(info_gc.get("personal", 0)),
                            int(info_gc.get("guild_total", 0)),
                        )
                    )
                except Exception:
                    pass
            elif err_gc and err_gc != "GUILD_NOT_IN_GUILD" and self.logger:
                self.logger.warning(
                    f"[ARC Core]kill guild contribution failed xuid={xuid!r} err={err_gc!r}"
                )
        except Exception as e:
            try:
                if self.logger:
                    self.logger.error(f"[ARC Core]_grant_kill_guild_contribution error: {e}")
            except Exception:
                pass

    def _player_matches_land_owner_key(self, player: Player, owner_key: str) -> bool:
        """领地 / 子领地主人键（Player_/GUILD_/PUBLIC）是否与玩家匹配（公会领地：同公会成员视为有主权限）。"""
        xu = str(player.xuid)
        ok = str(owner_key or "").strip()
        px = LandSystem.parse_land_owner_player_xuid(ok)
        if px is not None and px == xu:
            return True
        if ok == xu:
            return True
        gid = LandSystem.parse_land_owner_guild_id(ok)
        if gid is not None and getattr(self, "guild_system", None):
            mem = self.guild_system.get_membership(xu)
            if mem and int(mem.get("guild_id") or 0) == gid:
                return True
        return False

    def _land_shared_user_grants_access(
        self, player: Player, owner_key: str, shared_users: Any
    ) -> bool:
        """共享名单：非公会的玩家领地照旧；公会领地（GUILD_）仅同公会成员可被名单放行。"""
        xu = str(player.xuid)
        seq = shared_users or []
        if not any(str(u) == xu for u in seq):
            return False
        guild_id = LandSystem.parse_land_owner_guild_id(str(owner_key or ""))
        if guild_id is None:
            return True
        if not getattr(self, "guild_system", None):
            return False
        mem = self.guild_system.get_membership(xu)
        return bool(mem and int(mem.get("guild_id") or 0) == int(guild_id))

    def _land_interact_allowed_for_guild_peer(
        self, player: Player, land_info: dict
    ) -> bool:
        """
        领地开启 allow_guild_member_interact 且当前玩家与领地主人（Player_ 键）在同一公会时，
        允许方块交互（仅用于 land_interact_check，不含建造/破坏）。
        """
        if not land_info.get("allow_guild_member_interact"):
            return False
        owner_key = str(land_info.get("owner_xuid") or "")
        owner_px = LandSystem.parse_land_owner_player_xuid(owner_key)
        if not owner_px:
            return False
        if not getattr(self, "guild_system", None):
            return False
        pmem = self.guild_system.get_membership(str(player.xuid))
        omem = self.guild_system.get_membership(owner_px)
        if not pmem or not omem:
            return False
        g1 = int(pmem.get("guild_id") or 0)
        g2 = int(omem.get("guild_id") or 0)
        return g1 > 0 and g1 == g2

    def _check_sub_land_permission(self, player: Player, sub_land_info: dict) -> bool:
        """检查玩家是否拥有子领地权限（主人或授权用户）"""
        try:
            owner_key = sub_land_info.get('owner_xuid', '')
            shared_users = sub_land_info.get('shared_users', [])
            if self._land_shared_user_grants_access(player, owner_key, shared_users):
                return True
            return self._player_matches_land_owner_key(player, owner_key)
        except Exception as e:
            self.logger.error(f"Check sub land permission error: {str(e)}")
            return False

    def _check_land_permission(self, player: Player, land_info: dict) -> bool:
        """
        检查玩家是否有领地权限（领地主人或授权用户）；公共领地仅 OP 有权限
        :param player: 玩家对象
        :param land_info: 领地信息字典
        :return: 是否有权限
        """
        try:
            owner_key = land_info['owner_xuid']
            if self.land_system.is_public_land_owner(owner_key):
                return player.is_op
            shared_users = land_info.get('shared_users', [])
            if self._land_shared_user_grants_access(player, owner_key, shared_users):
                return True
            return self._player_matches_land_owner_key(player, owner_key)
        except Exception as e:
            self.logger.error(f"Check land permission error: {str(e)}")
            return False

    def _land_player_exempt_from_frame_protect(
        self, player: Player, pos: tuple, land_id: int, land_info: dict
    ) -> bool:
        """
        allow_frame 为 False 时仍允许操作展示框/展示架的玩家：
        与领地核心权限一致（主人/授权/子领地、公共地 OP、开启公会成员交互时的同公会成员）。
        """
        x, y, z = pos[0], (pos[1] if len(pos) > 1 else None), pos[2]
        if y is not None:
            sub_land_id = self.get_sub_land_at_pos(land_id, int(x), int(y), int(z))
            if sub_land_id is not None:
                sub_info = self.get_sub_land_info(sub_land_id)
                if sub_info and self._check_sub_land_permission(player, sub_info):
                    return True
        if self._check_land_permission(player, land_info):
            return True
        if self._land_interact_allowed_for_guild_peer(player, land_info):
            return True
        return False

    def _sky_eye_setting_bool(self, key: str, default: bool = False) -> bool:
        raw = self.setting_manager.GetSetting(key)
        if raw is None:
            return default
        try:
            return str(raw).strip().lower() in ("true", "1", "yes")
        except (ValueError, AttributeError):
            return default

    def _is_land_claim_allowed(self) -> bool:
        """本服是否允许圈地（新建领地）；配置 ALLOW_LAND_CLAIM，默认 True，不跨服同步。"""
        return self._sky_eye_setting_bool("ALLOW_LAND_CLAIM", True)

    def _is_land_system_enabled(self) -> bool:
        """领地系统总开关；关闭时不启位置追踪线程（进出领地提示）。"""
        return self._sky_eye_setting_bool("ENABLE_LAND_SYSTEM", True)

    def _sync_land_position_thread(self) -> None:
        if self._is_land_system_enabled():
            self.start_position_thread()
        else:
            self.stop_position_thread()
            if self.logger:
                self.logger.info("[ARC Core]Land system disabled, position thread not started")

    def _notify_land_claim_disabled(self, player: Player) -> None:
        msg = self.language_manager.GetText("LAND_CLAIM_DISABLED")
        if not msg or not str(msg).strip():
            msg = "[弧光核心]本服已关闭圈地功能，无法创建新领地。"
        player.send_message(msg)

    def _ensure_land_claim_allowed(self, player: Player) -> bool:
        if self._is_land_claim_allowed():
            return True
        self._notify_land_claim_disabled(player)
        return False

    def _sky_eye_retention_days(self) -> int:
        raw = self.setting_manager.GetSetting("SKY_EYE_MAX_RETENTION_DAYS")
        if raw is None or not str(raw).strip():
            return 7
        try:
            return max(0, int(raw))
        except (ValueError, TypeError):
            return 7

    _SKY_EYE_ENCHANT_IDS: list[str] | None = None
    _SKY_EYE_ARMOR_ATTRS = (
        ("helmet", "helm"),
        ("chestplate", "chest"),
        ("leggings", "legs"),
        ("boots", "boots"),
        ("item_in_off_hand", "off"),
    )

    def _sky_eye_enchant_ids(self) -> list[str]:
        cached = ARCCorePlugin._SKY_EYE_ENCHANT_IDS
        if cached is not None:
            return cached
        ids: list[str] = []
        try:
            from endstone.enchantments import Enchantment

            for name in dir(Enchantment):
                if not name.isupper():
                    continue
                val = getattr(Enchantment, name, None)
                if isinstance(val, str) and val.startswith("minecraft:"):
                    ids.append(val)
        except Exception:
            pass
        if not ids:
            ids = [
                "minecraft:aqua_affinity",
                "minecraft:bane_of_arthropods",
                "minecraft:blast_protection",
                "minecraft:breach",
                "minecraft:channeling",
                "minecraft:binding",
                "minecraft:vanishing",
                "minecraft:density",
                "minecraft:depth_strider",
                "minecraft:efficiency",
                "minecraft:feather_falling",
                "minecraft:fire_aspect",
                "minecraft:fire_protection",
                "minecraft:flame",
                "minecraft:frost_walker",
                "minecraft:impaling",
                "minecraft:infinity",
                "minecraft:knockback",
                "minecraft:looting",
                "minecraft:loyalty",
                "minecraft:luck_of_the_sea",
                "minecraft:lure",
                "minecraft:mending",
                "minecraft:multishot",
                "minecraft:piercing",
                "minecraft:power",
                "minecraft:projectile_protection",
                "minecraft:protection",
                "minecraft:punch",
                "minecraft:quick_charge",
                "minecraft:respiration",
                "minecraft:riptide",
                "minecraft:sharpness",
                "minecraft:silk_touch",
                "minecraft:smite",
                "minecraft:soul_speed",
                "minecraft:swift_sneak",
                "minecraft:thorns",
                "minecraft:unbreaking",
                "minecraft:wind_burst",
            ]
        ARCCorePlugin._SKY_EYE_ENCHANT_IDS = ids
        return ids

    def _sky_eye_item_enchants(self, stack) -> dict[str, int]:
        """读取物品附魔（不访问 ItemMeta.enchants，避免 unhashable）。"""
        if stack is None:
            return {}
        meta = getattr(stack, "item_meta", None)
        get_level = getattr(meta, "get_enchant_level", None) if meta is not None else None
        if not callable(get_level):
            return {}
        out: dict[str, int] = {}
        try:
            for eid in self._sky_eye_enchant_ids():
                try:
                    level = int(get_level(eid) or 0)
                except Exception:
                    continue
                if level > 0:
                    short = eid.split(":", 1)[-1] if ":" in eid else eid
                    out[short] = level
        except Exception:
            return {}
        return out

    def _sky_eye_format_item_stack(self, stack, *, with_enchants: bool = True) -> str:
        if stack is None:
            return "empty"
        item_type = getattr(stack, "type", None)
        if item_type is None:
            return "unknown"
        item_id = getattr(item_type, "id", None) or getattr(item_type, "name", None)
        if not item_id:
            text = str(item_type).strip()
            return text if text else "unknown"
        amount = getattr(stack, "amount", 1)
        try:
            amount = int(amount)
        except (ValueError, TypeError):
            amount = 1
        if amount <= 0 or str(item_id) in ("minecraft:air", "air"):
            return "empty"
        enc_text = ""
        if with_enchants:
            enchants = self._sky_eye_item_enchants(stack)
            if enchants:
                enc_text = "{" + ",".join(f"{k}:{v}" for k, v in sorted(enchants.items())) + "}"
        return f"{item_id}{enc_text}x{amount}"

    def _sky_eye_format_main_hand(self, player: Player) -> str:
        inv = getattr(player, "inventory", None)
        if inv is None:
            return "-"
        stack = getattr(inv, "item_in_main_hand", None)
        return self._sky_eye_format_item_stack(stack)

    def _sky_eye_format_equipment(self, actor) -> str:
        """头盔/胸甲/护腿/靴子/副手摘要，空槽省略。"""
        inv = getattr(actor, "inventory", None)
        if inv is None:
            return "-"
        parts: list[str] = []
        for attr, label in self._SKY_EYE_ARMOR_ATTRS:
            try:
                stack = getattr(inv, attr, None)
            except Exception:
                stack = None
            text = self._sky_eye_format_item_stack(stack)
            if text in ("empty", "unknown", "-"):
                continue
            parts.append(f"{label}:{text}")
        return "|".join(parts) if parts else "-"

    def _sky_eye_format_inventory(self, actor, *, max_slots: int = 54, max_chars: int = 3500) -> str:
        """背包格子摘要（含热键栏），过长则截断。"""
        inv = getattr(actor, "inventory", None)
        if inv is None:
            return "-"
        try:
            size = int(getattr(inv, "size", 0) or 0)
        except (TypeError, ValueError):
            size = 0
        if size <= 0:
            return "-"
        parts: list[str] = []
        limit = min(size, max(1, int(max_slots)))
        omitted = 0
        for idx in range(limit):
            try:
                stack = inv.get_item(idx)
            except Exception:
                continue
            text = self._sky_eye_format_item_stack(stack)
            if text in ("empty", "unknown", "-"):
                continue
            parts.append(f"[{idx}]{text}")
        if size > limit:
            # 未遍历的格子粗略计为省略
            for idx in range(limit, size):
                try:
                    stack = inv.get_item(idx)
                except Exception:
                    continue
                text = self._sky_eye_format_item_stack(stack, with_enchants=False)
                if text not in ("empty", "unknown", "-"):
                    omitted += 1
        joined = "|".join(parts) if parts else "-"
        if len(joined) > max_chars:
            joined = joined[: max(0, max_chars - 20)].rstrip("|") + "|...(truncated)"
            omitted = max(omitted, 1)
        if omitted > 0 and joined != "-":
            joined = f"{joined}|(+{omitted}more)"
        return joined

    def _sky_eye_read_health(self, actor) -> tuple[str, str]:
        """返回 (当前血量, 最大血量) 字符串；读不到则为空串。"""
        hp = ""
        max_hp = ""
        if actor is None:
            return hp, max_hp
        try:
            raw = getattr(actor, "health", None)
            if raw is not None:
                hp = f"{float(raw):.1f}".rstrip("0").rstrip(".")
        except Exception:
            pass
        try:
            raw_max = getattr(actor, "max_health", None)
            if raw_max is not None:
                max_hp = f"{float(raw_max):.1f}".rstrip("0").rstrip(".")
        except Exception:
            pass
        return hp, max_hp

    def _sky_eye_damage_cause(self, damage_source) -> str:
        if damage_source is None:
            return ""
        for attr in ("damage_type", "type", "cause"):
            try:
                val = getattr(damage_source, attr, None)
            except Exception:
                val = None
            if val is not None and str(val).strip():
                return str(val).strip()
        return ""

    def _sky_eye_resolve_killer_actor(self, event):
        """尽量从死亡/伤害事件解析击杀者实体。"""
        if event is None:
            return None
        for attr in ("killer", "damager"):
            try:
                actor = getattr(event, attr, None)
            except Exception:
                actor = None
            if actor is not None:
                return actor
        try:
            damage_source = getattr(event, "damage_source", None)
        except Exception:
            damage_source = None
        if damage_source is None:
            return None
        for attr in ("actor", "damaging_actor", "damaging_entity", "entity", "attacker"):
            try:
                actor = getattr(damage_source, attr, None)
            except Exception:
                actor = None
            if actor is not None:
                return actor
        return None

    def _sky_eye_player_location(self, player: Player) -> tuple[str, float, float, float]:
        loc = getattr(player, "location", None)
        if loc is None:
            return "-", 0.0, 0.0, 0.0
        dimension = (
            get_dimension_id(getattr(loc, "dimension", None))
            if getattr(loc, "dimension", None) is not None
            else "-"
        )
        return dimension, float(loc.x), float(loc.y), float(loc.z)

    def _sky_eye_log_economy(
        self,
        *,
        player: Optional[Player] = None,
        player_name: str = "",
        player_xuid: str = "",
        delta: float = 0.0,
        balance: float = 0.0,
        reason: str = "",
    ) -> None:
        detail = f"delta={delta:+.2f};balance={balance:.2f}"
        if reason:
            detail += f";reason={reason}"
        if player is not None:
            dim, x, y, z = self._sky_eye_player_location(player)
            self._sky_eye_append("EconomyChange", player, dim, x, y, z, detail=detail)
            return
        self.api_sky_eye_log(
            "EconomyChange",
            player_name=player_name,
            player_xuid=player_xuid,
            detail=detail,
        )

    def _sky_eye_log_land(
        self,
        action: str,
        actor: Optional[Player],
        land_id: int,
        land_name: str = "",
        detail: str = "",
    ) -> None:
        extra = f"land_id={land_id}"
        if land_name:
            extra += f";name={land_name}"
        if detail:
            extra += f";{detail}"
        if actor is not None:
            dim, x, y, z = self._sky_eye_player_location(actor)
            self._sky_eye_append(action, actor, dim, x, y, z, detail=extra)
        else:
            self.api_sky_eye_log(action, detail=extra)

    def _sky_eye_log_teleport(
        self,
        player: Player,
        destination: str,
        teleport_type: str = "",
        dimension: str = "",
        position: tuple | None = None,
    ) -> None:
        detail = f"dest={destination}"
        if teleport_type:
            detail += f";type={teleport_type}"
        if dimension:
            detail += f";dimension={dimension}"
        if position is not None:
            detail += f";pos={position[0]:.1f},{position[1]:.1f},{position[2]:.1f}"
        dim, x, y, z = self._sky_eye_player_location(player)
        self._sky_eye_append("TeleportUse", player, dim, x, y, z, detail=detail)

    def api_sky_eye_log(
        self,
        action: str,
        *,
        player: Optional[Player] = None,
        player_name: str = "",
        player_xuid: str = "",
        dimension: str = "",
        x: float = 0.0,
        y: float = 0.0,
        z: float = 0.0,
        detail: str = "",
        target_name: str = "",
        target_xuid: str = "",
        target_type: str = "",
        resolve_online: bool = True,
    ) -> bool:
        """供其他插件或离线场景写入天眼记录。

        ``resolve_online=False``：不查找在线玩家、不读 location（进服早期事件用）。
        """
        if not self._sky_eye_setting_bool("ENABLE_SKY_EYE", True):
            return False
        try:
            subject = player if resolve_online else None
            if subject is None and resolve_online and (player_name or player_xuid):
                if player_name:
                    subject = self.server.get_player(player_name)
                if subject is None and player_xuid:
                    subject = self._find_online_player_by_xuid(str(player_xuid))
            if subject is not None:
                dim, px, py, pz = self._sky_eye_player_location(subject)
                if not dimension:
                    dimension = dim
                    x, y, z = px, py, pz
                try:
                    hand = self._sky_eye_format_main_hand(subject)
                except Exception:
                    hand = "-"
                land = self._sky_eye_land_snapshot(dimension or "-", x, y, z)
                pname = getattr(subject, "name", "") or player_name or "?"
                pxuid = str(getattr(subject, "xuid", "") or player_xuid or "")
            else:
                dim = dimension or "-"
                hand = "-"
                land = self._sky_eye_land_snapshot(dim, x, y, z)
                pname = str(player_name or "?")
                pxuid = str(player_xuid or "")
            store_dim = dimension or (dim if subject is not None else dim) or "-"
            self.sky_eye_store.append(
                self._sky_eye_retention_days(),
                action,
                pname,
                pxuid,
                store_dim,
                float(x),
                float(y),
                float(z),
                hand,
                detail,
                in_land=bool(land.get("in_land")),
                land_id=land.get("land_id"),
                land_name=str(land.get("land_name") or ""),
                land_owner=str(land.get("land_owner") or ""),
                target_name=target_name,
                target_xuid=target_xuid,
                target_type=target_type,
            )
            return True
        except Exception:
            return False

    def _sky_eye_identity(self, actor) -> tuple[str, str, str]:
        if actor is None:
            return "", "", ""
        actor_type = str(getattr(actor, "type", "") or getattr(actor, "identifier", "") or "")
        name = str(getattr(actor, "name", "") or "").strip()
        xuid = str(getattr(actor, "xuid", "") or "").strip()
        is_player = isinstance(actor, Player) or actor_type == "minecraft:player"
        if is_player:
            return name or "?", xuid, "player"
        return name or actor_type or "?", xuid, actor_type or "entity"

    def _sky_eye_land_snapshot(self, dimension: str, pos_x: float, pos_y: float, pos_z: float) -> dict:
        empty = {"in_land": False, "land_id": None, "land_name": "", "land_owner": ""}
        try:
            land_id = self.get_land_at_pos(
                dimension,
                int(math.floor(float(pos_x))),
                int(math.floor(float(pos_z))),
                int(math.floor(float(pos_y))),
            )
        except Exception:
            return empty
        if land_id is None:
            return empty
        meta = self._sky_eye_land_meta.get(int(land_id))
        if meta is not None:
            land_name, land_owner = meta
        else:
            try:
                info = self.get_land_info(land_id) or {}
                land_name = str(info.get("land_name") or "") or f"#{land_id}"
                land_owner = self.get_land_display_owner_name(land_id) or ""
            except Exception:
                land_name = f"#{land_id}"
                land_owner = ""
            if len(self._sky_eye_land_meta) >= 256:
                self._sky_eye_land_meta.clear()
            self._sky_eye_land_meta[int(land_id)] = (land_name, land_owner)
        return {
            "in_land": True,
            "land_id": int(land_id),
            "land_name": str(land_name),
            "land_owner": str(land_owner),
        }

    def _sky_eye_append(
        self,
        action: str,
        player: Optional[Player],
        dimension: str,
        pos_x: float,
        pos_y: float,
        pos_z: float,
        detail: str = "",
        target_name: str = "",
        target_xuid: str = "",
        target_type: str = "",
        hand: Optional[str] = None,
    ) -> None:
        if not self._sky_eye_setting_bool("ENABLE_SKY_EYE", True):
            return
        if player is None:
            return
        try:
            player_name = getattr(player, "name", "") or "?"
            player_xuid = str(getattr(player, "xuid", "") or "")
            if hand is None:
                if player_xuid and player_xuid in self._sky_eye_hand_cache:
                    hand = self._sky_eye_hand_cache[player_xuid]
                else:
                    try:
                        hand = self._sky_eye_format_main_hand(player)
                    except Exception:
                        hand = "-"
                    if player_xuid:
                        self._sky_eye_hand_cache[player_xuid] = hand
            elif player_xuid:
                self._sky_eye_hand_cache[player_xuid] = hand
            land = self._sky_eye_land_snapshot(dimension or "-", pos_x, pos_y, pos_z)
            self.sky_eye_store.append(
                self._sky_eye_retention_days(),
                action,
                player_name,
                player_xuid,
                dimension or "-",
                pos_x,
                pos_y,
                pos_z,
                hand,
                detail,
                in_land=bool(land.get("in_land")),
                land_id=land.get("land_id"),
                land_name=str(land.get("land_name") or ""),
                land_owner=str(land.get("land_owner") or ""),
                target_name=target_name,
                target_xuid=target_xuid,
                target_type=target_type,
            )
        except Exception:
            pass

    def _refresh_land_only_place_blocks(self):
        """从 LAND_ONLY_PLACE_BLOCKS 解析「仅允许在领地内放置」的方块 ID 集合（与 PUBLIC_LAND_INTERACT_BLOCK_BLACKLIST 相同的规范化规则）"""
        raw = self.setting_manager.GetSetting("LAND_ONLY_PLACE_BLOCKS")
        if raw is None or not str(raw).strip():
            self._land_only_place_block_ids = frozenset()
            return
        parsed = set()
        for part in str(raw).split(","):
            part = part.strip()
            if not part:
                continue
            normalized = part.lower()
            if ":" not in normalized:
                normalized = "minecraft:" + normalized
            parsed.add(normalized)
        self._land_only_place_block_ids = frozenset(parsed)

    @staticmethod
    def _normalize_block_id(block_identifier: str) -> str:
        normalized = str(block_identifier or "").lower().strip()
        if not normalized:
            return ""
        if ":" not in normalized:
            normalized = "minecraft:" + normalized
        return normalized

    def _refresh_disabled_blocks(self):
        """从 DISABLED_BLOCKS 解析「全局禁用方块」ID 集合（禁放置与禁交互）。"""
        raw = self.setting_manager.GetSetting("DISABLED_BLOCKS")
        if raw is None or not str(raw).strip():
            self._disabled_block_ids = frozenset()
            return
        parsed = set()
        for part in str(raw).split(","):
            normalized = self._normalize_block_id(part)
            if normalized:
                parsed.add(normalized)
        self._disabled_block_ids = frozenset(parsed)

    def _is_disabled_block(self, block_identifier: str) -> bool:
        return self._normalize_block_id(block_identifier) in self._disabled_block_ids

    def _parse_land_pos(self, pos: tuple):
        x = pos[0]
        y = pos[1] if len(pos) > 1 else None
        z = pos[2] if len(pos) > 2 else pos[1]
        return x, y, z

    def _build_land_check_context(self, dimension: str, pos: tuple) -> Optional[dict]:
        """领地系统关闭或坐标不在任何领地内时返回 None（调用方可直接跳过领地检查）。"""
        if not self._is_land_system_enabled():
            return None
        x, y, z = self._parse_land_pos(pos)
        land_id = self.land_system.get_land_at_pos(dimension, x, z, y)
        if land_id is None:
            return None
        land_info = self.get_land_info(land_id)
        if not land_info:
            return None
        sub_info = None
        if y is not None:
            sub_land_id = self.get_sub_land_at_pos(land_id, int(x), int(y), int(z))
            if sub_land_id is not None:
                sub_info = self.get_sub_land_info(sub_land_id)
        return {
            "land_id": land_id,
            "land_info": land_info,
            "sub_info": sub_info,
        }

    def _send_land_deny_message(
        self, player: Player, kind: str, land_id: Optional[int] = None
    ) -> None:
        try:
            if kind == "actor_interact":
                player.send_message(
                    self.language_manager.GetText("LAND_ACTOR_INTERACTION_DENIED")
                )
            elif kind == "actor_damage":
                player.send_message(
                    self.language_manager.GetText("LAND_ACTOR_DAMAGE_DENIED")
                )
            elif kind == "frame":
                player.send_message(
                    self.language_manager.GetText("LAND_FRAME_PROTECT_HINT")
                )
            else:
                if land_id is not None and self.is_public_land(land_id):
                    owner_disp = self.language_manager.GetText("PUBLIC_LAND_NAME")
                else:
                    owner_disp = (
                        self.get_land_display_owner_name(land_id) if land_id else "?"
                    )
                player.send_message(
                    self.language_manager.GetText("LAND_PROTECT_HINT").format(owner_disp)
                )
        except Exception:
            pass

    def check_land_permission(
        self,
        player: Player,
        dimension: str,
        pos: tuple,
        kind: str,
        *,
        damaged_actor=None,
        send_hint: bool = True,
    ) -> bool:
        """
        统一领地权限检查。
        kind: operation | interact | actor_interact | actor_damage | frame
        领地系统未开启或当前坐标无领地：True（跳过领地部分）。
        """
        ctx = self._build_land_check_context(dimension, pos)
        if ctx is None:
            return True

        land_id = ctx["land_id"]
        land_info = ctx["land_info"]
        sub_info = ctx.get("sub_info")

        if sub_info and self._check_sub_land_permission(player, sub_info):
            return True

        owner_key = land_info.get("owner_xuid", "")

        if kind == "frame":
            if land_info.get("allow_frame", False):
                return True
            allowed = self._land_player_exempt_from_frame_protect(
                player, pos, land_id, land_info
            )
            if not allowed and send_hint:
                self._send_land_deny_message(player, kind, land_id)
            return allowed

        if kind == "actor_interact":
            if land_info.get("allow_actor_interaction", False):
                return True
            allowed = self._check_land_permission(player, land_info)
            if not allowed and send_hint:
                self._send_land_deny_message(player, kind, land_id)
            return allowed

        if kind == "actor_damage":
            if self.is_public_land(land_id):
                if not land_info.get("allow_actor_damage", False):
                    if send_hint:
                        self._send_land_deny_message(player, kind, land_id)
                    return False
                if damaged_actor is not None:
                    protected = self._get_public_land_protected_entities()
                    damaged_entity_type = getattr(damaged_actor, "type", None)
                    if damaged_entity_type and damaged_entity_type in protected:
                        if send_hint:
                            self._send_land_deny_message(player, kind, land_id)
                        return False
                return True
            if not land_info.get("allow_actor_damage", False):
                allowed = self._check_land_permission(player, land_info)
                if not allowed and send_hint:
                    self._send_land_deny_message(player, kind, land_id)
                return allowed
            return True

        if kind == "interact":
            if land_info.get("allow_public_interact", False):
                if LandSystem.parse_land_owner_guild_id(str(owner_key or "")) is None:
                    return True
            if self.land_system.is_public_land_owner(owner_key):
                if player.is_op:
                    return True
                if send_hint:
                    self._send_land_deny_message(player, kind, land_id)
                return False
            shared_users = land_info.get("shared_users", [])
            if (
                self._player_matches_land_owner_key(player, owner_key)
                or self._land_shared_user_grants_access(player, owner_key, shared_users)
                or self._land_interact_allowed_for_guild_peer(player, land_info)
            ):
                return True
            if send_hint:
                self._send_land_deny_message(player, kind, land_id)
            return False

        # operation（破坏/放置等建造类）
        if self.land_system.is_public_land_owner(owner_key):
            if player.is_op:
                return True
            if send_hint:
                self._send_land_deny_message(player, kind, land_id)
            return False
        shared_users = land_info.get("shared_users", [])
        if self._player_matches_land_owner_key(player, owner_key) or self._land_shared_user_grants_access(
            player, owner_key, shared_users
        ):
            return True
        if send_hint:
            self._send_land_deny_message(player, kind, land_id)
        return False

    def is_land_explosion_allowed_at(
        self, dimension: str, x: int, z: int, y: Optional[int] = None
    ) -> bool:
        """坐标是否允许爆炸破坏方块；系统关闭或无领地视为允许。"""
        if not self._is_land_system_enabled():
            return True
        land_id = self.land_system.get_land_at_pos(dimension, x, z, y)
        if land_id is None:
            return True
        land_info = self.get_land_info(land_id)
        if not land_info:
            return True
        return bool(land_info.get("allow_explosion", False))

    def land_only_place_wilderness_check(
        self, player: Player, dimension: str, pos: tuple, block_identifier: str
    ) -> bool:
        """配置的方块禁止在荒野放置；是否允许建造/授权由 land_operation_check 判定。"""
        bid = str(block_identifier or "").lower().strip()
        if not bid:
            return True
        if ":" not in bid:
            bid = "minecraft:" + bid
        if bid not in self._land_only_place_block_ids:
            return True
        if not self._is_land_system_enabled():
            return True
        x, y, z = self._parse_land_pos(pos)
        land_id = self.land_system.get_land_at_pos(dimension, x, z, y)
        if land_id is None:
            player.send_message(self.language_manager.GetText("LAND_ONLY_PLACE_WILDERNESS_HINT"))
            return False
        return True

    def land_operation_check(self, player: Player, dimension: str, pos: tuple):
        return self.check_land_permission(player, dimension, pos, "operation")

    def land_interact_check(self, player: Player, dimension: str, pos: tuple):
        return self.check_land_permission(player, dimension, pos, "interact")
    
    def spawn_protect_check(self, player: Player, dimension: str, pos: tuple):
        if self.if_protect_spawn and len(self.spawn_pos_dict):
            if not self.spawn_protect_check(dimension, pos[0], pos[2]):
                player.send_message(self.language_manager.GetText('SPAWN_PROTECT_HINT').format(self.spawn_protect_range))
                return False
        return True

    # Listener
    def _threaded_position_listener(self):
        """多线程位置检测方法"""
        self.logger.info(f"{ColorFormat.GREEN}[ARC Core]Position detection thread started")
        
        while self.position_thread_running:
            try:
                # 检查是否有在线玩家，如果没有则跳过此次检测
                if not self.server.online_players:
                    time.sleep(self.position_check_interval)
                    continue
                
                # 批量处理所有在线玩家
                players_to_process = list(self.server.online_players)
                
                for player in players_to_process:
                    if not self.position_thread_running:  # 提前退出检查
                        break
                        
                    try:
                        # 获取玩家位置信息
                        player_pos = self.get_player_position_vector(player)
                        dimension = get_dimension_id(player.location.dimension)
                        land_id = self.get_land_at_pos(dimension, player_pos[0], player_pos[2], player_pos[1])
                        
                        # 使用锁保护共享数据
                        with self.position_thread_lock:
                            # 初始化玩家领地记录
                            if player.name not in self.player_in_land_id_dict:
                                self.player_in_land_id_dict[player.name] = None
                            
                            # 检查领地变化
                            old_land_id = self.player_in_land_id_dict[player.name]
                            if self.is_land_id_changed(old_land_id, land_id):
                                self.player_in_land_id_dict[player.name] = land_id

                                try:
                                    # 仅在「从某领地走出到无领地区」时提示离开；
                                    # 领地A→领地B 直接以进入B 的提示覆盖，避免两条 tip 互相覆盖。
                                    leave_text = (
                                        self._build_land_transition_text(old_land_id, is_leaving=True)
                                        if (old_land_id is not None and land_id is None)
                                        else None
                                    )
                                    enter_text = (
                                        self._build_land_transition_text(land_id, is_leaving=False)
                                        if land_id is not None
                                        else None
                                    )

                                    new_land_name = self.get_land_name(land_id) if land_id is not None else None
                                    new_land_info = self.get_land_info(land_id) if land_id is not None else None
                                    new_land_is_public = self.is_public_land(land_id) if land_id is not None else False

                                    def create_land_message_sender(
                                        target_player,
                                        leave_message,
                                        enter_message,
                                        new_id,
                                        new_name,
                                        new_info,
                                        is_public,
                                    ):
                                        def send_land_message():
                                            try:
                                                if leave_message:
                                                    target_player.send_tip(leave_message)
                                                if enter_message:
                                                    target_player.send_tip(enter_message)
                                                if new_id is not None and new_info:
                                                    self.display_land_particle_boundary(target_player, new_info)
                                                    # 私人领地上架出售：非主人进入时弹出购买表单
                                                    if not is_public:
                                                        seller_key = str(new_info.get("owner_xuid") or "")
                                                        seller_px = LandSystem.parse_land_owner_player_xuid(seller_key)
                                                        listed = bool(new_info.get("for_sale"))
                                                        list_price = float(new_info.get("sale_price") or 0)
                                                        if (
                                                            seller_px is not None
                                                            and listed
                                                            and list_price > 0
                                                            and str(seller_px) != str(target_player.xuid)
                                                        ):
                                                            self._show_land_purchase_offer_form(
                                                                target_player, new_id, new_name, new_info
                                                            )
                                            except Exception as send_error:
                                                self.logger.warning(
                                                    f"[ARC Core]Failed to send land message to {target_player.name}: {str(send_error)}"
                                                )
                                        return send_land_message

                                    message_sender = create_land_message_sender(
                                        player,
                                        leave_text,
                                        enter_text,
                                        land_id,
                                        new_land_name,
                                        new_land_info,
                                        new_land_is_public,
                                    )

                                    if hasattr(self.server, "scheduler"):
                                        self.server.scheduler.run_task(self, message_sender, delay=0)

                                except Exception as e:
                                    self.logger.warning(
                                        f"[ARC Core]Error processing land change for {player.name}: {str(e)}"
                                    )
                                        
                    except Exception as e:
                        self.logger.warning(f"[ARC Core]Error processing player {player.name} position: {str(e)}")
                        continue
                
                # 等待下次检测
                time.sleep(self.position_check_interval)
                
            except Exception as e:
                self.logger.error(f"[ARC Core]Position detection thread error: {str(e)}")
                time.sleep(1)  # 发生错误时等待1秒再继续
        
        self.logger.info(f"{ColorFormat.YELLOW}[ARC Core]Position detection thread stopped")

    def start_position_thread(self):
        """启动位置检测线程"""
        if not self._is_land_system_enabled():
            return
        if self.position_thread is None or not self.position_thread.is_alive():
            self.position_thread_running = True
            self.position_thread = threading.Thread(
                target=self._threaded_position_listener,
                daemon=True,  # 设为守护线程，主程序退出时自动结束
                name="ARCCore-PositionDetection"
            )
            self.position_thread.start()
            self.logger.info(f"{ColorFormat.GREEN}[ARC Core]Position detection thread initialized")
        else:
            self.logger.warning(f"{ColorFormat.YELLOW}[ARC Core]Position detection thread already running")

    def stop_position_thread(self):
        """停止位置检测线程"""
        if self.position_thread and self.position_thread.is_alive():
            self.position_thread_running = False
            try:
                self.position_thread.join(timeout=2.0)  # 等待最多2秒让线程正常结束
                if self.position_thread.is_alive():
                    self.logger.warning(f"{ColorFormat.YELLOW}[ARC Core]Position detection thread did not stop gracefully")
                else:
                    self.logger.info(f"{ColorFormat.GREEN}[ARC Core]Position detection thread stopped successfully")
            except Exception as e:
                self.logger.error(f"[ARC Core]Error stopping position detection thread: {str(e)}")
        self.position_thread = None

    @staticmethod
    def is_land_id_changed(old_land_id: int | None, new_land_id: int | None) -> bool:
        """
        判断玩家所在领地ID是否发生变化
        :param old_land_id: 玩家之前所在的领地ID（可能为None）
        :param new_land_id: 玩家当前所在的领地ID（可能为None）
        :return: 是否发生变化
        """
        # 都是None则未变化
        if old_land_id is None and new_land_id is None:
            return False

        # 一个是None另一个不是,说明进入或离开了领地
        if (old_land_id is None) != (new_land_id is None):
            return True

        # 都不是None,直接比较数值是否相同
        return old_land_id != new_land_id

    # Database
    def init_database(self):
        self.init_player_basic_table()
        self.init_spawn_locations_table()
        self.init_small_horn_orders_table()
        self.init_cross_server_table()
        self.economy.init_economy_table()
        self.economy.upgrade_player_economy_table_to_float()
        self.land_system.init_land_tables()
        self.land_system.init_sub_land_table()
        self.teleport_system.init_teleport_tables()
        self.title_system.ensure_tables()
        self.guild_system.ensure_tables()
        self.conditional_titles.ensure_tables()
        self.sidebar_system.init_pref_table()
        self.activity_stats.ensure_tables()
        self._drop_obsolete_core_tables()

    def _drop_obsolete_core_tables(self) -> None:
        """删除已迁出/废弃的核心库表（可重复执行）。"""
        obsolete = (
            # 旧成就解锁表：解锁标记已在成就插件 achievement.db
            "player_achievement_unlocked",
            # 一次性 UUID→XUID 迁移记录，代码不再读取
            "migration_history",
        )
        for name in obsolete:
            try:
                self.database_manager.execute(f"DROP TABLE IF EXISTS {name}")
            except Exception:
                pass

    def _can_compute_conditional_titles(self) -> bool:
        """仅权威服计算条件头衔（首富等）。

        显式 CONDITIONAL_TITLE_AUTHORITY 优先；未配置时：
        - 远程客户端模式不计算；
        - 其余（同步中心 / 单机）计算。
        """
        from endstone_arc_core.sync_config import setting_bool

        raw = self.setting_manager.GetSetting("CONDITIONAL_TITLE_AUTHORITY")
        if raw is not None and str(raw).strip() != "":
            return setting_bool(self.setting_manager, "CONDITIONAL_TITLE_AUTHORITY", True)

        mode = getattr(self, "_sync_consumer_mode", "none")
        if mode == "client":
            return False
        return True

    @property
    def current_richest_xuid(self) -> Optional[str]:
        try:
            return self.conditional_titles.get_holder_xuid("richest")
        except Exception:
            return None

    def _get_richest_title_name(self) -> str:
        name = self.setting_manager.GetSetting("RICHEST_TITLE_NAME")
        name = (str(name).strip() if name is not None else "").strip()
        return name if name else "首富"

    def _ensure_richest_title_definition(self) -> None:
        try:
            provider = self.conditional_titles.get_provider("richest")
            if provider is not None:
                provider.ensure_definition()
                return
        except Exception:
            pass
        richest_title_name = self._get_richest_title_name()
        self.title_system.set_title_definition(
            richest_title_name,
            "传奇",
            "服务器里最富有玩家",
            0.0,
            [],
        )

    def _query_current_richest_xuid(self) -> Optional[str]:
        """按配置决定是否隐藏 OP，然后查询财富榜第一名 xuid（同分按 xuid 稳定）。"""
        try:
            provider = self.conditional_titles.get_provider("richest")
            if provider is not None:
                return provider.query_holder_xuid()
        except Exception:
            pass
        try:
            if self.hide_op_in_money_ranking:
                row = self.database_manager.query_one(
                    "SELECT e.xuid, e.money "
                    "FROM player_economy e "
                    "LEFT JOIN player_basic_info b ON e.xuid = b.xuid "
                    "WHERE (b.once_op IS NULL OR b.once_op = 0) "
                    "ORDER BY e.money DESC, e.xuid ASC LIMIT 1"
                )
            else:
                row = self.database_manager.query_one(
                    "SELECT xuid, money FROM player_economy "
                    "ORDER BY money DESC, xuid ASC LIMIT 1"
                )
            if not row or not row.get("xuid"):
                return None
            return str(row["xuid"]).strip()
        except Exception:
            return None

    def _update_richest_title_if_needed(self) -> None:
        """金钱变化后调用：仅权威服迁移首富条件头衔。"""
        try:
            self.conditional_titles.refresh("richest")
        except Exception:
            pass

    def _on_economy_balance_changed_for_sidebar(self, xuid: str) -> None:
        """Economy 写余额成功 → 侧边栏即时刷新金钱行。"""
        try:
            if getattr(self, "sidebar_system", None) is not None:
                self.sidebar_system.notify_money_changed(xuid)
        except Exception:
            pass

    def _schedule_richest_title_refresh(self) -> None:
        """从服经济上行到主服后调度刷新（尽量落到主线程）。"""
        def _run():
            self._update_richest_title_if_needed()

        try:
            self.server.scheduler.run_task(self, _run)
        except Exception:
            _run()

    # Player basic info
    def _column_exists(self, table: str, column: str) -> bool:
        """
        检查表中是否存在指定列
        :param table: 表名
        :param column: 列名
        :return: 列是否存在
        """
        try:
            result = self.database_manager.query_one(f"PRAGMA table_info({table})")
            if not result:
                return False
            
            # PRAGMA table_info 返回所有列的信息
            columns_info = self.database_manager.query_all(f"PRAGMA table_info({table})")
            for col_info in columns_info:
                if col_info['name'] == column:
                    return True
            return False
        except Exception as e:
            # 在__init__期间不能使用self.logger，使用print代替
            print(f"[ARC Core]Check column exists error: {str(e)}")
            return False

    def _add_column_if_not_exists(self, table: str, column: str, column_type: str) -> bool:
        """
        如果列不存在则添加列
        :param table: 表名
        :param column: 列名
        :param column_type: 列类型定义
        :return: 是否成功
        """
        try:
            if not self._column_exists(table, column):
                sql = f"ALTER TABLE {table} ADD COLUMN {column} {column_type}"
                success = self.database_manager.execute(sql)
                if success:
                    # 在__init__期间不能使用self.logger，使用print代替
                    print(f"[ARC Core]Added column '{column}' to table '{table}'")
                else:
                    print(f"[ARC Core]Failed to add column '{column}' to table '{table}'")
                return success
            return True  # 列已存在，返回成功
        except Exception as e:
            # 在__init__期间不能使用self.logger，使用print代替
            print(f"[ARC Core]Add column error: {str(e)}")
            return False

    def _player_basic_info_columns(self) -> set:
        """Return column names of player_basic_info (empty if missing)."""
        try:
            rows = self.database_manager.query_all(
                "PRAGMA table_info(player_basic_info)", ()
            ) or []
            return {str(r.get("name") or "") for r in rows if r.get("name")}
        except Exception:
            return set()

    def _upgrade_player_basic_table(self) -> bool:
        """Upgrade cross-server player_basic_info columns (account + playtime + once_op)."""
        try:
            success = True
            upgrades = [
                ('inviter_xuid', 'TEXT'),
                ('pending_invite_reward_times', 'INTEGER DEFAULT 0'),
                ('default_title_auto_equipped', 'INTEGER DEFAULT 0'),
                ('total_playtime', 'INTEGER DEFAULT 0'),
                ('session_count', 'INTEGER DEFAULT 0'),
                ('last_join_time', 'TEXT'),
                ('last_quit_time', 'TEXT'),
                # 跨服粘性标记：任意服以 OP 登录过即置 1，永不回落；排行排除用
                ('once_op', 'INTEGER DEFAULT 0'),
            ]
            for col, decl in upgrades:
                if not self._add_column_if_not_exists('player_basic_info', col, decl):
                    success = False
            # 旧镜像列 is_op → once_op（只升不降）
            cols = self._player_basic_info_columns()
            if "is_op" in cols and "once_op" in cols:
                self.database_manager.execute(
                    "UPDATE player_basic_info SET once_op = 1 "
                    "WHERE COALESCE(is_op, 0) = 1 AND COALESCE(once_op, 0) = 0"
                )
            return success
        except Exception as e:
            print(f"[ARC Core]Upgrade player basic table error: {str(e)}")
            return False

    def _mark_player_once_op(self, xuid: str) -> None:
        """跨服粘性：任意服确认过 OP 后置 once_op=1，之后不再清零。"""
        xuid_s = str(xuid or "").strip()
        if not xuid_s:
            return
        try:
            if "once_op" not in self._player_basic_info_columns():
                return
            self.database_manager.execute(
                "UPDATE player_basic_info SET once_op = 1 "
                "WHERE xuid = ? AND COALESCE(once_op, 0) = 0",
                (xuid_s,),
            )
        except Exception:
            pass

    def get_player_once_op_by_xuid(self, player_xuid: str) -> Optional[bool]:
        """跨服是否曾以 OP 登录（排行排除）；无记录返回 None。"""
        try:
            if "once_op" not in self._player_basic_info_columns():
                return None
            row = self.database_manager.query_one(
                "SELECT once_op FROM player_basic_info WHERE xuid = ?",
                (str(player_xuid),),
            )
            if row is None:
                return None
            return bool(int(row.get("once_op") or 0))
        except Exception:
            return None

    def _player_local_info_columns(self) -> set:
        """Return column names of player_local_info (empty if missing)."""
        try:
            rows = self.database_manager.query_all(
                "PRAGMA table_info(player_local_info)", ()
            ) or []
            return {str(r.get("name") or "") for r in rows if r.get("name")}
        except Exception:
            return set()

    def init_player_local_table(self) -> bool:
        """Initialize per-server local profile (OP / free land / check-in)."""
        default_free_blocks = self.setting_manager.GetSetting('DEFAULT_FREE_LAND_BLOCKS') or '100'
        fields = {
            'xuid': 'TEXT PRIMARY KEY',
            'is_op': 'INTEGER DEFAULT 0',
            'remaining_free_land_blocks': f'INTEGER DEFAULT {default_free_blocks}',
            'last_checkin_date': 'TEXT',
            'total_checkin_count': 'INTEGER DEFAULT 0',
            'last_checkin_at': 'TEXT',
            'continuous_checkin_days': 'INTEGER DEFAULT 0',
        }
        # Always on default DATABASE_PATH (never synced cross-server).
        ok = self.database_manager.create_table('player_local_info', fields)
        # Legacy local tables may lack OP / check-in cols.
        for col, decl in (
            ('is_op', 'INTEGER DEFAULT 0'),
            ('remaining_free_land_blocks', f'INTEGER DEFAULT {default_free_blocks}'),
            ('last_checkin_date', 'TEXT'),
            ('total_checkin_count', 'INTEGER DEFAULT 0'),
            ('last_checkin_at', 'TEXT'),
            ('continuous_checkin_days', 'INTEGER DEFAULT 0'),
        ):
            self._add_column_if_not_exists('player_local_info', col, decl)
        return ok

    def _migrate_player_basic_info_split(self) -> None:
        """Normalize tables: check-in is local; playtime/session is cross-server.

        - player_basic_info (synced): account + total_playtime / session_count / join-quit / once_op
        - player_local_info (local): is_op / free land / check-in stats
        """
        default_free = int(self.setting_manager.GetSetting('DEFAULT_FREE_LAND_BLOCKS') or '100')
        try:
            self.init_player_local_table()
            self._upgrade_player_basic_table()

            # Drop obsolete shared-file seed table if present.
            try:
                self.database_manager.execute("DROP TABLE IF EXISTS player_local_info_seed")
            except Exception:
                pass

            basic_cols = self._player_basic_info_columns()
            local_cols = self._player_local_info_columns()
            basic_rows = self.database_manager.query_all(
                "SELECT * FROM player_basic_info", ()
            ) or []
            local_by_xuid = {}
            for row in self.database_manager.query_all(
                "SELECT * FROM player_local_info", ()
            ) or []:
                xuid = str(row.get("xuid") or "").strip()
                if xuid:
                    local_by_xuid[xuid] = row

            def _i(row, key, default=0):
                if not row:
                    return default
                try:
                    v = row.get(key)
                    return default if v is None else int(v)
                except (TypeError, ValueError):
                    return default

            for brow in basic_rows:
                xuid = str(brow.get("xuid") or "").strip()
                if not xuid:
                    continue
                lrow = local_by_xuid.get(xuid)

                # Ensure local row exists.
                if not lrow:
                    self.database_manager.insert(
                        "player_local_info",
                        {
                            "xuid": xuid,
                            "is_op": _i(brow, "is_op", 0),
                            "remaining_free_land_blocks": _i(
                                brow, "remaining_free_land_blocks", default_free
                            ),
                            "last_checkin_date": brow.get("last_checkin_date"),
                            "total_checkin_count": _i(brow, "total_checkin_count", 0),
                            "last_checkin_at": brow.get("last_checkin_at"),
                            "continuous_checkin_days": _i(
                                brow, "continuous_checkin_days", 0
                            ),
                        },
                    )
                    lrow = self.database_manager.query_one(
                        "SELECT * FROM player_local_info WHERE xuid = ?", (xuid,)
                    ) or {}
                    local_by_xuid[xuid] = lrow

                # Local: OP / free land / check-in（本服权限仍以 local.is_op 为准）
                local_update = {}
                if "is_op" in basic_cols:
                    # 旧镜像列仅用于一次性灌入本服 local
                    local_update["is_op"] = max(_i(lrow, "is_op"), _i(brow, "is_op"))
                if "remaining_free_land_blocks" in basic_cols:
                    # Prefer whichever is already on local; else take basic.
                    if lrow.get("remaining_free_land_blocks") is None:
                        local_update["remaining_free_land_blocks"] = _i(
                            brow, "remaining_free_land_blocks", default_free
                        )
                if "last_checkin_date" in basic_cols:
                    if not (lrow.get("last_checkin_date") or "").strip() and (
                        brow.get("last_checkin_date") or ""
                    ).strip():
                        local_update["last_checkin_date"] = brow.get("last_checkin_date")
                        local_update["last_checkin_at"] = brow.get("last_checkin_at")
                        local_update["total_checkin_count"] = max(
                            _i(lrow, "total_checkin_count"),
                            _i(brow, "total_checkin_count"),
                        )
                        local_update["continuous_checkin_days"] = max(
                            _i(lrow, "continuous_checkin_days"),
                            _i(brow, "continuous_checkin_days"),
                        )
                    elif _i(brow, "total_checkin_count") > _i(lrow, "total_checkin_count"):
                        # One-time promote synced historical check-in totals into local.
                        local_update["total_checkin_count"] = _i(brow, "total_checkin_count")
                        if (brow.get("last_checkin_date") or "").strip():
                            local_update["last_checkin_date"] = brow.get("last_checkin_date")
                            local_update["last_checkin_at"] = brow.get("last_checkin_at")
                        local_update["continuous_checkin_days"] = max(
                            _i(lrow, "continuous_checkin_days"),
                            _i(brow, "continuous_checkin_days"),
                        )
                if local_update:
                    self.database_manager.update(
                        table="player_local_info",
                        data=local_update,
                        where="xuid = ?",
                        params=(xuid,),
                    )

                # Synced: playtime / session (max of basic + local legacy).
                pt = max(
                    _i(brow, "total_playtime"),
                    _i(lrow, "total_playtime") if "total_playtime" in local_cols else 0,
                )
                sc = max(
                    _i(brow, "session_count"),
                    _i(lrow, "session_count") if "session_count" in local_cols else 0,
                )
                join_t = brow.get("last_join_time") or (
                    lrow.get("last_join_time") if "last_join_time" in local_cols else None
                )
                quit_t = brow.get("last_quit_time") or (
                    lrow.get("last_quit_time") if "last_quit_time" in local_cols else None
                )
                self.database_manager.update(
                    table="player_basic_info",
                    data={
                        "total_playtime": pt,
                        "session_count": sc,
                        "last_join_time": join_t,
                        "last_quit_time": quit_t,
                    },
                    where="xuid = ?",
                    params=(xuid,),
                )
                # 跨服 once_op：本服曾是 OP / 旧 basic.is_op / 已有 once_op → 粘性置 1
                try:
                    if _i(lrow, "is_op") or _i(brow, "is_op") or _i(brow, "once_op"):
                        self._mark_player_once_op(xuid)
                except Exception:
                    pass

            # Rebuild basic without check-in / free-land / legacy is_op（改用 once_op）。
            basic_cols = self._player_basic_info_columns()
            drop_from_basic = {
                "remaining_free_land_blocks",
                "last_checkin_date",
                "total_checkin_count",
                "last_checkin_at",
                "continuous_checkin_days",
                "is_op",
            }
            if basic_cols & drop_from_basic:
                sync_cols = [
                    "uuid",
                    "xuid",
                    "name",
                    "password",
                    "inviter_xuid",
                    "pending_invite_reward_times",
                    "default_title_auto_equipped",
                    "total_playtime",
                    "session_count",
                    "last_join_time",
                    "last_quit_time",
                    "once_op",
                ]
                # Ensure playtime / once_op cols exist before rebuild select.
                self._upgrade_player_basic_table()
                basic_cols = self._player_basic_info_columns()
                present = [c for c in sync_cols if c in basic_cols]
                if "uuid" in present and "xuid" in present and "name" in present:
                    create_sql = (
                        "CREATE TABLE player_basic_info__sync_new ("
                        "uuid TEXT PRIMARY KEY, "
                        "xuid TEXT NOT NULL, "
                        "name TEXT NOT NULL, "
                        "password TEXT, "
                        "inviter_xuid TEXT, "
                        "pending_invite_reward_times INTEGER DEFAULT 0, "
                        "default_title_auto_equipped INTEGER DEFAULT 0, "
                        "total_playtime INTEGER DEFAULT 0, "
                        "session_count INTEGER DEFAULT 0, "
                        "last_join_time TEXT, "
                        "last_quit_time TEXT, "
                        "once_op INTEGER DEFAULT 0"
                        ")"
                    )
                    ok = self.database_manager.rebuild_table_copy(
                        logical_table="player_basic_info",
                        temp_table="player_basic_info__sync_new",
                        create_sql=create_sql,
                        copy_columns=present,
                    )
                    if ok:
                        print(
                            "[ARC Core]Rebuilt player_basic_info "
                            "(synced account + playtime + once_op; check-in is local)"
                        )
                    else:
                        print(
                            "[ARC Core]Skip rebuild player_basic_info "
                            "(backup/copy failed; original table kept)"
                        )

            # Rebuild local without playtime columns if present.
            local_cols = self._player_local_info_columns()
            if local_cols & {
                "total_playtime",
                "session_count",
                "last_join_time",
                "last_quit_time",
            }:
                keep = [
                    "xuid",
                    "is_op",
                    "remaining_free_land_blocks",
                    "last_checkin_date",
                    "total_checkin_count",
                    "last_checkin_at",
                    "continuous_checkin_days",
                ]
                for col, decl in (
                    ('last_checkin_date', 'TEXT'),
                    ('total_checkin_count', 'INTEGER DEFAULT 0'),
                    ('last_checkin_at', 'TEXT'),
                    ('continuous_checkin_days', 'INTEGER DEFAULT 0'),
                ):
                    self._add_column_if_not_exists('player_local_info', col, decl)
                local_cols = self._player_local_info_columns()
                present = [c for c in keep if c in local_cols]
                if "xuid" in present:
                    default_free_sql = default_free
                    create_sql = (
                        "CREATE TABLE player_local_info__new ("
                        "xuid TEXT PRIMARY KEY, "
                        "is_op INTEGER DEFAULT 0, "
                        f"remaining_free_land_blocks INTEGER DEFAULT {default_free_sql}, "
                        "last_checkin_date TEXT, "
                        "total_checkin_count INTEGER DEFAULT 0, "
                        "last_checkin_at TEXT, "
                        "continuous_checkin_days INTEGER DEFAULT 0"
                        ")"
                    )
                    ok = self.database_manager.rebuild_table_copy(
                        logical_table="player_local_info",
                        temp_table="player_local_info__new",
                        create_sql=create_sql,
                        copy_columns=present,
                    )
                    if ok:
                        print(
                            "[ARC Core]Rebuilt player_local_info "
                            "(OP / free land / check-in; playtime is synced)"
                        )
                    else:
                        print(
                            "[ARC Core]Skip rebuild player_local_info "
                            "(backup/copy failed; original table kept)"
                        )
        except Exception as e:
            print(f"[ARC Core]Migrate player_basic_info split error: {e}")

    def init_player_basic_table(self) -> bool:
        """Initialize cross-server player_basic_info + per-server player_local_info."""
        fields = {
            'uuid': 'TEXT PRIMARY KEY',
            'xuid': 'TEXT NOT NULL',
            'name': 'TEXT NOT NULL',
            'password': 'TEXT',
            'inviter_xuid': 'TEXT',
            'pending_invite_reward_times': 'INTEGER DEFAULT 0',
            'default_title_auto_equipped': 'INTEGER DEFAULT 0',
            'total_playtime': 'INTEGER DEFAULT 0',
            'session_count': 'INTEGER DEFAULT 0',
            'last_join_time': 'TEXT',
            'last_quit_time': 'TEXT',
            'once_op': 'INTEGER DEFAULT 0',
        }
        result = self.database_manager.create_table('player_basic_info', fields)
        if result:
            self._upgrade_player_basic_table()
        local_ok = self.init_player_local_table()
        self._migrate_player_basic_info_split()
        return bool(result and local_ok)

    def _hash_password(self, password: str) -> str:
        """
        对密码进行加密
        :param password: 原始密码
        :return: 加密后的密码
        """
        # 使用SHA-256进行加密
        return hashlib.sha256(password.encode()).hexdigest()

    def init_player_local_info(self, player: Player) -> bool:
        """Initialize per-server local profile row for player."""
        try:
            default_free_blocks = int(
                self.setting_manager.GetSetting('DEFAULT_FREE_LAND_BLOCKS') or '100'
            )
            xuid = str(player.xuid)
            existing = self.database_manager.query_one(
                "SELECT xuid FROM player_local_info WHERE xuid = ?", (xuid,)
            )
            if existing:
                return True
            ok = self.database_manager.insert(
                "player_local_info",
                {
                    "xuid": xuid,
                    "is_op": 1 if player.is_op else 0,
                    "remaining_free_land_blocks": default_free_blocks,
                    "last_checkin_date": None,
                    "total_checkin_count": 0,
                    "last_checkin_at": None,
                    "continuous_checkin_days": 0,
                },
            )
            if ok and player.is_op:
                self._mark_player_once_op(xuid)
            return ok
        except Exception as e:
            self._safe_log(
                "error",
                f"{ColorFormat.RED}[ARC Core]Init player local info error: {str(e)}",
            )
            return False

    def init_player_basic_info(self, player: Player) -> bool:
        """
        初始化玩家跨服基本信息 + 本服本地信息
        :param player: 玩家对象
        :return: 是否初始化成功
        """
        try:
            player_data = {
                'uuid': str(player.unique_id),
                'xuid': str(player.xuid),
                'name': player.name,
                'password': None,
                'inviter_xuid': None,
                'pending_invite_reward_times': 0,
                'default_title_auto_equipped': 0,
                'total_playtime': 0,
                'session_count': 0,
            }
            ok = self.database_manager.insert('player_basic_info', player_data)
            local_ok = self.init_player_local_info(player)
            return bool(ok and local_ok)
        except Exception as e:
            self._safe_log('error', f"{ColorFormat.RED}[ARC Core]Init player basic info error: {str(e)}")
            return False

    def init_player_economy_info(self, player: Player) -> bool:
        """初始化玩家经济信息（委托 Economy）"""
        return self.economy.init_player_economy_by_xuid(str(player.xuid))

    def ensure_player_data_initialized(self, player: Player) -> tuple[bool, bool]:
        """
        确保玩家数据已完全初始化（基本信息和经济数据）
        :param player: 玩家对象
        :return: (是否初始化成功, 是否为新玩家)
        """
        try:
            player_xuid = str(player.xuid)
            success = True
            is_new_player = False

            # 检查并初始化玩家基本信息（使用XUID作为主键）
            basic_info = self.database_manager.query_one(
                "SELECT xuid, uuid FROM player_basic_info WHERE xuid = ?",
                (player_xuid,)
            )
            if not basic_info:
                is_new_player = True  # 没有基本信息说明是新玩家
                if not self.init_player_basic_info(player):
                    self._safe_log('error', f"{ColorFormat.RED}[ARC Core]Failed to init basic info for player {player.name}")
                    success = False
                else:
                    self._safe_log('info', f"{ColorFormat.GREEN}[ARC Core]Initialized basic info for new player {player.name}")
            else:
                self._fix_recovered_player_uuid(player, basic_info)

            # Ensure per-server local profile exists
            self.init_player_local_info(player)

            # 更新玩家名称（如果发生变化）
            self.update_player_name(player)

            # 更新玩家OP状态
            self.update_player_op_status(player)

            # 检查并初始化玩家经济信息
            if not self.init_player_economy_info(player):
                self._safe_log('error', f"{ColorFormat.RED}[ARC Core]Failed to init economy info for player {player.name}")
                success = False
            else:
                # 获取初始化后的金钱数量用于日志
                money = self.get_player_money(player)
                if is_new_player:
                    self._safe_log('info', f"{ColorFormat.GREEN}[ARC Core]Initialized economy data for new player {player.name}, balance: {money}")
                else:
                    self._safe_log('info', f"{ColorFormat.GREEN}[ARC Core]Ensured economy data for player {player.name}, balance: {money}")

            return success, is_new_player
        except Exception as e:
            self._safe_log('error', f"{ColorFormat.RED}[ARC Core]Ensure player data initialized error: {str(e)}")
            return False, False

    def _fix_recovered_player_uuid(self, player: Player, row: Optional[Dict[str, Any]]) -> None:
        """把恢复占位 uuid（recovered-<xuid>）覆写为真实 unique_id。"""
        uuid_s = str((row or {}).get("uuid") or "")
        if not uuid_s.startswith(RECOVERED_UUID_PREFIX):
            return
        real_uuid = str(player.unique_id)
        xuid = str(player.xuid)
        try:
            clash = self.database_manager.query_one(
                "SELECT xuid FROM player_basic_info WHERE uuid = ?",
                (real_uuid,),
            )
            if clash and str(clash.get("xuid") or "") != xuid:
                self._safe_log(
                    "warning",
                    f"[ARC Core]Skip recovered uuid rewrite for {player.name}: "
                    f"{real_uuid} already used by xuid={clash.get('xuid')}",
                )
                return
            self.database_manager.update(
                table="player_basic_info",
                data={"uuid": real_uuid},
                where="xuid = ? AND uuid LIKE ?",
                params=(xuid, RECOVERED_UUID_PREFIX + "%"),
            )
            if isinstance(row, dict):
                row["uuid"] = real_uuid
        except Exception as e:
            self._safe_log(
                "warning",
                f"[ARC Core]Fix recovered uuid error player={player.name!r}: {e}",
            )

    def get_player_basic_info(self, player: Player) -> Optional[Dict[str, Any]]:
        """
        获取玩家基本信息（跨服字段 + 本服本地字段合并，供 UI 兼容）
        :param player: 玩家对象
        :return: 玩家信息字典或None(如果发生错误)
        """
        try:
            result = self.database_manager.query_one(
                "SELECT * FROM player_basic_info WHERE xuid = ?",
                (str(player.xuid),)
            )
            if result is None:
                if self.init_player_basic_info(player):
                    result = {
                        'uuid': str(player.unique_id),
                        'xuid': str(player.xuid),
                        'name': player.name,
                        'password': None,
                        'default_title_auto_equipped': 0
                    }
                else:
                    return None
            else:
                self._fix_recovered_player_uuid(player, result)
                if str(result.get("uuid") or "").startswith(RECOVERED_UUID_PREFIX):
                    result["uuid"] = str(player.unique_id)
            self.init_player_local_info(player)
            local = self.database_manager.query_one(
                "SELECT * FROM player_local_info WHERE xuid = ?",
                (str(player.xuid),),
            ) or {}
            merged = dict(result)
            merged.update(local)
            return merged
        except Exception as e:
            self.logger.error(f"{ColorFormat.RED}[ARC Core]Get player basic info error: {str(e)}")
            return None

    def set_player_password(self, player: Player, password: str) -> bool:
        """
        设置玩家密码
        :param player: 玩家对象
        :param password: 原始密码
        :return: 是否设置成功
        """
        try:
            hashed_password = self._hash_password(password)
            return self.database_manager.update(
                table='player_basic_info',
                data={'password': hashed_password},
                where='xuid = ?',
                params=(str(player.xuid),)
            )
        except Exception as e:
            self.logger.error(f"{ColorFormat.RED}[ARC Core]Set player password error: {str(e)}")
            return False

    def verify_player_password(self, player: Player, password: str) -> bool:
        """
        验证玩家密码
        :param player: 玩家对象
        :param password: 待验证的密码
        :return: 密码是否正确
        """
        try:
            result = self.database_manager.query_one(
                "SELECT password FROM player_basic_info WHERE xuid = ?",
                (str(player.xuid),)
            )
            if not result or not result['password']:
                return False
            return result['password'] == self._hash_password(password)
        except Exception as e:
            self.logger.error(f"{ColorFormat.RED}[ARC Core]Verify player password error: {str(e)}")
            return False

    def update_player_name(self, player: Player) -> bool:
        """
        更新玩家名称（如果发生变化）
        :param player: 玩家对象
        :return: 是否需要更新以及更新是否成功
        """
        try:
            current_info = self.database_manager.query_one(
                "SELECT name FROM player_basic_info WHERE xuid = ?",
                (str(player.xuid),)
            )

            if not current_info:
                return False

            if current_info['name'] != player.name:
                # 名称发生变化，需要更新
                success = self.database_manager.update(
                    table='player_basic_info',
                    data={'name': player.name},
                    where='xuid = ?',
                    params=(str(player.xuid),)
                )
                if success:
                    self._safe_log('info', f"Player {current_info['name']} changed name to {player.name}")
                return success

            return True  # 名称没有变化，视为成功
        except Exception as e:
            self._safe_log('error', f"{ColorFormat.RED}[ARC Core]Update player name error: {str(e)}")
            return False

    def update_player_op_status(self, player: Player) -> bool:
        """
        更新本服 OP（player_local_info.is_op）；若当前为 OP 则跨服粘性标记 once_op。
        :param player: 玩家对象
        :return: 是否更新成功
        """
        try:
            current_op_status = 1 if player.is_op else 0
            xuid = str(player.xuid)

            self.init_player_local_info(player)
            current_info = self.database_manager.query_one(
                "SELECT is_op FROM player_local_info WHERE xuid = ?",
                (xuid,),
            )

            if current_info is not None:
                stored_op_status = int(current_info.get("is_op") or 0)
                if stored_op_status != current_op_status:
                    success = self.database_manager.update(
                        table="player_local_info",
                        data={"is_op": current_op_status},
                        where="xuid = ?",
                        params=(xuid,),
                    )
                    if success:
                        status_text = "OP" if current_op_status else "非OP"
                        self._safe_log(
                            "info",
                            f"{ColorFormat.GREEN}[ARC Core]Updated player OP status: "
                            f"{player.name} -> {status_text}",
                        )
                    else:
                        return False

            # 任意服以 OP 身份在线过 → 跨服 once_op=1（卸任不回落）
            if current_op_status:
                self._mark_player_once_op(xuid)
            return True
        except Exception as e:
            self._safe_log(
                "error",
                f"{ColorFormat.RED}[ARC Core]Update player OP status error: {str(e)}",
            )
            return False

    def get_offline_player_op_status(self, player_name: str) -> Optional[bool]:
        """
        获取离线玩家的OP状态
        :param player_name: 玩家名称
        :return: OP状态，如果玩家不存在则返回None
        """
        try:
            player_xuid = self.get_player_xuid_by_name(player_name)
            if not player_xuid:
                return None
            return self.get_offline_player_op_status_by_xuid(player_xuid)
        except Exception as e:
            self._safe_log('error', f"{ColorFormat.RED}[ARC Core]Get offline player OP status error: {str(e)}")
            return None

    def get_offline_player_op_status_by_xuid(self, player_xuid: str) -> Optional[bool]:
        """
        通过XUID获取离线玩家的OP状态
        :param player_xuid: 玩家XUID
        :return: OP状态，如果玩家不存在则返回None
        """
        try:
            result = self.database_manager.query_one(
                "SELECT is_op FROM player_local_info WHERE xuid = ?",
                (player_xuid,)
            )
            if result is not None:
                return bool(result['is_op'])
            return None
        except Exception as e:
            self._safe_log('error', f"{ColorFormat.RED}[ARC Core]Get offline player OP status by XUID error: {str(e)}")
            return None

    def get_offline_player_op_status_by_uuid(self, player_uuid: str) -> Optional[bool]:
        """
        通过UUID获取离线玩家的OP状态 (兼容性方法, 建议使用get_offline_player_op_status_by_xuid)
        :param player_uuid: 玩家UUID
        :return: OP状态，如果玩家不存在则返回None
        """
        try:
            result = self.database_manager.query_one(
                "SELECT l.is_op AS is_op FROM player_local_info l "
                "JOIN player_basic_info b ON b.xuid = l.xuid "
                "WHERE b.uuid = ?",
                (player_uuid,)
            )
            if result is not None:
                return bool(result['is_op'])
            return None
        except Exception as e:
            self._safe_log('error', f"{ColorFormat.RED}[ARC Core]Get offline player OP status by UUID error: {str(e)}")
            return None

    def get_player_name_by_xuid(
        self, player_xuid: str, return_with_title: bool = True
    ) -> Optional[str]:
        """
        通过 XUID 获取玩家名称。
        :param player_xuid: 玩家 XUID
        :param return_with_title: 为 True（默认）时返回展示名：§白[无公会]§r 或 §普通色[公会名]§r，再接 [头衔]§r 与名字（与聊天、头顶名一致）；为 False 时返回库中裸名，适合指令参数等。
        """
        try:
            result = self.database_manager.query_one(
                "SELECT name FROM player_basic_info WHERE xuid = ?",
                (player_xuid,),
            )
            if not result or result.get("name") is None:
                return None
            raw = str(result["name"]).strip()
            if not raw:
                return None
            if not return_with_title:
                return raw
            try:
                equipped = self.title_system.get_equipped_title_by_xuid(str(player_xuid).strip())
            except Exception:
                equipped = None
            return self.format_player_display_label_with_guild(
                raw, equipped, str(player_xuid).strip()
            )
        except Exception as e:
            self.logger.error(f"{ColorFormat.RED}[ARC Core]Get player name by XUID error: {str(e)}")
            return None

    def _put_chat_prefix(self, prefix_name: str, priority: int) -> bool:
        """写入前缀注册表。priority 钳制到 >=0；同名覆盖。"""
        name = str(prefix_name or "").strip()
        if not name:
            return False
        try:
            prio = int(priority)
        except (TypeError, ValueError):
            return False
        if prio < 0:
            prio = 0
        with self._chat_prefixes_lock:
            self._chat_prefixes[name] = {"priority": prio}
        return True

    def _register_core_chat_prefixes(self) -> None:
        """内置聊天前缀：公会 priority=2，头衔 priority=3。"""
        self._put_chat_prefix(CHAT_PREFIX_GUILD, CHAT_PREFIX_PRIORITY_GUILD)
        self._put_chat_prefix(CHAT_PREFIX_TITLE, CHAT_PREFIX_PRIORITY_TITLE)

    def _compute_guild_chat_prefix(self, xuid: str) -> str:
        """公会前缀展示文本：有公会为带色 [公会名]，否则 §f[无公会]§r。"""
        xs = str(xuid or "").strip()
        no_guild_label = self.language_manager.GetText("GUILD_DISPLAY_NO_GUILD_SHORT")
        if no_guild_label is None or not str(no_guild_label).strip():
            no_guild_label = "[无公会]"
        else:
            no_guild_label = str(no_guild_label).strip()
        guild_prefix = ""
        try:
            if xs and getattr(self, "guild_system", None):
                mem = self.guild_system.get_membership(xs)
                if mem:
                    gid = int(mem["guild_id"])
                    g = self.guild_system.get_guild(gid)
                    if g and g.get("name"):
                        # 二次去色：即便旧库里残留 §X 也不会污染聊天/头顶名
                        gname = guild_strip_mc_color_codes(g.get("name")).strip()
                        if gname:
                            tier = self.guild_system.normalize_size_tier(g.get("size_tier"))
                            gc = self._guild_size_tier_color(tier)
                            if not gc:
                                gc = self.title_system.get_normal_rarity_color()
                            guild_prefix = f"{gc}[{gname}]§r"
        except Exception:
            guild_prefix = ""
        if not guild_prefix:
            guild_prefix = f"§f{no_guild_label}§r"
        return guild_prefix

    def _compute_title_chat_prefix(
        self, xuid: str, equipped_title: Optional[str]
    ) -> str:
        """头衔前缀展示文本；未佩戴则为空串。"""
        xs = str(xuid or "").strip()
        et = (equipped_title or "").strip() if equipped_title else ""
        if not et:
            return ""
        rarity = None
        try:
            if xs:
                info = self.title_system.get_equipped_title_entry_by_xuid(xs)
                if info and info.get("title") == et:
                    rarity = info.get("rarity")
        except Exception:
            rarity = None
        tc = self.title_system.get_title_rarity_color(et, rarity)
        return f"{tc}[{et}]§r"

    def format_player_display_label_with_guild(
        self,
        raw_player_name: str,
        equipped_title: Optional[str],
        xuid: str,
    ) -> str:
        """
        展示用：按已注册前缀 priority 升序拼接（越小越靠前），最后为游戏名。
        内置 guild=2、title=3；其它插件注册的前缀取玩家已设置文本。
        与聊天、头顶 name_tag、死亡播报及 get_player_name_by_xuid(..., True) 保持一致。
        """
        name = (raw_player_name or "").strip() or "?"
        xs = str(xuid or "").strip()
        with self._chat_prefixes_lock:
            defs = [(n, dict(meta)) for n, meta in self._chat_prefixes.items()]
        with self._player_chat_prefixes_lock:
            player_map = dict(self._player_chat_prefixes.get(xs, {})) if xs else {}
        parts = []
        for pname, meta in defs:
            if pname == CHAT_PREFIX_GUILD:
                text = self._compute_guild_chat_prefix(xs)
            elif pname == CHAT_PREFIX_TITLE:
                text = self._compute_title_chat_prefix(xs, equipped_title)
            else:
                text = str(player_map.get(pname) or "")
            if not text:
                continue
            try:
                prio = int(meta.get("priority", 0))
            except (TypeError, ValueError):
                prio = 0
            if prio < 0:
                prio = 0
            parts.append((prio, pname, text))
        parts.sort(key=lambda item: (item[0], item[1]))
        return "".join(t[2] for t in parts) + name

    def _refresh_player_name_tag_by_xuid(self, xuid: Optional[str]) -> None:
        if not xuid:
            return
        p = self._find_online_player_by_xuid(str(xuid).strip())
        if p:
            self._update_player_name_tag(p)

    def get_player_xuid_by_name(self, player_name: str) -> Optional[str]:
        """
        通过玩家名称获取 XUID。
        使用在线玩家列表 + 大小写不敏感、去空白的 DB 查询，避免 name 与调用方字符串仅差大小写/空格时查不到。
        """
        if player_name is None:
            return None
        normalized_name = str(player_name).strip()
        if not normalized_name:
            return None
        try:
            # 1) 在线玩家优先：与运行时 player.name 一致，可规避 DB 未及时同步或第三方传入名与库不完全一致
            server = getattr(self, "server", None)
            if server is not None:
                try:
                    online_players = server.online_players
                    if online_players:
                        key_lower = normalized_name.lower()
                        for online_player in online_players:
                            on_name = (online_player.name or "").strip()
                            if on_name.lower() == key_lower:
                                return str(online_player.xuid)
                except Exception:
                    pass
            # 2) 数据库：TRIM + 大小写不敏感（SQLite 默认 BINARY 下 name = ? 对英文大小写敏感）
            result = self.database_manager.query_one(
                "SELECT xuid FROM player_basic_info WHERE LOWER(TRIM(name)) = LOWER(?)",
                (normalized_name,),
            )
            if not result or result.get("xuid") is None:
                return None
            return str(result["xuid"])
        except Exception as e:
            self.logger.error(f"{ColorFormat.RED}[ARC Core]Get player XUID by name error: {str(e)}")
            return None

    def _rank_display_name_from_row(self, row: Dict[str, Any]) -> str:
        """排行榜等展示用：row 含 xuid、name 时优先返回带头衔的展示名。"""
        xuid_s = str(row.get("xuid") or "").strip()
        raw = (row.get("name") or "?").strip() or "?"
        if not xuid_s:
            return raw
        labeled = self.get_player_name_by_xuid(xuid_s, return_with_title=True)
        return labeled if labeled else raw

    # Spawn protect
    def init_spawn_locations_table(self) -> bool:
        """初始化出生点表格"""
        fields = {
            'dimension': 'TEXT PRIMARY KEY',  # 维度名称作为主键
            'spawn_x': 'INTEGER NOT NULL',
            'spawn_y': 'INTEGER NOT NULL',
            'spawn_z': 'INTEGER NOT NULL'
        }
        ok = self.database_manager.create_table('spawn_locations', fields)
        migrate_spawn_locations_dimensions(self.database_manager)
        return ok

    def init_small_horn_orders_table(self) -> bool:
        """初始化小喇叭订单表。"""
        fields = {
            'id': 'INTEGER PRIMARY KEY AUTOINCREMENT',
            'xuid': 'TEXT NOT NULL',
            'content': 'TEXT NOT NULL',
            'start_time': 'TEXT NOT NULL',
            'valid_hours': 'INTEGER NOT NULL',
            'end_time': 'TEXT NOT NULL',
            'created_at': 'TEXT NOT NULL',
        }
        return self.database_manager.create_table('small_horn_orders', fields)

    def init_cross_server_table(self) -> bool:
        """初始化跨服传送目标表。"""
        fields = {
            'id': 'INTEGER PRIMARY KEY AUTOINCREMENT',
            'server_name': 'TEXT NOT NULL',
            'server_host': 'TEXT NOT NULL',
            'server_port': 'INTEGER NOT NULL',
            'created_at': 'TEXT NOT NULL',
        }
        return self.database_manager.create_table('cross_server_targets', fields)

    def update_spawn_location(self, dimension: str, coordinates: tuple) -> bool:
        """
        更新出生地信息
        :param db: 数据库管理器
        :param dimension: 维度名称
        :param coordinates: (x, y, z) 坐标元组
        :return: 是否更新成功
        """
        from endstone_arc_core.dimension_utils import normalize_dimension_id

        dimension = normalize_dimension_id(dimension)
        x, y, z = coordinates
        data = {
            'spawn_x': x,
            'spawn_y': y,
            'spawn_z': z
        }

        existing = self.database_manager.query_one("SELECT * FROM spawn_locations WHERE dimension = ?", (dimension,))

        if existing:
            return self.database_manager.update('spawn_locations', data, 'dimension = ?', (dimension,))
        else:
            data['dimension'] = dimension
            return self.database_manager.insert('spawn_locations', data)

    def get_all_spawn_locations(self) -> Dict[str, tuple]:
        """
        获取所有出生地信息
        :return: 字典，键为维度名称，值为坐标元组(x, y, z)
        """
        result = self.database_manager.query_all("SELECT * FROM spawn_locations")
        return {
            row['dimension']: (row['spawn_x'], row['spawn_y'], row['spawn_z'])
            for row in result
        }

    def spawn_protect_check(self, dimension_name: str, pos_x: float, pos_z: float) -> bool:
        spawn_xyz = self.spawn_pos_dict.get(dimension_name)
        if spawn_xyz is not None:
            if math.fabs(pos_x - spawn_xyz[0]) <= self.spawn_protect_range and \
                    math.fabs(pos_z - spawn_xyz[2]) <= self.spawn_protect_range:
                return False
        return True

    def _player_has_checked_in_today(self, player: Player) -> bool:
        today = self._today_checkin_date_str()
        self.init_player_local_info(player)
        row = self.database_manager.query_one(
            "SELECT last_checkin_date FROM player_local_info WHERE xuid = ?",
            (str(player.xuid),),
        )
        last_date = (row.get("last_checkin_date") if row else None) or ""
        return str(last_date).strip() == today

    def require_sensitive_password_verified(
        self,
        player: Player,
        on_verified: Callable[[Player], None],
        on_cancel: Optional[Callable[[Player], None]] = None,
    ) -> None:
        """转账、创建/管理领地等敏感操作前要求密码；本会话内验证一次即可。"""
        if self.player_sensitive_password_verified.get(player.name):
            on_verified(player)
            return
        player_basic_info = self.get_player_basic_info(player)
        if player_basic_info is None:
            self.report_arc_error(
                "AUTH0",
                f"require_sensitive_password_verified get_player_basic_info None player={player.name!r}",
                player,
            )
            return
        if not player_basic_info.get("password"):
            self._pending_sensitive_action_by_player[player.name] = {
                "on_verified": on_verified,
                "on_cancel": on_cancel or (lambda p: None),
            }
            hint = self.language_manager.GetText("SENSITIVE_ACTION_NEED_REGISTER_PASSWORD_HINT")
            self.show_register_panel(player, hint or None)
            return
        self.show_sensitive_password_verify_modal(player, on_verified, on_cancel=on_cancel)

    def show_sensitive_password_verify_modal(
        self,
        player: Player,
        on_verified: Callable[[Player], None],
        on_cancel: Optional[Callable[[Player], None]] = None,
    ) -> None:
        password_input = TextInput(
            label=self.language_manager.GetText("LOGIN_PANEL_PASSWORD_INPUT_LABEL"),
            placeholder=self.language_manager.GetText("LOGIN_PANEL_PASSWORD_INPUT_PLACEHOLDER"),
        )
        panel_title = self.language_manager.GetText("SENSITIVE_VERIFY_PANEL_TITLE")

        def try_verify(p: Player, json_str: str):
            data = json.loads(json_str)
            if len(data) < 2:
                p.send_message(
                    self.language_manager.GetText("SENSITIVE_VERIFY_FAIL_PASSWORD_NOT_INPUT")
                )
                self.show_sensitive_password_verify_modal(
                    p,
                    on_verified,
                    on_cancel=on_cancel,
                )
                return
            if self._modal_choice_is_back(data, 0):
                if on_cancel:
                    on_cancel(p)
                return
            pwd = (data[1] or "").strip()
            if not pwd:
                p.send_message(
                    self.language_manager.GetText("SENSITIVE_VERIFY_FAIL_PASSWORD_NOT_INPUT")
                )
                self.show_sensitive_password_verify_modal(
                    p,
                    on_verified,
                    on_cancel=on_cancel,
                )
                return
            if self.verify_player_password(p, pwd):
                self.player_sensitive_password_verified[p.name] = True
                on_verified(p)
            else:
                p.send_message(self.language_manager.GetText("SENSITIVE_VERIFY_FAIL_WRONG_PASSWORD"))
                self.show_sensitive_password_verify_modal(
                    p,
                    on_verified,
                    on_cancel=on_cancel,
                )

        verify_panel = ModalForm(
            title=panel_title,
            controls=[self._modal_nav_dropdown(), password_input],
            on_close=None,
            on_submit=try_verify,
        )
        player.send_form(verify_panel)

    def _on_register_panel_closed_for_sensitive(self, player: Player) -> None:
        pending = self._pending_sensitive_action_by_player.pop(player.name, None)
        if pending and pending.get("on_cancel"):
            pending["on_cancel"](player)

    def _modal_nav_dropdown(self) -> Dropdown:
        """Modal 内「返回上一级 / 继续」；默认继续。点窗口关闭仅关表单，不跳转。"""
        return Dropdown(
            label="",
            options=[
                self.language_manager.GetText("RETURN_BUTTON_TEXT"),
                "继续",
            ],
            default_index=1,
        )

    @staticmethod
    def _modal_choice_is_back(data: list, index: int = 0) -> bool:
        if not isinstance(data, list) or len(data) <= index:
            return False
        raw = data[index]
        try:
            return int(raw) == 0
        except (TypeError, ValueError):
            return str(raw).strip() in ("0", "false", "False")

    # UI Main menu
    def show_main_menu(self, player: Player):
        self.update_player_name(player)
        arc_menu = ActionForm(
            title=self.language_manager.GetText('MAIN_MENU_TITLE'),
        )
        for text, on_click in self._iter_main_menu_buttons_for_player(player):
            arc_menu.add_button(text, on_click=on_click)
        arc_menu.on_close = None
        player.send_form(arc_menu)

    def _put_main_menu_button(
        self,
        button_id: str,
        text,
        on_click,
        priority=6,
        visible=None,
    ) -> bool:
        """内部注册主菜单按钮。text/priority 可为常量或按玩家计算的 callable。"""
        bid = str(button_id or "").strip()
        if not bid or on_click is None or not callable(on_click):
            return False
        if text is None or (not callable(text) and not str(text)):
            return False
        entry = {
            "button_id": bid,
            "text": text,
            "on_click": on_click,
            "priority": priority,
            "visible": visible,
        }
        with self._main_menu_buttons_lock:
            self._main_menu_buttons[bid] = entry
        return True

    def _register_core_main_menu_buttons(self) -> None:
        """注册弧光核心自带主菜单按钮；优先级从 3 起，保持原相对顺序。"""
        lm = self.language_manager
        self._put_main_menu_button(
            "arc_core:checkin",
            text=lambda _p: lm.GetText("CHECKIN_MENU_BUTTON"),
            on_click=self.show_daily_checkin_panel,
            priority=lambda p: 0 if not self._player_has_checked_in_today(p) else 99,
        )
        self._put_main_menu_button(
            "arc_core:newbie",
            text=lambda _p: lm.GetText("NEWBIE_GUIDE_BUTTON"),
            on_click=self.show_newbie_welcome_panel,
            priority=3,
        )
        self._put_main_menu_button(
            "arc_core:teleport",
            text=lambda _p: lm.GetText("TELEPORT_MENU_NAME"),
            on_click=self.show_teleport_menu,
            priority=4,
            visible=lambda _p: self._teleport_menu_has_any_feature(),
        )
        self._put_main_menu_button(
            "arc_core:land",
            text=lambda _p: lm.GetText("LAND_MENU_NAME"),
            on_click=self.show_land_main_menu,
            priority=5,
            visible=lambda p: self._is_land_system_enabled() and (self._is_land_claim_allowed() or p.is_op),
        )
        self._put_main_menu_button(
            "arc_core:bank",
            text=lambda _p: lm.GetText("BANK_MENU_NAME"),
            on_click=self.show_bank_main_menu,
            priority=6,
        )
        self._put_main_menu_button(
            "arc_core:guild",
            text=lambda _p: lm.GetText("GUILD_MENU_NAME"),
            on_click=self.show_guild_main_menu,
            priority=7,
        )
        self._put_main_menu_button(
            "arc_core:tools",
            text=lambda _p: lm.GetText("MAIN_MENU_TOOLS_BUTTON"),
            on_click=self.show_arc_tools_menu,
            priority=8,
        )
        self._put_main_menu_button(
            "arc_core:op",
            text=lambda _p: lm.GetText("OP_PANEL_NAME"),
            on_click=self.show_op_main_panel,
            priority=50,
            visible=lambda p: bool(getattr(p, "is_op", False)),
        )

    def _iter_main_menu_buttons_for_player(self, player: Player):
        """按优先级升序（同优先级按按钮文本）产出 (text, on_click)。"""
        with self._main_menu_buttons_lock:
            entries = list(self._main_menu_buttons.values())
        resolved = []
        for entry in entries:
            visible = entry.get("visible")
            try:
                if visible is not None and not visible(player):
                    continue
            except Exception:
                continue
            text = entry.get("text")
            try:
                text = text(player) if callable(text) else text
            except Exception:
                continue
            text = str(text) if text is not None else ""
            if not text:
                continue
            priority = entry.get("priority", 6)
            try:
                priority = priority(player) if callable(priority) else priority
                priority = int(priority)
            except Exception:
                priority = 6
            on_click = entry.get("on_click")
            if on_click is None or not callable(on_click):
                continue
            resolved.append((priority, text, entry.get("button_id", ""), on_click))
        # 0 最高；同优先级按文本首字符（整串比较）再按 button_id 稳定排序
        resolved.sort(key=lambda item: (item[0], item[1], item[2]))
        for _priority, text, _bid, on_click in resolved:
            yield text, on_click

    def api_register_main_menu_button(
        self,
        button_id: str,
        text: str,
        on_click,
        priority: int = 6,
    ) -> bool:
        """供其它插件注册 ARC 主菜单按钮。priority 越小越靠前（0 最高）；同优先级按文本排序。"""
        try:
            return self._put_main_menu_button(
                button_id,
                text=str(text),
                on_click=on_click,
                priority=int(priority),
            )
        except Exception as e:
            try:
                if self.logger:
                    self.logger.error(f"[ARC Core]api_register_main_menu_button error: {e}")
            except Exception:
                pass
            return False

    def api_unregister_main_menu_button(self, button_id: str) -> bool:
        """注销其它插件（或自身）注册的主菜单按钮。"""
        bid = str(button_id or "").strip()
        if not bid:
            return False
        try:
            with self._main_menu_buttons_lock:
                if bid not in self._main_menu_buttons:
                    return False
                del self._main_menu_buttons[bid]
            return True
        except Exception:
            return False

    def api_register_chat_prefix(self, prefix_name: str, priority: int = 0) -> bool:
        """
        供其它插件注册聊天/展示名前缀槽位。
        prefix_name：前缀名（如 \"vip\"）；priority 越小越靠前（最低 0）；同名覆盖。
        内置：guild=2、title=3。注册后需再调 api_set_player_chat_prefix 写入玩家文本。
        """
        try:
            return self._put_chat_prefix(prefix_name, priority)
        except Exception as e:
            try:
                if self.logger:
                    self.logger.error(f"[ARC Core]api_register_chat_prefix error: {e}")
            except Exception:
                pass
            return False

    def api_set_player_chat_prefix(
        self,
        prefix_name: str,
        text: str,
        player_name: str = "",
        xuid: str = "",
    ) -> bool:
        """
        设置玩家某已注册前缀的展示文本（含颜色码）。text 为空则清除。
        player_name / xuid 填一个即可，xuid 优先。不可设置内置 guild / title（由核心实时计算）。
        成功后若玩家在线则刷新 name_tag。
        """
        pname = str(prefix_name or "").strip()
        if not pname:
            return False
        if pname in (CHAT_PREFIX_GUILD, CHAT_PREFIX_TITLE):
            return False
        with self._chat_prefixes_lock:
            if pname not in self._chat_prefixes:
                return False
        resolved = self._api_resolve_player_xuid(player_name, xuid)
        if not resolved:
            return False
        display = str(text or "")
        try:
            with self._player_chat_prefixes_lock:
                slot = self._player_chat_prefixes.setdefault(resolved, {})
                if not display:
                    slot.pop(pname, None)
                    if not slot:
                        self._player_chat_prefixes.pop(resolved, None)
                else:
                    slot[pname] = display
            self._refresh_player_name_tag_by_xuid(resolved)
            return True
        except Exception as e:
            try:
                if self.logger:
                    self.logger.error(f"[ARC Core]api_set_player_chat_prefix error: {e}")
            except Exception:
                pass
            return False

    def show_arc_tools_menu(self, player: Player):
        """我的信息、小喇叭、重生等快捷功能入口。"""
        tools_form = ActionForm(
            title=self.language_manager.GetText("MAIN_MENU_TOOLS_TITLE"),
            content=self.language_manager.GetText("MAIN_MENU_TOOLS_CONTENT"),
            on_close=None,
        )
        tools_form.add_button(
            self.language_manager.GetText("MAIN_MENU_MY_INFO_NAME"),
            on_click=self.show_my_info_panel,
        )
        tools_form.add_button(
            self.language_manager.GetText("SMALL_HORN_MENU_BUTTON"),
            on_click=lambda p: self.show_small_horn_buy_panel(p, on_panel_close=self.show_arc_tools_menu),
        )
        tools_form.add_button(self.language_manager.GetText("SUICIDE_FUNC_BUTTON"), on_click=self.execute_suicide)
        tools_form.add_button(self.language_manager.GetText("RETURN_BUTTON_TEXT"), on_click=self.show_main_menu)
        player.send_form(tools_form)

    def execute_suicide(self, player: Player):
        player.perform_command('suicide')

    def show_newbie_welcome_panel(self, player: Player):
        """显示新手引导面板，内容来自内存中的 newbie_welcome.txt"""
        welcome_content = self.newbie_welcome_text or ""
        if not str(welcome_content).strip():
            welcome_content = (
                self.language_manager.GetText("NEWBIE_GUIDE_PANEL_TITLE")
                + "\n\n（引导文件暂未配置）"
            )
        newbie_form = ActionForm(
            title=self.language_manager.GetText('NEWBIE_GUIDE_PANEL_TITLE'),
            content=welcome_content,
            on_close=None,
        )
        newbie_form.add_button(
            self.language_manager.GetText('RETURN_BUTTON_TEXT'),
            on_click=self.show_main_menu
        )
        player.send_form(newbie_form)

    # Player info & invite system UI
    def show_my_info_panel(self, player: Player):
        """显示玩家自己的信息面板"""
        player_basic_info = self.get_player_basic_info(player)
        if player_basic_info is None:
            self.report_arc_error(
                "INFO1",
                f"show_my_info_panel get_player_basic_info returned None player={player.name!r}",
                player,
            )
            return

        player_name = player.name
        player_xuid = str(player.xuid)
        player_money = self.get_player_money(player)
        player_land_count = self.get_player_land_count(player_xuid)
        remaining_free_blocks = self.get_player_free_land_blocks(player)

        inviter_xuid = player_basic_info.get('inviter_xuid')
        if inviter_xuid:
            inviter_name = self.get_player_name_by_xuid(inviter_xuid) or inviter_xuid
        else:
            inviter_name = self.language_manager.GetText('INVITER_NONE_TEXT')

        pending_info = self.database_manager.query_one(
            "SELECT pending_invite_reward_times FROM player_basic_info WHERE xuid = ?",
            (player_xuid,)
        )
        pending_times = 0
        if pending_info is not None:
            try:
                pending_times = int(pending_info.get('pending_invite_reward_times', 0) or 0)
            except (ValueError, TypeError):
                pending_times = 0

        info_content = self.language_manager.GetText('MY_INFO_PANEL_CONTENT').format(
            player_name,
            player_xuid,
            self._format_money_display(player_money),
            player_land_count,
            remaining_free_blocks,
            inviter_name,
            pending_times
        )

        my_info_panel = ActionForm(
            title=self.language_manager.GetText('MY_INFO_PANEL_TITLE'),
            content=info_content,
            on_close=None,
        )

        # 未填写邀请人时显示“填写邀请人”按钮
        if not inviter_xuid:
            my_info_panel.add_button(
                self.language_manager.GetText('MY_INFO_FILL_INVITER_BUTTON'),
                on_click=self.show_fill_inviter_panel
            )

        # 有待领取邀请奖励时显示“领取邀请奖励”按钮
        if pending_times > 0:
            my_info_panel.add_button(
                self.language_manager.GetText('MY_INFO_CLAIM_INVITE_REWARD_BUTTON'),
                on_click=self.claim_invite_rewards
            )

        # 头衔管理
        my_info_panel.add_button(
            self.language_manager.GetText('TITLE_MANAGE_BUTTON'),
            on_click=self.show_title_manage_panel
        )

        # 我的成就（独立插件 arc_achievement）
        if self.server.plugin_manager.get_plugin('arc_achievement'):
            my_info_panel.add_button(
                self.language_manager.GetText('MY_ACHIEVEMENTS_BUTTON'),
                on_click=self.show_arc_achievement_menu,
            )

        my_info_panel.add_button(
            self.language_manager.GetText('CHANGE_PASSWORD_BUTTON'),
            on_click=self.show_change_password_panel,
        )

        # 返回工具菜单
        my_info_panel.add_button(
            self.language_manager.GetText('RETURN_BUTTON_TEXT'),
            on_click=self.show_arc_tools_menu,
        )

        player.send_form(my_info_panel)


    def show_title_manage_panel(self, player: Player):
        """头衔管理：选择佩戴的头衔或取消佩戴"""
        unlocked = self.title_system.get_unlocked_title_entries(player)
        equipped_entry = self.title_system.get_equipped_title_entry(player)
        equipped = equipped_entry.get("title") if equipped_entry else None
        equipped_rarity = equipped_entry.get("rarity") if equipped_entry else None
        if equipped and equipped_rarity:
            equipped_display = f"{equipped}（{equipped_rarity}）"
        else:
            equipped_display = equipped if equipped else self.language_manager.GetText('TITLE_NONE')
        content = self.language_manager.GetText('TITLE_MANAGE_CONTENT').format(equipped_display)
        panel = ActionForm(
            title=self.language_manager.GetText('TITLE_MANAGE_TITLE'),
            content=content,
            on_close=None,
        )
        panel.add_button(
            self.language_manager.GetText('TITLE_UNEQUIP_BUTTON'),
            on_click=lambda p: self._title_set_equipped_and_back(p, None),
        )
        for entry in unlocked:
            t = entry.get("title") or ""
            r = entry.get("rarity") or DEFAULT_RARITY
            equipped_match = bool(
                equipped_entry
                and equipped_entry.get("title") == t
                and equipped_entry.get("rarity") == r
            )
            label = self._format_title_button_label(t, equipped_match, r)
            panel.add_button(
                label,
                on_click=lambda pl, title=t, rarity=r: self._title_set_equipped_and_back(
                    pl, title, rarity
                ),
            )
        panel.add_button(
            self.language_manager.GetText('RETURN_BUTTON_TEXT'),
            on_click=self.show_my_info_panel,
        )
        player.send_form(panel)

    def _format_title_button_label(
        self, title_name: str, is_equipped: bool, rarity: Optional[str] = None
    ) -> str:
        """头衔按钮两行显示：第一行名称（按稀有度颜色+加粗），第二行介绍（若有）。"""
        color = self.title_system.get_title_rarity_color(title_name, rarity)
        defn = self.title_system.get_title_definition(title_name, rarity)
        desc = (defn.get("description") or "").strip() if defn else ""
        rarity_s = (defn.get("rarity") if defn else None) or rarity or DEFAULT_RARITY
        prefix = (
            ("§a" + self.language_manager.GetText('TITLE_EQUIPPED_CURRENT').format("") + "§r ")
            if is_equipped
            else ""
        )
        line1 = prefix + "§l" + color + title_name + "§r §7(" + str(rarity_s) + ")§r"
        if desc:
            return line1 + "\n§r§f" + desc
        return line1

    def _auto_equip_highest_title_if_needed(self, player: Player) -> None:
        """进服一次：若未标记完成且当前未佩戴，则佩戴已解锁中稀有度最高的头衔并写标记；已有佩戴则仅同步标记。"""
        try:
            player_xuid = str(player.xuid)
            row = self.database_manager.query_one(
                "SELECT default_title_auto_equipped FROM player_basic_info WHERE xuid = ?",
                (player_xuid,),
            )
            if row is None:
                return
            try:
                flag = int(row.get("default_title_auto_equipped", 0) or 0)
            except (ValueError, TypeError):
                flag = 0
            if flag != 0:
                return
            equipped = self.title_system.get_equipped_title(player)
            if equipped:
                self.database_manager.update(
                    table="player_basic_info",
                    data={"default_title_auto_equipped": 1},
                    where="xuid = ?",
                    params=(player_xuid,),
                )
                return
            unlocked = self.title_system.get_unlocked_title_entries(player)
            if not unlocked:
                return
            best = self.title_system.pick_highest_rarity_entry(unlocked)
            if not best:
                return
            if not self.title_system.set_equipped_title(
                player, best["title"], best["rarity"]
            ):
                return
            self.database_manager.update(
                table="player_basic_info",
                data={"default_title_auto_equipped": 1},
                where="xuid = ?",
                params=(player_xuid,),
            )
        except Exception as e:
            if self.logger:
                self.logger.error(f"{ColorFormat.RED}[ARC Core]Auto equip title on join error: {str(e)}")

    def _update_player_name_tag(self, player: Player) -> None:
        """将玩家 name_tag 设为 [公会][头衔]名字（与聊天、接口展示一致）。"""
        try:
            equipped = self.title_system.get_equipped_title(player)
            name = player.name or ""
            xuid = str(player.xuid)
            player.name_tag = self.format_player_display_label_with_guild(
                name, equipped, xuid
            )
        except Exception as e:
            if self.logger:
                self.logger.error(f"[ARC Core]Update player name_tag error: {str(e)}")

    def _title_set_equipped_and_back(
        self, player: Player, title: Optional[str], rarity: Optional[str] = None
    ):
        if title is None:
            self.title_system.set_equipped_title(player, None)
        else:
            self.title_system.set_equipped_title(player, title, rarity)
        self._update_player_name_tag(player)
        self.show_title_manage_panel(player)

    def api_unlock_title(
        self, player: Player, title: str, rarity: str = DEFAULT_RARITY
    ) -> bool:
        """供其他插件调用：解锁指定名称+稀有度的头衔（不发奖）。"""
        if player is None:
            return False
        title_s = str(title or "").strip()
        if not title_s:
            return False
        rarity_s = str(rarity or DEFAULT_RARITY).strip() or DEFAULT_RARITY
        equipped_before = None
        try:
            equipped_before = self.title_system.get_equipped_title(player)
        except Exception:
            equipped_before = None
        unlock_ok, was_new_unlock = self.title_system.unlock_title(
            player, title_s, rarity=rarity_s
        )
        if not unlock_ok:
            return False
        if was_new_unlock and not equipped_before:
            try:
                self.title_system.set_equipped_title(player, title_s, rarity_s)
                self._update_player_name_tag(player)
            except Exception:
                pass
        return True

    def api_unlock_title_by_xuid(
        self, xuid: str, title: str, rarity: str = DEFAULT_RARITY
    ) -> bool:
        """按 xuid 解锁指定名称+稀有度头衔；在线且新解锁时未佩戴则自动佩戴（不发奖）。"""
        xuid_s = str(xuid or "").strip()
        title_s = str(title or "").strip()
        if not xuid_s or not title_s:
            return False
        rarity_s = str(rarity or DEFAULT_RARITY).strip() or DEFAULT_RARITY
        online = self._find_online_player_by_xuid(xuid_s)
        equipped_before = None
        if online is not None:
            try:
                equipped_before = self.title_system.get_equipped_title(online)
            except Exception:
                equipped_before = None
        unlock_ok, was_new_unlock = self.title_system.unlock_title_by_xuid(
            xuid_s, title_s, rarity=rarity_s
        )
        if not unlock_ok:
            return False
        if was_new_unlock and online is not None and not equipped_before:
            try:
                self.title_system.set_equipped_title(online, title_s, rarity_s)
                self._update_player_name_tag(online)
            except Exception:
                pass
        return True

    def api_set_title_definition(
        self,
        title: str,
        rarity: str,
        description: str,
        reward_money: float = 0.0,
        reward_items: Optional[List] = None,
    ) -> bool:
        """创建或覆盖指定 (title, rarity) 的头衔定义。reward_* 兼容旧调用，解锁不发奖。"""
        items = reward_items if reward_items is not None else []
        if not isinstance(items, list):
            return False
        try:
            money = float(reward_money or 0.0)
        except (TypeError, ValueError):
            return False
        return bool(
            self.title_system.set_title_definition(
                str(title or "").strip(),
                str(rarity or DEFAULT_RARITY).strip() or DEFAULT_RARITY,
                str(description or ""),
                money,
                items,
            )
        )

    def api_ensure_title_definition(
        self,
        title: str,
        rarity: str = DEFAULT_RARITY,
        description: str = "",
        reward_money: float = 0.0,
        reward_items: Optional[List] = None,
    ) -> bool:
        """若 (title, rarity) 未注册则写入基本属性；已存在同名同稀有度不覆盖。"""
        items = reward_items if reward_items is not None else []
        if not isinstance(items, list):
            return False
        try:
            money = float(reward_money or 0.0)
        except (TypeError, ValueError):
            return False
        return bool(
            self.title_system.ensure_title_definition(
                str(title or "").strip(),
                str(rarity or DEFAULT_RARITY).strip() or DEFAULT_RARITY,
                str(description or ""),
                money,
                items,
            )
        )

    def api_get_title_definition(
        self, title: str, rarity: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """获取头衔定义。rarity 给定则精确匹配；省略时同名仅一条则返回，多条优先普通。"""
        title_s = str(title or "").strip()
        if not title_s:
            return None
        return self.title_system.get_title_definition(title_s, rarity)

    def api_has_title_definition(
        self, title: str, rarity: str = DEFAULT_RARITY
    ) -> bool:
        """是否已注册指定名称+稀有度的头衔。"""
        title_s = str(title or "").strip()
        if not title_s:
            return False
        rarity_s = str(rarity or DEFAULT_RARITY).strip() or DEFAULT_RARITY
        return bool(self.title_system.has_title_definition(title_s, rarity_s))

    def api_list_title_definitions(self) -> list:
        """全部头衔定义列表（含同名不同稀有度）。"""
        try:
            return list(self.title_system.get_all_title_entries())
        except Exception:
            out = []
            for name in self.title_system.get_all_title_names():
                title_s = str(name or "").strip()
                if not title_s:
                    continue
                defn = self.title_system.get_title_definition(title_s)
                if defn:
                    out.append(defn)
                else:
                    out.append({
                        "title": title_s,
                        "rarity": DEFAULT_RARITY,
                        "description": "",
                        "reward_money": 0.0,
                        "reward_items": [],
                    })
            return out

    def api_has_unlocked_title(
        self,
        title: str,
        *,
        player: Optional[Player] = None,
        player_name: str = "",
        xuid: str = "",
        rarity: Optional[str] = None,
    ) -> bool:
        """查询玩家是否已解锁指定头衔。rarity 给定则精确匹配名称+稀有度。"""
        title_s = str(title or "").strip()
        if not title_s:
            return False
        resolved = str(xuid or "").strip()
        if not resolved and player is not None:
            try:
                resolved = str(player.xuid or "").strip()
            except Exception:
                resolved = ""
        if not resolved:
            resolved = self._api_resolve_player_xuid(player_name, "") or ""
        if not resolved:
            return False
        return bool(
            self.title_system.has_unlocked_title_by_xuid(resolved, title_s, rarity)
        )

    def api_get_player_xuid_by_name(self, player_name: str) -> Optional[str]:
        """供其他插件调用：按玩家名解析 XUID（在线优先，其次数据库，大小写不敏感）。"""
        return self.get_player_xuid_by_name(player_name)

    def api_give_player_items(
        self,
        player: Optional[Player] = None,
        items: Optional[List] = None,
        player_name: str = "",
        xuid: str = "",
    ) -> bool:
        """给在线玩家发放物品。可用 Player，或 player_name / xuid（须在线）。

        每项为 dict：item_name 或 id，以及 count。任一有效条目成功发出即计为部分成功。
        """
        if player is None:
            resolved = self._api_resolve_player_xuid(player_name, xuid)
            player = self._find_online_player_by_xuid(resolved) if resolved else None
        if player is None or not items:
            return False
        if not isinstance(items, list):
            return False
        any_ok = False
        for it in items:
            if not isinstance(it, dict):
                continue
            item_name = str(it.get("item_name") or it.get("id") or "").strip()
            try:
                count = int(it.get("count", 1))
            except (TypeError, ValueError):
                continue
            if not item_name or count <= 0:
                continue
            try:
                self.server.dispatch_command(
                    self.server.command_sender,
                    f"give {format_mc_command_player_name(player.name)} {item_name} {count}",
                )
                any_ok = True
            except Exception:
                pass
        return any_ok

    def api_get_newbie_guide_text(self) -> str:
        """供其他插件调用：返回新手引导文本全文（与内存中的 newbie_welcome.txt、主菜单新手引导一致）。"""
        return str(self.newbie_welcome_text or "").strip()

    def _landmark_dim_label(self, dimension: str) -> str:
        dim = str(dimension or "").strip() or "-"
        try:
            label = translate_dimension_display(dim, self.language_manager)
        except Exception:
            label = dim
        return str(label or dim)

    @staticmethod
    def _landmark_xyz(x, y, z) -> str:
        try:
            return f"{float(x):.0f}, {float(y):.0f}, {float(z):.0f}"
        except (TypeError, ValueError):
            return "?, ?, ?"

    def api_list_spawn_locations(self) -> list:
        """各维度出生点。每项 dimension / display / x / y / z。"""
        out = []
        try:
            mapping = self.get_all_spawn_locations() or {}
        except Exception:
            mapping = dict(getattr(self, "spawn_pos_dict", None) or {})
        for dim, coords in mapping.items():
            if not coords or len(coords) < 3:
                continue
            out.append(
                {
                    "dimension": str(dim),
                    "display": self._landmark_dim_label(str(dim)),
                    "x": coords[0],
                    "y": coords[1],
                    "z": coords[2],
                }
            )
        return out

    def api_list_public_lands(self, limit: int = 50) -> list:
        """公共领地/功能区摘要（名称与传送点），供 AI 指路。"""
        cap = max(1, min(int(limit or 50), 80))
        out = []
        try:
            lands = self.get_all_lands() or {}
        except Exception:
            return []
        for land_id, info in lands.items():
            if not isinstance(info, dict):
                continue
            owner = str(info.get("owner_xuid") or "")
            if not self.land_system.is_public_land_owner(owner):
                continue
            out.append(
                {
                    "land_id": int(land_id),
                    "land_name": str(info.get("land_name") or f"#{land_id}"),
                    "dimension": str(info.get("dimension") or ""),
                    "display": self._landmark_dim_label(str(info.get("dimension") or "")),
                    "x": info.get("tp_x"),
                    "y": info.get("tp_y"),
                    "z": info.get("tp_z"),
                }
            )
            if len(out) >= cap:
                break
        return out

    def api_get_server_landmarks_text(self) -> str:
        """给 AI 的本服地标说明：出生点、公共传送点、公共领地。"""
        lines = ["本服公开地标（以服务器当前数据为准，不要编造清单外的地点）："]
        spawns = self.api_list_spawn_locations()
        if spawns:
            lines.append("【出生点】玩家可用 /spawn 传送到当前维度出生点。")
            for item in spawns:
                lines.append(
                    f"• {item.get('display') or item.get('dimension')} "
                    f"({self._landmark_xyz(item.get('x'), item.get('y'), item.get('z'))})"
                )
        else:
            lines.append("【出生点】尚未用 /updatespawnpos 设置自定义出生点。")

        warps = []
        try:
            warps = self.api_list_public_warps() or []
        except Exception:
            warps = []
        warp_cost = 0
        try:
            warp_cost = int(getattr(self.teleport_system, "teleport_cost_public_warp", 0) or 0)
        except (TypeError, ValueError):
            warp_cost = 0
        cost_hint = f"（传送费用 {warp_cost}）" if warp_cost > 0 else "（免费）"
        if warps:
            lines.append(f"【公共传送点 / Warp】游戏内 /arc tp 打开传送菜单{cost_hint}。")
            for item in warps:
                name = item.get("warp_name") or "?"
                dim = item.get("dimension") or ""
                lines.append(
                    f"• {name} — {self._landmark_dim_label(str(dim))} "
                    f"({self._landmark_xyz(item.get('x'), item.get('y'), item.get('z'))})"
                )
        else:
            lines.append("【公共传送点 / Warp】当前没有公共传送点。")

        public_lands = self.api_list_public_lands(50)
        if public_lands:
            lines.append("【公共领地 / 功能区】名称即常见地标或功能建筑。")
            for item in public_lands:
                name = item.get("land_name") or f"#{item.get('land_id')}"
                lines.append(
                    f"• {name} — {item.get('display') or item.get('dimension')} "
                    f"传送点 ({self._landmark_xyz(item.get('x'), item.get('y'), item.get('z'))})"
                )
        return "\n".join(lines)

    def _grant_title_unlock_reward(self, player: Player, title: str) -> None:
        """已废弃：头衔解锁不再发放金钱/物品（成就奖励改由 arc_achievement 发放）。"""
        _ = player
        _ = title
        return

    def show_fill_inviter_panel(self, player: Player, hint_message: Optional[str] = None):
        """显示填写邀请人面板"""
        panel_title = self.language_manager.GetText('FILL_INVITER_PANEL_TITLE') if hint_message is None else hint_message

        inviter_input = TextInput(
            label=self.language_manager.GetText('FILL_INVITER_INPUT_LABEL'),
            placeholder=self.language_manager.GetText('FILL_INVITER_INPUT_PLACEHOLDER')
        )

        def try_set_inviter(player: Player, json_str: str):
            try:
                data = json.loads(json_str)
            except Exception as parse_exc:
                self.report_arc_error(
                    "INV1",
                    "fill_inviter json.loads failed",
                    player,
                    exception=parse_exc,
                )
                self.show_fill_inviter_panel(player, self.language_manager.GetText('FILL_INVITER_FAIL_SYSTEM_ERROR'))
                return

            if len(data) < 2:
                self.show_fill_inviter_panel(player, self.language_manager.GetText('FILL_INVITER_FAIL_PLAYER_NOT_FOUND'))
                return

            if self._modal_choice_is_back(data, 0):
                self.show_my_info_panel(player)
                return

            if not str(data[1]).strip():
                self.show_fill_inviter_panel(player, self.language_manager.GetText('FILL_INVITER_FAIL_PLAYER_NOT_FOUND'))
                return

            inviter_name_input = str(data[1]).strip()
            player_xuid = str(player.xuid)

            # 再次检查自己是否已经填写过邀请人
            basic_info = self.database_manager.query_one(
                "SELECT inviter_xuid FROM player_basic_info WHERE xuid = ?",
                (player_xuid,)
            )
            if basic_info is None:
                self.report_arc_error(
                    "INV2",
                    f"fill_inviter SELECT inviter_xuid returned None xuid={player_xuid!r}",
                    player,
                )
                self.show_my_info_panel(player)
                return

            if basic_info.get('inviter_xuid'):
                player.send_message(self.language_manager.GetText('FILL_INVITER_FAIL_ALREADY_HAS_INVITER'))
                self.show_my_info_panel(player)
                return

            inviter_xuid = self.get_player_xuid_by_name(inviter_name_input)
            if not inviter_xuid:
                self.show_fill_inviter_panel(player, self.language_manager.GetText('FILL_INVITER_FAIL_PLAYER_NOT_FOUND'))
                return

            if inviter_xuid == player_xuid:
                self.show_fill_inviter_panel(player, self.language_manager.GetText('FILL_INVITER_FAIL_CANNOT_INVITE_SELF'))
                return

            # 写入邀请人信息
            try:
                update_success = self.database_manager.update(
                    table='player_basic_info',
                    data={'inviter_xuid': inviter_xuid},
                    where='xuid = ?',
                    params=(player_xuid,)
                )
            except Exception:
                update_success = False

            if not update_success:
                self.report_arc_error(
                    "INV3",
                    f"fill_inviter UPDATE inviter_xuid failed xuid={player_xuid!r}",
                    player,
                )
                self.show_my_info_panel(player)
                return

            # 给自己发放一次邀请奖励
            self.grant_invite_reward_to_player(player, 1)

            # 给邀请人累加一份待领取奖励
            self.add_pending_invite_rewards(inviter_xuid, 1)

            self._notify_important(
                player,
                self.language_manager.GetText('FILL_INVITER_SUBMIT_SUCCESS').format(inviter_name_input),
                title=self._toast_title("INVITE_TOAST_TITLE", "邀请奖励"),
            )

            inviter_player = self.server.get_player(inviter_name_input)
            if inviter_player is not None:
                self._notify_important(
                    inviter_player,
                    self.language_manager.GetText('INVITE_REWARD_GIVE_INVITER_HINT').format(player.name),
                    title=self._toast_title("INVITE_TOAST_TITLE", "邀请奖励"),
                )

            self.show_my_info_panel(player)

        fill_inviter_panel = ModalForm(
            title=panel_title,
            controls=[self._modal_nav_dropdown(), inviter_input],
            on_close=None,
            on_submit=try_set_inviter
        )
        player.send_form(fill_inviter_panel)

    def claim_invite_rewards(self, player: Player):
        """领取玩家待领取的邀请奖励"""
        player_xuid = str(player.xuid)
        pending_info = self.database_manager.query_one(
            "SELECT pending_invite_reward_times FROM player_basic_info WHERE xuid = ?",
            (player_xuid,)
        )

        pending_times = 0
        if pending_info is not None:
            try:
                pending_times = int(pending_info.get('pending_invite_reward_times', 0) or 0)
            except (ValueError, TypeError):
                pending_times = 0

        if pending_times <= 0:
            no_reward_panel = ActionForm(
                title=self.language_manager.GetText('INVITE_REWARD_CLAIM_RESULT_TITLE'),
                content=self.language_manager.GetText('INVITE_REWARD_CLAIM_NOTHING'),
                on_close=None,
            )
            player.send_form(no_reward_panel)
            return

        # 发放奖励（按照累计次数一次性发放）
        self.grant_invite_reward_to_player(player, pending_times)

        # 清零数据库中的待领取次数
        try:
            self.database_manager.update(
                table='player_basic_info',
                data={'pending_invite_reward_times': 0},
                where='xuid = ?',
                params=(player_xuid,)
            )
        except Exception as e:
            self.logger.error(f"{ColorFormat.RED}[ARC Core]Clear pending invite reward times error: {str(e)}")

        result_content = self.language_manager.GetText('INVITE_REWARD_CLAIM_RESULT_CONTENT').format(pending_times)
        result_panel = ActionForm(
            title=self.language_manager.GetText('INVITE_REWARD_CLAIM_RESULT_TITLE'),
            content=result_content,
            on_close=None,
        )
        player.send_form(result_panel)

    def show_change_password_panel(self, player: Player) -> None:
        """我的信息入口：已设密码则验证旧密码后修改；未设密码则走注册设密流程并回到我的信息。"""
        player_basic_info = self.get_player_basic_info(player)
        if player_basic_info is None:
            self.report_arc_error(
                "PWD1",
                f"show_change_password_panel get_player_basic_info returned None player={player.name!r}",
                player,
            )
            return
        if not player_basic_info.get("password"):
            hint = self.language_manager.GetText("CHANGE_PASSWORD_NEED_REGISTER_HINT")
            self.show_register_panel(
                player,
                hint or self.language_manager.GetText("REGISTER_PANEL_TITLE"),
                on_success_no_pending=self.show_my_info_panel,
                on_close_after_sensitive=self.show_my_info_panel,
            )
            return

        old_password_input = TextInput(
            label=self.language_manager.GetText("CHANGE_PASSWORD_OLD_LABEL"),
            placeholder=self.language_manager.GetText("CHANGE_PASSWORD_OLD_PLACEHOLDER"),
        )
        new_password_input = TextInput(
            label=self.language_manager.GetText("CHANGE_PASSWORD_NEW_LABEL"),
            placeholder=self.language_manager.GetText("CHANGE_PASSWORD_NEW_PLACEHOLDER"),
        )
        confirm_password_input = TextInput(
            label=self.language_manager.GetText("CHANGE_PASSWORD_CONFIRM_LABEL"),
            placeholder=self.language_manager.GetText("CHANGE_PASSWORD_CONFIRM_PLACEHOLDER"),
        )
        panel_title = self.language_manager.GetText("CHANGE_PASSWORD_PANEL_TITLE")

        def try_change_password(p: Player, json_str: str) -> None:
            data = json.loads(json_str)
            if len(data) < 4:
                p.send_message(self.language_manager.GetText("CHANGE_PASSWORD_FAIL_INCOMPLETE"))
                self.show_change_password_panel(p)
                return
            if self._modal_choice_is_back(data, 0):
                self.show_my_info_panel(p)
                return
            old_password = (data[1] or "").strip()
            new_password = (data[2] or "").strip()
            confirm_password = (data[3] or "").strip()
            if not old_password:
                p.send_message(self.language_manager.GetText("CHANGE_PASSWORD_FAIL_OLD_EMPTY"))
                self.show_change_password_panel(p)
                return
            if not self.verify_player_password(p, old_password):
                p.send_message(self.language_manager.GetText("CHANGE_PASSWORD_FAIL_OLD_WRONG"))
                self.show_change_password_panel(p)
                return
            if not new_password:
                p.send_message(self.language_manager.GetText("CHANGE_PASSWORD_FAIL_NEW_EMPTY"))
                self.show_change_password_panel(p)
                return
            if new_password != confirm_password:
                p.send_message(self.language_manager.GetText("CHANGE_PASSWORD_FAIL_MISMATCH"))
                self.show_change_password_panel(p)
                return
            if new_password == old_password:
                p.send_message(self.language_manager.GetText("CHANGE_PASSWORD_FAIL_SAME_AS_OLD"))
                self.show_change_password_panel(p)
                return
            if self.set_player_password(p, new_password):
                self.player_sensitive_password_verified.pop(p.name, None)
                p.send_message(self.language_manager.GetText("CHANGE_PASSWORD_SUCCESS"))
                self.show_my_info_panel(p)
            else:
                p.send_message(self.language_manager.GetText("REGISTER_FAIL"))
                self.show_change_password_panel(p)

        change_panel = ModalForm(
            title=panel_title,
            controls=[
                self._modal_nav_dropdown(),
                old_password_input,
                new_password_input,
                confirm_password_input,
            ],
            on_close=None,
            on_submit=try_change_password,
        )
        player.send_form(change_panel)

    # Register and sensitive password (ARC 主菜单不再要求登录)
    def show_register_panel(
        self,
        player: Player,
        hint_message=None,
        *,
        on_success_no_pending: Optional[Callable[[Player], None]] = None,
        on_close_after_sensitive: Optional[Callable[[Player], None]] = None,
    ):
        password_input = TextInput(
            label=self.language_manager.GetText('REGISTER_PANEL_PASSWORD_INPUT_LABEL'),
            placeholder=self.language_manager.GetText('REGISTER_PANEL_PASSWORD_INPUT_PLACEHOLDER')
        )
        confirm_password_input = TextInput(
            label=self.language_manager.GetText('REGISTER_PANEL_CONFIRM_PASSWORD_LABEL'),
            placeholder=self.language_manager.GetText('REGISTER_PANEL_CONFIRM_PASSWORD_PLACEHOLDER')
        )
        panel_title = self.language_manager.GetText('REGISTER_PANEL_TITLE') if hint_message is None else hint_message

        def register_panel_on_close(p: Player) -> None:
            self._on_register_panel_closed_for_sensitive(p)

        def try_register(player: Player, json_str: str):
            data = json.loads(json_str)
            if len(data) < 3:
                self.show_register_panel(
                    player,
                    self.language_manager.GetText('REGISTER_FAIL_PASSWORD_NOT_INPUT'),
                    on_success_no_pending=on_success_no_pending,
                    on_close_after_sensitive=on_close_after_sensitive,
                )
                return
            if self._modal_choice_is_back(data, 0):
                self._on_register_panel_closed_for_sensitive(player)
                if on_close_after_sensitive:
                    on_close_after_sensitive(player)
                return
            password = data[1]
            confirm_password = data[2]
            if not password:
                self.show_register_panel(
                    player,
                    self.language_manager.GetText('REGISTER_FAIL_PASSWORD_NOT_INPUT'),
                    on_success_no_pending=on_success_no_pending,
                    on_close_after_sensitive=on_close_after_sensitive,
                )
                return
            if password != confirm_password:
                self.show_register_panel(
                    player,
                    self.language_manager.GetText('REGISTER_FAIL_PASSWORD_MISMATCH'),
                    on_success_no_pending=on_success_no_pending,
                    on_close_after_sensitive=on_close_after_sensitive,
                )
                return
            r = self.set_player_password(player, password)
            if r:
                self._notify_important(
                    player,
                    self.language_manager.GetText('REGISTER_SUCCESS'),
                    title=self._toast_title("ACCOUNT_TOAST_TITLE", "账户"),
                )
                pending = self._pending_sensitive_action_by_player.pop(player.name, None)
                self.player_sensitive_password_verified[player.name] = True
                if pending and pending.get("on_verified"):
                    pending["on_verified"](player)
                elif on_success_no_pending:
                    on_success_no_pending(player)
                else:
                    self.show_main_menu(player)
            else:
                player.send_message(self.language_manager.GetText('REGISTER_FAIL'))

        register_panel = ModalForm(
            title=panel_title,
            controls=[self._modal_nav_dropdown(), password_input, confirm_password_input],
            on_close=register_panel_on_close,
            on_submit=try_register
        )
        player.send_form(register_panel)

    # Economy system（委托 Economy 模块，金钱以 float 存储，精确到分）
    def _round_money(self, value: float) -> float:
        return self.economy.round_money(value)

    def _format_money_display(self, value: float) -> str:
        return self.economy.format_money_display(value)

    def _set_player_money_by_name(self, player_name: str, amount: float) -> bool:
        player_xuid = self.get_player_xuid_by_name(player_name)
        if not player_xuid:
            if self.logger:
                self.logger.error(f"{ColorFormat.RED}[ARC Core]Player {player_name} not found")
            return False
        success = self.economy.set_player_money_by_xuid(player_xuid, amount)
        if success:
            try:
                self._update_richest_title_if_needed()
            except Exception:
                pass
        return success

    def _set_player_money(self, player: Player, amount: float) -> bool:
        return self._set_player_money_by_name(player.name, amount)

    def get_player_money_by_name(self, player_name: str) -> float:
        player_xuid = self.get_player_xuid_by_name(player_name)
        return self.economy.get_player_money_by_xuid(player_xuid) if player_xuid else 0.0

    def get_player_money(self, player: Player) -> float:
        return self.economy.get_player_money_by_xuid(str(player.xuid))

    def _toast_title(self, key: str, fallback: str) -> str:
        """取 toast 标题；按场景套用基岩字形到最前面（会替换旧字形）。"""
        text = self.language_manager.GetText(key)
        title = text if text and str(text).strip() else fallback
        title = str(title or "").strip()
        for glyph in bedrock_glyphs.KNOWN_TOAST_GLYPHS:
            if title.startswith(glyph):
                title = title[len(glyph) :].lstrip()
                break
        icon = bedrock_glyphs.TOAST_TITLE_ICONS.get(key) or ""
        if icon:
            title = f"{icon} {title}".strip()
        return title

    def _strip_arc_message_prefix(self, message: str) -> str:
        s = str(message or "").strip()
        if not s:
            return ""
        cleaned = re.sub(r"^(?:§.)*\[弧光核心\]\s*", "", s)
        return cleaned.strip() if cleaned else s

    def _send_toast(self, player: Player, title: str, content: str = "") -> None:
        """重要提示：优先 send_toast（有提示音）；失败则退回聊天栏。"""
        if player is None:
            return
        title_s = str(title or "").strip() or self._toast_title("TOAST_DEFAULT_TITLE", "弧光核心")
        content_s = str(content or "").strip()
        try:
            player.send_toast(title_s, content_s)
            return
        except Exception:
            pass
        try:
            if content_s:
                player.send_message(f"{title_s} {content_s}".strip())
            else:
                player.send_message(title_s)
        except Exception:
            pass

    def _notify_important(
        self, player: Player, message: str, *, title: Optional[str] = None
    ) -> None:
        """把重要结果通知改为 toast；title 为空时用默认「弧光核心」。"""
        if player is None:
            return
        msg = str(message or "").strip()
        if not msg:
            return
        self._send_toast(
            player,
            title or self._toast_title("TOAST_DEFAULT_TITLE", "弧光核心"),
            self._strip_arc_message_prefix(msg),
        )

    def _handle_tpa_command(self, sender: CommandSender, args: list[str]) -> bool:
        if not isinstance(sender, Player):
            sender.send_message("[ARC Core]This command only works for players.")
            return True
        usage = self.language_manager.GetText("TPA_COMMAND_USAGE")
        if not (usage and str(usage).strip()):
            usage = "[弧光核心]用法：/tpa accept 或 /tpa deny（响应最近一条传送请求）"
        if not args:
            sender.send_message(usage)
            return True
        sub = str(args[0] or "").strip().lower()
        if sub in ("accept", "yes", "y", "ok", "a"):
            self.accept_teleport_request(sender)
            return True
        if sub in ("deny", "no", "n", "refuse", "reject", "d"):
            self.deny_teleport_request(sender)
            return True
        sender.send_message(usage)
        return True

    def increase_player_money_by_name(
        self,
        player_name: str,
        amount: float,
        notify: bool = True,
        *,
        defer_richest: bool = False,
    ) -> bool:
        player_xuid = self.get_player_xuid_by_name(player_name)
        if not player_xuid:
            online_player = self.server.get_player(player_name)
            self.report_arc_error(
                "BANK15",
                f"increase_player_money_by_name cannot resolve xuid for name={player_name!r}",
                online_player,
            )
            return False
        success = self.economy.increase_player_money_by_xuid(player_xuid, amount)
        if not success:
            online_player = self.server.get_player(player_name)
            self.report_arc_error(
                "BANK10",
                f"increase_player_money_by_name failed name={player_name!r} amount={amount!r}",
                online_player,
            )
        if success and notify:
            online_player = self.server.get_player(player_name)
            if online_player is not None:
                new_money = self.economy.get_player_money_by_xuid(player_xuid)
                self._notify_important(
                    online_player,
                    self.language_manager.GetText('MONEY_ADD_HINT').format(
                        self._format_money_display(amount),
                        self._format_money_display(new_money)
                    ),
                    title=self._toast_title("MONEY_ADD_TOAST_TITLE", "存款到账"),
                )
        if success:
            try:
                if defer_richest:
                    self._schedule_richest_title_refresh()
                else:
                    self._update_richest_title_if_needed()
            except Exception:
                pass
            try:
                online_player = self.server.get_player(player_name)
                new_money = self.economy.get_player_money_by_xuid(player_xuid)
                self._sky_eye_log_economy(
                    player=online_player,
                    player_xuid=player_xuid,
                    delta=amount,
                    balance=new_money,
                    reason="increase",
                )
            except Exception:
                pass
        return success

    def increase_player_money_by_xuid(
        self,
        xuid: str,
        amount: float,
        notify: bool = True,
        *,
        defer_richest: bool = False,
    ) -> bool:
        """按 XUID 增加余额（用于 OP 退款等不依赖在线游戏名的场景）。"""
        xuid_s = str(xuid or "").strip()
        if not xuid_s or amount <= 0:
            return False
        success = self.economy.increase_player_money_by_xuid(xuid_s, amount)
        if not success:
            self.report_arc_error(
                "BANK17",
                f"increase_player_money_by_xuid failed xuid={xuid_s!r} amount={amount!r}",
                None,
            )
        if success and notify:
            target = self._find_online_player_by_xuid(xuid_s)
            if target is not None:
                new_money = self.economy.get_player_money_by_xuid(xuid_s)
                self._notify_important(
                    target,
                    self.language_manager.GetText('MONEY_ADD_HINT').format(
                        self._format_money_display(amount),
                        self._format_money_display(new_money)
                    ),
                    title=self._toast_title("MONEY_ADD_TOAST_TITLE", "存款到账"),
                )
        if success:
            try:
                if defer_richest:
                    self._schedule_richest_title_refresh()
                else:
                    self._update_richest_title_if_needed()
            except Exception:
                pass
            try:
                target = self._find_online_player_by_xuid(xuid_s)
                new_money = self.economy.get_player_money_by_xuid(xuid_s)
                self._sky_eye_log_economy(
                    player=target,
                    player_xuid=xuid_s,
                    delta=amount,
                    balance=new_money,
                    reason="increase",
                )
            except Exception:
                pass
        return success

    def decrease_player_money_by_name(self, player_name: str, amount: float, notify: bool = True) -> bool:
        player_xuid = self.get_player_xuid_by_name(player_name)
        if not player_xuid:
            online_player = self.server.get_player(player_name)
            self.report_arc_error(
                "BANK16",
                f"decrease_player_money_by_name cannot resolve xuid for name={player_name!r}",
                online_player,
            )
            return False
        success = self.economy.decrease_player_money_by_xuid(player_xuid, amount)
        if not success:
            online_player = self.server.get_player(player_name)
            self.report_arc_error(
                "BANK11",
                f"decrease_player_money_by_name failed name={player_name!r} amount={amount!r}",
                online_player,
            )
        if success and notify:
            online_player = self.server.get_player(player_name)
            if online_player is not None:
                new_money = self.economy.get_player_money_by_xuid(player_xuid)
                self._notify_important(
                    online_player,
                    self.language_manager.GetText('MONEY_REDUCE_HINT').format(
                        self._format_money_display(amount),
                        self._format_money_display(new_money)
                    ),
                    title=self._toast_title("MONEY_REDUCE_TOAST_TITLE", "存款扣款"),
                )
        if success:
            try:
                self._update_richest_title_if_needed()
            except Exception:
                pass
            try:
                online_player = self.server.get_player(player_name)
                new_money = self.economy.get_player_money_by_xuid(player_xuid)
                self._sky_eye_log_economy(
                    player=online_player,
                    player_xuid=player_xuid,
                    delta=-amount,
                    balance=new_money,
                    reason="decrease",
                )
            except Exception:
                pass
        return success

    def change_player_money_by_name(self, player_name: str, money_to_change: float, notify: bool = True) -> bool:
        m = self._round_money(money_to_change)
        if m == 0:
            return True
        if m > 0:
            return self.increase_player_money_by_name(player_name, m, notify)
        return self.decrease_player_money_by_name(player_name, abs(m), notify)

    def increase_player_money(self, player: Player, amount: float, notify: bool = True) -> bool:
        return self.increase_player_money_by_name(player.name, amount, notify)

    def decrease_player_money(self, player: Player, amount: float, notify: bool = True) -> bool:
        return self.decrease_player_money_by_name(player.name, amount, notify)

    def _today_checkin_date_str(self) -> str:
        return datetime.now().date().isoformat()

    def _parse_checkin_reward_list_raw(self, raw: Optional[str]) -> list:
        """解析配置中的 CHECKIN_REWARD_LIST（JSON 数组），每项 [物品ID, 数量, 权重]。"""
        if raw is None or not str(raw).strip():
            return []
        try:
            data = json.loads(str(raw).strip())
        except Exception:
            return []
        if not isinstance(data, list):
            return []
        result = []
        for row in data:
            if not isinstance(row, (list, tuple)) or len(row) < 3:
                continue
            item_id = str(row[0]).strip()
            try:
                item_count = int(row[1])
            except (ValueError, TypeError):
                continue
            try:
                weight = int(row[2])
            except (ValueError, TypeError):
                continue
            if not item_id or item_count <= 0 or weight <= 0:
                continue
            result.append({"item_id": item_id, "item_count": item_count, "weight": weight})
        return result

    def _save_checkin_reward_list_entries(self, entries: list) -> None:
        """将奖励条目写回 CHECKIN_REWARD_LIST（每项 [物品ID, 数量, 权重]）。"""
        payload = [[e["item_id"], int(e["item_count"]), int(e["weight"])] for e in entries]
        self.setting_manager.SetSetting(
            "CHECKIN_REWARD_LIST", json.dumps(payload, ensure_ascii=False)
        )

    def _get_checkin_pick_range(self) -> tuple:
        """随机物品抽取条数区间 [最小, 最大]；未配置 MIN/MAX 时沿用 CHECKIN_REWARD_PICK_COUNT。"""
        raw_min = self.setting_manager.GetSetting("CHECKIN_REWARD_PICK_MIN")
        raw_max = self.setting_manager.GetSetting("CHECKIN_REWARD_PICK_MAX")

        def parse_int_safe(raw_value, default_value: int) -> int:
            try:
                return int(str(raw_value).strip())
            except (ValueError, TypeError, AttributeError):
                return default_value

        min_empty = raw_min is None or str(raw_min).strip() == ""
        max_empty = raw_max is None or str(raw_max).strip() == ""
        if min_empty and max_empty:
            legacy = parse_int_safe(self.setting_manager.GetSetting("CHECKIN_REWARD_PICK_COUNT"), 0)
            pick_min = max(0, legacy)
            pick_max = max(0, legacy)
        else:
            pick_min = max(0, parse_int_safe(raw_min, 0))
            pick_max = max(0, parse_int_safe(raw_max, pick_min))
        if pick_min > pick_max:
            pick_min, pick_max = pick_max, pick_min
        return pick_min, pick_max

    def _get_checkin_non_negative_int_setting(self, setting_key: str, default_value: int = 0) -> int:
        raw_value = self.setting_manager.GetSetting(setting_key)
        try:
            parsed_value = int(str(raw_value).strip())
        except (ValueError, TypeError, AttributeError):
            parsed_value = default_value
        return max(0, parsed_value)

    def _get_checkin_non_negative_money_setting(self, setting_key: str, default_value: float = 0.0) -> float:
        raw_value = self.setting_manager.GetSetting(setting_key)
        try:
            parsed_value = float(str(raw_value).strip())
        except (ValueError, TypeError, AttributeError):
            parsed_value = default_value
        return self._round_money(max(0.0, parsed_value))

    @staticmethod
    def _compute_next_continuous_checkin_days(last_checkin_date: str, current_days: int, today: str) -> int:
        if not last_checkin_date:
            return 1
        try:
            today_date = datetime.fromisoformat(today).date()
            yesterday = (today_date - timedelta(days=1)).isoformat()
        except ValueError:
            return 1
        if last_checkin_date == yesterday:
            return max(1, int(current_days) + 1)
        return 1

    @staticmethod
    def _calculate_checkin_top_rank_bonus_money(today_rank: int, top_rank_limit: int, step_money: float) -> float:
        if today_rank <= 0 or top_rank_limit <= 0 or today_rank > top_rank_limit or step_money <= 0:
            return 0.0
        reward_multiplier = top_rank_limit - today_rank + 1
        return round(step_money * reward_multiplier, 2)

    def get_checkin_config(self) -> Dict[str, Any]:
        raw_money = self.setting_manager.GetSetting("CHECKIN_DAILY_MONEY")
        try:
            daily_money = float(raw_money)
        except (ValueError, TypeError):
            daily_money = 0.0
        daily_money = self._round_money(daily_money)

        pick_min, pick_max = self._get_checkin_pick_range()
        top_rank_limit = self._get_checkin_non_negative_int_setting("CHECKIN_TOP_RANK_LIMIT", 0)
        top_rank_bonus_item_count = self._get_checkin_non_negative_int_setting(
            "CHECKIN_TOP_RANK_BONUS_ITEM_COUNT", 0
        )
        top_rank_bonus_money_step = self._get_checkin_non_negative_money_setting(
            "CHECKIN_TOP_RANK_BONUS_MONEY_STEP", 0.0
        )
        continuous_checkin_money_increment = self._get_checkin_non_negative_money_setting(
            "CHECKIN_CONTINUOUS_DAYS_MONEY_INCREMENT", 0.0
        )
        checkin_guild_contribution_points = self._get_checkin_non_negative_int_setting(
            "CHECKIN_GUILD_CONTRIBUTION_POINTS", 10
        )

        reward_list = self._parse_checkin_reward_list_raw(self.setting_manager.GetSetting("CHECKIN_REWARD_LIST"))
        return {
            "daily_money": daily_money,
            "pick_min": pick_min,
            "pick_max": pick_max,
            "top_rank_limit": top_rank_limit,
            "top_rank_bonus_item_count": top_rank_bonus_item_count,
            "top_rank_bonus_money_step": top_rank_bonus_money_step,
            "continuous_checkin_money_increment": continuous_checkin_money_increment,
            "checkin_guild_contribution_points": checkin_guild_contribution_points,
            "reward_list": reward_list,
        }

    @staticmethod
    def _weighted_sample_checkin_rewards(entries: list, pick_count: int) -> list:
        """按权重不放回抽取若干条奖励配置。"""
        pool = [e for e in entries if int(e.get("weight", 0) or 0) > 0]
        if not pool or pick_count <= 0:
            return []
        k = min(int(pick_count), len(pool))
        chosen = []
        for _ in range(k):
            total_w = sum(int(x["weight"]) for x in pool)
            if total_w <= 0:
                break
            r = random.uniform(0, total_w)
            acc = 0.0
            pick_idx = 0
            for i, x in enumerate(pool):
                acc += int(x["weight"])
                if r <= acc:
                    pick_idx = i
                    break
            chosen.append(pool.pop(pick_idx))
        return chosen

    def _broadcast_chat_lines(self, message: str) -> None:
        """将多行文本按行分别广播，避免聊天栏把换行挤成一行看不清。"""
        if not (message or "").strip():
            return
        for line in message.replace("\r\n", "\n").split("\n"):
            stripped = line.strip()
            if stripped:
                self.server.broadcast_message(stripped)

    def _build_checkin_rank_texts(self, today: str):
        """
        构建签到排行榜文本：
        - 今日最早签到前 10 名（使用 CHECKIN_CHAT_TODAY_RANK_EARLY）
        - 累计签到前 10 名（使用 CHECKIN_CHAT_TOTAL_RANK）
        返回 (today_text or None, total_text or None)
        """
        rank_limit = 10
        today_text = None
        total_text = None

        try:
            order_by_time = (
                "(last_checkin_at IS NULL OR trim(last_checkin_at) = ''), last_checkin_at "
            )
            sep = self.language_manager.GetText("CHECKIN_CHAT_RANK_SEPARATOR")
            slot_key = "CHECKIN_CHAT_TODAY_RANK_SLOT"

            # 今日最早签到前 10 名（本服）
            today_rows = self.database_manager.query_all(
                "SELECT l.xuid AS xuid, b.name AS name, l.last_checkin_at AS last_checkin_at "
                "FROM player_local_info l "
                "LEFT JOIN player_basic_info b ON b.xuid = l.xuid "
                "WHERE l.last_checkin_date = ? "
                "ORDER BY " + order_by_time + "ASC LIMIT ?",
                (today, rank_limit),
            )
            if today_rows:
                early_parts = []
                for index, row in enumerate(today_rows, start=1):
                    display_name = self._rank_display_name_from_row(row)
                    early_parts.append(
                        self.language_manager.GetText(slot_key).format(
                            index, display_name
                        )
                    )
                today_text = self.language_manager.GetText(
                    "CHECKIN_CHAT_TODAY_RANK_EARLY"
                ).format(sep.join(early_parts))
        except Exception as e:
            if self.logger:
                self.logger.error(
                    f"{ColorFormat.RED}[ARC Core]build checkin today rank text error: {e}"
                )

        try:
            # 累计签到前 10 名（本服）
            total_rows = self.database_manager.query_all(
                "SELECT l.xuid AS xuid, b.name AS name, "
                "l.total_checkin_count AS total_checkin_count "
                "FROM player_local_info l "
                "LEFT JOIN player_basic_info b ON b.xuid = l.xuid "
                "WHERE COALESCE(l.total_checkin_count, 0) > 0 "
                "ORDER BY l.total_checkin_count DESC, l.xuid ASC LIMIT ?",
                (rank_limit,),
            )
            if total_rows:
                times_unit = self.language_manager.GetText(
                    "CHECKIN_RANK_TIMES_SUFFIX"
                )
                total_parts = []
                for index, row in enumerate(total_rows, start=1):
                    display_name = self._rank_display_name_from_row(row)
                    try:
                        checkin_times = int(row.get("total_checkin_count") or 0)
                    except (ValueError, TypeError):
                        checkin_times = 0
                    total_parts.append(
                        self.language_manager.GetText(
                            "CHECKIN_CHAT_TOTAL_RANK_SLOT"
                        ).format(index, display_name, checkin_times, times_unit)
                    )
                total_text = self.language_manager.GetText(
                    "CHECKIN_CHAT_TOTAL_RANK"
                ).format(
                    self.language_manager.GetText(
                        "CHECKIN_CHAT_RANK_SEPARATOR"
                    ).join(total_parts)
                )
        except Exception as e:
            if self.logger:
                self.logger.error(
                    f"{ColorFormat.RED}[ARC Core]build checkin total rank text error: {e}"
                )

        return today_text, total_text

    def _broadcast_checkin_rankings(self, player_display_name: str, today: str) -> None:
        """签到成功后全服广播：完成提示、今日最早前 10 名、累计前 10 名（与面板复用同一套文本逻辑）。"""
        try:
            self.server.broadcast_message(
                self.language_manager.GetText("CHECKIN_CHAT_ANNOUNCE").format(
                    player_display_name
                )
            )
        except Exception as e:
            if self.logger:
                self.logger.error(
                    f"{ColorFormat.RED}[ARC Core]checkin broadcast announce error: {e}"
                )
            return

        today_text, total_text = self._build_checkin_rank_texts(today)
        if today_text:
            self._broadcast_chat_lines(today_text)
        if total_text:
            self._broadcast_chat_lines(total_text)

    def show_daily_checkin_panel(self, player: Player):
        """每日签到：同一天仅一次，发放配置中的金钱与加权随机物品。"""
        player_xuid = str(player.xuid)
        today = self._today_checkin_date_str()
        self.init_player_local_info(player)
        row = self.database_manager.query_one(
            "SELECT last_checkin_date, continuous_checkin_days FROM player_local_info WHERE xuid = ?",
            (player_xuid,),
        )
        last_date = (row.get("last_checkin_date") if row else None) or ""
        last_date = str(last_date).strip()
        if last_date == today:
            # 已签到玩家：在提示文案下方附带签到排行榜（只显示今日前 10 名 + 累计前 10 名）
            content_lines = [self.language_manager.GetText("CHECKIN_ALREADY_DONE")]
            try:
                today_text, total_text = self._build_checkin_rank_texts(today)
                if today_text:
                    content_lines.append(today_text)
                if total_text:
                    content_lines.append(total_text)
            except Exception as e:
                if self.logger:
                    self.logger.error(
                        f"{ColorFormat.RED}[ARC Core]show_daily_checkin_panel rank build error: {e}"
                    )

            panel = ActionForm(
                title=self.language_manager.GetText("CHECKIN_PANEL_TITLE"),
                content="\n\n".join(content_lines),
                on_close=None,
            )
            panel.add_button(
                self.language_manager.GetText("RETURN_BUTTON_TEXT"),
                on_click=self.show_main_menu,
            )
            player.send_form(panel)
            return

        cfg = self.get_checkin_config()
        daily_money = cfg["daily_money"]
        pick_min = cfg["pick_min"]
        pick_max = cfg["pick_max"]
        top_rank_limit = cfg["top_rank_limit"]
        top_rank_bonus_item_count = cfg["top_rank_bonus_item_count"]
        top_rank_bonus_money_step = cfg["top_rank_bonus_money_step"]
        continuous_checkin_money_increment = cfg["continuous_checkin_money_increment"]
        checkin_guild_contribution_points = int(cfg.get("checkin_guild_contribution_points") or 0)
        reward_list = cfg["reward_list"]

        today_rank = 1
        try:
            signed_count_row = self.database_manager.query_one(
                "SELECT COUNT(1) AS cnt FROM player_local_info WHERE last_checkin_date = ?",
                (today,),
            )
            today_signed_count = int((signed_count_row or {}).get("cnt") or 0)
            today_rank = max(1, today_signed_count + 1)
        except (ValueError, TypeError):
            today_rank = 1

        try:
            current_continuous_days = int((row or {}).get("continuous_checkin_days") or 0)
        except (ValueError, TypeError):
            current_continuous_days = 0
        next_continuous_days = self._compute_next_continuous_checkin_days(
            last_date, current_continuous_days, today
        )

        rank_bonus_money = self._calculate_checkin_top_rank_bonus_money(
            today_rank, top_rank_limit, top_rank_bonus_money_step
        )
        continuous_bonus_money = self._round_money(
            max(0, next_continuous_days - 1) * continuous_checkin_money_increment
        )
        total_money = self._round_money(daily_money + rank_bonus_money + continuous_bonus_money)

        pick_count = random.randint(pick_min, pick_max) if pick_max >= pick_min else 0
        picked = self._weighted_sample_checkin_rewards(reward_list, pick_count)
        bonus_picked = []
        if (
            top_rank_limit > 0
            and today_rank <= top_rank_limit
            and top_rank_bonus_item_count > 0
            and reward_list
        ):
            bonus_picked = self._weighted_sample_checkin_rewards(
                reward_list, top_rank_bonus_item_count
            )

        if total_money > 0:
            self.increase_player_money(player, total_money)

        item_lines = []
        for it in picked:
            item_id = it["item_id"]
            cnt = int(it["item_count"])
            if cnt <= 0:
                continue
            try:
                self.server.dispatch_command(
                    self.server.command_sender,
                    f"give {format_mc_command_player_name(player.name)} {item_id} {cnt}",
                )
            except Exception:
                pass
            item_lines.append(f"{item_id} x{cnt}")

        bonus_item_lines = []
        for it in bonus_picked:
            item_id = it["item_id"]
            cnt = int(it["item_count"])
            if cnt <= 0:
                continue
            try:
                self.server.dispatch_command(
                    self.server.command_sender,
                    f"give {format_mc_command_player_name(player.name)} {item_id} {cnt}",
                )
            except Exception:
                pass
            bonus_item_lines.append(f"{item_id} x{cnt}")

        now_iso = datetime.now().replace(microsecond=0).isoformat()
        self.init_player_local_info(player)
        ok = self.database_manager.execute(
            "UPDATE player_local_info SET last_checkin_date = ?, last_checkin_at = ?, "
            "total_checkin_count = COALESCE(total_checkin_count, 0) + 1, continuous_checkin_days = ? "
            "WHERE xuid = ?",
            (today, now_iso, next_continuous_days, player_xuid),
        )
        # Keep display name fresh on synced account row.
        try:
            self.database_manager.update(
                table="player_basic_info",
                data={"name": player.name},
                where="xuid = ?",
                params=(player_xuid,),
            )
        except Exception:
            pass
        guild_contrib_extra_line = ""
        if not ok:
            self.report_arc_error(
                "CHK1",
                f"checkin update stats failed xuid={player_xuid!r} date={today!r}",
                player,
            )
        else:
            announcer = self.get_player_name_by_xuid(player_xuid, return_with_title=True) or player.name
            self._broadcast_checkin_rankings(announcer, today)
            if checkin_guild_contribution_points > 0:
                ok_gc, err_gc, info_gc = self.guild_system.add_contribution_by_xuid(
                    player_xuid, checkin_guild_contribution_points
                )
                if ok_gc:
                    tmpl = self.language_manager.GetText("CHECKIN_SUCCESS_GUILD_CONTRIB")
                    if not (tmpl and str(tmpl).strip()):
                        tmpl = "公会贡献点：+{0}（我的：{1}，公会公共：{2}）"
                    guild_contrib_extra_line = "\n\n" + tmpl.format(
                        int(checkin_guild_contribution_points),
                        int(info_gc.get("personal", 0)),
                        int(info_gc.get("guild_total", 0)),
                    )
                elif err_gc and err_gc != "GUILD_NOT_IN_GUILD" and self.logger:
                    self.logger.warning(
                        f"[ARC Core]checkin guild contribution failed xuid={player_xuid!r} err={err_gc!r}"
                    )

        if total_money > 0:
            money_line = self.language_manager.GetText("CHECKIN_SUCCESS_MONEY_LINE").format(
                self._format_money_display(daily_money),
                self._format_money_display(rank_bonus_money),
                self._format_money_display(continuous_bonus_money),
                self._format_money_display(total_money),
            )
        else:
            money_line = self.language_manager.GetText("CHECKIN_SUCCESS_NO_MONEY_LINE")
        items_text = "、".join(item_lines) if item_lines else self.language_manager.GetText("CHECKIN_NO_ITEM_REWARD")
        content = self.language_manager.GetText("CHECKIN_SUCCESS_CONTENT").format(money_line, items_text)
        if bonus_item_lines:
            content = (
                content
                + "\n\n"
                + self.language_manager.GetText("CHECKIN_TOP_RANK_BONUS_ITEM_NOTICE").format(
                    top_rank_limit, "、".join(bonus_item_lines)
                )
            )
        if guild_contrib_extra_line:
            content = content + guild_contrib_extra_line

        result_panel = ActionForm(
            title=self.language_manager.GetText("CHECKIN_PANEL_TITLE"),
            content=content,
            on_close=None,
        )
        result_panel.add_button(self.language_manager.GetText("RETURN_BUTTON_TEXT"), on_click=self.show_main_menu)
        player.send_form(result_panel)

    def show_checkin_config_panel(self, player: Player):
        """OP：签到配置入口（存款/条数 与 物品奖励列表分步配置）。"""
        cfg = self.get_checkin_config()
        hub_content = self.language_manager.GetText("CHECKIN_CONFIG_HUB_CONTENT").format(
            self._format_money_display(cfg["daily_money"]),
            cfg["pick_min"],
            cfg["pick_max"],
            cfg["top_rank_limit"],
            cfg["top_rank_bonus_item_count"],
            self._format_money_display(cfg["top_rank_bonus_money_step"]),
            self._format_money_display(cfg["continuous_checkin_money_increment"]),
            len(cfg["reward_list"]),
            int(cfg.get("checkin_guild_contribution_points") or 0),
        )
        hub = ActionForm(
            title=self.language_manager.GetText("CHECKIN_CONFIG_TITLE"),
            content=hub_content,
            on_close=None,
        )
        hub.add_button(
            self.language_manager.GetText("CHECKIN_CONFIG_MONEY_PICK_BUTTON"),
            on_click=self.show_checkin_money_pick_modal,
        )
        hub.add_button(
            self.language_manager.GetText("CHECKIN_CONFIG_REWARD_LIST_BUTTON"),
            on_click=self.show_checkin_reward_list_panel,
        )
        hub.add_button(
            self.language_manager.GetText("RETURN_BUTTON_TEXT"),
            on_click=self.show_op_main_panel,
        )
        player.send_form(hub)

    def show_checkin_money_pick_modal(self, player: Player):
        """OP：编辑签到存款与随机抽取条数区间（每日在最小～最大之间随机）。"""
        cfg = self.get_checkin_config()
        money_input = TextInput(
            label=self.language_manager.GetText("CHECKIN_CONFIG_MONEY_LABEL"),
            placeholder="0",
            default_value=str(cfg["daily_money"]),
        )
        pick_min_input = TextInput(
            label=self.language_manager.GetText("CHECKIN_CONFIG_PICK_MIN_LABEL"),
            placeholder="0",
            default_value=str(cfg["pick_min"]),
        )
        pick_max_input = TextInput(
            label=self.language_manager.GetText("CHECKIN_CONFIG_PICK_MAX_LABEL"),
            placeholder="0",
            default_value=str(cfg["pick_max"]),
        )
        top_rank_limit_input = TextInput(
            label=self.language_manager.GetText("CHECKIN_CONFIG_TOP_RANK_LIMIT_LABEL"),
            placeholder="0",
            default_value=str(cfg["top_rank_limit"]),
        )
        top_rank_bonus_item_count_input = TextInput(
            label=self.language_manager.GetText("CHECKIN_CONFIG_TOP_RANK_BONUS_ITEM_COUNT_LABEL"),
            placeholder="0",
            default_value=str(cfg["top_rank_bonus_item_count"]),
        )
        top_rank_bonus_money_step_input = TextInput(
            label=self.language_manager.GetText("CHECKIN_CONFIG_TOP_RANK_BONUS_MONEY_STEP_LABEL"),
            placeholder="0",
            default_value=str(cfg["top_rank_bonus_money_step"]),
        )
        continuous_checkin_money_increment_input = TextInput(
            label=self.language_manager.GetText("CHECKIN_CONFIG_CONTINUOUS_MONEY_INCREMENT_LABEL"),
            placeholder="0",
            default_value=str(cfg["continuous_checkin_money_increment"]),
        )
        guild_contribution_points_input = TextInput(
            label=self.language_manager.GetText("CHECKIN_CONFIG_GUILD_CONTRIB_LABEL"),
            placeholder="10",
            default_value=str(int(cfg.get("checkin_guild_contribution_points") or 0)),
        )

        def try_save(p: Player, json_str: str):
            try:
                data = json.loads(json_str)
            except Exception:
                p.send_message(self.language_manager.GetText("CHECKIN_CONFIG_SAVE_FAIL"))
                return self.show_checkin_config_panel(p)
            if not data or len(data) < 8:
                p.send_message(self.language_manager.GetText("CHECKIN_CONFIG_SAVE_FAIL"))
                return self.show_checkin_config_panel(p)
            try:
                money_v = self._round_money(float(str(data[0]).strip()))
            except (ValueError, TypeError):
                money_v = 0.0
            try:
                pick_min_v = int(str(data[1]).strip())
            except (ValueError, TypeError):
                pick_min_v = 0
            try:
                pick_max_v = int(str(data[2]).strip())
            except (ValueError, TypeError):
                pick_max_v = 0
            try:
                top_rank_limit_v = int(str(data[3]).strip())
            except (ValueError, TypeError):
                top_rank_limit_v = 0
            try:
                top_rank_bonus_item_count_v = int(str(data[4]).strip())
            except (ValueError, TypeError):
                top_rank_bonus_item_count_v = 0
            try:
                top_rank_bonus_money_step_v = self._round_money(float(str(data[5]).strip()))
            except (ValueError, TypeError):
                top_rank_bonus_money_step_v = 0.0
            try:
                continuous_checkin_money_increment_v = self._round_money(float(str(data[6]).strip()))
            except (ValueError, TypeError):
                continuous_checkin_money_increment_v = 0.0
            try:
                guild_contribution_points_v = int(str(data[7]).strip())
            except (ValueError, TypeError):
                guild_contribution_points_v = 0
            if pick_min_v < 0:
                pick_min_v = 0
            if pick_max_v < 0:
                pick_max_v = 0
            if top_rank_limit_v < 0:
                top_rank_limit_v = 0
            if top_rank_bonus_item_count_v < 0:
                top_rank_bonus_item_count_v = 0
            if top_rank_bonus_money_step_v < 0:
                top_rank_bonus_money_step_v = 0.0
            if continuous_checkin_money_increment_v < 0:
                continuous_checkin_money_increment_v = 0.0
            if guild_contribution_points_v < 0:
                guild_contribution_points_v = 0
            if pick_min_v > pick_max_v:
                pick_min_v, pick_max_v = pick_max_v, pick_min_v
            reward_entries = self.get_checkin_config()["reward_list"]
            if (pick_max_v > 0 or (top_rank_limit_v > 0 and top_rank_bonus_item_count_v > 0)) and not reward_entries:
                p.send_message(self.language_manager.GetText("CHECKIN_CONFIG_LIST_INVALID"))
                return self.show_checkin_config_panel(p)
            self.setting_manager.SetSetting("CHECKIN_DAILY_MONEY", money_v)
            self.setting_manager.SetSetting("CHECKIN_REWARD_PICK_MIN", pick_min_v)
            self.setting_manager.SetSetting("CHECKIN_REWARD_PICK_MAX", pick_max_v)
            self.setting_manager.SetSetting("CHECKIN_REWARD_PICK_COUNT", pick_max_v)
            self.setting_manager.SetSetting("CHECKIN_TOP_RANK_LIMIT", top_rank_limit_v)
            self.setting_manager.SetSetting("CHECKIN_TOP_RANK_BONUS_ITEM_COUNT", top_rank_bonus_item_count_v)
            self.setting_manager.SetSetting("CHECKIN_TOP_RANK_BONUS_MONEY_STEP", top_rank_bonus_money_step_v)
            self.setting_manager.SetSetting(
                "CHECKIN_CONTINUOUS_DAYS_MONEY_INCREMENT", continuous_checkin_money_increment_v
            )
            self.setting_manager.SetSetting(
                "CHECKIN_GUILD_CONTRIBUTION_POINTS", guild_contribution_points_v
            )
            p.send_message(self.language_manager.GetText("CHECKIN_CONFIG_SAVED"))
            self.show_checkin_config_panel(p)

        form = ModalForm(
            title=self.language_manager.GetText("CHECKIN_CONFIG_MONEY_PICK_MODAL_TITLE"),
            controls=[
                money_input,
                pick_min_input,
                pick_max_input,
                top_rank_limit_input,
                top_rank_bonus_item_count_input,
                top_rank_bonus_money_step_input,
                continuous_checkin_money_increment_input,
                guild_contribution_points_input,
            ],
            on_close=None,
            on_submit=try_save,
        )
        player.send_form(form)

    def show_checkin_reward_list_panel(self, player: Player):
        """OP：查看/增删改签到物品奖励（带编号）。"""
        reward_entries = self.get_checkin_config()["reward_list"]
        if reward_entries:
            lines = []
            for idx, e in enumerate(reward_entries, start=1):
                lines.append(
                    self.language_manager.GetText("CHECKIN_REWARD_LIST_LINE").format(
                        idx, e["item_id"], e["item_count"], e["weight"]
                    )
                )
            list_content = "\n".join(lines)
        else:
            list_content = self.language_manager.GetText("CHECKIN_REWARD_LIST_EMPTY")
        panel = ActionForm(
            title=self.language_manager.GetText("CHECKIN_REWARD_LIST_TITLE"),
            content=list_content,
            on_close=None,
        )
        panel.add_button(
            self.language_manager.GetText("CHECKIN_REWARD_ADD_BUTTON"),
            on_click=self.show_checkin_reward_add_modal,
        )
        for entry_index, e in enumerate(reward_entries):
            btn_label = self.language_manager.GetText("CHECKIN_REWARD_LIST_ITEM_BUTTON").format(
                entry_index + 1, e["item_id"], e["item_count"]
            )
            panel.add_button(
                btn_label,
                on_click=lambda pl, i=entry_index: self.show_checkin_reward_entry_menu(pl, i),
            )
        panel.add_button(
            self.language_manager.GetText("RETURN_BUTTON_TEXT"),
            on_click=self.show_checkin_config_panel,
        )
        player.send_form(panel)

    def show_checkin_reward_entry_menu(self, player: Player, entry_index: int):
        """OP：单条条目 — 编辑或删除。"""
        reward_entries = self.get_checkin_config()["reward_list"]
        if entry_index < 0 or entry_index >= len(reward_entries):
            self.show_checkin_reward_list_panel(player)
            return
        e = reward_entries[entry_index]
        display_num = entry_index + 1
        sub = ActionForm(
            title=self.language_manager.GetText("CHECKIN_REWARD_ENTRY_MENU_TITLE"),
            content=self.language_manager.GetText("CHECKIN_REWARD_ENTRY_MENU_CONTENT").format(
                display_num, e["item_id"], e["item_count"], e["weight"]
            ),
            on_close=None,
        )
        sub.add_button(
            self.language_manager.GetText("CHECKIN_REWARD_EDIT_BUTTON"),
            on_click=lambda pl, i=entry_index: self.show_checkin_reward_edit_modal(pl, i),
        )
        sub.add_button(
            self.language_manager.GetText("CHECKIN_REWARD_DELETE_BUTTON"),
            on_click=lambda pl, i=entry_index: self.show_checkin_reward_delete_confirm(pl, i),
        )
        sub.add_button(
            self.language_manager.GetText("RETURN_BUTTON_TEXT"),
            on_click=self.show_checkin_reward_list_panel,
        )
        player.send_form(sub)

    def show_checkin_reward_delete_confirm(self, player: Player, entry_index: int):
        """OP：确认删除某条奖励。"""
        reward_entries = self.get_checkin_config()["reward_list"]
        if entry_index < 0 or entry_index >= len(reward_entries):
            self.show_checkin_reward_list_panel(player)
            return
        e = reward_entries[entry_index]
        display_num = entry_index + 1
        confirm = ActionForm(
            title=self.language_manager.GetText("CHECKIN_REWARD_DELETE_CONFIRM_TITLE"),
            content=self.language_manager.GetText("CHECKIN_REWARD_DELETE_CONFIRM_CONTENT").format(
                display_num, e["item_id"], e["item_count"]
            ),
            on_close=None,
        )

        def do_delete(pl: Player, del_index: int = entry_index):
            current = self.get_checkin_config()["reward_list"]
            if del_index < 0 or del_index >= len(current):
                self.show_checkin_reward_list_panel(pl)
                return
            new_list = [current[j] for j in range(len(current)) if j != del_index]
            self._save_checkin_reward_list_entries(new_list)
            pl.send_message(self.language_manager.GetText("CHECKIN_REWARD_LIST_SAVED"))
            self.show_checkin_reward_list_panel(pl)

        confirm.add_button(
            self.language_manager.GetText("CHECKIN_REWARD_DELETE_CONFIRM_BUTTON"),
            on_click=lambda pl, i=entry_index: do_delete(pl, i),
        )
        confirm.add_button(
            self.language_manager.GetText("RETURN_BUTTON_TEXT"),
            on_click=self.show_checkin_reward_list_panel,
        )
        player.send_form(confirm)

    def show_checkin_reward_add_modal(self, player: Player):
        """OP：新增一条 [物品ID, 数量, 权重]，权重可填默认 1。"""
        item_input = TextInput(
            label=self.language_manager.GetText("CHECKIN_REWARD_ITEM_ID_LABEL"),
            placeholder="minecraft:diamond",
            default_value="",
        )
        count_input = TextInput(
            label=self.language_manager.GetText("CHECKIN_REWARD_ITEM_COUNT_LABEL"),
            placeholder="1",
            default_value="1",
        )

        def try_add(p: Player, json_str: str):
            try:
                data = json.loads(json_str)
            except Exception:
                p.send_message(self.language_manager.GetText("CHECKIN_REWARD_ADD_INVALID"))
                return self.show_checkin_reward_list_panel(p)
            if not data or len(data) < 2:
                p.send_message(self.language_manager.GetText("CHECKIN_REWARD_ADD_INVALID"))
                return self.show_checkin_reward_list_panel(p)
            item_id = str(data[0]).strip()
            try:
                item_count = int(str(data[1]).strip())
            except (ValueError, TypeError):
                item_count = 0
            weight = 1
            if len(data) >= 3 and str(data[2]).strip() != "":
                try:
                    weight = int(str(data[2]).strip())
                except (ValueError, TypeError):
                    weight = 1
            if not item_id or item_count <= 0:
                p.send_message(self.language_manager.GetText("CHECKIN_REWARD_ADD_INVALID"))
                return self.show_checkin_reward_list_panel(p)
            if weight <= 0:
                weight = 1
            current = self.get_checkin_config()["reward_list"][:]
            current.append(
                {"item_id": item_id, "item_count": item_count, "weight": weight}
            )
            self._save_checkin_reward_list_entries(current)
            p.send_message(self.language_manager.GetText("CHECKIN_REWARD_LIST_SAVED"))
            self.show_checkin_reward_list_panel(p)

        weight_input = TextInput(
            label=self.language_manager.GetText("CHECKIN_REWARD_WEIGHT_LABEL"),
            placeholder="1",
            default_value="1",
        )
        form = ModalForm(
            title=self.language_manager.GetText("CHECKIN_REWARD_ADD_TITLE"),
            controls=[item_input, count_input, weight_input],
            on_close=None,
            on_submit=try_add,
        )
        player.send_form(form)

    def show_checkin_reward_edit_modal(self, player: Player, entry_index: int):
        """OP：编辑指定编号的奖励条目。"""
        reward_entries = self.get_checkin_config()["reward_list"]
        if entry_index < 0 or entry_index >= len(reward_entries):
            self.show_checkin_reward_list_panel(player)
            return
        e = reward_entries[entry_index]

        item_input = TextInput(
            label=self.language_manager.GetText("CHECKIN_REWARD_ITEM_ID_LABEL"),
            placeholder="minecraft:diamond",
            default_value=str(e["item_id"]),
        )
        count_input = TextInput(
            label=self.language_manager.GetText("CHECKIN_REWARD_ITEM_COUNT_LABEL"),
            placeholder="1",
            default_value=str(e["item_count"]),
        )
        weight_input = TextInput(
            label=self.language_manager.GetText("CHECKIN_REWARD_WEIGHT_LABEL"),
            placeholder="1",
            default_value=str(e["weight"]),
        )

        def try_save(p: Player, json_str: str):
            try:
                data = json.loads(json_str)
            except Exception:
                p.send_message(self.language_manager.GetText("CHECKIN_REWARD_ADD_INVALID"))
                return self.show_checkin_reward_list_panel(p)
            if not data or len(data) < 2:
                p.send_message(self.language_manager.GetText("CHECKIN_REWARD_ADD_INVALID"))
                return self.show_checkin_reward_list_panel(p)
            item_id = str(data[0]).strip()
            try:
                item_count = int(str(data[1]).strip())
            except (ValueError, TypeError):
                item_count = 0
            try:
                weight = int(str(data[2]).strip()) if len(data) >= 3 else int(e["weight"])
            except (ValueError, TypeError):
                weight = 1
            if not item_id or item_count <= 0:
                p.send_message(self.language_manager.GetText("CHECKIN_REWARD_ADD_INVALID"))
                return self.show_checkin_reward_list_panel(p)
            if weight <= 0:
                weight = 1
            current = self.get_checkin_config()["reward_list"][:]
            if entry_index < 0 or entry_index >= len(current):
                self.show_checkin_reward_list_panel(p)
                return
            current[entry_index] = {
                "item_id": item_id,
                "item_count": item_count,
                "weight": weight,
            }
            self._save_checkin_reward_list_entries(current)
            p.send_message(self.language_manager.GetText("CHECKIN_REWARD_LIST_SAVED"))
            self.show_checkin_reward_list_panel(p)

        form = ModalForm(
            title=self.language_manager.GetText("CHECKIN_REWARD_EDIT_TITLE"),
            controls=[item_input, count_input, weight_input],
            on_close=None,
            on_submit=try_save,
        )
        player.send_form(form)

    def get_player_free_land_blocks(self, player: Player) -> int:
        """获取玩家剩余免费领地格子数"""
        try:
            player_xuid = str(player.xuid)
            result = self.database_manager.query_one(
                "SELECT remaining_free_land_blocks FROM player_local_info WHERE xuid = ?",
                (player_xuid,)
            )
            if result is None:
                # 如果没有记录，返回默认值
                default_free_blocks = int(self.setting_manager.GetSetting('DEFAULT_FREE_LAND_BLOCKS') or '100')
                return default_free_blocks
            return result['remaining_free_land_blocks'] or 0
        except Exception as e:
            self.logger.error(f"{ColorFormat.RED}[ARC Core]Get player free land blocks error: {str(e)}")
            return 0

    def set_player_free_land_blocks(self, player: Player, amount: int) -> bool:
        """设置玩家剩余免费领地格子数"""
        try:
            player_xuid = str(player.xuid)
            return self.database_manager.update(
                table='player_local_info',
                data={'remaining_free_land_blocks': amount},
                where='xuid = ?',
                params=(player_xuid,),
            )
        except Exception as e:
            self.logger.error(f"{ColorFormat.RED}[ARC Core]Set player free land blocks error: {str(e)}")
            return False

    def get_invite_reward_config(self) -> Dict[str, Any]:
        """获取邀请奖励配置"""
        item_name_setting = self.setting_manager.GetSetting('INVITE_REWARD_ITEM_NAME')
        item_name = item_name_setting if item_name_setting is not None else ''

        item_count_setting = self.setting_manager.GetSetting('INVITE_REWARD_ITEM_COUNT')
        money_setting = self.setting_manager.GetSetting('INVITE_REWARD_MONEY')
        free_blocks_setting = self.setting_manager.GetSetting('INVITE_REWARD_FREE_LAND_BLOCKS')

        def parse_int_setting(raw_value: Optional[str]) -> int:
            if raw_value is None:
                return 0
            try:
                value = int(raw_value)
                if value < 0:
                    value = 0
                return value
            except (ValueError, TypeError):
                return 0

        def parse_float_money_setting(raw_value: Optional[str]) -> float:
            if raw_value is None:
                return 0.0
            try:
                value = float(raw_value)
                if value < 0:
                    value = 0.0
                return self._round_money(value)
            except (ValueError, TypeError):
                return 0.0

        item_count = parse_int_setting(item_count_setting)
        money_amount = parse_float_money_setting(money_setting)
        free_blocks = parse_int_setting(free_blocks_setting)

        return {
            'item_name': item_name,
            'item_count': item_count,
            'money': money_amount,
            'free_blocks': free_blocks
        }

    def grant_invite_reward_to_player(self, player: Player, times: int = 1):
        """给玩家发放邀请奖励（可一次性发放多份）"""
        if times <= 0:
            return

        reward_config = self.get_invite_reward_config()

        total_item_count = reward_config['item_count'] * times
        total_money = reward_config['money'] * times
        total_free_blocks = reward_config['free_blocks'] * times

        # 物资奖励通过服务器指令发放
        item_name = reward_config['item_name']
        if item_name and total_item_count > 0:
            try:
                self.server.dispatch_command(
                    self.server.command_sender,
                    f"give {format_mc_command_player_name(player.name)} {item_name} {total_item_count}",
                )
            except Exception as e:
                self.logger.error(f"{ColorFormat.RED}[ARC Core]Give invite reward item error: {str(e)}")

        # 金钱奖励
        if total_money > 0:
            self.increase_player_money(player, total_money, notify=False)

        # 免费领地格子奖励
        if total_free_blocks > 0:
            current_free_blocks = self.get_player_free_land_blocks(player)
            new_free_blocks = current_free_blocks + total_free_blocks
            self.set_player_free_land_blocks(player, new_free_blocks)

        self._notify_important(
            player,
            self.language_manager.GetText('INVITE_REWARD_GIVE_SELF_HINT').format(
                total_item_count,
                self._format_money_display(total_money),
                total_free_blocks
            ),
            title=self._toast_title("INVITE_TOAST_TITLE", "邀请奖励"),
        )

    def add_pending_invite_rewards(self, inviter_xuid: str, times: int = 1):
        """为邀请人累加待领取邀请奖励次数"""
        if times <= 0:
            return
        try:
            self.database_manager.execute(
                "UPDATE player_basic_info "
                "SET pending_invite_reward_times = COALESCE(pending_invite_reward_times, 0) + ? "
                "WHERE xuid = ?",
                (times, inviter_xuid)
            )
        except Exception as e:
            self.logger.error(f"{ColorFormat.RED}[ARC Core]Add pending invite rewards error: {str(e)}")

    def get_top_richest_players(self, top_count: int):
        """
        获取存款排行前 top_count 名玩家的信息列表。
        返回值为列表，每项包含：
        {
            "xuid": str,
            "display_name": str,  # 可能带有头衔/颜色的展示名
            "money": float,
            "once_op": bool or None,  # None 表示未知；曾以 OP 登录过则 True
        }
        """
        rich_list = []
        for entry in self.economy.get_top_richest_xuids(top_count):
            try:
                player_xuid = entry.get("xuid")
                if not player_xuid:
                    continue
                money_value = self._round_money(entry.get("money", 0.0))
                display_name = (
                    self.get_player_name_by_xuid(player_xuid, return_with_title=True)
                    or self.get_player_name_by_xuid(player_xuid, return_with_title=False)
                    or str(player_xuid)
                )
                once_op = self.get_player_once_op_by_xuid(player_xuid)
                rich_list.append(
                    {
                        "xuid": player_xuid,
                        "display_name": display_name,
                        "money": money_value,
                        "once_op": once_op,
                    }
                )
            except Exception:
                continue
        return rich_list

    def get_player_money_rank(self, player: Player) -> Optional[int]:
        return self.economy.get_player_money_rank_by_xuid(str(player.xuid))

    def judge_if_player_has_enough_money_by_name(self, player_name: str, amount: float) -> bool:
        player_xuid = self.get_player_xuid_by_name(player_name)
        return self.economy.judge_if_player_has_enough_money_by_xuid(player_xuid, amount) if player_xuid else False

    def judge_if_player_has_enough_money(self, player: Player, amount: float) -> bool:
        return self.economy.judge_if_player_has_enough_money_by_xuid(str(player.xuid), amount)

    def _guild_text(self, key: str, default: str) -> str:
        t = self.language_manager.GetText(key)
        if t is None or not str(t).strip():
            return default
        return str(t)

    def _guild_err(self, code: Optional[str]) -> str:
        if not code:
            return self._guild_text("GUILD_ERR_UNKNOWN", "[弧光核心]操作失败。")
        return self._guild_text(code, f"[弧光核心]操作失败（{code}）。")

    def _find_online_player_by_xuid(self, xuid: str) -> Optional[Player]:
        xuid_s = str(xuid).strip()
        if not xuid_s:
            return None
        try:
            for p in self.server.online_players or []:
                if str(p.xuid) == xuid_s:
                    return p
        except Exception:
            pass
        return None

    def _resolve_online_player(self, xuid: str = "", name: str = "") -> Optional[Player]:
        """从 online_players 重取活对象；不触碰可能已销毁的旧 Player 引用。"""
        xuid_s = str(xuid or "").strip()
        if xuid_s:
            p = self._find_online_player_by_xuid(xuid_s)
            if p is not None:
                return p
        name_s = str(name or "").strip()
        if name_s:
            try:
                return self.server.get_player(name_s)
            except Exception:
                return None
        return None

    def run_player_task(self, player: Player, fn: Callable[[Player], None], delay: int = 0):
        """调度仅对仍在线玩家执行的回调；闭包只存 xuid/name，避免 purecall 崩服。"""
        xuid = str(getattr(player, "xuid", "") or "").strip()
        name = str(getattr(player, "name", "") or "").strip()

        def _wrapped() -> None:
            p = self._resolve_online_player(xuid, name)
            if p is None:
                return
            fn(p)

        return self.server.scheduler.run_task(self, _wrapped, delay=delay)

    def run_two_player_task(
        self,
        player: Player,
        target_player: Player,
        fn: Callable[[Player, Player], None],
        delay: int = 0,
    ):
        """调度需同时解析发起者与目标仍在线的回调。"""
        xuid = str(getattr(player, "xuid", "") or "").strip()
        name = str(getattr(player, "name", "") or "").strip()
        target_xuid = str(getattr(target_player, "xuid", "") or "").strip()
        target_name = str(getattr(target_player, "name", "") or "").strip()

        def _wrapped() -> None:
            p = self._resolve_online_player(xuid, name)
            if p is None:
                return
            t = self._resolve_online_player(target_xuid, target_name)
            if t is None:
                return
            fn(p, t)

        return self.server.scheduler.run_task(self, _wrapped, delay=delay)

    # Guild
    def _guild_size_tier_color(self, tier: str) -> str:
        """规模等级的 MC 颜色码：小型 §h，中型 §s，大型 §p（可由语言文件覆盖）。"""
        t = self.guild_system.normalize_size_tier(tier)
        defaults = {
            SIZE_TIER_SMALL: "§h",
            SIZE_TIER_MEDIUM: "§s",
            SIZE_TIER_LARGE: "§p",
        }
        raw = self._guild_text(f"GUILD_SIZE_TIER_COLOR_{t.upper()}", defaults.get(t, ""))
        s = (raw or "").strip()
        if not s:
            s = defaults.get(t, "")
        return s

    def _guild_size_tier_label(self, tier: str, *, colored: bool = True) -> str:
        """规模等级的本地化显示名。colored=True 时加上 MC 颜色码。"""
        t = self.guild_system.normalize_size_tier(tier)
        plain = self._guild_text(
            f"GUILD_SIZE_TIER_{t.upper()}",
            {SIZE_TIER_SMALL: "小型", SIZE_TIER_MEDIUM: "中型", SIZE_TIER_LARGE: "大型"}.get(
                t, t
            ),
        )
        if not colored:
            return plain
        color = self._guild_size_tier_color(t)
        return f"{color}{plain}§r" if color else plain

    def show_guild_main_menu(self, player: Player):
        xuid = str(player.xuid)
        pending = self.guild_system.list_invites_for_player(xuid)
        mem = self.guild_system.get_membership(xuid)
        cost = self.guild_system.get_create_cost()
        lines = []
        if mem:
            gid = int(mem["guild_id"])
            g = self.guild_system.get_guild(gid)
            gname = g.get("name", "") if g else ""
            role = mem.get("role", "")
            role_label = self._guild_text(
                f"GUILD_ROLE_{str(role).upper()}",
                str(role),
            )
            motto = (g.get("motto") or "") if g else ""
            tier = self.guild_system.get_guild_size_tier(gid)
            tier_label = self._guild_size_tier_label(tier)
            cap = self.guild_system.get_size_tier_max(tier)
            cur = self.guild_system.count_members(gid)
            personal_contrib = self.guild_system.get_member_contribution(xuid)
            guild_contrib = self.guild_system.get_guild_total_contribution(gid)
            lines.append(
                self._guild_text("GUILD_MAIN_IN_GUILD", "所属公会：{0}  职级：{1}").format(
                    gname, role_label
                )
            )
            if motto:
                lines.append(
                    self._guild_text("GUILD_MAIN_MOTTO", "简介：{0}").format(motto)
                )
            lines.append(
                self._guild_text(
                    "GUILD_MAIN_SIZE_LINE",
                    "规模：{0}  人数：{1}/{2}",
                ).format(tier_label, cur, cap)
            )
            lines.append(
                self._guild_text(
                    "GUILD_MAIN_CONTRIB_LINE",
                    "公会贡献点：{0}  我的贡献点：{1}",
                ).format(int(guild_contrib), int(personal_contrib))
            )
        else:
            lines.append(
                self._guild_text(
                    "GUILD_MAIN_NOT_IN_GUILD",
                    "您尚未加入公会。创建需支付 {0}。",
                ).format(self._format_money_display(cost))
            )
            small_max = self.guild_system.get_size_tier_max(SIZE_TIER_SMALL)
            medium_max = self.guild_system.get_size_tier_max(SIZE_TIER_MEDIUM)
            large_max = self.guild_system.get_size_tier_max(SIZE_TIER_LARGE)
            lines.append(
                self._guild_text(
                    "GUILD_MAIN_TIER_HINT",
                    "公会规模：小型≤{0} / 中型≤{1} / 大型≤{2}（默认小型，由 OP 升级）",
                ).format(small_max, medium_max, large_max)
            )
        if pending:
            lines.append(
                self._guild_text(
                    "GUILD_MAIN_PENDING_HINT",
                    "您有 {0} 条待处理公会邀请。",
                ).format(len(pending))
            )
        form = ActionForm(
            title=self._guild_text("GUILD_MAIN_TITLE", "公会"),
            content="\n".join(lines),
            on_close=None,
        )
        if pending:
            form.add_button(
                self._guild_text("GUILD_BTN_PENDING_INVITES", "待处理邀请"),
                on_click=self.show_guild_pending_invites_menu,
            )
        if not mem:
            form.add_button(
                self._guild_text("GUILD_BTN_CREATE", "创建公会"),
                on_click=self.show_guild_create_panel,
            )
        if mem:
            form.add_button(
                self._guild_text("GUILD_BTN_MY_GUILD", "我的公会"),
                on_click=self.show_guild_my_menu,
            )
        form.add_button(
            self._guild_text("GUILD_BTN_BROWSE_ALL", "查看全部公会"),
            on_click=self.show_guild_browse_menu,
        )
        form.add_button(
            self._guild_text("RETURN_BUTTON_TEXT", "返回"),
            on_click=self.show_main_menu,
        )
        player.send_form(form)

    def show_guild_browse_menu(
        self,
        player: Player,
        *,
        page: int = 0,
        name_query: str = "",
    ):
        q = str(name_query or "").strip()
        all_rows = self.guild_system.list_guilds_directory(q)
        page = max(0, int(page))
        ps = GUILD_BROWSE_PAGE_SIZE
        total = len(all_rows)
        total_pages = max(1, (total + ps - 1) // ps)
        if page >= total_pages:
            page = total_pages - 1
        chunk = all_rows[page * ps : (page + 1) * ps]
        hint = self._guild_text(
            "GUILD_BROWSE_HINT",
            "按规模（大→小）排序，同规模按公共贡献点从高到低。",
        )
        filter_line = self._guild_text("GUILD_BROWSE_FILTER", "名称筛选：{0}").format(
            q or self._guild_text("GUILD_BROWSE_NO_FILTER", "（全部）")
        )
        page_line = self._guild_text(
            "GUILD_BROWSE_PAGE",
            "第 {0}/{1} 页，共 {2} 个公会",
        ).format(page + 1, total_pages, total)
        form = ActionForm(
            title=self._guild_text("GUILD_BROWSE_TITLE", "全部公会"),
            content="\n".join([hint, filter_line, page_line]),
            on_close=None,
        )

        def _search(p: Player):
            self.show_guild_browse_search_modal(p, page=page, name_query=q)

        form.add_button(
            self._guild_text("GUILD_BROWSE_BTN_SEARCH", "搜索公会"),
            on_click=_search,
        )
        for row in chunk:
            gid = int(row.get("id") or 0)
            if gid <= 0:
                continue
            gname = str(row.get("name") or "")
            tier = self.guild_system.normalize_size_tier(row.get("size_tier"))
            cap = self.guild_system.get_size_tier_max(tier)
            mc = int(row.get("member_count") or 0)
            contrib = int(row.get("total_contribution") or 0)
            btn = self._guild_text(
                "GUILD_BROWSE_ROW",
                "{0} | {1} {2}/{3} | 贡献 {4}",
            ).format(
                gname,
                self._guild_size_tier_label(tier, colored=False),
                mc,
                cap,
                contrib,
            )

            def _open(p: Player, _gid: int = gid):
                self.show_guild_public_detail(
                    p, _gid, browse_page=page, browse_query=q
                )

            form.add_button(btn, on_click=_open)
        if not chunk and total == 0:
            form.add_button(
                self._guild_text("GUILD_BROWSE_EMPTY", "没有匹配的公会"),
                on_click=lambda p: self.show_guild_browse_menu(
                    p, page=0, name_query=""
                ),
            )
        if page > 0:

            def _prev(p: Player):
                self.show_guild_browse_menu(p, page=page - 1, name_query=q)

            form.add_button(
                self._guild_text("GUILD_BROWSE_PREV", "上一页"),
                on_click=_prev,
            )
        if page < total_pages - 1:

            def _next(p: Player):
                self.show_guild_browse_menu(p, page=page + 1, name_query=q)

            form.add_button(
                self._guild_text("GUILD_BROWSE_NEXT", "下一页"),
                on_click=_next,
            )
        form.add_button(
            self._guild_text("RETURN_BUTTON_TEXT", "返回"),
            on_click=self.show_guild_main_menu,
        )
        player.send_form(form)

    def show_guild_browse_search_modal(
        self, player: Player, *, page: int = 0, name_query: str = ""
    ):
        hint = Label(
            text=self._guild_text(
                "GUILD_BROWSE_SEARCH_HINT",
                "输入公会名称关键字（留空列出全部）；匹配不区分大小写。",
            )
        )
        inp = TextInput(
            label=self._guild_text("GUILD_BROWSE_SEARCH_LABEL", "关键字"),
            placeholder=self._guild_text(
                "GUILD_BROWSE_SEARCH_PLACEHOLDER", "例如：星辰"
            ),
            default_value=str(name_query or ""),
        )

        def _submit(p: Player, json_str: str):
            try:
                data = json.loads(json_str)
            except Exception:
                self.show_guild_browse_menu(p, page=0, name_query=name_query)
                return
            if self._modal_choice_is_back(data, 0):
                self.show_guild_browse_menu(p, page=page, name_query=name_query)
                return
            kw = str(data[2]).strip() if len(data) > 2 else ""
            self.show_guild_browse_menu(p, page=0, name_query=kw)

        form = ModalForm(
            title=self._guild_text("GUILD_BROWSE_SEARCH_TITLE", "搜索公会"),
            controls=[self._modal_nav_dropdown(), hint, inp],
            on_close=None,
            on_submit=_submit,
        )
        player.send_form(form)

    def show_guild_public_detail(
        self,
        player: Player,
        guild_id: int,
        *,
        browse_page: int = 0,
        browse_query: str = "",
    ):
        g = self.guild_system.get_guild(int(guild_id))
        if not g:
            player.send_message(self._guild_err("GUILD_NOT_FOUND"))
            self.show_guild_browse_menu(
                player, page=browse_page, name_query=browse_query
            )
            return
        gid = int(g.get("id") or guild_id)
        gname = str(g.get("name") or "")
        motto = str(g.get("motto") or "").strip()
        tier = self.guild_system.get_guild_size_tier(gid)
        cap = self.guild_system.get_size_tier_max(tier)
        cur = self.guild_system.count_members(gid)
        guild_contrib = self.guild_system.get_guild_total_contribution(gid)
        join_req = self.guild_system.guild_join_requires_approval(gid)
        policy_line = (
            self._guild_text("GUILD_PUBLIC_POLICY_APPROVAL", "入会：需管理员审核")
            if join_req
            else self._guild_text("GUILD_PUBLIC_POLICY_OPEN", "入会：未满时可立即加入")
        )
        lines = [
            self._guild_text("GUILD_PUBLIC_NAME", "公会：{0}").format(gname),
            policy_line,
        ]
        if motto:
            lines.append(
                self._guild_text("GUILD_MAIN_MOTTO", "简介：{0}").format(motto)
            )
        lines.append(
            self._guild_text(
                "GUILD_PUBLIC_META",
                "规模：{0}  人数：{1}/{2}\n公共贡献点：{3}",
            ).format(
                self._guild_size_tier_label(tier), cur, cap, int(guild_contrib)
            ),
        )
        viewer_mem = self.guild_system.get_membership(str(player.xuid))
        in_this = bool(
            viewer_mem and int(viewer_mem.get("guild_id") or 0) == gid
        )
        other_guild = bool(
            viewer_mem and int(viewer_mem.get("guild_id") or 0) != gid
        )
        if other_guild:
            og = self.guild_system.get_guild(int(viewer_mem["guild_id"]))
            oname = str(og.get("name") or "") if og else ""
            lines.append(
                self._guild_text(
                    "GUILD_PUBLIC_YOU_IN_OTHER",
                    "您已加入其他公会：{0}",
                ).format(oname)
            )

        def _back(p: Player):
            self.show_guild_browse_menu(
                p, page=browse_page, name_query=browse_query
            )

        form = ActionForm(
            title=self._guild_text("GUILD_PUBLIC_PREVIEW_TITLE", "公会预览"),
            content="\n".join(lines),
            on_close=None,
        )
        if not viewer_mem:
            join_label = (
                self._guild_text("GUILD_PUBLIC_BTN_APPLY", "申请加入")
                if join_req
                else self._guild_text("GUILD_PUBLIC_BTN_JOIN", "加入公会")
            )

            def _join(p: Player, _gid: int = gid):
                ok, err, outcome = self.guild_system.try_public_join_guild(
                    str(p.xuid), _gid
                )
                if ok:
                    if outcome == "joined":
                        p.send_message(
                            self._guild_text(
                                "GUILD_PUBLIC_JOIN_OK",
                                "[弧光核心]已成功加入该公会。",
                            )
                        )
                        self._update_player_name_tag(p)
                    elif outcome == "pending":
                        p.send_message(
                            self._guild_text(
                                "GUILD_PUBLIC_APPLY_SENT",
                                "[弧光核心]已提交入会申请，请等待管理员处理。",
                            )
                        )
                    self.show_guild_main_menu(p)
                else:
                    p.send_message(self._guild_err(err))
                    self.show_guild_public_detail(
                        p,
                        _gid,
                        browse_page=browse_page,
                        browse_query=browse_query,
                    )

            form.add_button(join_label, on_click=_join)
        elif other_guild:
            pass
        elif in_this:
            form.add_button(
                self._guild_text("GUILD_PUBLIC_BTN_MY_GUILD", "我的公会"),
                on_click=self.show_guild_my_menu,
            )
        form.add_button(
            self._guild_text("GUILD_BROWSE_BACK_TO_LIST", "返回列表"),
            on_click=_back,
        )
        player.send_form(form)

    def show_guild_join_policy_menu(
        self,
        player: Player,
        *,
        browse_page: int = 0,
        browse_query: str = "",
        from_my_guild: bool = False,
    ):
        mem = self.guild_system.get_membership(str(player.xuid))
        if not mem or str(mem.get("role") or "") not in (
            ROLE_OWNER,
            ROLE_MANAGER,
        ):
            player.send_message(self._guild_err("GUILD_NO_PERMISSION"))
            if from_my_guild:
                self.show_guild_my_menu(player)
            else:
                self.show_guild_browse_menu(
                    player, page=browse_page, name_query=browse_query
                )
            return
        gid = int(mem["guild_id"])

        def _back_from_policy(p: Player, _gid: int = gid):
            if from_my_guild:
                self.show_guild_my_menu(p)
            else:
                self.show_guild_public_detail(
                    p, _gid, browse_page=browse_page, browse_query=browse_query
                )

        cur = self.guild_system.guild_join_requires_approval(gid)
        desc = self._guild_text(
            "GUILD_POLICY_CURRENT_APPROVAL",
            "当前：新玩家入会需管理员在「入会申请」中审批。",
        )
        if not cur:
            desc = self._guild_text(
                "GUILD_POLICY_CURRENT_OPEN",
                "当前：未满员时，玩家可从「全部公会」中直接加入。",
            )
        form = ActionForm(
            title=self._guild_text("GUILD_POLICY_TITLE", "入会审核"),
            content=desc,
            on_close=None,
        )

        def _set(p: Player, requires: bool):
            ok, err = self.guild_system.set_guild_join_requires_approval(
                str(p.xuid), requires
            )
            if ok:
                p.send_message(
                    self._guild_text(
                        "GUILD_POLICY_OK",
                        "[弧光核心]入会条件已更新。",
                    )
                )
            else:
                p.send_message(self._guild_err(err))
            _back_from_policy(p, gid)

        form.add_button(
            self._guild_text("GUILD_POLICY_BTN_NEED_APPROVAL", "开启：需要审核"),
            on_click=lambda p: _set(p, True),
        )
        form.add_button(
            self._guild_text("GUILD_POLICY_BTN_DIRECT", "关闭：无需审核（可直接加入）"),
            on_click=lambda p: _set(p, False),
        )
        form.add_button(
            self._guild_text("RETURN_BUTTON_TEXT", "返回"),
            on_click=_back_from_policy,
        )
        player.send_form(form)

    def show_guild_join_requests_menu(self, player: Player):
        mem = self.guild_system.get_membership(str(player.xuid))
        if not mem or str(mem.get("role") or "") not in (
            ROLE_OWNER,
            ROLE_MANAGER,
        ):
            player.send_message(self._guild_err("GUILD_NO_PERMISSION"))
            self.show_guild_my_menu(player)
            return
        gid = int(mem["guild_id"])
        rows = self.guild_system.list_join_requests(gid)
        form = ActionForm(
            title=self._guild_text("GUILD_REQUESTS_TITLE", "入会申请"),
            content=self._guild_text(
                "GUILD_REQUESTS_CONTENT", "选择一名申请人进行处理。"
            ),
            on_close=None,
        )
        if not rows:
            form.add_button(
                self._guild_text("GUILD_REQUESTS_EMPTY", "暂无申请"),
                on_click=self.show_guild_my_menu,
            )
        for r in rows:
            rid = int(r.get("id") or 0)
            ax = str(r.get("applicant_xuid") or "")
            disp = self.get_player_name_by_xuid(ax, return_with_title=False) or ax

            def _open(p: Player, _rid: int = rid):
                self.show_guild_join_request_actions(p, _rid)

            form.add_button(
                self._guild_text("GUILD_REQUESTS_ROW", "{0}").format(disp),
                on_click=_open,
            )
        form.add_button(
            self._guild_text("RETURN_BUTTON_TEXT", "返回"),
            on_click=self.show_guild_my_menu,
        )
        player.send_form(form)

    def show_guild_join_request_actions(self, player: Player, request_id: int):
        mem = self.guild_system.get_membership(str(player.xuid))
        if not mem or str(mem.get("role") or "") not in (
            ROLE_OWNER,
            ROLE_MANAGER,
        ):
            player.send_message(self._guild_err("GUILD_NO_PERMISSION"))
            self.show_guild_my_menu(player)
            return
        req = self.guild_system.get_join_request(int(request_id))
        if not req or int(req.get("guild_id") or 0) != int(mem["guild_id"]):
            player.send_message(
                self._guild_err("GUILD_JOIN_REQUEST_NOT_FOUND")
            )
            self.show_guild_join_requests_menu(player)
            return
        ax = str(req.get("applicant_xuid") or "")
        disp = self.get_player_name_by_xuid(ax, return_with_title=False) or ax
        form = ActionForm(
            title=self._guild_text("GUILD_REQUEST_ACTION_TITLE", "处理申请"),
            content=self._guild_text(
                "GUILD_REQUEST_ACTION_CONTENT", "申请人：{0}"
            ).format(disp),
            on_close=None,
        )

        def _approve(p: Player, _rid: int = int(request_id)):
            ok, err = self.guild_system.approve_join_request(str(p.xuid), _rid)
            if ok:
                p.send_message(
                    self._guild_text(
                        "GUILD_REQUEST_APPROVE_OK",
                        "[弧光核心]已同意该玩家的入会申请。",
                    )
                )
                tgt = self._find_online_player_by_xuid(ax)
                if tgt:
                    tgt.send_message(
                        self._guild_text(
                            "GUILD_REQUEST_ACCEPTED_TARGET",
                            "[弧光核心]您的公会加入申请已通过。",
                        )
                    )
                    self._update_player_name_tag(tgt)
            else:
                p.send_message(self._guild_err(err))
            self.show_guild_join_requests_menu(p)

        def _reject(p: Player, _rid: int = int(request_id)):
            ok, err = self.guild_system.reject_join_request(str(p.xuid), _rid)
            if ok:
                p.send_message(
                    self._guild_text(
                        "GUILD_REQUEST_REJECT_OK",
                        "[弧光核心]已拒绝该申请。",
                    )
                )
                tgt = self._find_online_player_by_xuid(ax)
                if tgt:
                    tgt.send_message(
                        self._guild_text(
                            "GUILD_REQUEST_REJECTED_TARGET",
                            "[弧光核心]您的公会加入申请未通过。",
                        )
                    )
            else:
                p.send_message(self._guild_err(err))
            self.show_guild_join_requests_menu(p)

        form.add_button(
            self._guild_text("GUILD_REQUEST_APPROVE", "同意"),
            on_click=_approve,
        )
        form.add_button(
            self._guild_text("GUILD_REQUEST_REJECT", "拒绝"),
            on_click=_reject,
        )
        form.add_button(
            self._guild_text("RETURN_BUTTON_TEXT", "返回"),
            on_click=self.show_guild_join_requests_menu,
        )
        player.send_form(form)

    def show_guild_pending_invites_menu(self, player: Player):
        xuid = str(player.xuid)
        rows = self.guild_system.list_invites_for_player(xuid)
        form = ActionForm(
            title=self._guild_text("GUILD_PENDING_TITLE", "公会邀请"),
            content=self._guild_text("GUILD_PENDING_CONTENT", "选择一条邀请查看详情。"),
            on_close=None,
        )
        for r in rows:
            gid = int(r["guild_id"])
            inv_id = int(r["invite_id"])
            gname = str(r.get("guild_name") or "")
            label = self._guild_text("GUILD_PENDING_ROW", "{0}").format(gname)

            def _open(p: Player, iid: int = inv_id):
                self.show_guild_invite_action_menu(p, iid)

            form.add_button(label, on_click=_open)
        form.add_button(
            self._guild_text("RETURN_BUTTON_TEXT", "返回"),
            on_click=self.show_guild_main_menu,
        )
        player.send_form(form)

    def show_guild_invite_action_menu(self, player: Player, invite_id: int):
        xuid = str(player.xuid)
        inv = self.guild_system.get_invite(invite_id)
        if not inv or str(inv.get("invitee_xuid")) != xuid:
            player.send_message(self._guild_err("GUILD_NO_INVITE"))
            self.show_guild_pending_invites_menu(player)
            return
        g = self.guild_system.get_guild(int(inv["guild_id"]))
        gname = g.get("name", "") if g else ""
        form = ActionForm(
            title=self._guild_text("GUILD_INVITE_DETAIL_TITLE", "邀请详情"),
            content=self._guild_text(
                "GUILD_INVITE_DETAIL_CONTENT", "公会：{0}"
            ).format(gname),
            on_close=None,
        )

        def _accept(p: Player, iid: int = invite_id):
            ok, err = self.guild_system.accept_invite(str(p.xuid), iid)
            if ok:
                p.send_message(
                    self._guild_text("GUILD_ACCEPT_OK", "[弧光核心]已加入公会。")
                )
                self._update_player_name_tag(p)
            else:
                p.send_message(self._guild_err(err))
            self.show_guild_main_menu(p)

        def _decline(p: Player, iid: int = invite_id):
            ok, err = self.guild_system.decline_invite(str(p.xuid), iid)
            if ok:
                p.send_message(
                    self._guild_text("GUILD_DECLINE_OK", "[弧光核心]已拒绝邀请。")
                )
            else:
                p.send_message(self._guild_err(err))
            self.show_guild_pending_invites_menu(p)

        form.add_button(
            self._guild_text("GUILD_INVITE_ACCEPT", "接受"),
            on_click=_accept,
        )
        form.add_button(
            self._guild_text("GUILD_INVITE_DECLINE", "拒绝"),
            on_click=_decline,
        )
        form.add_button(
            self._guild_text("RETURN_BUTTON_TEXT", "返回"),
            on_click=self.show_guild_pending_invites_menu,
        )
        player.send_form(form)

    def show_guild_invite_popup_live(
        self, player: Player, guild_id: int, inviter_xuid: str
    ):
        """在线邀请弹窗：不依赖 guild_invites 表，凭公会 id 与邀请人 xuid 确认后加入。"""
        g = self.guild_system.get_guild(int(guild_id))
        if not g:
            return
        gname = str(g.get("name") or "")
        ix = str(inviter_xuid or "").strip()
        inviter_disp = self.get_player_name_by_xuid(ix, return_with_title=False) or ix
        form = ActionForm(
            title=self._guild_text("GUILD_INVITE_POPUP_TITLE", "公会邀请"),
            content=self._guild_text(
                "GUILD_INVITE_POPUP_CONTENT",
                "公会：{0}\n邀请人：{1}\n\n是否加入该公会？",
            ).format(gname, inviter_disp),
            on_close=None,
        )

        def _accept(p: Player, gid: int = int(guild_id), inv: str = ix):
            ok, err = self.guild_system.join_via_live_invite(str(p.xuid), gid, inv)
            if ok:
                p.send_message(
                    self._guild_text("GUILD_ACCEPT_OK", "[弧光核心]已加入公会。")
                )
                self._update_player_name_tag(p)
            else:
                p.send_message(self._guild_err(err))

        def _decline(p: Player):
            p.send_message(
                self._guild_text("GUILD_DECLINE_OK", "[弧光核心]已拒绝邀请。")
            )

        form.add_button(
            self._guild_text("GUILD_INVITE_ACCEPT", "接受"),
            on_click=_accept,
        )
        form.add_button(
            self._guild_text("GUILD_INVITE_DECLINE", "拒绝"),
            on_click=_decline,
        )
        player.send_form(form)

    def _guild_send_live_invite(
        self, inviter: Player, target: Player, guild_id: int
    ) -> None:
        mem = self.guild_system.get_membership(str(inviter.xuid))
        if not mem or int(mem["guild_id"]) != int(guild_id):
            inviter.send_message(self._guild_err("GUILD_NO_PERMISSION"))
            self.show_guild_my_menu(inviter)
            return
        if mem.get("role") not in (ROLE_OWNER, ROLE_MANAGER):
            inviter.send_message(self._guild_err("GUILD_NO_PERMISSION"))
            self.show_guild_my_menu(inviter)
            return
        if self.guild_system.get_membership(str(target.xuid)):
            inviter.send_message(self._guild_err("GUILD_TARGET_IN_GUILD"))
            self.show_guild_invite_online_pick_menu(inviter)
            return
        if self.guild_system.is_guild_full(int(guild_id)):
            inviter.send_message(self._guild_err("GUILD_FULL"))
            self.show_guild_my_menu(inviter)
            return
        self.show_guild_invite_popup_live(
            target, int(guild_id), str(inviter.xuid)
        )
        inviter.send_message(
            self._guild_text(
                "GUILD_INVITE_SENT",
                "[弧光核心]已向 {0} 发送公会邀请。",
            ).format(target.name or "?")
        )
        self.show_guild_my_menu(inviter)

    def show_guild_create_panel(self, player: Player):
        cost = self.guild_system.get_create_cost()
        info = Label(
            text=self._guild_text(
                "GUILD_CREATE_LABEL",
                "创建费用：{0}（将立即扣除）",
            ).format(self._format_money_display(cost))
        )
        name_in = TextInput(
            label=self._guild_text(
                "GUILD_CREATE_NAME_LABEL",
                "公会名称（最多8字；禁止 [ ] \" 与 § 颜色/样式符号）",
            ),
            placeholder=self._guild_text(
                "GUILD_CREATE_NAME_PLACEHOLDER", "请输入唯一公会名"
            ),
        )
        motto_in = TextInput(
            label=self._guild_text("GUILD_CREATE_MOTTO_LABEL", "公会简介（可选）"),
            placeholder=self._guild_text("GUILD_CREATE_MOTTO_PLACEHOLDER", "简介"),
            default_value="",
        )

        def _submit(p: Player, json_str: str):
            try:
                data = json.loads(json_str)
            except Exception:
                p.send_message(
                    self._guild_text("GUILD_CREATE_INVALID", "[弧光核心]输入无效。")
                )
                self.show_guild_create_panel(p)
                return
            if len(data) < 3:
                self.show_guild_create_panel(p)
                return
            name = str(data[1]).strip()
            motto = str(data[2]).strip()
            ok, err = self.guild_system.create_guild(name, str(p.xuid), motto)
            if ok:
                self._notify_important(
                    p,
                    self._guild_text(
                        "GUILD_CREATE_OK",
                        "[弧光核心]公会创建成功，已扣除 {0}。",
                    ).format(self._format_money_display(cost)),
                    title=self._toast_title("GUILD_TOAST_TITLE", "公会"),
                )
                self._update_player_name_tag(p)
                self.show_guild_main_menu(p)
            else:
                p.send_message(self._guild_err(err))
                self.show_guild_create_panel(p)

        form = ModalForm(
            title=self._guild_text("GUILD_CREATE_TITLE", "创建公会"),
            controls=[info, name_in, motto_in],
            on_close=None,
            on_submit=_submit,
        )
        player.send_form(form)

    def show_guild_my_menu(self, player: Player):
        xuid = str(player.xuid)
        mem = self.guild_system.get_membership(xuid)
        if not mem:
            self.show_guild_main_menu(player)
            return
        gid = int(mem["guild_id"])
        role = str(mem.get("role") or "")
        tier = self.guild_system.get_guild_size_tier(gid)
        cap = self.guild_system.get_size_tier_max(tier)
        cur = self.guild_system.count_members(gid)
        personal_contrib = self.guild_system.get_member_contribution(xuid)
        guild_contrib = self.guild_system.get_guild_total_contribution(gid)
        my_content_lines = [
            self._guild_text("GUILD_MY_CONTENT", "管理公会事务。"),
            self._guild_text(
                "GUILD_MY_SIZE_LINE",
                "规模：{0}  人数：{1}/{2}",
            ).format(self._guild_size_tier_label(tier), cur, cap),
            self._guild_text(
                "GUILD_MY_CONTRIB_LINE",
                "公会贡献点：{0}  我的贡献点：{1}",
            ).format(int(guild_contrib), int(personal_contrib)),
        ]
        form = ActionForm(
            title=self._guild_text("GUILD_MY_TITLE", "我的公会"),
            content="\n".join(my_content_lines),
            on_close=None,
        )
        form.add_button(
            self._guild_text("GUILD_BTN_MEMBER_LIST", "成员列表"),
            on_click=lambda p: self.show_guild_member_list_menu(p, readonly=True),
        )
        form.add_button(
            self._guild_text("GUILD_BTN_GUILD_LANDS", "公会领地"),
            on_click=self.show_guild_lands_menu,
        )
        if role in (ROLE_OWNER, ROLE_MANAGER):
            form.add_button(
                self._guild_text("GUILD_BTN_INVITE", "邀请玩家"),
                on_click=self.show_guild_invite_online_pick_menu,
            )
            n_req = self.guild_system.count_join_requests(gid)
            if n_req > 0:
                form.add_button(
                    self._guild_text(
                        "GUILD_BTN_JOIN_REQUESTS", "入会申请 ({0})"
                    ).format(n_req),
                    on_click=self.show_guild_join_requests_menu,
                )
            form.add_button(
                self._guild_text("GUILD_BTN_JOIN_POLICY", "入会审核设置"),
                on_click=lambda p: self.show_guild_join_policy_menu(
                    p, from_my_guild=True
                ),
            )
            form.add_button(
                self._guild_text("GUILD_BTN_KICK", "踢出成员"),
                on_click=self.show_guild_kick_menu,
            )
            if tier != SIZE_TIER_LARGE:
                form.add_button(
                    self._guild_text("GUILD_BTN_UPGRADE_TIER", "升级公会规模"),
                    on_click=self.show_guild_upgrade_tier_menu,
                )
        if role == ROLE_OWNER:
            form.add_button(
                self._guild_text("GUILD_BTN_SET_ROLE", "变更职级"),
                on_click=self.show_guild_set_role_pick_member,
            )
            form.add_button(
                self._guild_text("GUILD_BTN_RENAME", "公会改名"),
                on_click=self.show_guild_rename_panel,
            )
            form.add_button(
                self._guild_text("GUILD_BTN_DISBAND", "解散公会"),
                on_click=self.show_guild_disband_confirm,
            )
        if role != ROLE_OWNER:
            form.add_button(
                self._guild_text("GUILD_BTN_LEAVE", "退出公会"),
                on_click=self.show_guild_leave_confirm,
            )
        form.add_button(
            self._guild_text("RETURN_BUTTON_TEXT", "返回"),
            on_click=self.show_guild_main_menu,
        )
        player.send_form(form)

    def _get_guild_land_teleport_contrib_cost(self) -> int:
        raw = self.setting_manager.GetSetting("GUILD_LAND_TELEPORT_CONTRIB_COST")
        if raw is None or str(raw).strip() == "":
            return 10
        try:
            return max(0, int(str(raw).strip()))
        except (TypeError, ValueError):
            return 10

    def show_guild_lands_menu(self, player: Player):
        xuid = str(player.xuid)
        mem = self.guild_system.get_membership(xuid)
        if not mem:
            self.show_guild_main_menu(player)
            return
        gid = int(mem["guild_id"])
        cost = self._get_guild_land_teleport_contrib_cost()
        personal = self.guild_system.get_member_contribution(xuid)
        lands_map = self.land_system.get_guild_lands(gid)
        if cost > 0:
            hint = self._guild_text(
                "GUILD_LANDS_HINT_COST",
                "每次传送消耗 {0} 点个人公会贡献点（当前 {1}）。费用见配置 GUILD_LAND_TELEPORT_CONTRIB_COST。",
            ).format(int(cost), int(personal))
        else:
            hint = self._guild_text(
                "GUILD_LANDS_HINT_FREE",
                "当前配置为免费传送到公会领地。",
            )
        if lands_map:
            list_intro = hint
        else:
            list_intro = hint + "\n\n" + self._guild_text(
                "GUILD_LANDS_EMPTY", "当前公会还没有公会领地。"
            )
        form = ActionForm(
            title=self._guild_text("GUILD_LANDS_TITLE", "公会领地"),
            content=list_intro,
            on_close=None,
        )
        if not lands_map:
            form.add_button(
                self._guild_text("RETURN_BUTTON_TEXT", "返回"),
                on_click=self.show_guild_my_menu,
            )
            player.send_form(form)
            return
        for lid in sorted(lands_map.keys()):
            info = lands_map.get(lid) or {}
            lname = str(info.get("land_name") or f"#{lid}")
            dim = self.get_land_dimension(int(lid))
            btn = self._guild_text(
                "GUILD_LANDS_ROW",
                "{0}  #{1}  {2}",
            ).format(lname, int(lid), dim)

            def _open(p: Player, land_id: int = int(lid)):
                self.show_guild_land_teleport_confirm(p, land_id)

            form.add_button(btn, on_click=_open)
        form.add_button(
            self._guild_text("RETURN_BUTTON_TEXT", "返回"),
            on_click=self.show_guild_my_menu,
        )
        player.send_form(form)

    def show_guild_land_teleport_confirm(self, player: Player, land_id: int):
        xuid = str(player.xuid)
        mem = self.guild_system.get_membership(xuid)
        if not mem:
            self.show_guild_main_menu(player)
            return
        gid = int(mem["guild_id"])
        info = self.get_land_info(int(land_id))
        if not info:
            player.send_message(
                self._guild_text("GUILD_LAND_TP_INVALID", "[弧光核心]领地不存在。")
            )
            self.show_guild_lands_menu(player)
            return
        ogid = LandSystem.parse_land_owner_guild_id(info.get("owner_xuid"))
        if ogid is None or int(ogid) != gid:
            player.send_message(
                self._guild_text(
                    "GUILD_LAND_TP_NOT_GUILD_LAND",
                    "[弧光核心]该领地不属于本公会。",
                )
            )
            self.show_guild_lands_menu(player)
            return
        cost = self._get_guild_land_teleport_contrib_cost()
        personal = self.guild_system.get_member_contribution(xuid)
        lname = str(info.get("land_name") or "")
        dim = self.get_land_dimension(int(land_id))
        try:
            tpx, tpy, tpz = (
                int(info["tp_x"]),
                int(info["tp_y"]),
                int(info["tp_z"]),
            )
        except (KeyError, TypeError, ValueError):
            player.send_message(
                self._guild_text(
                    "GUILD_LAND_TP_NO_TP",
                    "[弧光核心]该领地未设置传送点。",
                )
            )
            self.show_guild_lands_menu(player)
            return
        if cost > 0:
            cost_block = self._guild_text(
                "GUILD_LAND_TP_CONFIRM_COST",
                "将消耗 {0} 点个人贡献点（当前 {1}）。",
            ).format(int(cost), int(personal))
        else:
            cost_block = self._guild_text(
                "GUILD_LAND_TP_CONFIRM_FREE", "本次传送不消耗贡献点。"
            )
        content = self._guild_text(
            "GUILD_LAND_TP_CONFIRM_CONTENT",
            "领地：{0}\n维度：{1}\n传送点：({2},{3},{4})\n\n{5}\n确定传送？",
        ).format(lname, dim, tpx, tpy, tpz, cost_block)
        form = ActionForm(
            title=self._guild_text("GUILD_LAND_TP_CONFIRM_TITLE", "传送到公会领地"),
            content=content,
            on_close=None,
        )

        def _yes(p: Player, lid: int = int(land_id)):
            self.teleport_to_guild_land_as_member(p, lid)

        form.add_button(
            self._guild_text("GUILD_CONFIRM_YES", "确定"),
            on_click=_yes,
        )
        form.add_button(
            self._guild_text("RETURN_BUTTON_TEXT", "取消"),
            on_click=self.show_guild_lands_menu,
        )
        player.send_form(form)

    def teleport_to_guild_land_as_member(self, player: Player, land_id: int):
        xuid = str(player.xuid)
        mem = self.guild_system.get_membership(xuid)
        if not mem:
            self.show_guild_main_menu(player)
            return
        gid = int(mem["guild_id"])
        info = self.get_land_info(int(land_id))
        if not info:
            player.send_message(
                self._guild_text("GUILD_LAND_TP_INVALID", "[弧光核心]领地不存在。")
            )
            self.show_guild_lands_menu(player)
            return
        ogid = LandSystem.parse_land_owner_guild_id(info.get("owner_xuid"))
        if ogid is None or int(ogid) != gid:
            player.send_message(
                self._guild_text(
                    "GUILD_LAND_TP_NOT_GUILD_LAND",
                    "[弧光核心]该领地不属于本公会。",
                )
            )
            self.show_guild_lands_menu(player)
            return
        cost = self._get_guild_land_teleport_contrib_cost()
        if cost > 0:
            ok_c, err_c, new_p = self.guild_system.consume_member_contribution(
                xuid, cost
            )
            if not ok_c:
                if err_c == "GUILD_CONTRIB_NOT_ENOUGH":
                    cur = self.guild_system.get_member_contribution(xuid)
                    player.send_message(
                        self._guild_text(
                            "GUILD_LAND_TP_CONTRIB_NOT_ENOUGH",
                            "[弧光核心]个人贡献点不足（需要 {0}，当前 {1}）。",
                        ).format(int(cost), int(cur))
                    )
                else:
                    player.send_message(self._guild_err(err_c))
                self.show_guild_land_teleport_confirm(player, int(land_id))
                return
            player.send_message(
                self._guild_text(
                    "GUILD_LAND_TP_CONTRIB_DEDUCTED",
                    "[弧光核心]已消耗 {0} 点个人贡献点（剩余 {1}）。",
                ).format(int(cost), int(new_p))
            )
        tp_target_pos = self.get_land_teleport_point(int(land_id))
        self.run_player_task(
            player,
            lambda p, l_id=int(land_id), pos=tp_target_pos: self.delay_teleport_to_land(
                p, l_id, pos
            ),
            delay=45,
        )
        player.send_message(
            self.language_manager.GetText("READY_TELEPORT_TO_LAND").format(
                int(land_id)
            )
        )

    def show_guild_member_list_menu(self, player: Player, readonly: bool = True):
        mem = self.guild_system.get_membership(str(player.xuid))
        if not mem:
            self.show_guild_main_menu(player)
            return
        gid = int(mem["guild_id"])
        members = self.guild_system.list_members(gid)
        tier = self.guild_system.get_guild_size_tier(gid)
        cap = self.guild_system.get_size_tier_max(tier)
        header = self._guild_text(
            "GUILD_MEMBER_LIST_HEADER",
            "规模：{0}  人数：{1}/{2}",
        ).format(self._guild_size_tier_label(tier), len(members), cap)
        lines = [header]
        for m in members:
            xu = str(m.get("xuid") or "")
            rn = self.get_player_name_by_xuid(xu, return_with_title=False) or xu
            rl = self._guild_text(
                f"GUILD_ROLE_{str(m.get('role') or '').upper()}",
                str(m.get("role") or ""),
            )
            contrib = int(m.get("contribution") or 0)
            lines.append(
                self._guild_text(
                    "GUILD_MEMBER_LIST_ROW",
                    "{0}  [{1}]  贡献：{2}",
                ).format(rn, rl, contrib)
            )
        form = ActionForm(
            title=self._guild_text("GUILD_MEMBER_LIST_TITLE", "成员列表"),
            content="\n".join(lines)
            if members
            else self._guild_text("GUILD_MEMBER_LIST_EMPTY", "暂无成员"),
            on_close=None,
        )
        form.add_button(
            self._guild_text("RETURN_BUTTON_TEXT", "返回"),
            on_click=self.show_guild_my_menu,
        )
        player.send_form(form)

    def show_guild_invite_online_pick_menu(self, player: Player):
        """仅邀请当前在线、且未加入任何公会的玩家；点击后对方弹出确认，不入库邀请表。"""
        mem = self.guild_system.get_membership(str(player.xuid))
        if not mem or mem.get("role") not in (ROLE_OWNER, ROLE_MANAGER):
            player.send_message(self._guild_err("GUILD_NO_PERMISSION"))
            self.show_guild_my_menu(player)
            return
        gid = int(mem["guild_id"])
        try:
            online = list(getattr(self.server, "online_players", []) or [])
        except Exception:
            online = []
        candidates: List[Player] = []
        self_xuid = str(player.xuid)
        for op in online:
            try:
                if str(op.xuid) == self_xuid:
                    continue
                if self.guild_system.get_membership(str(op.xuid)):
                    continue
            except Exception:
                continue
            candidates.append(op)
        candidates.sort(key=lambda pl: (pl.name or "").lower())

        tier = self.guild_system.get_guild_size_tier(gid)
        cap = self.guild_system.get_size_tier_max(tier)
        cur = self.guild_system.count_members(gid)
        capacity_line = self._guild_text(
            "GUILD_INVITE_ONLINE_CAPACITY",
            "当前规模：{0}  人数：{1}/{2}",
        ).format(self._guild_size_tier_label(tier), cur, cap)
        base_content = self._guild_text(
            "GUILD_INVITE_ONLINE_CONTENT",
            "选择一名未加入公会的在线玩家，对方将收到确认窗口。",
        )
        form = ActionForm(
            title=self._guild_text("GUILD_INVITE_ONLINE_TITLE", "邀请在线玩家"),
            content=f"{capacity_line}\n{base_content}",
            on_close=None,
        )
        if cur >= cap:
            form.add_button(
                self._guild_text("GUILD_INVITE_ONLINE_FULL", "公会已满，无法继续邀请"),
                on_click=self.show_guild_my_menu,
            )
            form.add_button(
                self._guild_text("RETURN_BUTTON_TEXT", "返回"),
                on_click=self.show_guild_my_menu,
            )
            player.send_form(form)
            return
        if not candidates:
            form.add_button(
                self._guild_text(
                    "GUILD_INVITE_ONLINE_EMPTY",
                    "当前没有可邀请的在线玩家",
                ),
                on_click=self.show_guild_my_menu,
            )
        else:
            for tgt in candidates:
                label = tgt.name or "?"

                def _pick(inviter: Player, target: Player = tgt, g_id: int = gid):
                    self._guild_send_live_invite(inviter, target, g_id)

                form.add_button(label, on_click=_pick)
        form.add_button(
            self._guild_text("RETURN_BUTTON_TEXT", "返回"),
            on_click=self.show_guild_my_menu,
        )
        player.send_form(form)

    def show_guild_kick_menu(self, player: Player):
        actor_xuid = str(player.xuid)
        mem = self.guild_system.get_membership(actor_xuid)
        if not mem or mem.get("role") not in (ROLE_OWNER, ROLE_MANAGER):
            player.send_message(self._guild_err("GUILD_NO_PERMISSION"))
            self.show_guild_my_menu(player)
            return
        gid = int(mem["guild_id"])
        role = str(mem.get("role") or "")
        members = self.guild_system.list_members(gid)
        targets: List[Dict[str, Any]] = []
        for m in members:
            tx = str(m.get("xuid") or "")
            tr = str(m.get("role") or "")
            if tx == actor_xuid:
                continue
            if tr == ROLE_OWNER:
                continue
            if role == ROLE_MANAGER and tr != ROLE_MEMBER:
                continue
            targets.append(m)
        form = ActionForm(
            title=self._guild_text("GUILD_KICK_TITLE", "踢出成员"),
            content=self._guild_text("GUILD_KICK_CONTENT", "选择要移出公会的成员。"),
            on_close=None,
        )
        if not targets:
            form.add_button(
                self._guild_text("GUILD_KICK_NONE", "暂无可踢出的成员"),
                on_click=self.show_guild_my_menu,
            )
        for m in targets:
            tx = str(m.get("xuid") or "")
            disp = self.get_player_name_by_xuid(tx, return_with_title=False) or tx

            def _kick(p: Player, target: str = tx):
                ok, err = self.guild_system.kick(str(p.xuid), target)
                if ok:
                    p.send_message(
                        self._guild_text("GUILD_KICK_OK", "[弧光核心]已移出该成员。")
                    )
                    self._refresh_player_name_tag_by_xuid(target)
                else:
                    p.send_message(self._guild_err(err))
                self.show_guild_my_menu(p)

            form.add_button(disp, on_click=_kick)
        form.add_button(
            self._guild_text("RETURN_BUTTON_TEXT", "返回"),
            on_click=self.show_guild_my_menu,
        )
        player.send_form(form)

    def show_guild_set_role_pick_member(self, player: Player):
        actor_xuid = str(player.xuid)
        mem = self.guild_system.get_membership(actor_xuid)
        if not mem or mem.get("role") != ROLE_OWNER:
            player.send_message(self._guild_err("GUILD_NO_PERMISSION"))
            self.show_guild_my_menu(player)
            return
        gid = int(mem["guild_id"])
        members = self.guild_system.list_members(gid)
        form = ActionForm(
            title=self._guild_text("GUILD_SET_ROLE_PICK_TITLE", "变更职级"),
            content=self._guild_text(
                "GUILD_SET_ROLE_PICK_CONTENT", "选择一名成员（不含会长）。"
            ),
            on_close=None,
        )
        any_btn = False
        for m in members:
            tx = str(m.get("xuid") or "")
            if tx == actor_xuid:
                continue
            if str(m.get("role") or "") == ROLE_OWNER:
                continue
            any_btn = True
            disp = self.get_player_name_by_xuid(tx, return_with_title=False) or tx

            def _pick(p: Player, target: str = tx):
                self.show_guild_set_role_actions(p, target)

            form.add_button(disp, on_click=_pick)
        if not any_btn:
            form.add_button(
                self._guild_text("GUILD_SET_ROLE_NOBODY", "没有其他成员"),
                on_click=self.show_guild_my_menu,
            )
        form.add_button(
            self._guild_text("RETURN_BUTTON_TEXT", "返回"),
            on_click=self.show_guild_my_menu,
        )
        player.send_form(form)

    def show_guild_set_role_actions(self, player: Player, target_xuid: str):
        form = ActionForm(
            title=self._guild_text("GUILD_SET_ROLE_ACTION_TITLE", "职级操作"),
            content=self._guild_text("GUILD_SET_ROLE_ACTION_CONTENT", "选择新职级。"),
            on_close=None,
        )

        def _set(p: Player, new_r: str):
            ok, err = self.guild_system.set_role(str(p.xuid), target_xuid, new_r)
            if ok:
                p.send_message(
                    self._guild_text("GUILD_SET_ROLE_OK", "[弧光核心]职级已更新。")
                )
            else:
                p.send_message(self._guild_err(err))
            self.show_guild_my_menu(p)

        form.add_button(
            self._guild_text("GUILD_ROLE_PROMOTE_MANAGER", "设为管理者"),
            on_click=lambda p: _set(p, ROLE_MANAGER),
        )
        form.add_button(
            self._guild_text("GUILD_ROLE_DEMOTE_MEMBER", "设为普通成员"),
            on_click=lambda p: _set(p, ROLE_MEMBER),
        )
        form.add_button(
            self._guild_text("RETURN_BUTTON_TEXT", "返回"),
            on_click=self.show_guild_set_role_pick_member,
        )
        player.send_form(form)

    def show_guild_leave_confirm(self, player: Player):
        form = ActionForm(
            title=self._guild_text("GUILD_LEAVE_TITLE", "退出公会"),
            content=self._guild_text(
                "GUILD_LEAVE_CONFIRM", "确定退出当前公会吗？"
            ),
            on_close=None,
        )

        def _yes(p: Player):
            ok, err = self.guild_system.leave(str(p.xuid))
            if ok:
                p.send_message(
                    self._guild_text("GUILD_LEAVE_OK", "[弧光核心]已退出公会。")
                )
                self._update_player_name_tag(p)
            else:
                p.send_message(self._guild_err(err))
            self.show_guild_main_menu(p)

        form.add_button(
            self._guild_text("GUILD_CONFIRM_YES", "确定"),
            on_click=_yes,
        )
        form.add_button(
            self._guild_text("RETURN_BUTTON_TEXT", "取消"),
            on_click=self.show_guild_my_menu,
        )
        player.send_form(form)

    def show_guild_disband_confirm(self, player: Player):
        form = ActionForm(
            title=self._guild_text("GUILD_DISBAND_TITLE", "解散公会"),
            content=self._guild_text(
                "GUILD_DISBAND_CONFIRM",
                "解散后所有成员将被移除，且不可恢复。确定吗？",
            ),
            on_close=None,
        )

        def _yes(p: Player):
            member_xuids: List[str] = []
            try:
                mem = self.guild_system.get_membership(str(p.xuid))
                if mem:
                    for row in self.guild_system.list_members(int(mem["guild_id"])):
                        xu = str(row.get("xuid") or "").strip()
                        if xu:
                            member_xuids.append(xu)
            except Exception:
                member_xuids = []
            ok, err = self.guild_system.disband(str(p.xuid))
            if ok:
                p.send_message(
                    self._guild_text("GUILD_DISBAND_OK", "[弧光核心]公会已解散。")
                )
                for xu in member_xuids:
                    self._refresh_player_name_tag_by_xuid(xu)
            else:
                p.send_message(self._guild_err(err))
            self.show_guild_main_menu(p)

        form.add_button(
            self._guild_text("GUILD_CONFIRM_YES_DISBAND", "确定解散"),
            on_click=_yes,
        )
        form.add_button(
            self._guild_text("RETURN_BUTTON_TEXT", "取消"),
            on_click=self.show_guild_my_menu,
        )
        player.send_form(form)

    # 公会改名（仅会长）
    def _refresh_guild_members_name_tag(self, guild_id: int) -> None:
        """改名/规模升级等场景：刷新该公会全体在线成员的展示名（含头顶名）。"""
        try:
            members = self.guild_system.list_members(int(guild_id))
        except Exception:
            members = []
        for m in members:
            xu = str(m.get("xuid") or "").strip()
            if xu:
                self._refresh_player_name_tag_by_xuid(xu)

    def show_guild_rename_panel(self, player: Player):
        xuid = str(player.xuid)
        mem = self.guild_system.get_membership(xuid)
        if not mem:
            self.show_guild_main_menu(player)
            return
        if str(mem.get("role") or "") != ROLE_OWNER:
            player.send_message(self._guild_err("GUILD_NOT_OWNER"))
            self.show_guild_my_menu(player)
            return
        gid = int(mem["guild_id"])
        g = self.guild_system.get_guild(gid)
        if not g:
            player.send_message(self._guild_err("GUILD_NOT_FOUND"))
            self.show_guild_my_menu(player)
            return
        old_name = guild_strip_mc_color_codes(g.get("name") or "").strip()
        cost = self.guild_system.get_rename_cost()
        cost_line = (
            self._guild_text(
                "GUILD_RENAME_COST_LABEL",
                "改名费用：{0}（将立即扣除）",
            ).format(self._format_money_display(cost))
            if cost > 0
            else self._guild_text("GUILD_RENAME_FREE_LABEL", "改名免费。")
        )
        info = Label(
            text=self._guild_text(
                "GUILD_RENAME_INFO",
                "当前公会名：{0}\n{1}",
            ).format(old_name, cost_line)
        )
        new_name_in = TextInput(
            label=self._guild_text(
                "GUILD_RENAME_INPUT_LABEL",
                "新公会名称（最多8字；禁止 [ ] \" 与 § 颜色/样式符号）",
            ),
            placeholder=self._guild_text(
                "GUILD_RENAME_INPUT_PLACEHOLDER", "请输入新公会名"
            ),
            default_value=old_name,
        )

        def _submit(p: Player, json_str: str):
            try:
                data = json.loads(json_str)
            except Exception:
                p.send_message(
                    self._guild_text("GUILD_CREATE_INVALID", "[弧光核心]输入无效。")
                )
                self.show_guild_rename_panel(p)
                return
            if len(data) < 2:
                self.show_guild_rename_panel(p)
                return
            new_name = str(data[1])
            ok, err, ri = self.guild_system.rename_guild(str(p.xuid), new_name)
            if ok:
                paid = float(ri.get("cost") or 0.0)
                if paid > 0:
                    p.send_message(
                        self._guild_text(
                            "GUILD_RENAME_OK_PAID",
                            "[弧光核心]公会已改名为 {0}（消耗 {1}）。",
                        ).format(
                            ri.get("new_name") or new_name,
                            self._format_money_display(paid),
                        )
                    )
                else:
                    p.send_message(
                        self._guild_text(
                            "GUILD_RENAME_OK",
                            "[弧光核心]公会已改名为 {0}。",
                        ).format(ri.get("new_name") or new_name)
                    )
                self._refresh_guild_members_name_tag(int(ri.get("guild_id") or 0))
                self.show_guild_my_menu(p)
            else:
                p.send_message(self._guild_err(err))
                if err in (
                    "GUILD_NAME_TAKEN",
                    "GUILD_INVALID_NAME",
                    "GUILD_NAME_TOO_LONG",
                    "GUILD_NAME_FORBIDDEN_CHARS",
                    "GUILD_NAME_NO_COLOR_CODES",
                    "GUILD_RENAME_SAME_NAME",
                ):
                    self.show_guild_rename_panel(p)
                else:
                    self.show_guild_my_menu(p)

        form = ModalForm(
            title=self._guild_text("GUILD_RENAME_TITLE", "公会改名"),
            controls=[info, new_name_in],
            on_close=None,
            on_submit=_submit,
        )
        player.send_form(form)

    # 升级公会规模（消耗公共贡献点）
    def show_guild_upgrade_tier_menu(self, player: Player):
        xuid = str(player.xuid)
        mem = self.guild_system.get_membership(xuid)
        if not mem:
            self.show_guild_main_menu(player)
            return
        role = str(mem.get("role") or "")
        if role not in (ROLE_OWNER, ROLE_MANAGER):
            player.send_message(self._guild_err("GUILD_NO_PERMISSION"))
            self.show_guild_my_menu(player)
            return
        gid = int(mem["guild_id"])
        cur_tier = self.guild_system.get_guild_size_tier(gid)
        cur_cap = self.guild_system.get_size_tier_max(cur_tier)
        cur_count = self.guild_system.count_members(gid)
        guild_contrib = self.guild_system.get_guild_total_contribution(gid)

        candidate_tiers = [
            t
            for t in (SIZE_TIER_MEDIUM, SIZE_TIER_LARGE)
            if self.guild_system._tier_rank(t)
            > self.guild_system._tier_rank(cur_tier)
        ]
        content = self._guild_text(
            "GUILD_UPGRADE_TIER_CONTENT",
            "当前规模：{0}（人数 {1}/{2}）\n公会贡献点：{3}\n选择目标规模（消耗公会公共贡献点）：",
        ).format(self._guild_size_tier_label(cur_tier), cur_count, cur_cap, int(guild_contrib))
        form = ActionForm(
            title=self._guild_text("GUILD_UPGRADE_TIER_TITLE", "升级公会规模"),
            content=content,
            on_close=None,
        )

        if not candidate_tiers:
            form.add_button(
                self._guild_text("GUILD_UPGRADE_TIER_AT_MAX", "已是最高规模"),
                on_click=self.show_guild_my_menu,
            )
        else:
            for t in candidate_tiers:
                cap = self.guild_system.get_size_tier_max(t)
                cost = self.guild_system.get_upgrade_cost(t)
                affordable = guild_contrib >= cost
                label_key = (
                    "GUILD_UPGRADE_TIER_BTN_OK"
                    if affordable
                    else "GUILD_UPGRADE_TIER_BTN_LACK"
                )
                default_template = (
                    "{0}（≤{1} 人，需 {2} 贡献点）"
                    if affordable
                    else "{0}（≤{1} 人，需 {2} 贡献点，不足）"
                )
                label = self._guild_text(label_key, default_template).format(
                    self._guild_size_tier_label(t), cap, int(cost)
                )

                def _open(p: Player, target_tier: str = t):
                    self.show_guild_upgrade_tier_confirm(p, target_tier)

                form.add_button(label, on_click=_open)
        form.add_button(
            self._guild_text("RETURN_BUTTON_TEXT", "返回"),
            on_click=self.show_guild_my_menu,
        )
        player.send_form(form)

    def show_guild_upgrade_tier_confirm(self, player: Player, target_tier: str):
        xuid = str(player.xuid)
        mem = self.guild_system.get_membership(xuid)
        if not mem:
            self.show_guild_main_menu(player)
            return
        role = str(mem.get("role") or "")
        if role not in (ROLE_OWNER, ROLE_MANAGER):
            player.send_message(self._guild_err("GUILD_NO_PERMISSION"))
            self.show_guild_my_menu(player)
            return
        target = self.guild_system.normalize_size_tier(target_tier)
        if target not in (SIZE_TIER_MEDIUM, SIZE_TIER_LARGE):
            self.show_guild_upgrade_tier_menu(player)
            return
        gid = int(mem["guild_id"])
        cur_tier = self.guild_system.get_guild_size_tier(gid)
        if self.guild_system._tier_rank(target) <= self.guild_system._tier_rank(cur_tier):
            player.send_message(self._guild_err("GUILD_TIER_NOT_UPGRADABLE"))
            self.show_guild_upgrade_tier_menu(player)
            return
        cap = self.guild_system.get_size_tier_max(target)
        cost = self.guild_system.get_upgrade_cost(target)
        guild_contrib = self.guild_system.get_guild_total_contribution(gid)
        confirm_content = self._guild_text(
            "GUILD_UPGRADE_TIER_CONFIRM",
            "将公会规模升级为：{0}（≤{1} 人）\n消耗公会公共贡献点：{2}\n升级后剩余：{3}\n（操作不可撤销）",
        ).format(
            self._guild_size_tier_label(target),
            cap,
            int(cost),
            int(max(0, guild_contrib - cost)),
        )
        form = ActionForm(
            title=self._guild_text("GUILD_UPGRADE_TIER_CONFIRM_TITLE", "确认升级"),
            content=confirm_content,
            on_close=None,
        )

        def _yes(p: Player, _target: str = target):
            actor_xuid = str(p.xuid)
            ok, err, info = self.guild_system.upgrade_size_tier_with_contribution(
                actor_xuid, _target
            )
            if ok:
                p.send_message(
                    self._guild_text(
                        "GUILD_UPGRADE_TIER_OK",
                        "[弧光核心]公会规模已升级为 {0}（消耗 {1} 贡献点，剩余 {2}）。",
                    ).format(
                        self._guild_size_tier_label(info.get("new_tier") or _target),
                        int(info.get("cost") or 0),
                        int(info.get("guild_total_contribution") or 0),
                    )
                )
                self._refresh_guild_members_name_tag(int(info.get("guild_id") or 0))
            else:
                p.send_message(self._guild_err(err))
            self.show_guild_my_menu(p)

        form.add_button(
            self._guild_text("GUILD_CONFIRM_YES", "确定"),
            on_click=_yes,
        )
        form.add_button(
            self._guild_text("RETURN_BUTTON_TEXT", "取消"),
            on_click=self.show_guild_upgrade_tier_menu,
        )
        player.send_form(form)

    # OP - 公会管理
    def show_op_guild_manage_panel(self, player: Player):
        if not player.is_op:
            player.send_message(self.language_manager.GetText("OP_PANEL_NO_PERMISSION"))
            return
        guilds = self.guild_system.list_guilds_directory("")
        small_max = self.guild_system.get_size_tier_max(SIZE_TIER_SMALL)
        medium_max = self.guild_system.get_size_tier_max(SIZE_TIER_MEDIUM)
        large_max = self.guild_system.get_size_tier_max(SIZE_TIER_LARGE)
        header = self._guild_text(
            "OP_GUILD_MANAGE_HEADER",
            "公会规模门槛：小型≤{0} / 中型≤{1} / 大型≤{2}（在 core_setting.yml 修改）",
        ).format(small_max, medium_max, large_max)
        if not guilds:
            header += "\n" + self._guild_text("OP_GUILD_MANAGE_EMPTY", "目前没有公会。")
        form = ActionForm(
            title=self._guild_text("OP_GUILD_MANAGE_TITLE", "公会管理"),
            content=header,
            on_close=None,
        )
        for g in guilds:
            try:
                gid = int(g.get("id") or 0)
            except (TypeError, ValueError):
                continue
            if gid <= 0:
                continue
            gname = guild_strip_mc_color_codes(g.get("name")).strip()
            tier = self.guild_system.normalize_size_tier(g.get("size_tier"))
            cap = self.guild_system.get_size_tier_max(tier)
            cur = self.guild_system.count_members(gid)
            label = self._guild_text(
                "OP_GUILD_MANAGE_ROW",
                "{0}  规模：{1}  人数：{2}/{3}  贡献点：{4}",
            ).format(
                gname,
                self._guild_size_tier_label(tier),
                cur,
                cap,
                int(g.get("total_contribution") or 0),
            )

            def _open(p: Player, _gid: int = gid):
                self.show_op_guild_detail_panel(p, _gid)

            form.add_button(label, on_click=_open)
        form.add_button(
            self._guild_text("RETURN_BUTTON_TEXT", "返回"),
            on_click=self.show_op_main_panel,
        )
        player.send_form(form)

    def show_op_guild_detail_panel(self, player: Player, guild_id: int):
        if not player.is_op:
            player.send_message(self.language_manager.GetText("OP_PANEL_NO_PERMISSION"))
            return
        g = self.guild_system.get_guild(int(guild_id))
        if not g:
            player.send_message(self._guild_err("GUILD_NOT_FOUND"))
            self.show_op_guild_manage_panel(player)
            return
        gid = int(g.get("id") or guild_id)
        gname = str(g.get("name") or "")
        tier = self.guild_system.normalize_size_tier(g.get("size_tier"))
        cap = self.guild_system.get_size_tier_max(tier)
        cur = self.guild_system.count_members(gid)
        owner_xuid = str(g.get("owner_xuid") or "")
        owner_name = self.get_player_name_by_xuid(owner_xuid, return_with_title=False) or owner_xuid
        info_lines = [
            self._guild_text("OP_GUILD_DETAIL_NAME", "公会：{0}").format(gname),
            self._guild_text("OP_GUILD_DETAIL_OWNER", "会长：{0}").format(owner_name),
            self._guild_text(
                "OP_GUILD_DETAIL_SIZE",
                "规模：{0}  人数：{1}/{2}",
            ).format(self._guild_size_tier_label(tier), cur, cap),
            self._guild_text(
                "OP_GUILD_DETAIL_CONTRIB",
                "公共贡献点：{0}",
            ).format(int(g.get("total_contribution") or 0)),
        ]
        form = ActionForm(
            title=self._guild_text("OP_GUILD_DETAIL_TITLE", "公会详情"),
            content="\n".join(info_lines),
            on_close=None,
        )

        def _change_tier(p: Player, _gid: int = gid):
            self.show_op_guild_change_tier_panel(p, _gid)

        form.add_button(
            self._guild_text("OP_GUILD_BTN_CHANGE_TIER", "调整公会规模"),
            on_click=_change_tier,
        )
        form.add_button(
            self._guild_text("RETURN_BUTTON_TEXT", "返回"),
            on_click=self.show_op_guild_manage_panel,
        )
        player.send_form(form)

    def show_op_guild_change_tier_panel(self, player: Player, guild_id: int):
        if not player.is_op:
            player.send_message(self.language_manager.GetText("OP_PANEL_NO_PERMISSION"))
            return
        g = self.guild_system.get_guild(int(guild_id))
        if not g:
            player.send_message(self._guild_err("GUILD_NOT_FOUND"))
            self.show_op_guild_manage_panel(player)
            return
        gid = int(g.get("id") or guild_id)
        cur_tier = self.guild_system.normalize_size_tier(g.get("size_tier"))
        cur_cap = self.guild_system.get_size_tier_max(cur_tier)
        cur_count = self.guild_system.count_members(gid)
        form = ActionForm(
            title=self._guild_text("OP_GUILD_TIER_TITLE", "调整公会规模"),
            content=self._guild_text(
                "OP_GUILD_TIER_CONTENT",
                "公会：{0}\n当前规模：{1}（人数 {2}/{3}）",
            ).format(g.get("name", ""), self._guild_size_tier_label(cur_tier), cur_count, cur_cap),
            on_close=None,
        )

        def _set_tier(p: Player, target_tier: str, _gid: int = gid):
            cap2 = self.guild_system.get_size_tier_max(target_tier)
            count2 = self.guild_system.count_members(_gid)
            if count2 > cap2:
                p.send_message(
                    self._guild_text(
                        "OP_GUILD_TIER_DOWNGRADE_BLOCK",
                        "[弧光核心]当前人数 {0} 超过目标规模上限 {1}，请先减少成员后再降级。",
                    ).format(count2, cap2)
                )
                self.show_op_guild_detail_panel(p, _gid)
                return
            ok, err = self.guild_system.set_size_tier(_gid, target_tier)
            if ok:
                p.send_message(
                    self._guild_text(
                        "OP_GUILD_TIER_OK",
                        "[弧光核心]已将公会规模设为 {0}（上限 {1}）。",
                    ).format(self._guild_size_tier_label(target_tier), cap2)
                )
                self._refresh_guild_members_name_tag(_gid)
            else:
                p.send_message(self._guild_err(err))
            self.show_op_guild_detail_panel(p, _gid)

        for tier in SIZE_TIERS:
            cap = self.guild_system.get_size_tier_max(tier)
            label = self._guild_text(
                "OP_GUILD_TIER_BTN",
                "{0}（≤{1} 人）",
            ).format(self._guild_size_tier_label(tier), cap)
            if tier == cur_tier:
                label = "✓ " + label
            form.add_button(label, on_click=lambda p, t=tier: _set_tier(p, t))
        form.add_button(
            self._guild_text("RETURN_BUTTON_TEXT", "返回"),
            on_click=lambda p, _gid=gid: self.show_op_guild_detail_panel(p, _gid),
        )
        player.send_form(form)

    # Bank
    def show_bank_main_menu(self, player: Player):
        bank_main_menu = ActionForm(
            title=self.language_manager.GetText('BANK_MAIN_MENU_TITLE'),
            content=self.language_manager.GetText('BANK_MAIN_MENU_BALANCE_CONTENT').format(
                self._format_money_display(self.get_player_money(player))
            )
        )
        bank_main_menu.add_button(self.language_manager.GetText('BANK_MAIN_MENU_TRANSFER_BUTTON_TEXT'), on_click=self.show_transfer_panel)
        bank_main_menu.add_button(self.language_manager.GetText('BANK_MAIN_MENU_MONEY_RANK_BUTTON_TEXT'),on_click=self.show_money_rank_panel)
        # 返回
        bank_main_menu.add_button(self.language_manager.GetText('RETURN_BUTTON_TEXT'),
                                  on_click=self.show_main_menu)
        player.send_form(bank_main_menu)

    def show_transfer_panel(self, player: Player):
        """显示在线玩家选择面板"""

        def open_transfer_panel(p: Player):
            online_players = self.server.online_players
            available_players = [x for x in online_players if x.name != p.name]

            if not available_players:
                no_players_form = ActionForm(
                    title=self.language_manager.GetText('TRANSFER_PANEL_TITLE'),
                    content=self.language_manager.GetText('TRANSFER_NO_ONLINE_PLAYERS_TEXT'),
                    on_close=None,
                )
                no_players_form.add_button(
                    self.language_manager.GetText('RETURN_BUTTON_TEXT'),
                    on_click=self.show_bank_main_menu,
                )
                p.send_form(no_players_form)
                return

            player_select_panel = ActionForm(
                title=self.language_manager.GetText('TRANSFER_PANEL_TITLE'),
                content=self.language_manager.GetText('TRANSFER_SELECT_PLAYER_CONTENT').format(
                    self._format_money_display(self.get_player_money(p))
                )
            )

            for target_player in available_players:
                player_select_panel.add_button(
                    f"{target_player.name}",
                    on_click=lambda sender, target=target_player: self.show_transfer_amount_panel(sender, target)
                )

            player_select_panel.add_button(
                self.language_manager.GetText('RETURN_BUTTON_TEXT'),
                on_click=self.show_bank_main_menu
            )

            p.send_form(player_select_panel)

        self.require_sensitive_password_verified(
            player,
            open_transfer_panel,
            on_cancel=lambda p: self.show_bank_main_menu(p),
        )
    
    def show_transfer_amount_panel(self, player: Player, target_player: Player):
        """显示转账金额输入面板"""
        # 添加信息标签来显示转账信息
        info_label = Label(
            text=self.language_manager.GetText('TRANSFER_PANEL_INFO_LABEL').format(
                target_player.name,
                self._format_money_display(self.get_player_money(player))
            )
        )
        
        money_amount_input = TextInput(
            label=self.language_manager.GetText('TRANSFER_PANEL_MONEY_AMOUNT_INPUT_LABEL'),
            placeholder=self.language_manager.GetText('TRANSFER_PANEL_MONEY_AMOUNT_INPUT_PLACEHOLDER'),
            default_value='0'
        )

        def try_transfer(sender: Player, json_str: str):
            data = json.loads(json_str)
            if self._modal_choice_is_back(data, 0):
                self.show_transfer_panel(sender)
                return
            # 直接使用目标玩家对象和金额进行转账
            error_code, receive_player, amount = self._validate_transfer_data_new(sender, target_player, data[2])
            if error_code == 0:
                if not self.decrease_player_money(sender, amount, notify=False):
                    self.report_arc_error(
                        "BANK12",
                        f"bank transfer decrease failed sender={sender.name!r} receiver={receive_player.name!r} amount={amount!r}",
                        sender,
                    )
                    result_str = self.language_manager.GetText("TRANSFER_FAIL_DB_TEXT").format("BANK12")
                elif not self.increase_player_money(receive_player, amount, notify=False):
                    self.report_arc_error(
                        "BANK13",
                        f"bank transfer increase failed after decrease; attempting rollback sender={sender.name!r} receiver={receive_player.name!r} amount={amount!r}",
                        sender,
                    )
                    if not self.increase_player_money(sender, amount, notify=False):
                        self.report_arc_error(
                            "BANK14",
                            f"bank transfer rollback to sender FAILED sender={sender.name!r} amount={amount!r}",
                            sender,
                        )
                        result_str = self.language_manager.GetText("TRANSFER_FAIL_DB_TEXT").format("BANK14")
                    else:
                        result_str = self.language_manager.GetText("TRANSFER_FAIL_DB_TEXT").format("BANK13")
                else:
                    self._notify_important(
                        receive_player,
                        self.language_manager.GetText('RECEIVE_PLAYER_TRANSFER_MESSAGE').format(
                            sender.name,
                            self._format_money_display(amount),
                            self._format_money_display(self.get_player_money(receive_player))),
                        title=self._toast_title("TRANSFER_RECEIVE_TOAST_TITLE", "收到转账"),
                    )
                    result_str = self.language_manager.GetText('TRANSFER_COMPLETED_HINT_TEXT').format(
                        receive_player.name,
                        self._format_money_display(amount),
                        self._format_money_display(self.get_player_money(sender))
                    )
                    self._notify_important(
                        sender,
                        result_str,
                        title=self._toast_title("TRANSFER_SEND_TOAST_TITLE", "转账成功"),
                    )
            else:
                result_str = self.language_manager.GetText(f'TRANSFER_ERROR_{error_code}_TEXT')
                if error_code == 2:
                    result_str = result_str.format(target_player.name)
            result_form = ActionForm(
                title=self.language_manager.GetText('TRANSFER_RESULT_PANEL_TITLE'),
                content=result_str,
                on_close=None,
            )
            result_form.add_button(
                self.language_manager.GetText('RETURN_BUTTON_TEXT'),
                on_click=self.show_bank_main_menu,
            )
            sender.send_form(result_form)

        transfer_panel = ModalForm(
            title=self.language_manager.GetText('TRANSFER_PANEL_TITLE'),
            controls=[self._modal_nav_dropdown(), info_label, money_amount_input],
            on_close=None,
            on_submit=try_transfer
        )
        player.send_form(transfer_panel)

    def show_small_horn_buy_panel(
        self,
        player: Player,
        hint_message: Optional[str] = None,
        *,
        on_panel_close: Any = None,
    ):
        """显示小喇叭购买面板。on_panel_close 为显式「返回上一级」时的回调，默认回主菜单。"""
        close_cb = on_panel_close if on_panel_close is not None else self.show_main_menu
        panel_title = self.language_manager.GetText('SMALL_HORN_MENU_TITLE')
        if hint_message:
            panel_title = hint_message

        info_label = Label(
            text=self.language_manager.GetText('SMALL_HORN_MENU_CONTENT').format(
                self._format_money_display(self.small_horn_price_per_hour),
                self._format_money_display(self.get_player_money(player))
            )
        )
        valid_hours_input = TextInput(
            label=self.language_manager.GetText('SMALL_HORN_HOURS_INPUT_LABEL'),
            placeholder=self.language_manager.GetText('SMALL_HORN_HOURS_INPUT_PLACEHOLDER'),
            default_value='1'
        )
        content_input = TextInput(
            label=self.language_manager.GetText('SMALL_HORN_CONTENT_INPUT_LABEL'),
            placeholder=self.language_manager.GetText('SMALL_HORN_CONTENT_INPUT_PLACEHOLDER')
        )

        def try_buy_small_horn(sender: Player, json_str: str):
            try:
                data = json.loads(json_str)
            except Exception:
                self.show_small_horn_buy_panel(
                    sender, self.language_manager.GetText('SMALL_HORN_BUY_INVALID_INPUT'), on_panel_close=close_cb
                )
                return

            if len(data) < 4:
                self.show_small_horn_buy_panel(
                    sender, self.language_manager.GetText('SMALL_HORN_BUY_INVALID_INPUT'), on_panel_close=close_cb
                )
                return

            if self._modal_choice_is_back(data, 0):
                close_cb(sender)
                return

            valid_hours_text = str(data[2]).strip()
            content = str(data[3]).strip()
            try:
                valid_hours = int(valid_hours_text)
            except (TypeError, ValueError):
                self.show_small_horn_buy_panel(
                    sender, self.language_manager.GetText('SMALL_HORN_BUY_INVALID_HOURS'), on_panel_close=close_cb
                )
                return

            if valid_hours <= 0:
                self.show_small_horn_buy_panel(
                    sender, self.language_manager.GetText('SMALL_HORN_BUY_INVALID_HOURS'), on_panel_close=close_cb
                )
                return

            if not content:
                self.show_small_horn_buy_panel(
                    sender, self.language_manager.GetText('SMALL_HORN_BUY_EMPTY_CONTENT'), on_panel_close=close_cb
                )
                return

            total_cost = float(valid_hours * self.small_horn_price_per_hour)
            if total_cost > 0 and not self.judge_if_player_has_enough_money(sender, total_cost):
                sender.send_message(
                    self.language_manager.GetText('SMALL_HORN_BUY_NO_MONEY').format(
                        self._format_money_display(total_cost),
                        self._format_money_display(self.get_player_money(sender))
                    )
                )
                self.show_small_horn_buy_panel(sender, on_panel_close=close_cb)
                return

            if total_cost > 0 and not self.decrease_player_money(sender, total_cost, notify=False):
                sender.send_message(self.language_manager.GetText('SMALL_HORN_BUY_PAY_FAILED'))
                self.show_small_horn_buy_panel(sender, on_panel_close=close_cb)
                return

            start_time = datetime.now()
            end_time = start_time + timedelta(hours=valid_hours)
            success = self.database_manager.insert(
                'small_horn_orders',
                {
                    'xuid': str(sender.xuid),
                    'content': content,
                    'start_time': start_time.isoformat(timespec='seconds'),
                    'valid_hours': valid_hours,
                    'end_time': end_time.isoformat(timespec='seconds'),
                    'created_at': start_time.isoformat(timespec='seconds'),
                }
            )
            if not success:
                if total_cost > 0:
                    self.increase_player_money(sender, total_cost)
                sender.send_message(self.language_manager.GetText('SMALL_HORN_BUY_SAVE_FAILED'))
                self.show_small_horn_buy_panel(sender, on_panel_close=close_cb)
                return

            self._notify_important(
                sender,
                self.language_manager.GetText('SMALL_HORN_BUY_SUCCESS').format(
                    valid_hours,
                    self._format_money_display(total_cost),
                    self._format_money_display(self.get_player_money(sender))
                ),
                title=self._toast_title("SMALL_HORN_TOAST_TITLE", "小喇叭"),
            )
            self.show_main_menu(sender)

        buy_form = ModalForm(
            title=panel_title,
            controls=[self._modal_nav_dropdown(), info_label, valid_hours_input, content_input],
            on_close=None,
            on_submit=try_buy_small_horn
        )
        player.send_form(buy_form)

    def _validate_transfer_data(self, player: Player, data: list) -> tuple[int, Optional[Player], Optional[int]]:
        """
        验证转账数据
        :param player: 发起转账的玩家
        :param data: 转账数据[接收玩家名, 金额]
        :return: (错误码, 接收玩家对象, 转账金额)
        """
        # 初始化返回值
        error_code = 0
        receive_player = None
        amount = None

        # 检查数据格式
        if not isinstance(data, list) or len(data) != 2:
            return 1, None, None

        # 获取并检查接收玩家
        receive_player = self.server.get_player(data[0])
        if receive_player is None:
            return 2, None, None

        # 检查是否自己给自己转账
        if receive_player.name == player.name:
            return 6, receive_player, None

        # 检查并转换金额
        try:
            amount = int(data[1])
        except (ValueError, TypeError):
            return 3, receive_player, None

        # 检查金额是否大于0
        if amount <= 0:
            return 5, receive_player, amount

        # 检查玩家余额是否足够
        if not self.judge_if_player_has_enough_money(player, amount):
            return 4, receive_player, amount

        return error_code, receive_player, amount

    def _validate_transfer_data_new(self, player: Player, target_player: Player, amount_str: str) -> tuple[int, Optional[Player], Optional[float]]:
        """
        验证新转账流程的数据
        :param player: 发起转账的玩家
        :param target_player: 目标玩家对象
        :param amount_str: 转账金额字符串（支持小数，精确到分）
        :return: (错误码, 接收玩家对象, 转账金额)
        """
        error_code = 0
        amount = None

        if target_player not in self.server.online_players:
            return 2, target_player, None

        if target_player.name == player.name:
            return 6, target_player, None

        try:
            amount = self._round_money(float(amount_str))
        except (ValueError, TypeError):
            return 3, target_player, None

        if amount <= 0:
            return 5, target_player, amount

        if not self.judge_if_player_has_enough_money(player, amount):
            return 4, target_player, amount

        return error_code, target_player, amount

    def show_money_rank_panel(self, player: Player):
        # 为了在隐藏 OP 时仍能展示足够名次，这里多取一些再在内存中过滤
        initial_count = 20 if self.hide_op_in_money_ranking else 10
        rich_entries = self.get_top_richest_players(initial_count)

        filtered_entries = []
        for entry in rich_entries:
            if self.hide_op_in_money_ranking and entry.get("once_op") is True:
                # 隐藏曾以 OP 登录过的玩家
                continue
            filtered_entries.append(entry)
            if len(filtered_entries) >= 10:
                break

        rank_lines = []
        for index, entry in enumerate(filtered_entries, start=1):
            rank_lines.append(
                self.language_manager.GetText("MONEY_RANK_INFO_TEXT").format(
                    index,
                    entry.get("display_name", ""),
                    self._format_money_display(entry.get("money", 0.0)),
                )
            )

        rank_content = "\n".join(rank_lines)
        player_balance = self._format_money_display(self.get_player_money(player))
        player_rank = self.get_player_money_rank(player)

        rank_panel = ActionForm(
            title=self.language_manager.GetText("MONEY_RANK_PANEL_TITLE"),
            content=rank_content
            + "\n"
            + self.language_manager.GetText("MONEY_RANK_PLYAER_RANK_INFO_TEXT").format(
                player_balance, player_rank
            ),
            on_close=None,
        )
        rank_panel.add_button(
            self.language_manager.GetText('RETURN_BUTTON_TEXT'),
            on_click=self.show_bank_main_menu,
        )
        player.send_form(rank_panel)
    
    def show_arc_achievement_menu(self, player: Player):
        """委托弧光成就插件打开玩家成就菜单。"""
        player.perform_command('ach')

    def _teleport_menu_has_any_feature(self) -> bool:
        """主菜单是否显示传送入口：至少有一项传送子功能开启。"""
        ts = getattr(self, "teleport_system", None)
        if ts is None:
            return False
        return bool(
            getattr(ts, "enable_teleport_public_warp", False)
            or getattr(ts, "enable_teleport_home", False)
            or getattr(ts, "enable_random_teleport", False)
            or getattr(ts, "enable_teleport_death_location", False)
            or getattr(ts, "enable_teleport_player", False)
            or getattr(ts, "enable_teleport_cross_server", False)
        )

    def show_arc_achievement_op_menu(self, player: Player):
        """委托弧光成就插件打开 OP 成就管理。"""
        player.perform_command('achop')

    # Teleport menu
    def _notify_teleport_feature_disabled(self, player: Player) -> None:
        msg = self.language_manager.GetText("TELEPORT_FEATURE_DISABLED")
        if not msg or not str(msg).strip():
            msg = "[弧光核心]该传送功能已被管理员关闭"
        player.send_message(msg)

    def show_teleport_menu(self, player: Player):
        teleport_main_menu = ActionForm(
            title=self.language_manager.GetText('TELEPORT_MAIN_MENU_TITLE'),
            content=self.language_manager.GetText('TELEPORT_MAIN_MENU_CONTENT')
        )
        ts = self.teleport_system
        
        # 公共传送点按钮
        if ts.enable_teleport_public_warp:
            public_warp_text = self.language_manager.GetText('TELEPORT_MAIN_MENU_PUBLIC_WARP_BUTTON')
            if ts.teleport_cost_public_warp > 0:
                public_warp_text = self.language_manager.GetText('TELEPORT_BUTTON_WITH_COST').format(public_warp_text, ts.teleport_cost_public_warp)
            teleport_main_menu.add_button(public_warp_text, on_click=self.show_public_warp_menu)
        
        # 私人传送点按钮
        if ts.enable_teleport_home:
            home_text = self.language_manager.GetText('TELEPORT_MAIN_MENU_HOME_BUTTON')
            if ts.teleport_cost_home > 0:
                home_text = self.language_manager.GetText('TELEPORT_BUTTON_WITH_COST').format(home_text, ts.teleport_cost_home)
            teleport_main_menu.add_button(home_text, on_click=self.show_home_menu)
        
        # 随机传送按钮
        if ts.enable_random_teleport:
            random_text = self.language_manager.GetText('TELEPORT_MAIN_MENU_RANDOM_BUTTON')
            if ts.teleport_cost_random > 0:
                random_text = self.language_manager.GetText('TELEPORT_BUTTON_WITH_COST').format(random_text, ts.teleport_cost_random)
            teleport_main_menu.add_button(random_text, on_click=self.start_random_teleport)
        
        # 如果玩家有死亡位置记录，显示返回死亡地点的按钮
        if ts.enable_teleport_death_location and ts.has_death_location(player.name):
            death_location = ts.get_death_location(player.name)
            death_text = self.language_manager.GetText('TELEPORT_MAIN_MENU_DEATH_LOCATION_BUTTON').format(death_location['dimension'])
            if ts.teleport_cost_death_location > 0:
                death_text = self.language_manager.GetText('TELEPORT_BUTTON_WITH_COST').format(death_text, ts.teleport_cost_death_location)
            teleport_main_menu.add_button(death_text, on_click=self.teleport_to_death_location)
        
        # 玩家传送请求按钮
        if ts.enable_teleport_player:
            player_request_text = self.language_manager.GetText('TELEPORT_MAIN_MENU_PLAYER_REQUEST_BUTTON')
            if ts.teleport_cost_player > 0:
                player_request_text = self.language_manager.GetText('TELEPORT_BUTTON_WITH_COST').format(player_request_text, ts.teleport_cost_player)
            teleport_main_menu.add_button(player_request_text, on_click=self.show_player_teleport_request_menu)

        # 跨服传送按钮
        if ts.enable_teleport_cross_server:
            teleport_main_menu.add_button(
                self.language_manager.GetText('TELEPORT_MAIN_MENU_CROSS_SERVER_BUTTON'),
                on_click=self.show_cross_server_menu
            )
        
        # 返回
        teleport_main_menu.add_button(self.language_manager.GetText('RETURN_BUTTON_TEXT'),
                                      on_click=self.show_main_menu)
        player.send_form(teleport_main_menu)

    # Teleport System（委托 TeleportSystem）
    def create_public_warp(self, warp_name: str, dimension: str, x: float, y: float, z: float, creator_xuid: str) -> bool:
        return self.teleport_system.create_public_warp(warp_name, dimension, x, y, z, creator_xuid)

    def delete_public_warp(self, warp_name: str) -> bool:
        return self.teleport_system.delete_public_warp(warp_name)

    def get_public_warp(self, warp_name: str) -> Optional[Dict[str, Any]]:
        return self.teleport_system.get_public_warp(warp_name)

    def get_all_public_warps(self) -> Dict[str, Dict[str, Any]]:
        return self.teleport_system.get_all_public_warps()

    def public_warp_exists(self, warp_name: str) -> bool:
        return self.teleport_system.public_warp_exists(warp_name)

    def create_player_home(self, owner_xuid: str, home_name: str, dimension: str, x: float, y: float, z: float) -> bool:
        return self.teleport_system.create_player_home(owner_xuid, home_name, dimension, x, y, z)

    def delete_player_home(self, owner_xuid: str, home_name: str) -> bool:
        return self.teleport_system.delete_player_home(owner_xuid, home_name)

    def get_player_home(self, owner_xuid: str, home_name: str) -> Optional[Dict[str, Any]]:
        return self.teleport_system.get_player_home(owner_xuid, home_name)

    def get_player_homes(self, owner_xuid: str) -> Dict[str, Dict[str, Any]]:
        return self.teleport_system.get_player_homes(owner_xuid)

    def get_player_home_count(self, owner_xuid: str) -> int:
        return self.teleport_system.get_player_home_count(owner_xuid)

    def player_home_exists(self, owner_xuid: str, home_name: str) -> bool:
        return self.teleport_system.player_home_exists(owner_xuid, home_name)

    # Teleport System UI
    def show_public_warp_menu(self, player: Player):
        """显示公共传送点菜单"""
        if not self.teleport_system.enable_teleport_public_warp:
            self._notify_teleport_feature_disabled(player)
            return self.show_teleport_menu(player)
        public_warps = self.get_all_public_warps()
        if not public_warps:
            no_warp_panel = ActionForm(
                title=self.language_manager.GetText('PUBLIC_WARP_MENU_TITLE'),
                content=self.language_manager.GetText('PUBLIC_WARP_NO_WARP_CONTENT'),
                on_close=None,
            )
            no_warp_panel.add_button(
                self.language_manager.GetText('RETURN_BUTTON_TEXT'),
                on_click=self.show_teleport_menu,
            )
            player.send_form(no_warp_panel)
            return

        warp_menu = ActionForm(
            title=self.language_manager.GetText('PUBLIC_WARP_MENU_TITLE'),
            content=self.language_manager.GetText('PUBLIC_WARP_MENU_CONTENT').format(len(public_warps)),
            on_close=None,
        )
        
        for warp_name, warp_info in public_warps.items():
            creator_name = self.get_player_name_by_xuid(warp_info['created_by']) or 'Unknown'
            warp_button_text = self.language_manager.GetText('PUBLIC_WARP_BUTTON_TEXT').format(warp_name, warp_info['dimension'], creator_name)
            # 如果公共传送点收费，显示价格
            if self.teleport_system.teleport_cost_public_warp > 0:
                warp_button_text = self.language_manager.GetText('TELEPORT_BUTTON_WITH_COST').format(warp_button_text, self.teleport_system.teleport_cost_public_warp)
            warp_menu.add_button(
                warp_button_text,
                on_click=lambda p=player, w_name=warp_name, w_info=warp_info: self.teleport_to_public_warp(p, w_name, w_info)
            )

        warp_menu.add_button(
            self.language_manager.GetText('RETURN_BUTTON_TEXT'),
            on_click=self.show_teleport_menu,
        )

        player.send_form(warp_menu)

    def _get_all_cross_server_targets(self) -> list[Dict[str, Any]]:
        return self.database_manager.query_all(
            "SELECT id, server_name, server_host, server_port FROM cross_server_targets ORDER BY id ASC"
        )

    def _create_cross_server_target(self, server_name: str, server_host: str, server_port: int) -> bool:
        now_str = datetime.now().isoformat(timespec='seconds')
        return self.database_manager.insert(
            'cross_server_targets',
            {
                'server_name': server_name,
                'server_host': server_host,
                'server_port': int(server_port),
                'created_at': now_str,
            }
        )

    def _update_cross_server_target(self, target_id: int, server_name: str, server_host: str, server_port: int) -> bool:
        return self.database_manager.update(
            'cross_server_targets',
            {
                'server_name': server_name,
                'server_host': server_host,
                'server_port': int(server_port),
            },
            'id = ?',
            (int(target_id),)
        )

    def _delete_cross_server_target(self, target_id: int) -> bool:
        return self.database_manager.delete('cross_server_targets', 'id = ?', (int(target_id),))

    def show_cross_server_menu(self, player: Player, on_close=None):
        """显示跨服传送菜单。back_menu 用于「返回」按钮目标；关闭窗口不会自动跳转。"""
        if not self.teleport_system.enable_teleport_cross_server:
            self._notify_teleport_feature_disabled(player)
            back_menu = on_close if on_close is not None else self.show_teleport_menu
            return back_menu(player)
        back_menu = on_close if on_close is not None else self.show_teleport_menu
        targets = self._get_all_cross_server_targets()
        if not targets:
            no_target_panel = ActionForm(
                title=self.language_manager.GetText('CROSS_SERVER_MENU_TITLE'),
                content=self.language_manager.GetText('CROSS_SERVER_MENU_EMPTY'),
                on_close=None,
            )
            no_target_panel.add_button(
                self.language_manager.GetText('RETURN_BUTTON_TEXT'),
                on_click=back_menu
            )
            player.send_form(no_target_panel)
            return

        cross_server_menu = ActionForm(
            title=self.language_manager.GetText('CROSS_SERVER_MENU_TITLE'),
            content=self.language_manager.GetText('CROSS_SERVER_MENU_CONTENT').format(len(targets)),
            on_close=None,
        )
        for target in targets:
            server_name = str(target.get('server_name') or '').strip()
            server_host = str(target.get('server_host') or '').strip()
            server_port = int(target.get('server_port') or 19132)
            cross_server_menu.add_button(
                self.language_manager.GetText('CROSS_SERVER_TARGET_BUTTON').format(
                    server_name, server_host, server_port
                ),
                on_click=lambda p=player, h=server_host, po=server_port, n=server_name: self.transfer_player_to_server(p, h, po, n)
            )
        cross_server_menu.add_button(
            self.language_manager.GetText('RETURN_BUTTON_TEXT'),
            on_click=back_menu
        )
        player.send_form(cross_server_menu)

    def transfer_player_to_server(self, player: Player, server_host: str, server_port: int, server_name: str):
        """执行跨服传送。"""
        if not self.teleport_system.enable_teleport_cross_server:
            self._notify_teleport_feature_disabled(player)
            return
        try:
            player.send_message(self.language_manager.GetText('CROSS_SERVER_TRANSFER_START').format(server_name))
            player.transfer(server_host, int(server_port))
        except Exception as e:
            self.logger.error(f"[ARC Core]Cross server transfer failed: {str(e)}")
            player.send_message(self.language_manager.GetText('CROSS_SERVER_TRANSFER_FAILED').format(server_name))

    def show_home_menu(self, player: Player):
        """显示玩家传送点菜单"""
        if not self.teleport_system.enable_teleport_home:
            self._notify_teleport_feature_disabled(player)
            return self.show_teleport_menu(player)
        player_homes = self.get_player_homes(str(player.xuid))
        home_count = len(player_homes)
        
        home_menu = ActionForm(
            title=self.language_manager.GetText('HOME_MENU_TITLE'),
            content=self.language_manager.GetText('HOME_MENU_CONTENT').format(home_count, self.teleport_system.max_player_home_num),
            on_close=None,
        )
        
        # 显示现有传送点
        for home_name, home_info in player_homes.items():
            home_menu.add_button(
                self.language_manager.GetText('HOME_BUTTON_TEXT').format(home_name, home_info['dimension']),
                on_click=lambda p=player, h_name=home_name, h_info=home_info: self.show_home_detail_menu(p, h_name, h_info)
            )
        
        # 添加新传送点按钮
        if home_count < self.teleport_system.max_player_home_num:
            home_menu.add_button(
                self.language_manager.GetText('HOME_ADD_NEW_BUTTON'),
                on_click=self.show_create_home_panel
            )

        home_menu.add_button(
            self.language_manager.GetText('RETURN_BUTTON_TEXT'),
            on_click=self.show_teleport_menu,
        )

        player.send_form(home_menu)

    def show_home_detail_menu(self, player: Player, home_name: str, home_info: Dict[str, Any]):
        """显示传送点详情菜单"""
        detail_menu = ActionForm(
            title=self.language_manager.GetText('HOME_DETAIL_MENU_TITLE').format(home_name),
            content=self.language_manager.GetText('HOME_DETAIL_MENU_CONTENT').format(
                home_name,
                home_info['dimension'],
                int(home_info['x']),
                int(home_info['y']),
                int(home_info['z'])
            ),
            on_close=None,
        )

        # 私人传送点传送按钮（显示价格）
        home_teleport_text = self.language_manager.GetText('HOME_TELEPORT_BUTTON')
        if self.teleport_system.teleport_cost_home > 0:
            home_teleport_text = self.language_manager.GetText('TELEPORT_BUTTON_WITH_COST').format(home_teleport_text, self.teleport_system.teleport_cost_home)
        detail_menu.add_button(
            home_teleport_text,
            on_click=lambda p=player, h_name=home_name, h_info=home_info: self.teleport_to_home(p, h_name, h_info)
        )

        detail_menu.add_button(
            self.language_manager.GetText('HOME_DELETE_BUTTON'),
            on_click=lambda p=player, h_name=home_name: self.confirm_delete_home(p, h_name)
        )

        detail_menu.add_button(
            self.language_manager.GetText('RETURN_BUTTON_TEXT'),
            on_click=self.show_home_menu,
        )

        player.send_form(detail_menu)

    def show_create_home_panel(self, player: Player):
        """显示创建传送点面板"""
        home_name_input = TextInput(
            label=self.language_manager.GetText('CREATE_HOME_NAME_INPUT_LABEL'),
            placeholder=self.language_manager.GetText('CREATE_HOME_NAME_INPUT_PLACEHOLDER'),
            default_value=f"{player.name}之家"
        )

        def try_create_home(player: Player, json_str: str):
            data = json.loads(json_str)
            if len(data) < 2:
                player.send_message(self.language_manager.GetText('CREATE_HOME_EMPTY_NAME_ERROR'))
                self.show_create_home_panel(player)
                return
            if self._modal_choice_is_back(data, 0):
                self.show_home_menu(player)
                return
            if not data[1] or not str(data[1]).strip():
                player.send_message(self.language_manager.GetText('CREATE_HOME_EMPTY_NAME_ERROR'))
                self.show_create_home_panel(player)
                return

            home_name = str(data[1]).strip()
            if self.player_home_exists(str(player.xuid), home_name):
                player.send_message(self.language_manager.GetText('CREATE_HOME_NAME_EXISTS_ERROR').format(home_name))
                self.show_create_home_panel(player)
                return
            
            # 创建传送点
            success = self.create_player_home(
                str(player.xuid),
                home_name,
                get_dimension_id(player.location.dimension),
                player.location.x,
                player.location.y,
                player.location.z
            )
            
            if success:
                self._notify_important(
                    player,
                    self.language_manager.GetText('CREATE_HOME_SUCCESS').format(home_name),
                    title=self._toast_title("HOME_TOAST_TITLE", "传送点"),
                )
            else:
                player.send_message(self.language_manager.GetText('CREATE_HOME_FAILED'))
            
            self.show_home_menu(player)

        create_panel = ModalForm(
            title=self.language_manager.GetText('CREATE_HOME_PANEL_TITLE'),
            controls=[self._modal_nav_dropdown(), home_name_input],
            on_close=None,
            on_submit=try_create_home
        )
        
        player.send_form(create_panel)

    def confirm_delete_home(self, player: Player, home_name: str):
        """确认删除传送点"""
        confirm_panel = ActionForm(
            title=self.language_manager.GetText('CONFIRM_DELETE_HOME_TITLE'),
            content=self.language_manager.GetText('CONFIRM_DELETE_HOME_CONTENT').format(home_name),
            on_close=None,
        )

        confirm_panel.add_button(
            self.language_manager.GetText('RETURN_BUTTON_TEXT'),
            on_click=self.show_home_menu,
        )

        confirm_panel.add_button(
            self.language_manager.GetText('CONFIRM_DELETE_HOME_BUTTON'),
            on_click=lambda p=player, h_name=home_name: self.delete_home_confirmed(p, h_name)
        )
        
        player.send_form(confirm_panel)

    def delete_home_confirmed(self, player: Player, home_name: str):
        """确认删除传送点"""
        success = self.delete_player_home(str(player.xuid), home_name)
        if success:
            player.send_message(self.language_manager.GetText('DELETE_HOME_SUCCESS').format(home_name))
        else:
            player.send_message(self.language_manager.GetText('DELETE_HOME_FAILED'))
        self.show_home_menu(player)

    # Teleport Functions
    def teleport_to_public_warp(self, player: Player, warp_name: str, warp_info: Dict[str, Any]):
        """传送到公共传送点"""
        if not self.teleport_system.enable_teleport_public_warp:
            self._notify_teleport_feature_disabled(player)
            return
        # 检查费用
        if self.teleport_system.teleport_cost_public_warp > 0:
            player_money = self.get_player_money(player)
            if player_money < self.teleport_system.teleport_cost_public_warp:
                player.send_message(self.language_manager.GetText('TELEPORT_COST_NOT_ENOUGH_MONEY').format(
                    self._format_money_display(self.teleport_system.teleport_cost_public_warp),
                    self._format_money_display(player_money)
                ))
                return
            
            # 扣除费用
            if not self.decrease_player_money(player, self.teleport_system.teleport_cost_public_warp):
                self.report_arc_error(
                    "TP1",
                    f"teleport_to_public_warp decrease failed warp={warp_name!r} cost={self.teleport_system.teleport_cost_public_warp!r}",
                    player,
                )
                return
        
        self.start_teleport_to_position_countdown(player, warp_name, (warp_info['x'], warp_info['y'], warp_info['z']), 'PUBLIC_WARP', warp_info['dimension'])

    def teleport_to_home(self, player: Player, home_name: str, home_info: Dict[str, Any]):
        """传送到玩家传送点"""
        if not self.teleport_system.enable_teleport_home:
            self._notify_teleport_feature_disabled(player)
            return
        # 检查费用
        if self.teleport_system.teleport_cost_home > 0:
            player_money = self.get_player_money(player)
            if player_money < self.teleport_system.teleport_cost_home:
                player.send_message(self.language_manager.GetText('TELEPORT_COST_NOT_ENOUGH_MONEY').format(
                    self._format_money_display(self.teleport_system.teleport_cost_home),
                    self._format_money_display(player_money)
                ))
                return
            
            # 扣除费用
            if not self.decrease_player_money(player, self.teleport_system.teleport_cost_home):
                self.report_arc_error(
                    "TP2",
                    f"teleport_to_home decrease failed home={home_name!r} cost={self.teleport_system.teleport_cost_home!r}",
                    player,
                )
                return
        
        self.start_teleport_to_position_countdown(player, home_name, (home_info['x'], home_info['y'], home_info['z']), 'HOME', home_info['dimension'])

    def _send_teleport_countdown_titles(
        self, player: Player, subtitle: str, total_ticks: int = 45
    ) -> None:
        """用屏幕 title 显示传送倒计时：大数字 + 说明字幕。"""
        seconds = max(1, (int(total_ticks) + 19) // 20)
        sub = str(subtitle or "")
        for i in range(seconds):
            remaining = seconds - i

            def _make_show(rem: int):
                def _show(p: Player) -> None:
                    try:
                        p.send_title(f"§e{rem}", sub, 0, 25, 5)
                    except Exception:
                        if sub:
                            p.send_message(sub)

                return _show

            self.run_player_task(player, _make_show(remaining), delay=i * 20)

    def start_teleport_to_position_countdown(self, player: Player, destination_name: str, position: tuple, teleport_type: str, dimension: str = 'overworld'):
        """开始传送到位置倒计时"""
        delay = 45
        self.run_player_task(
            player,
            lambda p: self.execute_teleport_to_position(
                p, destination_name, position, teleport_type, dimension
            ),
            delay=delay,
        )

        if teleport_type == 'PUBLIC_WARP':
            message = self.language_manager.GetText('TELEPORT_TO_WARP_COUNTDOWN').format(destination_name)
        elif teleport_type == 'HOME':
            message = self.language_manager.GetText('TELEPORT_TO_HOME_COUNTDOWN').format(destination_name)
        else:
            message = self.language_manager.GetText('TELEPORT_COUNTDOWN').format(destination_name)
        self._send_teleport_countdown_titles(player, message, delay)
    
    def start_teleport_to_player_countdown(self, player: Player, target_player: Player):
        """开始传送到玩家倒计时"""
        delay = 45
        self.run_two_player_task(
            player,
            target_player,
            self.execute_teleport_to_player,
            delay=delay,
        )

        message = self.language_manager.GetText('TELEPORT_COUNTDOWN').format(target_player.name)
        self._send_teleport_countdown_titles(player, message, delay)

    def execute_teleport_to_position(self, player: Player, destination_name: str, position: tuple, teleport_type: str, dimension: str = 'overworld'):
        """执行传送"""
        if teleport_type == 'PUBLIC_WARP':
            message = self.language_manager.GetText('TELEPORT_TO_WARP_SUCCESS').format(destination_name)
        elif teleport_type == 'HOME':
            message = self.language_manager.GetText('TELEPORT_TO_HOME_SUCCESS').format(destination_name)
        else:
            message = self.language_manager.GetText('TELEPORT_SUCCESS').format(destination_name)
        player.send_message(message)
        self.teleport_system.execute_teleport_to_position(player.name, position, dimension)
        try:
            self._sky_eye_log_teleport(
                player,
                destination_name,
                teleport_type=teleport_type,
                dimension=dimension,
                position=position,
            )
        except Exception:
            pass
    
    def execute_teleport_to_player(self, player: Player, target_player: Player):
        """执行传送"""
        message = self.language_manager.GetText('TELEPORT_SUCCESS').format(target_player.name)
        player.send_message(message)
        target_dimension = get_dimension_id(target_player.location.dimension)
        self.teleport_system.execute_teleport_to_player(player.name, target_player.name, target_dimension)
        try:
            self._sky_eye_log_teleport(
                player,
                target_player.name,
                teleport_type="TPA",
                dimension=target_dimension,
            )
        except Exception:
            pass

    # Death Location Teleport
    def teleport_to_death_location(self, player: Player):
        """传送到死亡地点"""
        if not self.teleport_system.enable_teleport_death_location:
            self._notify_teleport_feature_disabled(player)
            return
        if not self.teleport_system.has_death_location(player.name):
            player.send_message(self.language_manager.GetText('NO_DEATH_LOCATION_RECORDED'))
            return
        
        # 检查费用
        if self.teleport_system.teleport_cost_death_location > 0:
            player_money = self.get_player_money(player)
            if player_money < self.teleport_system.teleport_cost_death_location:
                player.send_message(self.language_manager.GetText('TELEPORT_COST_NOT_ENOUGH_MONEY').format(
                    self._format_money_display(self.teleport_system.teleport_cost_death_location),
                    self._format_money_display(player_money)
                ))
                return
            
            # 扣除费用
            if not self.decrease_player_money(player, self.teleport_system.teleport_cost_death_location):
                self.report_arc_error(
                    "TP3",
                    f"teleport_to_death_location decrease failed cost={self.teleport_system.teleport_cost_death_location!r}",
                    player,
                )
                return
        
        death_location = self.teleport_system.get_death_location(player.name)
        
        # 开始传送倒计时
        delay = 45
        self.run_player_task(
            player,
            self.execute_death_location_teleport,
            delay=delay,
        )

        self._send_teleport_countdown_titles(
            player,
            self.language_manager.GetText('TELEPORT_TO_DEATH_LOCATION_COUNTDOWN'),
            delay,
        )

    def execute_death_location_teleport(self, player: Player):
        """执行死亡地点传送"""
        if not self.teleport_system.has_death_location(player.name):
            player.send_message(self.language_manager.GetText('NO_DEATH_LOCATION_RECORDED'))
            return
        death_location = self.teleport_system.get_death_location(player.name)
        position = (death_location['x'], death_location['y'], death_location['z'])
        dimension = death_location['dimension']
        player.send_message(self.language_manager.GetText('TELEPORT_TO_DEATH_LOCATION_SUCCESS'))
        self.teleport_system.execute_teleport_to_position(player.name, position, dimension)
        self.teleport_system.clear_death_location(player.name)

    # Random Teleport System
    def start_random_teleport(self, player: Player):
        """开始随机传送"""
        # 检查功能是否启用
        if not self.teleport_system.enable_random_teleport:
            player.send_message(self.language_manager.GetText('RANDOM_TELEPORT_DISABLED'))
            return
        
        # 检查费用
        if self.teleport_system.teleport_cost_random > 0:
            player_money = self.get_player_money(player)
            if player_money < self.teleport_system.teleport_cost_random:
                player.send_message(self.language_manager.GetText('TELEPORT_COST_NOT_ENOUGH_MONEY').format(
                    self._format_money_display(self.teleport_system.teleport_cost_random),
                    self._format_money_display(player_money)
                ))
                return
            
            # 扣除费用
            if not self.decrease_player_money(player, self.teleport_system.teleport_cost_random):
                self.report_arc_error(
                    "TP4",
                    f"start_random_teleport decrease failed cost={self.teleport_system.teleport_cost_random!r}",
                    player,
                )
                return
        
        # title 倒计时提示 + 延迟执行传送
        delay = 45
        self._send_teleport_countdown_titles(
            player,
            self.language_manager.GetText('RANDOM_TELEPORT_COUNTDOWN'),
            delay,
        )
        self.run_player_task(
            player,
            self.execute_random_teleport,
            delay=delay,
        )
    
    def execute_random_teleport(self, player: Player):
        """执行随机传送"""
        position = self.teleport_system.get_random_teleport_position()
        dimension = 'overworld'
        player.send_message(self.language_manager.GetText('RANDOM_TELEPORT_SUCCESS').format(position[0], position[2]))
        self.teleport_system.execute_teleport_to_position(player.name, position, dimension)
        self.run_player_task(
            player,
            self._apply_slow_falling_effect,
            delay=2,
        )

    def _apply_slow_falling_effect(self, player: Player):
        """给玩家添加羽落效果（随机传送用）"""
        self.teleport_system.apply_slow_falling_effect(player.name)
        player.send_message(self.language_manager.GetText('RANDOM_TELEPORT_SLOW_FALLING_APPLIED'))

    # Player Teleport Request System
    def show_player_teleport_request_menu(self, player: Player):
        """显示玩家传送请求菜单"""
        if not self.teleport_system.enable_teleport_player:
            self._notify_teleport_feature_disabled(player)
            return self.show_teleport_menu(player)
        request_menu = ActionForm(
            title=self.language_manager.GetText('PLAYER_TELEPORT_REQUEST_MENU_TITLE'),
            content=self.language_manager.GetText('PLAYER_TELEPORT_REQUEST_MENU_CONTENT'),
            on_close=None,
        )
        
        send_btn = self.language_manager.GetText('SEND_PLAYER_TP_REQUEST_BUTTON')
        if not send_btn or not str(send_btn).strip():
            send_btn = "发送传送请求"
        request_menu.add_button(
            send_btn,
            on_click=self.show_send_player_teleport_request_form
        )
        
        # 检查是否有待处理的请求
        pending_requests = self.get_pending_requests_for_player(player)
        if pending_requests:
            request_menu.add_button(
                self.language_manager.GetText('HANDLE_PENDING_REQUESTS_BUTTON').format(len(pending_requests)),
                on_click=self.show_pending_requests_menu
            )

        request_menu.add_button(
            self.language_manager.GetText('RETURN_BUTTON_TEXT'),
            on_click=self.show_teleport_menu,
        )

        player.send_form(request_menu)

    def show_send_player_teleport_request_form(self, player: Player):
        """用下拉框选择请求类型与目标玩家。"""
        if not self.teleport_system.enable_teleport_player:
            self._notify_teleport_feature_disabled(player)
            return self.show_teleport_menu(player)
        target_names = [p.name for p in self.server.online_players if p.name != player.name]
        if not target_names:
            no_players_panel = ActionForm(
                title=self.language_manager.GetText('PLAYER_TELEPORT_REQUEST_MENU_TITLE'),
                content=self.language_manager.GetText('NO_OTHER_PLAYERS_ONLINE'),
                on_close=None,
            )
            no_players_panel.add_button(
                self.language_manager.GetText('RETURN_BUTTON_TEXT'),
                on_click=self.show_player_teleport_request_menu,
            )
            player.send_form(no_players_panel)
            return

        type_label = self.language_manager.GetText('PLAYER_TP_REQUEST_TYPE_LABEL') or "请求类型"
        target_label = self.language_manager.GetText('PLAYER_TP_REQUEST_TARGET_LABEL') or "目标玩家"
        type_dropdown = Dropdown(
            label=type_label,
            options=[
                self.language_manager.GetText('SEND_TPA_REQUEST_BUTTON'),
                self.language_manager.GetText('SEND_TPHERE_REQUEST_BUTTON'),
            ],
            default_index=0,
        )
        target_dropdown = Dropdown(
            label=target_label,
            options=target_names,
            default_index=0,
        )

        def on_submit(p: Player, json_str: str):
            try:
                data = json.loads(json_str)
            except Exception:
                return self.show_player_teleport_request_menu(p)
            if self._modal_choice_is_back(data, 0):
                return self.show_player_teleport_request_menu(p)
            try:
                type_idx = int(data[1])
                name_idx = int(data[2])
            except (TypeError, ValueError, IndexError):
                return self.show_player_teleport_request_menu(p)
            if name_idx < 0 or name_idx >= len(target_names):
                return self.show_player_teleport_request_menu(p)
            target = self.server.get_player(target_names[name_idx])
            if not target:
                offline_msg = self.language_manager.GetText('TELEPORT_TARGET_OFFLINE')
                if not offline_msg or not str(offline_msg).strip():
                    offline_msg = "[弧光核心]目标玩家已下线"
                p.send_message(offline_msg)
                return self.show_player_teleport_request_menu(p)
            if type_idx == 1:
                self.send_tphere_request(p, target)
            else:
                self.send_tpa_request(p, target)
            self.show_player_teleport_request_menu(p)

        form = ModalForm(
            title=self.language_manager.GetText('PLAYER_TELEPORT_REQUEST_MENU_TITLE'),
            controls=[self._modal_nav_dropdown(), type_dropdown, target_dropdown],
            on_close=None,
            on_submit=on_submit,
        )
        player.send_form(form)

    def send_tpa_request(self, sender: Player, target: Player):
        """发送TPA请求（请求传送到目标玩家处）；费用在对方接受时从发起者扣除。"""
        if not self.teleport_system.enable_teleport_player:
            self._notify_teleport_feature_disabled(sender)
            return
        if not self.teleport_system.add_request(target.name, 'tpa', sender.name):
            sender.send_message(self.language_manager.GetText('TELEPORT_REQUEST_ALREADY_EXISTS').format(target.name))
            return
        sender.send_message(self.language_manager.GetText('TPA_REQUEST_SENT').format(target.name))
        received = self.language_manager.GetText('TPA_REQUEST_RECEIVED').format(sender.name)
        if not (received and str(received).strip()):
            received = (
                f"[弧光核心]玩家{sender.name}请求传送到你身边。"
                f"可用 /tpa accept 接受，或 /tpa deny 拒绝"
            )
        self._notify_important(
            target,
            received,
            title=self._toast_title("TPA_REQUEST_TOAST_TITLE", "传送请求"),
        )
        target.send_message(received)
        self._send_incoming_teleport_request_form(target)

    def send_tphere_request(self, sender: Player, target: Player):
        """发送TPHERE请求（请求目标玩家传送过来）；费用在对方接受时从发起者扣除。"""
        if not self.teleport_system.enable_teleport_player:
            self._notify_teleport_feature_disabled(sender)
            return
        if not self.teleport_system.add_request(target.name, 'tphere', sender.name):
            sender.send_message(self.language_manager.GetText('TELEPORT_REQUEST_ALREADY_EXISTS').format(target.name))
            return
        sender.send_message(self.language_manager.GetText('TPHERE_REQUEST_SENT').format(target.name))
        received = self.language_manager.GetText('TPHERE_REQUEST_RECEIVED').format(sender.name)
        if not (received and str(received).strip()):
            received = (
                f"[弧光核心]玩家{sender.name}请求将你传送到对方身边。"
                f"可用 /tpa accept 接受，或 /tpa deny 拒绝"
            )
        self._notify_important(
            target,
            received,
            title=self._toast_title("TPA_REQUEST_TOAST_TITLE", "传送请求"),
        )
        target.send_message(received)
        self._send_incoming_teleport_request_form(target)

    def _incoming_teleport_request_form_content(self, request: Dict[str, Any]) -> str:
        sender_name = request.get('sender') or ''
        if request.get('type') == 'tphere':
            return self.language_manager.GetText('TPHERE_INCOMING_REQUEST_FORM_CONTENT').format(sender_name)
        return self.language_manager.GetText('TPA_INCOMING_REQUEST_FORM_CONTENT').format(sender_name)

    def _send_incoming_teleport_request_form(
        self, target_player: Player, return_to_menu: Optional[Callable[[Player], None]] = None
    ):
        """向被请求方弹出接受/拒绝表单；可选 return_to_menu 提供「返回」按钮打开上一级菜单。"""
        pending_requests = self.get_pending_requests_for_player(target_player)
        if not pending_requests:
            return
        request = pending_requests[0]
        request_menu = ActionForm(
            title=self.language_manager.GetText('INCOMING_TP_REQUEST_TITLE'),
            content=self._incoming_teleport_request_form_content(request),
            on_close=None,
        )
        request_menu.add_button(
            self.language_manager.GetText('ACCEPT_REQUEST_BUTTON'),
            on_click=lambda p=target_player: self.accept_teleport_request(p),
        )
        request_menu.add_button(
            self.language_manager.GetText('DENY_REQUEST_BUTTON'),
            on_click=lambda p=target_player: self.deny_teleport_request(p),
        )
        if return_to_menu is not None:
            request_menu.add_button(
                self.language_manager.GetText('RETURN_BUTTON_TEXT'),
                on_click=lambda p=target_player, cb=return_to_menu: cb(p),
            )
        target_player.send_form(request_menu)

    def get_pending_requests_for_player(self, player: Player) -> list:
        """获取玩家的待处理请求"""
        return self.teleport_system.get_pending_requests_for_player(player.name)

    def show_pending_requests_menu(self, player: Player):
        """显示待处理请求菜单（与收到请求时的弹窗文案一致）"""
        pending_requests = self.get_pending_requests_for_player(player)
        if not pending_requests:
            player.send_message(self.language_manager.GetText('NO_PENDING_REQUESTS'))
            self.show_player_teleport_request_menu(player)
            return
        self._send_incoming_teleport_request_form(
            player, return_to_menu=self.show_player_teleport_request_menu
        )

    def accept_teleport_request(self, player: Player):
        """接受传送请求：此时从发起者扣除玩家互传费用（若配置大于 0）。"""
        request = self.teleport_system.get_request(player.name)
        if not request:
            player.send_message(self.language_manager.GetText('NO_PENDING_REQUESTS'))
            return
        if request.get('expire_time', 0) <= time.time():
            self.teleport_system.remove_request(player.name)
            player.send_message(self.language_manager.GetText('TP_REQUEST_EXPIRED'))
            return
        sender = self.server.get_player(request['sender'])
        if not sender:
            player.send_message(self.language_manager.GetText('REQUEST_SENDER_OFFLINE'))
            self.teleport_system.remove_request(player.name)
            return
        teleport_cost = self.teleport_system.teleport_cost_player
        if teleport_cost > 0:
            sender_money = self.get_player_money(sender)
            if sender_money < teleport_cost:
                player.send_message(
                    self.language_manager.GetText('TP_REQUEST_ACCEPT_SENDER_NO_MONEY_TARGET').format(sender.name)
                )
                sender.send_message(
                    self.language_manager.GetText('TELEPORT_COST_NOT_ENOUGH_MONEY').format(
                        self._format_money_display(teleport_cost),
                        self._format_money_display(sender_money),
                    )
                )
                self.teleport_system.remove_request(player.name)
                return
            if not self.decrease_player_money(sender, teleport_cost):
                self.report_arc_error(
                    "TP7",
                    f"accept_teleport_request decrease failed sender={sender.name!r} cost={teleport_cost!r}",
                    sender,
                )
                player.send_message(
                    self.language_manager.GetText('TP_REQUEST_ACCEPT_SENDER_NO_MONEY_TARGET').format(sender.name)
                )
                self.teleport_system.remove_request(player.name)
                return
        if request['type'] == 'tpa':
            self.start_teleport_to_player_countdown(sender, player)
            self._notify_important(
                player,
                self.language_manager.GetText('TPA_REQUEST_ACCEPTED_BY_TARGET').format(sender.name),
                title=self._toast_title("TPA_RESULT_TOAST_TITLE", "传送请求"),
            )
            self._notify_important(
                sender,
                self.language_manager.GetText('TPA_REQUEST_ACCEPTED').format(player.name),
                title=self._toast_title("TPA_RESULT_TOAST_TITLE", "传送请求"),
            )
        else:
            self.start_teleport_to_player_countdown(player, sender)
            self._notify_important(
                player,
                self.language_manager.GetText('TPHERE_REQUEST_ACCEPTED_BY_TARGET').format(sender.name),
                title=self._toast_title("TPA_RESULT_TOAST_TITLE", "传送请求"),
            )
            self._notify_important(
                sender,
                self.language_manager.GetText('TPHERE_REQUEST_ACCEPTED').format(player.name),
                title=self._toast_title("TPA_RESULT_TOAST_TITLE", "传送请求"),
            )
        self.teleport_system.remove_request(player.name)

    def deny_teleport_request(self, player: Player):
        """拒绝传送请求"""
        request = self.teleport_system.get_request(player.name)
        if not request:
            player.send_message(self.language_manager.GetText('NO_PENDING_REQUESTS'))
            return
        sender = self.server.get_player(request['sender'])
        if sender:
            if request['type'] == 'tpa':
                self._notify_important(
                    sender,
                    self.language_manager.GetText('TPA_REQUEST_DENIED').format(player.name),
                    title=self._toast_title("TPA_RESULT_TOAST_TITLE", "传送请求"),
                )
                self._notify_important(
                    player,
                    self.language_manager.GetText('TPA_REQUEST_DENIED_BY_YOU').format(sender.name),
                    title=self._toast_title("TPA_RESULT_TOAST_TITLE", "传送请求"),
                )
            else:
                self._notify_important(
                    sender,
                    self.language_manager.GetText('TPHERE_REQUEST_DENIED').format(player.name),
                    title=self._toast_title("TPA_RESULT_TOAST_TITLE", "传送请求"),
                )
                self._notify_important(
                    player,
                    self.language_manager.GetText('TPHERE_REQUEST_DENIED_BY_YOU').format(sender.name),
                    title=self._toast_title("TPA_RESULT_TOAST_TITLE", "传送请求"),
                )
        self.teleport_system.remove_request(player.name)

    def show_op_teleport_manage_panel(self, player: Player):
        """OP 传送管理：公共传送点与传送相关配置。"""
        panel = ActionForm(
            title=self.language_manager.GetText('OP_TELEPORT_MANAGE_TITLE'),
            content=self.language_manager.GetText('OP_TELEPORT_MANAGE_CONTENT'),
            on_close=None,
        )
        panel.add_button(
            self.language_manager.GetText('OP_TELEPORT_MANAGE_WARP_BUTTON'),
            on_click=self.show_op_warp_manage_menu,
        )
        panel.add_button(
            self.language_manager.GetText('OP_TELEPORT_MANAGE_SETTINGS_BUTTON'),
            on_click=self.show_op_teleport_settings_panel,
        )
        panel.add_button(
            self.language_manager.GetText('OP_TELEPORT_MANAGE_CROSS_SERVER_BUTTON'),
            on_click=self.show_op_cross_server_manage_menu,
        )
        panel.add_button(self.language_manager.GetText('RETURN_BUTTON_TEXT'), on_click=self.show_op_main_panel)
        player.send_form(panel)

    def show_op_teleport_settings_panel(self, player: Player):
        """OP 编辑 core_setting 中与传送相关的参数并立即重载 TeleportSystem。"""
        ts = self.teleport_system

        def _raw(key: str, fallback: str) -> str:
            v = self.setting_manager.GetSetting(key)
            if v is None or str(v).strip() == "":
                return fallback
            return str(v).strip()

        in_max_home = TextInput(
            label=self.language_manager.GetText('OP_TELEPORT_SET_MAX_HOME_LABEL'),
            placeholder="5",
            default_value=_raw("MAX_PLAYER_HOME_NUM", str(ts.max_player_home_num)),
        )
        in_enable_random = TextInput(
            label=self.language_manager.GetText('OP_TELEPORT_SET_ENABLE_RANDOM_LABEL'),
            placeholder="true / false",
            default_value="true" if ts.enable_random_teleport else "false",
        )
        in_cx = TextInput(
            label=self.language_manager.GetText('OP_TELEPORT_SET_CENTER_X_LABEL'),
            placeholder="0",
            default_value=_raw("RANDOM_TELEPORT_CENTER_X", str(ts.random_teleport_center_x)),
        )
        in_cz = TextInput(
            label=self.language_manager.GetText('OP_TELEPORT_SET_CENTER_Z_LABEL'),
            placeholder="0",
            default_value=_raw("RANDOM_TELEPORT_CENTER_Z", str(ts.random_teleport_center_z)),
        )
        in_radius = TextInput(
            label=self.language_manager.GetText('OP_TELEPORT_SET_RADIUS_LABEL'),
            placeholder="4096",
            default_value=_raw("RANDOM_TELEPORT_RADIUS", str(ts.random_teleport_radius)),
        )
        in_cost_warp = TextInput(
            label=self.language_manager.GetText('OP_TELEPORT_SET_COST_PUBLIC_WARP_LABEL'),
            placeholder="0",
            default_value=_raw("TELEPORT_COST_PUBLIC_WARP", str(ts.teleport_cost_public_warp)),
        )
        in_cost_home = TextInput(
            label=self.language_manager.GetText('OP_TELEPORT_SET_COST_HOME_LABEL'),
            placeholder="0",
            default_value=_raw("TELEPORT_COST_HOME", str(ts.teleport_cost_home)),
        )
        in_cost_land = TextInput(
            label=self.language_manager.GetText('OP_TELEPORT_SET_COST_LAND_LABEL'),
            placeholder="0",
            default_value=_raw("TELEPORT_COST_LAND", str(ts.teleport_cost_land)),
        )
        in_cost_death = TextInput(
            label=self.language_manager.GetText('OP_TELEPORT_SET_COST_DEATH_LABEL'),
            placeholder="0",
            default_value=_raw("TELEPORT_COST_DEATH_LOCATION", str(ts.teleport_cost_death_location)),
        )
        in_cost_random = TextInput(
            label=self.language_manager.GetText('OP_TELEPORT_SET_COST_RANDOM_LABEL'),
            placeholder="0",
            default_value=_raw("TELEPORT_COST_RANDOM", str(ts.teleport_cost_random)),
        )
        in_cost_player = TextInput(
            label=self.language_manager.GetText('OP_TELEPORT_SET_COST_PLAYER_LABEL'),
            placeholder="0",
            default_value=_raw("TELEPORT_COST_PLAYER", str(ts.teleport_cost_player)),
        )

        def try_save(p: Player, json_str: str):
            try:
                data = json.loads(json_str)
            except Exception:
                p.send_message(self.language_manager.GetText('OP_TELEPORT_SETTINGS_SAVE_FAIL'))
                return self.show_op_teleport_manage_panel(p)
            if not data or len(data) < 11:
                p.send_message(self.language_manager.GetText('OP_TELEPORT_SETTINGS_SAVE_FAIL'))
                return self.show_op_teleport_manage_panel(p)

            def parse_int_safe(raw_value: str, default_value: int) -> int:
                try:
                    return int(str(raw_value).strip())
                except (ValueError, TypeError):
                    return default_value

            max_home = parse_int_safe(data[0], ts.max_player_home_num)
            enable_raw = str(data[1]).strip().lower()
            enable_random = enable_raw in ("true", "1", "yes", "on")
            cx = parse_int_safe(data[2], ts.random_teleport_center_x)
            cz = parse_int_safe(data[3], ts.random_teleport_center_z)
            radius = parse_int_safe(data[4], ts.random_teleport_radius)
            if radius < 0:
                radius = 0

            self.setting_manager.SetSetting("MAX_PLAYER_HOME_NUM", max_home)
            self.setting_manager.SetSetting("ENABLE_RANDOM_TELEPORT", "true" if enable_random else "false")
            self.setting_manager.SetSetting("RANDOM_TELEPORT_CENTER_X", cx)
            self.setting_manager.SetSetting("RANDOM_TELEPORT_CENTER_Z", cz)
            self.setting_manager.SetSetting("RANDOM_TELEPORT_RADIUS", radius)
            self.setting_manager.SetSetting("TELEPORT_COST_PUBLIC_WARP", parse_int_safe(data[5], ts.teleport_cost_public_warp))
            self.setting_manager.SetSetting("TELEPORT_COST_HOME", parse_int_safe(data[6], ts.teleport_cost_home))
            self.setting_manager.SetSetting("TELEPORT_COST_LAND", parse_int_safe(data[7], ts.teleport_cost_land))
            self.setting_manager.SetSetting("TELEPORT_COST_DEATH_LOCATION", parse_int_safe(data[8], ts.teleport_cost_death_location))
            self.setting_manager.SetSetting("TELEPORT_COST_RANDOM", parse_int_safe(data[9], ts.teleport_cost_random))
            self.setting_manager.SetSetting("TELEPORT_COST_PLAYER", parse_int_safe(data[10], ts.teleport_cost_player))

            self.teleport_system.reload_config()

            result = ActionForm(
                title=self.language_manager.GetText('OP_TELEPORT_SETTINGS_TITLE'),
                content=self.language_manager.GetText('OP_TELEPORT_SETTINGS_SAVED'),
                on_close=None,
            )
            result.add_button(self.language_manager.GetText('RETURN_BUTTON_TEXT'), on_click=self.show_op_teleport_manage_panel)
            p.send_form(result)

        form = ModalForm(
            title=self.language_manager.GetText('OP_TELEPORT_SETTINGS_TITLE'),
            controls=[
                in_max_home,
                in_enable_random,
                in_cx,
                in_cz,
                in_radius,
                in_cost_warp,
                in_cost_home,
                in_cost_land,
                in_cost_death,
                in_cost_random,
                in_cost_player,
            ],
            on_close=None,
            on_submit=try_save,
        )
        player.send_form(form)

    # OP Warp Management
    def show_op_cross_server_manage_menu(self, player: Player):
        """OP 管理跨服传送目标。"""
        targets = self._get_all_cross_server_targets()
        panel = ActionForm(
            title=self.language_manager.GetText('OP_CROSS_SERVER_MANAGE_TITLE'),
            content=self.language_manager.GetText('OP_CROSS_SERVER_MANAGE_CONTENT').format(len(targets)),
            on_close=None,
        )
        panel.add_button(
            self.language_manager.GetText('OP_CROSS_SERVER_ADD_BUTTON'),
            on_click=self.show_op_create_cross_server_panel
        )
        for target in targets:
            target_id = int(target.get('id'))
            panel.add_button(
                self.language_manager.GetText('OP_CROSS_SERVER_ITEM_BUTTON').format(
                    target.get('server_name', ''),
                    target.get('server_host', ''),
                    int(target.get('server_port') or 19132)
                ),
                on_click=lambda p=player, t_id=target_id: self.show_op_cross_server_detail_menu(p, t_id)
            )
        panel.add_button(
            self.language_manager.GetText('RETURN_BUTTON_TEXT'),
            on_click=self.show_op_teleport_manage_panel
        )
        player.send_form(panel)

    def _get_cross_server_target_by_id(self, target_id: int) -> Optional[Dict[str, Any]]:
        return self.database_manager.query_one(
            "SELECT id, server_name, server_host, server_port FROM cross_server_targets WHERE id = ?",
            (int(target_id),)
        )

    def _get_cross_server_target_by_name(self, server_name: str) -> Optional[Dict[str, Any]]:
        server_name_str = str(server_name or "").strip()
        if not server_name_str:
            return None
        return self.database_manager.query_one(
            "SELECT id, server_name, server_host, server_port FROM cross_server_targets "
            "WHERE LOWER(TRIM(server_name)) = LOWER(?) "
            "ORDER BY id ASC LIMIT 1",
            (server_name_str,)
        )

    def show_op_create_cross_server_panel(self, player: Player):
        server_name_input = TextInput(
            label=self.language_manager.GetText('OP_CROSS_SERVER_NAME_LABEL'),
            placeholder=self.language_manager.GetText('OP_CROSS_SERVER_NAME_PLACEHOLDER')
        )
        server_host_input = TextInput(
            label=self.language_manager.GetText('OP_CROSS_SERVER_HOST_LABEL'),
            placeholder=self.language_manager.GetText('OP_CROSS_SERVER_HOST_PLACEHOLDER')
        )
        server_port_input = TextInput(
            label=self.language_manager.GetText('OP_CROSS_SERVER_PORT_LABEL'),
            placeholder=self.language_manager.GetText('OP_CROSS_SERVER_PORT_PLACEHOLDER'),
            default_value='19132'
        )

        def try_create_target(sender: Player, json_str: str):
            try:
                data = json.loads(json_str)
            except Exception:
                sender.send_message(self.language_manager.GetText('OP_CROSS_SERVER_SAVE_FAIL'))
                self.show_op_create_cross_server_panel(sender)
                return

            if len(data) < 3:
                sender.send_message(self.language_manager.GetText('OP_CROSS_SERVER_SAVE_FAIL'))
                self.show_op_create_cross_server_panel(sender)
                return

            server_name = str(data[0]).strip()
            server_host = str(data[1]).strip()
            server_port_text = str(data[2]).strip()
            try:
                server_port = int(server_port_text)
            except (ValueError, TypeError):
                sender.send_message(self.language_manager.GetText('OP_CROSS_SERVER_PORT_INVALID'))
                self.show_op_create_cross_server_panel(sender)
                return

            if not server_name or not server_host:
                sender.send_message(self.language_manager.GetText('OP_CROSS_SERVER_SAVE_FAIL'))
                self.show_op_create_cross_server_panel(sender)
                return
            if server_port <= 0 or server_port > 65535:
                sender.send_message(self.language_manager.GetText('OP_CROSS_SERVER_PORT_INVALID'))
                self.show_op_create_cross_server_panel(sender)
                return

            if not self._create_cross_server_target(server_name, server_host, server_port):
                sender.send_message(self.language_manager.GetText('OP_CROSS_SERVER_SAVE_FAIL'))
                self.show_op_create_cross_server_panel(sender)
                return

            sender.send_message(self.language_manager.GetText('OP_CROSS_SERVER_SAVE_SUCCESS'))
            self.show_op_cross_server_manage_menu(sender)

        form = ModalForm(
            title=self.language_manager.GetText('OP_CROSS_SERVER_CREATE_TITLE'),
            controls=[server_name_input, server_host_input, server_port_input],
            on_close=None,
            on_submit=try_create_target
        )
        player.send_form(form)

    def show_op_cross_server_detail_menu(self, player: Player, target_id: int):
        target = self._get_cross_server_target_by_id(target_id)
        if not target:
            player.send_message(self.language_manager.GetText('OP_CROSS_SERVER_NOT_FOUND'))
            self.show_op_cross_server_manage_menu(player)
            return

        detail_menu = ActionForm(
            title=self.language_manager.GetText('OP_CROSS_SERVER_DETAIL_TITLE').format(target.get('server_name', '')),
            content=self.language_manager.GetText('OP_CROSS_SERVER_DETAIL_CONTENT').format(
                target.get('server_name', ''),
                target.get('server_host', ''),
                int(target.get('server_port') or 19132)
            ),
            on_close=None,
        )
        detail_menu.add_button(
            self.language_manager.GetText('OP_CROSS_SERVER_EDIT_BUTTON'),
            on_click=lambda p=player, t_id=target_id: self.show_op_edit_cross_server_panel(p, t_id)
        )
        detail_menu.add_button(
            self.language_manager.GetText('OP_CROSS_SERVER_DELETE_BUTTON'),
            on_click=lambda p=player, t_id=target_id: self.show_op_delete_cross_server_confirm(p, t_id)
        )
        detail_menu.add_button(
            self.language_manager.GetText('RETURN_BUTTON_TEXT'),
            on_click=self.show_op_cross_server_manage_menu
        )
        player.send_form(detail_menu)

    def show_op_edit_cross_server_panel(self, player: Player, target_id: int):
        target = self._get_cross_server_target_by_id(target_id)
        if not target:
            player.send_message(self.language_manager.GetText('OP_CROSS_SERVER_NOT_FOUND'))
            self.show_op_cross_server_manage_menu(player)
            return

        server_name_input = TextInput(
            label=self.language_manager.GetText('OP_CROSS_SERVER_NAME_LABEL'),
            placeholder=self.language_manager.GetText('OP_CROSS_SERVER_NAME_PLACEHOLDER'),
            default_value=str(target.get('server_name') or '')
        )
        server_host_input = TextInput(
            label=self.language_manager.GetText('OP_CROSS_SERVER_HOST_LABEL'),
            placeholder=self.language_manager.GetText('OP_CROSS_SERVER_HOST_PLACEHOLDER'),
            default_value=str(target.get('server_host') or '')
        )
        server_port_input = TextInput(
            label=self.language_manager.GetText('OP_CROSS_SERVER_PORT_LABEL'),
            placeholder=self.language_manager.GetText('OP_CROSS_SERVER_PORT_PLACEHOLDER'),
            default_value=str(int(target.get('server_port') or 19132))
        )

        def try_edit_target(sender: Player, json_str: str):
            try:
                data = json.loads(json_str)
            except Exception:
                sender.send_message(self.language_manager.GetText('OP_CROSS_SERVER_SAVE_FAIL'))
                self.show_op_edit_cross_server_panel(sender, target_id)
                return
            if len(data) < 3:
                sender.send_message(self.language_manager.GetText('OP_CROSS_SERVER_SAVE_FAIL'))
                self.show_op_edit_cross_server_panel(sender, target_id)
                return

            server_name = str(data[0]).strip()
            server_host = str(data[1]).strip()
            server_port_text = str(data[2]).strip()
            try:
                server_port = int(server_port_text)
            except (ValueError, TypeError):
                sender.send_message(self.language_manager.GetText('OP_CROSS_SERVER_PORT_INVALID'))
                self.show_op_edit_cross_server_panel(sender, target_id)
                return

            if not server_name or not server_host:
                sender.send_message(self.language_manager.GetText('OP_CROSS_SERVER_SAVE_FAIL'))
                self.show_op_edit_cross_server_panel(sender, target_id)
                return
            if server_port <= 0 or server_port > 65535:
                sender.send_message(self.language_manager.GetText('OP_CROSS_SERVER_PORT_INVALID'))
                self.show_op_edit_cross_server_panel(sender, target_id)
                return

            if not self._update_cross_server_target(target_id, server_name, server_host, server_port):
                sender.send_message(self.language_manager.GetText('OP_CROSS_SERVER_SAVE_FAIL'))
                self.show_op_edit_cross_server_panel(sender, target_id)
                return
            sender.send_message(self.language_manager.GetText('OP_CROSS_SERVER_SAVE_SUCCESS'))
            self.show_op_cross_server_detail_menu(sender, target_id)

        form = ModalForm(
            title=self.language_manager.GetText('OP_CROSS_SERVER_EDIT_TITLE'),
            controls=[server_name_input, server_host_input, server_port_input],
            on_close=None,
            on_submit=try_edit_target
        )
        player.send_form(form)

    def show_op_delete_cross_server_confirm(self, player: Player, target_id: int):
        target = self._get_cross_server_target_by_id(target_id)
        if not target:
            player.send_message(self.language_manager.GetText('OP_CROSS_SERVER_NOT_FOUND'))
            self.show_op_cross_server_manage_menu(player)
            return

        panel = ActionForm(
            title=self.language_manager.GetText('OP_CROSS_SERVER_DELETE_CONFIRM_TITLE'),
            content=self.language_manager.GetText('OP_CROSS_SERVER_DELETE_CONFIRM_CONTENT').format(
                target.get('server_name', ''),
                target.get('server_host', ''),
                int(target.get('server_port') or 19132)
            ),
            on_close=None,
        )
        panel.add_button(
            self.language_manager.GetText('OP_CROSS_SERVER_DELETE_CONFIRM_BUTTON'),
            on_click=lambda p=player, t_id=target_id: self._do_delete_cross_server_target(p, t_id)
        )
        panel.add_button(
            self.language_manager.GetText('RETURN_BUTTON_TEXT'),
            on_click=lambda p=player: self.show_op_cross_server_detail_menu(p, target_id)
        )
        player.send_form(panel)

    def _do_delete_cross_server_target(self, player: Player, target_id: int):
        if self._delete_cross_server_target(target_id):
            player.send_message(self.language_manager.GetText('OP_CROSS_SERVER_DELETE_SUCCESS'))
        else:
            player.send_message(self.language_manager.GetText('OP_CROSS_SERVER_DELETE_FAIL'))
        self.show_op_cross_server_manage_menu(player)

    def show_op_warp_manage_menu(self, player: Player):
        """显示OP传送点管理菜单"""
        warp_manage_menu = ActionForm(
            title=self.language_manager.GetText('OP_WARP_MANAGE_MENU_TITLE'),
            content=self.language_manager.GetText('OP_WARP_MANAGE_MENU_CONTENT'),
            on_close=None,
        )
        
        warp_manage_menu.add_button(
            self.language_manager.GetText('OP_CREATE_WARP_BUTTON'),
            on_click=self.show_create_warp_panel
        )
        
        public_warps = self.get_all_public_warps()
        if public_warps:
            warp_manage_menu.add_button(
                self.language_manager.GetText('OP_DELETE_WARP_BUTTON').format(len(public_warps)),
                on_click=self.show_delete_warp_menu
            )
        
        player.send_form(warp_manage_menu)

    def show_create_warp_panel(self, player: Player):
        """显示创建公共传送点面板"""
        warp_name_input = TextInput(
            label=self.language_manager.GetText('CREATE_WARP_NAME_INPUT_LABEL'),
            placeholder=self.language_manager.GetText('CREATE_WARP_NAME_INPUT_PLACEHOLDER')
        )

        def try_create_warp(player: Player, json_str: str):
            data = json.loads(json_str)
            if not data or not data[0].strip():
                player.send_message(self.language_manager.GetText('CREATE_WARP_EMPTY_NAME_ERROR'))
                self.show_create_warp_panel(player)
                return
            
            warp_name = data[0].strip()
            if self.public_warp_exists(warp_name):
                player.send_message(self.language_manager.GetText('CREATE_WARP_NAME_EXISTS_ERROR').format(warp_name))
                self.show_create_warp_panel(player)
                return
            
            # 创建公共传送点
            success = self.create_public_warp(
                warp_name,
                get_dimension_id(player.location.dimension),
                player.location.x,
                player.location.y,
                player.location.z,
                str(player.xuid)
            )
            
            if success:
                player.send_message(self.language_manager.GetText('CREATE_WARP_SUCCESS').format(warp_name))
            else:
                player.send_message(self.language_manager.GetText('CREATE_WARP_FAILED'))
            
            self.show_op_warp_manage_menu(player)

        create_panel = ModalForm(
            title=self.language_manager.GetText('CREATE_WARP_PANEL_TITLE'),
            controls=[warp_name_input],
            on_close=None,
            on_submit=try_create_warp
        )
        
        player.send_form(create_panel)

    def show_delete_warp_menu(self, player: Player):
        """显示删除公共传送点菜单"""
        public_warps = self.get_all_public_warps()
        if not public_warps:
            player.send_message(self.language_manager.GetText('NO_WARPS_TO_DELETE'))
            self.show_op_warp_manage_menu(player)
            return

        delete_menu = ActionForm(
            title=self.language_manager.GetText('DELETE_WARP_MENU_TITLE'),
            content=self.language_manager.GetText('DELETE_WARP_MENU_CONTENT'),
            on_close=None,
        )
        
        for warp_name, warp_info in public_warps.items():
            creator_name = self.get_player_name_by_xuid(warp_info['created_by']) or 'Unknown'
            delete_menu.add_button(
                self.language_manager.GetText('DELETE_WARP_BUTTON_TEXT').format(warp_name, creator_name),
                on_click=lambda p=player, w_name=warp_name: self.confirm_delete_warp(p, w_name)
            )
        
        player.send_form(delete_menu)

    def confirm_delete_warp(self, player: Player, warp_name: str):
        """确认删除公共传送点"""
        confirm_panel = ActionForm(
            title=self.language_manager.GetText('CONFIRM_DELETE_WARP_TITLE'),
            content=self.language_manager.GetText('CONFIRM_DELETE_WARP_CONTENT').format(warp_name),
            on_close=None,
        )
        
        confirm_panel.add_button(
            self.language_manager.GetText('CONFIRM_DELETE_WARP_BUTTON'),
            on_click=lambda p=player, w_name=warp_name: self.delete_warp_confirmed(p, w_name)
        )
        
        player.send_form(confirm_panel)

    def delete_warp_confirmed(self, player: Player, warp_name: str):
        """确认删除公共传送点"""
        success = self.delete_public_warp(warp_name)
        if success:
            player.send_message(self.language_manager.GetText('DELETE_WARP_SUCCESS').format(warp_name))
        else:
            player.send_message(self.language_manager.GetText('DELETE_WARP_FAILED'))
        self.show_delete_warp_menu(player)

    # Land System
    # ─── Land data methods (delegated to LandSystem) ─────────────────────────

    def rebuild_chunk_land_mapping(self) -> tuple:
        """委托 LandSystem 重建区块映射，并将结果格式化为语言文本"""
        success, num_dims, num_lands, err = self.land_system.rebuild_chunk_land_mapping()
        if not success:
            return False, self.language_manager.GetText('OP_REBUILD_CHUNK_MAPPING_FAILED').format(err)
        if num_lands == 0:
            return True, self.language_manager.GetText('OP_REBUILD_CHUNK_MAPPING_NO_LANDS')
        return True, self.language_manager.GetText('OP_REBUILD_CHUNK_MAPPING_SUCCESS').format(num_dims, num_lands)

    def create_land(self, owner_xuid: str, land_name: str, dimension: str,
                    min_x: int, max_x: int, min_y: int, max_y: int, min_z: int, max_z: int,
                    tp_x: float, tp_y: float, tp_z: float, owner_paid_money: float = 0.0,
                    public_priority: int = LandSystem.PUBLIC_PRIORITY_DEFAULT,
                    actor: Optional[Player] = None) -> Optional[int]:
        land_id = self.land_system.create_land(
            owner_xuid, land_name, dimension,
            min_x, max_x, min_y, max_y, min_z, max_z,
            tp_x, tp_y, tp_z, owner_paid_money, public_priority=public_priority
        )
        if land_id:
            try:
                subject = actor or self._find_online_player_by_xuid(str(owner_xuid))
                detail = f"paid={owner_paid_money:.2f};dim={dimension}"
                self._sky_eye_log_land("LandCreate", subject, int(land_id), land_name, detail)
            except Exception:
                pass
        return land_id

    def get_land_at_pos(self, dimension: str, x: int, z: int, y: int = None) -> Optional[int]:
        return self.land_system.get_land_at_pos(dimension, x, z, y)

    def delete_land(self, land_id: int, actor: Optional[Player] = None) -> bool:
        land_name = ""
        owner_xuid = ""
        try:
            info = self.get_land_info(land_id)
            if info:
                land_name = str(info.get("name") or info.get("land_name") or "")
                owner_xuid = str(info.get("owner_xuid") or "")
        except Exception:
            pass
        ok = self.land_system.delete_land(land_id)
        if ok:
            try:
                subject = actor or self._find_online_player_by_xuid(owner_xuid)
                self._sky_eye_log_land("LandDelete", subject, int(land_id), land_name)
            except Exception:
                pass
        return ok

    def check_land_availability(
        self,
        dimension: str,
        min_x: int,
        max_x: int,
        min_y: int,
        max_y: int,
        min_z: int,
        max_z: int,
        exclude_land_ids: Optional[Set[int]] = None,
        creating_public_priority: Optional[int] = None,
        creating_allow_non_public_land: bool = False,
    ) -> tuple:
        return self.land_system.check_land_availability(
            dimension,
            min_x,
            max_x,
            min_y,
            max_y,
            min_z,
            max_z,
            exclude_land_ids,
            creating_public_priority=creating_public_priority,
            creating_allow_non_public_land=creating_allow_non_public_land,
        )

    def create_sub_land(self, parent_land_id: int, owner_xuid: str, sub_land_name: str,
                        min_x: int, max_x: int, min_y: int, max_y: int,
                        min_z: int, max_z: int) -> Optional[int]:
        return self.land_system.create_sub_land(
            parent_land_id, owner_xuid, sub_land_name,
            min_x, max_x, min_y, max_y, min_z, max_z
        )

    def delete_sub_land(self, sub_land_id: int) -> bool:
        return self.land_system.delete_sub_land(sub_land_id)

    def get_sub_land_info(self, sub_land_id: int) -> dict:
        return self.land_system.get_sub_land_info(sub_land_id)

    def get_sub_lands_by_parent(self, parent_land_id: int) -> dict:
        return self.land_system.get_sub_lands_by_parent(parent_land_id)

    def get_sub_lands_by_owner_in_parent(self, parent_land_id: int, owner_xuid: str) -> dict:
        return self.land_system.get_sub_lands_by_owner_in_parent(parent_land_id, owner_xuid)

    def get_sub_land_at_pos(self, parent_land_id: int, x: int, y: int, z: int) -> Optional[int]:
        return self.land_system.get_sub_land_at_pos(parent_land_id, x, y, z)

    def check_sub_land_availability(self, parent_land_id: int,
                                    min_x: int, max_x: int, min_y: int, max_y: int,
                                    min_z: int, max_z: int,
                                    exclude_sub_land_id: int = None) -> tuple:
        return self.land_system.check_sub_land_availability(
            parent_land_id, min_x, max_x, min_y, max_y, min_z, max_z, exclude_sub_land_id
        )

    def add_sub_land_shared_user(self, sub_land_id: int, xuid: str) -> bool:
        return self.land_system.add_sub_land_shared_user(sub_land_id, xuid)

    def remove_sub_land_shared_user(self, sub_land_id: int, xuid: str) -> bool:
        return self.land_system.remove_sub_land_shared_user(sub_land_id, xuid)

    def rename_sub_land(self, sub_land_id: int, new_name: str) -> bool:
        return self.land_system.rename_sub_land(sub_land_id, new_name)

    def get_player_land_count(self, xuid: str) -> int:
        return self.land_system.get_player_land_count(xuid)

    def get_player_lands(self, xuid: str) -> dict:
        return self.land_system.get_player_lands(xuid)

    def get_all_lands(self) -> Dict[int, dict]:
        return self.land_system.get_all_lands()

    def get_land_info(self, land_id: int) -> dict:
        return self.land_system.get_land_info(land_id)

    PUBLIC_LAND_OWNER_XUID = LandSystem.PUBLIC_LAND_OWNER_XUID

    def is_public_land(self, land_id: int) -> bool:
        return self.land_system.is_public_land(land_id)

    def _get_public_land_protected_entities(self) -> Set[str]:
        return self.land_system.get_public_land_protected_entities()

    def format_land_owner_key_display(self, owner_key: str) -> str:
        """将 lands.owner_xuid 展示为可读名称（玩家名 / 公会名 / 公共）。"""
        ok = str(owner_key or "").strip()
        if self.land_system.is_public_land_owner(ok):
            t = self.language_manager.GetText('PUBLIC_LAND_NAME')
            return str(t) if t else ok
        px = LandSystem.parse_land_owner_player_xuid(ok)
        if px is not None:
            return self.get_player_name_by_xuid(px) or ok
        gid = LandSystem.parse_land_owner_guild_id(ok)
        if gid is not None:
            g = self.guild_system.get_guild(gid)
            if g:
                gn = guild_strip_mc_color_codes(g.get("name") or "").strip()
                return gn or f"公会#{gid}"
            return f"公会#{gid}"
        return self.get_player_name_by_xuid(ok) or ok or ''

    def get_land_display_owner_name(self, land_id: int) -> str:
        return self.format_land_owner_key_display(self.land_system.get_land_owner(land_id))

    def _build_land_transition_text(self, land_id: int, is_leaving: bool) -> Optional[str]:
        """构建进入/离开领地的单行提示文本。
        - 私人领地：使用带公会与头衔的玩家展示名
        - 公会领地：使用公会名（去色）
        - 公共领地：仅显示领地名
        无法解析时返回 None。
        """
        try:
            land_name = self.get_land_name(land_id) or ""
            owner_key = str(self.land_system.get_land_owner(land_id) or "").strip()
            if not owner_key:
                return None
            # 公共领地
            if self.land_system.is_public_land_owner(owner_key):
                key_name = "LAND_LEAVE_PUBLIC" if is_leaving else "LAND_ENTER_PUBLIC"
                template = self.language_manager.GetText(key_name) or (
                    "§c已离开公共领地§r §e{0}§r" if is_leaving else "§a已进入公共领地§r §e{0}§r"
                )
                return template.format(land_name)
            # 公会领地
            guild_id = LandSystem.parse_land_owner_guild_id(owner_key)
            if guild_id is not None:
                guild_info = self.guild_system.get_guild(guild_id)
                if guild_info and guild_info.get("name"):
                    guild_name = guild_strip_mc_color_codes(guild_info.get("name") or "").strip()
                    if not guild_name:
                        guild_name = f"公会#{guild_id}"
                else:
                    guild_name = f"公会#{guild_id}"
                key_name = "LAND_LEAVE_GUILD" if is_leaving else "LAND_ENTER_GUILD"
                template = self.language_manager.GetText(key_name) or (
                    "§c已离开公会§r §6{0}§r §c的领地§r §e{1}§r"
                    if is_leaving
                    else "§a已进入公会§r §6{0}§r §a的领地§r §e{1}§r"
                )
                return template.format(guild_name, land_name)
            # 私人领地（带公会与头衔的玩家展示名）
            player_xuid = LandSystem.parse_land_owner_player_xuid(owner_key)
            if player_xuid is None:
                player_xuid = owner_key
            player_display = self.get_player_name_by_xuid(player_xuid, return_with_title=True)
            if not player_display:
                player_display = owner_key
            key_name = "LAND_LEAVE_PRIVATE" if is_leaving else "LAND_ENTER_PRIVATE"
            template = self.language_manager.GetText(key_name) or (
                "§c已离开§r {0}§r §c的领地§r §e{1}§r"
                if is_leaving
                else "§a已进入§r {0}§r §a的领地§r §e{1}§r"
            )
            return template.format(player_display, land_name)
        except Exception as build_error:
            self.logger.warning(
                f"[ARC Core]Failed to build land transition text for land {land_id}: {str(build_error)}"
            )
            return None

    def _op_force_delete_land_refund_xuid(self, owner_key: str) -> Optional[str]:
        """私人领地强制删除时退款目标 XUID（玩家领地→玩家；公会领地→会长）。"""
        ok = str(owner_key or "").strip()
        if self.land_system.is_public_land_owner(ok):
            return None
        px = LandSystem.parse_land_owner_player_xuid(ok)
        if px:
            return px
        gid = LandSystem.parse_land_owner_guild_id(ok)
        if gid is not None:
            g = self.guild_system.get_guild(gid)
            if g:
                ou = str(g.get("owner_xuid") or "").strip()
                return ou or None
        return None

    def get_land_owner(self, land_id: int) -> str:
        return self.land_system.get_land_owner(land_id)

    def set_land_as_public(
        self, land_id: int, public_priority: Optional[int] = None
    ) -> bool:
        return self.land_system.set_land_as_public(
            land_id, public_priority=public_priority
        )

    def rename_land(self, land_id: int, new_name: str) -> tuple:
        success, err = self.land_system.rename_land(land_id, new_name)
        if success:
            return True, "领地名称修改成功"
        return False, err or "修改领地名称时发生错误"

    def set_land_teleport_point(self, land_id: int, x: int, y: int, z: int) -> tuple:
        success, err = self.land_system.set_land_teleport_point(land_id, x, y, z)
        if success:
            return True, "领地传送点设置成功"
        if err == "LAND_NOT_FOUND":
            return False, "领地不存在"
        if err == "TP_POINT_OUT_OF_LAND":
            return False, "传送点必须在领地范围内"
        return False, f"设置传送点时发生错误: {err}"

    def get_land_teleport_point(self, land_id: int) -> Optional[tuple]:
        return self.land_system.get_land_teleport_point(land_id)

    def get_land_dimension(self, land_id: int) -> str:
        return self.land_system.get_land_dimension(land_id)

    def get_land_name(self, land_id: int) -> str:
        return self.land_system.get_land_name(land_id)

    # Land System UI
    def show_land_main_menu(self, player: Player):
        land_main_menu = ActionForm(
            title=self.language_manager.GetText('LAND_MAIN_MENU_TITLE'),
            content=self.language_manager.GetText('LAND_MAIN_MENU_CONTENT').format(
                self.get_player_land_count(str(player.xuid)))
        )
        land_main_menu.add_button(self.language_manager.GetText('LAND_MAIN_MENU_MANAGE_LAND_TEXT'),
                                  on_click=self.show_own_land_menu)
        if self._is_land_claim_allowed():
            land_main_menu.add_button(self.language_manager.GetText('LAND_MAIN_MENU_CREATE_NEW_LAND_TEXT'),
                                      on_click=self.start_interactive_land_creation)
        land_main_menu.add_button(self.language_manager.GetText('LAND_MAIN_MENU_CHECK_CURRENT_LAND_TEXT'),
                                  on_click=self.show_current_land_info)
        # 返回
        land_main_menu.add_button(self.language_manager.GetText('RETURN_BUTTON_TEXT'),
                                  on_click=self.show_main_menu)
        player.send_form(land_main_menu)

    def show_own_land_menu(self, player: Player):
        self.require_sensitive_password_verified(
            player,
            self._show_own_land_menu_impl,
            on_cancel=lambda p: self.show_land_main_menu(p),
        )

    def _show_own_land_menu_impl(self, player: Player) -> None:
        player_land_num = self.get_player_land_count(str(player.xuid))
        if player_land_num == 0:
            own_land_panel = ActionForm(
                title=self.language_manager.GetText('OWN_LAND_PANEL_TITLE'),
                content=self.language_manager.GetText('OWN_LAND_PANEL_NO_LAND_EXIST_CONTENT').format(
                    self.get_player_land_count(str(player.xuid))),
                on_close=None,
            )
            player.send_form(own_land_panel)
            return
        else:
            own_land_panel = ActionForm(
                title=self.language_manager.GetText('OWN_LAND_PANEL_TITLE'),
                on_close=None,
            )
            player_lands = self.get_player_lands(str(player.xuid))
            for land_id in player_lands.keys():
                own_land_panel.add_button(
                    self.language_manager.GetText('OWN_LAND_PANEL_LAND_BUTTON_TEXT').format(
                        land_id,
                        player_lands[land_id]['land_name']
                    ),
                    on_click=lambda p=player, l_id=land_id, l_info=player_lands[land_id]: self.show_own_land_detail_panel(p, l_id, l_info)
                )
            player.send_form(own_land_panel)

    def show_own_land_detail_panel(self, player: Player, land_id: int, land_info: dict):
        # 处理具体领地的详情显示
        if len(land_info['shared_users']):
            shared_user_names = [self.get_player_name_by_xuid(uu_id) for uu_id in land_info['shared_users']]
            shared_user_name_str = '\n'.join(shared_user_names)
        else:
            shared_user_name_str = self.language_manager.GetText('LAND_DETAIL_NO_SHARED_USER_TEXT')
        land_detail_panel = ActionForm(
            title=self.language_manager.GetText('LAND_DETAIL_PANEL_TITLE'),
            content=self.language_manager.GetText('LAND_DETAIL_PANEL_CONTENT').format(
                land_id,
                land_info['land_name'],
                land_info['dimension'],
                (int(land_info['min_x']), int(land_info.get('min_y', 0)), int(land_info['min_z'])),
                (int(land_info['max_x']), int(land_info.get('max_y', 255)), int(land_info['max_z'])),
                (int(land_info['tp_x']), int(land_info['tp_y']), int(land_info['tp_z'])),
                shared_user_name_str
            ),
            on_close=None,
        )
        
        # 领地传送按钮（显示价格）
        land_teleport_text = self.language_manager.GetText('LAND_DETAIL_PANEL_TELEPORT_BUTTON_TEXT')
        if self.teleport_system.teleport_cost_land > 0:
            land_teleport_text = self.language_manager.GetText('TELEPORT_BUTTON_WITH_COST').format(land_teleport_text, self.teleport_system.teleport_cost_land)
        land_detail_panel.add_button(land_teleport_text, on_click=lambda p=player, l_id=land_id: self.teleport_to_land(p, l_id))
        land_detail_panel.add_button(self.language_manager.GetText('LAND_DETAIL_PANEL_RENAME_BUTTON_TEXT'),
                                     on_click=lambda p=player, l_id=land_id: self.show_rename_own_land_panel(p, l_id)
                                     )
        land_detail_panel.add_button(
            self.language_manager.GetText('LAND_RESIZE_REDEMARCATION_BUTTON'),
            on_click=lambda p=player, l_id=land_id: self._player_start_land_resize_redemarcation(p, l_id),
        )
        land_detail_panel.add_button(self.language_manager.GetText('LAND_DETAIL_PANEL_RESET_LAND_TP_POS_BUTTON_TEXT'),
                                     on_click=lambda p=player, l_id=land_id: self.set_player_pos_as_land_tp_pos(p, l_id)
                                     )
        land_detail_panel.add_button(self.language_manager.GetText('LAND_DETAIL_PANEL_MANAGE_AUTH_BUTTON_TEXT'),
                                     on_click=lambda p=player, l_id=land_id: self.show_land_auth_manage_panel(p, l_id)
                                     )
        land_detail_panel.add_button(self.language_manager.GetText('LAND_EXPLOSION_SETTING_BUTTON_TEXT'),
                                     on_click=lambda p=player, l_id=land_id: self.show_land_explosion_setting_panel(p, l_id)
                                     )
        land_detail_panel.add_button(self.language_manager.GetText('LAND_ACTOR_INTERACTION_SETTING_BUTTON_TEXT'),
                                     on_click=lambda p=player, l_id=land_id: self.show_land_actor_interaction_setting_panel(p, l_id)
                                     )
        land_detail_panel.add_button(self.language_manager.GetText('LAND_ACTOR_DAMAGE_SETTING_BUTTON_TEXT'),
                                     on_click=lambda p=player, l_id=land_id: self.show_land_actor_damage_setting_panel(p, l_id)
                                     )
        land_detail_panel.add_button(self.language_manager.GetText('LAND_FRAME_SETTING_BUTTON_TEXT'),
                                     on_click=lambda p=player, l_id=land_id: self.show_land_frame_setting_panel(p, l_id)
                                     )
        owner_key_detail = str(land_info.get("owner_xuid") or "")
        if LandSystem.parse_land_owner_guild_id(owner_key_detail) is None:
            land_detail_panel.add_button(
                self.language_manager.GetText('LAND_PUBLIC_INTERACT_SETTING_BUTTON_TEXT'),
                on_click=lambda p=player, l_id=land_id: self.show_land_public_interact_setting_panel(p, l_id),
            )
            land_detail_panel.add_button(
                self.language_manager.GetText('LAND_GUILD_MEMBER_INTERACT_SETTING_BUTTON_TEXT'),
                on_click=lambda p=player, l_id=land_id: self.show_land_guild_member_interact_setting_panel(p, l_id),
            )
        land_detail_panel.add_button(self.language_manager.GetText('LAND_DETAIL_PANEL_MANAGE_SUB_LAND_BUTTON_TEXT'),
                                     on_click=lambda p=player, l_id=land_id: self.show_sub_land_manage_panel(p, l_id)
                                     )
        land_detail_panel.add_button(self.language_manager.GetText('LAND_DETAIL_PANEL_TRANSFER_LAND_BUTTON_TEXT'),
                                     on_click=lambda p=player, l_id=land_id: self.show_transfer_land_panel(p, l_id)
                                     )
        land_detail_panel.add_button(
            self.language_manager.GetText('LAND_DETAIL_PANEL_SALE_MODE_BUTTON_TEXT'),
            on_click=lambda p=player, l_id=land_id: self.show_land_sale_mode_panel(p, l_id),
        )
        land_detail_panel.add_button(self.language_manager.GetText('LAND_DETAIL_PANEL_DELETE_LAND_BUTTON_TEXT'),
                                     on_click=lambda p=player, l_id=land_id: self.confirm_delete_land(p, l_id)
                                     )
        player.send_form(land_detail_panel)

    def show_land_sale_mode_panel(self, player: Player, land_id: int):
        """私人领地上架/改价/下架出售"""
        land_info = self.get_land_info(land_id)
        if not land_info:
            player.send_message(self.language_manager.GetText('LAND_SALE_PANEL_LAND_MISSING'))
            return
        owner_key = str(land_info.get("owner_xuid") or "")
        if LandSystem.parse_land_owner_player_xuid(owner_key) != str(player.xuid):
            player.send_message(self.language_manager.GetText('LAND_SALE_PANEL_NOT_OWNER'))
            return
        for_sale = bool(land_info.get("for_sale"))
        price = float(land_info.get("sale_price") or 0)
        if for_sale and price > 0:
            content = self.language_manager.GetText('LAND_SALE_MODE_PANEL_ON').format(
                self._format_money_display(self._round_money(price))
            )
        else:
            content = self.language_manager.GetText('LAND_SALE_MODE_PANEL_OFF')
        form = ActionForm(
            title=self.language_manager.GetText('LAND_SALE_MODE_PANEL_TITLE'),
            content=content,
            on_close=None,
        )
        if for_sale and price > 0:
            form.add_button(
                self.language_manager.GetText('LAND_SALE_MODE_CHANGE_PRICE_BUTTON'),
                on_click=lambda p=player, lid=land_id: self.show_land_sale_set_price_modal(p, lid),
            )
            form.add_button(
                self.language_manager.GetText('LAND_SALE_MODE_UNLIST_BUTTON'),
                on_click=lambda p=player, lid=land_id: self._try_clear_land_sale_listing(p, lid),
            )
        else:
            form.add_button(
                self.language_manager.GetText('LAND_SALE_MODE_LIST_BUTTON'),
                on_click=lambda p=player, lid=land_id: self.show_land_sale_set_price_modal(p, lid),
            )
        form.add_button(
            self.language_manager.GetText('RETURN_BUTTON_TEXT'),
            on_click=lambda p=player, l_id=land_id, li=self.get_land_info(land_id): self.show_own_land_detail_panel(
                p, l_id, li
            ),
        )
        player.send_form(form)

    def show_land_sale_set_price_modal(self, player: Player, land_id: int):
        land_info = self.get_land_info(land_id)
        cur = float(land_info.get("sale_price") or 0) if land_info else 0.0
        cur = self._round_money(cur)
        default_s = str(int(cur)) if cur > 0 else "1000"
        price_in = TextInput(
            label=self.language_manager.GetText('LAND_SALE_PRICE_INPUT_LABEL'),
            placeholder=self.language_manager.GetText('LAND_SALE_PRICE_INPUT_PLACEHOLDER'),
            default_value=default_s,
        )

        def on_submit(p: Player, json_str: str):
            try:
                data = json.loads(json_str)
                raw = (data[0] or "").strip()
                v = float(raw)
            except (ValueError, TypeError, IndexError, json.JSONDecodeError):
                p.send_message(self.language_manager.GetText('LAND_SALE_PRICE_INVALID'))
                self.show_land_sale_mode_panel(p, land_id)
                return
            v = self._round_money(v)
            if v <= 0:
                p.send_message(self.language_manager.GetText('LAND_SALE_PRICE_INVALID'))
                self.show_land_sale_mode_panel(p, land_id)
                return
            if not self.land_system.set_land_sale_listing(land_id, True, v):
                p.send_message(self.language_manager.GetText('LAND_SALE_LISTING_FAIL'))
            else:
                self._notify_important(
                    p,
                    self.language_manager.GetText('LAND_SALE_LISTING_OK').format(self._format_money_display(v)),
                    title=self._toast_title("LAND_RESULT_TOAST_TITLE", "领地"),
                )
            self.show_land_sale_mode_panel(p, land_id)

        modal = ModalForm(
            title=self.language_manager.GetText('LAND_SALE_PRICE_MODAL_TITLE'),
            controls=[price_in],
            on_close=None,
            on_submit=on_submit,
        )
        player.send_form(modal)

    def _try_clear_land_sale_listing(self, player: Player, land_id: int):
        if self.land_system.set_land_sale_listing(land_id, False, 0.0):
            player.send_message(self.language_manager.GetText('LAND_SALE_UNLIST_OK'))
        else:
            player.send_message(self.language_manager.GetText('LAND_SALE_UNLIST_FAIL'))
        self.show_land_sale_mode_panel(player, land_id)

    def _show_land_purchase_offer_form(
        self, buyer: Player, land_id: int, land_name: str, land_info: dict
    ):
        owner_disp = self.get_land_display_owner_name(land_id)
        price = self._round_money(float(land_info.get("sale_price") or 0))
        price_s = self._format_money_display(price)
        content = self.language_manager.GetText('LAND_SALE_OFFER_FORM_CONTENT').format(
            land_name, land_id, price_s, owner_disp
        )
        form = ActionForm(
            title=self.language_manager.GetText('LAND_SALE_OFFER_FORM_TITLE'),
            content=content,
            on_close=None,
        )
        form.add_button(
            self.language_manager.GetText('LAND_SALE_OFFER_BUY_BUTTON'),
            on_click=lambda p=buyer, lid=land_id, pr=price: self._try_purchase_listed_land(p, lid, pr),
        )
        form.add_button(
            self.language_manager.GetText('RETURN_BUTTON_TEXT'),
            on_click=lambda p=buyer: p.send_message(self.language_manager.GetText('LAND_SALE_OFFER_CLOSED')),
        )
        buyer.send_form(form)

    def _try_purchase_listed_land(self, buyer: Player, land_id: int, expected_price: float):
        land_info = self.get_land_info(land_id)
        if not land_info:
            buyer.send_message(self.language_manager.GetText('LAND_SALE_BUY_FAIL_NOT_FOUND'))
            return
        seller_key = str(land_info.get("owner_xuid") or "")
        seller_px = LandSystem.parse_land_owner_player_xuid(seller_key)
        if seller_px is None:
            buyer.send_message(self.language_manager.GetText('LAND_SALE_BUY_FAIL_NOT_PLAYER_LAND'))
            return
        if str(seller_px) == str(buyer.xuid):
            buyer.send_message(self.language_manager.GetText('LAND_SALE_BUY_FAIL_SELF'))
            return
        if not land_info.get("for_sale"):
            buyer.send_message(self.language_manager.GetText('LAND_SALE_BUY_FAIL_OFF_MARKET'))
            return
        price = self._round_money(float(land_info.get("sale_price") or 0))
        exp = self._round_money(float(expected_price))
        if price <= 0 or abs(price - exp) > 1e-6:
            buyer.send_message(self.language_manager.GetText('LAND_SALE_BUY_FAIL_PRICE_CHANGED'))
            return
        buyer_money = self.get_player_money(buyer)
        if buyer_money < price:
            buyer.send_message(
                self.language_manager.GetText('LAND_SALE_BUY_FAIL_NO_MONEY').format(
                    self._format_money_display(price),
                    self._format_money_display(buyer_money),
                )
            )
            return
        if not self.decrease_player_money(buyer, price, notify=False):
            self.report_arc_error(
                "LAND_SALE1",
                f"_try_purchase_listed_land decrease failed buyer={buyer.name!r} land_id={land_id!r} price={price!r}",
                buyer,
            )
            buyer.send_message(self.language_manager.GetText('LAND_SALE_BUY_FAIL_PAY'))
            return
        buyer_key = LandSystem.land_owner_key_player(str(buyer.xuid))
        ok = self.land_system.transfer_land_purchase(land_id, buyer_key, seller_key, price)
        if not ok:
            if not self.increase_player_money(buyer, price, notify=False):
                self.report_arc_error(
                    "LAND_SALE2",
                    f"_try_purchase_listed_land refund after transfer fail FAILED buyer={buyer.name!r} amount={price!r}",
                    buyer,
                )
            buyer.send_message(self.language_manager.GetText('LAND_SALE_BUY_FAIL_TRANSFER'))
            return
        buy_in = self._round_money(float(land_info.get("owner_paid_money") or 0))
        profit = max(0.0, self._round_money(price - buy_in))
        vat_rate = float(self.land_sale_vat_rate)
        vat = (
            self._round_money(profit * vat_rate)
            if profit > 0 and vat_rate > 0
            else 0.0
        )
        seller_net = self._round_money(max(0.0, price - vat))
        pay_ok = True
        if seller_net > 0:
            pay_ok = self.increase_player_money_by_xuid(
                seller_px, seller_net, notify=False
            )
        if not pay_ok:
            self.report_arc_error(
                "LAND_SALE3",
                f"_try_purchase_listed_land seller credit failed; reverting land land_id={land_id!r} seller={seller_px!r} net={seller_net!r} gross={price!r}",
                buyer,
            )
            if self.land_system.transfer_land(land_id, seller_key):
                if not self.increase_player_money(buyer, price, notify=False):
                    self.report_arc_error(
                        "LAND_SALE4",
                        f"_try_purchase_listed_land refund after revert FAILED buyer={buyer.name!r} amount={price!r}",
                        buyer,
                    )
                buyer.send_message(self.language_manager.GetText('LAND_SALE_BUY_FAIL_SELLER_PAY_ROLLBACK'))
            else:
                buyer.send_message(self.language_manager.GetText('LAND_SALE_BUY_FAIL_SELLER_PAY_CRITICAL'))
            return
        ln = land_info.get("land_name", str(land_id))
        self._notify_important(
            buyer,
            self.language_manager.GetText('LAND_SALE_BUY_SUCCESS_BUYER').format(
                ln, self._format_money_display(price)
            ),
            title=self._toast_title("LAND_RESULT_TOAST_TITLE", "领地"),
        )
        seller_online = self._find_online_player_by_xuid(seller_px)
        if seller_online:
            self._notify_important(
                seller_online,
                self.language_manager.GetText('LAND_SALE_BUY_SUCCESS_SELLER').format(
                    buyer.name,
                    ln,
                    self._format_money_display(price),
                    self._format_money_display(vat),
                    self._format_money_display(seller_net),
                ),
                title=self._toast_title("LAND_RESULT_TOAST_TITLE", "领地"),
            )

    def show_rename_own_land_panel(self, player: Player, land_id: int):
        new_name_input = TextInput(
            label=self.language_manager.GetText('RENAME_OWN_LAND_PANEL_INPUT_LABEL').format(land_id),
            placeholder=self.language_manager.GetText('RENAME_OWN_LAND_PANEL_INPUT_PLACEHOLDER').format(player.name),
            default_value=self.language_manager.GetText('RENAME_OWN_LAND_PANEL_INPUT_PLACEHOLDER').format(player.name)
        )

        def try_change_name(player: Player, json_str: str):
            data = json.loads(json_str)
            self.rename_land(land_id, data[0])
            # 返回上级菜单
            self.show_own_land_detail_panel(player, land_id, self.get_land_info(land_id))

        rename_panel = ModalForm(
            title=self.language_manager.GetText('RENAME_OWN_LAND_PANEL_TITLE'),
            controls=[new_name_input],
            on_close=None,
            on_submit=try_change_name
        )
        player.send_form(rename_panel)

    def set_player_pos_as_land_tp_pos(self, player: Player, land_id: int):
        """用玩家当前位置设领地传送点：按目标领地 AABB（含维度/Y）校验，不依赖脚下生效领地。"""
        dim = get_dimension_id(player.location.dimension)
        x = math.floor(player.location.x)
        y = math.floor(player.location.y)
        z = math.floor(player.location.z)
        if not self.land_system.position_in_land_bounds(land_id, x, y, z, dim):
            result = self.language_manager.GetText('SET_LAND_TP_POS_FAIL_OUT_LAND')
        else:
            success, _err = self.set_land_teleport_point(land_id, x, y, z)
            if success:
                result = self.language_manager.GetText('SET_LAND_TP_POS_SUCCESS').format(
                    land_id, (x, y, z)
                )
            else:
                result = self.language_manager.GetText('SET_LAND_TP_POS_FAIL_OUT_LAND')
        result_panel = ActionForm(
            title=self.language_manager.GetText('SET_LAND_TP_POS_RESULT_TITLE'),
            content=result,
            on_close=None,
        )
        player.send_form(result_panel)

    def teleport_to_land(self, player: Player, land_id: int):
        # 检查费用
        if self.teleport_system.teleport_cost_land > 0:
            player_money = self.get_player_money(player)
            if player_money < self.teleport_system.teleport_cost_land:
                player.send_message(self.language_manager.GetText('TELEPORT_COST_NOT_ENOUGH_MONEY').format(
                    self._format_money_display(self.teleport_system.teleport_cost_land),
                    self._format_money_display(player_money)
                ))
                return
            
            # 扣除费用
            if not self.decrease_player_money(player, self.teleport_system.teleport_cost_land):
                self.report_arc_error(
                    "TP7",
                    f"teleport_to_land decrease failed land_id={land_id!r} cost={self.teleport_system.teleport_cost_land!r}",
                    player,
                )
                return
        
        tp_target_pos = self.get_land_teleport_point(land_id)
        self.run_player_task(
            player,
            lambda p, l_id=land_id, pos=tp_target_pos: self.delay_teleport_to_land(p, l_id, pos),
            delay=45,
        )
        player.send_message(self.language_manager.GetText('READY_TELEPORT_TO_LAND').format(land_id))

    def delay_teleport_to_land(self, player: Player, land_id: int, position: tuple):
        player.send_message(self.language_manager.GetText('TELEPORT_TO_LAND_START_HINT').format(land_id))
        land_dimension = self.get_land_dimension(land_id)
        self.server.dispatch_command(self.server.command_sender, generate_tp_command_to_position(player.name, position, land_dimension))

    def confirm_delete_land(self, player: Player, land_id: int):
        deleta_land_info = self.get_land_info(land_id)
        owner_paid = deleta_land_info.get('owner_paid_money')
        if owner_paid is not None:
            return_money = round(float(owner_paid) * self.land_sell_refund_coefficient, 2)
        else:
            land_area = (deleta_land_info['max_x'] - deleta_land_info['min_x'] + 1) * (deleta_land_info['max_z'] - deleta_land_info['min_z'] + 1)
            return_money = round(land_area * self.land_price * self.land_sell_refund_coefficient, 2)
        confirm_panel = ActionForm(
            title=self.language_manager.GetText('CONFIRM_DELETE_LAND_TITLE').format(land_id),
            content=self.language_manager.GetText('CONFIRM_DELETE_LAND_CONTENT').format(
            land_id, deleta_land_info['land_name'], self.land_sell_refund_coefficient,
            self._format_money_display(return_money)),
            on_close=None,
        )
        confirm_panel.add_button(self.language_manager.GetText('CONFIRM_DELETE_LAND_BUTTON').format(land_id),
                                 on_click=lambda p=player, l_id=land_id, r_m=return_money: self.try_delete_land(p, l_id, r_m)
                                 )
        player.send_form(confirm_panel)

    def try_delete_land(self, player: Player, land_id: int, return_money: int):
        r = self.delete_land(land_id, actor=player)
        if r:
            if return_money and return_money > 0:
                if not self.increase_player_money(player, return_money):
                    self.report_arc_error(
                        "LAND_PAY2",
                        f"try_delete_land land_id={land_id} deleted but refund increase failed amount={return_money!r}",
                        player,
                    )
            player.send_message(self.language_manager.GetText('DELETE_LAND_SUCCESS').format(
                land_id,
                self._format_money_display(return_money),
                self._format_money_display(self.get_player_money(player))))
        else:
            player.send_message(self.language_manager.GetText('DELETE_LAND_FAILED').format(land_id))
        self.show_own_land_menu(player)

    def show_transfer_land_panel(self, player: Player, land_id: int):
        """显示移交领地面板，让玩家选择要移交给谁"""
        online_players = [p for p in self.server.online_players if p.name != player.name]
        if not online_players:
            no_players_panel = ActionForm(
                title=self.language_manager.GetText('TRANSFER_LAND_PANEL_TITLE'),
                content=self.language_manager.GetText('NO_OTHER_PLAYERS_ONLINE'),
                on_close=None,
            )
            player.send_form(no_players_panel)
            return

        transfer_menu = ActionForm(
            title=self.language_manager.GetText('TRANSFER_LAND_PANEL_TITLE'),
            content=self.language_manager.GetText('TRANSFER_LAND_PANEL_CONTENT'),
            on_close=None,
        )
        
        for target_player in online_players:
            transfer_menu.add_button(
                self.language_manager.GetText('TRANSFER_LAND_TARGET_BUTTON').format(target_player.name),
                on_click=lambda p=player, l_id=land_id, t=target_player: self.confirm_transfer_land(p, l_id, t)
            )
        
        player.send_form(transfer_menu)

    def confirm_transfer_land(self, player: Player, land_id: int, target_player: Player):
        """显示确认移交领地的面板"""
        land_info = self.get_land_info(land_id)
        if not land_info:
            self.report_arc_error(
                "LAND10",
                f"confirm_transfer_land get_land_info empty land_id={land_id!r}",
                player,
            )
            return

        confirm_panel = ActionForm(
            title=self.language_manager.GetText('CONFIRM_TRANSFER_LAND_TITLE').format(land_id),
            content=self.language_manager.GetText('CONFIRM_TRANSFER_LAND_CONTENT').format(
                land_id, 
                land_info['land_name'], 
                target_player.name
            ),
            on_close=None,
        )
        confirm_panel.add_button(
            self.language_manager.GetText('CONFIRM_TRANSFER_LAND_BUTTON'),
            on_click=lambda p=player, l_id=land_id, t=target_player: self.try_transfer_land(p, l_id, t)
        )
        player.send_form(confirm_panel)

    def transfer_land(self, land_id: int, new_owner_xuid: str) -> bool:
        return self.land_system.transfer_land(land_id, new_owner_xuid)

    def try_transfer_land(self, player: Player, land_id: int, target_player: Player):
        """尝试移交领地"""
        land_info = self.get_land_info(land_id)
        if not land_info:
            self.report_arc_error(
                "LAND11",
                f"try_transfer_land get_land_info empty land_id={land_id!r}",
                player,
            )
            return

        # 检查目标玩家是否还在线
        target_online = any(p.name == target_player.name for p in self.server.online_players)
        if not target_online:
            player.send_message(self.language_manager.GetText('REQUEST_SENDER_OFFLINE'))
            self.show_own_land_menu(player)
            return

        # 执行移交
        success = self.transfer_land(land_id, str(target_player.xuid))
        if success:
            # 通知当前玩家
            self._notify_important(
                player,
                self.language_manager.GetText('TRANSFER_LAND_SUCCESS').format(land_id, target_player.name),
                title=self._toast_title("LAND_RESULT_TOAST_TITLE", "领地"),
            )
            
            # 通知目标玩家
            self._notify_important(
                target_player,
                self.language_manager.GetText('TRANSFER_LAND_NOTIFICATION').format(
                    player.name, 
                    land_id, 
                    land_info['land_name']
                ),
                title=self._toast_title("LAND_RESULT_TOAST_TITLE", "领地"),
            )
        else:
            player.send_message(self.language_manager.GetText('TRANSFER_LAND_FAILED').format(land_id))
        
        self.show_own_land_menu(player)

    def show_land_auth_manage_panel(self, player: Player, land_id: int):
        """显示领地授权管理面板"""
        land_info = self.get_land_info(land_id)
        if not land_info:
            self.report_arc_error(
                "LAND12",
                f"show_land_auth_manage_panel get_land_info empty land_id={land_id!r}",
                player,
            )
            return

        auth_panel = ActionForm(
            title=self.language_manager.GetText('LAND_AUTH_MANAGE_TITLE'),
            on_close=None,
        )
        
        auth_panel.add_button(
            self.language_manager.GetText('LAND_AUTH_ADD_BUTTON'),
            on_click=lambda p=player, l_id=land_id: self.show_add_land_auth_panel(p, l_id)
        )
        
        if land_info['shared_users']:
            auth_panel.add_button(
                self.language_manager.GetText('LAND_AUTH_REMOVE_BUTTON'),
                on_click=lambda p=player, l_id=land_id: self.show_remove_land_auth_panel(p, l_id)
            )
        
        player.send_form(auth_panel)

    def show_add_land_auth_panel(self, player: Player, land_id: int):
        """显示添加领地授权面板"""
        online_players = [p for p in self.server.online_players if p.name != player.name]
        if not online_players:
            no_players_panel = ActionForm(
                title=self.language_manager.GetText('LAND_AUTH_ADD_PANEL_TITLE'),
                content=self.language_manager.GetText('NO_OTHER_PLAYERS_ONLINE'),
                on_close=None,
            )
            player.send_form(no_players_panel)
            return

        add_auth_panel = ActionForm(
            title=self.language_manager.GetText('LAND_AUTH_ADD_PANEL_TITLE'),
            content=self.language_manager.GetText('LAND_AUTH_SELECT_PLAYER_CONTENT'),
            on_close=None,
        )
        
        for target_player in online_players:
            add_auth_panel.add_button(
                self.language_manager.GetText('LAND_AUTH_ADD_TARGET_BUTTON').format(target_player.name),
                on_click=lambda p=player, l_id=land_id, t=target_player: self.add_land_auth(p, l_id, t)
            )
        
        player.send_form(add_auth_panel)

    def show_remove_land_auth_panel(self, player: Player, land_id: int):
        """显示移除领地授权面板"""
        land_info = self.get_land_info(land_id)
        if not land_info or not land_info['shared_users']:
            no_auth_panel = ActionForm(
                title=self.language_manager.GetText('LAND_AUTH_REMOVE_PANEL_TITLE'),
                content=self.language_manager.GetText('LAND_AUTH_NO_SHARED_USERS'),
                on_close=None,
            )
            player.send_form(no_auth_panel)
            return

        remove_auth_panel = ActionForm(
            title=self.language_manager.GetText('LAND_AUTH_REMOVE_PANEL_TITLE'),
            content=self.language_manager.GetText('LAND_AUTH_SELECT_REMOVE_CONTENT'),
            on_close=None,
        )
        
        for shared_uuid in land_info['shared_users']:
            raw_name = self.get_player_name_by_xuid(shared_uuid, return_with_title=False)
            if not raw_name:
                continue
            display_name = self.get_player_name_by_xuid(shared_uuid, return_with_title=True) or raw_name
            remove_auth_panel.add_button(
                self.language_manager.GetText('LAND_AUTH_REMOVE_TARGET_BUTTON').format(display_name),
                on_click=lambda p=player, l_id=land_id, uuid=shared_uuid, name=raw_name: self.remove_land_auth(p, l_id, uuid, name)
            )
        
        player.send_form(remove_auth_panel)

    def add_land_auth(self, player: Player, land_id: int, target_player: Player):
        """添加领地授权"""
        try:
            land_info = self.get_land_info(land_id)
            if not land_info:
                self.report_arc_error(
                    "LAND13",
                    f"add_land_auth get_land_info empty land_id={land_id!r}",
                    player,
                )
                return
            target_xuid = str(target_player.xuid)
            if target_xuid in land_info['shared_users']:
                player.send_message(self.language_manager.GetText('LAND_AUTH_ALREADY_EXISTS').format(target_player.name))
                self.show_land_auth_manage_panel(player, land_id)
                return
            success = self.land_system.add_land_shared_user(land_id, target_xuid)
            if success:
                player.send_message(self.language_manager.GetText('LAND_AUTH_SUCCESS_ADD').format(land_id, target_player.name))
                self._notify_important(
                    target_player,
                    self.language_manager.GetText('LAND_AUTH_NOTIFICATION').format(
                        player.name, land_id, land_info['land_name']
                    ),
                    title=self._toast_title("LAND_RESULT_TOAST_TITLE", "领地"),
                )
            else:
                player.send_message(self.language_manager.GetText('LAND_AUTH_FAILED_ADD'))
        except Exception as e:
            self.logger.error(f"Add land auth error: {str(e)}")
            player.send_message(self.language_manager.GetText('LAND_AUTH_FAILED_ADD'))
        self.show_land_auth_manage_panel(player, land_id)

    def remove_land_auth(self, player: Player, land_id: int, target_uuid: str, target_name: str):
        """移除领地授权"""
        try:
            land_info = self.get_land_info(land_id)
            if not land_info:
                self.report_arc_error(
                    "LAND14",
                    f"remove_land_auth get_land_info empty land_id={land_id!r}",
                    player,
                )
                return
            if target_uuid not in land_info['shared_users']:
                player.send_message(self.language_manager.GetText('LAND_AUTH_NOT_EXISTS').format(target_name))
                self.show_land_auth_manage_panel(player, land_id)
                return
            success = self.land_system.remove_land_shared_user(land_id, target_uuid)
            if success:
                player.send_message(self.language_manager.GetText('LAND_AUTH_SUCCESS_REMOVE').format(target_name, land_id))
                target_player = self.server.get_player(target_name)
                if target_player:
                    self._notify_important(
                        target_player,
                        self.language_manager.GetText('LAND_AUTH_REMOVE_NOTIFICATION').format(
                            player.name, land_id, land_info['land_name']
                        ),
                        title=self._toast_title("LAND_RESULT_TOAST_TITLE", "领地"),
                    )
            else:
                player.send_message(self.language_manager.GetText('LAND_AUTH_FAILED_REMOVE'))
        except Exception as e:
            self.logger.error(f"Remove land auth error: {str(e)}")
            player.send_message(self.language_manager.GetText('LAND_AUTH_FAILED_REMOVE'))
        self.show_land_auth_manage_panel(player, land_id)

    def show_land_explosion_setting_panel(self, player: Player, land_id: int):
        """显示领地爆炸保护设置面板"""
        land_info = self.get_land_info(land_id)
        if not land_info:
            self.report_arc_error(
                "LAND15",
                f"show_land_explosion_setting_panel get_land_info empty land_id={land_id!r}",
                player,
            )
            return

        current_allow_explosion = land_info.get('allow_explosion', False)
        status_text = self.language_manager.GetText('LAND_EXPLOSION_STATUS_ENABLED') if current_allow_explosion else self.language_manager.GetText('LAND_EXPLOSION_STATUS_DISABLED')
        
        explosion_setting_panel = ActionForm(
            title=self.language_manager.GetText('LAND_EXPLOSION_SETTING_TITLE'),
            content=self.language_manager.GetText('LAND_EXPLOSION_CURRENT_STATUS').format(status_text),
            on_close=None,
        )
        
        if current_allow_explosion:
            # 当前允许爆炸，显示禁止爆炸按钮
            explosion_setting_panel.add_button(
                self.language_manager.GetText('LAND_EXPLOSION_TOGGLE_DISABLE_BUTTON'),
                on_click=lambda p=player, l_id=land_id: self.toggle_land_explosion_setting(p, l_id, False)
            )
        else:
            # 当前禁止爆炸，显示允许爆炸按钮
            explosion_setting_panel.add_button(
                self.language_manager.GetText('LAND_EXPLOSION_TOGGLE_ENABLE_BUTTON'),
                on_click=lambda p=player, l_id=land_id: self.toggle_land_explosion_setting(p, l_id, True)
            )
        
        player.send_form(explosion_setting_panel)

    def show_land_public_interact_setting_panel(self, player: Player, land_id: int):
        """显示领地方块互动开放设置面板"""
        land_info = self.get_land_info(land_id)
        if not land_info:
            self.report_arc_error(
                "LAND16",
                f"show_land_public_interact_setting_panel get_land_info empty land_id={land_id!r}",
                player,
            )
            return

        current_allow_public_interact = land_info.get('allow_public_interact', False)
        status_text = self.language_manager.GetText('LAND_PUBLIC_INTERACT_STATUS_ENABLED') if current_allow_public_interact else self.language_manager.GetText('LAND_PUBLIC_INTERACT_STATUS_DISABLED')
        
        public_interact_setting_panel = ActionForm(
            title=self.language_manager.GetText('LAND_PUBLIC_INTERACT_SETTING_TITLE'),
            content=self.language_manager.GetText('LAND_PUBLIC_INTERACT_CURRENT_STATUS').format(status_text),
            on_close=None,
        )
        
        if current_allow_public_interact:
            # 当前对所有人开放方块互动，显示关闭按钮
            public_interact_setting_panel.add_button(
                self.language_manager.GetText('LAND_PUBLIC_INTERACT_TOGGLE_DISABLE_BUTTON'),
                on_click=lambda p=player, l_id=land_id: self.toggle_land_public_interact_setting(p, l_id, False)
            )
        else:
            # 当前不对所有人开放方块互动，显示开启按钮
            public_interact_setting_panel.add_button(
                self.language_manager.GetText('LAND_PUBLIC_INTERACT_TOGGLE_ENABLE_BUTTON'),
                on_click=lambda p=player, l_id=land_id: self.toggle_land_public_interact_setting(p, l_id, True)
            )
        
        player.send_form(public_interact_setting_panel)

    def show_land_guild_member_interact_setting_panel(self, player: Player, land_id: int):
        """与领地主人同公会的成员是否可进行方块互动（不含建造/破坏）。"""
        land_info = self.get_land_info(land_id)
        if not land_info:
            self.report_arc_error(
                "LAND_GUILDA1",
                f"show_land_guild_member_interact_setting_panel get_land_info empty land_id={land_id!r}",
                player,
            )
            return
        cur = land_info.get("allow_guild_member_interact", False)
        status_text = (
            self.language_manager.GetText("LAND_GUILD_MEMBER_INTERACT_STATUS_ENABLED")
            if cur
            else self.language_manager.GetText("LAND_GUILD_MEMBER_INTERACT_STATUS_DISABLED")
        )
        panel = ActionForm(
            title=self.language_manager.GetText("LAND_GUILD_MEMBER_INTERACT_SETTING_TITLE"),
            content=self.language_manager.GetText(
                "LAND_GUILD_MEMBER_INTERACT_CURRENT_STATUS"
            ).format(status_text),
            on_close=None,
        )
        if cur:
            panel.add_button(
                self.language_manager.GetText(
                    "LAND_GUILD_MEMBER_INTERACT_TOGGLE_DISABLE_BUTTON"
                ),
                on_click=lambda p=player, l_id=land_id: self.toggle_land_guild_member_interact_setting(
                    p, l_id, False
                ),
            )
        else:
            panel.add_button(
                self.language_manager.GetText(
                    "LAND_GUILD_MEMBER_INTERACT_TOGGLE_ENABLE_BUTTON"
                ),
                on_click=lambda p=player, l_id=land_id: self.toggle_land_guild_member_interact_setting(
                    p, l_id, True
                ),
            )
        panel.add_button(
            self.language_manager.GetText("RETURN_BUTTON_TEXT"),
            on_click=lambda p=player, l_id=land_id, l_info=land_info: self.show_own_land_detail_panel(
                p, l_id, l_info
            ),
        )
        player.send_form(panel)

    def toggle_land_guild_member_interact_setting(
        self, player: Player, land_id: int, allow: bool
    ) -> None:
        try:
            success = self.land_system.set_land_allow_guild_member_interact(land_id, allow)
            if success:
                key = (
                    "LAND_GUILD_MEMBER_INTERACT_SETTING_UPDATED_ENABLE"
                    if allow
                    else "LAND_GUILD_MEMBER_INTERACT_SETTING_UPDATED_DISABLE"
                )
                player.send_message(self.language_manager.GetText(key).format(land_id))
            else:
                player.send_message(
                    self.language_manager.GetText(
                        "LAND_GUILD_MEMBER_INTERACT_SETTING_FAILED"
                    )
                )
            land_info = self.get_land_info(land_id)
            self.show_own_land_detail_panel(player, land_id, land_info)
        except Exception as e:
            self.logger.error(
                f"Update land guild member interact setting error: {str(e)}"
            )
            self.report_arc_error(
                "LAND_GUILDA2",
                f"toggle_land_guild_member_interact_setting exception land_id={land_id!r}",
                player,
                exception=e,
            )

    def toggle_land_public_interact_setting(self, player: Player, land_id: int, allow_public_interact: bool):
        """切换领地方块互动开放设置"""
        try:
            land_info_pre = self.get_land_info(land_id)
            if land_info_pre and LandSystem.parse_land_owner_guild_id(
                str(land_info_pre.get("owner_xuid") or "")
            ) is not None:
                if allow_public_interact:
                    player.send_message(
                        self.language_manager.GetText("LAND_PUBLIC_INTERACT_GUILD_FORBIDDEN")
                        or "公会领地仅本公会成员有权限，不能开启对全体开放方块互动。"
                    )
                    self.show_own_land_detail_panel(player, land_id, land_info_pre)
                    return
            success = self.land_system.set_land_allow_public_interact(land_id, allow_public_interact)
            if success:
                key = 'LAND_PUBLIC_INTERACT_SETTING_UPDATED_ENABLE' if allow_public_interact else 'LAND_PUBLIC_INTERACT_SETTING_UPDATED_DISABLE'
                player.send_message(self.language_manager.GetText(key).format(land_id))
            else:
                player.send_message(self.language_manager.GetText('LAND_PUBLIC_INTERACT_SETTING_FAILED'))
            land_info = self.get_land_info(land_id)
            self.show_own_land_detail_panel(player, land_id, land_info)
        except Exception as e:
            self.logger.error(f"Update land public interact setting error: {str(e)}")
            self.report_arc_error(
                "LAND17",
                f"toggle_land_public_interact_setting exception land_id={land_id!r}",
                player,
                exception=e,
            )

    def toggle_land_explosion_setting(self, player: Player, land_id: int, allow_explosion: bool):
        """切换领地爆炸保护设置"""
        try:
            success = self.land_system.set_land_allow_explosion(land_id, allow_explosion)
            if success:
                key = 'LAND_EXPLOSION_SETTING_UPDATED_ENABLE' if allow_explosion else 'LAND_EXPLOSION_SETTING_UPDATED_DISABLE'
                player.send_message(self.language_manager.GetText(key).format(land_id))
            else:
                player.send_message(self.language_manager.GetText('LAND_EXPLOSION_SETTING_FAILED'))
        except Exception as e:
            self.logger.error(f"Toggle land explosion setting error: {str(e)}")
            player.send_message(self.language_manager.GetText('LAND_EXPLOSION_SETTING_FAILED'))
        land_info = self.get_land_info(land_id)
        if land_info:
            self.show_own_land_detail_panel(player, land_id, land_info)

    def show_land_actor_interaction_setting_panel(self, player: Player, land_id: int):
        """显示领地生物互动设置面板"""
        land_info = self.get_land_info(land_id)
        if not land_info:
            self.report_arc_error(
                "LAND18",
                f"show_land_actor_interaction_setting_panel get_land_info empty land_id={land_id!r}",
                player,
            )
            return

        current_allow_actor_interaction = land_info.get('allow_actor_interaction', False)
        status_text = self.language_manager.GetText('LAND_ACTOR_INTERACTION_STATUS_ENABLED') if current_allow_actor_interaction else self.language_manager.GetText('LAND_ACTOR_INTERACTION_STATUS_DISABLED')
        
        actor_interaction_setting_panel = ActionForm(
            title=self.language_manager.GetText('LAND_ACTOR_INTERACTION_SETTING_TITLE'),
            content=self.language_manager.GetText('LAND_ACTOR_INTERACTION_CURRENT_STATUS').format(status_text),
            on_close=None,
        )
        
        if current_allow_actor_interaction:
            # 当前允许生物互动，显示禁止生物互动按钮
            actor_interaction_setting_panel.add_button(
                self.language_manager.GetText('LAND_ACTOR_INTERACTION_TOGGLE_DISABLE_BUTTON'),
                on_click=lambda p=player, l_id=land_id: self.toggle_land_actor_interaction_setting(p, l_id, False)
            )
        else:
            # 当前禁止生物互动，显示允许生物互动按钮
            actor_interaction_setting_panel.add_button(
                self.language_manager.GetText('LAND_ACTOR_INTERACTION_TOGGLE_ENABLE_BUTTON'),
                on_click=lambda p=player, l_id=land_id: self.toggle_land_actor_interaction_setting(p, l_id, True)
            )
        
        player.send_form(actor_interaction_setting_panel)

    def show_land_actor_damage_setting_panel(self, player: Player, land_id: int):
        """显示领地生物攻击设置面板"""
        land_info = self.get_land_info(land_id)
        if not land_info:
            self.report_arc_error(
                "LAND19",
                f"show_land_actor_damage_setting_panel get_land_info empty land_id={land_id!r}",
                player,
            )
            return

        current_allow_actor_damage = land_info.get('allow_actor_damage', False)
        status_text = self.language_manager.GetText('LAND_ACTOR_DAMAGE_STATUS_ENABLED') if current_allow_actor_damage else self.language_manager.GetText('LAND_ACTOR_DAMAGE_STATUS_DISABLED')
        
        actor_damage_setting_panel = ActionForm(
            title=self.language_manager.GetText('LAND_ACTOR_DAMAGE_SETTING_TITLE'),
            content=self.language_manager.GetText('LAND_ACTOR_DAMAGE_CURRENT_STATUS').format(status_text),
            on_close=None,
        )
        
        if current_allow_actor_damage:
            # 当前允许攻击生物，显示禁止攻击生物按钮
            actor_damage_setting_panel.add_button(
                self.language_manager.GetText('LAND_ACTOR_DAMAGE_TOGGLE_DISABLE_BUTTON'),
                on_click=lambda p=player, l_id=land_id: self.toggle_land_actor_damage_setting(p, l_id, False)
            )
        else:
            # 当前禁止攻击生物，显示允许攻击生物按钮
            actor_damage_setting_panel.add_button(
                self.language_manager.GetText('LAND_ACTOR_DAMAGE_TOGGLE_ENABLE_BUTTON'),
                on_click=lambda p=player, l_id=land_id: self.toggle_land_actor_damage_setting(p, l_id, True)
            )

        actor_damage_setting_panel.add_button(
            self.language_manager.GetText('RETURN_BUTTON_TEXT'),
            on_click=lambda p=player, l_id=land_id, l_info=self.get_land_info(land_id): self.show_own_land_detail_panel(
                p, l_id, l_info
            ),
        )

        player.send_form(actor_damage_setting_panel)

    def toggle_land_actor_interaction_setting(self, player: Player, land_id: int, allow_actor_interaction: bool):
        """切换领地生物互动设置"""
        try:
            success = self.land_system.set_land_allow_actor_interaction(land_id, allow_actor_interaction)
            if success:
                key = 'LAND_ACTOR_INTERACTION_SETTING_UPDATED_ENABLE' if allow_actor_interaction else 'LAND_ACTOR_INTERACTION_SETTING_UPDATED_DISABLE'
                player.send_message(self.language_manager.GetText(key).format(land_id))
            else:
                player.send_message(self.language_manager.GetText('LAND_ACTOR_INTERACTION_SETTING_FAILED'))
        except Exception as e:
            self.logger.error(f"Toggle land actor interaction setting error: {str(e)}")
            player.send_message(self.language_manager.GetText('LAND_ACTOR_INTERACTION_SETTING_FAILED'))
        land_info = self.get_land_info(land_id)
        if land_info:
            self.show_own_land_detail_panel(player, land_id, land_info)

    def toggle_land_actor_damage_setting(self, player: Player, land_id: int, allow_actor_damage: bool):
        """切换领地生物攻击设置"""
        try:
            success = self.land_system.set_land_allow_actor_damage(land_id, allow_actor_damage)
            if success:
                key = 'LAND_ACTOR_DAMAGE_SETTING_UPDATED_ENABLE' if allow_actor_damage else 'LAND_ACTOR_DAMAGE_SETTING_UPDATED_DISABLE'
                player.send_message(self.language_manager.GetText(key).format(land_id))
            else:
                player.send_message(self.language_manager.GetText('LAND_ACTOR_DAMAGE_SETTING_FAILED'))
        except Exception as e:
            self.logger.error(f"Toggle land actor damage setting error: {str(e)}")
            player.send_message(self.language_manager.GetText('LAND_ACTOR_DAMAGE_SETTING_FAILED'))
        land_info = self.get_land_info(land_id)
        if land_info:
            self.show_own_land_detail_panel(player, land_id, land_info)

    def show_land_frame_setting_panel(self, player: Player, land_id: int):
        """领地展示框权限设置：为 False 时禁止对展示框/发光展示框进行互动与破坏。"""
        land_info = self.get_land_info(land_id)
        if not land_info:
            self.show_own_land_menu(player)
            return
        current_allow_frame = land_info.get('allow_frame', False)
        status_text = self.language_manager.GetText('LAND_FRAME_STATUS_ENABLED') if current_allow_frame else self.language_manager.GetText('LAND_FRAME_STATUS_DISABLED')
        panel = ActionForm(
            title=self.language_manager.GetText('LAND_FRAME_SETTING_TITLE'),
            content=self.language_manager.GetText('LAND_FRAME_CURRENT_STATUS').format(status_text),
            on_close=None,
        )
        panel.add_button(self.language_manager.GetText('LAND_FRAME_TOGGLE_ENABLE_BUTTON'),
                         on_click=lambda p=player, l_id=land_id: self.toggle_land_frame_setting(p, l_id, True))
        panel.add_button(self.language_manager.GetText('LAND_FRAME_TOGGLE_DISABLE_BUTTON'),
                         on_click=lambda p=player, l_id=land_id: self.toggle_land_frame_setting(p, l_id, False))
        panel.add_button(self.language_manager.GetText('RETURN_BUTTON_TEXT'),
                         on_click=lambda p=player, l_id=land_id, linfo=self.get_land_info(land_id): self.show_own_land_detail_panel(p, l_id, linfo))
        player.send_form(panel)

    def toggle_land_frame_setting(self, player: Player, land_id: int, allow_frame: bool):
        try:
            success = self.land_system.set_land_allow_frame(land_id, allow_frame)
            if success:
                key = 'LAND_FRAME_SETTING_UPDATED_ENABLE' if allow_frame else 'LAND_FRAME_SETTING_UPDATED_DISABLE'
                player.send_message(self.language_manager.GetText(key).format(land_id))
            else:
                player.send_message(self.language_manager.GetText('LAND_FRAME_SETTING_FAILED'))
        except Exception as e:
            self.logger.error(f"Toggle land frame setting error: {str(e)}")
            player.send_message(self.language_manager.GetText('LAND_FRAME_SETTING_FAILED'))
        land_info = self.get_land_info(land_id)
        if land_info:
            self.show_own_land_detail_panel(player, land_id, land_info)

    def show_create_new_land_guide(self, player: Player):
        """显示创建领地的坐标输入表单，可预填上次设定的值"""
        cached = self.player_new_land_creation_info.get(player.name, {})
        # 调整已有领地范围不受「允许圈地」限制；仅新建圈地需检查
        if cached.get("resize_land_id") is None and not self._ensure_land_claim_allowed(player):
            return
        dim_raw = (
            cached.get("dimension", get_dimension_id(player.location.dimension))
            if cached.get("resize_land_id") is not None
            else get_dimension_id(player.location.dimension)
        )
        dim_label = self._translate_dimension_name(dim_raw)
        default_min_x = str(cached.get('min_x', math.floor(player.location.x)))
        default_max_x = str(cached.get('max_x', math.floor(player.location.x)))
        default_min_y = str(cached.get('min_y', math.floor(player.location.y)))
        default_max_y = str(cached.get('max_y', math.floor(player.location.y)))
        default_min_z = str(cached.get('min_z', math.floor(player.location.z)))
        default_max_z = str(cached.get('max_z', math.floor(player.location.z)))

        controls = [
            self._modal_nav_dropdown(),
            Label(text=self.language_manager.GetText('CREATE_LAND_FORM_DIMENSION_LABEL').format(dim_label)),
            TextInput(label=self.language_manager.GetText('CREATE_LAND_FORM_MIN_X'), placeholder='例如: -100', default_value=default_min_x),
            TextInput(label=self.language_manager.GetText('CREATE_LAND_FORM_MAX_X'), placeholder='例如: 100', default_value=default_max_x),
            TextInput(label=self.language_manager.GetText('CREATE_LAND_FORM_MIN_Y'), placeholder='例如: 0', default_value=default_min_y),
            TextInput(label=self.language_manager.GetText('CREATE_LAND_FORM_MAX_Y'), placeholder='例如: 255', default_value=default_max_y),
            TextInput(label=self.language_manager.GetText('CREATE_LAND_FORM_MIN_Z'), placeholder='例如: -100', default_value=default_min_z),
            TextInput(label=self.language_manager.GetText('CREATE_LAND_FORM_MAX_Z'), placeholder='例如: 100', default_value=default_max_z),
        ]

        def on_submit(p: Player, json_str: str):
            try:
                data = json.loads(json_str)
                if self._modal_choice_is_back(data, 0):
                    _create_guide_close(p)
                    return
                # data[1] is Label (ignored), data[2..7] are the text inputs
                min_x_str = data[2]
                max_x_str = data[3]
                min_y_str = data[4]
                max_y_str = data[5]
                min_z_str = data[6]
                max_z_str = data[7]
                try:
                    min_x = int(min_x_str)
                    max_x = int(max_x_str)
                    min_y = int(min_y_str)
                    max_y = int(max_y_str)
                    min_z = int(min_z_str)
                    max_z = int(max_z_str)
                except (ValueError, TypeError):
                    p.send_message(self.language_manager.GetText('CREATE_LAND_FORM_INVALID_COORD'))
                    return
                # 自动排序
                min_x, max_x = min(min_x, max_x), max(min_x, max_x)
                min_y, max_y = min(min_y, max_y), max(min_y, max_y)
                min_z, max_z = min(min_z, max_z), max(min_z, max_z)
                prev = self.player_new_land_creation_info.get(p.name, {})
                dim_use = (
                    prev.get("dimension", get_dimension_id(p.location.dimension))
                    if prev.get("resize_land_id") is not None
                    else get_dimension_id(p.location.dimension)
                )
                new_info = {
                    'dimension': dim_use,
                    'min_x': min_x, 'max_x': max_x,
                    'min_y': min_y, 'max_y': max_y,
                    'min_z': min_z, 'max_z': max_z
                }
                if prev.get("resize_land_id") is not None:
                    new_info["resize_land_id"] = prev["resize_land_id"]
                    new_info["resize_mode"] = prev.get("resize_mode", "player")
                    if prev.get("resize_op_from_page") is not None:
                        new_info["resize_op_from_page"] = prev["resize_op_from_page"]
                self.player_new_land_creation_info[p.name] = new_info
                self._visualize_pending_land(p)
                self.show_pending_land_purchase_panel(p)
            except Exception as e:
                self.logger.error(f"Create land form submit error: {str(e)}")
                self.report_arc_error(
                    "LAND20",
                    "show_create_new_land_guide on_submit exception",
                    p,
                    exception=e,
                )

        def _create_guide_close(p: Player):
            if self.player_new_land_creation_info.get(p.name, {}).get("resize_land_id"):
                self.show_land_resize_confirm_panel(p)
            else:
                self.show_land_main_menu(p)

        form = ModalForm(
            title=self.language_manager.GetText('CREATE_LAND_FORM_TITLE'),
            controls=controls,
            on_submit=on_submit,
            on_close=None,
        )
        player.send_form(form)

    def show_current_land_info(self, player: Player):
        """显示玩家当前位置的领地信息并绘制粒子边界"""
        try:
            # 获取玩家当前位置（与位置线程、权限检测使用同一套维度和坐标逻辑）
            pos = self.get_player_position_vector(player)
            if not pos:
                self.report_arc_error(
                    "LAND21",
                    f"show_current_land_info get_player_position_vector None player={player.name!r}",
                    player,
                )
                return
            
            x, y, z = pos
            dimension = get_dimension_id(player.location.dimension)
            
            # 获取当前位置的领地ID（传入 y 做三维判断，与进入领地提示一致）
            land_id = self.get_land_at_pos(dimension, x, z, y)
            
            if not land_id:
                player.send_message(self.language_manager.GetText('LAND_CURRENT_POSITION_NO_LAND'))
                return
            
            # 获取领地详细信息
            land_info = self.get_land_info(land_id)
            if not land_info:
                self.report_arc_error(
                    "LAND22",
                    f"show_current_land_info get_land_info empty land_id={land_id!r}",
                    player,
                )
                return
            
            # 获取领地拥有者名称（公共领地显示「公共领地」）
            owner_name = self.get_land_display_owner_name(land_id) or '未知'
            
            # 格式化爆炸保护状态
            explosion_status = (self.language_manager.GetText('LAND_CURRENT_POSITION_EXPLOSION_ENABLED') 
                              if land_info.get('allow_explosion', False) 
                              else self.language_manager.GetText('LAND_CURRENT_POSITION_EXPLOSION_DISABLED'))
            
            # 格式化公共互动状态
            public_interact_status = (self.language_manager.GetText('LAND_CURRENT_POSITION_PUBLIC_INTERACT_ENABLED') 
                                    if land_info.get('allow_public_interact', False) 
                                    else self.language_manager.GetText('LAND_CURRENT_POSITION_PUBLIC_INTERACT_DISABLED'))
            
            shared_users = land_info.get('shared_users', [])
            if shared_users:
                shared_names = [self.get_player_name_by_xuid(uid) or uid for uid in shared_users]
                shared_str = ', '.join(shared_names)
            else:
                shared_str = self.language_manager.GetText('LAND_DETAIL_NO_SHARED_USER_TEXT')

            land_message = self.language_manager.GetText('LAND_CURRENT_POSITION_INFO').format(
                land_id,
                land_info['land_name'],
                owner_name,
                land_info['dimension'],
                land_info['min_x'], land_info.get('min_y', 0), land_info['min_z'],
                land_info['max_x'], land_info.get('max_y', 255), land_info['max_z'],
                land_info['tp_x'], land_info['tp_y'], land_info['tp_z'],
                explosion_status,
                public_interact_status
            )

            info_panel = ActionForm(
                title=self.language_manager.GetText('LAND_CURRENT_PANEL_TITLE'),
                content=land_message + '\n' + self.language_manager.GetText('LAND_CURRENT_POSITION_SHARED_USERS').format(shared_str),
                on_close=None,
            )

            info_panel.add_button(
                self.language_manager.GetText('RETURN_BUTTON_TEXT'),
                on_click=self.show_land_main_menu
            )

            # 显示粒子边界
            self.display_land_particle_boundary(player, land_info)
            player.send_form(info_panel)
            
        except Exception as e:
            self.logger.error(f"Show current land info error: {str(e)}")
            self.report_arc_error(
                "LAND23",
                "show_current_land_info outer exception",
                player,
                exception=e,
            )

    def _land_particle_player_key(self, player: Player = None, xuid: str = "", name: str = "") -> str:
        """领地粒子任务键：优先 xuid，否则 name。"""
        key = str(xuid or "").strip()
        if not key and player is not None:
            key = str(getattr(player, "xuid", "") or "").strip()
        if not key:
            key = str(name or "").strip()
        if not key and player is not None:
            key = str(getattr(player, "name", "") or "").strip()
        return key

    def _cancel_land_particle_boundary(self, xuid: str = "", name: str = "") -> None:
        """取消该玩家未完成的领地边界粒子分批任务（退出/重开时调用）。"""
        keys = []
        x = str(xuid or "").strip()
        n = str(name or "").strip()
        if x:
            keys.append(x)
        if n and n not in keys:
            keys.append(n)
        for key in keys:
            self._land_particle_gen_by_key[key] = int(self._land_particle_gen_by_key.get(key, 0)) + 1
            task = self._land_particle_task_by_key.pop(key, None)
            if task is None:
                continue
            try:
                task.cancel()
            except Exception:
                pass

    def display_land_particle_boundary(self, player: Player, land_info: dict, y_coord: float = None):
        """显示三维领地粒子边界（立方体12条棱），分 tick 发送避免单帧 100+ 次 spawn。

        不闭包持有 Player：每批按 xuid 从 online_players 重取；退出时取消任务，
        避免对已销毁 Player 调用 spawn_particle 触发 purecall 崩服。
        """
        try:
            player_xuid = str(getattr(player, "xuid", "") or "").strip()
            player_name = str(getattr(player, "name", "") or "").strip()
            key = self._land_particle_player_key(player, player_xuid, player_name)
            if not key:
                return

            min_x = land_info['min_x']
            max_x = land_info['max_x']
            min_y = land_info.get('min_y', 0)
            max_y = land_info.get('max_y', 255)
            min_z = land_info['min_z']
            max_z = land_info['max_z']

            STEPS = 4  # 每条棱 5 个点；12 棱共 60 点
            BATCH = 20

            def lerp(p1, p2, t):
                return (
                    p1[0] + (p2[0] - p1[0]) * t,
                    p1[1] + (p2[1] - p1[1]) * t,
                    p1[2] + (p2[2] - p1[2]) * t,
                )

            corners = [
                (min_x, min_y, min_z),
                (max_x, min_y, min_z),
                (max_x, min_y, max_z),
                (min_x, min_y, max_z),
                (min_x, max_y, min_z),
                (max_x, max_y, min_z),
                (max_x, max_y, max_z),
                (min_x, max_y, max_z),
            ]
            edges = (
                (0, 1), (1, 2), (2, 3), (3, 0),
                (4, 5), (5, 6), (6, 7), (7, 4),
                (0, 4), (1, 5), (2, 6), (3, 7),
            )
            points = []
            for a, b in edges:
                for i in range(STEPS + 1):
                    points.append(lerp(corners[a], corners[b], i / STEPS))

            # 取消同玩家旧任务并提升代数，使已排队的旧 lambda 直接退出
            self._cancel_land_particle_boundary(player_xuid, player_name)
            gen = int(self._land_particle_gen_by_key.get(key, 0))

            def _resolve_online_player():
                if player_xuid:
                    p = self._find_online_player_by_xuid(player_xuid)
                    if p is not None:
                        return p
                if player_name:
                    try:
                        return self.server.get_player(player_name)
                    except Exception:
                        return None
                return None

            def spawn_batch(start: int = 0):
                try:
                    if self._land_particle_gen_by_key.get(key) != gen:
                        return
                    online = _resolve_online_player()
                    if online is None:
                        self._land_particle_task_by_key.pop(key, None)
                        return
                    iv = getattr(online, "is_valid", None)
                    if callable(iv) and not iv():
                        self._land_particle_task_by_key.pop(key, None)
                        return
                    end = min(start + BATCH, len(points))
                    for x, y, z in points[start:end]:
                        # 同 tick 内中途下线极少见；purecall 不可捕获，每点前再确认代数
                        if self._land_particle_gen_by_key.get(key) != gen:
                            self._land_particle_task_by_key.pop(key, None)
                            return
                        online.spawn_particle(
                            "minecraft:crop_growth_emitter", float(x), float(y), float(z)
                        )
                    if end < len(points):
                        if self._land_particle_gen_by_key.get(key) != gen:
                            self._land_particle_task_by_key.pop(key, None)
                            return
                        task = self.server.scheduler.run_task(
                            self, lambda s=end: spawn_batch(s), delay=1
                        )
                        if task is not None:
                            self._land_particle_task_by_key[key] = task
                        else:
                            self._land_particle_task_by_key.pop(key, None)
                    else:
                        self._land_particle_task_by_key.pop(key, None)
                except Exception as inner:
                    self._land_particle_task_by_key.pop(key, None)
                    self.logger.error(f"Display land particle batch error: {str(inner)}")

            spawn_batch(0)

        except Exception as e:
            self.logger.error(f"Display land particle boundary error: {str(e)}")
            self.report_arc_error(
                "LAND24",
                "display_land_particle_boundary exception",
                player,
                exception=e,
            )

    def show_new_land_info(self, player: Player):
        """兼容旧入口：打开待购领地面板。"""
        self.show_pending_land_purchase_panel(player)

    def _execute_land_buy(self, player: Player):
        """供 /land buy 调用：打开购买确认面板（不再直接扣款购买）。"""
        self.show_pending_land_purchase_panel(player)

    def _visualize_pending_land(self, player: Player):
        """用粒子效果可视化玩家缓存中的待购买领地"""
        info = self.player_new_land_creation_info.get(player.name)
        if not info:
            return
        self.display_land_particle_boundary(player, {
            'min_x': info['min_x'], 'max_x': info['max_x'],
            'min_y': info['min_y'], 'max_y': info['max_y'],
            'min_z': info['min_z'], 'max_z': info['max_z']
        })

    def clear_new_land_creation_info_memory(self, player: Player):
        self.player_new_land_creation_info.pop(player.name, None)
        self.player_land_creation_pick.pop(player.name, None)
        self.player_land_pick_last_event_ts.pop(player.name, None)

    def start_interactive_land_creation(self, player: Player):
        """创建领地：先在世界中交互 4 个选点，再进入购买确认面板。"""
        if not self._ensure_land_claim_allowed(player):
            return
        self.require_sensitive_password_verified(
            player,
            self._start_interactive_land_creation_impl,
            on_cancel=lambda p: self.show_land_main_menu(p),
        )

    def _start_interactive_land_creation_impl(self, player: Player) -> None:
        self.player_new_land_creation_info.pop(player.name, None)
        self.player_land_pos1.pop(player.name, None)
        self.player_land_creation_pick[player.name] = {
            "step": "rect_a",
            "dimension": get_dimension_id(player.location.dimension),
        }
        self.player_land_pick_last_event_ts.pop(player.name, None)
        player.send_message(self.language_manager.GetText("LAND_CREATION_PICK_RECT_A"))

    def _try_consume_land_creation_pick(self, event: PlayerInteractEvent) -> bool:
        """处理圈地选点；已处理则取消默认交互。成功推进选点后打时间戳，短间隔内重复事件视为同一次点击防抖丢弃。"""
        player = event.player
        name = player.name
        state = self.player_land_creation_pick.get(name)
        if not state:
            return False
        # 新建圈地选点受 ALLOW_LAND_CLAIM 限制；调整已有领地范围不受影响
        if state.get("resize_land_id") is None and not self._is_land_claim_allowed():
            self.clear_new_land_creation_info_memory(player)
            self._notify_land_claim_disabled(player)
            event.is_cancelled = True
            return True
        if not getattr(event, "has_block", False):
            return False
        block = getattr(event, "block", None)
        if block is None or not hasattr(block, "location") or block.location is None:
            return False
        block_location = block.location
        now_ts = time.time()
        last_ok_ts = self.player_land_pick_last_event_ts.get(name)
        if last_ok_ts is not None and (now_ts - last_ok_ts) < self._land_creation_pick_debounce_sec:
            event.is_cancelled = True
            return True
        if hasattr(block, "dimension") and block.dimension is not None:
            dimension = get_dimension_id(block.dimension)
        else:
            dimension = get_dimension_id(player.location.dimension) if hasattr(player, "location") and player.location else ""
        if dimension != state.get("dimension"):
            player.send_message(self.language_manager.GetText("LAND_CREATION_PICK_WRONG_DIM"))
            return False
        bx = int(math.floor(block_location.x))
        by = int(math.floor(block_location.y))
        bz = int(math.floor(block_location.z))
        step = state.get("step")
        try:
            if step == "rect_a":
                event.is_cancelled = True
                state["step"] = "rect_b"
                state["ax"], state["az"] = bx, bz
                self.player_land_pick_last_event_ts[name] = time.time()
                player.send_message(self.language_manager.GetText("LAND_CREATION_PICK_RECT_B"))
                return True
            if step == "rect_b":
                event.is_cancelled = True
                ax, az = state["ax"], state["az"]
                state.pop("ax", None)
                state.pop("az", None)
                state["min_x"], state["max_x"] = min(ax, bx), max(ax, bx)
                state["min_z"], state["max_z"] = min(az, bz), max(az, bz)
                state["step"] = "y_min"
                self.player_land_pick_last_event_ts[name] = time.time()
                player.send_message(self.language_manager.GetText("LAND_CREATION_PICK_Y_MIN"))
                return True
            if step == "y_min":
                event.is_cancelled = True
                state["y_low"] = by
                state["step"] = "y_max"
                self.player_land_pick_last_event_ts[name] = time.time()
                player.send_message(self.language_manager.GetText("LAND_CREATION_PICK_Y_MAX"))
                return True
            if step == "y_max":
                event.is_cancelled = True
                y_low = int(state.get("y_low", by))
                y_high = by
                min_y = min(y_low, y_high)
                max_y = max(y_low, y_high)
                pick_info = {
                    "dimension": state["dimension"],
                    "min_x": state["min_x"],
                    "max_x": state["max_x"],
                    "min_y": min_y,
                    "max_y": max_y,
                    "min_z": state["min_z"],
                    "max_z": state["max_z"],
                }
                rid = state.get("resize_land_id")
                if rid is not None:
                    pick_info["resize_land_id"] = rid
                    pick_info["resize_mode"] = state.get("resize_mode", "player")
                    if state.get("resize_op_from_page") is not None:
                        pick_info["resize_op_from_page"] = state["resize_op_from_page"]
                self.player_new_land_creation_info[name] = pick_info
                self.player_land_creation_pick.pop(name, None)
                self.player_land_pick_last_event_ts.pop(name, None)
                self._visualize_pending_land(player)
                if rid is not None:
                    player.send_message(
                        self.language_manager.GetText("LAND_CREATION_PICK_Y_MAX_RESIZE")
                    )
                    self.show_land_resize_confirm_panel(player)
                else:
                    player.send_message(
                        self.language_manager.GetText("LAND_CREATION_PICK_Y_MAX")
                    )
                    self.show_pending_land_purchase_panel(player)
                return True
        except Exception as e:
            self.logger.error(f"[ARC Core] land creation pick error: {e}")
            self.report_arc_error(
                "LAND_PICK1",
                "_try_consume_land_creation_pick exception",
                player,
                exception=e,
            )
        return False

    def show_pending_land_purchase_panel(self, player: Player):
        """待购领地：粒子预览、坐标修改、确认购买；亦供 /land buy 打开。"""
        info = self.player_new_land_creation_info.get(player.name)
        if not info:
            player.send_message(self.language_manager.GetText("LANDBUY_NO_PENDING_LAND"))
            return
        if info.get("resize_land_id") is not None:
            self.show_land_resize_confirm_panel(player)
            return
        if not self._ensure_land_claim_allowed(player):
            self.clear_new_land_creation_info_memory(player)
            return

        dimension = info["dimension"]
        min_x, max_x = info["min_x"], info["max_x"]
        min_y, max_y = info["min_y"], info["max_y"]
        min_z, max_z = info["min_z"], info["max_z"]

        if_allowed, reason, overlap_ids = self.check_land_availability(dimension, min_x, max_x, min_y, max_y, min_z, max_z)
        overlap_lands_text = ""
        if overlap_ids:
            land_parts = [f"#{lid} {self.get_land_name(lid) or ''}".strip() for lid in overlap_ids]
            overlap_lands_text = ", ".join(land_parts)
        if not if_allowed:
            if reason == "SYSTEM_ERROR":
                self.report_arc_error(
                    "LAND0",
                    f"show_pending_land_purchase_panel check_land_availability SYSTEM_ERROR dim={dimension!r} overlap_ids={overlap_ids!r}",
                    player,
                )
                msg = self.language_manager.GetText(f"CHECK_NEW_LAND_AVAILABILITY_FAIL_{reason}")
                if overlap_lands_text:
                    msg = msg + "\n" + self.language_manager.GetText("LAND_OVERLAP_WITH_LANDS").format(overlap_lands_text)
                player.send_message(msg)
                return
            # 非 OP：与私人/公会等冲突时仍直接拦下；OP 需能进入面板以创建「允许覆盖」的公共领地
            if not player.is_op:
                msg = self.language_manager.GetText(f"CHECK_NEW_LAND_AVAILABILITY_FAIL_{reason}")
                if overlap_lands_text:
                    msg = msg + "\n" + self.language_manager.GetText("LAND_OVERLAP_WITH_LANDS").format(overlap_lands_text)
                player.send_message(msg)
                return

        length = max_x - min_x + 1
        height = max_y - min_y + 1
        width = max_z - min_z + 1

        if length <= self.land_min_size or width <= self.land_min_size:
            player.send_message(
                self.language_manager.GetText("CREATE_NEW_LAND_SIZE_TOO_SMALL").format(length, width, self.land_min_size)
            )
            return

        volume = length * height * width

        remaining_free_blocks = self.get_player_free_land_blocks(player)
        paid_blocks = max(0, volume - remaining_free_blocks)
        money_cost = paid_blocks * self.land_price
        used_free_blocks = min(volume, remaining_free_blocks)

        player_money = self.get_player_money(player)
        can_afford_private = if_allowed and (player.is_op or player_money >= money_cost)

        guild_contrib_cost = int(volume * int(self.land_price))
        mem_gl = self.guild_system.get_membership(str(player.xuid))
        guild_total_contrib = 0
        can_offer_guild_land_button = False
        if if_allowed and mem_gl:
            _gid = int(mem_gl.get("guild_id") or 0)
            if _gid > 0:
                guild_total_contrib = int(
                    self.guild_system.get_guild_total_contribution(_gid)
                )
                role_gl = str(mem_gl.get("role") or "")
                if role_gl in (ROLE_OWNER, ROLE_MANAGER):
                    can_offer_guild_land_button = True

        base_text = self.language_manager.GetText("NEW_LAND_INFO_TEXT").format(
            dimension,
            (min_x, min_y, min_z),
            (max_x, max_y, max_z),
            volume,
            self._format_money_display(money_cost),
            self._format_money_display(player_money),
        )
        hint_public = self.language_manager.GetText("NEW_LAND_MODE_PUBLIC_HINT")
        if mem_gl:
            hint_guild = self.language_manager.GetText(
                "NEW_LAND_MODE_GUILD_HINT"
            ).format(guild_contrib_cost, guild_total_contrib)
        else:
            hint_guild = self.language_manager.GetText(
                "NEW_LAND_MODE_GUILD_NOT_IN_GUILD"
            )
        content_parts = [
            base_text,
            hint_public,
            hint_guild,
            self.language_manager.GetText("NEW_LAND_PURCHASE_CONTENT_SUFFIX"),
        ]
        if not if_allowed and player.is_op and overlap_lands_text:
            content_parts.insert(
                1,
                self.language_manager.GetText("NEW_LAND_OP_OVERLAP_PUBLIC_HINT").format(
                    overlap_lands_text
                ),
            )
        content = "\n".join(content_parts)

        purchase_form = ActionForm(
            title=self.language_manager.GetText("LAND_PENDING_PURCHASE_PANEL_TITLE"),
            content=content,
            on_close=None,
        )

        def _preview(p: Player):
            self._visualize_pending_land(p)
            p.send_message(self.language_manager.GetText("LAND_CURRENT_POSITION_PARTICLE_DISPLAY"))

        purchase_form.add_button(self.language_manager.GetText("LAND_PENDING_PREVIEW_BUTTON"), on_click=_preview)
        purchase_form.add_button(
            self.language_manager.GetText("LAND_PENDING_EDIT_COORD_BUTTON"),
            on_click=self.show_edit_pending_land_coordinates_modal,
        )
        purchase_form.add_button(
            self.language_manager.GetText("LAND_PENDING_MANUAL_INPUT_BUTTON"),
            on_click=self.show_create_new_land_guide,
        )
        purchase_form.add_button(
            self.language_manager.GetText("LAND_PENDING_RESTART_PICK_BUTTON"),
            on_click=self.start_interactive_land_creation,
        )
        if if_allowed:
            if can_afford_private:
                purchase_form.add_button(
                    self.language_manager.GetText("LAND_BTN_CREATE_PRIVATE_LAND"),
                    on_click=lambda p, dim=dimension, m1=min_x, m2=max_x, m3=min_y, m4=max_y, m5=min_z, m6=max_z, vol=volume, mc=money_cost, uf=used_free_blocks: self.player_buy_new_land(
                        p, dim, m1, m2, m3, m4, m5, m6, vol, mc, uf
                    ),
                )
            else:
                purchase_form.add_button(
                    self.language_manager.GetText("BUY_NEW_LAND_NO_MONEY_TEXT")
                )
        elif player.is_op:
            purchase_form.add_button(
                self.language_manager.GetText("LAND_BTN_PRIVATE_BLOCKED_BY_OVERLAP")
            )
        prefer_allow_private = bool(not if_allowed and player.is_op)
        purchase_form.add_button(
            self.language_manager.GetText("LAND_BTN_CREATE_PUBLIC_LAND"),
            on_click=lambda p, dim=dimension, m1=min_x, m2=max_x, m3=min_y, m4=max_y, m5=min_z, m6=max_z, allow_def=prefer_allow_private: self.show_create_public_land_priority_modal(
                p, dim, m1, m2, m3, m4, m5, m6, default_allow_non_public=allow_def
            ),
        )
        if can_offer_guild_land_button:
            purchase_form.add_button(
                self.language_manager.GetText("LAND_BTN_CREATE_GUILD_LAND"),
                on_click=lambda p, dim=dimension, m1=min_x, m2=max_x, m3=min_y, m4=max_y, m5=min_z, m6=max_z, gcc=guild_contrib_cost: self.player_create_guild_land_from_pending(
                    p, dim, m1, m2, m3, m4, m5, m6, gcc
                ),
            )

        purchase_form.add_button(self.language_manager.GetText("RETURN_BUTTON_TEXT"), on_click=self.show_land_main_menu)
        player.send_form(purchase_form)

    def show_edit_pending_land_coordinates_modal(self, player: Player):
        """待购领地：6 个整数框 xmin/xmax ymin ymax zmin zmax（与领地存储字段一致）。"""
        info = self.player_new_land_creation_info.get(player.name)
        if not info:
            player.send_message(self.language_manager.GetText("LANDBUY_NO_PENDING_LAND"))
            return

        controls = [
            self._modal_nav_dropdown(),
            Label(text=self.language_manager.GetText("LAND_PENDING_EDIT_COORD_LABEL")),
            TextInput(
                label=self.language_manager.GetText("CREATE_LAND_FORM_MIN_X"),
                default_value=str(info["min_x"]),
            ),
            TextInput(
                label=self.language_manager.GetText("CREATE_LAND_FORM_MAX_X"),
                default_value=str(info["max_x"]),
            ),
            TextInput(
                label=self.language_manager.GetText("CREATE_LAND_FORM_MIN_Y"),
                default_value=str(info["min_y"]),
            ),
            TextInput(
                label=self.language_manager.GetText("CREATE_LAND_FORM_MAX_Y"),
                default_value=str(info["max_y"]),
            ),
            TextInput(
                label=self.language_manager.GetText("CREATE_LAND_FORM_MIN_Z"),
                default_value=str(info["min_z"]),
            ),
            TextInput(
                label=self.language_manager.GetText("CREATE_LAND_FORM_MAX_Z"),
                default_value=str(info["max_z"]),
            ),
        ]

        def on_submit(p: Player, json_str: str):
            try:
                data = json.loads(json_str)
                if self._modal_choice_is_back(data, 0):
                    _edit_coord_close(p)
                    return
                try:
                    raw_min_x = int(data[2])
                    raw_max_x = int(data[3])
                    raw_min_y = int(data[4])
                    raw_max_y = int(data[5])
                    raw_min_z = int(data[6])
                    raw_max_z = int(data[7])
                except (ValueError, TypeError, IndexError):
                    p.send_message(self.language_manager.GetText("CREATE_LAND_FORM_INVALID_COORD"))
                    return
                min_x, max_x = min(raw_min_x, raw_max_x), max(raw_min_x, raw_max_x)
                min_y, max_y = min(raw_min_y, raw_max_y), max(raw_min_y, raw_max_y)
                min_z, max_z = min(raw_min_z, raw_max_z), max(raw_min_z, raw_max_z)
                p_new = self.player_new_land_creation_info.get(p.name, {})
                dim = p_new.get("dimension", get_dimension_id(p.location.dimension))
                merged = {
                    "dimension": dim,
                    "min_x": min_x,
                    "max_x": max_x,
                    "min_y": min_y,
                    "max_y": max_y,
                    "min_z": min_z,
                    "max_z": max_z,
                }
                if p_new.get("resize_land_id") is not None:
                    merged["resize_land_id"] = p_new["resize_land_id"]
                    merged["resize_mode"] = p_new.get("resize_mode", "player")
                    if p_new.get("resize_op_from_page") is not None:
                        merged["resize_op_from_page"] = p_new["resize_op_from_page"]
                self.player_new_land_creation_info[p.name] = merged
                self._visualize_pending_land(p)
                self.show_pending_land_purchase_panel(p)
            except Exception as e:
                self.logger.error(f"Edit pending land coords error: {e}")
                self.report_arc_error(
                    "LAND_EDIT1",
                    "show_edit_pending_land_coordinates_modal on_submit exception",
                    p,
                    exception=e,
                )

        def _edit_coord_close(p: Player):
            if self.player_new_land_creation_info.get(p.name, {}).get("resize_land_id"):
                self.show_land_resize_confirm_panel(p)
            else:
                self.show_pending_land_purchase_panel(p)

        form = ModalForm(
            title=self.language_manager.GetText("LAND_PENDING_EDIT_COORD_TITLE"),
            controls=controls,
            on_submit=on_submit,
            on_close=None,
        )
        player.send_form(form)

    def player_buy_new_land(self, player: Player, dimension: str,
                            min_x: int, max_x: int, min_y: int, max_y: int, min_z: int, max_z: int,
                            volume: int, money_cost: int, used_free_blocks: int = 0):
        if not self._ensure_land_claim_allowed(player):
            return
        if self.judge_if_player_has_enough_money(player, money_cost) or player.is_op:
            paid_money = float(money_cost) if not player.is_op else 0.0
            land_id = self.create_land(
                str(player.xuid),
                self.language_manager.GetText('DEFAULT_LAND_NAME').format(player.name, self.get_player_land_count(str(player.xuid)) + 1),
                dimension, min_x, max_x, min_y, max_y, min_z, max_z,
                player.location.x, player.location.y, player.location.z,
                owner_paid_money=paid_money,
                actor=player,
            )
            if land_id is not None:
                if not player.is_op:
                    if money_cost > 0:
                        if not self.decrease_player_money(player, money_cost):
                            self.report_arc_error(
                                "LAND_PAY1",
                                f"player_buy_new_land land_id={land_id} created but decrease_money failed cost={money_cost!r}",
                                player,
                            )

                    if used_free_blocks > 0:
                        current_free_blocks = self.get_player_free_land_blocks(player)
                        new_free_blocks = max(0, current_free_blocks - used_free_blocks)
                        self.set_player_free_land_blocks(player, new_free_blocks)
                        self._notify_important(
                            player,
                            self.language_manager.GetText('USE_FREE_BLOCKS_HINT').format(used_free_blocks),
                            title=self._toast_title("LAND_RESULT_TOAST_TITLE", "领地"),
                        )

                create_msg = self.language_manager.GetText('LAND_CREATE_PRIVATE_SUCCESS')
                if not (create_msg and str(create_msg).strip()):
                    create_msg = "[弧光核心]私人领地 #{0} 创建成功。"
                self._notify_important(
                    player,
                    create_msg.format(land_id),
                    title=self._toast_title("LAND_RESULT_TOAST_TITLE", "领地"),
                )
                self.clear_new_land_creation_info_memory(player)
                self.show_own_land_detail_panel(player, land_id, self.get_land_info(land_id))
            else:
                self.report_arc_error(
                    "LAND26",
                    f"player_buy_new_land create_land returned None player={player.name!r} dim={dimension!r}",
                    player,
                )
        else:
            player.send_message(self.language_manager.GetText('PAY_FAIL_NO_ENOUGH_MONEY').format(
                self._format_money_display(money_cost),
                self._format_money_display(self.get_player_money(player))))

    def show_create_public_land_priority_modal(
        self,
        player: Player,
        dimension: str,
        min_x: int,
        max_x: int,
        min_y: int,
        max_y: int,
        min_z: int,
        max_z: int,
        default_allow_non_public: bool = False,
    ) -> None:
        """创建公共领地前选择优先级（1/2/3）及是否允许私人/公会覆盖。"""
        if not self._ensure_land_claim_allowed(player):
            return
        if not player.is_op:
            player.send_message(
                self.language_manager.GetText("LAND_CREATE_PUBLIC_NEED_OP")
            )
            self.show_pending_land_purchase_panel(player)
            return
        priority_dropdown = Dropdown(
            label=self.language_manager.GetText("PUBLIC_LAND_PRIORITY_DROPDOWN_LABEL"),
            options=[
                self.language_manager.GetText("PUBLIC_LAND_PRIORITY_OPTION_1"),
                self.language_manager.GetText("PUBLIC_LAND_PRIORITY_OPTION_2"),
                self.language_manager.GetText("PUBLIC_LAND_PRIORITY_OPTION_3"),
            ],
            default_index=0,
        )
        allow_private_dropdown = Dropdown(
            label=self.language_manager.GetText(
                "PUBLIC_LAND_ALLOW_PRIVATE_DROPDOWN_LABEL"
            ),
            options=[
                self.language_manager.GetText(
                    "PUBLIC_LAND_ALLOW_PRIVATE_OPTION_DENY"
                ),
                self.language_manager.GetText(
                    "PUBLIC_LAND_ALLOW_PRIVATE_OPTION_ALLOW"
                ),
            ],
            default_index=1 if default_allow_non_public else 0,
        )

        def on_submit(p: Player, json_str: str):
            try:
                data = json.loads(json_str)
                if self._modal_choice_is_back(data, 0):
                    self.show_pending_land_purchase_panel(p)
                    return
                try:
                    idx = int(data[1])
                except (TypeError, ValueError, IndexError):
                    idx = 0
                try:
                    allow_idx = int(data[2])
                except (TypeError, ValueError, IndexError):
                    allow_idx = 0
                priority = LandSystem.clamp_public_priority(idx + 1)
                allow_non_public = allow_idx == 1
                self.player_create_public_land_from_pending(
                    p,
                    dimension,
                    min_x,
                    max_x,
                    min_y,
                    max_y,
                    min_z,
                    max_z,
                    priority,
                    allow_non_public_land=allow_non_public,
                )
            except Exception as e:
                self.logger.error(f"create public land priority modal error: {e}")
                self.show_pending_land_purchase_panel(p)

        form = ModalForm(
            title=self.language_manager.GetText("PUBLIC_LAND_PRIORITY_SELECT_TITLE"),
            controls=[
                self._modal_nav_dropdown(),
                priority_dropdown,
                allow_private_dropdown,
            ],
            on_close=None,
            on_submit=on_submit,
        )
        player.send_form(form)

    def player_create_public_land_from_pending(
        self,
        player: Player,
        dimension: str,
        min_x: int,
        max_x: int,
        min_y: int,
        max_y: int,
        min_z: int,
        max_z: int,
        public_priority: int = LandSystem.PUBLIC_PRIORITY_DEFAULT,
        allow_non_public_land: bool = False,
    ) -> None:
        """圈地确认：创建公共领地（仅 OP，不扣款）。"""
        if not self._ensure_land_claim_allowed(player):
            return
        if not player.is_op:
            player.send_message(
                self.language_manager.GetText("LAND_CREATE_PUBLIC_NEED_OP")
            )
            self.show_pending_land_purchase_panel(player)
            return
        priority = LandSystem.clamp_public_priority(public_priority)
        allow_private = bool(allow_non_public_land)
        if_allowed, reason, overlap_ids = self.check_land_availability(
            dimension,
            min_x,
            max_x,
            min_y,
            max_y,
            min_z,
            max_z,
            creating_public_priority=priority,
            creating_allow_non_public_land=allow_private,
        )
        if not if_allowed:
            if reason == "SYSTEM_ERROR":
                self.report_arc_error(
                    "LAND_PUBLIC1",
                    f"player_create_public_land check_land_availability SYSTEM_ERROR dim={dimension!r}",
                    player,
                )
            msg = self.language_manager.GetText(f"CHECK_NEW_LAND_AVAILABILITY_FAIL_{reason}")
            if overlap_ids:
                land_parts = [
                    f"#{lid} {self.get_land_name(lid) or ''}".strip() for lid in overlap_ids
                ]
                msg = msg + "\n" + self.language_manager.GetText(
                    "LAND_OVERLAP_WITH_LANDS"
                ).format(", ".join(land_parts))
            player.send_message(msg)
            self.show_pending_land_purchase_panel(player)
            return
        land_name = self.language_manager.GetText("DEFAULT_PUBLIC_LAND_NAME").format(
            player.name or "?"
        )
        land_id = self.create_land(
            LandSystem.LAND_OWNER_PUBLIC,
            land_name,
            dimension,
            min_x,
            max_x,
            min_y,
            max_y,
            min_z,
            max_z,
            player.location.x,
            player.location.y,
            player.location.z,
            owner_paid_money=0.0,
            public_priority=priority,
            actor=player,
        )
        if land_id is not None:
            self.set_land_as_public(land_id, public_priority=priority)
            if allow_private:
                self.land_system.set_land_allow_non_public_land(land_id, True)
            self.clear_new_land_creation_info_memory(player)
            success_key = (
                "LAND_CREATE_PUBLIC_SUCCESS_ALLOW_PRIVATE"
                if allow_private
                else "LAND_CREATE_PUBLIC_SUCCESS"
            )
            self._notify_important(
                player,
                self.language_manager.GetText(success_key).format(land_id, priority),
                title=self._toast_title("LAND_RESULT_TOAST_TITLE", "领地"),
            )
            self.show_land_main_menu(player)
        else:
            self.report_arc_error(
                "LAND_PUBLIC2",
                f"player_create_public_land create_land returned None player={player.name!r}",
                player,
            )
            self.show_pending_land_purchase_panel(player)

    def player_create_guild_land_from_pending(
        self,
        player: Player,
        dimension: str,
        min_x: int,
        max_x: int,
        min_y: int,
        max_y: int,
        min_z: int,
        max_z: int,
        contrib_cost: int,
    ) -> None:
        """圈地确认：创建公会领地，消耗公会公共贡献点（会长/管理者）。"""
        if not self._ensure_land_claim_allowed(player):
            return
        mem = self.guild_system.get_membership(str(player.xuid))
        if not mem:
            player.send_message(self._guild_err("GUILD_NOT_IN_GUILD"))
            self.show_pending_land_purchase_panel(player)
            return
        role = str(mem.get("role") or "")
        if role not in (ROLE_OWNER, ROLE_MANAGER):
            player.send_message(self._guild_err("GUILD_NO_PERMISSION"))
            self.show_pending_land_purchase_panel(player)
            return
        gid = int(mem.get("guild_id") or 0)
        if gid <= 0:
            player.send_message(self._guild_err("GUILD_NOT_FOUND"))
            self.show_pending_land_purchase_panel(player)
            return
        cost = int(contrib_cost)
        if cost <= 0:
            self.show_pending_land_purchase_panel(player)
            return
        if_allowed, reason, overlap_ids = self.check_land_availability(
            dimension, min_x, max_x, min_y, max_y, min_z, max_z
        )
        if not if_allowed:
            if reason == "SYSTEM_ERROR":
                self.report_arc_error(
                    "LAND_GUILD1",
                    f"player_create_guild_land check_land_availability SYSTEM_ERROR dim={dimension!r}",
                    player,
                )
            msg = self.language_manager.GetText(f"CHECK_NEW_LAND_AVAILABILITY_FAIL_{reason}")
            if overlap_ids:
                land_parts = [
                    f"#{lid} {self.get_land_name(lid) or ''}".strip() for lid in overlap_ids
                ]
                msg = msg + "\n" + self.language_manager.GetText(
                    "LAND_OVERLAP_WITH_LANDS"
                ).format(", ".join(land_parts))
            player.send_message(msg)
            self.show_pending_land_purchase_panel(player)
            return
        if self.guild_system.get_guild_total_contribution(gid) < cost:
            player.send_message(self._guild_err("GUILD_CONTRIB_NOT_ENOUGH"))
            self.show_pending_land_purchase_panel(player)
            return
        ok_consume, err_c, new_total = self.guild_system.consume_guild_contribution(
            gid, cost
        )
        if not ok_consume:
            player.send_message(self._guild_err(err_c or "GUILD_DB_ERROR"))
            self.show_pending_land_purchase_panel(player)
            return
        g = self.guild_system.get_guild(gid)
        gname = (
            guild_strip_mc_color_codes(g.get("name") or "").strip()
            if g
            else str(gid)
        )
        idx = self.land_system.get_guild_land_count(gid) + 1
        land_name = self.language_manager.GetText("DEFAULT_GUILD_LAND_NAME").format(
            gname, idx
        )
        land_id = self.create_land(
            LandSystem.land_owner_key_guild(gid),
            land_name,
            dimension,
            min_x,
            max_x,
            min_y,
            max_y,
            min_z,
            max_z,
            player.location.x,
            player.location.y,
            player.location.z,
            owner_paid_money=0.0,
            actor=player,
        )
        if land_id is None:
            if not self.guild_system.refund_guild_contribution_pool(gid, cost):
                self.report_arc_error(
                    "LAND_GUILD2",
                    f"player_create_guild_land create failed and refund failed gid={gid} cost={cost!r}",
                    player,
                )
            player.send_message(
                self.language_manager.GetText("LAND_CREATE_GUILD_DB_FAIL")
            )
            self.show_pending_land_purchase_panel(player)
            return
        self.clear_new_land_creation_info_memory(player)
        self._notify_important(
            player,
            self.language_manager.GetText("LAND_CREATE_GUILD_SUCCESS").format(
                land_id, cost, int(new_total)
            ),
            title=self._toast_title("LAND_RESULT_TOAST_TITLE", "领地"),
        )
        self.show_land_main_menu(player)

    def _land_resize_sub_lands_within_bounds(
        self,
        parent_land_id: int,
        min_x: int,
        max_x: int,
        min_y: int,
        max_y: int,
        min_z: int,
        max_z: int,
    ) -> tuple:
        """子领地是否完全落在新父领地盒内。返回 (True, None) 或 (False, sub_land_id)。"""
        for sl_id, sl in self.get_sub_lands_by_parent(parent_land_id).items():
            if not (
                min_x <= sl["min_x"]
                and sl["max_x"] <= max_x
                and min_y <= sl["min_y"]
                and sl["max_y"] <= max_y
                and min_z <= sl["min_z"]
                and sl["max_z"] <= max_z
            ):
                return False, sl_id
        return True, None

    def _land_resize_clamp_tp_if_needed(
        self,
        land_id: int,
        min_x: int,
        max_x: int,
        min_y: int,
        max_y: int,
        min_z: int,
        max_z: int,
    ) -> None:
        info = self.get_land_info(land_id)
        if not info:
            return
        tx = float(info.get("tp_x", 0))
        ty = float(info.get("tp_y", 0))
        tz = float(info.get("tp_z", 0))
        if (
            min_x <= tx <= max_x
            and min_z <= tz <= max_z
            and min_y <= ty <= max_y
        ):
            return
        cx = int((min_x + max_x) // 2)
        cy = int(min_y)
        cz = int((min_z + max_z) // 2)
        self.land_system.set_land_teleport_point(land_id, cx, cy, cz)

    def _player_start_land_resize_redemarcation(self, player: Player, land_id: int):
        def cancel_back_to_detail(p: Player):
            land_info = self.get_land_info(land_id)
            if land_info:
                self.show_own_land_detail_panel(p, land_id, land_info)
            else:
                self.show_own_land_menu(p)

        self.require_sensitive_password_verified(
            player,
            lambda p, lid=land_id: self._player_start_land_resize_redemarcation_impl(p, lid),
            on_cancel=cancel_back_to_detail,
        )

    def _player_start_land_resize_redemarcation_impl(self, player: Player, land_id: int) -> None:
        land_info = self.get_land_info(land_id)
        if not land_info:
            player.send_message(self.language_manager.GetText("LAND_RESIZE_LAND_GONE"))
            return
        if land_info.get("for_sale"):
            player.send_message(self.language_manager.GetText("LAND_RESIZE_FORBIDDEN_ON_SALE"))
            return
        owner_key = str(land_info.get("owner_xuid") or "")
        if self.land_system.is_public_land_owner(owner_key):
            player.send_message(self.language_manager.GetText("LAND_RESIZE_NOT_OWNER"))
            return
        resize_mode = None
        if LandSystem.parse_land_owner_guild_id(owner_key) is not None:
            resize_mode = "guild"
            mem = self.guild_system.get_membership(str(player.xuid))
            if not mem or mem.get("role") not in (ROLE_OWNER, ROLE_MANAGER):
                player.send_message(self.language_manager.GetText("LAND_RESIZE_GUILD_NO_PERM"))
                return
            gid = LandSystem.parse_land_owner_guild_id(owner_key)
            if not gid or int(mem.get("guild_id") or 0) != int(gid):
                player.send_message(self.language_manager.GetText("LAND_RESIZE_NOT_YOUR_GUILD_LAND"))
                return
        elif self._player_matches_land_owner_key(player, owner_key):
            resize_mode = "player"
        else:
            player.send_message(self.language_manager.GetText("LAND_RESIZE_NOT_OWNER"))
            return
        if get_dimension_id(player.location.dimension) != land_info["dimension"]:
            player.send_message(
                self.language_manager.GetText("LAND_RESIZE_WRONG_DIMENSION").format(
                    self._translate_dimension_name(land_info["dimension"])
                )
            )
            return
        self._begin_land_resize_pick(player, land_id, resize_mode, op_from_page=None)

    def _op_start_public_land_resize(self, player: Player, land_id: int, from_page: int):
        if not getattr(player, "is_op", False):
            player.send_message(self.language_manager.GetText("LAND_RESIZE_OP_ONLY"))
            return
        if not self.is_public_land(land_id):
            player.send_message(self.language_manager.GetText("LAND_RESIZE_OP_PUBLIC_ONLY"))
            return
        land_info = self.get_land_info(land_id)
        if not land_info:
            player.send_message(self.language_manager.GetText("LAND_RESIZE_LAND_GONE"))
            return
        if get_dimension_id(player.location.dimension) != land_info["dimension"]:
            player.send_message(
                self.language_manager.GetText("LAND_RESIZE_WRONG_DIMENSION").format(
                    self._translate_dimension_name(land_info["dimension"])
                )
            )
            return
        self._begin_land_resize_pick(player, land_id, "op_public", op_from_page=from_page)

    def _begin_land_resize_pick(
        self,
        player: Player,
        land_id: int,
        resize_mode: str,
        op_from_page: Optional[int],
    ):
        self.player_new_land_creation_info.pop(player.name, None)
        self.player_land_pos1.pop(player.name, None)
        dim = self.get_land_dimension(land_id)
        st: Dict[str, Any] = {
            "step": "rect_a",
            "dimension": dim,
            "resize_land_id": land_id,
            "resize_mode": resize_mode,
        }
        if op_from_page is not None:
            st["resize_op_from_page"] = op_from_page
        self.player_land_creation_pick[player.name] = st
        self.player_land_pick_last_event_ts.pop(player.name, None)
        player.send_message(self.language_manager.GetText("LAND_RESIZE_PICK_RECT_A"))

    def show_land_resize_confirm_panel(self, player: Player):
        """重设领地范围：校验、展示补/退差价，确认后更新 bounds 与 chunk 索引。"""
        info = self.player_new_land_creation_info.get(player.name)
        if not info or info.get("resize_land_id") is None:
            player.send_message(self.language_manager.GetText("LANDBUY_NO_PENDING_LAND"))
            return
        land_id = int(info["resize_land_id"])
        resize_mode = str(info.get("resize_mode") or "player")
        land_info = self.get_land_info(land_id)
        if not land_info:
            self.clear_new_land_creation_info_memory(player)
            player.send_message(self.language_manager.GetText("LAND_RESIZE_LAND_GONE"))
            return
        if land_info.get("for_sale"):
            player.send_message(self.language_manager.GetText("LAND_RESIZE_FORBIDDEN_ON_SALE"))
            self.clear_new_land_creation_info_memory(player)
            return

        dimension = info["dimension"]
        min_x, max_x = info["min_x"], info["max_x"]
        min_y, max_y = info["min_y"], info["max_y"]
        min_z, max_z = info["min_z"], info["max_z"]
        if dimension != land_info["dimension"]:
            player.send_message(self.language_manager.GetText("LAND_RESIZE_DIM_MISMATCH"))
            return

        creating_public_priority = None
        creating_allow_non_public = False
        if self.is_public_land(land_id):
            creating_public_priority = LandSystem.clamp_public_priority(
                land_info.get("public_priority", LandSystem.PUBLIC_PRIORITY_DEFAULT)
            )
            creating_allow_non_public = bool(land_info.get("allow_non_public_land"))
        if_allowed, reason, overlap_ids = self.check_land_availability(
            dimension,
            min_x,
            max_x,
            min_y,
            max_y,
            min_z,
            max_z,
            exclude_land_ids={land_id},
            creating_public_priority=creating_public_priority,
            creating_allow_non_public_land=creating_allow_non_public,
        )
        if not if_allowed:
            if reason == "SYSTEM_ERROR":
                self.report_arc_error(
                    "LAND_RESIZE1",
                    f"show_land_resize_confirm_panel check_land_availability SYSTEM_ERROR land_id={land_id!r}",
                    player,
                )
            msg = self.language_manager.GetText(f"CHECK_NEW_LAND_AVAILABILITY_FAIL_{reason}")
            if overlap_ids:
                land_parts = [
                    f"#{lid} {self.get_land_name(lid) or ''}".strip() for lid in overlap_ids
                ]
                msg = msg + "\n" + self.language_manager.GetText("LAND_OVERLAP_WITH_LANDS").format(
                    ", ".join(land_parts)
                )
            player.send_message(msg)
            return

        ok_sub, bad_sl = self._land_resize_sub_lands_within_bounds(
            land_id, min_x, max_x, min_y, max_y, min_z, max_z
        )
        if not ok_sub:
            player.send_message(
                self.language_manager.GetText("LAND_RESIZE_SUB_OUTSIDE").format(bad_sl)
            )
            return

        length = max_x - min_x + 1
        width = max_z - min_z + 1
        if length <= self.land_min_size or width <= self.land_min_size:
            player.send_message(
                self.language_manager.GetText("CREATE_NEW_LAND_SIZE_TOO_SMALL").format(
                    length, width, self.land_min_size
                )
            )
            return

        old_vol = (
            (land_info["max_x"] - land_info["min_x"] + 1)
            * (land_info.get("max_y", 255) - land_info.get("min_y", 0) + 1)
            * (land_info["max_z"] - land_info["min_z"] + 1)
        )
        new_vol = (max_x - min_x + 1) * (max_y - min_y + 1) * (max_z - min_z + 1)
        delta_vol = new_vol - old_vol

        price_lines: List[str] = []
        money_charge = 0
        money_refund = 0.0
        used_free_blocks = 0
        guild_contrib_charge = 0
        guild_contrib_refund = 0

        if resize_mode == "op_public":
            price_lines.append(self.language_manager.GetText("LAND_RESIZE_PRICE_OP_PUBLIC"))
        elif resize_mode == "guild":
            lp = int(self.land_price)
            if delta_vol > 0:
                guild_contrib_charge = int(delta_vol * lp)
                price_lines.append(
                    self.language_manager.GetText("LAND_RESIZE_PRICE_GUILD_EXPAND").format(
                        guild_contrib_charge
                    )
                )
            elif delta_vol < 0:
                guild_contrib_refund = int(abs(delta_vol) * lp)
                price_lines.append(
                    self.language_manager.GetText("LAND_RESIZE_PRICE_GUILD_SHRINK").format(
                        guild_contrib_refund
                    )
                )
            else:
                price_lines.append(self.language_manager.GetText("LAND_RESIZE_PRICE_NO_CHANGE"))
        else:
            if delta_vol > 0:
                remaining_free = self.get_player_free_land_blocks(player)
                used_free_blocks = min(delta_vol, remaining_free)
                paid_vol = delta_vol - used_free_blocks
                money_charge = int(paid_vol * int(self.land_price))
                if used_free_blocks > 0 and money_charge > 0:
                    price_lines.append(
                        self.language_manager.GetText("LAND_RESIZE_PRICE_PLAYER_EXPAND_BOTH").format(
                            self._format_money_display(money_charge),
                            used_free_blocks,
                        )
                    )
                elif used_free_blocks > 0:
                    price_lines.append(
                        self.language_manager.GetText("LAND_RESIZE_PRICE_PLAYER_EXPAND_FREE").format(
                            used_free_blocks
                        )
                    )
                else:
                    price_lines.append(
                        self.language_manager.GetText("LAND_RESIZE_PRICE_PLAYER_EXPAND_MONEY").format(
                            self._format_money_display(money_charge)
                        )
                    )
                if not player.is_op and money_charge > 0:
                    pm = self.get_player_money(player)
                    if pm < money_charge:
                        price_lines.append(
                            self.language_manager.GetText("LAND_RESIZE_CANNOT_AFFORD").format(
                                self._format_money_display(money_charge),
                                self._format_money_display(pm),
                            )
                        )
            elif delta_vol < 0:
                money_refund = round(
                    abs(delta_vol) * float(self.land_price) * self.land_sell_refund_coefficient,
                    2,
                )
                price_lines.append(
                    self.language_manager.GetText("LAND_RESIZE_PRICE_PLAYER_SHRINK").format(
                        self._format_money_display(money_refund),
                        self.land_sell_refund_coefficient,
                    )
                )
            else:
                price_lines.append(self.language_manager.GetText("LAND_RESIZE_PRICE_NO_CHANGE"))

        header = self.language_manager.GetText("LAND_RESIZE_CONFIRM_HEADER").format(
            land_id,
            land_info["land_name"],
            old_vol,
            new_vol,
            delta_vol,
            dimension,
            (min_x, min_y, min_z),
            (max_x, max_y, max_z),
        )
        content = header + "\n" + "\n".join(price_lines) + "\n"
        content += self.language_manager.GetText("LAND_RESIZE_CONFIRM_FOOTER")

        op_pg = info.get("resize_op_from_page")
        if op_pg is None:
            op_pg = 0

        def _resize_close(p: Player):
            self.clear_new_land_creation_info_memory(p)
            if resize_mode == "op_public":
                self.show_op_land_detail_panel(p, land_id, int(op_pg))
            else:
                li = self.get_land_info(land_id)
                if li:
                    self.show_own_land_detail_panel(p, land_id, li)
                else:
                    self.show_own_land_menu(p)

        form = ActionForm(
            title=self.language_manager.GetText("LAND_RESIZE_CONFIRM_TITLE"),
            content=content,
            on_close=None,
        )

        def _resize_preview(p: Player):
            self._visualize_pending_land(p)
            p.send_message(self.language_manager.GetText("LAND_CURRENT_POSITION_PARTICLE_DISPLAY"))

        form.add_button(
            self.language_manager.GetText("LAND_PENDING_PREVIEW_BUTTON"),
            on_click=_resize_preview,
        )
        form.add_button(
            self.language_manager.GetText("LAND_PENDING_EDIT_COORD_BUTTON"),
            on_click=self.show_edit_pending_land_coordinates_modal,
        )
        form.add_button(
            self.language_manager.GetText("LAND_PENDING_MANUAL_INPUT_BUTTON"),
            on_click=self.show_create_new_land_guide,
        )
        form.add_button(
            self.language_manager.GetText("LAND_PENDING_RESTART_PICK_BUTTON"),
            on_click=lambda p, lid=land_id, mode=resize_mode, pg=op_pg: self._begin_land_resize_pick(
                p, lid, mode, op_from_page=(pg if mode == "op_public" else None)
            ),
        )

        can_confirm = True
        if resize_mode == "player" and delta_vol > 0 and money_charge > 0 and not player.is_op:
            if self.get_player_money(player) < money_charge:
                can_confirm = False
        if resize_mode == "guild" and delta_vol > 0:
            gid = LandSystem.parse_land_owner_guild_id(str(land_info.get("owner_xuid") or ""))
            if gid and self.guild_system.get_guild_total_contribution(int(gid)) < guild_contrib_charge:
                can_confirm = False

        if can_confirm:
            form.add_button(
                self.language_manager.GetText("LAND_RESIZE_CONFIRM_BUTTON"),
                on_click=lambda p, lid=land_id, mode=resize_mode, dim=dimension,
                x1=min_x, x2=max_x, y1=min_y, y2=max_y, z1=min_z, z2=max_z,
                ov=old_vol, nv=new_vol, dv=delta_vol, mc=money_charge, mr=money_refund,
                uf=used_free_blocks, gcc=guild_contrib_charge, gcr=guild_contrib_refund,
                pg=op_pg: self._execute_land_resize_commit(
                    p,
                    land_id=lid,
                    resize_mode=mode,
                    dimension=dim,
                    min_x=x1,
                    max_x=x2,
                    min_y=y1,
                    max_y=y2,
                    min_z=z1,
                    max_z=z2,
                    old_volume=ov,
                    new_volume=nv,
                    delta_volume=dv,
                    money_charge=mc,
                    money_refund=mr,
                    used_free_blocks=uf,
                    guild_contrib_charge=gcc,
                    guild_contrib_refund=gcr,
                    op_from_page=int(pg),
                ),
            )
        else:
            form.add_button(self.language_manager.GetText("LAND_RESIZE_CANNOT_CONFIRM_BUTTON"))

        form.add_button(self.language_manager.GetText("RETURN_BUTTON_TEXT"), on_click=_resize_close)
        player.send_form(form)

    def _execute_land_resize_commit(
        self,
        player: Player,
        *,
        land_id: int,
        resize_mode: str,
        dimension: str,
        min_x: int,
        max_x: int,
        min_y: int,
        max_y: int,
        min_z: int,
        max_z: int,
        old_volume: int,
        new_volume: int,
        delta_volume: int,
        money_charge: int,
        money_refund: float,
        used_free_blocks: int,
        guild_contrib_charge: int,
        guild_contrib_refund: int,
        op_from_page: int,
    ):
        land_info = self.get_land_info(land_id)
        if not land_info or land_info.get("for_sale"):
            player.send_message(self.language_manager.GetText("LAND_RESIZE_COMMIT_ABORT"))
            return
        creating_public_priority = None
        creating_allow_non_public = False
        if self.is_public_land(land_id):
            creating_public_priority = LandSystem.clamp_public_priority(
                land_info.get("public_priority", LandSystem.PUBLIC_PRIORITY_DEFAULT)
            )
            creating_allow_non_public = bool(land_info.get("allow_non_public_land"))
        if_allowed, reason, overlap_ids = self.check_land_availability(
            dimension,
            min_x,
            max_x,
            min_y,
            max_y,
            min_z,
            max_z,
            exclude_land_ids={land_id},
            creating_public_priority=creating_public_priority,
            creating_allow_non_public_land=creating_allow_non_public,
        )
        if not if_allowed:
            player.send_message(self.language_manager.GetText("LAND_RESIZE_COMMIT_OVERLAP"))
            return
        ok_sub, bad_sl = self._land_resize_sub_lands_within_bounds(
            land_id, min_x, max_x, min_y, max_y, min_z, max_z
        )
        if not ok_sub:
            player.send_message(
                self.language_manager.GetText("LAND_RESIZE_SUB_OUTSIDE").format(bad_sl)
            )
            return

        recalc_old = (
            (land_info["max_x"] - land_info["min_x"] + 1)
            * (land_info.get("max_y", 255) - land_info.get("min_y", 0) + 1)
            * (land_info["max_z"] - land_info["min_z"] + 1)
        )
        recalc_new = (max_x - min_x + 1) * (max_y - min_y + 1) * (max_z - min_z + 1)
        if recalc_old != old_volume or recalc_new != new_volume:
            player.send_message(self.language_manager.GetText("LAND_RESIZE_COMMIT_STALE"))
            self.show_land_resize_confirm_panel(player)
            return

        old_paid = float(land_info.get("owner_paid_money") or 0)
        guild_extra_msg: Optional[str] = None

        if resize_mode == "op_public":
            if not self.land_system.update_land_bounds(
                land_id, min_x, max_x, min_y, max_y, min_z, max_z, None
            ):
                player.send_message(self.language_manager.GetText("LAND_RESIZE_COMMIT_DB_FAIL"))
                return

        elif resize_mode == "guild":
            gid = LandSystem.parse_land_owner_guild_id(str(land_info.get("owner_xuid") or ""))
            if not gid:
                player.send_message(self.language_manager.GetText("LAND_RESIZE_COMMIT_ABORT"))
                return
            if delta_volume > 0:
                cost = int(delta_volume * int(self.land_price))
                if cost != guild_contrib_charge:
                    player.send_message(self.language_manager.GetText("LAND_RESIZE_COMMIT_STALE"))
                    self.show_land_resize_confirm_panel(player)
                    return
                ok_c, err_c, _nt = self.guild_system.consume_guild_contribution(int(gid), cost)
                if not ok_c:
                    player.send_message(self._guild_err(err_c or "GUILD_DB_ERROR"))
                    return
                if not self.land_system.update_land_bounds(
                    land_id, min_x, max_x, min_y, max_y, min_z, max_z, None
                ):
                    self.guild_system.refund_guild_contribution_pool(int(gid), cost)
                    player.send_message(self.language_manager.GetText("LAND_RESIZE_COMMIT_DB_FAIL"))
                    return
                guild_extra_msg = self.language_manager.GetText(
                    "LAND_RESIZE_SUCCESS_GUILD"
                ).format(land_id, cost)
            elif delta_volume < 0:
                ref = int(abs(delta_volume) * int(self.land_price))
                if ref != guild_contrib_refund:
                    player.send_message(self.language_manager.GetText("LAND_RESIZE_COMMIT_STALE"))
                    self.show_land_resize_confirm_panel(player)
                    return
                if not self.land_system.update_land_bounds(
                    land_id, min_x, max_x, min_y, max_y, min_z, max_z, None
                ):
                    player.send_message(self.language_manager.GetText("LAND_RESIZE_COMMIT_DB_FAIL"))
                    return
                if ref > 0:
                    self.guild_system.refund_guild_contribution_pool(int(gid), ref)
                guild_extra_msg = self.language_manager.GetText(
                    "LAND_RESIZE_SUCCESS_GUILD_REFUND"
                ).format(land_id, ref)
            else:
                if not self.land_system.update_land_bounds(
                    land_id, min_x, max_x, min_y, max_y, min_z, max_z, None
                ):
                    player.send_message(self.language_manager.GetText("LAND_RESIZE_COMMIT_DB_FAIL"))
                    return

        else:
            if player.is_op:
                if not self.land_system.update_land_bounds(
                    land_id, min_x, max_x, min_y, max_y, min_z, max_z, None
                ):
                    player.send_message(self.language_manager.GetText("LAND_RESIZE_COMMIT_DB_FAIL"))
                    return
            elif delta_volume > 0:
                paid_vol = delta_volume - used_free_blocks
                if paid_vol * int(self.land_price) != money_charge:
                    player.send_message(self.language_manager.GetText("LAND_RESIZE_COMMIT_STALE"))
                    self.show_land_resize_confirm_panel(player)
                    return
                if money_charge > 0:
                    if not self.decrease_player_money(player, money_charge):
                        player.send_message(
                            self.language_manager.GetText("LAND_RESIZE_COMMIT_PAY_FAIL")
                        )
                        return
                if used_free_blocks > 0:
                    cur_f = self.get_player_free_land_blocks(player)
                    self.set_player_free_land_blocks(
                        player, max(0, cur_f - used_free_blocks)
                    )
                new_paid = old_paid + float(money_charge)
                if not self.land_system.update_land_bounds(
                    land_id, min_x, max_x, min_y, max_y, min_z, max_z, new_paid
                ):
                    if money_charge > 0:
                        self.increase_player_money(player, money_charge)
                    if used_free_blocks > 0:
                        cur_f = self.get_player_free_land_blocks(player)
                        self.set_player_free_land_blocks(player, cur_f + used_free_blocks)
                    player.send_message(self.language_manager.GetText("LAND_RESIZE_COMMIT_DB_FAIL"))
                    return
            elif delta_volume < 0:
                expect_refund = round(
                    abs(delta_volume) * float(self.land_price) * self.land_sell_refund_coefficient,
                    2,
                )
                if abs(expect_refund - money_refund) > 0.01:
                    player.send_message(self.language_manager.GetText("LAND_RESIZE_COMMIT_STALE"))
                    self.show_land_resize_confirm_panel(player)
                    return
                new_paid = max(
                    0.0, old_paid - abs(delta_volume) * float(self.land_price)
                )
                if not self.land_system.update_land_bounds(
                    land_id, min_x, max_x, min_y, max_y, min_z, max_z, new_paid
                ):
                    player.send_message(self.language_manager.GetText("LAND_RESIZE_COMMIT_DB_FAIL"))
                    return
                if expect_refund > 0:
                    self.increase_player_money(player, expect_refund)
            else:
                if not self.land_system.update_land_bounds(
                    land_id, min_x, max_x, min_y, max_y, min_z, max_z, None
                ):
                    player.send_message(self.language_manager.GetText("LAND_RESIZE_COMMIT_DB_FAIL"))
                    return

        self._land_resize_clamp_tp_if_needed(
            land_id, min_x, max_x, min_y, max_y, min_z, max_z
        )
        self.clear_new_land_creation_info_memory(player)
        if guild_extra_msg:
            self._notify_important(
                player,
                guild_extra_msg,
                title=self._toast_title("LAND_RESULT_TOAST_TITLE", "领地"),
            )
        else:
            self._notify_important(
                player,
                self.language_manager.GetText("LAND_RESIZE_SUCCESS").format(land_id),
                title=self._toast_title("LAND_RESULT_TOAST_TITLE", "领地"),
            )
        if resize_mode == "op_public":
            self.show_op_land_detail_panel(player, land_id, op_from_page)
        else:
            self.show_own_land_detail_panel(player, land_id, self.get_land_info(land_id))

    # ─── Sub-land UI ─────────────────────────────────────────────────────────────

    def show_sub_land_manage_panel(self, player: Player, land_id: int):
        """领地主人管理子领地：查看所有子领地 + 新建"""
        sub_lands = self.get_sub_lands_by_parent(land_id)
        land_info = self.get_land_info(land_id)
        if not land_info:
            self.report_arc_error(
                "LAND27",
                f"show_sub_land_manage_panel get_land_info empty land_id={land_id!r}",
                player,
            )
            return
        panel = ActionForm(
            title=self.language_manager.GetText('SUB_LAND_MANAGE_PANEL_TITLE'),
            content=self.language_manager.GetText('SUB_LAND_MANAGE_PANEL_CONTENT').format(len(sub_lands)),
            on_close=None,
        )
        panel.add_button(
            self.language_manager.GetText('SUB_LAND_CREATE_BUTTON_TEXT'),
            on_click=lambda p=player, l_id=land_id: self.show_create_sub_land_form(p, l_id)
        )
        for sl_id, sl_info in sub_lands.items():
            owner_name = self.format_land_owner_key_display(sl_info['owner_xuid'])
            panel.add_button(
                self.language_manager.GetText('SUB_LAND_LIST_BUTTON_TEXT').format(sl_id, sl_info['sub_land_name'], owner_name),
                on_click=lambda p=player, sl=sl_id: self.show_sub_land_detail_panel(p, sl)
            )
        panel.add_button(
            self.language_manager.GetText('RETURN_BUTTON_TEXT'),
            on_click=lambda p=player, l_id=land_id, l_info=land_info: self.show_own_land_detail_panel(p, l_id, l_info)
        )
        player.send_form(panel)

    def show_create_sub_land_form(self, player: Player, land_id: int):
        """显示创建子领地的 XYZ 输入表单"""
        parent_info = self.get_land_info(land_id)
        if not parent_info:
            self.report_arc_error(
                "LAND28",
                f"show_create_sub_land_form get_land_info empty land_id={land_id!r}",
                player,
            )
            return

        p_min_x, p_max_x = parent_info['min_x'], parent_info['max_x']
        p_min_y, p_max_y = parent_info.get('min_y', 0), parent_info.get('max_y', 255)
        p_min_z, p_max_z = parent_info['min_z'], parent_info['max_z']

        default_px = str(math.floor(player.location.x))
        default_py = str(math.floor(player.location.y))
        default_pz = str(math.floor(player.location.z))

        hint_label = self.language_manager.GetText('SUB_LAND_FORM_HINT').format(
            p_min_x, p_min_y, p_min_z, p_max_x, p_max_y, p_max_z
        )

        def _back(p):
            self.show_sub_land_manage_panel(p, land_id)

        controls = [
            Label(text=hint_label),
            TextInput(label=self.language_manager.GetText('CREATE_LAND_FORM_MIN_X'), placeholder=str(p_min_x), default_value=default_px),
            TextInput(label=self.language_manager.GetText('CREATE_LAND_FORM_MAX_X'), placeholder=str(p_max_x), default_value=default_px),
            TextInput(label=self.language_manager.GetText('CREATE_LAND_FORM_MIN_Y'), placeholder=str(p_min_y), default_value=default_py),
            TextInput(label=self.language_manager.GetText('CREATE_LAND_FORM_MAX_Y'), placeholder=str(p_max_y), default_value=default_py),
            TextInput(label=self.language_manager.GetText('CREATE_LAND_FORM_MIN_Z'), placeholder=str(p_min_z), default_value=default_pz),
            TextInput(label=self.language_manager.GetText('CREATE_LAND_FORM_MAX_Z'), placeholder=str(p_max_z), default_value=default_pz),
            TextInput(label=self.language_manager.GetText('SUB_LAND_NAME_INPUT_LABEL'),
                      placeholder=self.language_manager.GetText('SUB_LAND_NAME_INPUT_PLACEHOLDER').format(player.name),
                      default_value=self.language_manager.GetText('SUB_LAND_NAME_INPUT_PLACEHOLDER').format(player.name)),
        ]

        def on_submit(p: Player, json_str: str):
            try:
                data = json.loads(json_str)
                try:
                    min_x = int(data[1]); max_x = int(data[2])
                    min_y = int(data[3]); max_y = int(data[4])
                    min_z = int(data[5]); max_z = int(data[6])
                except (ValueError, TypeError, IndexError):
                    p.send_message(self.language_manager.GetText('CREATE_LAND_FORM_INVALID_COORD'))
                    return
                sub_land_name = (data[7] or '').strip()
                if not sub_land_name:
                    sub_land_name = self.language_manager.GetText('SUB_LAND_NAME_INPUT_PLACEHOLDER').format(p.name)
                min_x, max_x = min(min_x, max_x), max(min_x, max_x)
                min_y, max_y = min(min_y, max_y), max(min_y, max_y)
                min_z, max_z = min(min_z, max_z), max(min_z, max_z)

                ok, reason = self.check_sub_land_availability(land_id, min_x, max_x, min_y, max_y, min_z, max_z)
                if not ok:
                    if reason == "SYSTEM_ERROR":
                        self.report_arc_error(
                            "LAND0C",
                            f"create_sub_land form check_sub_land_availability SYSTEM_ERROR parent_land_id={land_id!r}",
                            p,
                        )
                    p.send_message(self.language_manager.GetText(f'CHECK_SUB_LAND_FAIL_{reason}'))
                    return

                sl_id = self.create_sub_land(land_id, str(p.xuid), sub_land_name, min_x, max_x, min_y, max_y, min_z, max_z)
                if sl_id is not None:
                    p.send_message(self.language_manager.GetText('SUB_LAND_CREATE_SUCCESS').format(sl_id, sub_land_name))
                    self.display_land_particle_boundary(p, {'min_x': min_x, 'max_x': max_x, 'min_y': min_y, 'max_y': max_y, 'min_z': min_z, 'max_z': max_z})
                    self.show_sub_land_detail_panel(p, sl_id)
                else:
                    self.report_arc_error(
                        "LAND29",
                        f"create_sub_land returned None parent_land_id={land_id!r} player={p.name!r}",
                        p,
                    )
            except Exception as e:
                self.logger.error(f"Create sub land form submit error: {str(e)}")
                self.report_arc_error(
                    "LAND30",
                    "show_create_sub_land_form on_submit exception",
                    p,
                    exception=e,
                )

        form = ModalForm(
            title=self.language_manager.GetText('SUB_LAND_CREATE_FORM_TITLE'),
            controls=controls,
            on_submit=on_submit,
            on_close=None,
        )
        player.send_form(form)

    def show_sub_land_detail_panel(self, player: Player, sub_land_id: int):
        """显示子领地详情面板"""
        sl_info = self.get_sub_land_info(sub_land_id)
        if not sl_info:
            self.report_arc_error(
                "LAND31",
                f"show_sub_land_detail_panel get_sub_land_info empty sub_land_id={sub_land_id!r}",
                player,
            )
            return

        parent_land_id = sl_info['parent_land_id']
        is_owner = self._player_matches_land_owner_key(player, sl_info['owner_xuid']) or player.is_op
        shared_names = [self.get_player_name_by_xuid(uid) or uid for uid in sl_info['shared_users']]
        shared_str = ', '.join(shared_names) if shared_names else self.language_manager.GetText('LAND_DETAIL_NO_SHARED_USER_TEXT')

        content = self.language_manager.GetText('SUB_LAND_DETAIL_CONTENT').format(
            sub_land_id,
            sl_info['sub_land_name'],
            (sl_info['min_x'], sl_info['min_y'], sl_info['min_z']),
            (sl_info['max_x'], sl_info['max_y'], sl_info['max_z']),
            self.format_land_owner_key_display(sl_info['owner_xuid']),
            shared_str
        )

        def _back(p):
            self.show_sub_land_manage_panel(p, parent_land_id)

        panel = ActionForm(
            title=self.language_manager.GetText('SUB_LAND_DETAIL_PANEL_TITLE'),
            content=content,
            on_close=None,
        )

        if is_owner:
            panel.add_button(
                self.language_manager.GetText('SUB_LAND_MANAGE_AUTH_BUTTON_TEXT'),
                on_click=lambda p=player, sl=sub_land_id: self.show_sub_land_auth_manage_panel(p, sl)
            )
            panel.add_button(
                self.language_manager.GetText('SUB_LAND_RENAME_BUTTON_TEXT'),
                on_click=lambda p=player, sl=sub_land_id: self.show_rename_sub_land_panel(p, sl)
            )
            panel.add_button(
                self.language_manager.GetText('SUB_LAND_DELETE_BUTTON_TEXT'),
                on_click=lambda p=player, sl=sub_land_id: self.confirm_delete_sub_land(p, sl)
            )

        panel.add_button(
            self.language_manager.GetText('RETURN_BUTTON_TEXT'),
            on_click=_back
        )
        player.send_form(panel)

    def show_rename_sub_land_panel(self, player: Player, sub_land_id: int):
        sl_info = self.get_sub_land_info(sub_land_id)
        if not sl_info:
            self.report_arc_error(
                "LAND32",
                f"show_rename_sub_land_panel get_sub_land_info empty sub_land_id={sub_land_id!r}",
                player,
            )
            return

        def on_submit(p: Player, json_str: str):
            data = json.loads(json_str)
            if len(data) < 2:
                p.send_message(self.language_manager.GetText('CREATE_HOME_EMPTY_NAME_ERROR'))
                return
            if self._modal_choice_is_back(data, 0):
                self.show_sub_land_detail_panel(p, sub_land_id)
                return
            new_name = (data[1] or '').strip()
            if not new_name:
                p.send_message(self.language_manager.GetText('CREATE_HOME_EMPTY_NAME_ERROR'))
                return
            self.rename_sub_land(sub_land_id, new_name)
            self.show_sub_land_detail_panel(p, sub_land_id)

        form = ModalForm(
            title=self.language_manager.GetText('SUB_LAND_RENAME_PANEL_TITLE'),
            controls=[
                self._modal_nav_dropdown(),
                TextInput(
                    label=self.language_manager.GetText('RENAME_OWN_LAND_PANEL_INPUT_LABEL').format(sub_land_id),
                    placeholder=sl_info['sub_land_name'],
                    default_value=sl_info['sub_land_name'],
                ),
            ],
            on_submit=on_submit,
            on_close=None,
        )
        player.send_form(form)

    def confirm_delete_sub_land(self, player: Player, sub_land_id: int):
        sl_info = self.get_sub_land_info(sub_land_id)
        if not sl_info:
            self.report_arc_error(
                "LAND33",
                f"confirm_delete_sub_land get_sub_land_info empty sub_land_id={sub_land_id!r}",
                player,
            )
            return

        parent_land_id = sl_info['parent_land_id']

        def _back(p):
            self.show_sub_land_manage_panel(p, parent_land_id)

        panel = ActionForm(
            title=self.language_manager.GetText('SUB_LAND_CONFIRM_DELETE_TITLE').format(sub_land_id),
            content=self.language_manager.GetText('SUB_LAND_CONFIRM_DELETE_CONTENT').format(sub_land_id, sl_info['sub_land_name']),
            on_close=None,
        )
        panel.add_button(
            self.language_manager.GetText('SUB_LAND_CONFIRM_DELETE_BUTTON'),
            on_click=lambda p=player, sl=sub_land_id, back=_back: self._do_delete_sub_land(p, sl, back)
        )
        panel.add_button(
            self.language_manager.GetText('RETURN_BUTTON_TEXT'),
            on_click=lambda p=player, sl=sub_land_id: self.show_sub_land_detail_panel(p, sl)
        )
        player.send_form(panel)

    def _do_delete_sub_land(self, player: Player, sub_land_id: int, back_func):
        if self.delete_sub_land(sub_land_id):
            player.send_message(self.language_manager.GetText('SUB_LAND_DELETE_SUCCESS').format(sub_land_id))
        else:
            player.send_message(self.language_manager.GetText('SUB_LAND_DELETE_FAILED'))
        back_func(player)

    def show_sub_land_auth_manage_panel(self, player: Player, sub_land_id: int):
        """管理子领地授权"""
        sl_info = self.get_sub_land_info(sub_land_id)
        if not sl_info:
            self.report_arc_error(
                "LAND34",
                f"show_sub_land_auth_manage_panel get_sub_land_info empty sub_land_id={sub_land_id!r}",
                player,
            )
            return

        panel = ActionForm(
            title=self.language_manager.GetText('LAND_AUTH_MANAGE_TITLE'),
            content=self.language_manager.GetText('SUB_LAND_AUTH_PANEL_CONTENT').format(sub_land_id, sl_info['sub_land_name']),
            on_close=None,
        )
        panel.add_button(
            self.language_manager.GetText('LAND_AUTH_ADD_BUTTON'),
            on_click=lambda p=player, sl=sub_land_id: self.show_add_sub_land_auth_panel(p, sl)
        )
        if sl_info['shared_users']:
            panel.add_button(
                self.language_manager.GetText('LAND_AUTH_REMOVE_BUTTON'),
                on_click=lambda p=player, sl=sub_land_id: self.show_remove_sub_land_auth_panel(p, sl)
            )
        panel.add_button(
            self.language_manager.GetText('RETURN_BUTTON_TEXT'),
            on_click=lambda p=player, sl=sub_land_id: self.show_sub_land_detail_panel(p, sl)
        )
        player.send_form(panel)

    def show_add_sub_land_auth_panel(self, player: Player, sub_land_id: int):
        sl_info = self.get_sub_land_info(sub_land_id)
        if not sl_info:
            self.report_arc_error(
                "LAND35",
                f"show_add_sub_land_auth_panel get_sub_land_info empty sub_land_id={sub_land_id!r}",
                player,
            )
            return
        sl_own = str(sl_info['owner_xuid'] or "")
        sl_owner_player_xuid = LandSystem.parse_land_owner_player_xuid(sl_own)
        if sl_owner_player_xuid is None and sl_own and not sl_own.startswith(
            LandSystem.LAND_OWNER_GUILD_PREFIX
        ) and not self.land_system.is_public_land_owner(sl_own):
            sl_owner_player_xuid = sl_own
        online_players = [
            p
            for p in self.server.online_players
            if str(p.xuid) != str(player.xuid)
            and str(p.xuid) != str(sl_owner_player_xuid or "")
            and str(p.xuid) not in sl_info['shared_users']
        ]
        if not online_players:
            player.send_message(self.language_manager.GetText('LAND_AUTH_NO_SHARED_USERS'))
            self.show_sub_land_auth_manage_panel(player, sub_land_id)
            return
        panel = ActionForm(
            title=self.language_manager.GetText('LAND_AUTH_ADD_PANEL_TITLE'),
            content=self.language_manager.GetText('LAND_AUTH_SELECT_PLAYER_CONTENT'),
            on_close=None,
        )
        for op in online_players:
            panel.add_button(
                self.language_manager.GetText('LAND_AUTH_ADD_TARGET_BUTTON').format(op.name),
                on_click=lambda p=player, sl=sub_land_id, target=op: self._do_add_sub_land_auth(p, sl, str(target.xuid), target.name)
            )
        player.send_form(panel)

    def _do_add_sub_land_auth(self, player: Player, sub_land_id: int, target_xuid: str, target_name: str):
        if self.add_sub_land_shared_user(sub_land_id, target_xuid):
            player.send_message(self.language_manager.GetText('LAND_AUTH_SUCCESS_ADD').format(sub_land_id, target_name))
        else:
            player.send_message(self.language_manager.GetText('LAND_AUTH_FAILED_ADD'))
        self.show_sub_land_auth_manage_panel(player, sub_land_id)

    def show_remove_sub_land_auth_panel(self, player: Player, sub_land_id: int):
        sl_info = self.get_sub_land_info(sub_land_id)
        if not sl_info or not sl_info['shared_users']:
            player.send_message(self.language_manager.GetText('LAND_AUTH_NO_SHARED_USERS'))
            self.show_sub_land_auth_manage_panel(player, sub_land_id)
            return
        panel = ActionForm(
            title=self.language_manager.GetText('LAND_AUTH_REMOVE_PANEL_TITLE'),
            content=self.language_manager.GetText('LAND_AUTH_SELECT_REMOVE_CONTENT'),
            on_close=None,
        )
        for uid in sl_info['shared_users']:
            name = self.get_player_name_by_xuid(uid) or uid
            panel.add_button(
                self.language_manager.GetText('LAND_AUTH_REMOVE_TARGET_BUTTON').format(name),
                on_click=lambda p=player, sl=sub_land_id, u=uid, n=name: self._do_remove_sub_land_auth(p, sl, u, n)
            )
        player.send_form(panel)

    def _do_remove_sub_land_auth(self, player: Player, sub_land_id: int, target_xuid: str, target_name: str):
        if self.remove_sub_land_shared_user(sub_land_id, target_xuid):
            player.send_message(self.language_manager.GetText('LAND_AUTH_SUCCESS_REMOVE').format(target_name, sub_land_id))
        else:
            player.send_message(self.language_manager.GetText('LAND_AUTH_FAILED_REMOVE'))
        self.show_sub_land_auth_manage_panel(player, sub_land_id)

    # ─── End Sub-land UI ─────────────────────────────────────────────────────────

    # OP Panel
    def show_op_land_manage_panel(self, player: Player):
        """OP 领地管理二级菜单。"""
        panel = ActionForm(
            title=self.language_manager.GetText('OP_LAND_MANAGE_TITLE'),
            content=self.language_manager.GetText('OP_LAND_MANAGE_CONTENT'),
            on_close=None,
        )
        panel.add_button(
            self.language_manager.GetText('OP_PANEL_MANAGE_ALL_LANDS'),
            on_click=self.show_op_all_lands_panel,
        )
        panel.add_button(
            self.language_manager.GetText('OP_PANEL_MANAGE_LAND_AT_POS'),
            on_click=self.show_op_land_at_pos,
        )
        panel.add_button(
            self.language_manager.GetText('OP_PANEL_REBUILD_CHUNK_MAPPING'),
            on_click=self.show_op_rebuild_chunk_mapping_confirm,
        )
        panel.add_button(self.language_manager.GetText('RETURN_BUTTON_TEXT'), on_click=self.show_op_main_panel)
        player.send_form(panel)

    def show_op_tools_panel(self, player: Player):
        """OP 工具二级菜单。"""
        panel = ActionForm(
            title=self.language_manager.GetText('OP_TOOLS_TITLE'),
            content=self.language_manager.GetText('OP_TOOLS_CONTENT'),
            on_close=None,
        )
        panel.add_button(
            self.language_manager.GetText('OP_PANEL_SWITCH_GAME_MODE'),
            on_click=self.switch_player_game_mode,
        )
        panel.add_button(self.language_manager.GetText('CLEAR_DROP_ITEM'), on_click=self.clear_drop_item)
        panel.add_button(self.language_manager.GetText('RECORD_COOR_1'), on_click=self.record_coordinate_1)
        panel.add_button(self.language_manager.GetText('RECORD_COOR_2'), on_click=self.record_coordinate_2)
        debug_btn_text = (
            self.language_manager.GetText('OP_DEBUG_MODE_BUTTON_ON')
            if player.name in self.op_debug_mode
            else self.language_manager.GetText('OP_DEBUG_MODE_BUTTON_OFF')
        )
        panel.add_button(debug_btn_text, on_click=self.toggle_op_debug_mode)
        panel.add_button(self.language_manager.GetText('RUN_COMMAND'), on_click=self.run_command_as_self)
        panel.add_button(self.language_manager.GetText('RETURN_BUTTON_TEXT'), on_click=self.show_op_main_panel)
        player.send_form(panel)

    def show_op_main_panel(self, player: Player):
        op_main_panel = ActionForm(
            title=self.language_manager.GetText('OP_PANEL_TITLE')
        )
        op_main_panel.add_button(self.language_manager.GetText('OP_PANEL_RELOAD_CONFIG_BUTTON'),
                                 on_click=self.op_reload_config)
        op_main_panel.add_button(self.language_manager.GetText('OP_CORE_SETTINGS_BUTTON'),
                                 on_click=self.show_op_core_settings_panel)
        op_main_panel.add_button(self.language_manager.GetText('OP_TOOLS_ENTRY'),
                                 on_click=self.show_op_tools_panel)
        op_main_panel.add_button(self.language_manager.GetText('OP_ECONOMY_MANAGE_ENTRY'),
                                 on_click=self.show_economy_manage_panel)
        op_main_panel.add_button(self.language_manager.GetText('OP_LAND_MANAGE_ENTRY'),
                                 on_click=self.show_op_land_manage_panel)
        op_main_panel.add_button(self.language_manager.GetText('OP_TELEPORT_MANAGE_ENTRY'),
                                 on_click=self.show_op_teleport_manage_panel)
        if self.server.plugin_manager.get_plugin('arc_achievement'):
            op_main_panel.add_button(
                self.language_manager.GetText('OP_ACHIEVEMENT_MANAGE_BUTTON'),
                on_click=self.show_arc_achievement_op_menu,
            )
        op_main_panel.add_button(self.language_manager.GetText('CHECKIN_CONFIG_OP_BUTTON'),
                                 on_click=self.show_checkin_config_panel)
        op_main_panel.add_button(self.language_manager.GetText('INVITE_REWARD_CONFIG_BUTTON'),
                                 on_click=self.show_invite_reward_config_panel)
        op_main_panel.add_button(self.language_manager.GetText('OP_TITLE_MANAGE_BUTTON'),
                                 on_click=self.show_op_title_manage_panel)
        op_main_panel.add_button(self.language_manager.GetText('OP_GUILD_MANAGE_BUTTON'),
                                 on_click=self.show_op_guild_manage_panel)
        # 返回
        op_main_panel.add_button(self.language_manager.GetText('RETURN_BUTTON_TEXT'),
                                  on_click=self.show_main_menu)
        player.send_form(op_main_panel)

    def show_op_core_settings_panel(self, player: Player):
        """OP：在面板中浏览并修改 core_setting.yml。"""
        if not getattr(self, "op_settings_ui", None):
            from endstone_arc_core.op_settings_ui import OpSettingsUi
            self.op_settings_ui = OpSettingsUi(self)
        self.op_settings_ui.show_groups(player)

    def show_op_title_manage_panel(self, player: Player):
        """OP 头衔管理子菜单：头衔属性管理、创建新头衔、给全体添加、给单独玩家添加。"""
        panel = ActionForm(
            title=self.language_manager.GetText('OP_TITLE_MANAGE_TITLE'),
            content=self.language_manager.GetText('OP_TITLE_MANAGE_CONTENT'),
            on_close=None,
        )
        panel.add_button(self.language_manager.GetText('OP_TITLE_ATTR_MANAGE_BUTTON'),
                         on_click=self.show_op_title_attr_list_panel)
        panel.add_button(self.language_manager.GetText('OP_TITLE_CREATE_BUTTON'),
                         on_click=self.show_op_title_create_panel)
        panel.add_button(self.language_manager.GetText('OP_TITLE_GRANT_TO_ALL_BUTTON'),
                         on_click=self.show_op_grant_title_to_all_panel)
        panel.add_button(self.language_manager.GetText('OP_TITLE_GRANT_TO_SINGLE_BUTTON'),
                         on_click=self.show_op_grant_title_to_single_player_input)
        panel.add_button(self.language_manager.GetText('RETURN_BUTTON_TEXT'),
                         on_click=self.show_op_main_panel)
        player.send_form(panel)

    def show_op_title_attr_list_panel(self, player: Player):
        """OP 头衔属性管理：列出所有 (名称, 稀有度) 条目。"""
        try:
            entries = list(self.title_system.get_all_title_entries())
        except Exception:
            entries = []
            for t in self.title_system.get_all_title_names():
                defn = self.title_system.get_title_definition(t) or {}
                entries.append({
                    "title": t,
                    "rarity": defn.get("rarity") or DEFAULT_RARITY,
                })
        if not entries:
            player.send_message(self.language_manager.GetText('OP_TITLE_NO_TITLES'))
            self.show_op_title_manage_panel(player)
            return
        panel = ActionForm(
            title=self.language_manager.GetText('OP_TITLE_ATTR_MANAGE_TITLE'),
            content=self.language_manager.GetText('OP_TITLE_ATTR_MANAGE_CONTENT'),
            on_close=None,
        )
        for entry in entries:
            t = str(entry.get("title") or "").strip()
            rarity = str(entry.get("rarity") or DEFAULT_RARITY).strip() or DEFAULT_RARITY
            if not t:
                continue
            panel.add_button(
                f"{t} ({rarity})",
                on_click=lambda p, title=t, r=rarity: self.show_op_title_edit_panel(p, title, r),
            )
        panel.add_button(self.language_manager.GetText('RETURN_BUTTON_TEXT'), on_click=self.show_op_title_manage_panel)
        player.send_form(panel)

    def show_op_title_edit_panel(self, player: Player, title_name: str, rarity: str = DEFAULT_RARITY):
        """OP 头衔管理：编辑属性 / 重命名。"""
        rarity_s = str(rarity or DEFAULT_RARITY).strip() or DEFAULT_RARITY
        menu = ActionForm(
            title=self.language_manager.GetText('OP_TITLE_ATTR_EDIT_TITLE').format(
                f"{title_name}（{rarity_s}）"
            ),
            on_close=None,
        )
        menu.add_button(
            self.language_manager.GetText('OP_TITLE_ATTR_EDIT_ATTR_BUTTON'),
            on_click=lambda p=player, t=title_name, r=rarity_s: self._show_op_title_attr_edit_modal(p, t, r)
        )
        menu.add_button(
            self.language_manager.GetText('OP_TITLE_RENAME_BUTTON'),
            on_click=lambda p=player, t=title_name, r=rarity_s: self.show_op_title_rename_panel(p, t, r)
        )
        menu.add_button(
            self.language_manager.GetText('RETURN_BUTTON_TEXT'),
            on_click=self.show_op_title_attr_list_panel
        )
        player.send_form(menu)

    def _show_op_title_attr_edit_modal(
        self, player: Player, title_name: str, rarity: str = DEFAULT_RARITY
    ):
        """OP 编辑头衔属性：稀有度、介绍（奖励字段保留兼容，解锁不发奖）。"""
        rarity_s = str(rarity or DEFAULT_RARITY).strip() or DEFAULT_RARITY
        defn = self.title_system.get_title_definition(title_name, rarity_s)
        if not defn:
            defn = {"rarity": rarity_s, "description": "", "reward_money": 0.0, "reward_items": []}
        reward_items_str = "; ".join(
            f"{x.get('item_name', x.get('id', ''))} {x.get('count', 0)}"
            for x in (defn.get("reward_items") or [])
        )
        rarity_input = TextInput(
            label=self.language_manager.GetText('OP_TITLE_ATTR_RARITY_LABEL'),
            placeholder="普通/稀有/史诗/传奇/神话",
            default_value=defn.get("rarity", "普通"),
        )
        desc_input = TextInput(
            label=self.language_manager.GetText('OP_TITLE_ATTR_DESC_LABEL'),
            placeholder=self.language_manager.GetText('OP_TITLE_ATTR_DESC_PLACEHOLDER'),
            default_value=defn.get("description", ""),
        )
        money_input = TextInput(
            label=self.language_manager.GetText('OP_TITLE_ATTR_REWARD_MONEY_LABEL'),
            placeholder="0",
            default_value=str(defn.get("reward_money", 0)),
        )
        items_input = TextInput(
            label=self.language_manager.GetText('OP_TITLE_ATTR_REWARD_ITEMS_LABEL'),
            placeholder="minecraft:diamond 2; minecraft:emerald 1",
            default_value=reward_items_str,
        )
        form = ModalForm(
            title=self.language_manager.GetText('OP_TITLE_ATTR_EDIT_TITLE').format(
                f"{title_name}（{rarity_s}）"
            ),
            controls=[rarity_input, desc_input, money_input, items_input],
            on_close=None,
            on_submit=lambda p, json_str, t=title_name, r=rarity_s: self._do_op_title_save_attr(
                p, json_str, t, r
            ),
        )
        player.send_form(form)

    def show_op_title_rename_panel(
        self, player: Player, title_name: str, rarity: str = DEFAULT_RARITY
    ):
        """OP 头衔重命名：将旧头衔名迁移为新头衔名（限定该稀有度变体）。"""
        rarity_s = str(rarity or DEFAULT_RARITY).strip() or DEFAULT_RARITY
        rename_input = TextInput(
            label=self.language_manager.GetText('OP_TITLE_RENAME_INPUT_LABEL'),
            placeholder=self.language_manager.GetText('OP_TITLE_RENAME_INPUT_PLACEHOLDER'),
            default_value="",
        )
        form = ModalForm(
            title=self.language_manager.GetText('OP_TITLE_RENAME_PANEL_TITLE').format(
                f"{title_name}（{rarity_s}）"
            ),
            controls=[rename_input],
            on_close=None,
            on_submit=lambda p, json_str, t=title_name, r=rarity_s: self._do_op_title_rename(
                p, json_str, t, r
            ),
        )
        player.send_form(form)

    def _do_op_title_rename(
        self, player: Player, json_str: str, title_name: str, rarity: str = DEFAULT_RARITY
    ):
        """执行头衔重命名：校验冲突 + 更新配置 + 同步数据库（限定稀有度变体）。"""
        try:
            data = json.loads(json_str)
            if not data or not str(data[0]).strip():
                player.send_message(self.language_manager.GetText('OP_TITLE_RENAME_FAIL_EMPTY'))
                return self.show_op_title_attr_list_panel(player)

            old_title = str(title_name).strip()
            new_title = str(data[0]).strip()
            rarity_s = str(rarity or DEFAULT_RARITY).strip() or DEFAULT_RARITY

            if old_title == new_title:
                player.send_message(self.language_manager.GetText('OP_TITLE_RENAME_FAIL_SAME'))
                return self.show_op_title_attr_list_panel(player)

            if self.title_system.get_title_definition(new_title, rarity_s):
                player.send_message(
                    self.language_manager.GetText('OP_TITLE_RENAME_FAIL_CONFLICT').format(new_title)
                )
                return self.show_op_title_attr_list_panel(player)

            ok = self.title_system.rename_title(old_title, new_title, rarity=rarity_s)
            if not ok:
                player.send_message(self.language_manager.GetText('OP_TITLE_RENAME_FAIL'))
                return self.show_op_title_attr_list_panel(player)

            current_op_title = self.setting_manager.GetSetting('OP_TITLE')
            if current_op_title and str(current_op_title).strip() == old_title:
                self.setting_manager.SetSetting('OP_TITLE', new_title)

            default_raw = self.setting_manager.GetSetting('DEFAULT_TITLE') or ""
            default_list = [t.strip() for t in str(default_raw).split(",") if t.strip()]
            if old_title in default_list:
                default_list = [new_title if t == old_title else t for t in default_list]
                self.setting_manager.SetSetting('DEFAULT_TITLE', ",".join(default_list))

            player.send_message(
                self.language_manager.GetText('OP_TITLE_RENAME_SUCCESS').format(old_title, new_title)
            )
        except Exception as e:
            if self.logger:
                self.logger.error(f"[ARC Core]Rename title error: {e}")
            player.send_message(self.language_manager.GetText('OP_TITLE_RENAME_FAIL'))
        self.show_op_title_attr_list_panel(player)

    def _parse_reward_items(self, text: str) -> list:
        """解析 '物品ID 数量; 物品ID 数量' 为 [{"item_name": id, "count": n}, ...]"""
        result = []
        for part in (text or "").split(";"):
            part = part.strip()
            if not part:
                continue
            tokens = part.split()
            if len(tokens) >= 2:
                try:
                    result.append({"item_name": tokens[0], "count": int(tokens[1])})
                except ValueError:
                    pass
            elif len(tokens) == 1:
                result.append({"item_name": tokens[0], "count": 1})
        return result

    def _do_op_title_save_attr(
        self, player: Player, json_str: str, title_name: str, old_rarity: str = DEFAULT_RARITY
    ):
        try:
            data = json.loads(json_str)
            if len(data) < 4:
                self.show_op_title_attr_list_panel(player)
                return
            new_rarity = str(data[0]).strip() if data[0] else DEFAULT_RARITY
            if new_rarity not in ("普通", "稀有", "史诗", "传奇", "神话"):
                new_rarity = DEFAULT_RARITY
            description = str(data[1]).strip() if data[1] else ""
            try:
                reward_money = float(data[2]) if data[2] is not None and str(data[2]).strip() else 0.0
            except (ValueError, TypeError):
                reward_money = 0.0
            reward_items = self._parse_reward_items(str(data[3]) if data[3] else "")
            old_r = str(old_rarity or DEFAULT_RARITY).strip() or DEFAULT_RARITY
            if new_rarity != old_r:
                # 稀有度变更：写入新 (title, new_rarity)，迁移解锁/佩戴，删除旧定义
                if self.title_system.get_title_definition(title_name, new_rarity):
                    player.send_message(
                        self.language_manager.GetText('OP_TITLE_RENAME_FAIL_CONFLICT').format(
                            f"{title_name}（{new_rarity}）"
                        )
                    )
                    self.show_op_title_attr_list_panel(player)
                    return
                self.title_system.set_title_definition(
                    title_name, new_rarity, description, reward_money, reward_items
                )
                try:
                    self.database_manager.execute(
                        "UPDATE player_title_unlock_time SET rarity = ? "
                        "WHERE title = ? AND rarity = ?",
                        (new_rarity, title_name, old_r),
                    )
                    self.database_manager.execute(
                        "UPDATE player_title_equipped SET rarity = ? "
                        "WHERE title = ? AND rarity = ?",
                        (new_rarity, title_name, old_r),
                    )
                    self.database_manager.execute(
                        "DELETE FROM title_definitions WHERE title = ? AND rarity = ?",
                        (title_name, old_r),
                    )
                except Exception:
                    pass
            else:
                self.title_system.set_title_definition(
                    title_name, new_rarity, description, reward_money, reward_items
                )
            player.send_message(self.language_manager.GetText('OP_TITLE_ATTR_SAVED'))
        except Exception as e:
            if self.logger:
                self.logger.error(f"[ARC Core]Save title attr error: {e}")
        self.show_op_title_attr_list_panel(player)

    def show_op_title_create_panel(self, player: Player):
        """OP 创建新头衔：名称、稀有度、介绍、解锁奖励。"""
        name_input = TextInput(
            label=self.language_manager.GetText('OP_TITLE_CREATE_NAME_LABEL'),
            placeholder=self.language_manager.GetText('OP_TITLE_CREATE_NAME_PLACEHOLDER')
        )
        rarity_input = TextInput(
            label=self.language_manager.GetText('OP_TITLE_ATTR_RARITY_LABEL'),
            placeholder="普通/稀有/史诗/传奇/神话",
            default_value="普通"
        )
        desc_input = TextInput(
            label=self.language_manager.GetText('OP_TITLE_ATTR_DESC_LABEL'),
            placeholder=self.language_manager.GetText('OP_TITLE_ATTR_DESC_PLACEHOLDER')
        )
        money_input = TextInput(label=self.language_manager.GetText('OP_TITLE_ATTR_REWARD_MONEY_LABEL'), placeholder="0")
        items_input = TextInput(
            label=self.language_manager.GetText('OP_TITLE_ATTR_REWARD_ITEMS_LABEL'),
            placeholder="minecraft:diamond 2; minecraft:emerald 1"
        )
        form = ModalForm(
            title=self.language_manager.GetText('OP_TITLE_CREATE_TITLE'),
            controls=[name_input, rarity_input, desc_input, money_input, items_input],
            on_close=None,
            on_submit=lambda p, json_str: self._do_op_title_create(p, json_str)
        )
        player.send_form(form)

    def _do_op_title_create(self, player: Player, json_str: str):
        try:
            data = json.loads(json_str)
            if not data or not str(data[0]).strip():
                player.send_message(self.language_manager.GetText('OP_TITLE_CREATE_FAIL_EMPTY'))
                self.show_op_title_manage_panel(player)
                return
            title_name = str(data[0]).strip()
            rarity = str(data[1]).strip() if len(data) > 1 and data[1] else "普通"
            if rarity not in ("普通", "稀有", "史诗", "传奇", "神话"):
                rarity = "普通"
            description = str(data[2]).strip() if len(data) > 2 and data[2] else ""
            try:
                reward_money = float(data[3]) if len(data) > 3 and data[3] is not None and str(data[3]).strip() else 0.0
            except (ValueError, TypeError):
                reward_money = 0.0
            reward_items = self._parse_reward_items(str(data[4]) if len(data) > 4 and data[4] else "")
            self.title_system.set_title_definition(title_name, rarity, description, reward_money, reward_items)
            player.send_message(self.language_manager.GetText('OP_TITLE_CREATE_SUCCESS').format(title_name))
        except Exception as e:
            if self.logger:
                self.logger.error(f"[ARC Core]Create title error: {e}")
            player.send_message(self.language_manager.GetText('OP_TITLE_CREATE_FAIL'))
        self.show_op_title_manage_panel(player)

    def show_op_grant_title_to_all_panel(self, player: Player):
        """OP 给所有玩家添加头衔：选择已有头衔（按钮列表）。"""
        titles = self.title_system.get_all_title_names()
        if not titles:
            player.send_message(self.language_manager.GetText('OP_TITLE_NO_TITLES'))
            self.show_op_title_manage_panel(player)
            return
        panel = ActionForm(
            title=self.language_manager.GetText('OP_TITLE_GRANT_TO_ALL_TITLE'),
            content=self.language_manager.GetText('OP_TITLE_GRANT_TO_ALL_SELECT_HINT'),
            on_close=None,
        )
        for t in titles:
            panel.add_button(t, on_click=lambda p, title=t: self._do_op_grant_title_to_all(p, title))
        panel.add_button(self.language_manager.GetText('RETURN_BUTTON_TEXT'), on_click=self.show_op_title_manage_panel)
        player.send_form(panel)

    def _do_op_grant_title_to_all(self, player: Player, title: str):
        """为当前数据库内所有玩家添加指定头衔（新人不会自动获得，需再次添加）。"""
        try:
            rows = self.database_manager.query_all("SELECT xuid FROM player_basic_info", ())
            count = 0
            for row in rows:
                xuid = row.get('xuid')
                if not xuid:
                    continue
                unlock_ok, was_new_unlock = self.title_system.unlock_title_by_xuid(xuid, title)
                if unlock_ok and was_new_unlock:
                    count += 1
            player.send_message(self.language_manager.GetText('OP_TITLE_GRANT_TO_ALL_SUCCESS').format(count, title))
        except Exception as e:
            if self.logger:
                self.logger.error(f"[ARC Core]Grant title to all error: {e}")
            player.send_message(self.language_manager.GetText('OP_TITLE_GRANT_TO_ALL_FAIL'))
        self.show_op_title_manage_panel(player)

    def show_op_grant_title_to_single_player_input(self, player: Player):
        """OP 给单独玩家添加头衔：先输入玩家名。"""
        player_input = TextInput(
            label=self.language_manager.GetText('OP_TITLE_GRANT_TO_SINGLE_PLAYER_LABEL'),
            placeholder=self.language_manager.GetText('OP_TITLE_GRANT_TO_SINGLE_PLAYER_PLACEHOLDER')
        )
        form = ModalForm(
            title=self.language_manager.GetText('OP_TITLE_GRANT_TO_SINGLE_TITLE'),
            controls=[player_input],
            on_close=None,
            on_submit=lambda p, json_str: self._op_grant_single_on_player_entered(p, json_str)
        )
        player.send_form(form)

    def _op_grant_single_on_player_entered(self, player: Player, json_str: str):
        try:
            data = json.loads(json_str)
            if not data or not str(data[0]).strip():
                player.send_message(self.language_manager.GetText('OP_TITLE_GRANT_TO_SINGLE_FAIL_EMPTY'))
                self.show_op_title_manage_panel(player)
                return
            target_name = str(data[0]).strip()
            xuid = self.get_player_xuid_by_name(target_name)
            if not xuid:
                player.send_message(self.language_manager.GetText('OP_TITLE_GRANT_TO_SINGLE_FAIL_NOT_FOUND').format(target_name))
                self.show_op_title_manage_panel(player)
                return
            self.show_op_grant_title_to_single_select_title(player, target_name, xuid)
        except Exception:
            self.show_op_title_manage_panel(player)

    def show_op_grant_title_to_single_select_title(self, player: Player, target_name: str, target_xuid: str):
        """选择要授予的头衔（按钮列表）。"""
        titles = self.title_system.get_all_title_names()
        if not titles:
            player.send_message(self.language_manager.GetText('OP_TITLE_NO_TITLES'))
            self.show_op_title_manage_panel(player)
            return
        panel = ActionForm(
            title=self.language_manager.GetText('OP_TITLE_GRANT_TO_SINGLE_TITLE'),
            content=self.language_manager.GetText('OP_TITLE_GRANT_TO_SINGLE_SELECT_HINT').format(target_name),
            on_close=None,
        )
        for t in titles:
            panel.add_button(t, on_click=lambda p, title=t, name=target_name, xuid=target_xuid: self._do_op_grant_title_to_single(p, name, xuid, title))
        panel.add_button(self.language_manager.GetText('RETURN_BUTTON_TEXT'), on_click=self.show_op_title_manage_panel)
        player.send_form(panel)

    def _do_op_grant_title_to_single(self, player: Player, target_name: str, target_xuid: str, title: str):
        unlock_ok, was_new_unlock = self.title_system.unlock_title_by_xuid(target_xuid, title)
        if unlock_ok:
            target_online = None
            for p in (self.server.online_players or []):
                if str(p.xuid) == target_xuid:
                    target_online = p
                    break
            player.send_message(self.language_manager.GetText('OP_TITLE_GRANT_TO_SINGLE_SUCCESS').format(target_name, title))
        else:
            player.send_message(self.language_manager.GetText('OP_TITLE_GRANT_TO_SINGLE_FAIL'))
        self.show_op_title_manage_panel(player)

    def show_op_land_at_pos(self, player: Player):
        """OP 直接获取脚下领地（含公共领地）并进入管理面板"""
        pos = self.get_player_position_vector(player)
        if not pos:
            self.report_arc_error(
                "OP1",
                f"show_op_land_at_pos get_player_position_vector None player={player.name!r}",
                player,
            )
            return
        x, y, z = pos
        dimension = get_dimension_id(player.location.dimension)

        land_id = self.get_land_at_pos(dimension, x, z, y)
        if land_id is None:
            result_panel = ActionForm(
                title=self.language_manager.GetText('OP_LAND_AT_POS_TITLE'),
                content=self.language_manager.GetText('OP_LAND_AT_POS_NOT_FOUND').format(x, y, z),
                on_close=None,
            )
            result_panel.add_button(self.language_manager.GetText('RETURN_BUTTON_TEXT'),
                                    on_click=self.show_op_land_manage_panel)
            player.send_form(result_panel)
            return

        self.show_op_land_detail_panel(player, land_id, from_page=0)

    def show_op_rebuild_chunk_mapping_confirm(self, player: Player):
        """OP 确认重建领地区块映射"""
        confirm = ActionForm(
            title=self.language_manager.GetText('OP_REBUILD_CHUNK_MAPPING_TITLE'),
            content=self.language_manager.GetText('OP_REBUILD_CHUNK_MAPPING_CONFIRM_CONTENT'),
            on_close=None,
        )
        confirm.add_button(
            self.language_manager.GetText('OP_REBUILD_CHUNK_MAPPING_CONFIRM_BUTTON'),
            on_click=self._do_op_rebuild_chunk_mapping
        )
        confirm.add_button(
            self.language_manager.GetText('RETURN_BUTTON_TEXT'),
            on_click=self.show_op_land_manage_panel
        )
        player.send_form(confirm)

    def _do_op_rebuild_chunk_mapping(self, player: Player):
        """执行重建区块映射并反馈结果"""
        success, message = self.rebuild_chunk_land_mapping()
        result_panel = ActionForm(
            title=self.language_manager.GetText('OP_REBUILD_CHUNK_MAPPING_TITLE'),
            content=message,
            on_close=None,
        )
        result_panel.add_button(self.language_manager.GetText('RETURN_BUTTON_TEXT'), on_click=self.show_op_land_manage_panel)
        player.send_form(result_panel)
        if success:
            player.send_message(self.language_manager.GetText('OP_REBUILD_CHUNK_MAPPING_DONE'))

    def show_op_force_delete_land_confirm(self, player: Player, land_id: int, from_page: int):
        """OP 强制删除领地确认面板（私人领地全额退款给主人，公共领地不退款）"""
        land_info = self.get_land_info(land_id)
        if not land_info:
            self.report_arc_error(
                "OP2",
                f"show_op_force_delete_land_confirm get_land_info empty land_id={land_id!r}",
                player,
            )
            self.show_op_land_detail_panel(player, land_id, from_page)
            return

        is_public = self.is_public_land(land_id)
        owner_name = "" if is_public else self.get_land_display_owner_name(land_id)
        refund = 0.0
        if not is_public:
            owner_paid = land_info.get('owner_paid_money')
            if owner_paid is not None:
                refund = round(float(owner_paid), 2)
            else:
                land_volume = ((land_info['max_x'] - land_info['min_x'] + 1) *
                               (land_info.get('max_y', 255) - land_info.get('min_y', 0) + 1) *
                               (land_info['max_z'] - land_info['min_z'] + 1))
                refund = round(float(land_volume) * self.land_price, 2)

        if is_public:
            content = self.language_manager.GetText('OP_FORCE_DELETE_LAND_CONFIRM_CONTENT_PUBLIC').format(
                land_id, land_info['land_name']
            )
        else:
            content = self.language_manager.GetText('OP_FORCE_DELETE_LAND_CONFIRM_CONTENT').format(
                land_id, land_info['land_name'], owner_name,
                self._format_money_display(refund)
            )

        confirm_panel = ActionForm(
            title=self.language_manager.GetText('OP_FORCE_DELETE_LAND_CONFIRM_TITLE').format(land_id),
            content=content,
            on_close=None,
        )
        confirm_panel.add_button(
            self.language_manager.GetText('OP_FORCE_DELETE_LAND_CONFIRM_BUTTON'),
            on_click=lambda p=player, l_id=land_id, r=refund, pg=from_page: self._do_op_force_delete_land(p, l_id, r, pg)
        )
        confirm_panel.add_button(
            self.language_manager.GetText('RETURN_BUTTON_TEXT'),
            on_click=lambda p=player, l_id=land_id, pg=from_page: self.show_op_land_detail_panel(p, l_id, pg)
        )
        player.send_form(confirm_panel)

    def _do_op_force_delete_land(self, player: Player, land_id: int, refund: float, from_page: int):
        """OP 执行强制删除领地；私人领地全额退款给主人，公共领地不退款"""
        land_info = self.get_land_info(land_id)
        owner_key = str(land_info.get("owner_xuid") or "") if land_info else ""
        owner_name = (
            self.format_land_owner_key_display(owner_key) if land_info else ""
        )
        refund_xuid = (
            self._op_force_delete_land_refund_xuid(owner_key) if land_info else None
        )
        if self.delete_land(land_id, actor=player):
            if refund > 0 and refund_xuid:
                self.increase_player_money_by_xuid(refund_xuid, refund, notify=True)
            if refund > 0:
                player.send_message(self.language_manager.GetText('OP_FORCE_DELETE_LAND_SUCCESS').format(
                    land_id, owner_name, self._format_money_display(refund)
                ))
            else:
                player.send_message(self.language_manager.GetText('OP_FORCE_DELETE_LAND_SUCCESS_PUBLIC').format(land_id))
            self.show_op_all_lands_panel(player, from_page)
        else:
            player.send_message(self.language_manager.GetText('OP_FORCE_DELETE_LAND_FAILED').format(land_id))
            self.show_op_land_detail_panel(player, land_id, from_page)

    def op_reload_config(self, player: Player):
        """OP 重载配置文件：设置、广播、迎新指令/文案、语言文件"""
        try:
            self.setting_manager.Reload()
            self._reapply_cached_settings()
            self._load_broadcast_messages()
            self._load_newbie_files()
            self.language_manager.ReloadCurrentLanguage()
            self.entity_display_name_manager.reload()
            self.kill_reward_config.reload()
            if self.sync_server and self.sync_server.is_running():
                self.sync_server.broadcast_settings()
            if self.sync_client and self.sync_client.is_running():
                self.sync_client.request_settings()
            player.send_message(self.language_manager.GetText('OP_RELOAD_CONFIG_SUCCESS'))
            self.show_op_main_panel(player)
        except Exception as e:
            self.logger.error(f"{ColorFormat.RED}[ARC Core]Reload config error: {str(e)}")
            player.send_message(self.language_manager.GetText('OP_RELOAD_CONFIG_FAILED'))
            self.show_op_main_panel(player)

    def _reapply_cached_settings(self):
        """重载配置后重新应用从 core_setting 读取的缓存项"""
        try:
            self.broadcast_interval = self.setting_manager.GetSetting('BROADCAST_INTERVAL')
            try:
                self.broadcast_interval = int(self.broadcast_interval)
            except (ValueError, TypeError):
                self.broadcast_interval = 300
            self.spawn_protect_range = self.setting_manager.GetSetting('SPAWN_PROTECT_RANGE')
            if self.spawn_protect_range is None:
                self.spawn_protect_range = 8
            else:
                try:
                    self.spawn_protect_range = int(self.spawn_protect_range)
                except ValueError:
                    self.spawn_protect_range = 8
            self.if_protect_spawn = self.setting_manager.GetSetting('IF_PROTECT_SPAWN')
            if self.if_protect_spawn is None:
                self.if_protect_spawn = False
            else:
                try:
                    self.if_protect_spawn = str(self.if_protect_spawn).lower() in ['true', '1', 'yes']
                except (ValueError, AttributeError):
                    self.if_protect_spawn = False
            land_price_raw = self.setting_manager.GetSetting('LAND_PRICE')
            try:
                self.land_price = int(land_price_raw)
            except (ValueError, TypeError):
                self.land_price = 1000
            try:
                self.land_sell_refund_coefficient = float(self.setting_manager.GetSetting('LAND_SELL_REFUND_COEFFICIENT'))
            except (ValueError, TypeError):
                self.land_sell_refund_coefficient = 0.9
            self.land_sale_vat_rate = self.setting_manager.GetSetting('LAND_SALE_VAT_RATE')
            try:
                self.land_sale_vat_rate = float(self.land_sale_vat_rate)
                if self.land_sale_vat_rate < 0:
                    self.land_sale_vat_rate = 0.0
                elif self.land_sale_vat_rate > 1.0:
                    self.land_sale_vat_rate = 1.0
            except (ValueError, TypeError):
                self.land_sale_vat_rate = 0.1
            try:
                self.land_min_size = int(self.setting_manager.GetSetting('LAND_MIN_SIZE'))
            except (ValueError, TypeError):
                self.land_min_size = 5
            try:
                self.land_min_distance = int(self.setting_manager.GetSetting('MIN_LAND_DISTANCE'))
            except (ValueError, TypeError):
                self.land_min_distance = 0
            self.teleport_system.reload_config()
            self.land_system.reload_config()
            self._sync_land_position_thread()
            self._refresh_disabled_blocks()
            self._refresh_land_only_place_blocks()
            self.hide_op_in_money_ranking = self.setting_manager.GetSetting('HIDE_OP_IN_MONEY_RANKING')
            if self.hide_op_in_money_ranking is None:
                self.hide_op_in_money_ranking = True
            else:
                try:
                    self.hide_op_in_money_ranking = str(self.hide_op_in_money_ranking).lower() in ['true', '1', 'yes']
                except (ValueError, AttributeError):
                    self.hide_op_in_money_ranking = True
            self.small_horn_price_per_hour = self.setting_manager.GetSetting('SMALL_HORN_PRICE_PER_HOUR')
            try:
                self.small_horn_price_per_hour = int(self.small_horn_price_per_hour)
                if self.small_horn_price_per_hour < 0:
                    self.small_horn_price_per_hour = 60
            except (ValueError, TypeError):
                self.small_horn_price_per_hour = 60
            self._init_cleaner_system()
            self._init_mspt_emergency_shutdown_settings()
            self.kill_reward_guild_contrib_ratio = self._load_kill_reward_guild_contrib_ratio()
            try:
                if getattr(self, "sidebar_system", None) is not None:
                    self.sidebar_system.reload_config()
            except Exception:
                pass
        except Exception as e:
            self.logger.error(f"[ARC Core]Reapply cached settings error: {str(e)}")

    def show_invite_reward_config_panel(self, player: Player):
        """OP 配置邀请奖励"""
        reward_config = self.get_invite_reward_config()

        item_name_input = TextInput(
            label=self.language_manager.GetText('INVITE_REWARD_ITEM_NAME_LABEL'),
            placeholder=self.language_manager.GetText('INVITE_REWARD_ITEM_NAME_PLACEHOLDER'),
            default_value=str(reward_config.get('item_name', ''))
        )
        item_count_input = TextInput(
            label=self.language_manager.GetText('INVITE_REWARD_ITEM_COUNT_LABEL'),
            placeholder=self.language_manager.GetText('INVITE_REWARD_ITEM_COUNT_PLACEHOLDER'),
            default_value=str(reward_config.get('item_count', 0))
        )
        money_input = TextInput(
            label=self.language_manager.GetText('INVITE_REWARD_MONEY_LABEL'),
            placeholder=self.language_manager.GetText('INVITE_REWARD_MONEY_PLACEHOLDER'),
            default_value=str(reward_config.get('money', 0))
        )
        free_blocks_input = TextInput(
            label=self.language_manager.GetText('INVITE_REWARD_FREE_BLOCKS_LABEL'),
            placeholder=self.language_manager.GetText('INVITE_REWARD_FREE_BLOCKS_PLACEHOLDER'),
            default_value=str(reward_config.get('free_blocks', 0))
        )

        def try_save_reward_config(p: Player, json_str: str):
            try:
                data = json.loads(json_str)
            except Exception as parse_exc:
                self.report_arc_error(
                    "OP_INV1",
                    "show_invite_reward_config_panel json.loads failed",
                    p,
                    exception=parse_exc,
                )
                result_panel = ActionForm(
                    title=self.language_manager.GetText('INVITE_REWARD_CONFIG_TITLE'),
                    content=self.language_manager.GetText('FILL_INVITER_FAIL_SYSTEM_ERROR'),
                    on_close=None,
                )
                p.send_form(result_panel)
                return

            item_name_value = str(data[0]).strip() if len(data) > 0 else ''
            item_count_raw = str(data[1]).strip() if len(data) > 1 else '0'
            money_raw = str(data[2]).strip() if len(data) > 2 else '0'
            free_blocks_raw = str(data[3]).strip() if len(data) > 3 else '0'

            def parse_int_non_negative(raw_value: str) -> int:
                try:
                    value = int(raw_value)
                    if value < 0:
                        value = 0
                    return value
                except (ValueError, TypeError):
                    return 0

            item_count_value = parse_int_non_negative(item_count_raw)
            money_value = parse_int_non_negative(money_raw)
            free_blocks_value = parse_int_non_negative(free_blocks_raw)

            self.setting_manager.SetSetting('INVITE_REWARD_ITEM_NAME', item_name_value)
            self.setting_manager.SetSetting('INVITE_REWARD_ITEM_COUNT', item_count_value)
            self.setting_manager.SetSetting('INVITE_REWARD_MONEY', money_value)
            self.setting_manager.SetSetting('INVITE_REWARD_FREE_LAND_BLOCKS', free_blocks_value)

            result_panel = ActionForm(
                title=self.language_manager.GetText('INVITE_REWARD_CONFIG_TITLE'),
                content=self.language_manager.GetText('INVITE_REWARD_CONFIG_SAVED'),
                on_close=None,
            )
            p.send_form(result_panel)

        config_panel = ModalForm(
            title=self.language_manager.GetText('INVITE_REWARD_CONFIG_TITLE'),
            controls=[item_name_input, item_count_input, money_input, free_blocks_input],
            on_close=None,
            on_submit=try_save_reward_config
        )
        player.send_form(config_panel)

    def switch_player_game_mode(self, player: Player):
        if player.game_mode == GameMode.CREATIVE:
            self.server.dispatch_command(
                self.server.command_sender,
                f'gamemode 0 {format_mc_command_player_name(player.name)}',
            )
        else:
            self.server.dispatch_command(
                self.server.command_sender,
                f'gamemode 1 {format_mc_command_player_name(player.name)}',
            )

    def clear_drop_item(self, player: Player):
        self.server.scheduler.run_task(self, self.delay_drop_item, delay=150)
        self.server.broadcast_message(self.language_manager.GetText('READY_TO_CLEAR_DROP_ITEM_BROADCAST'))

    def delay_drop_item(self):
        self.execute_cleaner()

    def _send_op_debug_message(self, player: Optional[Player], event_type: str, target_desc: str, dimension: str, x: float, y: float, z: float):
        """若该玩家开启了 OP 调试模式，则发送一条调试聊天消息"""
        if player is None or player.name not in self.op_debug_mode:
            return
        try:
            msg = self.language_manager.GetText('OP_DEBUG_MSG').format(
                event_type, target_desc, dimension,
                int(x) if x == math.floor(x) else x,
                int(y) if y == math.floor(y) else y,
                int(z) if z == math.floor(z) else z
            )
            player.send_message(msg)
        except Exception:
            pass

    def toggle_op_debug_mode(self, player: Player):
        """切换 OP 调试模式：开启后会在方块破坏/放置、方块交互、生物攻击、生物交互时向该玩家发送调试消息"""
        if player.name in self.op_debug_mode:
            self.op_debug_mode.discard(player.name)
            player.send_message(self.language_manager.GetText('OP_DEBUG_MODE_TOGGLED_OFF'))
        else:
            self.op_debug_mode.add(player.name)
            player.send_message(self.language_manager.GetText('OP_DEBUG_MODE_TOGGLED_ON'))
            try:
                dim_id = get_dimension_id(player.location.dimension)
                dim_label = self._translate_dimension_name(dim_id)
                loc = player.location
                player.send_message(
                    self.language_manager.GetText('OP_DEBUG_MSG').format(
                        'PlayerLocation',
                        player.name,
                        dim_id if dim_id == dim_label else f'{dim_label} ({dim_id})',
                        int(loc.x) if loc.x == math.floor(loc.x) else loc.x,
                        int(loc.y) if loc.y == math.floor(loc.y) else loc.y,
                        int(loc.z) if loc.z == math.floor(loc.z) else loc.z,
                    )
                )
            except Exception:
                pass
        self.show_op_tools_panel(player)

    def record_coordinate_1(self, player: Player):
        if not player.name in self.op_coordinate1_dict:
            self.op_coordinate1_dict[player.name] = None
        self.op_coordinate1_dict[player.name] = self.get_player_position_vector(player)

    def record_coordinate_2(self, player: Player):
        if not player.name in self.op_coordinate2_dict:
            self.op_coordinate2_dict[player.name] = None
        self.op_coordinate2_dict[player.name] = self.get_player_position_vector(player)
        self.show_op_tools_panel(player)

    def get_op_record_coor1(self, player: Player):
        if not player.name in self.op_coordinate1_dict or self.op_coordinate1_dict[player.name] is None:
            return self.get_player_position_vector(player)
        else:
            return self.op_coordinate1_dict[player.name]

    def get_op_record_coor2(self, player: Player):
        if not player.name in self.op_coordinate2_dict or self.op_coordinate2_dict[player.name] is None:
            return self.get_player_position_vector(player)
        else:
            return self.op_coordinate2_dict[player.name]

    def run_command_as_self(self, player: Player):
        command_input = TextInput(
            label=self.language_manager.GetText('RUN_COMMAND_PANEL_COMMAND_INPUT_LABEL'),
            placeholder=self.language_manager.GetText('RUN_COMMAND_PANEL_COMMAND_INPUT_PLACEHOLDER').format(player.name),
            default_value=''
        )

        def try_execute_command(player: Player, json_str: str):
            data = json.loads(json_str)
            command_str = (data[0].strip() if len(data) and data[0] is not None else '')
            if not command_str:
                command_str = self.op_last_command_dict.get(player.name, '')
            if not command_str:
                player.send_message(self.language_manager.GetText('RUN_COMMAND_PANEL_NO_LAST_COMMAND'))
                return
            self.op_last_command_dict[player.name] = command_str
            if '@p1' in command_str:
                command_str = command_str.replace('@p1', ' '.join([str(_) for _ in self.get_op_record_coor1(player)]))
            if '@p2' in command_str:
                command_str = command_str.replace('@p2', ' '.join([str(_) for _ in self.get_op_record_coor2(player)]))
            player.perform_command(command_str)

        command_input_form = ModalForm(
            title=self.language_manager.GetText('RUN_COMMAND_PANEL_TITLE'),
            controls=[command_input],
            on_close=None,
            on_submit=try_execute_command
        )
        player.send_form(command_input_form)
    
    def show_economy_manage_panel(self, player: Player):
        """OP 经济管理：在线玩家存款调整 + 经济相关配置。"""
        panel = ActionForm(
            title=self.language_manager.GetText('OP_ECONOMY_MANAGE_TITLE'),
            content=self.language_manager.GetText('OP_ECONOMY_MANAGE_CONTENT'),
            on_close=None,
        )
        panel.add_button(
            self.language_manager.GetText('OP_ECONOMY_ADJUST_MONEY_BUTTON'),
            on_click=self.show_money_manage_menu,
        )
        panel.add_button(
            self.language_manager.GetText('OP_ECONOMY_SETTINGS_BUTTON'),
            on_click=self.show_economy_settings_panel,
        )
        panel.add_button(self.language_manager.GetText('RETURN_BUTTON_TEXT'), on_click=self.show_op_main_panel)
        player.send_form(panel)

    def show_economy_settings_panel(self, player: Player):
        """OP 配置 PLAYER_INIT_MONEY_NUM、HIDE_OP_IN_MONEY_RANKING、RICHEST_TITLE_NAME。"""
        def _raw(key: str, fallback: str) -> str:
            v = self.setting_manager.GetSetting(key)
            if v is None or str(v).strip() == "":
                return fallback
            return str(v).strip()

        in_init_money = TextInput(
            label=self.language_manager.GetText('OP_ECONOMY_SET_INIT_MONEY_LABEL'),
            placeholder="2000",
            default_value=_raw("PLAYER_INIT_MONEY_NUM", "0"),
        )
        in_hide_op_rank = TextInput(
            label=self.language_manager.GetText('OP_ECONOMY_SET_HIDE_OP_RANK_LABEL'),
            placeholder="true / false",
            default_value="true" if self.hide_op_in_money_ranking else "false",
        )
        richest_default = self.setting_manager.GetSetting("RICHEST_TITLE_NAME")
        if richest_default is None or not str(richest_default).strip():
            richest_default = "首富"
        else:
            richest_default = str(richest_default).strip()
        in_richest_title = TextInput(
            label=self.language_manager.GetText('OP_ECONOMY_SET_RICHEST_TITLE_LABEL'),
            placeholder="首富",
            default_value=richest_default,
        )

        def try_save(p: Player, json_str: str):
            try:
                data = json.loads(json_str)
            except Exception:
                p.send_message(self.language_manager.GetText('OP_ECONOMY_SETTINGS_SAVE_FAIL'))
                return self.show_economy_manage_panel(p)
            if not data or len(data) < 3:
                p.send_message(self.language_manager.GetText('OP_ECONOMY_SETTINGS_SAVE_FAIL'))
                return self.show_economy_manage_panel(p)

            try:
                init_money = self._round_money(float(str(data[0]).strip()))
            except (ValueError, TypeError):
                p.send_message(self.language_manager.GetText('OP_ECONOMY_SETTINGS_SAVE_FAIL'))
                return self.show_economy_manage_panel(p)
            if init_money < 0:
                init_money = 0.0

            hide_raw = str(data[1]).strip().lower()
            hide_op = hide_raw in ("true", "1", "yes", "on")

            new_richest = str(data[2]).strip() if data[2] is not None else ""
            if not new_richest:
                new_richest = "首富"

            raw_old_r = self.setting_manager.GetSetting("RICHEST_TITLE_NAME")
            old_richest = (str(raw_old_r).strip() if raw_old_r else "") or "首富"
            can_compute = self._can_compute_conditional_titles()
            holder_xuid = self.current_richest_xuid if can_compute else None

            if can_compute and old_richest != new_richest and holder_xuid:
                try:
                    was_equipped = False
                    _, was_equipped = self.title_system.revoke_title_by_xuid(
                        holder_xuid, old_richest
                    )
                    if was_equipped:
                        unlocked = [
                            t
                            for t in self.title_system.get_unlocked_titles_by_xuid(holder_xuid)
                            if t and t != old_richest
                        ]
                        fallback = self.title_system.pick_highest_rarity_title(unlocked)
                        if fallback:
                            self.title_system.set_equipped_title_by_xuid(
                                holder_xuid, fallback
                            )
                    online = self._find_online_player_by_xuid(holder_xuid)
                    if online is not None:
                        self._update_player_name_tag(online)
                except Exception:
                    pass

            self.setting_manager.SetSetting("PLAYER_INIT_MONEY_NUM", init_money)
            self.setting_manager.SetSetting(
                "HIDE_OP_IN_MONEY_RANKING", "true" if hide_op else "false"
            )
            self.setting_manager.SetSetting("RICHEST_TITLE_NAME", new_richest)

            self.hide_op_in_money_ranking = hide_op
            self._ensure_richest_title_definition()
            try:
                self._update_richest_title_if_needed()
            except Exception:
                pass

            # 仅改名时 xuid 不变，refresh 会早退；权威服补发新名称头衔（无佩戴则戴上）
            if can_compute and old_richest != new_richest and self.current_richest_xuid:
                try:
                    grant_title = self._get_richest_title_name()
                    rx = self.current_richest_xuid
                    equipped_before = self.title_system.get_equipped_title_by_xuid(rx)
                    online_player = self._find_online_player_by_xuid(rx)
                    ok, was_new = self.title_system.unlock_title_by_xuid(
                        rx, grant_title, rarity="传奇"
                    )
                    _ = was_new
                    if ok and not equipped_before:
                        self.title_system.set_equipped_title_by_xuid(
                            rx, grant_title, "传奇"
                        )
                    if online_player is not None:
                        self._update_player_name_tag(online_player)
                except Exception:
                    pass

            result = ActionForm(
                title=self.language_manager.GetText('OP_ECONOMY_SETTINGS_TITLE'),
                content=self.language_manager.GetText('OP_ECONOMY_SETTINGS_SAVED'),
                on_close=None,
            )
            result.add_button(self.language_manager.GetText('RETURN_BUTTON_TEXT'), on_click=self.show_economy_manage_panel)
            p.send_form(result)

        form = ModalForm(
            title=self.language_manager.GetText('OP_ECONOMY_SETTINGS_TITLE'),
            controls=[in_init_money, in_hide_op_rank, in_richest_title],
            on_close=None,
            on_submit=try_save,
        )
        player.send_form(form)

    # Money Management UI（经济管理子菜单：调整在线玩家存款）
    def show_money_manage_menu(self, player: Player):
        """选择增加或减少在线玩家存款"""
        money_menu = ActionForm(
            title=self.language_manager.GetText('MONEY_MANAGE_MENU_TITLE'),
            content=self.language_manager.GetText('MONEY_MANAGE_MENU_CONTENT'),
            on_close=None,
        )
        money_menu.add_button(
            self.language_manager.GetText('MONEY_MANAGE_ADD_BUTTON'),
            on_click=lambda p=player, op_type='add': self.show_money_manage_select_player(p, op_type)
        )
        money_menu.add_button(
            self.language_manager.GetText('MONEY_MANAGE_REMOVE_BUTTON'),
            on_click=lambda p=player, op_type='remove': self.show_money_manage_select_player(p, op_type)
        )
        player.send_form(money_menu)
    
    def show_money_manage_select_player(self, player: Player, operation_type: str):
        """显示选择玩家面板"""
        online_players = [p for p in self.server.online_players]
        if not online_players:
            no_players_panel = ActionForm(
                title=self.language_manager.GetText('MONEY_MANAGE_SELECT_PLAYER_TITLE'),
                content=self.language_manager.GetText('NO_OTHER_PLAYERS_ONLINE'),
                on_close=None,
            )
            player.send_form(no_players_panel)
            return
        
        select_player_menu = ActionForm(
            title=self.language_manager.GetText('MONEY_MANAGE_SELECT_PLAYER_TITLE'),
            content=self.language_manager.GetText('MONEY_MANAGE_SELECT_PLAYER_CONTENT'),
            on_close=None,
        )
        
        for target_player in online_players:
            # 显示玩家名称和当前余额
            player_info = f"{target_player.name} (余额: {self._format_money_display(self.get_player_money(target_player))})"
            select_player_menu.add_button(
                player_info,
                on_click=lambda p=player, t=target_player, op=operation_type: self.show_money_manage_input_amount(p, t, op)
            )
        
        player.send_form(select_player_menu)
    
    def show_money_manage_input_amount(self, player: Player, target_player: Player, operation_type: str):
        """显示输入金额面板"""
        amount_input = TextInput(
            label=self.language_manager.GetText('MONEY_MANAGE_INPUT_AMOUNT_LABEL'),
            placeholder=self.language_manager.GetText('MONEY_MANAGE_INPUT_AMOUNT_PLACEHOLDER')
        )
        
        def try_change_money(player: Player, json_str: str):
            data = json.loads(json_str)
            if not len(data) or not data[0]:
                player.send_message(self.language_manager.GetText('MONEY_MANAGE_AMOUNT_EMPTY'))
                return
            
            try:
                amount = self._round_money(float(data[0]))
                if amount <= 0:
                    raise ValueError
            except (ValueError, TypeError):
                player.send_message(self.language_manager.GetText('MONEY_MANAGE_INVALID_AMOUNT'))
                return
            
            # 执行金钱操作
            if operation_type == 'add':
                if self.increase_player_money(target_player, amount):
                    player.send_message(self.language_manager.GetText('MONEY_SYSTEM_ADD_MONEY_SUCCESS').format(
                        target_player.name,
                        self._format_money_display(amount),
                        self._format_money_display(self.get_player_money(target_player))
                    ))
                else:
                    player.send_message(self.language_manager.GetText('MONEY_SYSTEM_ADD_MONEY_FAILED'))
            else:  # remove
                if self.decrease_player_money(target_player, amount):
                    player.send_message(self.language_manager.GetText('MONEY_SYSTEM_REMOVE_MONEY_SUCCESS').format(
                        target_player.name,
                        self._format_money_display(amount),
                        self._format_money_display(self.get_player_money(target_player))
                    ))
                else:
                    player.send_message(self.language_manager.GetText('MONEY_SYSTEM_REMOVE_MONEY_FAILED'))
            
            self.show_economy_manage_panel(player)
        
        amount_input_form = ModalForm(
            title=self.language_manager.GetText('MONEY_MANAGE_INPUT_AMOUNT_TITLE'),
            controls=[amount_input],
            on_close=None,
            on_submit=try_change_money
        )
        player.send_form(amount_input_form)
    
    # OP Manage All Lands
    OP_ALL_LANDS_PAGE_SIZE = 15
    
    def show_op_all_lands_panel(self, player: Player, page: int = 0):
        """显示全服领地列表（分页）"""
        all_lands = self.get_all_lands()
        if not all_lands:
            empty_panel = ActionForm(
                title=self.language_manager.GetText('OP_ALL_LANDS_MENU_TITLE'),
                content=self.language_manager.GetText('OP_ALL_LANDS_EMPTY'),
                on_close=None,
            )
            player.send_form(empty_panel)
            return
        
        land_ids = sorted(all_lands.keys())
        total_pages = max(1, (len(land_ids) + self.OP_ALL_LANDS_PAGE_SIZE - 1) // self.OP_ALL_LANDS_PAGE_SIZE)
        page = max(0, min(page, total_pages - 1))
        start = page * self.OP_ALL_LANDS_PAGE_SIZE
        end = min(start + self.OP_ALL_LANDS_PAGE_SIZE, len(land_ids))
        page_land_ids = land_ids[start:end]
        
        menu = ActionForm(
            title=self.language_manager.GetText('OP_ALL_LANDS_MENU_TITLE'),
            content=self.language_manager.GetText('OP_ALL_LANDS_MENU_CONTENT').format(len(land_ids), page + 1),
            on_close=None,
        )
        
        for land_id in page_land_ids:
            land_info = all_lands[land_id]
            owner_name = self.get_land_display_owner_name(land_id)
            btn_text = self.language_manager.GetText('OP_ALL_LANDS_BUTTON_TEXT').format(
                land_id,
                land_info['land_name'],
                owner_name,
                land_info['dimension']
            )
            menu.add_button(
                btn_text,
                on_click=lambda p=player, l_id=land_id, pg=page: self.show_op_land_detail_panel(p, l_id, pg)
            )
        
        if page > 0:
            menu.add_button(
                self.language_manager.GetText('OP_ALL_LANDS_PREV_PAGE'),
                on_click=lambda p=player, pg=page: self.show_op_all_lands_panel(p, pg - 1)
            )
        if page < total_pages - 1:
            menu.add_button(
                self.language_manager.GetText('OP_ALL_LANDS_NEXT_PAGE'),
                on_click=lambda p=player, pg=page: self.show_op_all_lands_panel(p, pg + 1)
            )
        menu.add_button(
            self.language_manager.GetText('RETURN_BUTTON_TEXT'),
            on_click=self.show_op_land_manage_panel
        )
        player.send_form(menu)
    
    def show_op_land_detail_panel(self, player: Player, land_id: int, from_page: int = 0):
        """OP 查看单个领地详情（可传送前往）"""
        land_info = self.get_land_info(land_id)
        if not land_info:
            self.report_arc_error(
                "OP3",
                f"show_op_land_detail_panel get_land_info empty land_id={land_id!r}",
                player,
            )
            self.show_op_all_lands_panel(player, from_page)
            return
        
        if len(land_info['shared_users']):
            shared_user_names = [self.get_player_name_by_xuid(uid) or uid for uid in land_info['shared_users']]
            shared_user_name_str = '\n'.join(shared_user_names)
        else:
            shared_user_name_str = self.language_manager.GetText('LAND_DETAIL_NO_SHARED_USER_TEXT')
        
        owner_name = self.get_land_display_owner_name(land_id)
        
        content = self.language_manager.GetText('LAND_DETAIL_PANEL_CONTENT').format(
            land_id,
            land_info['land_name'],
            land_info['dimension'],
            (int(land_info['min_x']), int(land_info['min_z'])),
            (int(land_info['max_x']), int(land_info['max_z'])),
            (int(land_info['tp_x']), int(land_info['tp_y']), int(land_info['tp_z'])),
            shared_user_name_str
        )
        content = f"所有者: {owner_name}\n\n" + content
        if self.is_public_land(land_id):
            pri = LandSystem.clamp_public_priority(
                land_info.get("public_priority", LandSystem.PUBLIC_PRIORITY_DEFAULT)
            )
            content += "\n" + self.language_manager.GetText(
                "PUBLIC_LAND_PRIORITY_CURRENT_STATUS"
            ).format(pri)
        
        detail_panel = ActionForm(
            title=self.language_manager.GetText('OP_LAND_DETAIL_TITLE').format(land_id),
            content=content,
            on_close=None,
        )
        # 传送前往
        detail_panel.add_button(
            self.language_manager.GetText('OP_LAND_TELEPORT_BUTTON'),
            on_click=lambda p=player, l_id=land_id: self.op_teleport_to_land(p, l_id)
        )
        # 强制修改领地名称（所有领地均可用）
        detail_panel.add_button(
            self.language_manager.GetText('OP_LAND_RENAME_BUTTON'),
            on_click=lambda p=player, l_id=land_id, pg=from_page: self.show_op_rename_land_panel(p, l_id, pg)
        )
        # 管理授权（添加/移除授权玩家）
        detail_panel.add_button(
            self.language_manager.GetText('OP_LAND_MANAGE_AUTH_BUTTON'),
            on_click=lambda p=player, l_id=land_id, pg=from_page: self.show_op_land_auth_manage_panel(p, l_id, pg)
        )
        if self.is_public_land(land_id):
            detail_panel.add_button(
                self.language_manager.GetText('OP_PUBLIC_LAND_SETTINGS_BUTTON'),
                on_click=lambda p=player, l_id=land_id, pg=from_page: self.show_op_public_land_settings_panel(p, l_id, pg)
            )
            detail_panel.add_button(
                self.language_manager.GetText('LAND_RESIZE_OP_PUBLIC_BUTTON'),
                on_click=lambda p=player, l_id=land_id, pg=from_page: self._op_start_public_land_resize(
                    p, l_id, pg
                ),
            )
        else:
            detail_panel.add_button(
                self.language_manager.GetText('OP_SET_LAND_PUBLIC_BUTTON'),
                on_click=lambda p=player, l_id=land_id, pg=from_page: self.show_op_confirm_set_land_public(p, l_id, pg)
            )
        detail_panel.add_button(
            self.language_manager.GetText('OP_FORCE_DELETE_LAND_BUTTON'),
            on_click=lambda p=player, l_id=land_id, pg=from_page: self.show_op_force_delete_land_confirm(p, l_id, pg)
        )
        detail_panel.add_button(
            self.language_manager.GetText('RETURN_BUTTON_TEXT'),
            on_click=lambda p=player, pg=from_page: self.show_op_all_lands_panel(p, pg)
        )
        player.send_form(detail_panel)
    
    def op_teleport_to_land(self, player: Player, land_id: int):
        """OP 传送到领地（不扣费）"""
        tp_target_pos = self.get_land_teleport_point(land_id)
        if tp_target_pos is None:
            self.report_arc_error(
                "OP4",
                f"op_teleport_to_land get_land_teleport_point None land_id={land_id!r}",
                player,
            )
            return
        self.run_player_task(
            player,
            lambda p, l_id=land_id, pos=tp_target_pos: self.delay_teleport_to_land(
                p, l_id, pos
            ),
            delay=45,
        )
        player.send_message(self.language_manager.GetText('READY_TELEPORT_TO_LAND').format(land_id))
    
    def show_op_confirm_set_land_public(self, player: Player, land_id: int, from_page: int):
        """OP 确认设为公共领地面板"""
        land_info = self.get_land_info(land_id)
        if not land_info:
            self.report_arc_error(
                "OP5",
                f"show_op_confirm_set_land_public get_land_info empty land_id={land_id!r}",
                player,
            )
            self.show_op_all_lands_panel(player, from_page)
            return
        confirm_panel = ActionForm(
            title=self.language_manager.GetText('OP_CONFIRM_SET_PUBLIC_TITLE'),
            content=self.language_manager.GetText('OP_CONFIRM_SET_PUBLIC_CONTENT').format(land_id),
            on_close=None,
        )
        confirm_panel.add_button(
            self.language_manager.GetText('OP_CONFIRM_SET_PUBLIC_BUTTON'),
            on_click=lambda p=player, l_id=land_id, pg=from_page: self.op_do_set_land_public(p, l_id, pg)
        )
        confirm_panel.add_button(
            self.language_manager.GetText('RETURN_BUTTON_TEXT'),
            on_click=lambda p=player, l_id=land_id, pg=from_page: self.show_op_land_detail_panel(p, l_id, pg)
        )
        player.send_form(confirm_panel)
    
    def op_do_set_land_public(self, player: Player, land_id: int, from_page: int):
        """OP 执行设为公共领地"""
        if self.set_land_as_public(land_id):
            player.send_message(self.language_manager.GetText('OP_SET_LAND_PUBLIC_SUCCESS').format(land_id))
            self.show_op_land_detail_panel(player, land_id, from_page)
        else:
            player.send_message(self.language_manager.GetText('OP_SET_LAND_PUBLIC_FAILED'))
            self.show_op_land_detail_panel(player, land_id, from_page)
    
    def show_op_rename_land_panel(self, player: Player, land_id: int, from_page: int):
        """OP 修改领地名称面板（用于公共领地等）"""
        land_info = self.get_land_info(land_id)
        if not land_info:
            self.report_arc_error(
                "OP6",
                f"show_op_rename_land_panel get_land_info empty land_id={land_id!r}",
                player,
            )
            self.show_op_all_lands_panel(player, from_page)
            return
        current_name = land_info['land_name']
        new_name_input = TextInput(
            label=self.language_manager.GetText('RENAME_OWN_LAND_PANEL_INPUT_LABEL').format(land_id),
            placeholder=self.language_manager.GetText('RENAME_OWN_LAND_PANEL_INPUT_PLACEHOLDER').format(player.name),
            default_value=current_name
        )
        
        def try_change_name(player: Player, json_str: str):
            data = json.loads(json_str)
            if not data or not data[0]:
                player.send_message(self.language_manager.GetText('CREATE_HOME_EMPTY_NAME_ERROR'))
                return
            success, msg = self.rename_land(land_id, data[0])
            player.send_message(msg)
            self.show_op_land_detail_panel(player, land_id, from_page)
        
        rename_panel = ModalForm(
            title=self.language_manager.GetText('RENAME_OWN_LAND_PANEL_TITLE'),
            controls=[new_name_input],
            on_close=None,
            on_submit=try_change_name
        )
        player.send_form(rename_panel)

    def show_op_land_auth_manage_panel(self, player: Player, land_id: int, from_page: int):
        """OP 领地授权管理面板（添加/移除授权玩家）"""
        land_info = self.get_land_info(land_id)
        if not land_info:
            self.report_arc_error(
                "OP7",
                f"show_op_land_auth_manage_panel get_land_info empty land_id={land_id!r}",
                player,
            )
            self.show_op_all_lands_panel(player, from_page)
            return
        auth_panel = ActionForm(
            title=self.language_manager.GetText('OP_LAND_MANAGE_AUTH_BUTTON'),
            content=self.language_manager.GetText('LAND_AUTH_MANAGE_TITLE'),
            on_close=None,
        )
        auth_panel.add_button(
            self.language_manager.GetText('LAND_AUTH_ADD_BUTTON'),
            on_click=lambda p=player, l_id=land_id, pg=from_page: self.show_op_add_land_auth_panel(p, l_id, pg)
        )
        if land_info['shared_users']:
            auth_panel.add_button(
                self.language_manager.GetText('LAND_AUTH_REMOVE_BUTTON'),
                on_click=lambda p=player, l_id=land_id, pg=from_page: self.show_op_remove_land_auth_panel(p, l_id, pg)
            )
        auth_panel.add_button(
            self.language_manager.GetText('RETURN_BUTTON_TEXT'),
            on_click=lambda p=player, l_id=land_id, pg=from_page: self.show_op_land_detail_panel(p, l_id, pg)
        )
        player.send_form(auth_panel)

    def show_op_add_land_auth_panel(self, player: Player, land_id: int, from_page: int):
        """OP 添加领地授权：选择在线玩家"""
        online_players = [p for p in self.server.online_players]
        if not online_players:
            no_players_panel = ActionForm(
                title=self.language_manager.GetText('LAND_AUTH_ADD_PANEL_TITLE'),
                content=self.language_manager.GetText('NO_OTHER_PLAYERS_ONLINE'),
                on_close=None,
            )
            player.send_form(no_players_panel)
            return
        add_panel = ActionForm(
            title=self.language_manager.GetText('LAND_AUTH_ADD_PANEL_TITLE'),
            content=self.language_manager.GetText('LAND_AUTH_SELECT_PLAYER_CONTENT'),
            on_close=None,
        )
        for target_player in online_players:
            add_panel.add_button(
                self.language_manager.GetText('LAND_AUTH_ADD_TARGET_BUTTON').format(target_player.name),
                on_click=lambda p=player, l_id=land_id, t=target_player, pg=from_page: self.op_add_land_auth(p, l_id, t, pg)
            )
        player.send_form(add_panel)

    def show_op_remove_land_auth_panel(self, player: Player, land_id: int, from_page: int):
        """OP 移除领地授权：选择要移除的授权玩家"""
        land_info = self.get_land_info(land_id)
        if not land_info or not land_info['shared_users']:
            no_auth_panel = ActionForm(
                title=self.language_manager.GetText('LAND_AUTH_REMOVE_PANEL_TITLE'),
                content=self.language_manager.GetText('LAND_AUTH_NO_SHARED_USERS'),
                on_close=None,
            )
            player.send_form(no_auth_panel)
            return
        remove_panel = ActionForm(
            title=self.language_manager.GetText('LAND_AUTH_REMOVE_PANEL_TITLE'),
            content=self.language_manager.GetText('LAND_AUTH_SELECT_REMOVE_CONTENT'),
            on_close=None,
        )
        for shared_xuid in land_info['shared_users']:
            raw_name = self.get_player_name_by_xuid(shared_xuid, return_with_title=False)
            if not raw_name:
                continue
            display_name = self.get_player_name_by_xuid(shared_xuid, return_with_title=True) or raw_name
            remove_panel.add_button(
                self.language_manager.GetText('LAND_AUTH_REMOVE_TARGET_BUTTON').format(display_name),
                on_click=lambda p=player, l_id=land_id, uid=shared_xuid, name=raw_name, pg=from_page: self.op_remove_land_auth(p, l_id, uid, name, pg)
            )
        player.send_form(remove_panel)

    def op_add_land_auth(self, player: Player, land_id: int, target_player: Player, from_page: int):
        """OP 执行添加领地授权"""
        try:
            land_info = self.get_land_info(land_id)
            if not land_info:
                self.report_arc_error(
                    "OP8",
                    f"op_add_land_auth get_land_info empty land_id={land_id!r}",
                    player,
                )
                self.show_op_land_auth_manage_panel(player, land_id, from_page)
                return
            target_xuid = str(target_player.xuid)
            if target_xuid in land_info['shared_users']:
                player.send_message(self.language_manager.GetText('LAND_AUTH_ALREADY_EXISTS').format(target_player.name))
                self.show_op_land_auth_manage_panel(player, land_id, from_page)
                return
            success = self.land_system.add_land_shared_user(land_id, target_xuid)
            if success:
                player.send_message(self.language_manager.GetText('LAND_AUTH_SUCCESS_ADD').format(land_id, target_player.name))
                self._notify_important(
                    target_player,
                    self.language_manager.GetText('LAND_AUTH_NOTIFICATION').format(
                        player.name, land_id, land_info['land_name']
                    ),
                    title=self._toast_title("LAND_RESULT_TOAST_TITLE", "领地"),
                )
            else:
                player.send_message(self.language_manager.GetText('LAND_AUTH_FAILED_ADD'))
        except Exception as e:
            self.logger.error(f"OP add land auth error: {str(e)}")
            player.send_message(self.language_manager.GetText('LAND_AUTH_FAILED_ADD'))
        self.show_op_land_auth_manage_panel(player, land_id, from_page)

    def op_remove_land_auth(self, player: Player, land_id: int, target_xuid: str, target_name: str, from_page: int):
        """OP 执行移除领地授权"""
        try:
            land_info = self.get_land_info(land_id)
            if not land_info:
                self.report_arc_error(
                    "OP9",
                    f"op_remove_land_auth get_land_info empty land_id={land_id!r}",
                    player,
                )
                self.show_op_land_auth_manage_panel(player, land_id, from_page)
                return
            if target_xuid not in land_info['shared_users']:
                player.send_message(self.language_manager.GetText('LAND_AUTH_NOT_EXISTS').format(target_name))
                self.show_op_land_auth_manage_panel(player, land_id, from_page)
                return
            success = self.land_system.remove_land_shared_user(land_id, target_xuid)
            if success:
                player.send_message(self.language_manager.GetText('LAND_AUTH_SUCCESS_REMOVE').format(target_name, land_id))
                target_player = self.server.get_player(target_name)
                if target_player:
                    self._notify_important(
                        target_player,
                        self.language_manager.GetText('LAND_AUTH_REMOVE_NOTIFICATION').format(
                            player.name, land_id, land_info['land_name']
                        ),
                        title=self._toast_title("LAND_RESULT_TOAST_TITLE", "领地"),
                    )
            else:
                player.send_message(self.language_manager.GetText('LAND_AUTH_FAILED_REMOVE'))
        except Exception as e:
            self.logger.error(f"OP remove land auth error: {str(e)}")
            player.send_message(self.language_manager.GetText('LAND_AUTH_FAILED_REMOVE'))
        self.show_op_land_auth_manage_panel(player, land_id, from_page)

    def show_op_public_land_settings_panel(self, player: Player, land_id: int, from_page: int):
        """OP 公共领地设置面板：开放互动/开放爆炸/开放生物互动/开放生物伤害/拦截生物生成等"""
        land_info = self.get_land_info(land_id)
        if not land_info:
            self.report_arc_error(
                "OP10",
                f"show_op_public_land_settings_panel get_land_info empty land_id={land_id!r}",
                player,
            )
            self.show_op_all_lands_panel(player, from_page)
            return
        status_lines = []
        pri = LandSystem.clamp_public_priority(
            land_info.get("public_priority", LandSystem.PUBLIC_PRIORITY_DEFAULT)
        )
        status_lines.append(
            self.language_manager.GetText("PUBLIC_LAND_PRIORITY_CURRENT_STATUS").format(pri)
        )
        status_lines.append('开放方块互动: ' + (self.language_manager.GetText('LAND_PUBLIC_INTERACT_STATUS_ENABLED') if land_info.get('allow_public_interact') else self.language_manager.GetText('LAND_PUBLIC_INTERACT_STATUS_DISABLED')))
        status_lines.append(
            '同公会成员可互动: '
            + (
                self.language_manager.GetText('LAND_GUILD_MEMBER_INTERACT_STATUS_ENABLED')
                if land_info.get('allow_guild_member_interact')
                else self.language_manager.GetText('LAND_GUILD_MEMBER_INTERACT_STATUS_DISABLED')
            )
        )
        status_lines.append('开放爆炸: ' + (self.language_manager.GetText('LAND_EXPLOSION_STATUS_ENABLED') if land_info.get('allow_explosion') else self.language_manager.GetText('LAND_EXPLOSION_STATUS_DISABLED')))
        status_lines.append('开放生物互动: ' + (self.language_manager.GetText('LAND_ACTOR_INTERACTION_STATUS_ENABLED') if land_info.get('allow_actor_interaction') else self.language_manager.GetText('LAND_ACTOR_INTERACTION_STATUS_DISABLED')))
        status_lines.append('展示框: ' + (self.language_manager.GetText('LAND_FRAME_STATUS_ENABLED') if land_info.get('allow_frame') else self.language_manager.GetText('LAND_FRAME_STATUS_DISABLED')))
        status_lines.append('开放生物伤害: ' + (self.language_manager.GetText('LAND_ACTOR_DAMAGE_STATUS_ENABLED') if land_info.get('allow_actor_damage') else self.language_manager.GetText('LAND_ACTOR_DAMAGE_STATUS_DISABLED')))
        spawn_status = (
            self.language_manager.GetText('BLOCK_ACTOR_SPAWN_STATUS_ENABLED')
            if land_info.get('block_actor_spawn')
            else self.language_manager.GetText('BLOCK_ACTOR_SPAWN_STATUS_DISABLED')
        )
        status_lines.append(
            self.language_manager.GetText('BLOCK_ACTOR_SPAWN_CURRENT_STATUS').format(spawn_status)
        )
        anpl_enabled = self.language_manager.GetText('ALLOW_NON_PUBLIC_LAND_STATUS_ENABLED') if land_info.get('allow_non_public_land') else self.language_manager.GetText('ALLOW_NON_PUBLIC_LAND_STATUS_DISABLED')
        status_lines.append(self.language_manager.GetText('ALLOW_NON_PUBLIC_LAND_CURRENT_STATUS').format(anpl_enabled))
        content = '\n'.join(status_lines)
        settings_panel = ActionForm(
            title=self.language_manager.GetText('OP_PUBLIC_LAND_SETTINGS_BUTTON'),
            content=content,
            on_close=None,
        )
        settings_panel.add_button(
            self.language_manager.GetText('PUBLIC_LAND_PRIORITY_SETTING_BUTTON_TEXT'),
            on_click=lambda p=player, l_id=land_id, pg=from_page: self.show_op_public_land_priority_panel(p, l_id, pg)
        )
        settings_panel.add_button(
            self.language_manager.GetText('LAND_PUBLIC_INTERACT_SETTING_BUTTON_TEXT'),
            on_click=lambda p=player, l_id=land_id, pg=from_page: self.show_op_public_land_toggle_panel(p, l_id, 'allow_public_interact', pg)
        )
        settings_panel.add_button(
            self.language_manager.GetText('LAND_GUILD_MEMBER_INTERACT_SETTING_BUTTON_TEXT'),
            on_click=lambda p=player, l_id=land_id, pg=from_page: self.show_op_public_land_toggle_panel(p, l_id, 'allow_guild_member_interact', pg)
        )
        settings_panel.add_button(
            self.language_manager.GetText('LAND_EXPLOSION_SETTING_BUTTON_TEXT'),
            on_click=lambda p=player, l_id=land_id, pg=from_page: self.show_op_public_land_toggle_panel(p, l_id, 'allow_explosion', pg)
        )
        settings_panel.add_button(
            self.language_manager.GetText('LAND_ACTOR_INTERACTION_SETTING_BUTTON_TEXT'),
            on_click=lambda p=player, l_id=land_id, pg=from_page: self.show_op_public_land_toggle_panel(p, l_id, 'allow_actor_interaction', pg)
        )
        settings_panel.add_button(
            self.language_manager.GetText('LAND_FRAME_SETTING_BUTTON_TEXT'),
            on_click=lambda p=player, l_id=land_id, pg=from_page: self.show_op_public_land_toggle_panel(p, l_id, 'allow_frame', pg)
        )
        settings_panel.add_button(
            self.language_manager.GetText('LAND_ACTOR_DAMAGE_SETTING_BUTTON_TEXT'),
            on_click=lambda p=player, l_id=land_id, pg=from_page: self.show_op_public_land_toggle_panel(p, l_id, 'allow_actor_damage', pg)
        )
        settings_panel.add_button(
            self.language_manager.GetText('BLOCK_ACTOR_SPAWN_SETTING_BUTTON_TEXT'),
            on_click=lambda p=player, l_id=land_id, pg=from_page: self.show_op_public_land_toggle_panel(p, l_id, 'block_actor_spawn', pg)
        )
        settings_panel.add_button(
            self.language_manager.GetText('ALLOW_NON_PUBLIC_LAND_SETTING_BUTTON_TEXT'),
            on_click=lambda p=player, l_id=land_id, pg=from_page: self.show_op_public_land_toggle_panel(p, l_id, 'allow_non_public_land', pg)
        )
        settings_panel.add_button(
            self.language_manager.GetText('RETURN_BUTTON_TEXT'),
            on_click=lambda p=player, l_id=land_id, pg=from_page: self.show_op_land_detail_panel(p, l_id, pg)
        )
        player.send_form(settings_panel)

    def show_op_public_land_priority_panel(self, player: Player, land_id: int, from_page: int):
        """OP 修改公共领地优先级面板"""
        land_info = self.get_land_info(land_id)
        if not land_info or not self.is_public_land(land_id):
            self.show_op_all_lands_panel(player, from_page)
            return
        current = LandSystem.clamp_public_priority(
            land_info.get("public_priority", LandSystem.PUBLIC_PRIORITY_DEFAULT)
        )
        panel = ActionForm(
            title=self.language_manager.GetText("PUBLIC_LAND_PRIORITY_SETTING_TITLE"),
            content=self.language_manager.GetText(
                "PUBLIC_LAND_PRIORITY_CURRENT_STATUS"
            ).format(current),
            on_close=None,
        )
        for level in (1, 2, 3):
            label_key = f"PUBLIC_LAND_PRIORITY_OPTION_{level}"
            btn = self.language_manager.GetText(label_key)
            if level == current:
                btn = self.language_manager.GetText(
                    "PUBLIC_LAND_PRIORITY_OPTION_CURRENT"
                ).format(btn)
            panel.add_button(
                btn,
                on_click=lambda p=player, l_id=land_id, lv=level, pg=from_page: self.op_set_public_land_priority(
                    p, l_id, lv, pg
                ),
            )
        panel.add_button(
            self.language_manager.GetText("RETURN_BUTTON_TEXT"),
            on_click=lambda p=player, l_id=land_id, pg=from_page: self.show_op_public_land_settings_panel(
                p, l_id, pg
            ),
        )
        player.send_form(panel)

    def op_set_public_land_priority(
        self, player: Player, land_id: int, priority: int, from_page: int
    ):
        """OP 设置公共领地优先级；与同级/更高公共重叠时拒绝。"""
        land_info = self.get_land_info(land_id)
        if not land_info or not self.is_public_land(land_id):
            player.send_message(
                self.language_manager.GetText("PUBLIC_LAND_PRIORITY_FAILED")
            )
            self.show_op_public_land_settings_panel(player, land_id, from_page)
            return
        new_priority = LandSystem.clamp_public_priority(priority)
        old_priority = LandSystem.clamp_public_priority(
            land_info.get("public_priority", LandSystem.PUBLIC_PRIORITY_DEFAULT)
        )
        if new_priority == old_priority:
            self.show_op_public_land_settings_panel(player, land_id, from_page)
            return
        # 仅升高优先级时校验：不可与同级/更高公共冲突；
        # 若已开启「允许圈私人领地」则跳过私人/公会冲突，否则仍拦截。
        if new_priority > old_priority:
            if_allowed, reason, overlap_ids = self.check_land_availability(
                land_info["dimension"],
                land_info["min_x"],
                land_info["max_x"],
                land_info.get("min_y", 0),
                land_info.get("max_y", 255),
                land_info["min_z"],
                land_info["max_z"],
                exclude_land_ids={land_id},
                creating_public_priority=new_priority,
                creating_allow_non_public_land=bool(
                    land_info.get("allow_non_public_land")
                ),
            )
            if not if_allowed:
                msg = self.language_manager.GetText("PUBLIC_LAND_PRIORITY_CONFLICT")
                if overlap_ids:
                    land_parts = [
                        f"#{lid} {self.get_land_name(lid) or ''}".strip()
                        for lid in overlap_ids
                    ]
                    msg = msg + "\n" + self.language_manager.GetText(
                        "LAND_OVERLAP_WITH_LANDS"
                    ).format(", ".join(land_parts))
                player.send_message(msg)
                self.show_op_public_land_priority_panel(player, land_id, from_page)
                return
        if self.land_system.set_land_public_priority(land_id, new_priority):
            player.send_message(
                self.language_manager.GetText("PUBLIC_LAND_PRIORITY_UPDATED").format(
                    land_id, new_priority
                )
            )
        else:
            player.send_message(
                self.language_manager.GetText("PUBLIC_LAND_PRIORITY_FAILED")
            )
        self.show_op_public_land_settings_panel(player, land_id, from_page)
    
    def show_op_public_land_toggle_panel(self, player: Player, land_id: int, setting_key: str, from_page: int):
        """OP 公共领地单项设置切换面板"""
        land_info = self.get_land_info(land_id)
        if not land_info:
            self.report_arc_error(
                "OP11",
                f"show_op_public_land_toggle_panel get_land_info empty land_id={land_id!r} key={setting_key!r}",
                player,
            )
            self.show_op_all_lands_panel(player, from_page)
            return
        current = land_info.get(setting_key, False)
        if setting_key == 'allow_public_interact':
            status_text = self.language_manager.GetText('LAND_PUBLIC_INTERACT_STATUS_ENABLED') if current else self.language_manager.GetText('LAND_PUBLIC_INTERACT_STATUS_DISABLED')
            title = self.language_manager.GetText('LAND_PUBLIC_INTERACT_SETTING_TITLE')
        elif setting_key == 'allow_guild_member_interact':
            status_text = self.language_manager.GetText('LAND_GUILD_MEMBER_INTERACT_STATUS_ENABLED') if current else self.language_manager.GetText('LAND_GUILD_MEMBER_INTERACT_STATUS_DISABLED')
            title = self.language_manager.GetText('LAND_GUILD_MEMBER_INTERACT_SETTING_TITLE')
        elif setting_key == 'allow_explosion':
            status_text = self.language_manager.GetText('LAND_EXPLOSION_STATUS_ENABLED') if current else self.language_manager.GetText('LAND_EXPLOSION_STATUS_DISABLED')
            title = self.language_manager.GetText('LAND_EXPLOSION_SETTING_TITLE')
        elif setting_key == 'allow_actor_interaction':
            status_text = self.language_manager.GetText('LAND_ACTOR_INTERACTION_STATUS_ENABLED') if current else self.language_manager.GetText('LAND_ACTOR_INTERACTION_STATUS_DISABLED')
            title = self.language_manager.GetText('LAND_ACTOR_INTERACTION_SETTING_TITLE')
        elif setting_key == 'allow_frame':
            status_text = self.language_manager.GetText('LAND_FRAME_STATUS_ENABLED') if current else self.language_manager.GetText('LAND_FRAME_STATUS_DISABLED')
            title = self.language_manager.GetText('LAND_FRAME_SETTING_TITLE')
        elif setting_key == 'allow_non_public_land':
            status_text = self.language_manager.GetText('ALLOW_NON_PUBLIC_LAND_STATUS_ENABLED') if current else self.language_manager.GetText('ALLOW_NON_PUBLIC_LAND_STATUS_DISABLED')
            title = self.language_manager.GetText('ALLOW_NON_PUBLIC_LAND_SETTING_BUTTON_TEXT')
        elif setting_key == 'block_actor_spawn':
            status_text = self.language_manager.GetText('BLOCK_ACTOR_SPAWN_STATUS_ENABLED') if current else self.language_manager.GetText('BLOCK_ACTOR_SPAWN_STATUS_DISABLED')
            title = self.language_manager.GetText('BLOCK_ACTOR_SPAWN_SETTING_TITLE')
        else:  # allow_actor_damage
            status_text = self.language_manager.GetText('LAND_ACTOR_DAMAGE_STATUS_ENABLED') if current else self.language_manager.GetText('LAND_ACTOR_DAMAGE_STATUS_DISABLED')
            title = self.language_manager.GetText('LAND_ACTOR_DAMAGE_SETTING_TITLE')
        toggle_panel = ActionForm(
            title=title,
            content=status_text,
            on_close=None,
        )
        enable_key = {
            'allow_public_interact': ('LAND_PUBLIC_INTERACT_TOGGLE_ENABLE_BUTTON', 'LAND_PUBLIC_INTERACT_TOGGLE_DISABLE_BUTTON'),
            'allow_guild_member_interact': ('LAND_GUILD_MEMBER_INTERACT_TOGGLE_ENABLE_BUTTON', 'LAND_GUILD_MEMBER_INTERACT_TOGGLE_DISABLE_BUTTON'),
            'allow_explosion': ('LAND_EXPLOSION_TOGGLE_ENABLE_BUTTON', 'LAND_EXPLOSION_TOGGLE_DISABLE_BUTTON'),
            'allow_actor_interaction': ('LAND_ACTOR_INTERACTION_TOGGLE_ENABLE_BUTTON', 'LAND_ACTOR_INTERACTION_TOGGLE_DISABLE_BUTTON'),
            'allow_frame': ('LAND_FRAME_TOGGLE_ENABLE_BUTTON', 'LAND_FRAME_TOGGLE_DISABLE_BUTTON'),
            'allow_actor_damage': ('LAND_ACTOR_DAMAGE_TOGGLE_ENABLE_BUTTON', 'LAND_ACTOR_DAMAGE_TOGGLE_DISABLE_BUTTON'),
            'block_actor_spawn': ('BLOCK_ACTOR_SPAWN_TOGGLE_ENABLE_BUTTON', 'BLOCK_ACTOR_SPAWN_TOGGLE_DISABLE_BUTTON'),
            'allow_non_public_land': ('ALLOW_NON_PUBLIC_LAND_TOGGLE_ENABLE_BUTTON', 'ALLOW_NON_PUBLIC_LAND_TOGGLE_DISABLE_BUTTON'),
        }[setting_key]
        btn_text = self.language_manager.GetText(enable_key[0]) if not current else self.language_manager.GetText(enable_key[1])
        toggle_panel.add_button(
            btn_text,
            on_click=lambda p=player, l_id=land_id, key=setting_key, enable=not current, pg=from_page: self.op_toggle_land_setting(p, l_id, key, enable, pg)
        )
        player.send_form(toggle_panel)
    
    def op_toggle_land_setting(self, player: Player, land_id: int, setting_key: str, enable: bool, from_page: int):
        """OP 切换公共领地某项设置并返回设置面板"""
        setter_map = {
            'allow_public_interact': self.land_system.set_land_allow_public_interact,
            'allow_guild_member_interact': self.land_system.set_land_allow_guild_member_interact,
            'allow_explosion': self.land_system.set_land_allow_explosion,
            'allow_actor_interaction': self.land_system.set_land_allow_actor_interaction,
            'allow_frame': self.land_system.set_land_allow_frame,
            'allow_actor_damage': self.land_system.set_land_allow_actor_damage,
            'block_actor_spawn': self.land_system.set_land_block_actor_spawn,
            'allow_non_public_land': self.land_system.set_land_allow_non_public_land,
        }
        msg_map = {
            'allow_public_interact': ('LAND_PUBLIC_INTERACT_SETTING_UPDATED_ENABLE', 'LAND_PUBLIC_INTERACT_SETTING_UPDATED_DISABLE', 'LAND_PUBLIC_INTERACT_SETTING_FAILED'),
            'allow_guild_member_interact': ('LAND_GUILD_MEMBER_INTERACT_SETTING_UPDATED_ENABLE', 'LAND_GUILD_MEMBER_INTERACT_SETTING_UPDATED_DISABLE', 'LAND_GUILD_MEMBER_INTERACT_SETTING_FAILED'),
            'allow_explosion': ('LAND_EXPLOSION_SETTING_UPDATED_ENABLE', 'LAND_EXPLOSION_SETTING_UPDATED_DISABLE', 'LAND_EXPLOSION_SETTING_FAILED'),
            'allow_actor_interaction': ('LAND_ACTOR_INTERACTION_SETTING_UPDATED_ENABLE', 'LAND_ACTOR_INTERACTION_SETTING_UPDATED_DISABLE', 'LAND_ACTOR_INTERACTION_SETTING_FAILED'),
            'allow_frame': ('LAND_FRAME_SETTING_UPDATED_ENABLE', 'LAND_FRAME_SETTING_UPDATED_DISABLE', 'LAND_FRAME_SETTING_FAILED'),
            'allow_actor_damage': ('LAND_ACTOR_DAMAGE_SETTING_UPDATED_ENABLE', 'LAND_ACTOR_DAMAGE_SETTING_UPDATED_DISABLE', 'LAND_ACTOR_DAMAGE_SETTING_FAILED'),
            'block_actor_spawn': ('BLOCK_ACTOR_SPAWN_UPDATED_ENABLE', 'BLOCK_ACTOR_SPAWN_UPDATED_DISABLE', 'BLOCK_ACTOR_SPAWN_FAILED'),
            'allow_non_public_land': ('ALLOW_NON_PUBLIC_LAND_UPDATED_ENABLE', 'ALLOW_NON_PUBLIC_LAND_UPDATED_DISABLE', 'ALLOW_NON_PUBLIC_LAND_FAILED'),
        }
        msg_enable, msg_disable, msg_fail = msg_map[setting_key]
        try:
            success = setter_map[setting_key](land_id, enable)
            if success:
                player.send_message(self.language_manager.GetText(msg_enable if enable else msg_disable).format(land_id))
            else:
                player.send_message(self.language_manager.GetText(msg_fail))
            self.show_op_public_land_settings_panel(player, land_id, from_page)
        except Exception as e:
            self.logger.error(f"OP toggle land setting error: {str(e)}")
            self.report_arc_error(
                "OP12",
                f"op_toggle_land_setting exception land_id={land_id!r} setting_key={setting_key!r}",
                player,
                exception=e,
            )
            self.show_op_public_land_settings_panel(player, land_id, from_page)
    
    # Tool
    @staticmethod
    def get_player_position_vector(player: Player):
        """
        获取玩家所在方块的坐标
        使用 math.floor() 确保负坐标也能正确计算方块位置
        """
        return (math.floor(player.location.x), math.floor(player.location.y), math.floor(player.location.z))

    # API methods for other plugins
    def api_get_all_money_data(self) -> dict:
        money_data = {}
        for entry in self.economy.get_all_money_raw():
            try:
                player_name = self.get_player_name_by_xuid(entry['xuid'], return_with_title=False)
                if player_name:
                    money_data[player_name] = entry['money']
            except Exception:
                continue
        return money_data

    def api_get_player_money(self, player_name: str = "", xuid: str = "") -> float:
        """获取玩家金钱。player_name 与 xuid 填一个即可，xuid 优先。找不到返回 0.0。"""
        resolved = self._api_resolve_player_xuid(player_name, xuid)
        if not resolved:
            return 0.0
        return self.economy.get_player_money_by_xuid(resolved)

    # ------------------------------------------------------------------ 玩家活动统计 API（只读）
    def api_get_player_stat(
        self, stat_key: str, player_name: str = "", xuid: str = ""
    ) -> int:
        """查询玩家单项活动统计。stat_key 如 kill_total、kill:minecraft:zombie、break_total。"""
        resolved = self._api_resolve_player_xuid(player_name, xuid)
        if not resolved:
            return 0
        try:
            return int(self.activity_stats.get_stat(resolved, str(stat_key or "")))
        except Exception:
            return 0

    def api_get_player_stats(
        self, prefix: str = "", player_name: str = "", xuid: str = ""
    ) -> dict:
        """查询玩家活动统计字典。prefix 如 \"kill:\" / \"break:\" / \"place:\"；空则全部。"""
        resolved = self._api_resolve_player_xuid(player_name, xuid)
        if not resolved:
            return {}
        try:
            return dict(self.activity_stats.get_stats(resolved, str(prefix or "")))
        except Exception:
            return {}

    def api_get_player_kill_count(
        self, entity_id: str = "*", player_name: str = "", xuid: str = ""
    ) -> int:
        """击杀数。entity_id=\"*\" 为总击杀；否则为指定生物（含 minecraft:player）。"""
        resolved = self._api_resolve_player_xuid(player_name, xuid)
        if not resolved:
            return 0
        try:
            return int(self.activity_stats.get_kill_count(resolved, entity_id))
        except Exception:
            return 0

    def api_get_player_block_break_count(
        self, block_id: str = "*", player_name: str = "", xuid: str = ""
    ) -> int:
        """破坏方块数。block_id=\"*\" 为总数。"""
        resolved = self._api_resolve_player_xuid(player_name, xuid)
        if not resolved:
            return 0
        try:
            return int(self.activity_stats.get_block_break_count(resolved, block_id))
        except Exception:
            return 0

    def api_get_player_block_place_count(
        self, block_id: str = "*", player_name: str = "", xuid: str = ""
    ) -> int:
        """放置方块数。block_id=\"*\" 为总数。"""
        resolved = self._api_resolve_player_xuid(player_name, xuid)
        if not resolved:
            return 0
        try:
            return int(self.activity_stats.get_block_place_count(resolved, block_id))
        except Exception:
            return 0

    # ------------------------------------------------------------------ 侧边栏 API
    def api_sidebar_register_page(
        self,
        page_id: str,
        title: str,
        lines,
        owner: str = "",
        priority: int = 0,
        hide_line_if_missing: bool = True,
    ) -> bool:
        """注册侧边栏页面。lines 为行模板列表，可用 {key} 占位符。"""
        try:
            return bool(
                self.sidebar_system.register_page(
                    page_id,
                    title,
                    list(lines or []),
                    owner=owner,
                    priority=priority,
                    hide_line_if_missing=hide_line_if_missing,
                )
            )
        except Exception as e:
            try:
                if self.logger:
                    self.logger.error(f"[ARC Core]api_sidebar_register_page error: {e}")
            except Exception:
                pass
            return False

    def api_sidebar_unregister_page(self, page_id: str) -> bool:
        """注销侧边栏页面（不可注销核心主页面 arc_core_main）。"""
        try:
            return bool(self.sidebar_system.unregister_page(page_id))
        except Exception:
            return False

    def api_sidebar_set_page_lines(self, page_id: str, lines) -> bool:
        """更换已注册页面的行模板。"""
        try:
            return bool(self.sidebar_system.set_page_lines(page_id, list(lines or [])))
        except Exception:
            return False

    def api_sidebar_set_page_title(self, page_id: str, title: str) -> bool:
        """更换已注册页面的标题。"""
        try:
            return bool(self.sidebar_system.set_page_title(page_id, title))
        except Exception:
            return False

    def api_sidebar_set_value(
        self,
        page_id: str,
        key: str,
        value,
        player_name: str = "",
        xuid: str = "",
    ) -> bool:
        """设置页面键值。传 xuid/player_name 则为该玩家私有值，否则为全服全局值。"""
        try:
            resolved = ""
            if xuid or player_name:
                resolved = self._api_resolve_player_xuid(player_name, xuid) or ""
                if not resolved:
                    return False
            return bool(
                self.sidebar_system.set_value(page_id, key, value, xuid=resolved)
            )
        except Exception:
            return False

    def api_sidebar_set_values(
        self,
        page_id: str,
        values: dict,
        player_name: str = "",
        xuid: str = "",
    ) -> bool:
        """批量设置页面键值。传 xuid/player_name 则为该玩家私有值。"""
        try:
            resolved = ""
            if xuid or player_name:
                resolved = self._api_resolve_player_xuid(player_name, xuid) or ""
                if not resolved:
                    return False
            return bool(
                self.sidebar_system.set_values(page_id, dict(values or {}), xuid=resolved)
            )
        except Exception:
            return False

    def api_sidebar_get_value(
        self,
        page_id: str,
        key: str,
        player_name: str = "",
        xuid: str = "",
        default=None,
    ):
        """读取页面键值（玩家私有优先，其次全局）。"""
        try:
            resolved = ""
            if xuid or player_name:
                resolved = self._api_resolve_player_xuid(player_name, xuid) or ""
            return self.sidebar_system.get_value(
                page_id, key, xuid=resolved, default=default
            )
        except Exception:
            return default

    def api_sidebar_clear_values(
        self, page_id: str, player_name: str = "", xuid: str = ""
    ) -> bool:
        """清除页面键值。传玩家则只清该玩家私有值。"""
        try:
            resolved = ""
            if xuid or player_name:
                resolved = self._api_resolve_player_xuid(player_name, xuid) or ""
                if not resolved:
                    return False
            return bool(self.sidebar_system.clear_values(page_id, xuid=resolved))
        except Exception:
            return False

    def api_sidebar_set_global_value(self, page_id: str, key: str, value) -> bool:
        """设置页面对全服生效的全局键值。"""
        try:
            return bool(self.sidebar_system.set_global_value(page_id, key, value))
        except Exception:
            return False

    def api_sidebar_set_page_visible(
        self,
        page_id: str,
        visible: bool,
        player_name: str = "",
        xuid: str = "",
    ) -> bool:
        """按玩家显隐某个已注册页面（主页面不可隐藏）。"""
        try:
            resolved = self._api_resolve_player_xuid(player_name, xuid)
            if not resolved:
                return False
            return bool(
                self.sidebar_system.set_page_visible(page_id, visible, resolved)
            )
        except Exception:
            return False

    def api_sidebar_refresh(self, player_name: str = "", xuid: str = "") -> None:
        """立即重绘侧边栏。不传玩家则刷新所有在线玩家。"""
        try:
            resolved = ""
            if xuid or player_name:
                resolved = self._api_resolve_player_xuid(player_name, xuid) or ""
                if not resolved:
                    return
            self.sidebar_system.refresh(xuid=resolved)
        except Exception:
            pass

    def api_sidebar_list_pages(self) -> list:
        """列出已注册侧边栏页面。"""
        try:
            return list(self.sidebar_system.list_pages())
        except Exception:
            return []

    def api_get_richest_player_money_data(self) -> list:
        result = self.economy.get_richest_one()
        if result:
            player_name = self.get_player_name_by_xuid(result['xuid'], return_with_title=False)
            if player_name:
                return [player_name, self._round_money(result['money'])]
        return ["", 0.0]

    def api_get_poorest_player_money_data(self) -> list:
        result = self.economy.get_poorest_one()
        if result:
            player_name = self.get_player_name_by_xuid(result['xuid'], return_with_title=False)
            if player_name:
                return [player_name, self._round_money(result['money'])]
        return ["", 0.0]

    def api_change_player_money(
        self,
        player_name: str = "",
        money_to_change: float = 0,
        xuid: str = "",
        notify: bool = True,
    ) -> bool:
        """增减玩家金钱。player_name 与 xuid 填一个即可（xuid 优先）。四舍五入到分为 0 时返回 False。"""
        result = self.api_adjust_player_money(
            money_to_change, player_name=player_name, xuid=xuid, notify=notify
        )
        return bool(result.get("ok"))

    def api_adjust_player_money(
        self,
        delta: float,
        player_name: str = "",
        xuid: str = "",
        notify: bool = True,
    ) -> dict:
        """增减玩家金钱，返回变动后余额。delta 四舍五入到分后为 0 视为无效。"""
        result = {
            "ok": False,
            "error": None,
            "xuid": "",
            "money": 0.0,
            "delta": 0.0,
        }
        try:
            dlt = self._round_money(delta)
        except (TypeError, ValueError):
            result["error"] = "MONEY_INVALID_AMOUNT"
            return result
        result["delta"] = dlt
        if dlt == 0:
            result["error"] = "MONEY_INVALID_AMOUNT"
            return result
        resolved = self._api_resolve_player_xuid(player_name, xuid)
        if not resolved:
            result["error"] = "PLAYER_NOT_FOUND"
            return result
        result["xuid"] = resolved
        try:
            ok = self.economy.change_player_money_by_xuid(resolved, dlt)
            result["money"] = self.economy.get_player_money_by_xuid(resolved)
            if not ok:
                result["error"] = "MONEY_DB_ERROR"
                return result
            if notify:
                online = self._find_online_player_by_xuid(resolved)
                if online is not None:
                    hint_key = "MONEY_ADD_HINT" if dlt > 0 else "MONEY_REDUCE_HINT"
                    online.send_message(
                        self.language_manager.GetText(hint_key).format(
                            self._format_money_display(abs(dlt)),
                            self._format_money_display(result["money"]),
                        )
                    )
            try:
                self._update_richest_title_if_needed()
            except Exception:
                pass
            result["ok"] = True
            return result
        except Exception as e:
            try:
                if self.logger:
                    self.logger.error(f"[ARC Core]api_adjust_player_money error: {e}")
            except Exception:
                pass
            result["error"] = "MONEY_DB_ERROR"
            return result

    def api_get_player_money_rank(self, player_name: str = "", xuid: str = "") -> int:
        """财富排名，从 1 开始；找不到返回 0。"""
        resolved = self._api_resolve_player_xuid(player_name, xuid)
        if not resolved:
            return 0
        rank = self.economy.get_player_money_rank_by_xuid(resolved)
        try:
            return int(rank or 0)
        except (TypeError, ValueError):
            return 0

    def api_get_player_name_by_xuid(self, xuid: str, with_title: bool = False) -> str:
        """按 xuid 取玩家名。with_title=True 时带公会/头衔展示名；找不到返回空字符串。"""
        name = self.get_player_name_by_xuid(str(xuid or "").strip(), return_with_title=bool(with_title))
        return str(name) if name else ""

    def api_get_equipped_title(self, player_name: str = "", xuid: str = "") -> str:
        """当前佩戴头衔；未佩戴或找不到玩家返回空字符串。"""
        resolved = self._api_resolve_player_xuid(player_name, xuid)
        if not resolved:
            return ""
        title = self.title_system.get_equipped_title_by_xuid(resolved)
        return str(title).strip() if title else ""

    def api_list_unlocked_titles(self, player_name: str = "", xuid: str = "") -> list:
        """已解锁头衔名列表。"""
        resolved = self._api_resolve_player_xuid(player_name, xuid)
        if not resolved:
            return []
        return list(self.title_system.get_unlocked_titles_by_xuid(resolved))

    def api_get_player_lands(self, player_name: str = "", xuid: str = "") -> list:
        """玩家名下私人领地列表（含 land_id）。"""
        resolved = self._api_resolve_player_xuid(player_name, xuid)
        if not resolved:
            return []
        lands = self.land_system.get_player_lands(resolved) or {}
        out = []
        for lid, info in lands.items():
            item = dict(info or {})
            item["land_id"] = int(lid)
            out.append(item)
        return out

    def api_get_guild_lands(self, guild_id: int) -> list:
        """公会名下领地列表（含 land_id）。"""
        try:
            gid = int(guild_id)
        except (TypeError, ValueError):
            return []
        lands = self.land_system.get_guild_lands(gid) or {}
        out = []
        for lid, info in lands.items():
            item = dict(info or {})
            item["land_id"] = int(lid)
            out.append(item)
        return out

    def api_check_land_access(
        self,
        dimension: str,
        position: tuple,
        player_name: str = "",
        xuid: str = "",
        action: str = "build",
    ) -> dict:
        """静默检查玩家在该点的建造或交互权限（不向玩家发提示）。"""
        result = {
            "allowed": False,
            "land_id": None,
            "sub_land_id": None,
            "is_public": False,
            "wilderness": False,
            "action": str(action or "build"),
        }
        resolved = self._api_resolve_player_xuid(player_name, xuid)
        if not resolved:
            return result
        try:
            from endstone_arc_core.dimension_utils import normalize_dimension_id

            if not position or len(position) < 3:
                return result
            dim = normalize_dimension_id(dimension)
            x = math.floor(float(position[0]))
            y = math.floor(float(position[1]))
            z = math.floor(float(position[2]))
            land_id = self.get_land_at_pos(dim, x, z, y)
            if land_id is None:
                result["allowed"] = True
                result["wilderness"] = True
                return result
            result["land_id"] = int(land_id)
            result["is_public"] = bool(self.is_public_land(land_id))
            sub_id = self.get_sub_land_at_pos(land_id, x, y, z)
            if sub_id is not None:
                result["sub_land_id"] = int(sub_id)
            is_op = self._api_player_is_op(resolved)
            want_interact = str(action or "build").strip().lower() == "interact"
            if result["sub_land_id"] is not None:
                sub_info = self.get_sub_land_info(result["sub_land_id"])
                if sub_info and self._xuid_has_sub_land_access(resolved, sub_info):
                    result["allowed"] = True
                    return result
            land_info = self.get_land_info(land_id) or {}
            if not land_info:
                result["allowed"] = True
                return result
            if self._xuid_has_land_build_access(resolved, land_info, is_op):
                result["allowed"] = True
                return result
            if want_interact and self._xuid_has_land_interact_access(resolved, land_info, is_op):
                result["allowed"] = True
            return result
        except Exception:
            return result

    def _api_player_is_op(self, xuid: str) -> bool:
        online = self._find_online_player_by_xuid(xuid)
        if online is not None:
            try:
                return bool(online.is_op)
            except Exception:
                pass
        try:
            row = self.database_manager.query_one(
                "SELECT is_op FROM player_local_info WHERE xuid = ?",
                (str(xuid),),
            )
            if not row:
                return False
            return bool(int(row.get("is_op") or 0))
        except Exception:
            return False

    def _xuid_matches_land_owner_key(self, xuid: str, owner_key: str) -> bool:
        xu = str(xuid)
        ok = str(owner_key or "").strip()
        px = LandSystem.parse_land_owner_player_xuid(ok)
        if px is not None and px == xu:
            return True
        if ok == xu:
            return True
        gid = LandSystem.parse_land_owner_guild_id(ok)
        if gid is not None and getattr(self, "guild_system", None):
            mem = self.guild_system.get_membership(xu)
            if mem and int(mem.get("guild_id") or 0) == gid:
                return True
        return False

    def _xuid_in_shared_users(self, xuid: str, owner_key: str, shared_users) -> bool:
        xu = str(xuid)
        seq = shared_users or []
        if not any(str(u) == xu for u in seq):
            return False
        guild_id = LandSystem.parse_land_owner_guild_id(str(owner_key or ""))
        if guild_id is None:
            return True
        if not getattr(self, "guild_system", None):
            return False
        mem = self.guild_system.get_membership(xu)
        return bool(mem and int(mem.get("guild_id") or 0) == int(guild_id))

    def _xuid_has_sub_land_access(self, xuid: str, sub_info: dict) -> bool:
        owner_key = str(sub_info.get("owner_xuid") or "")
        shared = sub_info.get("shared_users") or []
        return self._xuid_matches_land_owner_key(xuid, owner_key) or self._xuid_in_shared_users(
            xuid, owner_key, shared
        )

    def _xuid_has_land_build_access(self, xuid: str, land_info: dict, is_op: bool) -> bool:
        owner_key = str(land_info.get("owner_xuid") or "")
        if self.land_system.is_public_land_owner(owner_key):
            return bool(is_op)
        shared = land_info.get("shared_users") or []
        return self._xuid_matches_land_owner_key(xuid, owner_key) or self._xuid_in_shared_users(
            xuid, owner_key, shared
        )

    def _xuid_has_land_interact_access(self, xuid: str, land_info: dict, is_op: bool) -> bool:
        if self._xuid_has_land_build_access(xuid, land_info, is_op):
            return True
        owner_key = str(land_info.get("owner_xuid") or "")
        if land_info.get("allow_public_interact", False):
            if LandSystem.parse_land_owner_guild_id(owner_key) is None:
                return True
        if not land_info.get("allow_guild_member_interact"):
            return False
        owner_px = LandSystem.parse_land_owner_player_xuid(owner_key)
        if not owner_px or not getattr(self, "guild_system", None):
            return False
        pmem = self.guild_system.get_membership(str(xuid))
        omem = self.guild_system.get_membership(owner_px)
        if not pmem or not omem:
            return False
        g1 = int(pmem.get("guild_id") or 0)
        g2 = int(omem.get("guild_id") or 0)
        return g1 > 0 and g1 == g2

    def api_teleport_player_to(
        self,
        dimension: str,
        x: float,
        y: float,
        z: float,
        player_name: str = "",
        xuid: str = "",
    ) -> dict:
        """将在线玩家传送到指定坐标。离线或不存在返回失败。"""
        result = {"ok": False, "error": None}
        resolved = self._api_resolve_player_xuid(player_name, xuid)
        player = self._find_online_player_by_xuid(resolved) if resolved else None
        if player is None and str(player_name or "").strip():
            try:
                player = self.server.get_player(str(player_name).strip())
            except Exception:
                player = None
        if player is None:
            result["error"] = "PLAYER_NOT_ONLINE"
            return result
        try:
            from endstone_arc_core.dimension_utils import normalize_dimension_id

            dim = normalize_dimension_id(dimension)
            cmd = generate_tp_command_to_position(
                player.name, (x, y, z), dim or "overworld"
            )
            self.server.dispatch_command(self.server.command_sender, cmd)
            result["ok"] = True
            return result
        except Exception as e:
            try:
                if self.logger:
                    self.logger.error(f"[ARC Core]api_teleport_player_to error: {e}")
            except Exception:
                pass
            result["error"] = "TELEPORT_FAILED"
            return result

    def api_list_player_homes(self, player_name: str = "", xuid: str = "") -> list:
        """玩家 Home 列表。"""
        resolved = self._api_resolve_player_xuid(player_name, xuid)
        if not resolved:
            return []
        homes = self.teleport_system.get_player_homes(resolved) or {}
        out = []
        for name, row in homes.items():
            out.append({
                "home_name": str(name),
                "dimension": str(row.get("dimension") or ""),
                "x": row.get("x"),
                "y": row.get("y"),
                "z": row.get("z"),
            })
        return out

    def api_list_public_warps(self) -> list:
        """公共传送点列表。"""
        warps = self.teleport_system.get_all_public_warps() or {}
        out = []
        for name, row in warps.items():
            out.append({
                "warp_name": str(name),
                "dimension": str(row.get("dimension") or ""),
                "x": row.get("x"),
                "y": row.get("y"),
                "z": row.get("z"),
            })
        return out

    def api_teleport_player_to_home(
        self,
        home_name: str,
        player_name: str = "",
        xuid: str = "",
    ) -> dict:
        """将在线玩家传送到其指定 Home。"""
        result = {"ok": False, "error": None}
        name = str(home_name or "").strip()
        if not name:
            result["error"] = "HOME_NOT_FOUND"
            return result
        resolved = self._api_resolve_player_xuid(player_name, xuid)
        if not resolved:
            result["error"] = "PLAYER_NOT_FOUND"
            return result
        home = self.teleport_system.get_player_home(resolved, name)
        if not home:
            result["error"] = "HOME_NOT_FOUND"
            return result
        return self.api_teleport_player_to(
            str(home.get("dimension") or ""),
            home.get("x"),
            home.get("y"),
            home.get("z"),
            xuid=resolved,
        )

    def api_teleport_player_to_warp(
        self,
        warp_name: str,
        player_name: str = "",
        xuid: str = "",
    ) -> dict:
        """将在线玩家传送到指定公共传送点。"""
        result = {"ok": False, "error": None}
        name = str(warp_name or "").strip()
        if not name:
            result["error"] = "WARP_NOT_FOUND"
            return result
        warp = self.teleport_system.get_public_warp(name)
        if not warp:
            result["error"] = "WARP_NOT_FOUND"
            return result
        return self.api_teleport_player_to(
            str(warp.get("dimension") or ""),
            warp.get("x"),
            warp.get("y"),
            warp.get("z"),
            player_name=player_name,
            xuid=xuid,
        )

    def api_if_position_in_land(self, dimension: str, position: tuple) -> Optional[int]:
        """
        判断位置是否在领地内（多层生效领地）。

        维度字符串会经 normalize_dimension_id 规范化（如 Overworld → minecraft:overworld；
        自定义维度原样保留）。坐标按三维 AABB 判定（含 Y）。

        多层规则：私人/公会 > 公共(public_priority 3>2>1)；同点返回生效领地 ID。
        不在任何领地内返回 None。
        """
        try:
            from endstone_arc_core.dimension_utils import normalize_dimension_id

            if not position or len(position) < 3:
                return None
            dim = normalize_dimension_id(dimension)
            x = math.floor(float(position[0]))
            y = math.floor(float(position[1]))
            z = math.floor(float(position[2]))
            return self.get_land_at_pos(dim, x, z, y)
        except Exception:
            return None

    def api_list_lands_at_position(self, dimension: str, position: tuple) -> list:
        """
        列出覆盖该点的全部主领地（含被更高层覆盖的下层），按生效优先级降序。

        每项为 get_land_info 风格字典，并额外含 land_id。
        维度会规范化；坐标含 Y。无效输入返回 []。
        """
        try:
            from endstone_arc_core.dimension_utils import normalize_dimension_id

            if not position or len(position) < 3:
                return []
            dim = normalize_dimension_id(dimension)
            x = math.floor(float(position[0]))
            y = math.floor(float(position[1]))
            z = math.floor(float(position[2]))
            return self.land_system.list_lands_at_pos(dim, x, z, y)
        except Exception:
            return []

    def api_resolve_land_at_position(self, dimension: str, position: tuple) -> dict:
        """
        解析坐标处的生效领地与子领地（多层 + 规范化维度）。

        返回 dict（不在任何领地时 land_id 为 None）::
            {
              'dimension': str,              # 规范化后的维度 ID
              'land_id': int | None,         # 生效主领地
              'sub_land_id': int | None,     # 生效主领地内的子领地（若有）
              'is_public': bool,
              'public_priority': int | None, # 仅公共领地有值
              'owner_xuid': str,
              'covering_land_ids': list[int] # 覆盖该点的全部主领地 ID（优先级降序）
            }
        """
        empty = {
            "dimension": "",
            "land_id": None,
            "sub_land_id": None,
            "is_public": False,
            "public_priority": None,
            "owner_xuid": "",
            "covering_land_ids": [],
        }
        try:
            from endstone_arc_core.dimension_utils import normalize_dimension_id

            if not position or len(position) < 3:
                return empty
            dim = normalize_dimension_id(dimension)
            x = math.floor(float(position[0]))
            y = math.floor(float(position[1]))
            z = math.floor(float(position[2]))
            covering = self.land_system.list_lands_at_pos(dim, x, z, y)
            covering_ids = [int(r["land_id"]) for r in covering if r.get("land_id") is not None]
            land_id = covering_ids[0] if covering_ids else self.get_land_at_pos(dim, x, z, y)
            if land_id is None:
                empty["dimension"] = dim
                return empty
            info = self.get_land_info(land_id) or {}
            is_public = self.is_public_land(land_id)
            sub_id = None
            if not is_public:
                sub_id = self.get_sub_land_at_pos(land_id, x, y, z)
            return {
                "dimension": dim,
                "land_id": int(land_id),
                "sub_land_id": int(sub_id) if sub_id is not None else None,
                "is_public": bool(is_public),
                "public_priority": (
                    LandSystem.clamp_public_priority(info.get("public_priority", 1))
                    if is_public
                    else None
                ),
                "owner_xuid": str(info.get("owner_xuid") or ""),
                "covering_land_ids": covering_ids,
            }
        except Exception:
            return empty

    def api_sky_eye_query(
        self,
        player_name: str = "",
        xuid: str = "",
        action: str = "",
        minutes: int = 30,
        time_from: str = "",
        time_to: str = "",
        dimension: str = "",
        x: Any = None,
        y: Any = None,
        z: Any = None,
        radius: Any = 8,
        combat_role: str = "",
        in_land: Optional[bool] = None,
        name_fuzzy: bool = True,
        limit: int = 40,
    ) -> list:
        """查询天眼 SQLite。返回事件字典列表（新→旧）。ENABLE_SKY_EYE 关闭时仍可读历史。

        action 支持精确名（PlayerDeath）或类别别名（death/pvp/死亡/打怪 等）。
        player_name 默认模糊匹配（子串，忽略大小写）；传 name_fuzzy=False 可改精确。
        可不传玩家名，仅按 action / 时间 / 坐标筛选（如「最近谁死了」）。
        """
        try:
            from endstone_arc_core.dimension_utils import normalize_dimension_id

            dim = normalize_dimension_id(dimension) if str(dimension or "").strip() else ""
            px = None if x in (None, "") else float(x)
            py = None if y in (None, "") else float(y)
            pz = None if z in (None, "") else float(z)
            rad = None if radius in (None, "") else float(radius)
            fuzzy_raw = name_fuzzy
            if isinstance(fuzzy_raw, str):
                fuzzy = fuzzy_raw.strip().lower() not in ("0", "false", "no", "exact", "off")
            else:
                fuzzy = bool(fuzzy_raw)
            return self.sky_eye_store.query(
                player_name=str(player_name or "").strip(),
                player_xuid=str(xuid or "").strip(),
                action=str(action or "").strip(),
                minutes=int(minutes) if minutes not in (None, "") else 30,
                time_from=str(time_from or ""),
                time_to=str(time_to or ""),
                dimension=dim,
                x=px,
                y=py,
                z=pz,
                radius=rad,
                combat_role=str(combat_role or ""),
                in_land=in_land,
                name_fuzzy=fuzzy,
                limit=int(limit or 40),
            )
        except Exception:
            return []

    def api_sky_eye_query_text(self, **kwargs) -> str:
        """天眼查询的纯文本，供弧光天星 / AstrBot 工具直接阅读。"""
        heading = str(kwargs.pop("heading", "") or "")
        name_hint = str(kwargs.get("player_name") or "")
        records = self.api_sky_eye_query(**kwargs)
        return format_sky_eye_records(records, heading=heading, name_hint=name_hint)

    def api_sky_eye_player_now(self, player_name: str = "", xuid: str = "") -> dict:
        """在线则返回实时坐标与是否在领地；离线则返回最近一条天眼记录。

        玩家名支持模糊：在线列表先精确再子串匹配；离线走天眼模糊查询。
        """
        result = {
            "online": False,
            "player_name": str(player_name or "").strip(),
            "xuid": str(xuid or "").strip(),
            "dimension": "",
            "x": None,
            "y": None,
            "z": None,
            "in_land": False,
            "land_id": None,
            "land_name": "",
            "land_owner": "",
            "source": "",
            "name_matches": [],
        }
        resolved_name = result["player_name"]
        player = None
        try:
            if result["xuid"]:
                for online in list(self.server.online_players or []):
                    if str(getattr(online, "xuid", "") or "") == result["xuid"]:
                        player = online
                        break
            if player is None and resolved_name:
                player = self.server.get_player(resolved_name)
                if player is None:
                    lowered = resolved_name.lower()
                    fuzzy_hits = []
                    for online in list(self.server.online_players or []):
                        oname = str(getattr(online, "name", "") or "")
                        if oname.lower() == lowered:
                            player = online
                            break
                        if lowered in oname.lower():
                            fuzzy_hits.append(online)
                    if player is None and len(fuzzy_hits) == 1:
                        player = fuzzy_hits[0]
                    elif player is None and fuzzy_hits:
                        result["name_matches"] = [
                            str(getattr(p, "name", "") or "") for p in fuzzy_hits
                        ]
                        result["source"] = "ambiguous_online"
                        return result
        except Exception:
            player = None
        if player is not None:
            loc = getattr(player, "location", None)
            result["online"] = True
            result["player_name"] = str(getattr(player, "name", "") or resolved_name)
            result["xuid"] = str(getattr(player, "xuid", "") or result["xuid"])
            result["source"] = "online"
            if loc is not None:
                dim = get_dimension_id(loc.dimension) if getattr(loc, "dimension", None) is not None else ""
                result["dimension"] = dim
                result["x"] = float(loc.x)
                result["y"] = float(loc.y)
                result["z"] = float(loc.z)
                land = self._sky_eye_land_snapshot(dim, loc.x, loc.y, loc.z)
                result.update(land)
            return result
        records = self.api_sky_eye_query(
            player_name=resolved_name,
            xuid=result["xuid"],
            minutes=24 * 60,
            name_fuzzy=True,
            limit=1,
        )
        if not records and resolved_name:
            try:
                matches = self.sky_eye_store.distinct_player_names(
                    hint=resolved_name, minutes=24 * 60, limit=10
                )
            except Exception:
                matches = []
            if matches:
                result["name_matches"] = matches
                result["source"] = "name_suggestions"
                return result
        if not records:
            result["source"] = "none"
            return result
        item = records[0]
        result["player_name"] = item.get("player_name") or resolved_name
        result["xuid"] = item.get("player_xuid") or result["xuid"]
        result["dimension"] = item.get("dimension") or ""
        result["x"] = item.get("x")
        result["y"] = item.get("y")
        result["z"] = item.get("z")
        result["in_land"] = bool(item.get("in_land"))
        result["land_id"] = item.get("land_id")
        result["land_name"] = item.get("land_name") or ""
        result["land_owner"] = item.get("land_owner") or ""
        result["source"] = "sky_eye"
        result["last_action"] = item.get("action")
        result["last_ts"] = item.get("ts")
        return result

    def api_get_land_info(self, land_id: int) -> dict:
        """
        获取领地信息
        :return: 领地信息字典 {
            'land_name': 领地名称,
            'dimension': 维度（规范 ID，如 minecraft:overworld）,
            'min_x': 最小X坐标,
            'max_x': 最大X坐标,
            'min_y': 最小Y坐标,
            'max_y': 最大Y坐标,
            'min_z': 最小Z坐标,
            'max_z': 最大Z坐标,
            'tp_x': 传送点X坐标,
            'tp_y': 传送点Y坐标,
            'tp_z': 传送点Z坐标,
            'shared_users': 共享玩家XUID列表,
            'owner_xuid': 拥有者键：Player_<xuid>、GUILD_<公会id> 或 PUBLIC（公共）,
            'allow_guild_member_interact': 与 Player_ 主人同公会成员是否允许方块交互（bool）,
            'for_sale': 是否上架出售（bool，私人领地）,
            'sale_price': 上架价格（float，未上架为 0）,
            'public_priority': 公共领地优先级 1/2/3（3 最高；非公共亦可能有默认值）,
            'block_actor_spawn': 该公共领地是否开启拦截生物生成（bool；开启后按全局黑/白名单生效）,
            ... 其它 allow_* 开关
        } 不存在则返回空字典
        """
        return self.get_land_info(land_id)

    # ─── 公会 API ─────────────────────────────────────────────────────────
    def api_get_player_guild_info(self, player_name: str = "", xuid: str = "") -> dict:
        """
        获取玩家当前公会信息（含规模、容量、公私贡献点）。
        player_name 与 xuid 填一个即可，xuid 优先。
        玩家不存在或未加入公会时返回空字典 {}。
        """
        try:
            resolved = self._api_resolve_player_xuid(player_name, xuid)
            if not resolved:
                return {}
            mem = self.guild_system.get_membership(resolved)
            if not mem:
                return {}
            gid = int(mem.get("guild_id") or 0)
            if gid <= 0:
                return {}
            g = self.guild_system.get_guild(gid)
            if not g:
                return {}
            info = self._guild_public_info_dict(gid, g)
            if not info:
                return {}
            info["role"] = str(mem.get("role") or "")
            info["personal_contribution"] = int(self.guild_system.get_member_contribution(resolved))
            return info
        except Exception as e:
            try:
                if self.logger:
                    self.logger.error(f"[ARC Core]api_get_player_guild_info error: {e}")
            except Exception:
                pass
            return {}

    def api_add_guild_contribution(self, player_name: str = "", points: int = 0, xuid: str = "") -> dict:
        """
        给玩家增加公会贡献点：
            - 玩家私人公会贡献点 += points
            - 玩家所属公会的公共贡献点 += points
        :param player_name: 玩家名称
        :param points: 增加的点数（必须为正整数；非正数返回 ok=False）
        :return: dict
            {
              'ok': bool,
              'error': Optional[str],          # 失败时为错误码（如 'GUILD_NOT_IN_GUILD'）
              'personal_contribution': int,    # 增加后的玩家私人贡献点
              'guild_total_contribution': int, # 增加后的公会公共贡献点
              'guild_id': int                  # 所在公会 id；玩家无公会时为 0
            }
        说明：玩家退出/被踢/公会解散时该玩家私人贡献点清零（删除成员行）；
              公会公共贡献点不会因成员退出而减少。
        """
        result = {
            "ok": False,
            "error": None,
            "personal_contribution": 0,
            "guild_total_contribution": 0,
            "guild_id": 0,
        }
        try:
            resolved = self._api_resolve_player_xuid(player_name, xuid)
            if not resolved:
                result["error"] = "GUILD_INVALID_PLAYER"
                return result
            ok, err, info = self.guild_system.add_contribution_by_xuid(resolved, points)
            result["ok"] = bool(ok)
            result["error"] = err
            result["personal_contribution"] = int(info.get("personal", 0))
            result["guild_total_contribution"] = int(info.get("guild_total", 0))
            result["guild_id"] = int(info.get("guild_id", 0))
            if ok:
                online = self._find_online_player_by_xuid(resolved)
                if online is not None:
                    try:
                        msg_template = self.language_manager.GetText("GUILD_CONTRIB_ADDED_HINT")
                        if not msg_template:
                            msg_template = "[弧光核心]获得公会贡献点 +{0}（我的：{1}，公会：{2}）。"
                        online.send_message(
                            msg_template.format(
                                int(points),
                                int(info.get("personal", 0)),
                                int(info.get("guild_total", 0)),
                            )
                        )
                    except Exception:
                        pass
            return result
        except Exception as e:
            try:
                if self.logger:
                    self.logger.error(f"[ARC Core]api_add_guild_contribution error: {e}")
            except Exception:
                pass
            result["error"] = "GUILD_DB_ERROR"
            return result

    def api_get_player_guild_contribution(self, player_name: str = "", xuid: str = "") -> int:
        """
        获取玩家当前的私人公会贡献点。
        玩家未加入公会或不存在时返回 0。player_name 与 xuid 填一个即可。
        """
        try:
            resolved = self._api_resolve_player_xuid(player_name, xuid)
            if not resolved:
                return 0
            return int(self.guild_system.get_member_contribution(resolved))
        except Exception:
            return 0

    def api_get_guild_total_contribution_by_player(self, player_name: str = "", xuid: str = "") -> int:
        """
        获取玩家所在公会的公共贡献点。玩家未加入公会时返回 0。
        """
        try:
            resolved = self._api_resolve_player_xuid(player_name, xuid)
            if not resolved:
                return 0
            mem = self.guild_system.get_membership(resolved)
            if not mem:
                return 0
            gid = int(mem.get("guild_id") or 0)
            if gid <= 0:
                return 0
            return int(self.guild_system.get_guild_total_contribution(gid))
        except Exception:
            return 0

    def api_set_guild_size_tier(self, guild_name: str, tier: str) -> bool:
        """
        设置公会规模等级（'small' / 'medium' / 'large'）。
        若目标规模上限低于当前成员数则拒绝（返回 False），需先减少成员后再降级。
        """
        try:
            n = (str(guild_name).strip() if guild_name else "")
            if not n:
                return False
            g = self.guild_system.get_guild_by_name(n)
            if not g:
                return False
            gid = int(g.get("id") or 0)
            if gid <= 0:
                return False
            target_tier = self.guild_system.normalize_size_tier(tier)
            cap = self.guild_system.get_size_tier_max(target_tier)
            cur = self.guild_system.count_members(gid)
            if cur > cap:
                return False
            ok, _err = self.guild_system.set_size_tier(gid, target_tier)
            return bool(ok)
        except Exception:
            return False

    def _api_resolve_player_xuid(self, player_name: str = "", xuid: str = "") -> Optional[str]:
        xuid_s = str(xuid or "").strip()
        if xuid_s:
            return xuid_s
        name = str(player_name or "").strip()
        if not name:
            return None
        return self.get_player_xuid_by_name(name)

    def _guild_public_info_dict(self, guild_id: int, g: Optional[dict] = None) -> dict:
        try:
            gid = int(guild_id)
        except (TypeError, ValueError):
            return {}
        if gid <= 0:
            return {}
        if g is None:
            g = self.guild_system.get_guild(gid)
        if not g:
            return {}
        tier = self.guild_system.normalize_size_tier(g.get("size_tier"))
        jra = g.get("join_requires_approval")
        try:
            join_requires_approval = int(jra) != 0 if jra is not None else True
        except (TypeError, ValueError):
            join_requires_approval = True
        return {
            "guild_id": gid,
            "name": str(g.get("name") or ""),
            "size_tier": tier,
            "capacity": int(self.guild_system.get_size_tier_max(tier)),
            "member_count": int(self.guild_system.count_members(gid)),
            "total_contribution": int(g.get("total_contribution") or 0),
            "motto": str(g.get("motto") or ""),
            "owner_xuid": str(g.get("owner_xuid") or ""),
            "join_requires_approval": bool(join_requires_approval),
        }

    def api_get_player_guild_id(self, player_name: str = "", xuid: str = "") -> int:
        """获取玩家当前公会 id。未加入或不存在时返回 0。player_name 与 xuid 填一个即可，xuid 优先。"""
        try:
            resolved = self._api_resolve_player_xuid(player_name, xuid)
            if not resolved:
                return 0
            mem = self.guild_system.get_membership(resolved)
            if not mem:
                return 0
            return int(mem.get("guild_id") or 0)
        except Exception:
            return 0

    def api_get_guild_info(self, guild_id: int) -> dict:
        """按公会 id 获取公会信息。不存在时返回 {}。"""
        try:
            return self._guild_public_info_dict(guild_id)
        except Exception as e:
            try:
                if self.logger:
                    self.logger.error(f"[ARC Core]api_get_guild_info error: {e}")
            except Exception:
                pass
            return {}

    def api_get_guild_total_contribution(self, guild_id: int) -> int:
        """按公会 id 获取公共贡献点。公会不存在时返回 0。"""
        try:
            return int(self.guild_system.get_guild_total_contribution(guild_id))
        except Exception:
            return 0

    def api_change_guild_total_contribution(self, guild_id: int, delta: int) -> dict:
        """增减公会公共贡献点（可正可负，结果不得低于 0）。不影响成员私人贡献。"""
        result = {
            "ok": False,
            "error": None,
            "guild_id": 0,
            "total_contribution": 0,
            "delta": 0,
        }
        try:
            result["delta"] = int(delta)
        except (TypeError, ValueError):
            result["error"] = "GUILD_CONTRIB_INVALID_POINTS"
            return result
        try:
            gid = int(guild_id)
        except (TypeError, ValueError):
            result["error"] = "GUILD_NOT_FOUND"
            return result
        result["guild_id"] = gid
        try:
            ok, err, total = self.guild_system.change_guild_total_contribution(gid, result["delta"])
            result["ok"] = bool(ok)
            result["error"] = err
            result["total_contribution"] = int(total)
            return result
        except Exception as e:
            try:
                if self.logger:
                    self.logger.error(f"[ARC Core]api_change_guild_total_contribution error: {e}")
            except Exception:
                pass
            result["error"] = "GUILD_DB_ERROR"
            return result

    def api_get_member_guild_contribution(
        self, guild_id: int, player_name: str = "", xuid: str = ""
    ) -> int:
        """按公会 id + 玩家（名或 xuid）获取该成员私人贡献点。非该会成员返回 0。"""
        try:
            resolved = self._api_resolve_player_xuid(player_name, xuid)
            if not resolved:
                return 0
            return int(self.guild_system.get_member_contribution_in_guild(guild_id, resolved))
        except Exception:
            return 0

    def api_change_member_guild_contribution(
        self, guild_id: int, delta: int, player_name: str = "", xuid: str = ""
    ) -> dict:
        """增减指定公会内该成员的私人贡献点（可正可负，结果不得低于 0）。不影响公共池。"""
        result = {
            "ok": False,
            "error": None,
            "guild_id": 0,
            "personal_contribution": 0,
            "delta": 0,
        }
        try:
            result["delta"] = int(delta)
        except (TypeError, ValueError):
            result["error"] = "GUILD_CONTRIB_INVALID_POINTS"
            return result
        try:
            gid = int(guild_id)
        except (TypeError, ValueError):
            result["error"] = "GUILD_NOT_FOUND"
            return result
        result["guild_id"] = gid
        try:
            resolved = self._api_resolve_player_xuid(player_name, xuid)
            if not resolved:
                result["error"] = "GUILD_INVALID_PLAYER"
                return result
            ok, err, personal = self.guild_system.change_member_contribution_in_guild(
                gid, resolved, result["delta"]
            )
            result["ok"] = bool(ok)
            result["error"] = err
            result["personal_contribution"] = int(personal)
            return result
        except Exception as e:
            try:
                if self.logger:
                    self.logger.error(f"[ARC Core]api_change_member_guild_contribution error: {e}")
            except Exception:
                pass
            result["error"] = "GUILD_DB_ERROR"
            return result

    def api_list_guild_members(self, guild_id: int) -> list:
        """列出公会成员。每项含 xuid、role、joined_at、contribution。公会不存在或失败返回 []。"""
        try:
            gid = int(guild_id)
        except (TypeError, ValueError):
            return []
        if gid <= 0 or not self.guild_system.get_guild(gid):
            return []
        try:
            rows = self.guild_system.list_members(gid) or []
            out = []
            for row in rows:
                out.append({
                    "xuid": str(row.get("xuid") or ""),
                    "role": str(row.get("role") or ""),
                    "joined_at": str(row.get("joined_at") or ""),
                    "contribution": int(row.get("contribution") or 0),
                })
            return out
        except Exception as e:
            try:
                if self.logger:
                    self.logger.error(f"[ARC Core]api_list_guild_members error: {e}")
            except Exception:
                pass
            return []

    # 公告系统
    def _load_broadcast_messages(self):
        """从broadcast.txt文件加载公告消息"""
        try:
            broadcast_file = Path(MAIN_PATH) / "broadcast.txt"
            if not broadcast_file.exists():
                self.logger.warning(f"[ARC Core]broadcast.txt not found, creating empty file")
                broadcast_file.parent.mkdir(exist_ok=True)
                broadcast_file.touch()
                return

            with broadcast_file.open("r", encoding="utf-8") as f:
                lines = f.readlines()
                self.broadcast_messages = [line.strip() for line in lines if line.strip()]

            if not self.broadcast_messages:
                self.logger.warning(f"[ARC Core]broadcast.txt is empty")
            else:
                self.logger.info(f"[ARC Core]Loaded {len(self.broadcast_messages)} broadcast messages")
        except Exception as e:
            self.logger.error(f"[ARC Core]Load broadcast messages error: {str(e)}")

    def send_broadcast_message(self):
        """发送公告消息"""
        try:
            if not self.broadcast_messages:
                return

            # 获取当前公告消息
            message = self.broadcast_messages[self.current_broadcast_index]
            
            # 替换特殊符号
            message = self._process_broadcast_placeholders(message)
            
            # 发送给所有在线玩家
            for player in self.server.online_players:
                player.send_message(f"{self.language_manager.GetText('BROADCAST_MESSAGE_PREFIX')}: {message}")
            
            # 更新索引
            self.current_broadcast_index = (self.current_broadcast_index + 1) % len(self.broadcast_messages)
            
        except Exception as e:
            self.logger.error(f"[ARC Core]Send broadcast message error: {str(e)}")

    def _process_broadcast_placeholders(self, message: str) -> str:
        """处理公告消息中的占位符"""
        try:
            current_time = datetime.now()
            
            # 替换{date}为当前日期 (年-月-日)
            date_str = current_time.strftime("%Y-%m-%d")
            message = message.replace("{date}", date_str)
            
            # 替换{time}为当前时间 (小时:分钟)
            time_str = current_time.strftime("%H:%M")
            message = message.replace("{time}", time_str)
            
            # 替换{online_player_number}为当前在线玩家数量
            online_player_count = len(self.server.online_players)
            message = message.replace("{online_player_number}", str(online_player_count))
            
            return message
        except Exception as e:
            self.logger.error(f"[ARC Core]Process broadcast placeholders error: {str(e)}")
            return message  # 如果处理失败，返回原消息

    def send_small_horn_messages(self):
        """每10分钟轮询并广播所有有效小喇叭。"""
        try:
            now_iso = datetime.now().isoformat(timespec='seconds')
            active_orders = self.database_manager.query_all(
                "SELECT id, xuid, content, end_time FROM small_horn_orders "
                "WHERE start_time <= ? AND end_time > ? "
                "ORDER BY created_at ASC",
                (now_iso, now_iso)
            )
            if not active_orders:
                return

            for order in active_orders:
                player_xuid = str(order.get('xuid') or '').strip()
                content = str(order.get('content') or '').strip()
                if not player_xuid or not content:
                    continue
                display_name = (
                    self.get_player_name_by_xuid(player_xuid, return_with_title=True)
                    or self.get_player_name_by_xuid(player_xuid, return_with_title=False)
                    or player_xuid
                )
                self.server.broadcast_message(f"[小喇叭]玩家{display_name}：{content}")

            self.database_manager.execute(
                "DELETE FROM small_horn_orders WHERE end_time <= ?",
                (now_iso,)
            )
        except Exception as e:
            self.logger.error(f"[ARC Core]Send small horn messages error: {str(e)}")

    def _format_death_broadcast_player_display(self, player: Player) -> str:
        """死亡播报中的玩家展示名：与聊天/头顶名一致（含公会前缀）。"""
        equipped = self.title_system.get_equipped_title(player)
        raw_name = getattr(player, "name", "") or ""
        base = self.format_player_display_label_with_guild(
            raw_name, equipped, str(player.xuid)
        )
        return f"{base}§r"


    def _send_death_broadcast(self, event: PlayerDeathEvent):
        """发送死亡播报消息"""
        try:
            player_name = self._format_death_broadcast_player_display(event.player)
            dimension_raw = get_dimension_id(event.player.location.dimension)
            dimension = self._translate_dimension_name(dimension_raw)
            x = int(event.player.location.x)
            y = int(event.player.location.y)
            z = int(event.player.location.z)
            
            # 尝试获取死亡原因
            death_cause_raw = self._get_death_cause(event)
            death_cause_translated = self._translate_death_cause(death_cause_raw) if death_cause_raw else ""
            
            # 尝试获取攻击者信息
            attacker_name = self._get_entity_name_from_damage_source(event)
            # 由生物/玩家造成的死亡原因（原始类型，用于判断是否显示攻击者；与语言文件“生物殴打”等对应）
            is_entity_cause = self._is_entity_attack_death_cause(death_cause_raw)
            
            # 根据死亡原因和攻击者信息选择消息格式
            if attacker_name and is_entity_cause:
                # 被生物或玩家杀死：根据死因使用更有梗的文案
                game_key, qq_key = self._pick_entity_kill_message_keys(death_cause_raw)
                game_message = self.language_manager.GetText(game_key).format(
                    player_name, dimension, x, y, z, attacker_name
                )
                qq_message = self.language_manager.GetText(qq_key).format(
                    player_name, dimension, x, y, z, attacker_name
                )
            elif death_cause_translated:
                # 只有死亡原因
                game_message = self.language_manager.GetText('DEATH_BROADCAST_MESSAGE_WITH_CAUSE').format(
                    player_name, dimension, x, y, z, death_cause_translated
                )
                qq_message = self.language_manager.GetText('DEATH_QQ_MESSAGE_WITH_CAUSE').format(
                    player_name, dimension, x, y, z, death_cause_translated
                )
            else:
                # 没有死亡原因
                game_message = self.language_manager.GetText('DEATH_BROADCAST_MESSAGE').format(
                    player_name, dimension, x, y, z
                )
                qq_message = self.language_manager.GetText('DEATH_QQ_MESSAGE').format(
                    player_name, dimension, x, y, z
                )
            
            # 发送给所有在线玩家
            for player in self.server.online_players:
                player.send_message(game_message)

            # 发送到QQ群（受 QQ_DEATH_BROADCAST_MODE 控制）
            if self._should_send_qq_death_broadcast(event):
                try:
                    equipped = self.title_system.get_equipped_title(event.player)
                    display_name = self.format_player_display_label_with_guild(
                        getattr(event.player, "name", "") or "",
                        equipped,
                        str(event.player.xuid),
                    )
                    self._notify_qqsync(
                        "death",
                        display_name,
                        getattr(event.player, "name", "") or "",
                        qq_message,
                    )
                except Exception as e:
                    self.logger.error(f"[ARC Core] 发送死亡消息到QQ群失败: {e}")
                
        except Exception as e:
            self.logger.error(f"[ARC Core]Send death broadcast error: {str(e)}")

    def _get_death_cause(self, event: PlayerDeathEvent) -> str:
        """获取死亡原因"""
        try:
            # 根据EndStone文档，PlayerDeathEvent有damage_source属性
            if hasattr(event, 'damage_source') and event.damage_source:
                damage_source = event.damage_source
                
                # 尝试获取伤害源类型
                if hasattr(damage_source, 'damage_type'):
                    return str(damage_source.damage_type)
                elif hasattr(damage_source, 'type'):
                    return str(damage_source.type)
                else:
                    return str(damage_source)
            # 兼容性检查其他可能的属性
            elif hasattr(event, 'death_cause'):
                return str(event.death_cause)
            elif hasattr(event, 'cause'):
                return str(event.cause)
            else:
                return ""
        except Exception as e:
            self.logger.error(f"[ARC Core]Get death cause error: {str(e)}")
            return ""

    def _is_entity_attack_death_cause(self, death_cause_raw: str) -> bool:
        """判断是否为生物/玩家攻击类死亡原因（有明确攻击者时可显示“被xx殴打致死”）。"""
        if not death_cause_raw or not str(death_cause_raw).strip():
            return False
        cause = str(death_cause_raw).strip().lower()
        if ":" in cause:
            cause = cause.split(":")[-1]
        entity_attack_causes = {
            "entity_attack", "mob_attack", "player_attack",
            "arrow", "trident", "thrown", "mob_projectile", "projectile",
            "entity_explosion", "mob_explosion", "entity_explosion",
            "ram_attack", "spit", "sting", "sweep_attack",
        }
        return cause in entity_attack_causes

    def _pick_entity_kill_message_keys(self, death_cause_raw: str) -> tuple[str, str]:
        """根据死因选择“带击杀者”的播报文案 key。"""
        cause = str(death_cause_raw or "").strip().lower()
        if ":" in cause:
            cause = cause.split(":")[-1]

        if cause in {"entity_explosion", "mob_explosion"}:
            return "DEATH_BROADCAST_BY_ENTITY_EXPLOSION", "DEATH_QQ_BY_ENTITY_EXPLOSION"

        if cause in {"arrow", "trident", "thrown", "mob_projectile", "projectile"}:
            return "DEATH_BROADCAST_BY_ENTITY_PROJECTILE", "DEATH_QQ_BY_ENTITY_PROJECTILE"

        # 默认近战/其他可归因到实体的情况
        return "DEATH_BROADCAST_BY_ENTITY", "DEATH_QQ_BY_ENTITY"

    def _translate_death_cause(self, death_cause: str) -> str:
        """翻译死亡原因"""
        try:
            if not death_cause:
                return ""
            
            # 将死亡原因转换为大写并添加前缀
            death_cause_key = f"DEATH_CAUSE_{death_cause.upper()}"
            
            # 使用 LanguageManager 获取翻译
            translation = self.language_manager.GetText(death_cause_key)
            
            # 如果找到了翻译，返回翻译结果
            if translation:
                return translation
            
            # 如果没找到翻译，尝试部分匹配
            # 处理一些特殊情况，比如 minecraft:fall 这样的格式
            if ':' in death_cause:
                simple_cause = death_cause.split(':')[-1]
                simple_key = f"DEATH_CAUSE_{simple_cause.upper()}"
                simple_translation = self.language_manager.GetText(simple_key)
                if simple_translation:
                    return simple_translation
            
            # 如果找不到翻译，返回原字符串
            return death_cause
            
        except Exception as e:
            self.logger.error(f"[ARC Core] 翻译死亡原因错误: {str(e)}")
            return death_cause

    def _get_entity_name_from_damage_source(self, event: PlayerDeathEvent) -> str:
        """从伤害源获取生物/玩家名称（用于“被xx殴打致死”播报）。"""
        try:
            # 先尝试事件自身的攻击者属性（部分引擎把 killer/damager 放在 event 上）
            if hasattr(event, "killer") and event.killer:
                name = self._translate_entity_name(event.killer)
                if name:
                    return name
            if hasattr(event, "damager") and event.damager:
                name = self._translate_entity_name(event.damager)
                if name:
                    return name
            if hasattr(event, "damage_source") and event.damage_source:
                damage_source = event.damage_source
                # 优先 actor（造成伤害的实体，近战为生物本身）
                if hasattr(damage_source, "actor") and damage_source.actor:
                    name = self._translate_entity_name(damage_source.actor)
                    if name:
                        return name
                if hasattr(damage_source, "damaging_actor") and damage_source.damaging_actor:
                    name = self._translate_entity_name(damage_source.damaging_actor)
                    if name:
                        return name
                if hasattr(damage_source, "damaging_entity") and damage_source.damaging_entity:
                    name = self._translate_entity_name(damage_source.damaging_entity)
                    if name:
                        return name
                if hasattr(damage_source, "entity") and damage_source.entity:
                    name = self._translate_entity_name(damage_source.entity)
                    if name:
                        return name
                if hasattr(damage_source, "attacker") and damage_source.attacker:
                    name = self._translate_entity_name(damage_source.attacker)
                    if name:
                        return name
            return ""
        except Exception as e:
            self.logger.error(f"[ARC Core] 获取生物名称错误: {str(e)}")
            return ""

    def _translate_entity_name(self, entity) -> str:
        """翻译生物名称。若名称为 MC 未翻译键（含 ':'，如 entity.ns_ab:vfx_dragon_fire.name），则用 EntityDisplayNameManager 从 entity_display_name.txt 读取。"""
        try:
            if not entity:
                return ""
            raw = (
                getattr(entity, "name_tag", None)
                or getattr(entity, "type", None)
                or getattr(entity, "name", None)
            )
            raw = str(raw).strip() if raw else str(type(entity).__name__)
            if ":" in raw or (raw.startswith("entity.") and raw.endswith(".name")):
                return self.entity_display_name_manager.get_display_name(raw)
            return raw
        except Exception as e:
            if self.logger:
                self.logger.error(f"[ARC Core] 翻译生物名称错误: {str(e)}")
            return str(entity) if entity else ""

    def _translate_dimension_name(self, dimension_name: str) -> str:
        """将维度标识转为显示名：原版走语言文件，自定义/未知返回完整 ID。"""
        return translate_dimension_display(dimension_name, self.language_manager)

    def _get_qq_sync_plugin(self):
        """Resolve ARC QQ Sync plugin (AstrBot hub id first, legacy id fallback).

        Endstone 会把 entry-point 里的 '-' 转成 '_'，故优先查找 arc_qq_sync_astrbot。
        """
        pm = self.server.plugin_manager
        for name in (
            "arc_qq_sync_astrbot",
            "arc-qq-sync-astrbot",
            "qqsync_plugin",
        ):
            plug = pm.get_plugin(name)
            if plug is not None:
                return plug
        return None

    def _get_qq_death_broadcast_mode(self) -> str:
        """群聊死亡播报模式：off / pvp / all（默认 all）。"""
        raw = (self.setting_manager.GetSetting("QQ_DEATH_BROADCAST_MODE") or "").strip().lower()
        if not raw:
            return "all"
        aliases = {
            "off": "off",
            "none": "off",
            "0": "off",
            "false": "off",
            "no": "off",
            "不播报": "off",
            "pvp": "pvp",
            "only_pvp": "pvp",
            "pvp_only": "pvp",
            "仅播报pvp死亡": "pvp",
            "仅pvp": "pvp",
            "all": "all",
            "全部播报": "all",
            "true": "all",
            "1": "all",
        }
        return aliases.get(raw, "all")

    def _is_pvp_death(self, event: PlayerDeathEvent) -> bool:
        """判断是否为玩家击杀玩家（PvP）死亡。"""
        try:
            killer = self._sky_eye_resolve_killer_actor(event)
            if killer is None:
                return False
            if isinstance(killer, Player):
                return True
            killer_type = str(getattr(killer, "type", "") or "").strip().lower()
            if killer_type in ("minecraft:player", "player"):
                return True
            cause = str(self._get_death_cause(event) or "").strip().lower()
            if ":" in cause:
                cause = cause.split(":")[-1]
            return cause == "player_attack"
        except Exception:
            return False

    def _should_send_qq_death_broadcast(self, event: PlayerDeathEvent) -> bool:
        mode = self._get_qq_death_broadcast_mode()
        if mode == "off":
            return False
        if mode == "pvp":
            return self._is_pvp_death(event)
        return True

    def _send_to_qq_group(self, message: str):
        """Send a raw text message to QQ via local ARC QQ Sync plugin."""
        try:
            qqsync = self._get_qq_sync_plugin()
            if qqsync is None:
                self.logger.warning("[ARC Core] QQ Sync plugin not found, cannot send group message")
                return
            if hasattr(qqsync, "api_send_raw"):
                success = qqsync.api_send_raw(message)
            elif hasattr(qqsync, "api_send_message"):
                success = qqsync.api_send_message(message)
            else:
                self.logger.warning("[ARC Core] QQ Sync 无可用发送 API")
                return
            if not success:
                self.logger.warning(f"[ARC Core] QQ group message send failed: {message}")
        except Exception as e:
            self.logger.error(f"[ARC Core] QQ group message send error: {str(e)}")

    def _on_db_write_for_sync(self, kind: str, table: str, **kwargs):
        """本地写库成功后：子服入 outbox 上行 / 主机广播给子服。"""
        try:
            from endstone_arc_core.sync_protocol import TABLE_TO_ENUM
            if table not in TABLE_TO_ENUM:
                return
            # 客户端：无论是否已连接都入队（断线不丢）
            client = getattr(self, "sync_client", None)
            if client is not None and getattr(client, "is_active", lambda: False)():
                client.mirror_local_write(kind, table, **kwargs)
                return
            if self.sync_server and self.sync_server.is_running():
                self.sync_server.mirror_local_write(kind, table, **kwargs)
        except Exception as e:
            try:
                self.logger.error(f"[ARC Core] Sync mirror write error: {e}")
            except Exception:
                pass

    def get_sync_status_text(self) -> str:
        """OP 面板：跨服同步状态摘要。"""
        from endstone_arc_core.sync_config import setting_bool

        mode = getattr(self, "_sync_consumer_mode", "none")
        mode_label = {
            "client": "远程客户端",
            "none": "未启用消费",
        }.get(mode, str(mode))
        lines = [
            f"消费模式: {mode_label}",
            f"同步中心: "
            f"{'运行中' if self.sync_server and self.sync_server.is_running() else '未开启'}",
        ]
        if self.sync_server and self.sync_server.is_running():
            try:
                lines.append(
                    f"已连接从服: {self.sync_server.get_connected_count()}"
                )
            except Exception:
                pass
        if self.sync_client is not None:
            st = {}
            try:
                st = self.sync_client.get_status()
            except Exception:
                st = {}
            lines.append(
                f"客户端: "
                f"{'已连接' if st.get('connected') else ('重连中' if st.get('active') else '未启动')}"
            )
            if st.get("server"):
                lines.append(f"目标: {st.get('server')}")
            lines.append(f"Outbox 待发: {st.get('outbox_pending', 0)}")
            if st.get("last_error"):
                lines.append(f"最近失败: {st.get('last_error')}")
        authority = self._can_compute_conditional_titles()
        raw_auth = self.setting_manager.GetSetting("CONDITIONAL_TITLE_AUTHORITY")
        auth_src = (
            "显式配置"
            if raw_auth is not None and str(raw_auth).strip() != ""
            else "自动推断"
        )
        lines.append(
            f"条件头衔权威: {'是' if authority else '否'}（{auth_src}）"
        )
        try:
            holder = self.current_richest_xuid
            if holder and authority:
                lines.append(f"当前首富 xuid: {holder}")
        except Exception:
            pass
        return "\n".join(lines)

    def run_sync_reconcile(self) -> dict:
        """OP / 连接：触发已启用同步表全面对账（远程重连自动拉+推）。"""
        mode = getattr(self, "_sync_consumer_mode", "none")
        if mode == "client" and self.sync_client is not None:
            if not getattr(self.sync_client, "is_active", lambda: False)():
                return {
                    "mode": "client",
                    "detail": "同步客户端未启动",
                }
            result = self.sync_client.reconcile_tables()
            return {
                "mode": "client",
                "detail": (
                    f"已发起全面对账（{result.get('requested_tables', 0)} 张表），"
                    f"重连后自动拉全量并上行；当前 outbox={result.get('outbox_pending', 0)}"
                ),
            }
        return {
            "mode": mode or "none",
            "detail": "当前不是远程客户端模式，无需对账",
        }


    def _notify_qqsync(self, event_type: str, display_name: str,
                        raw_player_name: str, message: str = ""):
        """Notify local ARC QQ Sync to send death/custom events to QQ.

        Join/chat/quit are owned by the QQ Sync plugin itself.
        Cross-server fan-out is handled by AstrBot hub, not ARCCore.
        """
        try:
            qqsync = self._get_qq_sync_plugin()
            if qqsync is None:
                return
            qqsync.api_send_event(event_type, display_name, raw_player_name, message)
        except Exception as e:
            self.logger.error(f"[ARC Core]Notify qqsync error: {e}")

    def _record_player_join_playtime(self, player) -> None:
        """Increment cross-server session_count and start this session timer."""
        try:
            import time as _time
            from datetime import datetime as _dt
            name = getattr(player, "name", "") or ""
            xuid = str(getattr(player, "xuid", "") or "")
            if not name or not xuid:
                return
            now_iso = _dt.now().isoformat(timespec="seconds")
            self._play_session_start[xuid] = int(_time.time())
            row = self.database_manager.query_one(
                "SELECT session_count FROM player_basic_info WHERE xuid = ?",
                (xuid,),
            )
            if not row:
                return
            session_count = int(row.get("session_count") or 0) + 1
            self.database_manager.update(
                table="player_basic_info",
                data={"session_count": session_count, "last_join_time": now_iso},
                where="xuid = ?",
                params=(xuid,),
            )
        except Exception as e:
            if self.logger:
                self.logger.error(f"[ARC Core] record join playtime error: {e}")

    def _record_player_quit_playtime(
        self, player=None, *, xuid: str = "", name: str = ""
    ) -> None:
        """Accumulate session seconds into cross-server total_playtime."""
        try:
            import time as _time
            from datetime import datetime as _dt
            if player is not None:
                name = getattr(player, "name", "") or name
                xuid = str(getattr(player, "xuid", "") or "") or xuid
            if not xuid:
                return
            started = self._play_session_start.pop(xuid, None)
            if started is None and name:
                started = self._play_session_start.pop(name, None)
            now_iso = _dt.now().isoformat(timespec="seconds")
            delta = max(0, int(_time.time()) - int(started)) if started is not None else 0
            row = self.database_manager.query_one(
                "SELECT total_playtime FROM player_basic_info WHERE xuid = ?",
                (xuid,),
            )
            if not row:
                return
            total = int(row.get("total_playtime") or 0) + delta
            self.database_manager.update(
                table="player_basic_info",
                data={"total_playtime": total, "last_quit_time": now_iso},
                where="xuid = ?",
                params=(xuid,),
            )
        except Exception as e:
            if self.logger:
                self.logger.error(f"[ARC Core] record quit playtime error: {e}")

    def _settle_all_online_playtime(self) -> None:
        """Settle timers for all online players (plugin disable / shutdown)."""
        for player in list(getattr(self.server, "online_players", []) or []):
            try:
                self._record_player_quit_playtime(player)
            except Exception:
                pass

    def api_get_player_playtime(self, raw_player_name: str = "", xuid: str = "") -> dict:
        """Public API: playtime / session stats for QQ Sync and other plugins.

        Args:
            raw_player_name: Player name.
            xuid: Optional XUID.

        Returns:
            Dict with session_count, total_playtime (seconds, includes current session),
            is_online, last_join_time, last_quit_time.
        """
        empty = {
            "session_count": 0,
            "total_playtime": 0,
            "is_online": False,
            "last_join_time": None,
            "last_quit_time": None,
            "xuid": "",
        }
        try:
            import time as _time
            resolved = self._api_resolve_player_xuid(raw_player_name, xuid)
            if not resolved:
                return empty
            row = self.database_manager.query_one(
                "SELECT * FROM player_basic_info WHERE xuid = ?",
                (resolved,),
            )
            if not row:
                return empty
            total = int(row.get("total_playtime") or 0)
            key = str(row.get("xuid") or resolved)
            started = self._play_session_start.get(key) if key else None
            is_online = started is not None
            if is_online:
                total += max(0, int(_time.time()) - int(started))
            if not is_online:
                is_online = self._find_online_player_by_xuid(key) is not None
            return {
                "session_count": int(row.get("session_count") or 0),
                "total_playtime": total,
                "is_online": bool(is_online),
                "last_join_time": row.get("last_join_time"),
                "last_quit_time": row.get("last_quit_time"),
                "xuid": key,
            }
        except Exception as e:
            if self.logger:
                self.logger.error(f"[ARC Core] api_get_player_playtime error: {e}")
            return empty

    # 清道夫系统
    def _init_cleaner_system(self):
        """初始化清道夫系统"""
        try:
            # 获取清道夫设置
            self.enable_cleaner = self.setting_manager.GetSetting('ENABLE_CLEANER')
            if self.enable_cleaner is None or self.enable_cleaner.lower() not in ['true', 'false']:
                self.enable_cleaner = False
            else:
                self.enable_cleaner = self.enable_cleaner.lower() == 'true'

            self.cleaner_interval = self.setting_manager.GetSetting('CLEANER_INTERVAL')
            try:
                self.cleaner_interval = int(self.cleaner_interval)
            except (ValueError, TypeError):
                self.cleaner_interval = 600  # 默认10分钟

            if self.enable_cleaner:
                self.logger.info(f"[ARC Core]Cleaner system enabled, interval: {self.cleaner_interval} seconds")
            else:
                self.logger.info(f"[ARC Core]Cleaner system disabled")

        except Exception as e:
            self.logger.error(f"[ARC Core]Init cleaner system error: {str(e)}")

    def _init_mspt_emergency_shutdown_settings(self):
        """性能检测应急关服：读取 ENABLE_MSPT_EMERGENCY_SHUTDOWN 与 MSPT_EMERGENCY_SHUTDOWN_LIMIT。"""
        try:
            raw = self.setting_manager.GetSetting('ENABLE_MSPT_EMERGENCY_SHUTDOWN')
            if raw is None or str(raw).strip() == '':
                self.enable_mspt_emergency_shutdown = False
            else:
                self.enable_mspt_emergency_shutdown = str(raw).lower() in ('true', '1', 'yes')

            lim = self.setting_manager.GetSetting('MSPT_EMERGENCY_SHUTDOWN_LIMIT')
            try:
                self.mspt_emergency_shutdown_limit = float(lim) if lim not in (None, '') else 100.0
                if self.mspt_emergency_shutdown_limit <= 0:
                    self.mspt_emergency_shutdown_limit = 100.0
            except (ValueError, TypeError):
                self.mspt_emergency_shutdown_limit = 100.0

            if self.logger:
                if self.enable_mspt_emergency_shutdown:
                    self.logger.info(
                        f"[ARC Core]性能检测应急关服已启用，每 10 秒检测 current_mspt，阈值={self.mspt_emergency_shutdown_limit}"
                    )
                else:
                    self.logger.info("[ARC Core]性能检测应急关服未启用")
        except Exception as e:
            self.enable_mspt_emergency_shutdown = False
            self.mspt_emergency_shutdown_limit = 100.0
            if self.logger:
                self.logger.error(f"[ARC Core]Init MSPT emergency shutdown error: {str(e)}")

    def _mspt_emergency_shutdown_tick(self):
        """定时检查 Server.current_mspt，超过阈值则执行 stop 关服。"""
        if not self.enable_mspt_emergency_shutdown:
            return
        try:
            server = self.server
            if not hasattr(server, 'current_mspt'):
                if self.logger and not self._mspt_emergency_missing_attr_logged:
                    self._mspt_emergency_missing_attr_logged = True
                    self.logger.warning(
                        "[ARC Core]性能检测应急关服：当前服务端无 current_mspt 属性，已跳过检测"
                    )
                return
            mspt = float(server.current_mspt)
            if mspt > self.mspt_emergency_shutdown_limit:
                if self.logger:
                    self.logger.error(
                        f"{ColorFormat.RED}[ARC Core]性能检测应急关服触发：current_mspt={mspt} "
                        f"> {self.mspt_emergency_shutdown_limit}，正在执行 stop"
                    )
                server.dispatch_command(server.command_sender, "stop")
        except Exception as e:
            if self.logger:
                self.logger.error(f"[ARC Core]MSPT emergency shutdown tick error: {str(e)}")

    def start_cleaner_warning(self):
        """开始清道夫警告倒计时"""
        try:
            if not self.enable_cleaner:
                return

            # 发送10秒后清理警告
            for player in self.server.online_players:
                player.send_message(self.language_manager.GetText('READY_TO_CLEAR_DROP_ITEM_BROADCAST'))

            # 10秒后执行清理
            self.server.scheduler.run_task(self, self.execute_cleaner, delay=200)  # 10秒 = 200 ticks

        except Exception as e:
            self.logger.error(f"[ARC Core]Start cleaner warning error: {str(e)}")

    def execute_cleaner(self):
        """执行清理掉落物"""
        try:
            if not self.enable_cleaner:
                return

            # 发送正在清理消息
            for player in self.server.online_players:
                player.send_message(self.language_manager.GetText('CLEAR_DROP_ITEM_BROADCAST'))

            # 执行清理命令
            self.server.dispatch_command(self.server.command_sender, "kill @e[type=item,name=!\"Trial Key\",name=!\"Ominous Trial Key\"]")

            # 发送清理完成消息
            self.server.scheduler.run_task(self, self.cleaner_complete_message, delay=20)  # 1秒后发送完成消息

        except Exception as e:
            self.logger.error(f"[ARC Core]Execute cleaner error: {str(e)}")

    def cleaner_complete_message(self):
        """发送清理完成消息"""
        try:
            for player in self.server.online_players:
                player.send_message(self.language_manager.GetText('CLEAR_DROP_ITEM_COMPLETE'))
        except Exception as e:
            self.logger.error(f"[ARC Core]Cleaner complete message error: {str(e)}")