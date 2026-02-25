import hashlib
import json
import math
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, Set

from endstone import ColorFormat, Player, GameMode
from endstone.form import ActionForm, TextInput, ModalForm, Label
from endstone.command import Command, CommandSender
from endstone.event import event_handler, PlayerJoinEvent, PlayerQuitEvent, BlockBreakEvent, BlockPlaceEvent, PlayerDeathEvent, PlayerInteractEvent, ActorExplodeEvent, PlayerInteractActorEvent, ActorDamageEvent 
from endstone.plugin import Plugin

from endstone_arc_core.DatabaseManager import DatabaseManager
from endstone_arc_core.LanguageManager import LanguageManager
from endstone_arc_core.SettingManager import SettingManager
from endstone_arc_core.MigrationManager import MigrationManager

MAIN_PATH = 'plugins/ARCCore'

class ARCCorePlugin(Plugin):
    api_version = "0.10"
    commands = {
        "updatespawnpos": {
            "description": "Update spawn position of current dimension.",
            "usages": ["/updatespawnpos"]
        },
        "arc": {
            "description": "ARC Core menu command.",
            "usages": ["/arc"],
            "permissions": ["arc_core.command.arc"],
        },
        "suicide":
            {
            "description": "Kill yourself.",
            "usages": ["/suicide"],
            "permissions": ["arc_core.command.suicide"],
        },
        "spawn":
        {
            "description": "Teleport to spawn position.",
            "usages": ["/spawn"],
            "permissions": ["arc_core.command.spawn"],
        },
        "landpos1": {
            "description": "Set new land corner 1.",
            "usages": ["/landpos1"],
            "permissions": ["arc_core.command.set_land_corner"],
        },
        "landpos2": {
            "description": "Set new land corner 2.",
            "usages": ["/landpos2"],
            "permissions": ["arc_core.command.set_land_corner"],
        },
        "landbuy": {
            "description": "Buy the pending new land.",
            "usages": ["/landbuy"],
            "permissions": ["arc_core.command.set_land_corner"],
        }
    }
    permissions = {
        "arc_core.command.arc": {
            "description": "Can used by everyone.",
            "default": True,
        },
        "arc_core.command.suicide": {
            "description": "Can used by everyone.",
            "default": True,
        },
        "arc_core.command.spawn": {
            "description": "Can used by everyone.",
            "default": True,
        },
        "arc_core.command.set_land_corner": {
            "description": "Can used by everyone.",
            "default": True,
        }
    }

    def __init__(self):
        # 在__init__中不能使用self.logger打印，因为self.logger还没有初始化
        super().__init__()
        self.setting_manager = SettingManager()
        default_language_dode = self.setting_manager.GetSetting('DEFAULT_LANGUAGE_CODE')
        self.language_manager = LanguageManager(default_language_dode if default_language_dode is not None else 'ZH-CN')
        self.database_manager = DatabaseManager(Path(MAIN_PATH) / self.setting_manager.GetSetting('DATABASE_PATH'))
        self.migration_manager = MigrationManager(self.database_manager)
        self.init_database()

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

        # 玩家认证
        self.player_authentication_state = {}

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
        self.land_min_size = self.setting_manager.GetSetting('LAND_MIN_SIZE')
        try:
            self.land_min_size = int(self.land_min_size)
        except (ValueError, TypeError):
            self.land_min_size = 5  # 默认最小尺寸为5
        self.player_new_land_creation_info = {}  # {name: {'dimension': str, 'min_x': int, 'max_x': int, 'min_y': int, 'max_y': int, 'min_z': int, 'max_z': int}}
        self.player_land_pos1 = {}  # {name: {'dimension': str, 'x': int, 'y': int, 'z': int}} 暂存/landpos1

        # OP坐标记录与上次执行指令（空输入时重复执行）
        self.op_coordinate1_dict = {}
        self.op_coordinate2_dict = {}
        self.op_last_command_dict = {}

        # 玩家出入领地
        self.player_in_land_id_dict = {}
        
        # 多线程位置检测相关
        self.position_thread = None
        self.position_thread_running = False
        self.position_thread_lock = threading.Lock()
        self.position_check_interval = 0.5  # 每0.5秒检查一次，比原来的1.25秒更快

        # 死亡回归系统
        self.player_death_locations = {}  # 存储玩家死亡位置 {player_name: {'dimension': str, 'x': float, 'y': float, 'z': float}}

        # 传送系统
        self.max_player_home_num = self.setting_manager.GetSetting('MAX_PLAYER_HOME_NUM')
        try:
            self.max_player_home_num = int(self.max_player_home_num)
        except (ValueError, TypeError):
            self.max_player_home_num = 3
        self.teleport_requests = {}  # 存储传送请求 {player_name: {'type': 'tpa'/'tphere', 'target': target_name, 'expire_time': time}}
        
        # 随机传送配置
        self.enable_random_teleport = self.setting_manager.GetSetting('ENABLE_RANDOM_TELEPORT')
        if self.enable_random_teleport is None:
            self.enable_random_teleport = True
        else:
            try:
                self.enable_random_teleport = str(self.enable_random_teleport).lower() in ['true', '1', 'yes']
            except (ValueError, AttributeError):
                self.enable_random_teleport = True
        
        self.random_teleport_center_x = self.setting_manager.GetSetting('RANDOM_TELEPORT_CENTER_X')
        try:
            self.random_teleport_center_x = int(self.random_teleport_center_x)
        except (ValueError, TypeError):
            self.random_teleport_center_x = 0
            
        self.random_teleport_center_z = self.setting_manager.GetSetting('RANDOM_TELEPORT_CENTER_Z')
        try:
            self.random_teleport_center_z = int(self.random_teleport_center_z)
        except (ValueError, TypeError):
            self.random_teleport_center_z = 0
            
        self.random_teleport_radius = self.setting_manager.GetSetting('RANDOM_TELEPORT_RADIUS')
        try:
            self.random_teleport_radius = int(self.random_teleport_radius)
        except (ValueError, TypeError):
            self.random_teleport_radius = 5000
        
        # 传送收费配置
        self.teleport_cost_public_warp = self.setting_manager.GetSetting('TELEPORT_COST_PUBLIC_WARP')
        try:
            self.teleport_cost_public_warp = int(self.teleport_cost_public_warp)
        except (ValueError, TypeError):
            self.teleport_cost_public_warp = 0
            
        self.teleport_cost_home = self.setting_manager.GetSetting('TELEPORT_COST_HOME')
        try:
            self.teleport_cost_home = int(self.teleport_cost_home)
        except (ValueError, TypeError):
            self.teleport_cost_home = 0
            
        self.teleport_cost_land = self.setting_manager.GetSetting('TELEPORT_COST_LAND')
        try:
            self.teleport_cost_land = int(self.teleport_cost_land)
        except (ValueError, TypeError):
            self.teleport_cost_land = 0
            
        self.teleport_cost_death_location = self.setting_manager.GetSetting('TELEPORT_COST_DEATH_LOCATION')
        try:
            self.teleport_cost_death_location = int(self.teleport_cost_death_location)
        except (ValueError, TypeError):
            self.teleport_cost_death_location = 0
            
        self.teleport_cost_random = self.setting_manager.GetSetting('TELEPORT_COST_RANDOM')
        try:
            self.teleport_cost_random = int(self.teleport_cost_random)
        except (ValueError, TypeError):
            self.teleport_cost_random = 100
            
        self.teleport_cost_player = self.setting_manager.GetSetting('TELEPORT_COST_PLAYER')
        try:
            self.teleport_cost_player = int(self.teleport_cost_player)
        except (ValueError, TypeError):
            self.teleport_cost_player = 50

        # 公告系统
        self.broadcast_messages = []  # 存储公告消息列表
        self.current_broadcast_index = 0  # 当前公告索引
        self.broadcast_interval = self.setting_manager.GetSetting('BROADCAST_INTERVAL')
        try:
            self.broadcast_interval = int(self.broadcast_interval)
        except (ValueError, TypeError):
            self.broadcast_interval = 300  # 默认5分钟（300秒）

        # 新人欢迎系统
        self.newbie_welcome_file = Path(MAIN_PATH) / "newbie_welcome.txt"
        self.newbie_commands_file = Path(MAIN_PATH) / "newbie_commands.txt"
        self._ensure_newbie_files_exist()

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
                default_commands = "# 新人指令文件\n# 每行一个指令，{player} 会被替换为玩家名称\n# 示例：\n# gamemode 0 {player}\n# give {player} minecraft:bread 16\n# clear {player}"
                self.newbie_commands_file.write_text(default_commands, encoding='utf-8')
                # 在__init__期间不能使用self.logger，使用print代替
                print(f"[ARC Core]Created default newbie commands file: {self.newbie_commands_file}")
                
        except Exception as e:
            # 在__init__期间不能使用self.logger，使用print代替
            print(f"[ARC Core]Failed to create newbie files: {str(e)}")

    def _send_newbie_welcome_message(self, player: Player):
        """发送新人欢迎消息"""
        try:
            if self.newbie_welcome_file.exists():
                welcome_content = self.newbie_welcome_file.read_text(encoding='utf-8').strip()
                if welcome_content:
                    # 将换行符分割成多条消息
                    messages = welcome_content.split('\n')
                    for message in messages:
                        if message.strip():  # 跳过空行
                            player.send_message(f"§e[欢迎] §f{message.strip()}")
                    self.logger.info(f"[ARC Core]Sent welcome message to new player: {player.name}")
                else:
                    self.logger.warning(f"[ARC Core]Welcome file is empty: {self.newbie_welcome_file}")
            else:
                self.logger.warning(f"[ARC Core]Welcome file not found: {self.newbie_welcome_file}")
        except Exception as e:
            self.logger.error(f"[ARC Core]Failed to send welcome message to {player.name}: {str(e)}")

    def _execute_newbie_commands(self, player: Player):
        """执行新人指令"""
        try:
            if self.newbie_commands_file.exists():
                commands_content = self.newbie_commands_file.read_text(encoding='utf-8').strip()
                if commands_content:
                    lines = commands_content.split('\n')
                    executed_count = 0
                    for line in lines:
                        line = line.strip()
                        # 跳过空行和注释行
                        if line and not line.startswith('#'):
                            # 替换玩家名称占位符
                            command = line.replace('{player}', player.name)
                            # 执行指令
                            try:
                                self.server.dispatch_command(self.server.command_sender, command)
                                executed_count += 1
                                self.logger.info(f"[ARC Core]Executed newbie command for {player.name}: {command}")
                            except Exception as cmd_e:
                                self.logger.error(f"[ARC Core]Failed to execute command '{command}' for {player.name}: {str(cmd_e)}")
                    
                    if executed_count > 0:
                        self.logger.info(f"[ARC Core]Executed {executed_count} newbie commands for {player.name}")
                else:
                    self.logger.warning(f"[ARC Core]Commands file is empty: {self.newbie_commands_file}")
            else:
                self.logger.warning(f"[ARC Core]Commands file not found: {self.newbie_commands_file}")
        except Exception as e:
            self.logger.error(f"[ARC Core]Failed to execute newbie commands for {player.name}: {str(e)}")

    def on_enable(self) -> None:
        self.register_events(self)
        self.logger.info(f"{ColorFormat.YELLOW}[ARC Core]Plugin enabled!")
        
        # 设置迁移管理器的日志记录器并执行迁移
        self.migration_manager.set_logger(self.logger)
        if not self.migration_manager.migrate_to_xuid():
            self.logger.warning(f"{ColorFormat.RED}[ARC Core]No migration executed.")

        # 初始化公告系统和清道夫系统
        self._load_broadcast_messages()
        self._init_cleaner_system()

        # 启动多线程位置检测系统
        self.start_position_thread()

        # Scheduler tasks
        # 移除了原有的 player_position_listener，现在使用多线程方式
        self.server.scheduler.run_task(self, self.cleanup_expired_teleport_requests, delay=0, period=100)  # 每5秒清理一次过期请求
        
        # 公告系统定时任务
        if self.broadcast_messages:
            broadcast_period = self.broadcast_interval * 20  # 转换为ticks (1秒 = 20 ticks)
            self.server.scheduler.run_task(self, self.send_broadcast_message, delay=broadcast_period, period=broadcast_period)
            self.logger.info(f"[ARC Core]Broadcast system started, interval: {self.broadcast_interval} seconds")

        # 清道夫系统定时任务
        if self.enable_cleaner:
            cleaner_period = self.cleaner_interval * 20  # 转换为ticks
            self.server.scheduler.run_task(self, self.start_cleaner_warning, delay=cleaner_period, period=cleaner_period)
            self.logger.info(f"[ARC Core]Cleaner system started, interval: {self.cleaner_interval} seconds")
        
        # 别踩白块接入
        self.dtwt_plugin = self.server.plugin_manager.get_plugin('arc_dtwt')
        print('[ARC Core]DTWT plugin loaded:', self.dtwt_plugin is not None)

    def on_disable(self) -> None:
        # 停止位置检测线程
        self.stop_position_thread()
        self.logger.info(f"{ColorFormat.YELLOW}[ARC Core]Plugin disabled!")

    def on_command(self, sender: CommandSender, command: Command, args: list[str]) -> bool:
        if command.name == 'updatespawnpos':
            if not isinstance(sender, Player):
                sender.send_message(f'[ARC Core]This command only works for players.')
                return True
            dimension_name = sender.location.dimension.name
            new_spawn_pos = (sender.location.x, sender.location.y, sender.location.z)
            r = self.update_spawn_location(dimension_name, new_spawn_pos)
            if r:
                self.spawn_pos_dict[dimension_name] = new_spawn_pos
                sender.send_message(self.language_manager.GetText('UPDATE_SPAWN_POS_SUCCESSFUL').format(dimension_name, new_spawn_pos))
            else:
                sender.send_message(self.language_manager.GetText('UPDATE_SPAWN_POS_FAILED'))
            return True
        if command.name == "arc":
            if not isinstance(sender, Player):
                sender.send_message(f'[ARC Core]This command only works for players.')
                return True
            self.show_main_menu(sender)
            return True
        if command.name == "suicide":
            if not isinstance(sender, Player):
                sender.send_message(f'[ARC Core]This command only works for players.')
                return True
            self.server.dispatch_command(self.server.command_sender, f'kill {sender.name}')
            self.server.broadcast_message(self.language_manager.GetText('PLAYER_SUICIDE_MESSAGE').format(sender.name))
            return True
        if command.name == "spawn":
            if not isinstance(sender, Player):
                sender.send_message(f'[ARC Core]This command only works for players.')
                return True
            if sender.location.dimension.name in self.spawn_pos_dict:
                self.server.dispatch_command(self.server.command_sender,
                                             f'tp {sender.name} {int(self.spawn_pos_dict[sender.location.dimension.name][0])} {int(self.spawn_pos_dict[sender.location.dimension.name][1])} {int(self.spawn_pos_dict[sender.location.dimension.name][2])}')
                sender.send_message(self.language_manager.GetText('PLAYER_TELEPORTED_TO_SPAWN_HINT'))
            else:
                sender.send_message(self.language_manager.GetText('NO_SPAWN_POSITION_SET_MESSAGE'))
            return True
        if command.name == 'landpos1':
            if not isinstance(sender, Player):
                sender.send_message(f'[ARC Core]This command only works for players.')
                return True
            if not self.if_player_logined(sender):
                self.show_main_menu(sender)
                return True
            self.player_land_pos1[sender.name] = {
                'dimension': sender.location.dimension.name,
                'x': math.floor(sender.location.x),
                'y': math.floor(sender.location.y),
                'z': math.floor(sender.location.z)
            }
            sender.send_message(self.language_manager.GetText('CREATE_NEW_LAND_POS1_SET').format(
                self.player_land_pos1[sender.name]['dimension'],
                (self.player_land_pos1[sender.name]['x'],
                 self.player_land_pos1[sender.name]['y'],
                 self.player_land_pos1[sender.name]['z']))
            )
            return True
        if command.name == 'landpos2':
            if not isinstance(sender, Player):
                sender.send_message(f'[ARC Core]This command only works for players.')
                return True
            if not self.if_player_logined(sender):
                self.show_main_menu(sender)
                return True
            if sender.name not in self.player_land_pos1:
                sender.send_message(self.language_manager.GetText('CREATE_NEW_LAND_POS2_SET_FAIL_POS1_NOT_SET'))
                return True
            pos1 = self.player_land_pos1[sender.name]
            if sender.location.dimension.name != pos1['dimension']:
                sender.send_message(self.language_manager.GetText('CREATE_NEW_LAND_POS2_SET_FAIL_DIMENSION_CHANGED'))
                return True
            x2 = math.floor(sender.location.x)
            y2 = math.floor(sender.location.y)
            z2 = math.floor(sender.location.z)
            self.player_new_land_creation_info[sender.name] = {
                'dimension': pos1['dimension'],
                'min_x': min(pos1['x'], x2),
                'max_x': max(pos1['x'], x2),
                'min_y': min(pos1['y'], y2),
                'max_y': max(pos1['y'], y2),
                'min_z': min(pos1['z'], z2),
                'max_z': max(pos1['z'], z2)
            }
            del self.player_land_pos1[sender.name]
            sender.send_message(self.language_manager.GetText('CREATE_NEW_LAND_POS2_SET').format(
                (x2, y2, z2)))
            self.show_new_land_info(sender)
            self._visualize_pending_land(sender)
            return True
        if command.name == 'landbuy':
            if not isinstance(sender, Player):
                sender.send_message(f'[ARC Core]This command only works for players.')
                return True
            if not self.if_player_logined(sender):
                self.show_main_menu(sender)
                return True
            self._execute_land_buy(sender)
            return True
        return False

    # Event handlers
    @event_handler
    def on_player_join(self, event: PlayerJoinEvent):
        # 在玩家加入时立即初始化玩家数据（基本信息和经济数据）
        success, is_new_player = self.ensure_player_data_initialized(event.player)
        
        # 如果是新玩家，执行新人欢迎功能
        if is_new_player and success:
            # 发送新人欢迎消息
            self._send_newbie_welcome_message(event.player)
            # 执行新人指令
            self._execute_newbie_commands(event.player)
        
        self.server.broadcast_message(self.language_manager.GetText('PLAYER_JOIN_MESSAGE').format(event.player.name))
        self.player_authentication_state[event.player.name] = False
        event.player.send_message(self.language_manager.GetText('PLAYER_JOIN_HINT'))

        # 登录时提示可领取的邀请奖励次数
        try:
            player_xuid = str(event.player.xuid)
            pending_info = self.database_manager.query_one(
                "SELECT pending_invite_reward_times FROM player_basic_info WHERE xuid = ?",
                (player_xuid,)
            )
            if pending_info is not None:
                try:
                    pending_times = int(pending_info.get('pending_invite_reward_times', 0) or 0)
                except (ValueError, TypeError):
                    pending_times = 0
                if pending_times > 0:
                    event.player.send_message(
                        self.language_manager.GetText('INVITE_REWARD_PENDING_HINT').format(pending_times)
                    )
        except Exception as e:
            self.logger.error(f"{ColorFormat.RED}[ARC Core]Check pending invite rewards on join error: {str(e)}")

    @event_handler
    def on_player_quit(self, event: PlayerQuitEvent):
        self.server.broadcast_message(self.language_manager.GetText('PLAYER_QUIT_MESSAGE').format(event.player.name))
        self.player_authentication_state[event.player.name] = False
        
        # 线程安全地清理玩家领地位置记录
        with self.position_thread_lock:
            if event.player.name in self.player_in_land_id_dict:
                del self.player_in_land_id_dict[event.player.name]
        
        # 清理死亡位置记录
        if event.player.name in self.player_death_locations:
            del self.player_death_locations[event.player.name]

    @event_handler
    def on_block_break(self, event: BlockBreakEvent):
        if event.player.is_op:
            return

        if self.dtwt_plugin is not None and self.dtwt_plugin.api_judge_if_start_block(event.block.location.x, event.block.location.y, event.block.location.z, event.block.dimension.name):
            # print('DTWT block break, ignore')
            return

        if not self.land_operation_check(event.player, event.block.location.dimension.name,
                                    (event.block.location.x, event.block.location.y, event.block.location.z)):
            event.is_cancelled = True
        if not self.spawn_protect_check(event.player, event.block.location.dimension.name,
                                    (event.block.location.x, event.block.location.y, event.block.location.z)):
            event.is_cancelled = True
        return

    @event_handler
    def on_block_place(self, event: BlockPlaceEvent):
        if event.player.is_op:
            return
        print(event.player.name, event.block.location.x, event.block.location.y, event.block.location.z, event.block.dimension.name)
        if not self.land_operation_check(event.player, event.block.location.dimension.name,
                                    (event.block.location.x, event.block.location.y, event.block.location.z)):
            event.is_cancelled = True
        if not self.spawn_protect_check(event.player, event.block.location.dimension.name,
                                    (event.block.location.x, event.block.location.y, event.block.location.z)):
            event.is_cancelled = True
        return
    
    @event_handler
    def on_actor_death(self, event: PlayerDeathEvent):
        # 记录玩家死亡位置
        self.player_death_locations[event.player.name] = {
            'dimension': event.player.location.dimension.name,
            'x': event.player.location.x,
            'y': event.player.location.y,
            'z': event.player.location.z
        }
        event.player.send_message(self.language_manager.GetText('DEATH_LOCATION_RECORDED'))
        
        # 发送死亡播报
        self._send_death_broadcast(event)

    @event_handler
    def on_player_interact(self, event: PlayerInteractEvent):
        """处理玩家交互事件，保护领地免受非法交互"""
        try:
            # 玩家或OP判定
            if not hasattr(event, 'player') or event.player is None:
                return
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
                    hasattr(block, 'dimension') and block.dimension is not None and hasattr(block.dimension, 'name') and
                    self.dtwt_plugin.api_judge_if_start_block(block_location.x, block_location.y, block_location.z, block.dimension.name)
                ):
                    return
            except Exception:
                # 外部插件异常不影响主流程
                pass

            # 维度与坐标
            if hasattr(block, 'dimension') and block.dimension is not None and hasattr(block.dimension, 'name'):
                dimension = block.dimension.name
            else:
                # 回退到玩家维度
                dimension = event.player.location.dimension.name if hasattr(event.player, 'location') and event.player.location else ''

            pos = (block_location.x, block_location.y, block_location.z)

            # 检查是否在领地内且不是领地主人
            if not self.land_interact_check(event.player, dimension, pos):
                event.is_cancelled = True
        except Exception as e:
            pass
            # self.logger.error(f"[ARC Core] on_player_interact error: {str(e)}")

    @event_handler
    def on_actor_explode(self, event: ActorExplodeEvent):
        """处理爆炸事件，保护领地免受爆炸伤害"""
        try:
            explosion_location = event.location
            dimension = explosion_location.dimension.name
            
            # 检查爆炸位置是否在任何领地内
            land_id = self.get_land_at_pos(dimension, math.floor(explosion_location.x), math.floor(explosion_location.z))
            if land_id is not None:
                land_info = self.get_land_info(land_id)
                if land_info and not land_info.get('allow_explosion', False):
                    # 如果领地不允许爆炸，则取消爆炸事件
                    event.is_cancelled = True
                    return
                    
            # 检查爆炸影响的方块是否在领地内
            filtered_blocks = []
            for block in event.block_list:
                block_land_id = self.get_land_at_pos(dimension, math.floor(block.location.x), math.floor(block.location.z))
                if block_land_id is not None:
                    block_land_info = self.get_land_info(block_land_id)
                    if block_land_info and block_land_info.get('allow_explosion', False):
                        # 如果该领地允许爆炸，保留这个方块在爆炸列表中
                        filtered_blocks.append(block)
                    # 如果不允许爆炸，则不添加到列表中（移除）
                else:
                    # 不在领地内的方块保持原样
                    filtered_blocks.append(block)
            
            # 更新爆炸影响的方块列表
            event.block_list = filtered_blocks
            
        except Exception as e:
            self.logger.error(f"Handle actor explode event error: {str(e)}")

    @event_handler
    def on_player_interact_actor(self, event: PlayerInteractActorEvent):
        """处理玩家与生物交互事件，保护领地内生物免受非法交互"""
        # OP玩家跳过检查
        if event.player.is_op:
            return
        
        # 获取生物位置
        actor_location = event.actor.location
        dimension = actor_location.dimension.name
        ax = math.floor(actor_location.x)
        ay = math.floor(actor_location.y)
        az = math.floor(actor_location.z)

        # 检查生物是否在领地内
        land_id = self.get_land_at_pos(dimension, ax, az, ay)
        if land_id is not None:
            # 先检查子领地权限
            sub_land_id = self.get_sub_land_at_pos(land_id, ax, ay, az)
            if sub_land_id is not None:
                sub_info = self.get_sub_land_info(sub_land_id)
                if sub_info and self._check_sub_land_permission(event.player, sub_info):
                    return
            land_info = self.get_land_info(land_id)
            if land_info and not land_info.get('allow_actor_interaction', False):
                # 检查玩家是否有权限（领地主人或授权用户）
                if not self._check_land_permission(event.player, land_info):
                    event.is_cancelled = True
                    event.player.send_message(self.language_manager.GetText('LAND_ACTOR_INTERACTION_DENIED'))

    @event_handler
    def on_actor_damage(self, event: ActorDamageEvent):
        """处理生物受伤事件，保护领地内生物免受攻击"""
        # 检查攻击者是否为玩家
        attacker = event.damage_source.actor
        if attacker is None or attacker.type != "minecraft:player":
            return

        # 如果玩家是op则不判断
        if attacker.is_op:
            return

        # 获取被攻击生物位置
        actor_location = event.actor.location
        dimension = actor_location.dimension.name
        ax = math.floor(actor_location.x)
        ay = math.floor(actor_location.y)
        az = math.floor(actor_location.z)

        # 检查生物是否在领地内
        land_id = self.get_land_at_pos(dimension, ax, az, ay)
        if land_id is not None:
            # 先检查子领地权限：有子领地权限则直接放行
            sub_land_id = self.get_sub_land_at_pos(land_id, ax, ay, az)
            if sub_land_id is not None:
                sub_info = self.get_sub_land_info(sub_land_id)
                if sub_info and self._check_sub_land_permission(attacker, sub_info):
                    return
            land_info = self.get_land_info(land_id)
            if not land_info:
                return
            # 公共领地：禁止生物伤害时一律拦截；开放生物伤害时仅保护白名单生物
            if self.is_public_land(land_id):
                if not land_info.get('allow_actor_damage', False):
                    event.is_cancelled = True
                    attacker.send_message(self.language_manager.GetText('LAND_ACTOR_DAMAGE_DENIED'))
                    return
                protected = self._get_public_land_protected_entities()
                # print("entity",event.actor.type, "public land protected entities", protected)
                damaged_entity_type = event.actor.type
                if damaged_entity_type and damaged_entity_type in protected:
                    event.is_cancelled = True
                    attacker.send_message(self.language_manager.GetText('LAND_ACTOR_DAMAGE_DENIED'))
                return
            # 非公共领地：未开放生物伤害时仅主人/授权用户可造成伤害
            if not land_info.get('allow_actor_damage', False):
                attacker_xuid = self.get_player_xuid_by_name(attacker.name)
                if attacker_xuid is None:
                    event.is_cancelled = True
                    attacker.send_message(self.language_manager.GetText('LAND_ACTOR_DAMAGE_DENIED'))
                    return
                owner_xuid = land_info['owner_xuid']
                shared_users = land_info.get('shared_users', [])
                if owner_xuid != attacker_xuid and attacker_xuid not in shared_users:
                    event.is_cancelled = True
                    attacker.send_message(self.language_manager.GetText('LAND_ACTOR_DAMAGE_DENIED'))

    def _check_sub_land_permission(self, player: Player, sub_land_info: dict) -> bool:
        """检查玩家是否拥有子领地权限（主人或授权用户）"""
        try:
            owner_xuid = sub_land_info.get('owner_xuid', '')
            shared_users = sub_land_info.get('shared_users', [])
            return owner_xuid == str(player.xuid) or str(player.xuid) in shared_users
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
            owner_xuid = land_info['owner_xuid']
            if owner_xuid == self.PUBLIC_LAND_OWNER_XUID:
                return player.is_op
            shared_users = land_info.get('shared_users', [])
            return owner_xuid == str(player.xuid) or str(player.xuid) in shared_users
        except Exception as e:
            self.logger.error(f"Check land permission error: {str(e)}")
            return False

    def land_operation_check(self, player: Player, dimension: str, pos: tuple):
        x, y, z = pos[0], (pos[1] if len(pos) > 1 else None), pos[2]
        land_id = self.get_land_at_pos(dimension, x, z, y)
        if land_id is not None:
            # 先检查子领地权限
            if y is not None:
                sub_land_id = self.get_sub_land_at_pos(land_id, int(x), int(y), int(z))
                if sub_land_id is not None:
                    sub_info = self.get_sub_land_info(sub_land_id)
                    if sub_info and self._check_sub_land_permission(player, sub_info):
                        return True
            # 回落到父领地权限检查
            land_info = self.get_land_info(land_id)
            if not land_info:
                return True
            owner_xuid = land_info['owner_xuid']
            if owner_xuid == self.PUBLIC_LAND_OWNER_XUID:
                if not player.is_op:
                    player.send_message(self.language_manager.GetText('LAND_PROTECT_HINT').format(self.language_manager.GetText('PUBLIC_LAND_NAME')))
                    return False
                return True
            shared_users = land_info['shared_users']
            if owner_xuid != str(player.xuid) and str(player.xuid) not in shared_users:
                player.send_message(self.language_manager.GetText('LAND_PROTECT_HINT').format(self.get_player_name_by_xuid(owner_xuid)))
                return False
        return True

    def land_interact_check(self, player: Player, dimension: str, pos: tuple):
        """检查玩家是否有权限在领地内进行方块互动"""
        x, y, z = pos[0], (pos[1] if len(pos) > 1 else None), pos[2]
        land_id = self.get_land_at_pos(dimension, x, z, y)
        if land_id is not None:
            # 先检查子领地权限
            if y is not None:
                sub_land_id = self.get_sub_land_at_pos(land_id, int(x), int(y), int(z))
                if sub_land_id is not None:
                    sub_info = self.get_sub_land_info(sub_land_id)
                    if sub_info and self._check_sub_land_permission(player, sub_info):
                        return True
            # 回落到父领地权限检查
            land_info = self.get_land_info(land_id)
            if not land_info:
                return True
            if land_info.get('allow_public_interact', False):
                return True
            owner_xuid = land_info['owner_xuid']
            if owner_xuid == self.PUBLIC_LAND_OWNER_XUID:
                if not player.is_op:
                    player.send_message(self.language_manager.GetText('LAND_PROTECT_HINT').format(self.language_manager.GetText('PUBLIC_LAND_NAME')))
                    return False
                return True
            shared_users = land_info['shared_users']
            if owner_xuid != str(player.xuid) and str(player.xuid) not in shared_users:
                player.send_message(self.language_manager.GetText('LAND_PROTECT_HINT').format(self.get_player_name_by_xuid(owner_xuid)))
                return False
        return True
    
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
                        dimension = player.location.dimension.name
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
                                
                                # 进入新领地时发送提示
                                if land_id is not None:
                                    try:
                                        new_land_name = self.get_land_name(land_id)
                                        land_owner = self.get_land_display_owner_name(land_id)
                                        
                                        # 创建固定参数的闭包，避免循环变量捕获问题
                                        def create_land_message_sender(target_player, land_name, owner_name, land_id):
                                            def send_land_message():
                                                try:
                                                    # 发送领地信息字幕（公共领地只显示「公共领地」，不显示「领主：公共领地」）
                                                    if self.is_public_land(land_id):
                                                        subtitle = self.language_manager.GetText('PUBLIC_LAND_NAME')
                                                    else:
                                                        subtitle = self.language_manager.GetText('STEP_IN_LAND_SUBTITLE').format(owner_name)
                                                    target_player.send_popup(
                                                        f'{self.language_manager.GetText("STEP_IN_LAND_TITLE").format(land_name)}\n{subtitle}'
                                                    )
                                                    # 显示领地边界粒子效果
                                                    land_info = self.get_land_info(land_id)
                                                    if land_info:
                                                        self.display_land_particle_boundary(target_player, land_info)
                                                    
                                                except Exception as e:
                                                    self.logger.warning(f"[ARC Core]Failed to send land message to {target_player.name}: {str(e)}")
                                            return send_land_message
                                        
                                        # 创建消息发送器，固定当前玩家和领地信息
                                        message_sender = create_land_message_sender(player, new_land_name, land_owner, land_id)
                                        
                                        # 在主线程中执行UI操作
                                        if hasattr(self.server, 'scheduler'):
                                            self.server.scheduler.run_task(self, message_sender, delay=0)
                                        
                                    except Exception as e:
                                        self.logger.warning(f"[ARC Core]Error processing land change for {player.name}: {str(e)}")
                                        
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
        self.init_economy_table()
        self._upgrade_player_economy_table_to_float()
        self.init_land_tables()
        self.init_sub_land_table()
        self.init_teleport_tables()

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

    def _upgrade_player_basic_table(self) -> bool:
        """
        升级玩家基本信息表结构
        """
        try:
            success = True
            # 检查并添加 is_op 列
            if not self._add_column_if_not_exists('player_basic_info', 'is_op', 'INTEGER DEFAULT 0'):
                success = False
            
            # 检查并添加 remaining_free_land_blocks 列
            default_free_blocks = self.setting_manager.GetSetting('DEFAULT_FREE_LAND_BLOCKS') or '100'
            if not self._add_column_if_not_exists('player_basic_info', 'remaining_free_land_blocks', f'INTEGER DEFAULT {default_free_blocks}'):
                success = False

            # 检查并添加 inviter_xuid 列（邀请人 XUID，允许为空）
            if not self._add_column_if_not_exists('player_basic_info', 'inviter_xuid', 'TEXT'):
                success = False

            # 检查并添加 pending_invite_reward_times 列（待领取邀请奖励次数，默认为 0）
            if not self._add_column_if_not_exists('player_basic_info', 'pending_invite_reward_times', 'INTEGER DEFAULT 0'):
                success = False
            
            return success
        except Exception as e:
            # 在__init__期间不能使用self.logger，使用print代替
            print(f"[ARC Core]Upgrade player basic table error: {str(e)}")
            return False

    def init_player_basic_table(self) -> bool:
        """初始化玩家基本信息表"""
        # 从配置文件获取默认免费领地格子数
        default_free_blocks = self.setting_manager.GetSetting('DEFAULT_FREE_LAND_BLOCKS') or '100'
        
        fields = {
            'uuid': 'TEXT PRIMARY KEY',  # 玩家UUID作为主键
            'xuid': 'TEXT NOT NULL',  # 玩家XUID
            'name': 'TEXT NOT NULL',  # 玩家名称
            'password': 'TEXT',  # 玩家密码(加密后的)，允许为NULL
            'is_op': 'INTEGER DEFAULT 0',  # 玩家是否为OP，默认为0(false)
            'remaining_free_land_blocks': f'INTEGER DEFAULT {default_free_blocks}',  # 剩余免费领地格子数
            'inviter_xuid': 'TEXT',  # 邀请人 XUID，允许为空
            'pending_invite_reward_times': 'INTEGER DEFAULT 0'  # 待领取邀请奖励次数
        }
        result = self.database_manager.create_table('player_basic_info', fields)
        
        # 对于已存在的表，执行升级操作
        if result:
            self._upgrade_player_basic_table()
        
        return result

    def _hash_password(self, password: str) -> str:
        """
        对密码进行加密
        :param password: 原始密码
        :return: 加密后的密码
        """
        # 使用SHA-256进行加密
        return hashlib.sha256(password.encode()).hexdigest()

    def init_player_basic_info(self, player: Player) -> bool:
        """
        初始化玩家基本信息
        :param player: 玩家对象
        :return: 是否初始化成功
        """
        try:
            # 获取默认免费领地格子数
            default_free_blocks = int(self.setting_manager.GetSetting('DEFAULT_FREE_LAND_BLOCKS') or '100')
            
            player_data = {
                'uuid': str(player.unique_id),
                'xuid': str(player.xuid),
                'name': player.name,
                'password': None,  # 初始密码为空
                'is_op': 1 if player.is_op else 0,  # 根据玩家当前OP状态设置
                'remaining_free_land_blocks': default_free_blocks,  # 设置默认免费格子数
                'inviter_xuid': None,  # 初始无邀请人
                'pending_invite_reward_times': 0  # 初始无待领取邀请奖励
            }
            return self.database_manager.insert('player_basic_info', player_data)
        except Exception as e:
            self._safe_log('error', f"{ColorFormat.RED}[ARC Core]Init player basic info error: {str(e)}")
            return False

    def init_player_economy_info(self, player: Player) -> bool:
        """
        初始化玩家经济信息
        :param player: 玩家对象
        :return: 是否初始化成功
        """
        try:
            player_xuid = str(player.xuid)
            # 检查玩家经济数据是否已存在
            existing_data = self.database_manager.query_one(
                "SELECT xuid FROM player_economy WHERE xuid = ?",
                (player_xuid,)
            )
            if existing_data:
                return True  # 已存在，无需重复创建

            # 获取初始金钱设置（支持小数，精确到分）
            player_init_money_num = self.setting_manager.GetSetting('PLAYER_INIT_MONEY_NUM')
            try:
                init_money = self._round_money(float(player_init_money_num))
            except (ValueError, TypeError):
                init_money = 0.0

            # 创建经济数据
            economy_data = {
                'xuid': player_xuid,
                'money': init_money
            }
            return self.database_manager.insert('player_economy', economy_data)
        except Exception as e:
            self._safe_log('error', f"{ColorFormat.RED}[ARC Core]Init player economy info error: {str(e)}")
            return False

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
                "SELECT xuid FROM player_basic_info WHERE xuid = ?",
                (player_xuid,)
            )
            if not basic_info:
                is_new_player = True  # 没有基本信息说明是新玩家
                if not self.init_player_basic_info(player):
                    self._safe_log('error', f"{ColorFormat.RED}[ARC Core]Failed to init basic info for player {player.name}")
                    success = False
                else:
                    self._safe_log('info', f"{ColorFormat.GREEN}[ARC Core]Initialized basic info for new player {player.name}")

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

    def get_player_basic_info(self, player: Player) -> Optional[Dict[str, Any]]:
        """
        获取玩家基本信息
        :param player: 玩家对象
        :return: 玩家信息字典或None(如果发生错误)
        """
        try:
            result = self.database_manager.query_one(
                "SELECT * FROM player_basic_info WHERE xuid = ?",
                (str(player.xuid),)
            )
            if result is None:
                # 玩家第一次进入服务器，初始化信息
                if self.init_player_basic_info(player):
                    return {
                        'uuid': str(player.unique_id),
                        'xuid': str(player.xuid),
                        'name': player.name,
                        'password': None
                    }
                return None
            return result
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
        更新玩家OP状态
        :param player: 玩家对象
        :return: 是否更新成功
        """
        try:
            current_op_status = 1 if player.is_op else 0
            
            # 检查当前数据库中的OP状态
            current_info = self.database_manager.query_one(
                "SELECT is_op FROM player_basic_info WHERE xuid = ?",
                (str(player.xuid),)
            )
            
            if current_info is not None:
                stored_op_status = current_info.get('is_op', 0)
                if stored_op_status != current_op_status:
                    # OP状态发生变化，更新数据库
                    success = self.database_manager.update(
                        table='player_basic_info',
                        data={'is_op': current_op_status},
                        where='xuid = ?',
                        params=(str(player.xuid),)
                    )
                    if success:
                        status_text = "OP" if current_op_status else "非OP"
                        self._safe_log('info', f"{ColorFormat.GREEN}[ARC Core]Updated player OP status: {player.name} -> {status_text}")
                    return success
            return True  # 状态未变化或记录不存在，返回成功
        except Exception as e:
            self._safe_log('error', f"{ColorFormat.RED}[ARC Core]Update player OP status error: {str(e)}")
            return False

    def get_offline_player_op_status(self, player_name: str) -> Optional[bool]:
        """
        获取离线玩家的OP状态
        :param player_name: 玩家名称
        :return: OP状态，如果玩家不存在则返回None
        """
        try:
            result = self.database_manager.query_one(
                "SELECT is_op FROM player_basic_info WHERE name = ?",
                (player_name,)
            )
            if result is not None:
                return bool(result['is_op'])
            return None
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
                "SELECT is_op FROM player_basic_info WHERE xuid = ?",
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
                "SELECT is_op FROM player_basic_info WHERE uuid = ?",
                (player_uuid,)
            )
            if result is not None:
                return bool(result['is_op'])
            return None
        except Exception as e:
            self._safe_log('error', f"{ColorFormat.RED}[ARC Core]Get offline player OP status by UUID error: {str(e)}")
            return None

    def get_player_name_by_xuid(self, player_xuid: str) -> Optional[str]:
        """
        通过XUID获取玩家名称
        :param player_xuid: 玩家XUID字符串
        :return: 玩家名称，如果未找到则返回None
        """
        try:
            result = self.database_manager.query_one(
                "SELECT name FROM player_basic_info WHERE xuid = ?",
                (player_xuid,)
            )
            return result['name'] if result else None
        except Exception as e:
            self.logger.error(f"{ColorFormat.RED}[ARC Core]Get player name by XUID error: {str(e)}")
            return None
    
    def get_player_xuid_by_name(self, player_name: str) -> Optional[str]:
        """
        通过玩家名称获取XUID
        :param player_name: 玩家名称
        :return: 玩家XUID字符串，如果未找到则返回None
        """
        try:
            result = self.database_manager.query_one(
                "SELECT xuid FROM player_basic_info WHERE name = ?",
                (player_name,)
            )
            return result['xuid'] if result else None
        except Exception as e:
            self.logger.error(f"{ColorFormat.RED}[ARC Core]Get player XUID by name error: {str(e)}")
            return None

    # Spawn protect
    def init_spawn_locations_table(self) -> bool:
        """初始化出生点表格"""
        fields = {
            'dimension': 'TEXT PRIMARY KEY',  # 维度名称作为主键
            'spawn_x': 'INTEGER NOT NULL',
            'spawn_y': 'INTEGER NOT NULL',
            'spawn_z': 'INTEGER NOT NULL'
        }
        return self.database_manager.create_table('spawn_locations', fields)

    def update_spawn_location(self, dimension: str, coordinates: tuple) -> bool:
        """
        更新出生地信息
        :param db: 数据库管理器
        :param dimension: 维度名称
        :param coordinates: (x, y, z) 坐标元组
        :return: 是否更新成功
        """
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
        if dimension_name in self.spawn_pos_dict:
            if math.fabs(pos_x - self.spawn_pos_dict[dimension_name][0]) <= self.spawn_protect_range and \
                    math.fabs(pos_z - self.spawn_pos_dict[dimension_name][2]) <= self.spawn_protect_range:
                return False
        return True

    # UI Main menu
    def show_main_menu(self, player: Player):
        if not self.if_player_logined(player):
            player_basic_info = self.get_player_basic_info(player)
            if player_basic_info is None:
                player.send_message('[ARC Core]An error occured, please contact server hoster!')
                return
            self.update_player_name(player)
            if player_basic_info['password'] is None:
                self.show_register_panel(player)
            else:
                self.show_login_panel(player)
        else:
            arc_menu = ActionForm(
                title=self.language_manager.GetText('MAIN_MENU_TITLE'),
            )
            arc_menu.add_button(self.language_manager.GetText('BANK_MENU_NAME'), on_click=self.show_bank_main_menu)
            arc_menu.add_button(self.language_manager.GetText('TELEPORT_MENU_NAME'), on_click=self.show_teleport_menu)
            arc_menu.add_button(self.language_manager.GetText('LAND_MENU_NAME'), on_click=self.show_land_main_menu)
            arc_menu.add_button(self.language_manager.GetText('MAIN_MENU_MY_INFO_NAME'), on_click=self.show_my_info_panel)
            if self.server.plugin_manager.get_plugin('ushop'):
                arc_menu.add_button(self.language_manager.GetText('SHOP_MENU_NAME'), on_click=self.show_shop_menu)
            if self.server.plugin_manager.get_plugin('arc_button_shop'):
                arc_menu.add_button(self.language_manager.GetText('BUTTON_SHOP_MENU_NAME'), on_click=self.show_button_shop_menu)
            if self.server.plugin_manager.get_plugin('arc_dtwt'):
                arc_menu.add_button(self.language_manager.GetText('DTWT_MENU_NAME'), on_click=self.show_dtwt_panel)
            if self.server.plugin_manager.get_plugin('up_and_down'):
                arc_menu.add_button(self.language_manager.GetText('STOCK_MARKET_NAME'), on_click=self.show_stock_ui)
            if player.is_op:
                arc_menu.add_button(self.language_manager.GetText('OP_PANEL_NAME'), on_click=self.show_op_main_panel)
            arc_menu.add_button(self.language_manager.GetText('SUICIDE_FUNC_BUTTON'), on_click=self.execute_suicide)
            arc_menu.on_close = None
            player.send_form(arc_menu)
    
    def execute_suicide(self, player: Player):
        player.perform_command('suicide')

    # Player info & invite system UI
    def show_my_info_panel(self, player: Player):
        """显示玩家自己的信息面板"""
        player_basic_info = self.get_player_basic_info(player)
        if player_basic_info is None:
            player.send_message(self.language_manager.GetText('SYSTEM_ERROR'))
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
            on_close=self.show_main_menu
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

        # 返回主菜单
        my_info_panel.add_button(
            self.language_manager.GetText('RETURN_BUTTON_TEXT'),
            on_click=self.show_main_menu
        )

        player.send_form(my_info_panel)

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
            except Exception:
                self.show_fill_inviter_panel(player, self.language_manager.GetText('FILL_INVITER_FAIL_SYSTEM_ERROR'))
                return

            if len(data) == 0 or not str(data[0]).strip():
                self.show_fill_inviter_panel(player, self.language_manager.GetText('FILL_INVITER_FAIL_PLAYER_NOT_FOUND'))
                return

            inviter_name_input = str(data[0]).strip()
            player_xuid = str(player.xuid)

            # 再次检查自己是否已经填写过邀请人
            basic_info = self.database_manager.query_one(
                "SELECT inviter_xuid FROM player_basic_info WHERE xuid = ?",
                (player_xuid,)
            )
            if basic_info is None:
                player.send_message(self.language_manager.GetText('SYSTEM_ERROR'))
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
                player.send_message(self.language_manager.GetText('FILL_INVITER_FAIL_SYSTEM_ERROR'))
                self.show_my_info_panel(player)
                return

            # 给自己发放一次邀请奖励
            self.grant_invite_reward_to_player(player, 1)

            # 给邀请人累加一份待领取奖励
            self.add_pending_invite_rewards(inviter_xuid, 1)

            player.send_message(self.language_manager.GetText('FILL_INVITER_SUBMIT_SUCCESS').format(inviter_name_input))

            inviter_player = self.server.get_player(inviter_name_input)
            if inviter_player is not None:
                inviter_player.send_message(self.language_manager.GetText('INVITE_REWARD_GIVE_INVITER_HINT').format(player.name))

            self.show_my_info_panel(player)

        fill_inviter_panel = ModalForm(
            title=panel_title,
            controls=[inviter_input],
            on_close=self.show_my_info_panel,
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
                on_close=self.show_my_info_panel
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
            on_close=self.show_my_info_panel
        )
        player.send_form(result_panel)

    # Register and login
    def login_successfully(self, player: Player):
        self.player_authentication_state[player.name] = True
        self.show_main_menu(player) # 登录成功后自动弹出主菜单

    def show_register_panel(self, player: Player, hint_message=None):
        password_input = TextInput(
            label=self.language_manager.GetText('REGISTER_PANEL_PASSWORD_INPUT_LABEL'),
            placeholder=self.language_manager.GetText('REGISTER_PANEL_PASSWORD_INPUT_PLACEHOLDER')
        )
        panel_title = self.language_manager.GetText('REGISTER_PANEL_TITLE') if hint_message is None else hint_message

        def try_register(player: Player, json_str: str):
            data = json.loads(json_str)
            if len(data) == 0:
                self.show_register_panel(player, self.language_manager.GetText('REGISTER_FAIL_PASSWORD_NOT_INPUT'))
            else:
                r = self.set_player_password(player, data[0])
                if r:
                    player.send_message(self.language_manager.GetText('REGISTER_SUCCESS'))
                    self.login_successfully(player)
                else:
                    player.send_message(self.language_manager.GetText('REGISTER_FAIL'))

        register_panel = ModalForm(
            title=panel_title,
            controls=[password_input],
            on_close=self.show_register_panel,
            on_submit=try_register
        )
        player.send_form(register_panel)

    def show_login_panel(self, player: Player, hint_message=None):
        password_input = TextInput(
            label=self.language_manager.GetText('LOGIN_PANEL_PASSWORD_INPUT_LABEL'),
            placeholder=self.language_manager.GetText('LOGIN_PANEL_PASSWORD_INPUT_PLACEHOLDER')
        )
        panel_title = self.language_manager.GetText('LOGIN_PANEL_TITLE') if hint_message is None else hint_message

        def try_login(player: Player, json_str: str):
            data = json.loads(json_str)
            if len(data) == 0:
                # 密码未输入，重新显示登录面板并提示
                self.show_login_panel(player, self.language_manager.GetText('LOGIN_FAIL_PASSWORD_NOT_INPUT'))
            else:
                # 验证密码
                if self.verify_player_password(player, data[0]):
                    player.send_message(self.language_manager.GetText('LOGIN_SUCCESS'))
                    self.login_successfully(player)
                else:
                    # 密码错误，重新显示登录面板
                    self.show_login_panel(player, self.language_manager.GetText('LOGIN_FAIL_WRONG_PASSWORD'))

        login_panel = ModalForm(
            title=panel_title,
            controls=[password_input],
            on_close=self.show_login_panel,
            on_submit=try_login
        )
        player.send_form(login_panel)

    def if_player_logined(self, player: Player):
        if not player.name in self.player_authentication_state:
            self.player_authentication_state[player.name] = False
        return self.player_authentication_state[player.name]

    # Economy system（金钱以 float 存储，精确到分，两位小数）
    def _round_money(self, value: float) -> float:
        """将金额四舍五入到分（两位小数）"""
        return round(float(value), 2)

    def _format_money_display(self, value: float) -> str:
        """格式化金额用于界面显示（始终两位小数）"""
        return "%.2f" % self._round_money(value)

    def init_economy_table(self) -> bool:
        """初始化经济系统表格（money 使用 REAL，支持小数到分）"""
        fields = {
            'xuid': 'TEXT PRIMARY KEY',  # 玩家XUID字符串作为主键
            'money': 'REAL NOT NULL DEFAULT 0'  # 玩家金钱数量，支持小数，精确到分
        }
        return self.database_manager.create_table('player_economy', fields)

    def _upgrade_player_economy_table_to_float(self) -> bool:
        """若 player_economy 表中 money 列为 INTEGER，则迁移为 REAL（仅执行一次）"""
        try:
            if not self.database_manager.table_exists('player_economy'):
                return True
            columns_info = self.database_manager.query_all("PRAGMA table_info(player_economy)")
            money_type = None
            for col in columns_info:
                if col.get('name') == 'money':
                    money_type = str(col.get('type', '')).upper()
                    break
            if money_type != 'INTEGER':
                return True
            # 创建新表（REAL），复制数据，替换旧表
            self.database_manager.execute(
                "CREATE TABLE player_economy_new (xuid TEXT PRIMARY KEY, money REAL NOT NULL DEFAULT 0)"
            )
            self.database_manager.execute(
                "INSERT INTO player_economy_new (xuid, money) SELECT xuid, CAST(money AS REAL) FROM player_economy"
            )
            self.database_manager.execute("DROP TABLE player_economy")
            self.database_manager.execute("ALTER TABLE player_economy_new RENAME TO player_economy")
            print("[ARC Core]Upgraded player_economy money column to REAL (float).")
            return True
        except Exception as e:
            print(f"[ARC Core]Upgrade player_economy to float error: {str(e)}")
            return False

    def _set_player_money_by_name(self, player_name: str, amount: float) -> bool:
        """
        设置玩家金钱（底层函数，基于玩家名称）
        :param player_name: 玩家名称
        :param amount: 金钱数量（支持小数，精确到分）
        :return: 是否设置成功
        """
        try:
            amount = self._round_money(amount)

            player_xuid = self.get_player_xuid_by_name(player_name)
            if not player_xuid:
                self.logger.error(f"{ColorFormat.RED}[ARC Core]Player {player_name} not found")
                return False

            return self.database_manager.update(
                table='player_economy',
                data={'money': amount},
                where='xuid = ?',
                params=(player_xuid,)
            )
        except Exception as e:
            self.logger.error(f"{ColorFormat.RED}[ARC Core]Set player money error: {str(e)}")
            return False

    def _set_player_money(self, player: Player, amount: float) -> bool:
        """
        设置玩家金钱（Player对象封装器）
        :param player: 玩家对象
        :param amount: 金钱数量（支持小数，精确到分）
        :return: 是否设置成功
        """
        return self._set_player_money_by_name(player.name, amount)

    def get_player_money_by_name(self, player_name: str) -> float:
        """
        获取玩家金钱（底层函数，基于玩家名称）
        :param player_name: 玩家名称
        :return: 玩家金钱数量（精确到分）
        """
        try:
            player_xuid = self.get_player_xuid_by_name(player_name)
            if not player_xuid:
                return 0.0

            result = self.database_manager.query_one(
                "SELECT money FROM player_economy WHERE xuid = ?",
                (player_xuid,)
            )
            if result is None:
                player_init_money_num = self.setting_manager.GetSetting('PLAYER_INIT_MONEY_NUM')
                try:
                    init_money = self._round_money(float(player_init_money_num))
                except (ValueError, TypeError):
                    init_money = 0.0
                self.database_manager.insert(
                    'player_economy',
                    {'xuid': player_xuid, 'money': init_money}
                )
                return init_money
            return self._round_money(result['money'])
        except Exception as e:
            self.logger.error(f"{ColorFormat.RED}[ARC Core]Get player money error: {str(e)}")
            return 0.0

    def get_player_money(self, player: Player) -> float:
        """
        获取玩家金钱（Player对象封装器）
        :param player: 玩家对象
        :return: 玩家金钱数量（精确到分）
        """
        return self.get_player_money_by_name(player.name)

    def increase_player_money_by_name(self, player_name: str, amount: float, notify: bool = True) -> bool:
        """
        增加玩家金钱（底层函数，基于玩家名称）
        :param player_name: 玩家名称
        :param amount: 增加的金钱数量（支持小数，精确到分）
        :param notify: 是否通知在线玩家
        :return: 是否操作成功
        """
        try:
            amount = abs(self._round_money(amount))
            if amount <= 0:
                return True
            current_money = self.get_player_money_by_name(player_name)
            new_money = self._round_money(current_money + amount)
            success = self._set_player_money_by_name(player_name, new_money)

            if success and notify:
                online_player = self.server.get_player(player_name)
                if online_player is not None:
                    online_player.send_message(
                        self.language_manager.GetText('MONEY_ADD_HINT').format(
                            self._format_money_display(amount),
                            self._format_money_display(new_money)
                        )
                    )

            return success
        except Exception as e:
            self.logger.error(f"{ColorFormat.RED}[ARC Core]Add player money error: {str(e)}")
            return False

    def decrease_player_money_by_name(self, player_name: str, amount: float, notify: bool = True) -> bool:
        """
        减少玩家金钱（底层函数，基于玩家名称）
        :param player_name: 玩家名称
        :param amount: 减少的金钱数量（支持小数，精确到分）
        :param notify: 是否通知在线玩家
        :return: 是否操作成功
        """
        try:
            amount = abs(self._round_money(amount))
            if amount <= 0:
                return True
            current_money = self.get_player_money_by_name(player_name)
            new_money = self._round_money(current_money - amount)
            success = self._set_player_money_by_name(player_name, new_money)

            if success and notify:
                online_player = self.server.get_player(player_name)
                if online_player is not None:
                    online_player.send_message(
                        self.language_manager.GetText('MONEY_REDUCE_HINT').format(
                            self._format_money_display(amount),
                            self._format_money_display(new_money)
                        )
                    )

            return success
        except Exception as e:
            self.logger.error(f"{ColorFormat.RED}[ARC Core]Remove player money error: {str(e)}")
            return False

    def change_player_money_by_name(self, player_name: str, money_to_change: float, notify: bool = True) -> bool:
        """
        改变玩家金钱（底层函数，基于玩家名称）
        :param player_name: 玩家名称
        :param money_to_change: 要改变的金钱数量（正数为增加，负数为减少），支持小数
        :param notify: 是否通知在线玩家
        :return: 是否操作成功
        """
        m = self._round_money(money_to_change)
        if m == 0:
            return True
        if m > 0:
            return self.increase_player_money_by_name(player_name, m, notify)
        return self.decrease_player_money_by_name(player_name, abs(m), notify)

    def increase_player_money(self, player: Player, amount: float) -> bool:
        """
        增加玩家金钱（Player对象封装器）
        :param player: 玩家对象
        :param amount: 增加的金钱数量（支持小数，精确到分）
        :return: 是否操作成功
        """
        return self.increase_player_money_by_name(player.name, amount)

    def decrease_player_money(self, player: Player, amount: float) -> bool:
        """
        减少玩家金钱（Player对象封装器）
        :param player: 玩家对象
        :param amount: 减少的金钱数量（支持小数，精确到分）
        :return: 是否操作成功
        """
        return self.decrease_player_money_by_name(player.name, amount)

    def get_player_free_land_blocks(self, player: Player) -> int:
        """获取玩家剩余免费领地格子数"""
        try:
            player_xuid = str(player.xuid)
            result = self.database_manager.query_one(
                "SELECT remaining_free_land_blocks FROM player_basic_info WHERE xuid = ?",
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
                'player_basic_info',
                {'remaining_free_land_blocks': amount},
                f"xuid = '{player_xuid}'"
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
                    f"give {player.name} {item_name} {total_item_count}"
                )
            except Exception as e:
                self.logger.error(f"{ColorFormat.RED}[ARC Core]Give invite reward item error: {str(e)}")

        # 金钱奖励
        if total_money > 0:
            self.increase_player_money(player, total_money)

        # 免费领地格子奖励
        if total_free_blocks > 0:
            current_free_blocks = self.get_player_free_land_blocks(player)
            new_free_blocks = current_free_blocks + total_free_blocks
            self.set_player_free_land_blocks(player, new_free_blocks)

        player.send_message(
            self.language_manager.GetText('INVITE_REWARD_GIVE_SELF_HINT').format(
                total_item_count,
                self._format_money_display(total_money),
                total_free_blocks
            )
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

    def get_top_richest_players(self, top_count: int) -> Dict[str, float]:
        try:
            results = self.database_manager.query_all(
                "SELECT * FROM player_economy ORDER BY money DESC LIMIT ?",
                (top_count,)
            )

            rich_list = {}
            for entry in results:
                try:
                    xuid_str = entry['xuid']
                    player_name = self.get_player_name_by_xuid(xuid_str)
                    if player_name:
                        rich_list[player_name] = self._round_money(entry['money'])
                except Exception:
                    continue

            return rich_list
        except Exception as e:
            self.logger.error(f"{ColorFormat.RED}[ARC Core]Get top richest players error: {str(e)}")
            return {}

    def get_player_money_rank(self, player: Player) -> Optional[int]:
        """
        获取玩家金钱排名
        :param player: 玩家对象
        :return: 玩家排名（从1开始），如果玩家不存在则返回None
        """
        try:
            # 使用SQL的ROW_NUMBER()函数来获取排名
            result = self.database_manager.query_one("""
                WITH RankedPlayers AS (
                    SELECT xuid, money,
                    ROW_NUMBER() OVER (ORDER BY money DESC) as rank
                    FROM player_economy
                )
                SELECT rank 
                FROM RankedPlayers 
                WHERE xuid = ?
            """, (str(player.xuid),))

            return result['rank'] if result else None
        except Exception as e:
            self.logger.error(f"{ColorFormat.RED}[ARC Core]Get player money rank error: {str(e)}")
            return None

    def judge_if_player_has_enough_money_by_name(self, player_name: str, amount: float) -> bool:
        """
        判断玩家是否有足够金钱（底层函数，基于玩家名称）
        :param player_name: 玩家名称
        :param amount: 需要的金钱数量（支持小数）
        :return: 是否有足够金钱
        """
        return self.get_player_money_by_name(player_name) >= abs(self._round_money(amount))

    def judge_if_player_has_enough_money(self, player: Player, amount: float) -> bool:
        """
        判断玩家是否有足够金钱（Player对象封装器）
        :param player: 玩家对象
        :param amount: 需要的金钱数量（支持小数）
        :return: 是否有足够金钱
        """
        return self.judge_if_player_has_enough_money_by_name(player.name, amount)

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
        online_players = self.server.online_players
        # 过滤掉自己
        available_players = [p for p in online_players if p.name != player.name]
        
        if not available_players:
            # 没有其他在线玩家
            no_players_form = ActionForm(
                title=self.language_manager.GetText('TRANSFER_PANEL_TITLE'),
                content=self.language_manager.GetText('TRANSFER_NO_ONLINE_PLAYERS_TEXT'),
                on_close=self.show_bank_main_menu
            )
            player.send_form(no_players_form)
            return
        
        # 创建玩家选择面板
        player_select_panel = ActionForm(
            title=self.language_manager.GetText('TRANSFER_PANEL_TITLE'),
            content=self.language_manager.GetText('TRANSFER_SELECT_PLAYER_CONTENT').format(
                self._format_money_display(self.get_player_money(player))
            )
        )
        
        # 为每个在线玩家添加按钮
        for target_player in available_players:
            player_select_panel.add_button(
                f"{target_player.name}",
                on_click=lambda sender, target=target_player: self.show_transfer_amount_panel(sender, target)
            )
        
        # 添加返回按钮
        player_select_panel.add_button(
            self.language_manager.GetText('RETURN_BUTTON_TEXT'),
            on_click=self.show_bank_main_menu
        )
        
        player.send_form(player_select_panel)
    
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
            # 直接使用目标玩家对象和金额进行转账
            error_code, receive_player, amount = self._validate_transfer_data_new(sender, target_player, data[1])
            if error_code == 0:
                self.decrease_player_money(sender, amount)
                self.increase_player_money(receive_player, amount)
                receive_player.send_message(self.language_manager.GetText('RECEIVE_PLAYER_TRANSFER_MESSAGE').format(
                    sender.name,
                    self._format_money_display(amount),
                    self._format_money_display(self.get_player_money(receive_player))))
                result_str = self.language_manager.GetText('TRANSFER_COMPLETED_HINT_TEXT').format(
                    receive_player.name,
                    self._format_money_display(amount),
                    self._format_money_display(self.get_player_money(sender))
                )
            else:
                result_str = self.language_manager.GetText(f'TRANSFER_ERROR_{error_code}_TEXT')
                if error_code == 2:
                    result_str = result_str.format(target_player.name)
            result_form = ActionForm(
                title=self.language_manager.GetText('TRANSFER_RESULT_PANEL_TITLE'),
                content=result_str,
                on_close=self.show_bank_main_menu
            )
            sender.send_form(result_form)

        transfer_panel = ModalForm(
            title=self.language_manager.GetText('TRANSFER_PANEL_TITLE'),
            controls=[info_label, money_amount_input],
            on_close=lambda sender: self.show_transfer_panel(sender),
            on_submit=try_transfer
        )
        player.send_form(transfer_panel)

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
        # 获取更多的玩家数据以便过滤后仍有足够的显示数量
        initial_count = 20 if self.hide_op_in_money_ranking else 10
        rank_dict = self.get_top_richest_players(initial_count)
        
        # 如果启用了隐藏OP功能，在业务逻辑层过滤OP玩家
        filtered_rank_dict = {}
        for player_name, player_money in rank_dict.items():
            if self.hide_op_in_money_ranking:
                # 检查玩家是否为OP
                is_op = self.get_offline_player_op_status(player_name)
                if is_op is True:
                    continue  # 跳过OP玩家
            filtered_rank_dict[player_name] = player_money
            # 如果已经有足够的显示数量，停止添加
            if len(filtered_rank_dict) >= 10:
                break
        
        rank_list = []
        for i, (player_name, player_money) in enumerate(filtered_rank_dict.items()):
            rank_list.append(
                self.language_manager.GetText('MONEY_RANK_INFO_TEXT').format(
                    i + 1, player_name, self._format_money_display(player_money)))
        
        rank_panel = ActionForm(
            title=self.language_manager.GetText('MONEY_RANK_PANEL_TITLE'),
            content='\n'.join(rank_list) + '\n' + self.language_manager.GetText('MONEY_RANK_PLYAER_RANK_INFO_TEXT').format(
                self._format_money_display(self.get_player_money(player)), self.get_player_money_rank(player)),
            on_close=self.show_bank_main_menu
        )
        player.send_form(rank_panel)
    
    # Shop menu
    def show_shop_menu(self, player: Player):
        player.perform_command('us')

    def show_button_shop_menu(self, player: Player):
        player.perform_command('shop')

    # Teleport menu
    def show_teleport_menu(self, player: Player):
        teleport_main_menu = ActionForm(
            title=self.language_manager.GetText('TELEPORT_MAIN_MENU_TITLE'),
            content=self.language_manager.GetText('TELEPORT_MAIN_MENU_CONTENT')
        )
        
        # 公共传送点按钮
        public_warp_text = self.language_manager.GetText('TELEPORT_MAIN_MENU_PUBLIC_WARP_BUTTON')
        if self.teleport_cost_public_warp > 0:
            public_warp_text = self.language_manager.GetText('TELEPORT_BUTTON_WITH_COST').format(public_warp_text, self.teleport_cost_public_warp)
        teleport_main_menu.add_button(public_warp_text, on_click=self.show_public_warp_menu)
        
        # 私人传送点按钮
        home_text = self.language_manager.GetText('TELEPORT_MAIN_MENU_HOME_BUTTON')
        if self.teleport_cost_home > 0:
            home_text = self.language_manager.GetText('TELEPORT_BUTTON_WITH_COST').format(home_text, self.teleport_cost_home)
        teleport_main_menu.add_button(home_text, on_click=self.show_home_menu)
        
        # 随机传送按钮
        if self.enable_random_teleport:
            random_text = self.language_manager.GetText('TELEPORT_MAIN_MENU_RANDOM_BUTTON')
            if self.teleport_cost_random > 0:
                random_text = self.language_manager.GetText('TELEPORT_BUTTON_WITH_COST').format(random_text, self.teleport_cost_random)
            teleport_main_menu.add_button(random_text, on_click=self.start_random_teleport)
        
        # 如果玩家有死亡位置记录，显示返回死亡地点的按钮
        if player.name in self.player_death_locations:
            death_location = self.player_death_locations[player.name]
            death_text = self.language_manager.GetText('TELEPORT_MAIN_MENU_DEATH_LOCATION_BUTTON').format(death_location['dimension'])
            if self.teleport_cost_death_location > 0:
                death_text = self.language_manager.GetText('TELEPORT_BUTTON_WITH_COST').format(death_text, self.teleport_cost_death_location)
            teleport_main_menu.add_button(death_text, on_click=self.teleport_to_death_location)
        
        # 玩家传送请求按钮
        player_request_text = self.language_manager.GetText('TELEPORT_MAIN_MENU_PLAYER_REQUEST_BUTTON')
        if self.teleport_cost_player > 0:
            player_request_text = self.language_manager.GetText('TELEPORT_BUTTON_WITH_COST').format(player_request_text, self.teleport_cost_player)
        teleport_main_menu.add_button(player_request_text, on_click=self.show_player_teleport_request_menu)
        
        if player.is_op:
            teleport_main_menu.add_button(self.language_manager.GetText('TELEPORT_MAIN_MENU_OP_MANAGE_WARP_BUTTON'),
                                          on_click=self.show_op_warp_manage_menu)
        # 返回
        teleport_main_menu.add_button(self.language_manager.GetText('RETURN_BUTTON_TEXT'),
                                      on_click=self.show_main_menu)
        player.send_form(teleport_main_menu)

    # Teleport System Database
    def init_teleport_tables(self) -> bool:
        """初始化传送系统相关数据表"""
        try:
            # 公共传送点表
            warp_fields = {
                'warp_id': 'INTEGER PRIMARY KEY AUTOINCREMENT',
                'warp_name': 'TEXT NOT NULL UNIQUE',
                'dimension': 'TEXT NOT NULL',
                'x': 'REAL NOT NULL',
                'y': 'REAL NOT NULL',
                'z': 'REAL NOT NULL',
                'created_by': 'TEXT NOT NULL',  # 创建者XUID
                'created_time': 'INTEGER NOT NULL'  # 创建时间戳
            }
            
            # 玩家私人传送点表
            home_fields = {
                'home_id': 'INTEGER PRIMARY KEY AUTOINCREMENT',
                'owner_xuid': 'TEXT NOT NULL',
                'home_name': 'TEXT NOT NULL',
                'dimension': 'TEXT NOT NULL',
                'x': 'REAL NOT NULL',
                'y': 'REAL NOT NULL',
                'z': 'REAL NOT NULL',
                'created_time': 'INTEGER NOT NULL'
            }
            
            return (self.database_manager.create_table('public_warps', warp_fields) and
                    self.database_manager.create_table('player_homes', home_fields))
        except Exception as e:
            self.logger.error(f"Init teleport tables error: {str(e)}")
            return False

    # Public Warps Management
    def create_public_warp(self, warp_name: str, dimension: str, x: float, y: float, z: float, creator_xuid: str) -> bool:
        """创建公共传送点"""
        try:
            import time
            warp_data = {
                'warp_name': warp_name,
                'dimension': dimension,
                'x': x,
                'y': y,
                'z': z,
                'created_by': creator_xuid,
                'created_time': int(time.time())
            }
            return self.database_manager.insert('public_warps', warp_data)
        except Exception as e:
            self.logger.error(f"Create public warp error: {str(e)}")
            return False

    def delete_public_warp(self, warp_name: str) -> bool:
        """删除公共传送点"""
        try:
            return self.database_manager.delete('public_warps', 'warp_name = ?', (warp_name,))
        except Exception as e:
            self.logger.error(f"Delete public warp error: {str(e)}")
            return False

    def get_public_warp(self, warp_name: str) -> Optional[Dict[str, Any]]:
        """获取公共传送点信息"""
        try:
            return self.database_manager.query_one(
                "SELECT * FROM public_warps WHERE warp_name = ?",
                (warp_name,)
            )
        except Exception as e:
            self.logger.error(f"Get public warp error: {str(e)}")
            return None

    def get_all_public_warps(self) -> Dict[str, Dict[str, Any]]:
        """获取所有公共传送点"""
        try:
            results = self.database_manager.query_all("SELECT * FROM public_warps ORDER BY warp_name")
            return {row['warp_name']: row for row in results}
        except Exception as e:
            self.logger.error(f"Get all public warps error: {str(e)}")
            return {}

    def public_warp_exists(self, warp_name: str) -> bool:
        """检查公共传送点是否存在"""
        return self.get_public_warp(warp_name) is not None

    # Player Homes Management
    def create_player_home(self, owner_xuid: str, home_name: str, dimension: str, x: float, y: float, z: float) -> bool:
        """创建玩家传送点"""
        try:
            import time
            home_data = {
                'owner_xuid': owner_xuid,
                'home_name': home_name,
                'dimension': dimension,
                'x': x,
                'y': y,
                'z': z,
                'created_time': int(time.time())
            }
            return self.database_manager.insert('player_homes', home_data)
        except Exception as e:
            self.logger.error(f"Create player home error: {str(e)}")
            return False

    def delete_player_home(self, owner_xuid: str, home_name: str) -> bool:
        """删除玩家传送点"""
        try:
            return self.database_manager.delete('player_homes', 'owner_xuid = ? AND home_name = ?', (owner_xuid, home_name))
        except Exception as e:
            self.logger.error(f"Delete player home error: {str(e)}")
            return False

    def get_player_home(self, owner_xuid: str, home_name: str) -> Optional[Dict[str, Any]]:
        """获取玩家传送点信息"""
        try:
            return self.database_manager.query_one(
                "SELECT * FROM player_homes WHERE owner_xuid = ? AND home_name = ?",
                (owner_xuid, home_name)
            )
        except Exception as e:
            self.logger.error(f"Get player home error: {str(e)}")
            return None

    def get_player_homes(self, owner_xuid: str) -> Dict[str, Dict[str, Any]]:
        """获取玩家所有传送点"""
        try:
            results = self.database_manager.query_all(
                "SELECT * FROM player_homes WHERE owner_xuid = ? ORDER BY home_name",
                (owner_xuid,)
            )
            return {row['home_name']: row for row in results}
        except Exception as e:
            self.logger.error(f"Get player homes error: {str(e)}")
            return {}

    def get_player_home_count(self, owner_xuid: str) -> int:
        """获取玩家传送点数量"""
        try:
            result = self.database_manager.query_one(
                "SELECT COUNT(*) as count FROM player_homes WHERE owner_xuid = ?",
                (owner_xuid,)
            )
            return result['count'] if result else 0
        except Exception as e:
            self.logger.error(f"Get player home count error: {str(e)}")
            return 0

    def player_home_exists(self, owner_xuid: str, home_name: str) -> bool:
        """检查玩家传送点是否存在"""
        return self.get_player_home(owner_xuid, home_name) is not None

    # Teleport System UI
    def show_public_warp_menu(self, player: Player):
        """显示公共传送点菜单"""
        public_warps = self.get_all_public_warps()
        if not public_warps:
            no_warp_panel = ActionForm(
                title=self.language_manager.GetText('PUBLIC_WARP_MENU_TITLE'),
                content=self.language_manager.GetText('PUBLIC_WARP_NO_WARP_CONTENT'),
                on_close=self.show_teleport_menu
            )
            player.send_form(no_warp_panel)
            return

        warp_menu = ActionForm(
            title=self.language_manager.GetText('PUBLIC_WARP_MENU_TITLE'),
            content=self.language_manager.GetText('PUBLIC_WARP_MENU_CONTENT').format(len(public_warps)),
            on_close=self.show_teleport_menu
        )
        
        for warp_name, warp_info in public_warps.items():
            creator_name = self.get_player_name_by_xuid(warp_info['created_by']) or 'Unknown'
            warp_button_text = self.language_manager.GetText('PUBLIC_WARP_BUTTON_TEXT').format(warp_name, warp_info['dimension'], creator_name)
            # 如果公共传送点收费，显示价格
            if self.teleport_cost_public_warp > 0:
                warp_button_text = self.language_manager.GetText('TELEPORT_BUTTON_WITH_COST').format(warp_button_text, self.teleport_cost_public_warp)
            warp_menu.add_button(
                warp_button_text,
                on_click=lambda p=player, w_name=warp_name, w_info=warp_info: self.teleport_to_public_warp(p, w_name, w_info)
            )
        
        player.send_form(warp_menu)

    def show_home_menu(self, player: Player):
        """显示玩家传送点菜单"""
        player_homes = self.get_player_homes(str(player.xuid))
        home_count = len(player_homes)
        
        home_menu = ActionForm(
            title=self.language_manager.GetText('HOME_MENU_TITLE'),
            content=self.language_manager.GetText('HOME_MENU_CONTENT').format(home_count, self.max_player_home_num),
            on_close=self.show_teleport_menu
        )
        
        # 显示现有传送点
        for home_name, home_info in player_homes.items():
            home_menu.add_button(
                self.language_manager.GetText('HOME_BUTTON_TEXT').format(home_name, home_info['dimension']),
                on_click=lambda p=player, h_name=home_name, h_info=home_info: self.show_home_detail_menu(p, h_name, h_info)
            )
        
        # 添加新传送点按钮
        if home_count < self.max_player_home_num:
            home_menu.add_button(
                self.language_manager.GetText('HOME_ADD_NEW_BUTTON'),
                on_click=self.show_create_home_panel
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
            on_close=self.show_home_menu
        )
        
        # 私人传送点传送按钮（显示价格）
        home_teleport_text = self.language_manager.GetText('HOME_TELEPORT_BUTTON')
        if self.teleport_cost_home > 0:
            home_teleport_text = self.language_manager.GetText('TELEPORT_BUTTON_WITH_COST').format(home_teleport_text, self.teleport_cost_home)
        detail_menu.add_button(
            home_teleport_text,
            on_click=lambda p=player, h_name=home_name, h_info=home_info: self.teleport_to_home(p, h_name, h_info)
        )
        
        detail_menu.add_button(
            self.language_manager.GetText('HOME_DELETE_BUTTON'),
            on_click=lambda p=player, h_name=home_name: self.confirm_delete_home(p, h_name)
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
            if not data or not data[0].strip():
                player.send_message(self.language_manager.GetText('CREATE_HOME_EMPTY_NAME_ERROR'))
                self.show_create_home_panel(player)
                return
            
            home_name = data[0].strip()
            if self.player_home_exists(str(player.xuid), home_name):
                player.send_message(self.language_manager.GetText('CREATE_HOME_NAME_EXISTS_ERROR').format(home_name))
                self.show_create_home_panel(player)
                return
            
            # 创建传送点
            success = self.create_player_home(
                str(player.xuid),
                home_name,
                player.location.dimension.name,
                player.location.x,
                player.location.y,
                player.location.z
            )
            
            if success:
                player.send_message(self.language_manager.GetText('CREATE_HOME_SUCCESS').format(home_name))
            else:
                player.send_message(self.language_manager.GetText('CREATE_HOME_FAILED'))
            
            self.show_home_menu(player)

        create_panel = ModalForm(
            title=self.language_manager.GetText('CREATE_HOME_PANEL_TITLE'),
            controls=[home_name_input],
            on_close=self.show_home_menu,
            on_submit=try_create_home
        )
        
        player.send_form(create_panel)

    def confirm_delete_home(self, player: Player, home_name: str):
        """确认删除传送点"""
        confirm_panel = ActionForm(
            title=self.language_manager.GetText('CONFIRM_DELETE_HOME_TITLE'),
            content=self.language_manager.GetText('CONFIRM_DELETE_HOME_CONTENT').format(home_name),
            on_close=self.show_home_menu
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
        # 检查费用
        if self.teleport_cost_public_warp > 0:
            player_money = self.get_player_money(player)
            if player_money < self.teleport_cost_public_warp:
                player.send_message(self.language_manager.GetText('TELEPORT_COST_NOT_ENOUGH_MONEY').format(
                    self._format_money_display(self.teleport_cost_public_warp),
                    self._format_money_display(player_money)
                ))
                return
            
            # 扣除费用
            if self.decrease_player_money(player, self.teleport_cost_public_warp):
                player.send_message(self.language_manager.GetText('TELEPORT_COST_DEDUCTED').format(
                    self._format_money_display(self.teleport_cost_public_warp),
                    self._format_money_display(self.get_player_money(player))
                ))
            else:
                player.send_message(self.language_manager.GetText('SYSTEM_ERROR'))
                return
        
        self.start_teleport_to_position_countdown(player, warp_name, (warp_info['x'], warp_info['y'], warp_info['z']), 'PUBLIC_WARP', warp_info['dimension'])

    def teleport_to_home(self, player: Player, home_name: str, home_info: Dict[str, Any]):
        """传送到玩家传送点"""
        # 检查费用
        if self.teleport_cost_home > 0:
            player_money = self.get_player_money(player)
            if player_money < self.teleport_cost_home:
                player.send_message(self.language_manager.GetText('TELEPORT_COST_NOT_ENOUGH_MONEY').format(
                    self._format_money_display(self.teleport_cost_home),
                    self._format_money_display(player_money)
                ))
                return
            
            # 扣除费用
            if self.decrease_player_money(player, self.teleport_cost_home):
                player.send_message(self.language_manager.GetText('TELEPORT_COST_DEDUCTED').format(
                    self._format_money_display(self.teleport_cost_home),
                    self._format_money_display(self.get_player_money(player))
                ))
            else:
                player.send_message(self.language_manager.GetText('SYSTEM_ERROR'))
                return
        
        self.start_teleport_to_position_countdown(player, home_name, (home_info['x'], home_info['y'], home_info['z']), 'HOME', home_info['dimension'])

    def start_teleport_to_position_countdown(self, player: Player, destination_name: str, position: tuple, teleport_type: str, dimension: str = 'overworld'):
        """开始传送到位置倒计时"""
        self.server.scheduler.run_task(
            self, 
            lambda: self.execute_teleport_to_position(player, destination_name, position, teleport_type, dimension), 
            delay=45
        )
        
        # 发送提示
        if teleport_type == 'PUBLIC_WARP':
            message = self.language_manager.GetText('TELEPORT_TO_WARP_COUNTDOWN').format(destination_name)
        elif teleport_type == 'HOME':
            message = self.language_manager.GetText('TELEPORT_TO_HOME_COUNTDOWN').format(destination_name)
        else:
            message = self.language_manager.GetText('TELEPORT_COUNTDOWN').format(destination_name)
        player.send_message(message)
    
    def start_teleport_to_player_countdown(self, player: Player, target_player: Player):
        """开始传送到玩家倒计时"""
        self.server.scheduler.run_task(
            self, 
            lambda: self.execute_teleport_to_player(player, target_player), 
            delay=45
        )

        # 发送提示
        message = self.language_manager.GetText('TELEPORT_COUNTDOWN').format(target_player.name)
        player.send_message(message)

    def execute_teleport_to_position(self, player: Player, destination_name: str, position: tuple, teleport_type: str, dimension: str = 'overworld'):
        """执行传送"""
        if teleport_type == 'PUBLIC_WARP':
            message = self.language_manager.GetText('TELEPORT_TO_WARP_SUCCESS').format(destination_name)
        elif teleport_type == 'HOME':
            message = self.language_manager.GetText('TELEPORT_TO_HOME_SUCCESS').format(destination_name)
        else:
            message = self.language_manager.GetText('TELEPORT_SUCCESS').format(destination_name)
        player.send_message(message)
        self.server.dispatch_command(self.server.command_sender, self.generate_tp_command_to_position(player.name, position, dimension))
    
    def execute_teleport_to_player(self, player: Player, target_player: Player):
        """执行传送"""
        message = self.language_manager.GetText('TELEPORT_SUCCESS').format(target_player.name)
        player.send_message(message)
        # 获取目标玩家的当前维度
        target_dimension = target_player.location.dimension.name
        self.server.dispatch_command(self.server.command_sender, self.generate_tp_command_to_player(player.name, target_player.name, target_dimension))
    
    @staticmethod
    def format_dimension_name(dimension: str) -> str:
        """
        将完整的维度名称转换为execute命令所需的格式
        :param dimension: 完整维度名称 (如 'minecraft:overworld')
        :return: 简化的维度名称 (如 'overworld')
        """
        # 明确的维度名称映射，确保正确处理
        dimension_mapping = {
            # 标准Minecraft格式
            'minecraft:overworld': 'overworld',
            'minecraft:the_nether': 'the_nether', 
            'minecraft:the_end': 'the_end',
            # EndStone可能的格式
            'Overworld': 'overworld',
            'TheNether': 'the_nether',
            'TheEnd': 'the_end',
            # 其他可能的格式
            'overworld': 'overworld',
            'the_nether': 'the_nether',
            'the_end': 'the_end',
            'nether': 'the_nether',
            'end': 'the_end'
        }
        
        # 如果在映射表中，直接返回映射的值
        if dimension in dimension_mapping:
            return dimension_mapping[dimension]
        
        # 否则使用通用的处理方式（去掉命名空间前缀）
        if ':' in dimension:
            return dimension.split(':')[1]
        return dimension

    @staticmethod
    def generate_tp_command_to_position(player_name: str, position: tuple, dimension: str = 'overworld'):
        formatted_name = f'"{player_name}"' if ' ' in player_name else player_name
        formatted_dimension = ARCCorePlugin.format_dimension_name(dimension)
        return f'execute in {formatted_dimension} run tp {formatted_name} {" ".join([str(int(_)) for _ in position])}'

    @staticmethod
    def generate_tp_command_to_player(player_name: str, target_player_name: str, dimension: str = 'overworld'):
        formatted_player = f'"{player_name}"' if ' ' in player_name else player_name
        formatted_target = f'"{target_player_name}"' if ' ' in target_player_name else target_player_name
        formatted_dimension = ARCCorePlugin.format_dimension_name(dimension)
        return f'execute in {formatted_dimension} run tp {formatted_player} {formatted_target}'

    # Death Location Teleport
    def teleport_to_death_location(self, player: Player):
        """传送到死亡地点"""
        if player.name not in self.player_death_locations:
            player.send_message(self.language_manager.GetText('NO_DEATH_LOCATION_RECORDED'))
            return
        
        # 检查费用
        if self.teleport_cost_death_location > 0:
            player_money = self.get_player_money(player)
            if player_money < self.teleport_cost_death_location:
                player.send_message(self.language_manager.GetText('TELEPORT_COST_NOT_ENOUGH_MONEY').format(
                    self._format_money_display(self.teleport_cost_death_location),
                    self._format_money_display(player_money)
                ))
                return
            
            # 扣除费用
            if self.decrease_player_money(player, self.teleport_cost_death_location):
                player.send_message(self.language_manager.GetText('TELEPORT_COST_DEDUCTED').format(
                    self._format_money_display(self.teleport_cost_death_location),
                    self._format_money_display(self.get_player_money(player))
                ))
            else:
                player.send_message(self.language_manager.GetText('SYSTEM_ERROR'))
                return
        
        death_location = self.player_death_locations[player.name]
        
        # 开始传送倒计时
        self.server.scheduler.run_task(
            self, 
            lambda: self.execute_death_location_teleport(player), 
            delay=45
        )
        
        player.send_message(self.language_manager.GetText('TELEPORT_TO_DEATH_LOCATION_COUNTDOWN'))

    def execute_death_location_teleport(self, player: Player):
        """执行死亡地点传送"""
        if player.name not in self.player_death_locations:
            player.send_message(self.language_manager.GetText('NO_DEATH_LOCATION_RECORDED'))
            return
        
        death_location = self.player_death_locations[player.name]
        position = (death_location['x'], death_location['y'], death_location['z'])
        dimension = death_location['dimension']
        
        # 执行传送
        player.send_message(self.language_manager.GetText('TELEPORT_TO_DEATH_LOCATION_SUCCESS'))
        self.server.dispatch_command(self.server.command_sender, self.generate_tp_command_to_position(player.name, position, dimension))
        
        # 清理死亡位置记录
        del self.player_death_locations[player.name]

    # Random Teleport System
    def start_random_teleport(self, player: Player):
        """开始随机传送"""
        # 检查功能是否启用
        if not self.enable_random_teleport:
            player.send_message(self.language_manager.GetText('RANDOM_TELEPORT_DISABLED'))
            return
        
        # 检查费用
        if self.teleport_cost_random > 0:
            player_money = self.get_player_money(player)
            if player_money < self.teleport_cost_random:
                player.send_message(self.language_manager.GetText('TELEPORT_COST_NOT_ENOUGH_MONEY').format(
                    self._format_money_display(self.teleport_cost_random),
                    self._format_money_display(player_money)
                ))
                return
            
            # 扣除费用
            if self.decrease_player_money(player, self.teleport_cost_random):
                player.send_message(self.language_manager.GetText('TELEPORT_COST_DEDUCTED').format(
                    self._format_money_display(self.teleport_cost_random),
                    self._format_money_display(self.get_player_money(player))
                ))
            else:
                player.send_message(self.language_manager.GetText('SYSTEM_ERROR'))
                return
        
        # 发送倒计时消息
        player.send_message(self.language_manager.GetText('RANDOM_TELEPORT_COUNTDOWN'))
        
        # 延迟执行传送
        self.server.scheduler.run_task(
            self,
            lambda: self.execute_random_teleport(player),
            delay=45
        )
    
    def execute_random_teleport(self, player: Player):
        """执行随机传送"""
        import random
        
        # 生成随机坐标
        angle = random.uniform(0, 2 * math.pi)
        distance = random.uniform(0, self.random_teleport_radius)
        
        random_x = self.random_teleport_center_x + int(distance * math.cos(angle))
        random_z = self.random_teleport_center_z + int(distance * math.sin(angle))
        random_y = 256
        
        # 执行传送到主世界
        position = (random_x, random_y, random_z)
        dimension = 'overworld'
        
        player.send_message(self.language_manager.GetText('RANDOM_TELEPORT_SUCCESS').format(random_x, random_z))
        self.server.dispatch_command(self.server.command_sender, self.generate_tp_command_to_position(player.name, position, dimension))
        
        # 添加羽落效果（10秒）
        self.server.scheduler.run_task(
            self,
            lambda: self.apply_slow_falling_effect(player),
            delay=2  # 稍微延迟以确保传送完成
        )
    
    def apply_slow_falling_effect(self, player: Player):
        """给玩家添加羽落效果"""
        try:
            # 使用 effect 命令给玩家添加缓降效果
            self.server.dispatch_command(
                self.server.command_sender,
                f'effect "{player.name}" slow_falling 10 255 true'
            )
            player.send_message(self.language_manager.GetText('RANDOM_TELEPORT_SLOW_FALLING_APPLIED'))
        except Exception as e:
            self.logger.error(f"Failed to apply slow falling effect: {str(e)}")

    # Player Teleport Request System
    def show_player_teleport_request_menu(self, player: Player):
        """显示玩家传送请求菜单"""
        request_menu = ActionForm(
            title=self.language_manager.GetText('PLAYER_TELEPORT_REQUEST_MENU_TITLE'),
            content=self.language_manager.GetText('PLAYER_TELEPORT_REQUEST_MENU_CONTENT'),
            on_close=self.show_teleport_menu
        )
        
        request_menu.add_button(
            self.language_manager.GetText('SEND_TPA_REQUEST_BUTTON'),
            on_click=self.show_send_tpa_request_panel
        )
        
        request_menu.add_button(
            self.language_manager.GetText('SEND_TPHERE_REQUEST_BUTTON'),
            on_click=self.show_send_tphere_request_panel
        )
        
        # 检查是否有待处理的请求
        pending_requests = self.get_pending_requests_for_player(player)
        if pending_requests:
            request_menu.add_button(
                self.language_manager.GetText('HANDLE_PENDING_REQUESTS_BUTTON').format(len(pending_requests)),
                on_click=self.show_pending_requests_menu
            )
        
        player.send_form(request_menu)

    def show_send_tpa_request_panel(self, player: Player):
        """显示发送TPA请求面板"""
        online_players = [p for p in self.server.online_players if p.name != player.name]
        if not online_players:
            no_players_panel = ActionForm(
                title=self.language_manager.GetText('SEND_TPA_REQUEST_TITLE'),
                content=self.language_manager.GetText('NO_OTHER_PLAYERS_ONLINE'),
                on_close=self.show_player_teleport_request_menu
            )
            player.send_form(no_players_panel)
            return

        tpa_menu = ActionForm(
            title=self.language_manager.GetText('SEND_TPA_REQUEST_TITLE'),
            content=self.language_manager.GetText('SEND_TPA_REQUEST_CONTENT'),
            on_close=self.show_player_teleport_request_menu
        )
        
        for target_player in online_players:
            tpa_menu.add_button(
                self.language_manager.GetText('TPA_TARGET_BUTTON').format(target_player.name),
                on_click=lambda p=player, t=target_player: self.send_tpa_request(p, t)
            )
        
        player.send_form(tpa_menu)

    def show_send_tphere_request_panel(self, player: Player):
        """显示发送TPHERE请求面板"""
        online_players = [p for p in self.server.online_players if p.name != player.name]
        if not online_players:
            no_players_panel = ActionForm(
                title=self.language_manager.GetText('SEND_TPHERE_REQUEST_TITLE'),
                content=self.language_manager.GetText('NO_OTHER_PLAYERS_ONLINE'),
                on_close=self.show_player_teleport_request_menu
            )
            player.send_form(no_players_panel)
            return

        tphere_menu = ActionForm(
            title=self.language_manager.GetText('SEND_TPHERE_REQUEST_TITLE'),
            content=self.language_manager.GetText('SEND_TPHERE_REQUEST_CONTENT'),
            on_close=self.show_player_teleport_request_menu
        )
        
        for target_player in online_players:
            tphere_menu.add_button(
                self.language_manager.GetText('TPHERE_TARGET_BUTTON').format(target_player.name),
                on_click=lambda p=player, t=target_player: self.send_tphere_request(p, t)
            )
        
        player.send_form(tphere_menu)

    def send_tpa_request(self, sender: Player, target: Player):
        """发送TPA请求（请求传送到目标玩家处）"""
        import time
        
        # 检查费用
        if self.teleport_cost_player > 0:
            player_money = self.get_player_money(sender)
            if player_money < self.teleport_cost_player:
                sender.send_message(self.language_manager.GetText('TELEPORT_COST_NOT_ENOUGH_MONEY').format(
                    self._format_money_display(self.teleport_cost_player),
                    self._format_money_display(player_money)
                ))
                return
            
            # 扣除费用
            if self.decrease_player_money(sender, self.teleport_cost_player):
                sender.send_message(self.language_manager.GetText('TELEPORT_COST_DEDUCTED').format(
                    self._format_money_display(self.teleport_cost_player),
                    self._format_money_display(self.get_player_money(sender))
                ))
            else:
                sender.send_message(self.language_manager.GetText('SYSTEM_ERROR'))
                return
        
        # 检查是否已有请求
        if target.name in self.teleport_requests:
            sender.send_message(self.language_manager.GetText('TELEPORT_REQUEST_ALREADY_EXISTS').format(target.name))
            return
        
        # 创建请求
        self.teleport_requests[target.name] = {
            'type': 'tpa',
            'sender': sender.name,
            'expire_time': time.time() + 60  # 60秒过期
        }
        
        # 通知双方
        sender.send_message(self.language_manager.GetText('TPA_REQUEST_SENT').format(target.name))
        target.send_message(self.language_manager.GetText('TPA_REQUEST_RECEIVED').format(sender.name))

    def send_tphere_request(self, sender: Player, target: Player):
        """发送TPHERE请求（请求目标玩家传送过来）"""
        import time
        
        # 检查费用
        if self.teleport_cost_player > 0:
            player_money = self.get_player_money(sender)
            if player_money < self.teleport_cost_player:
                sender.send_message(self.language_manager.GetText('TELEPORT_COST_NOT_ENOUGH_MONEY').format(
                    self._format_money_display(self.teleport_cost_player),
                    self._format_money_display(player_money)
                ))
                return
            
            # 扣除费用
            if self.decrease_player_money(sender, self.teleport_cost_player):
                sender.send_message(self.language_manager.GetText('TELEPORT_COST_DEDUCTED').format(
                    self._format_money_display(self.teleport_cost_player),
                    self._format_money_display(self.get_player_money(sender))
                ))
            else:
                sender.send_message(self.language_manager.GetText('SYSTEM_ERROR'))
                return
        
        # 检查是否已有请求
        if target.name in self.teleport_requests:
            sender.send_message(self.language_manager.GetText('TELEPORT_REQUEST_ALREADY_EXISTS').format(target.name))
            return
        
        # 创建请求
        self.teleport_requests[target.name] = {
            'type': 'tphere',
            'sender': sender.name,
            'expire_time': time.time() + 60  # 60秒过期
        }
        
        # 通知双方
        sender.send_message(self.language_manager.GetText('TPHERE_REQUEST_SENT').format(target.name))
        target.send_message(self.language_manager.GetText('TPHERE_REQUEST_RECEIVED').format(sender.name))

    def get_pending_requests_for_player(self, player: Player) -> list:
        """获取玩家的待处理请求"""
        import time
        current_time = time.time()
        pending_requests = []
        
        if player.name in self.teleport_requests:
            request = self.teleport_requests[player.name]
            if request['expire_time'] > current_time:
                pending_requests.append(request)
            else:
                # 清理过期请求
                del self.teleport_requests[player.name]
        
        return pending_requests

    def show_pending_requests_menu(self, player: Player):
        """显示待处理请求菜单"""
        pending_requests = self.get_pending_requests_for_player(player)
        if not pending_requests:
            player.send_message(self.language_manager.GetText('NO_PENDING_REQUESTS'))
            self.show_player_teleport_request_menu(player)
            return

        request = pending_requests[0]  # 目前只处理一个请求
        request_menu = ActionForm(
            title=self.language_manager.GetText('PENDING_REQUEST_MENU_TITLE'),
            content=self.language_manager.GetText('PENDING_REQUEST_CONTENT').format(
                request['sender'],
                self.language_manager.GetText(f'{request["type"].upper()}_REQUEST_DESCRIPTION')
            ),
            on_close=self.show_player_teleport_request_menu
        )
        
        request_menu.add_button(
            self.language_manager.GetText('ACCEPT_REQUEST_BUTTON'),
            on_click=lambda p=player: self.accept_teleport_request(p)
        )
        
        request_menu.add_button(
            self.language_manager.GetText('DENY_REQUEST_BUTTON'),
            on_click=lambda p=player: self.deny_teleport_request(p)
        )
        
        player.send_form(request_menu)

    def accept_teleport_request(self, player: Player):
        """接受传送请求"""
        if player.name not in self.teleport_requests:
            player.send_message(self.language_manager.GetText('NO_PENDING_REQUESTS'))
            return
        
        request = self.teleport_requests[player.name]
        sender = self.server.get_player(request['sender'])
        
        if not sender:
            player.send_message(self.language_manager.GetText('REQUEST_SENDER_OFFLINE'))
            del self.teleport_requests[player.name]
            return
        
        # 执行传送
        if request['type'] == 'tpa':
            # TPA: 发送者传送到接受者处
            self.start_teleport_to_player_countdown(sender, player)
            # 修正：接受者收到"你接受了XX的传送请求"，发起者收到"玩家XX接受了你的传送请求"
            player.send_message(self.language_manager.GetText('TPA_REQUEST_ACCEPTED_BY_TARGET').format(sender.name))
            sender.send_message(self.language_manager.GetText('TPA_REQUEST_ACCEPTED').format(player.name))
        else:
            # TPHERE: 接受者传送到发送者处
            self.start_teleport_to_player_countdown(player, sender)
            # 修正：接受者收到"你接受了XX的传送到此地请求"，发起者收到"玩家XX接受了你的传送到此地请求"
            player.send_message(self.language_manager.GetText('TPHERE_REQUEST_ACCEPTED_BY_TARGET').format(sender.name))
            sender.send_message(self.language_manager.GetText('TPHERE_REQUEST_ACCEPTED').format(player.name))
        
        # 清理请求
        del self.teleport_requests[player.name]

    def deny_teleport_request(self, player: Player):
        """拒绝传送请求"""
        if player.name not in self.teleport_requests:
            player.send_message(self.language_manager.GetText('NO_PENDING_REQUESTS'))
            return
        
        request = self.teleport_requests[player.name]
        sender = self.server.get_player(request['sender'])
        
        if sender:
            if request['type'] == 'tpa':
                sender.send_message(self.language_manager.GetText('TPA_REQUEST_DENIED').format(player.name))
                player.send_message(self.language_manager.GetText('TPA_REQUEST_DENIED_BY_YOU').format(sender.name))
            else:
                sender.send_message(self.language_manager.GetText('TPHERE_REQUEST_DENIED').format(player.name))
                player.send_message(self.language_manager.GetText('TPHERE_REQUEST_DENIED_BY_YOU').format(sender.name))
        
        # 清理请求
        del self.teleport_requests[player.name]

    # OP Warp Management
    def show_op_warp_manage_menu(self, player: Player):
        """显示OP传送点管理菜单"""
        warp_manage_menu = ActionForm(
            title=self.language_manager.GetText('OP_WARP_MANAGE_MENU_TITLE'),
            content=self.language_manager.GetText('OP_WARP_MANAGE_MENU_CONTENT'),
            on_close=self.show_teleport_menu
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
                player.location.dimension.name,
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
            on_close=self.show_op_warp_manage_menu,
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
            on_close=self.show_op_warp_manage_menu
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
            on_close=self.show_delete_warp_menu
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

    # Cleanup Tasks
    def cleanup_expired_teleport_requests(self):
        """清理过期的传送请求"""
        import time
        current_time = time.time()
        expired_requests = []
        
        for player_name, request in self.teleport_requests.items():
            if request['expire_time'] <= current_time:
                expired_requests.append(player_name)
        
        for player_name in expired_requests:
            del self.teleport_requests[player_name]

    # Land System
    def init_land_tables(self) -> bool:
        """初始化领地相关的数据表"""
        try:
            # 只创建领地基本信息表
            land_fields = {
                'land_id': 'INTEGER PRIMARY KEY AUTOINCREMENT',  # 领地编号
                'owner_xuid': 'TEXT NOT NULL',  # 领地主人XUID
                'land_name': 'TEXT NOT NULL',  # 领地名称
                'dimension': 'TEXT NOT NULL',  # 领地所在维度
                'min_x': 'INTEGER NOT NULL',  # 最小X坐标
                'max_x': 'INTEGER NOT NULL',  # 最大X坐标
                'min_y': 'INTEGER NOT NULL DEFAULT 0',  # 最小Y坐标
                'max_y': 'INTEGER NOT NULL DEFAULT 255',  # 最大Y坐标
                'min_z': 'INTEGER NOT NULL',  # 最小Z坐标
                'max_z': 'INTEGER NOT NULL',  # 最大Z坐标
                'tp_x': 'REAL NOT NULL',  # 传送点X坐标
                'tp_y': 'REAL NOT NULL',  # 传送点Y坐标
                'tp_z': 'REAL NOT NULL',  # 传送点Z坐标
                'shared_users': 'TEXT',  # 共有人UUID列表(JSON字符串)
                'allow_explosion': 'INTEGER DEFAULT 0',  # 是否允许爆炸 (0=不允许, 1=允许)
                'allow_public_interact': 'INTEGER DEFAULT 0',  # 是否对所有人开放方块互动 (0=不开放, 1=开放)
                'allow_actor_interaction': 'INTEGER DEFAULT 0',  # 是否允许生物互动 (0=不允许, 1=允许)
                'allow_actor_damage': 'INTEGER DEFAULT 0',  # 是否允许攻击生物 (0=不允许, 1=允许)
                'owner_paid_money': 'REAL DEFAULT 0',  # 购买时玩家实际支付的金钱，用于出售时按实付退款
                'allow_non_public_land': 'INTEGER DEFAULT 0'  # 公共领地是否允许玩家在其中圈私人领地
            }

            # 检查表是否已经存在
            table_exists = self.database_manager.table_exists('lands')
            
            if table_exists:
                # 表已存在，执行升级检查
                self._upgrade_land_table()
                return True
            else:
                # 表不存在，创建新表
                success = self.database_manager.create_table('lands', land_fields)
                if success:
                    print("[ARC Core]Created new land table with all fields")
                return success
        except Exception as e:
            print(f"[ARC Core]Init land tables error: {str(e)}")
            return False

    def _upgrade_land_table(self) -> bool:
        """升级领地数据表结构，为老用户添加新字段。使用 _column_exists 判断，避免依赖 execute() 的异常机制（execute 内部会吞掉异常）"""
        try:
            def _add_column(col: str, definition: str):
                if not self._column_exists('lands', col):
                    ok = self.database_manager.execute(f"ALTER TABLE lands ADD COLUMN {col} {definition}")
                    if ok:
                        print(f"[ARC Core]Upgraded land table: added {col} column")
                    else:
                        print(f"[ARC Core]Failed to add {col} column")
                    return ok
                return None  # 列已存在，无需操作

            _add_column('allow_explosion', 'INTEGER DEFAULT 0')
            _add_column('allow_public_interact', 'INTEGER DEFAULT 0')
            _add_column('allow_actor_interaction', 'INTEGER DEFAULT 0')
            _add_column('allow_actor_damage', 'INTEGER DEFAULT 0')

            # owner_paid_money 需要在首次添加时为存量领地回填估算价格（一次性迁移，不得重复执行）
            if not self._column_exists('lands', 'owner_paid_money'):
                ok = self.database_manager.execute("ALTER TABLE lands ADD COLUMN owner_paid_money REAL DEFAULT 0")
                if ok:
                    print("[ARC Core]Upgraded land table: added owner_paid_money column, initializing values for existing lands...")
                    land_price_raw = self.setting_manager.GetSetting('LAND_PRICE')
                    try:
                        upgrade_land_price = float(int(land_price_raw)) if land_price_raw is not None else 100.0
                    except (ValueError, TypeError):
                        upgrade_land_price = 100.0
                    self.database_manager.execute(
                        "UPDATE lands SET owner_paid_money = (max_x - min_x + 1) * (max_z - min_z + 1) * ?",
                        (upgrade_land_price,)
                    )
                    print(f"[ARC Core]owner_paid_money initialized (land_price={upgrade_land_price}, one-time migration only)")
                else:
                    print("[ARC Core]Failed to add owner_paid_money column")

            _add_column('allow_non_public_land', 'INTEGER DEFAULT 0')
            _add_column('min_y', 'INTEGER NOT NULL DEFAULT 0')
            _add_column('max_y', 'INTEGER NOT NULL DEFAULT 255')
            # allow_sub_land 已废弃，保留迁移仅为兼容旧数据库，不做任何逻辑依赖
            _add_column('allow_sub_land', 'INTEGER DEFAULT 0')

            return True
        except Exception as e:
            print(f"[ARC Core]Upgrade land table error: {str(e)}")
            return True  # 即使升级失败也不影响插件启动

    def _ensure_dimension_table(self, dimension: str) -> bool:
        """
        确保维度对应的区块表存在
        :param dimension: 维度名称
        :return: 是否成功
        """
        try:
            table_name = self._get_dimension_table(dimension)

            # 如果表已存在，直接返回True
            if self.database_manager.table_exists(table_name):
                return True

            # 创建新的区块表
            chunk_fields = {
                'chunk_key': 'TEXT PRIMARY KEY',  # 区块键(chunkX_chunkZ)
                'land_ids': 'TEXT NOT NULL'  # 该区块包含的领地ID列表(JSON字符串)
            }

            return self.database_manager.create_table(table_name, chunk_fields)
        except Exception as e:
            self.logger.error(f"Ensure dimension table error: {str(e)}")
            return False

    def _get_dimension_table(self, dimension: str) -> str:
        """
        获取维度对应的区块表名
        :param dimension: 维度名称
        :return: 表名
        """
        # 将minecraft:维度名转换为表名，移除非法字符
        dim_name = dimension.split(':')[-1].lower()
        # 替换所有非字母数字字符为下划线
        dim_name = ''.join(c if c.isalnum() else '_' for c in dim_name)
        return f'chunk_lands_{dim_name}'

    def _get_chunk_key(self, x: int, z: int) -> str:
        """
        获取区块键
        :param x: x坐标
        :param z: z坐标
        :return: 区块键
        """
        chunk_x = x >> 4  # 除以16
        chunk_z = z >> 4
        return f"{chunk_x}_{chunk_z}"

    def _get_affected_chunks(self, min_x: int, max_x: int, min_z: int, max_z: int) -> Set[str]:
        """
        获取受影响的所有区块键
        :return: 区块键集合
        """
        chunk_keys = set()
        start_chunk_x = min_x >> 4
        end_chunk_x = max_x >> 4
        start_chunk_z = min_z >> 4
        end_chunk_z = max_z >> 4

        for chunk_x in range(start_chunk_x, end_chunk_x + 1):
            for chunk_z in range(start_chunk_z, end_chunk_z + 1):
                chunk_keys.add(f"{chunk_x}_{chunk_z}")

        return chunk_keys

    def _register_land_to_chunk_mapping(self, land_id: int, dimension: str,
                                        min_x: int, max_x: int, min_z: int, max_z: int) -> bool:
        """
        将一块领地的 ID 注册到对应维度的区块映射表中（根据 XZ 范围计算涉及的区块并写入）。
        调用前需保证该维度的区块表已存在（可先 _ensure_dimension_table）。
        :return: 是否成功
        """
        try:
            chunk_table = self._get_dimension_table(dimension)
            affected_chunks = self._get_affected_chunks(min_x, max_x, min_z, max_z)
            for chunk_key in affected_chunks:
                existing = self.database_manager.query_one(
                    f"SELECT land_ids FROM {chunk_table} WHERE chunk_key = ?",
                    (chunk_key,)
                )
                if existing:
                    land_ids = json.loads(existing['land_ids'])
                    land_ids.append(land_id)
                    self.database_manager.update(
                        chunk_table,
                        {'land_ids': json.dumps(land_ids)},
                        'chunk_key = ?',
                        (chunk_key,)
                    )
                else:
                    self.database_manager.insert(
                        chunk_table,
                        {'chunk_key': chunk_key, 'land_ids': json.dumps([land_id])}
                    )
            return True
        except Exception as e:
            self.logger.error(f"Register land to chunk mapping error: {str(e)}")
            return False

    def rebuild_chunk_land_mapping(self) -> tuple[bool, str]:
        """
        根据 lands 表当前数据重建所有维度的区块-领地映射表。
        会先删除所有 chunk_lands_* 表再按领地边界重新生成。适用于在数据库里直接改了领地边界后的同步。
        :return: (是否成功, 结果描述信息)
        """
        try:
            tables = self.database_manager.query_all(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'chunk_lands_%'"
            )
            for row in tables:
                self.database_manager.execute(f"DROP TABLE IF EXISTS \"{row['name']}\"")

            lands = self.database_manager.query_all(
                "SELECT land_id, dimension, min_x, max_x, min_z, max_z FROM lands"
            )
            if not lands:
                return True, self.language_manager.GetText('OP_REBUILD_CHUNK_MAPPING_NO_LANDS')

            dimensions_done = set()
            for land in lands:
                dimension = land['dimension']
                if dimension not in dimensions_done:
                    if not self._ensure_dimension_table(dimension):
                        self.logger.warning(f"Rebuild chunk mapping: failed to ensure dimension table for {dimension}")
                        continue
                    dimensions_done.add(dimension)
                self._register_land_to_chunk_mapping(
                    land['land_id'], dimension,
                    land['min_x'], land['max_x'], land['min_z'], land['max_z']
                )

            num_lands = len(lands)
            num_dims = len(dimensions_done)
            return True, self.language_manager.GetText('OP_REBUILD_CHUNK_MAPPING_SUCCESS').format(
                num_dims, num_lands
            )
        except Exception as e:
            self.logger.error(f"Rebuild chunk land mapping error: {str(e)}")
            return False, self.language_manager.GetText('OP_REBUILD_CHUNK_MAPPING_FAILED').format(str(e))

    def create_land(self, owner_xuid: str, land_name: str, dimension: str,
                    min_x: int, max_x: int, min_y: int, max_y: int, min_z: int, max_z: int,
                    tp_x: float, tp_y: float, tp_z: float, owner_paid_money: float = 0.0) -> Optional[int]:
        """创建新领地。owner_paid_money 为购买时玩家实际支付的金钱，出售时按此退款。"""
        try:
            if not self._ensure_dimension_table(dimension):
                return None

            self.database_manager.execute(
                "INSERT INTO lands (owner_xuid, land_name, dimension, min_x, max_x, min_y, max_y, min_z, max_z, tp_x, tp_y, tp_z, shared_users, allow_explosion, allow_public_interact, owner_paid_money) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (owner_xuid, land_name, dimension, min_x, max_x, min_y, max_y, min_z, max_z, tp_x, tp_y, tp_z, '[]', 0, 0, float(owner_paid_money))
            )
            result = self.database_manager.query_one("SELECT last_insert_rowid() as land_id")
            land_id = result['land_id']

            if not self._register_land_to_chunk_mapping(land_id, dimension, min_x, max_x, min_z, max_z):
                self.logger.error("Create land: chunk mapping failed, land_id=%s", land_id)

            return land_id
        except Exception as e:
            self.logger.error(f"Create land error: {str(e)}")
            return None

    def get_land_at_pos(self, dimension: str, x: int, z: int, y: int = None) -> Optional[int]:
        """获取指定位置的领地ID，可选y坐标进行三维精确判断"""
        try:
            x_int = int(x)
            z_int = int(z)

            # 确保维度表存在
            if not self._ensure_dimension_table(dimension):
                return None

            chunk_table = self._get_dimension_table(dimension)
            chunk_key = self._get_chunk_key(x_int, z_int)

            chunk_data = self.database_manager.query_one(
                f"SELECT land_ids FROM {chunk_table} WHERE chunk_key = ?",
                (chunk_key,)
            )

            if not chunk_data:
                return None

            land_ids = json.loads(chunk_data['land_ids'])
            public_land_id = None  # 备选：公共领地（优先级低）
            for land_id in land_ids:
                land_info = self.database_manager.query_one(
                    "SELECT * FROM lands WHERE land_id = ?",
                    (land_id,)
                )
                if land_info and (
                        land_info['min_x'] <= x <= land_info['max_x'] and
                        land_info['min_z'] <= z <= land_info['max_z']
                ):
                    if y is not None:
                        land_min_y = land_info.get('min_y', 0)
                        land_max_y = land_info.get('max_y', 255)
                        if not (land_min_y <= int(y) <= land_max_y):
                            continue
                    # 私人领地（非公共）优先返回；公共领地作为兜底
                    if land_info['owner_xuid'] != self.PUBLIC_LAND_OWNER_XUID:
                        return land_id
                    else:
                        public_land_id = land_id

            return public_land_id
        except Exception as e:
            self.logger.error(f"Get land at pos error: {str(e)}")
            return None

    def delete_land(self, land_id: int) -> bool:
        """删除领地"""
        try:
            # 获取领地信息
            land_info = self.database_manager.query_one(
                "SELECT * FROM lands WHERE land_id = ?",
                (land_id,)
            )

            if not land_info:
                return False

            # 获取对应维度的区块表
            chunk_table = self._get_dimension_table(land_info['dimension'])

            # 从所有相关区块中移除领地ID
            affected_chunks = self._get_affected_chunks(
                land_info['min_x'],
                land_info['max_x'],
                land_info['min_z'],
                land_info['max_z']
            )

            for chunk_key in affected_chunks:
                chunk_data = self.database_manager.query_one(
                    f"SELECT land_ids FROM {chunk_table} WHERE chunk_key = ?",
                    (chunk_key,)
                )
                if chunk_data:
                    land_ids = json.loads(chunk_data['land_ids'])
                    if land_id in land_ids:
                        land_ids.remove(land_id)
                        if land_ids:
                            self.database_manager.update(
                                chunk_table,
                                {'land_ids': json.dumps(land_ids)},
                                'chunk_key = ?',
                                (chunk_key,)
                            )
                        else:
                            self.database_manager.delete(chunk_table, 'chunk_key = ?', (chunk_key,))

            # 删除领地基本信息
            return self.database_manager.delete('lands', 'land_id = ?', (land_id,))
        except Exception as e:
            self.logger.error(f"Delete land error: {str(e)}")
            return False

    def check_land_availability(self, dimension: str, min_x: int, max_x: int, min_y: int, max_y: int, min_z: int, max_z: int) -> tuple[bool, Optional[str], Optional[list]]:
        """
        检查领地范围是否可用（无重叠且满足最小间距）。
        返回 (是否可用, 失败原因键或None, 重叠的领地ID列表或None)
        """
        try:
            # 确保坐标顺序正确
            min_x, max_x = min(min_x, max_x), max(min_x, max_x)
            min_y, max_y = min(min_y, max_y), max(min_y, max_y)
            min_z, max_z = min(min_z, max_z), max(min_z, max_z)

            # 扩展检查范围以包含最小距离（仅XZ方向）
            check_min_x = min_x - self.land_min_distance
            check_max_x = max_x + self.land_min_distance
            check_min_z = min_z - self.land_min_distance
            check_max_z = max_z + self.land_min_distance

            # 获取可能受影响的所有区块
            affected_chunks = self._get_affected_chunks(check_min_x, check_max_x, check_min_z, check_max_z)

            # 确保维度表存在
            if not self._ensure_dimension_table(dimension):
                return False, 'SYSTEM_ERROR', None

            chunk_table = self._get_dimension_table(dimension)

            # 收集所有可能相关的领地ID
            nearby_land_ids = set()
            for chunk_key in affected_chunks:
                chunk_data = self.database_manager.query_one(
                    f"SELECT land_ids FROM {chunk_table} WHERE chunk_key = ?",
                    (chunk_key,)
                )
                if chunk_data:
                    land_ids = json.loads(chunk_data['land_ids'])
                    nearby_land_ids.update(land_ids)

            # 检查每个相关领地，收集所有重叠的领地ID
            overlapping_land_ids = []
            for land_id in nearby_land_ids:
                land_info = self.database_manager.query_one(
                    "SELECT * FROM lands WHERE land_id = ? AND dimension = ?",
                    (land_id, dimension)
                )

                if not land_info:
                    continue

                # 跳过允许玩家在内圈私人领地的公共领地
                if (land_info.get('owner_xuid') == self.PUBLIC_LAND_OWNER_XUID and
                        land_info.get('allow_non_public_land', 0)):
                    continue

                exist_min_y = land_info.get('min_y', 0)
                exist_max_y = land_info.get('max_y', 255)

                # 先判断Y轴是否有重叠，再判断XZ平面
                y_overlap = (min_y <= exist_max_y and max_y >= exist_min_y)
                xz_overlap = (check_min_x <= land_info['max_x'] and check_max_x >= land_info['min_x'] and
                              check_min_z <= land_info['max_z'] and check_max_z >= land_info['min_z'])

                if y_overlap and xz_overlap:
                    overlapping_land_ids.append(land_id)

            if overlapping_land_ids:
                return False, 'LAND_MIN_DISTANCE_NOT_SATISFIED', overlapping_land_ids

            return True, None, None

        except Exception as e:
            self.logger.error(f"[ARC Core]Check land availability error: {str(e)}")
            return False, 'SYSTEM_ERROR', None

    # ─── Sub-land System ────────────────────────────────────────────────────────

    def init_sub_land_table(self) -> bool:
        """初始化子领地数据表"""
        try:
            sub_land_fields = {
                'sub_land_id': 'INTEGER PRIMARY KEY AUTOINCREMENT',
                'parent_land_id': 'INTEGER NOT NULL',
                'owner_xuid': 'TEXT NOT NULL',
                'sub_land_name': 'TEXT NOT NULL',
                'min_x': 'INTEGER NOT NULL',
                'max_x': 'INTEGER NOT NULL',
                'min_y': 'INTEGER NOT NULL DEFAULT 0',
                'max_y': 'INTEGER NOT NULL DEFAULT 255',
                'min_z': 'INTEGER NOT NULL',
                'max_z': 'INTEGER NOT NULL',
                'shared_users': 'TEXT DEFAULT "[]"'
            }
            return self.database_manager.create_table('sub_lands', sub_land_fields)
        except Exception as e:
            print(f"[ARC Core]Init sub_land table error: {str(e)}")
            return False

    def create_sub_land(self, parent_land_id: int, owner_xuid: str, sub_land_name: str,
                        min_x: int, max_x: int, min_y: int, max_y: int,
                        min_z: int, max_z: int) -> Optional[int]:
        """创建子领地，返回新子领地ID，失败返回None"""
        try:
            self.database_manager.execute(
                "INSERT INTO sub_lands (parent_land_id, owner_xuid, sub_land_name, min_x, max_x, min_y, max_y, min_z, max_z, shared_users) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (parent_land_id, owner_xuid, sub_land_name, min_x, max_x, min_y, max_y, min_z, max_z, '[]')
            )
            result = self.database_manager.query_one("SELECT last_insert_rowid() as sub_land_id")
            return result['sub_land_id'] if result else None
        except Exception as e:
            self.logger.error(f"Create sub land error: {str(e)}")
            return None

    def delete_sub_land(self, sub_land_id: int) -> bool:
        """删除子领地"""
        try:
            return self.database_manager.delete('sub_lands', 'sub_land_id = ?', (sub_land_id,))
        except Exception as e:
            self.logger.error(f"Delete sub land error: {str(e)}")
            return False

    def get_sub_land_info(self, sub_land_id: int) -> dict:
        """获取子领地信息字典，不存在返回空字典"""
        try:
            result = self.database_manager.query_one(
                "SELECT * FROM sub_lands WHERE sub_land_id = ?", (sub_land_id,)
            )
            if result:
                return {
                    'sub_land_id': result['sub_land_id'],
                    'parent_land_id': result['parent_land_id'],
                    'owner_xuid': result['owner_xuid'],
                    'sub_land_name': result['sub_land_name'],
                    'min_x': result['min_x'], 'max_x': result['max_x'],
                    'min_y': result.get('min_y', 0), 'max_y': result.get('max_y', 255),
                    'min_z': result['min_z'], 'max_z': result['max_z'],
                    'shared_users': json.loads(result.get('shared_users') or '[]')
                }
            return {}
        except Exception as e:
            self.logger.error(f"Get sub land info error: {str(e)}")
            return {}

    def get_sub_lands_by_parent(self, parent_land_id: int) -> dict:
        """获取某领地下的所有子领地 {sub_land_id: info_dict}"""
        try:
            results = self.database_manager.query_all(
                "SELECT * FROM sub_lands WHERE parent_land_id = ?", (parent_land_id,)
            )
            lands = {}
            for r in results:
                lands[r['sub_land_id']] = {
                    'sub_land_id': r['sub_land_id'],
                    'parent_land_id': r['parent_land_id'],
                    'owner_xuid': r['owner_xuid'],
                    'sub_land_name': r['sub_land_name'],
                    'min_x': r['min_x'], 'max_x': r['max_x'],
                    'min_y': r.get('min_y', 0), 'max_y': r.get('max_y', 255),
                    'min_z': r['min_z'], 'max_z': r['max_z'],
                    'shared_users': json.loads(r.get('shared_users') or '[]')
                }
            return lands
        except Exception as e:
            self.logger.error(f"Get sub lands by parent error: {str(e)}")
            return {}

    def get_sub_lands_by_owner_in_parent(self, parent_land_id: int, owner_xuid: str) -> dict:
        """获取某玩家在指定父领地内拥有的所有子领地"""
        try:
            results = self.database_manager.query_all(
                "SELECT * FROM sub_lands WHERE parent_land_id = ? AND owner_xuid = ?",
                (parent_land_id, owner_xuid)
            )
            lands = {}
            for r in results:
                lands[r['sub_land_id']] = {
                    'sub_land_id': r['sub_land_id'],
                    'parent_land_id': r['parent_land_id'],
                    'owner_xuid': r['owner_xuid'],
                    'sub_land_name': r['sub_land_name'],
                    'min_x': r['min_x'], 'max_x': r['max_x'],
                    'min_y': r.get('min_y', 0), 'max_y': r.get('max_y', 255),
                    'min_z': r['min_z'], 'max_z': r['max_z'],
                    'shared_users': json.loads(r.get('shared_users') or '[]')
                }
            return lands
        except Exception as e:
            self.logger.error(f"Get sub lands by owner error: {str(e)}")
            return {}

    def get_sub_land_at_pos(self, parent_land_id: int, x: int, y: int, z: int) -> Optional[int]:
        """获取指定坐标处的子领地ID（在父领地范围内查询），不存在返回None"""
        try:
            results = self.database_manager.query_all(
                "SELECT sub_land_id, min_x, max_x, min_y, max_y, min_z, max_z FROM sub_lands WHERE parent_land_id = ?",
                (parent_land_id,)
            )
            for r in results:
                sl_min_y = r.get('min_y', 0)
                sl_max_y = r.get('max_y', 255)
                if (r['min_x'] <= x <= r['max_x'] and
                        sl_min_y <= y <= sl_max_y and
                        r['min_z'] <= z <= r['max_z']):
                    return r['sub_land_id']
            return None
        except Exception as e:
            self.logger.error(f"Get sub land at pos error: {str(e)}")
            return None

    def check_sub_land_availability(self, parent_land_id: int,
                                    min_x: int, max_x: int, min_y: int, max_y: int,
                                    min_z: int, max_z: int,
                                    exclude_sub_land_id: int = None) -> tuple:
        """
        检查子领地范围是否可用：
        1. 必须完全在父领地范围内
        2. 不能与同父领地下其他子领地重叠
        返回 (True, None) 或 (False, reason_str)
        """
        try:
            parent_info = self.get_land_info(parent_land_id)
            if not parent_info:
                return False, 'SYSTEM_ERROR'

            p_min_x, p_max_x = parent_info['min_x'], parent_info['max_x']
            p_min_y, p_max_y = parent_info.get('min_y', 0), parent_info.get('max_y', 255)
            p_min_z, p_max_z = parent_info['min_z'], parent_info['max_z']

            if min_x < p_min_x or max_x > p_max_x or min_y < p_min_y or max_y > p_max_y or min_z < p_min_z or max_z > p_max_z:
                return False, 'SUB_LAND_OUT_OF_PARENT'

            siblings = self.database_manager.query_all(
                "SELECT sub_land_id, min_x, max_x, min_y, max_y, min_z, max_z FROM sub_lands WHERE parent_land_id = ?",
                (parent_land_id,)
            )
            for r in siblings:
                if exclude_sub_land_id is not None and r['sub_land_id'] == exclude_sub_land_id:
                    continue
                sl_min_y = r.get('min_y', 0)
                sl_max_y = r.get('max_y', 255)
                if (min_x <= r['max_x'] and max_x >= r['min_x'] and
                        min_y <= sl_max_y and max_y >= sl_min_y and
                        min_z <= r['max_z'] and max_z >= r['min_z']):
                    return False, 'SUB_LAND_OVERLAP'

            return True, None
        except Exception as e:
            self.logger.error(f"Check sub land availability error: {str(e)}")
            return False, 'SYSTEM_ERROR'

    def add_sub_land_shared_user(self, sub_land_id: int, xuid: str) -> bool:
        try:
            info = self.get_sub_land_info(sub_land_id)
            if not info:
                return False
            shared = info['shared_users']
            if xuid in shared:
                return False
            shared.append(xuid)
            return bool(self.database_manager.execute(
                "UPDATE sub_lands SET shared_users = ? WHERE sub_land_id = ?",
                (json.dumps(shared), sub_land_id)
            ))
        except Exception as e:
            self.logger.error(f"Add sub land shared user error: {str(e)}")
            return False

    def remove_sub_land_shared_user(self, sub_land_id: int, xuid: str) -> bool:
        try:
            info = self.get_sub_land_info(sub_land_id)
            if not info:
                return False
            shared = info['shared_users']
            if xuid not in shared:
                return False
            shared.remove(xuid)
            return bool(self.database_manager.execute(
                "UPDATE sub_lands SET shared_users = ? WHERE sub_land_id = ?",
                (json.dumps(shared), sub_land_id)
            ))
        except Exception as e:
            self.logger.error(f"Remove sub land shared user error: {str(e)}")
            return False

    def rename_sub_land(self, sub_land_id: int, new_name: str) -> bool:
        try:
            return bool(self.database_manager.execute(
                "UPDATE sub_lands SET sub_land_name = ? WHERE sub_land_id = ?",
                (new_name, sub_land_id)
            ))
        except Exception as e:
            self.logger.error(f"Rename sub land error: {str(e)}")
            return False

    # ─── End Sub-land System ────────────────────────────────────────────────────

    def get_player_land_count(self, xuid: str) -> int:
        """
        获取玩家拥有的领地数量
        :param xuid: 玩家XUID
        :return: 领地数量
        """
        try:
            result = self.database_manager.query_one(
                "SELECT COUNT(*) as count FROM lands WHERE owner_xuid = ?",
                (xuid,)
            )
            return result['count'] if result else 0
        except Exception as e:
            self.logger.error(f"Get player land count error: {str(e)}")
            return 0

    def get_player_lands(self, xuid: str) -> dict[int, dict]:
        """
        获取玩家拥有的所有领地信息
        :param xuid: 玩家XUID
        :return: 字典 {领地ID: {
            'land_name': 领地名称,
            'dimension': 维度,
            'min_x': 最小X坐标,
            'max_x': 最大X坐标,
            'min_z': 最小Z坐标,
            'max_z': 最大Z坐标,
            'tp_x': 传送点X坐标,
            'tp_y': 传送点Y坐标,
            'tp_z': 传送点Z坐标,
            'shared_users': 共享玩家XUID列表
        }}
        """
        try:
            results = self.database_manager.query_all(
                "SELECT * FROM lands WHERE owner_xuid = ?",
                (xuid,)
            )

            lands_info = {}
            for land in results:
                lands_info[land['land_id']] = {
                    'land_name': land['land_name'],
                    'dimension': land['dimension'],
                    'min_x': land['min_x'],
                    'max_x': land['max_x'],
                    'min_y': land.get('min_y', 0),
                    'max_y': land.get('max_y', 255),
                    'min_z': land['min_z'],
                    'max_z': land['max_z'],
                    'tp_x': land['tp_x'],
                    'tp_y': land['tp_y'],
                    'tp_z': land['tp_z'],
                    'shared_users': json.loads(land['shared_users']),
                    'allow_explosion': bool(land.get('allow_explosion', 0)),
                    'allow_public_interact': bool(land.get('allow_public_interact', 0)),
                    'allow_actor_interaction': bool(land.get('allow_actor_interaction', 0)),
                    'allow_actor_damage': bool(land.get('allow_actor_damage', 0)),
                    'allow_non_public_land': bool(land.get('allow_non_public_land', 0))
                }

            return lands_info

        except Exception as e:
            self.logger.error(f"Get player lands error: {str(e)}")
            return {}

    def get_all_lands(self) -> Dict[int, dict]:
        """
        获取全服务器所有领地
        :return: {land_id: land_info} 格式的字典，land_info 与 get_land_info 返回结构一致（含 owner_xuid）
        """
        try:
            results = self.database_manager.query_all(
                "SELECT * FROM lands ORDER BY land_id"
            )
            lands_info = {}
            for land in results:
                lands_info[land['land_id']] = {
                    'land_name': land['land_name'],
                    'dimension': land['dimension'],
                    'min_x': land['min_x'],
                    'max_x': land['max_x'],
                    'min_y': land.get('min_y', 0),
                    'max_y': land.get('max_y', 255),
                    'min_z': land['min_z'],
                    'max_z': land['max_z'],
                    'tp_x': land['tp_x'],
                    'tp_y': land['tp_y'],
                    'tp_z': land['tp_z'],
                    'shared_users': json.loads(land['shared_users']),
                    'owner_xuid': land['owner_xuid'],
                    'allow_explosion': bool(land.get('allow_explosion', 0)),
                    'allow_public_interact': bool(land.get('allow_public_interact', 0)),
                    'allow_actor_interaction': bool(land.get('allow_actor_interaction', 0)),
                    'allow_actor_damage': bool(land.get('allow_actor_damage', 0)),
                    'allow_non_public_land': bool(land.get('allow_non_public_land', 0))
                }
            return lands_info
        except Exception as e:
            self.logger.error(f"Get all lands error: {str(e)}")
            return {}

    def get_land_info(self, land_id: int) -> dict:
        """
        根据领地ID获取领地信息
        :param land_id: 领地ID
        :return: 领地信息字典 {
            'land_name': 领地名称,
            'dimension': 维度,
            'min_x': 最小X坐标,
            'max_x': 最大X坐标,
            'min_z': 最小Z坐标,
            'max_z': 最大Z坐标,
            'tp_x': 传送点X坐标,
            'tp_y': 传送点Y坐标,
            'tp_z': 传送点Z坐标,
            'shared_users': 共享玩家XUID列表,
            'owner_xuid': 拥有者XUID
        } 不存在则返回空字典
        """
        try:
            result = self.database_manager.query_one(
                "SELECT * FROM lands WHERE land_id = ?",
                (land_id,)
            )

            if result:
                return {
                    'land_name': result['land_name'],
                    'dimension': result['dimension'],
                    'min_x': result['min_x'],
                    'max_x': result['max_x'],
                    'min_y': result.get('min_y', 0),
                    'max_y': result.get('max_y', 255),
                    'min_z': result['min_z'],
                    'max_z': result['max_z'],
                    'tp_x': result['tp_x'],
                    'tp_y': result['tp_y'],
                    'tp_z': result['tp_z'],
                    'shared_users': json.loads(result['shared_users']),
                    'owner_xuid': result['owner_xuid'],
                    'allow_explosion': bool(result.get('allow_explosion', 0)),
                    'allow_public_interact': bool(result.get('allow_public_interact', 0)),
                    'allow_actor_interaction': bool(result.get('allow_actor_interaction', 0)),
                    'allow_actor_damage': bool(result.get('allow_actor_damage', 0)),
                    'allow_non_public_land': bool(result.get('allow_non_public_land', 0))
                }
            return {}

        except Exception as e:
            self.logger.error(f"Get land info error: {str(e)}")
            return {}

    PUBLIC_LAND_OWNER_XUID = "0"  # 公共领地的 owner_xuid 固定为 "0"
    
    def is_public_land(self, land_id: int) -> bool:
        """判断领地是否为公共领地"""
        return self.get_land_owner(land_id) == self.PUBLIC_LAND_OWNER_XUID

    def _get_public_land_protected_entities(self) -> Set[str]:
        """获取公共领地白名单保护生物类型集合（配置 PUBLIC_LAND_PROTECTED_ENTITIES，逗号分隔）"""
        raw = self.setting_manager.GetSetting('PUBLIC_LAND_PROTECTED_ENTITIES')
        if not raw or not str(raw).strip():
            return set()
        return {s.strip() for s in str(raw).split(',') if s.strip()}
    
    def get_land_display_owner_name(self, land_id: int) -> str:
        """获取领地显示的所有者名称：公共领地显示翻译后的「公共领地」，否则显示玩家名"""
        owner_xuid = self.get_land_owner(land_id)
        if owner_xuid == self.PUBLIC_LAND_OWNER_XUID:
            return self.language_manager.GetText('PUBLIC_LAND_NAME')
        return self.get_player_name_by_xuid(owner_xuid) or owner_xuid or ''
    
    def get_land_owner(self, land_id: int) -> str:
        """
        获取领地拥有者的XUID
        :param land_id: 领地ID
        :return: 拥有者XUID，不存在则返回空字符串
        """
        try:
            result = self.database_manager.query_one(
                "SELECT owner_xuid FROM lands WHERE land_id = ?",
                (land_id,)
            )
            return result['owner_xuid'] if result else ""

        except Exception as e:
            self.logger.error(f"Get land owner error: {str(e)}")
            return ""

    def set_land_as_public(self, land_id: int) -> bool:
        """
        将领地设为公共领地（owner_xuid 设为 "0"），并默认开放方块互动、生物互动、生物伤害
        :param land_id: 领地ID
        :return: 是否成功
        """
        try:
            if not self.get_land_info(land_id):
                return False
            return self.database_manager.execute(
                "UPDATE lands SET owner_xuid = ?, owner_paid_money = 0, allow_public_interact = 1, allow_actor_interaction = 1, allow_actor_damage = 1 WHERE land_id = ?",
                (self.PUBLIC_LAND_OWNER_XUID, land_id)
            )
        except Exception as e:
            self.logger.error(f"Set land as public error: {str(e)}")
            return False
    
    def rename_land(self, land_id: int, new_name: str) -> tuple[bool, str]:
        """
        修改领地名称
        :param land_id: 领地ID
        :param new_name: 新的领地名称
        :return: (是否成功, 消息)
        """
        try:
            # 检查领地是否存在
            if not self.get_land_info(land_id):
                return False, "领地不存在"

            # 更新领地名称
            self.database_manager.execute(
                "UPDATE lands SET land_name = ? WHERE land_id = ?",
                (new_name, land_id)
            )

            return True, "领地名称修改成功"

        except Exception as e:
            self.logger.error(f"Rename land error: {str(e)}")
            return False, f"修改领地名称时发生错误: {str(e)}"

    def set_land_teleport_point(self, land_id: int, x: int, y: int, z: int) -> tuple[bool, str]:
        """
        设置领地传送点
        :param land_id: 领地ID
        :param x: 传送点X坐标
        :param y: 传送点Y坐标
        :param z: 传送点Z坐标
        :return: (是否成功, 消息)
        """
        try:
            # 获取领地信息检查是否存在
            land_info = self.get_land_info(land_id)
            if not land_info:
                return False, "领地不存在"

            # 检查传送点是否在领地范围内
            if not (land_info['min_x'] <= x <= land_info['max_x'] and
                    land_info['min_z'] <= z <= land_info['max_z']):
                return False, "传送点必须在领地范围内"

            # 更新传送点坐标
            self.database_manager.execute(
                "UPDATE lands SET tp_x = ?, tp_y = ?, tp_z = ? WHERE land_id = ?",
                (x, y, z, land_id)
            )

            return True, "领地传送点设置成功"

        except Exception as e:
            self.logger.error(f"Set land teleport point error: {str(e)}")
            return False, f"设置传送点时发生错误: {str(e)}"

    def get_land_teleport_point(self, land_id: int) -> tuple[int, int, int] | None:
        """
        获取领地传送点坐标
        :param land_id: 领地ID
        :return: (x, y, z)坐标元组, 如果领地不存在则返回None
        """
        try:
            result = self.database_manager.query_one(
                "SELECT tp_x, tp_y, tp_z FROM lands WHERE land_id = ?",
                (land_id,)
            )

            if result:
                return (result['tp_x'], result['tp_y'], result['tp_z'])
            return None

        except Exception as e:
            self.logger.error(f"Get land teleport point error: {str(e)}")
            return None

    def get_land_dimension(self, land_id: int) -> str:
        """
        获取领地所在维度
        :param land_id: 领地ID
        :return: 维度字符串 ('minecraft:overworld'/'minecraft:nether'/'minecraft:the_end')
                如果领地不存在则返回空字符串
        """
        try:
            result = self.database_manager.query_one(
                "SELECT dimension FROM lands WHERE land_id = ?",
                (land_id,)
            )
            return result['dimension'] if result else ""

        except Exception as e:
            self.logger.error(f"Get land dimension error: {str(e)}")
            return ""

    def get_land_name(self, land_id: int) -> str:
        """
        获取领地名称
        :param land_id: 领地ID
        :return: 领地名称，如果领地不存在则返回空字符串
        """
        try:
            result = self.database_manager.query_one(
                "SELECT land_name FROM lands WHERE land_id = ?",
                (land_id,)
            )
            return result['land_name'] if result else ""

        except Exception as e:
            self.logger.error(f"Get land name error: {str(e)}")
            return ""

    # Land System UI
    def show_land_main_menu(self, player: Player):
        land_main_menu = ActionForm(
            title=self.language_manager.GetText('LAND_MAIN_MENU_TITLE'),
            content=self.language_manager.GetText('LAND_MAIN_MENU_CONTENT').format(
                self.get_player_land_count(str(player.xuid)))
        )
        land_main_menu.add_button(self.language_manager.GetText('LAND_MAIN_MENU_MANAGE_LAND_TEXT'),
                                  on_click=self.show_own_land_menu)
        land_main_menu.add_button(self.language_manager.GetText('LAND_MAIN_MENU_CREATE_NEW_LAND_TEXT'),
                                  on_click=self.show_create_new_land_guide)
        land_main_menu.add_button(self.language_manager.GetText('LAND_MAIN_MENU_CHECK_CURRENT_LAND_TEXT'),
                                  on_click=self.show_current_land_info)
        # 返回
        land_main_menu.add_button(self.language_manager.GetText('RETURN_BUTTON_TEXT'),
                                  on_click=self.show_main_menu)
        player.send_form(land_main_menu)

    def show_own_land_menu(self, player: Player):
        player_land_num = self.get_player_land_count(str(player.xuid))
        if player_land_num == 0:
            own_land_panel = ActionForm(
                title=self.language_manager.GetText('OWN_LAND_PANEL_TITLE'),
                content=self.language_manager.GetText('OWN_LAND_PANEL_NO_LAND_EXIST_CONTENT').format(
                    self.get_player_land_count(str(player.xuid))),
                on_close=self.show_land_main_menu
            )
            player.send_form(own_land_panel)
            return
        else:
            own_land_panel = ActionForm(
                title=self.language_manager.GetText('OWN_LAND_PANEL_TITLE'),
                on_close=self.show_land_main_menu
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
            on_close=self.show_own_land_menu
        )
        
        # 领地传送按钮（显示价格）
        land_teleport_text = self.language_manager.GetText('LAND_DETAIL_PANEL_TELEPORT_BUTTON_TEXT')
        if self.teleport_cost_land > 0:
            land_teleport_text = self.language_manager.GetText('TELEPORT_BUTTON_WITH_COST').format(land_teleport_text, self.teleport_cost_land)
        land_detail_panel.add_button(land_teleport_text, on_click=lambda p=player, l_id=land_id: self.teleport_to_land(p, l_id))
        land_detail_panel.add_button(self.language_manager.GetText('LAND_DETAIL_PANEL_RENAME_BUTTON_TEXT'),
                                     on_click=lambda p=player, l_id=land_id: self.show_rename_own_land_panel(p, l_id)
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
        land_detail_panel.add_button(self.language_manager.GetText('LAND_PUBLIC_INTERACT_SETTING_BUTTON_TEXT'),
                                     on_click=lambda p=player, l_id=land_id: self.show_land_public_interact_setting_panel(p, l_id)
                                     )
        land_detail_panel.add_button(self.language_manager.GetText('LAND_DETAIL_PANEL_MANAGE_SUB_LAND_BUTTON_TEXT'),
                                     on_click=lambda p=player, l_id=land_id: self.show_sub_land_manage_panel(p, l_id)
                                     )
        land_detail_panel.add_button(self.language_manager.GetText('LAND_DETAIL_PANEL_TRANSFER_LAND_BUTTON_TEXT'),
                                     on_click=lambda p=player, l_id=land_id: self.show_transfer_land_panel(p, l_id)
                                     )
        land_detail_panel.add_button(self.language_manager.GetText('LAND_DETAIL_PANEL_DELETE_LAND_BUTTON_TEXT'),
                                     on_click=lambda p=player, l_id=land_id: self.confirm_delete_land(p, l_id)
                                     )
        player.send_form(land_detail_panel)

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
            on_close=self.show_own_land_menu,
            on_submit=try_change_name
        )
        player.send_form(rename_panel)

    def set_player_pos_as_land_tp_pos(self, player: Player, land_id: int):
        on_land_id = self.get_land_at_pos(player.location.dimension.name, math.floor(player.location.x), math.floor(player.location.z))
        if on_land_id is None or on_land_id != land_id:
            result = self.language_manager.GetText('SET_LAND_TP_POS_FAIL_OUT_LAND')
        else:
            new_pos = (math.floor(player.location.x), math.floor(player.location.y), math.floor(player.location.z))
            self.set_land_teleport_point(land_id, new_pos[0], new_pos[1], new_pos[2])
            result = self.language_manager.GetText('SET_LAND_TP_POS_SUCCESS').format(land_id, new_pos)
        result_panel = ActionForm(
            title=self.language_manager.GetText('SET_LAND_TP_POS_RESULT_TITLE'),
            content=result,
            on_close=lambda p=player, l_id=land_id, l_info=self.get_land_info(land_id): self.show_own_land_detail_panel(p, l_id, l_info)
        )
        player.send_form(result_panel)

    def teleport_to_land(self, player: Player, land_id: int):
        # 检查费用
        if self.teleport_cost_land > 0:
            player_money = self.get_player_money(player)
            if player_money < self.teleport_cost_land:
                player.send_message(self.language_manager.GetText('TELEPORT_COST_NOT_ENOUGH_MONEY').format(
                    self._format_money_display(self.teleport_cost_land),
                    self._format_money_display(player_money)
                ))
                return
            
            # 扣除费用
            if self.decrease_player_money(player, self.teleport_cost_land):
                player.send_message(self.language_manager.GetText('TELEPORT_COST_DEDUCTED').format(
                    self._format_money_display(self.teleport_cost_land),
                    self._format_money_display(self.get_player_money(player))
                ))
            else:
                player.send_message(self.language_manager.GetText('SYSTEM_ERROR'))
                return
        
        tp_target_pos = self.get_land_teleport_point(land_id)
        self.server.scheduler.run_task(self, lambda p=player, l_id=land_id, pos=tp_target_pos: self.delay_teleport_to_land(p, l_id, pos), delay=45)
        player.send_message(self.language_manager.GetText('READY_TELEPORT_TO_LAND').format(land_id))

    def delay_teleport_to_land(self, player: Player, land_id: int, position: tuple):
        player.send_message(self.language_manager.GetText('TELEPORT_TO_LAND_START_HINT').format(land_id))
        land_dimension = self.get_land_dimension(land_id)
        self.server.dispatch_command(self.server.command_sender, self.generate_tp_command_to_position(player.name, position, land_dimension))

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
            on_close=self.show_own_land_detail_panel(player, land_id, deleta_land_info)
        )
        confirm_panel.add_button(self.language_manager.GetText('CONFIRM_DELETE_LAND_BUTTON').format(land_id),
                                 on_click=lambda p=player, l_id=land_id, r_m=return_money: self.try_delete_land(p, l_id, r_m)
                                 )
        player.send_form(confirm_panel)

    def try_delete_land(self, player: Player, land_id: int, return_money: int):
        r = self.delete_land(land_id)
        if r:
            self.increase_player_money(player, return_money)
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
                on_close=lambda p=player, l_id=land_id, l_info=self.get_land_info(land_id): self.show_own_land_detail_panel(p, l_id, l_info)
            )
            player.send_form(no_players_panel)
            return

        transfer_menu = ActionForm(
            title=self.language_manager.GetText('TRANSFER_LAND_PANEL_TITLE'),
            content=self.language_manager.GetText('TRANSFER_LAND_PANEL_CONTENT'),
            on_close=lambda p=player, l_id=land_id, l_info=self.get_land_info(land_id): self.show_own_land_detail_panel(p, l_id, l_info)
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
            player.send_message(self.language_manager.GetText('SYSTEM_ERROR'))
            return

        confirm_panel = ActionForm(
            title=self.language_manager.GetText('CONFIRM_TRANSFER_LAND_TITLE').format(land_id),
            content=self.language_manager.GetText('CONFIRM_TRANSFER_LAND_CONTENT').format(
                land_id, 
                land_info['land_name'], 
                target_player.name
            ),
            on_close=lambda p=player, l_id=land_id, l_info=land_info: self.show_own_land_detail_panel(p, l_id, l_info)
        )
        confirm_panel.add_button(
            self.language_manager.GetText('CONFIRM_TRANSFER_LAND_BUTTON'),
            on_click=lambda p=player, l_id=land_id, t=target_player: self.try_transfer_land(p, l_id, t)
        )
        player.send_form(confirm_panel)

    def transfer_land(self, land_id: int, new_owner_xuid: str) -> bool:
        """
        移交领地给新的拥有者
        :param land_id: 领地ID
        :param new_owner_xuid: 新拥有者的XUID
        :return: 是否成功
        """
        try:
            # 检查领地是否存在
            if not self.get_land_info(land_id):
                return False

            # 更新领地拥有者
            self.database_manager.execute(
                "UPDATE lands SET owner_xuid = ? WHERE land_id = ?",
                (new_owner_xuid, land_id)
            )

            return True

        except Exception as e:
            self.logger.error(f"Transfer land error: {str(e)}")
            return False

    def try_transfer_land(self, player: Player, land_id: int, target_player: Player):
        """尝试移交领地"""
        land_info = self.get_land_info(land_id)
        if not land_info:
            player.send_message(self.language_manager.GetText('SYSTEM_ERROR'))
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
            player.send_message(self.language_manager.GetText('TRANSFER_LAND_SUCCESS').format(land_id, target_player.name))
            
            # 通知目标玩家
            target_player.send_message(self.language_manager.GetText('TRANSFER_LAND_NOTIFICATION').format(
                player.name, 
                land_id, 
                land_info['land_name']
            ))
        else:
            player.send_message(self.language_manager.GetText('TRANSFER_LAND_FAILED').format(land_id))
        
        self.show_own_land_menu(player)

    def show_land_auth_manage_panel(self, player: Player, land_id: int):
        """显示领地授权管理面板"""
        land_info = self.get_land_info(land_id)
        if not land_info:
            player.send_message(self.language_manager.GetText('SYSTEM_ERROR'))
            return

        auth_panel = ActionForm(
            title=self.language_manager.GetText('LAND_AUTH_MANAGE_TITLE'),
            on_close=lambda p=player, l_id=land_id, l_info=land_info: self.show_own_land_detail_panel(p, l_id, l_info)
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
                on_close=lambda p=player, l_id=land_id: self.show_land_auth_manage_panel(p, l_id)
            )
            player.send_form(no_players_panel)
            return

        add_auth_panel = ActionForm(
            title=self.language_manager.GetText('LAND_AUTH_ADD_PANEL_TITLE'),
            content=self.language_manager.GetText('LAND_AUTH_SELECT_PLAYER_CONTENT'),
            on_close=lambda p=player, l_id=land_id: self.show_land_auth_manage_panel(p, l_id)
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
                on_close=lambda p=player, l_id=land_id: self.show_land_auth_manage_panel(p, l_id)
            )
            player.send_form(no_auth_panel)
            return

        remove_auth_panel = ActionForm(
            title=self.language_manager.GetText('LAND_AUTH_REMOVE_PANEL_TITLE'),
            content=self.language_manager.GetText('LAND_AUTH_SELECT_REMOVE_CONTENT'),
            on_close=lambda p=player, l_id=land_id: self.show_land_auth_manage_panel(p, l_id)
        )
        
        for shared_uuid in land_info['shared_users']:
            user_name = self.get_player_name_by_xuid(shared_uuid)
            if user_name:
                remove_auth_panel.add_button(
                    self.language_manager.GetText('LAND_AUTH_REMOVE_TARGET_BUTTON').format(user_name),
                    on_click=lambda p=player, l_id=land_id, uuid=shared_uuid, name=user_name: self.remove_land_auth(p, l_id, uuid, name)
                )
        
        player.send_form(remove_auth_panel)

    def add_land_auth(self, player: Player, land_id: int, target_player: Player):
        """添加领地授权"""
        try:
            land_info = self.get_land_info(land_id)
            if not land_info:
                player.send_message(self.language_manager.GetText('SYSTEM_ERROR'))
                return

            target_xuid = str(target_player.xuid)
            
            # 检查是否已经授权
            if target_xuid in land_info['shared_users']:
                player.send_message(self.language_manager.GetText('LAND_AUTH_ALREADY_EXISTS').format(target_player.name))
                self.show_land_auth_manage_panel(player, land_id)
                return

            # 添加授权
            shared_users = land_info['shared_users']
            shared_users.append(target_xuid)
            
            success = self.database_manager.execute(
                "UPDATE lands SET shared_users = ? WHERE land_id = ?",
                (json.dumps(shared_users), land_id)
            )
            
            if success:
                player.send_message(self.language_manager.GetText('LAND_AUTH_SUCCESS_ADD').format(land_id, target_player.name))
                target_player.send_message(self.language_manager.GetText('LAND_AUTH_NOTIFICATION').format(
                    player.name, land_id, land_info['land_name']
                ))
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
                player.send_message(self.language_manager.GetText('SYSTEM_ERROR'))
                return

            # 检查是否存在授权
            if target_uuid not in land_info['shared_users']:
                player.send_message(self.language_manager.GetText('LAND_AUTH_NOT_EXISTS').format(target_name))
                self.show_land_auth_manage_panel(player, land_id)
                return

            # 移除授权
            shared_users = land_info['shared_users']
            shared_users.remove(target_uuid)
            
            success = self.database_manager.execute(
                "UPDATE lands SET shared_users = ? WHERE land_id = ?",
                (json.dumps(shared_users), land_id)
            )
            
            if success:
                player.send_message(self.language_manager.GetText('LAND_AUTH_SUCCESS_REMOVE').format(target_name, land_id))
                # 通知被移除授权的玩家（如果在线）
                target_player = self.server.get_player(target_name)
                if target_player:
                    target_player.send_message(self.language_manager.GetText('LAND_AUTH_REMOVE_NOTIFICATION').format(
                        player.name, land_id, land_info['land_name']
                    ))
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
            player.send_message(self.language_manager.GetText('SYSTEM_ERROR'))
            return

        current_allow_explosion = land_info.get('allow_explosion', False)
        status_text = self.language_manager.GetText('LAND_EXPLOSION_STATUS_ENABLED') if current_allow_explosion else self.language_manager.GetText('LAND_EXPLOSION_STATUS_DISABLED')
        
        explosion_setting_panel = ActionForm(
            title=self.language_manager.GetText('LAND_EXPLOSION_SETTING_TITLE'),
            content=self.language_manager.GetText('LAND_EXPLOSION_CURRENT_STATUS').format(status_text),
            on_close=lambda p=player, l_id=land_id, l_info=land_info: self.show_own_land_detail_panel(p, l_id, l_info)
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
            player.send_message(self.language_manager.GetText('SYSTEM_ERROR'))
            return

        current_allow_public_interact = land_info.get('allow_public_interact', False)
        status_text = self.language_manager.GetText('LAND_PUBLIC_INTERACT_STATUS_ENABLED') if current_allow_public_interact else self.language_manager.GetText('LAND_PUBLIC_INTERACT_STATUS_DISABLED')
        
        public_interact_setting_panel = ActionForm(
            title=self.language_manager.GetText('LAND_PUBLIC_INTERACT_SETTING_TITLE'),
            content=self.language_manager.GetText('LAND_PUBLIC_INTERACT_CURRENT_STATUS').format(status_text),
            on_close=lambda p=player, l_id=land_id, l_info=land_info: self.show_own_land_detail_panel(p, l_id, l_info)
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

    def toggle_land_public_interact_setting(self, player: Player, land_id: int, allow_public_interact: bool):
        """切换领地方块互动开放设置"""
        try:
            success = self.database_manager.execute(
                "UPDATE lands SET allow_public_interact = ? WHERE land_id = ?",
                (1 if allow_public_interact else 0, land_id)
            )
            
            if success:
                if allow_public_interact:
                    player.send_message(self.language_manager.GetText('LAND_PUBLIC_INTERACT_SETTING_UPDATED_ENABLE').format(land_id))
                else:
                    player.send_message(self.language_manager.GetText('LAND_PUBLIC_INTERACT_SETTING_UPDATED_DISABLE').format(land_id))
            else:
                player.send_message(self.language_manager.GetText('LAND_PUBLIC_INTERACT_SETTING_FAILED'))
                
            # 返回领地详情面板
            land_info = self.get_land_info(land_id)
            self.show_own_land_detail_panel(player, land_id, land_info)
            
        except Exception as e:
            self.logger.error(f"Update land public interact setting error: {str(e)}")
            player.send_message(self.language_manager.GetText('SYSTEM_ERROR'))

    def toggle_land_explosion_setting(self, player: Player, land_id: int, allow_explosion: bool):
        """切换领地爆炸保护设置"""
        try:
            success = self.database_manager.execute(
                "UPDATE lands SET allow_explosion = ? WHERE land_id = ?",
                (1 if allow_explosion else 0, land_id)
            )
            
            if success:
                if allow_explosion:
                    player.send_message(self.language_manager.GetText('LAND_EXPLOSION_SETTING_UPDATED_ENABLE').format(land_id))
                else:
                    player.send_message(self.language_manager.GetText('LAND_EXPLOSION_SETTING_UPDATED_DISABLE').format(land_id))
            else:
                player.send_message(self.language_manager.GetText('LAND_EXPLOSION_SETTING_FAILED'))
                
        except Exception as e:
            self.logger.error(f"Toggle land explosion setting error: {str(e)}")
            player.send_message(self.language_manager.GetText('LAND_EXPLOSION_SETTING_FAILED'))
        
        # 返回领地详情页面
        land_info = self.get_land_info(land_id)
        if land_info:
            self.show_own_land_detail_panel(player, land_id, land_info)

    def show_land_actor_interaction_setting_panel(self, player: Player, land_id: int):
        """显示领地生物互动设置面板"""
        land_info = self.get_land_info(land_id)
        if not land_info:
            player.send_message(self.language_manager.GetText('SYSTEM_ERROR'))
            return

        current_allow_actor_interaction = land_info.get('allow_actor_interaction', False)
        status_text = self.language_manager.GetText('LAND_ACTOR_INTERACTION_STATUS_ENABLED') if current_allow_actor_interaction else self.language_manager.GetText('LAND_ACTOR_INTERACTION_STATUS_DISABLED')
        
        actor_interaction_setting_panel = ActionForm(
            title=self.language_manager.GetText('LAND_ACTOR_INTERACTION_SETTING_TITLE'),
            content=self.language_manager.GetText('LAND_ACTOR_INTERACTION_CURRENT_STATUS').format(status_text),
            on_close=lambda p=player, l_id=land_id, l_info=land_info: self.show_own_land_detail_panel(p, l_id, l_info)
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
            player.send_message(self.language_manager.GetText('SYSTEM_ERROR'))
            return

        current_allow_actor_damage = land_info.get('allow_actor_damage', False)
        status_text = self.language_manager.GetText('LAND_ACTOR_DAMAGE_STATUS_ENABLED') if current_allow_actor_damage else self.language_manager.GetText('LAND_ACTOR_DAMAGE_STATUS_DISABLED')
        
        actor_damage_setting_panel = ActionForm(
            title=self.language_manager.GetText('LAND_ACTOR_DAMAGE_SETTING_TITLE'),
            content=self.language_manager.GetText('LAND_ACTOR_DAMAGE_CURRENT_STATUS').format(status_text),
            on_close=lambda p=player, l_id=land_id, l_info=land_info: self.show_own_land_detail_panel(p, l_id, l_info)
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
        
        player.send_form(actor_damage_setting_panel)

    def toggle_land_actor_interaction_setting(self, player: Player, land_id: int, allow_actor_interaction: bool):
        """切换领地生物互动设置"""
        try:
            success = self.database_manager.execute(
                "UPDATE lands SET allow_actor_interaction = ? WHERE land_id = ?",
                (1 if allow_actor_interaction else 0, land_id)
            )
            
            if success:
                if allow_actor_interaction:
                    player.send_message(self.language_manager.GetText('LAND_ACTOR_INTERACTION_SETTING_UPDATED_ENABLE').format(land_id))
                else:
                    player.send_message(self.language_manager.GetText('LAND_ACTOR_INTERACTION_SETTING_UPDATED_DISABLE').format(land_id))
            else:
                player.send_message(self.language_manager.GetText('LAND_ACTOR_INTERACTION_SETTING_FAILED'))
                
        except Exception as e:
            self.logger.error(f"Toggle land actor interaction setting error: {str(e)}")
            player.send_message(self.language_manager.GetText('LAND_ACTOR_INTERACTION_SETTING_FAILED'))
        
        # 返回领地详情页面
        land_info = self.get_land_info(land_id)
        if land_info:
            self.show_own_land_detail_panel(player, land_id, land_info)

    def toggle_land_actor_damage_setting(self, player: Player, land_id: int, allow_actor_damage: bool):
        """切换领地生物攻击设置"""
        try:
            success = self.database_manager.execute(
                "UPDATE lands SET allow_actor_damage = ? WHERE land_id = ?",
                (1 if allow_actor_damage else 0, land_id)
            )
            
            if success:
                if allow_actor_damage:
                    player.send_message(self.language_manager.GetText('LAND_ACTOR_DAMAGE_SETTING_UPDATED_ENABLE').format(land_id))
                else:
                    player.send_message(self.language_manager.GetText('LAND_ACTOR_DAMAGE_SETTING_UPDATED_DISABLE').format(land_id))
            else:
                player.send_message(self.language_manager.GetText('LAND_ACTOR_DAMAGE_SETTING_FAILED'))
                
        except Exception as e:
            self.logger.error(f"Toggle land actor damage setting error: {str(e)}")
            player.send_message(self.language_manager.GetText('LAND_ACTOR_DAMAGE_SETTING_FAILED'))
        
        # 返回领地详情页面
        land_info = self.get_land_info(land_id)
        if land_info:
            self.show_own_land_detail_panel(player, land_id, land_info)

    def show_create_new_land_guide(self, player: Player):
        """显示创建领地的坐标输入表单，可预填上次设定的值"""
        cached = self.player_new_land_creation_info.get(player.name, {})
        default_min_x = str(cached.get('min_x', math.floor(player.location.x)))
        default_max_x = str(cached.get('max_x', math.floor(player.location.x)))
        default_min_y = str(cached.get('min_y', math.floor(player.location.y)))
        default_max_y = str(cached.get('max_y', math.floor(player.location.y)))
        default_min_z = str(cached.get('min_z', math.floor(player.location.z)))
        default_max_z = str(cached.get('max_z', math.floor(player.location.z)))

        controls = [
            Label(text=self.language_manager.GetText('CREATE_LAND_FORM_DIMENSION_LABEL').format(player.location.dimension.name)),
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
                # data[0] is Label (ignored), data[1..6] are the text inputs
                min_x_str = data[1]
                max_x_str = data[2]
                min_y_str = data[3]
                max_y_str = data[4]
                min_z_str = data[5]
                max_z_str = data[6]
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
                self.player_new_land_creation_info[p.name] = {
                    'dimension': p.location.dimension.name,
                    'min_x': min_x, 'max_x': max_x,
                    'min_y': min_y, 'max_y': max_y,
                    'min_z': min_z, 'max_z': max_z
                }
                self._visualize_pending_land(p)
                self.show_new_land_info(p)
            except Exception as e:
                self.logger.error(f"Create land form submit error: {str(e)}")
                p.send_message(self.language_manager.GetText('SYSTEM_ERROR'))

        form = ModalForm(
            title=self.language_manager.GetText('CREATE_LAND_FORM_TITLE'),
            controls=controls,
            on_submit=on_submit,
            on_close=self.show_land_main_menu
        )
        player.send_form(form)

    def show_current_land_info(self, player: Player):
        """显示玩家当前位置的领地信息并绘制粒子边界"""
        try:
            # 获取玩家当前位置
            pos = self.get_player_position_vector(player)
            if not pos:
                player.send_message(self.language_manager.GetText('SYSTEM_ERROR'))
                return
            
            x, y, z = pos
            dimension = player.dimension.name.lower()
            
            # 获取当前位置的领地ID
            land_id = self.get_land_at_pos(dimension, x, z)
            
            if not land_id:
                player.send_message(self.language_manager.GetText('LAND_CURRENT_POSITION_NO_LAND'))
                return
            
            # 获取领地详细信息
            land_info = self.get_land_info(land_id)
            if not land_info:
                player.send_message(self.language_manager.GetText('SYSTEM_ERROR'))
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
                on_close=self.show_land_main_menu
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
            player.send_message(self.language_manager.GetText('SYSTEM_ERROR'))

    def display_land_particle_boundary(self, player: Player, land_info: dict, y_coord: float = None):
        """显示三维领地粒子边界（立方体12条棱）"""
        try:
            min_x = land_info['min_x']
            max_x = land_info['max_x']
            min_y = land_info.get('min_y', 0)
            max_y = land_info.get('max_y', 255)
            min_z = land_info['min_z']
            max_z = land_info['max_z']

            STEPS = 8  # 每条棱的插值段数（含端点共9个点）

            def emit(x, y, z):
                self.server.dispatch_command(
                    self.server.command_sender,
                    f"particle minecraft:crop_growth_emitter {x} {y} {z}"
                )

            def draw_edge(p1, p2):
                """在两点之间均匀生成粒子"""
                for i in range(STEPS + 1):
                    t = i / STEPS
                    x = p1[0] + (p2[0] - p1[0]) * t
                    y = p1[1] + (p2[1] - p1[1]) * t
                    z = p1[2] + (p2[2] - p1[2]) * t
                    emit(x, y, z)

            # 立方体8个顶点
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

            # 底面4条棱
            draw_edge(corners[0], corners[1])
            draw_edge(corners[1], corners[2])
            draw_edge(corners[2], corners[3])
            draw_edge(corners[3], corners[0])
            # 顶面4条棱
            draw_edge(corners[4], corners[5])
            draw_edge(corners[5], corners[6])
            draw_edge(corners[6], corners[7])
            draw_edge(corners[7], corners[4])
            # 4条竖直棱
            draw_edge(corners[0], corners[4])
            draw_edge(corners[1], corners[5])
            draw_edge(corners[2], corners[6])
            draw_edge(corners[3], corners[7])

        except Exception as e:
            self.logger.error(f"Display land particle boundary error: {str(e)}")
            player.send_message(self.language_manager.GetText('SYSTEM_ERROR'))

    def show_new_land_info(self, player: Player):
        """显示待购买领地的预览信息面板（含购买按钮和/landbuy提示）"""
        info = self.player_new_land_creation_info.get(player.name)
        if not info:
            player.send_message(self.language_manager.GetText('SYSTEM_ERROR'))
            return

        dimension = info['dimension']
        min_x, max_x = info['min_x'], info['max_x']
        min_y, max_y = info['min_y'], info['max_y']
        min_z, max_z = info['min_z'], info['max_z']

        if_allowed, reason, overlap_ids = self.check_land_availability(dimension, min_x, max_x, min_y, max_y, min_z, max_z)
        if not if_allowed:
            msg = self.language_manager.GetText(f'CHECK_NEW_LAND_AVAILABILITY_FAIL_{reason}')
            if overlap_ids:
                land_parts = [f"#{lid} {self.get_land_name(lid) or ''}".strip() for lid in overlap_ids]
                msg = msg + '\n' + self.language_manager.GetText('LAND_OVERLAP_WITH_LANDS').format(', '.join(land_parts))
            player.send_message(msg)
            return

        length = max_x - min_x + 1
        height = max_y - min_y + 1
        width = max_z - min_z + 1

        if length <= self.land_min_size or width <= self.land_min_size:
            player.send_message(self.language_manager.GetText('CREATE_NEW_LAND_SIZE_TOO_SMALL').format(length, width, self.land_min_size))
            return

        volume = length * height * width

        remaining_free_blocks = self.get_player_free_land_blocks(player)
        paid_blocks = max(0, volume - remaining_free_blocks)
        money_cost = paid_blocks * self.land_price
        used_free_blocks = min(volume, remaining_free_blocks)

        player_money = self.get_player_money(player)
        can_afford = player.is_op or player_money >= money_cost

        new_land_form = ActionForm(
            title=self.language_manager.GetText('NEW_LAND_TITLE'),
            content=self.language_manager.GetText('NEW_LAND_INFO_TEXT').format(
                dimension,
                (min_x, min_y, min_z),
                (max_x, max_y, max_z),
                volume,
                self._format_money_display(money_cost),
                self._format_money_display(player_money)
            )
        )

        if can_afford:
            new_land_form.add_button(
                self.language_manager.GetText('BUY_NEW_LAND_TEXT'),
                on_click=lambda p: self.player_buy_new_land(p, dimension, min_x, max_x, min_y, max_y, min_z, max_z, volume, money_cost, used_free_blocks)
            )
        else:
            new_land_form.add_button(self.language_manager.GetText('BUY_NEW_LAND_NO_MONEY_TEXT'))

        new_land_form.add_button(
            self.language_manager.GetText('LAND_RESELECT_BUTTON_TEXT'),
            on_click=self.show_create_new_land_guide
        )
        player.send_form(new_land_form)

    def _execute_land_buy(self, player: Player):
        """供/landbuy命令调用，检查并购买缓存中的领地"""
        info = self.player_new_land_creation_info.get(player.name)
        if not info:
            player.send_message(self.language_manager.GetText('LANDBUY_NO_PENDING_LAND'))
            return

        dimension = info['dimension']
        min_x, max_x = info['min_x'], info['max_x']
        min_y, max_y = info['min_y'], info['max_y']
        min_z, max_z = info['min_z'], info['max_z']

        if_allowed, reason, overlap_ids = self.check_land_availability(dimension, min_x, max_x, min_y, max_y, min_z, max_z)
        if not if_allowed:
            msg = self.language_manager.GetText(f'CHECK_NEW_LAND_AVAILABILITY_FAIL_{reason}')
            if overlap_ids:
                land_parts = [f"#{lid} {self.get_land_name(lid) or ''}".strip() for lid in overlap_ids]
                msg = msg + '\n' + self.language_manager.GetText('LAND_OVERLAP_WITH_LANDS').format(', '.join(land_parts))
            player.send_message(msg)
            return

        length = max_x - min_x + 1
        height = max_y - min_y + 1
        width = max_z - min_z + 1
        volume = length * height * width

        remaining_free_blocks = self.get_player_free_land_blocks(player)
        paid_blocks = max(0, volume - remaining_free_blocks)
        money_cost = paid_blocks * self.land_price
        used_free_blocks = min(volume, remaining_free_blocks)

        self.player_buy_new_land(player, dimension, min_x, max_x, min_y, max_y, min_z, max_z, volume, money_cost, used_free_blocks)

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

    def player_buy_new_land(self, player: Player, dimension: str,
                            min_x: int, max_x: int, min_y: int, max_y: int, min_z: int, max_z: int,
                            volume: int, money_cost: int, used_free_blocks: int = 0):
        if self.judge_if_player_has_enough_money(player, money_cost) or player.is_op:
            paid_money = float(money_cost) if not player.is_op else 0.0
            land_id = self.create_land(
                str(player.xuid),
                self.language_manager.GetText('DEFAULT_LAND_NAME').format(player.name, self.get_player_land_count(str(player.xuid)) + 1),
                dimension, min_x, max_x, min_y, max_y, min_z, max_z,
                player.location.x, player.location.y, player.location.z,
                owner_paid_money=paid_money
            )
            if land_id is not None:
                if not player.is_op:
                    if money_cost > 0:
                        self.decrease_player_money(player, money_cost)
                        player.send_message(self.language_manager.GetText('PAY_SUCCESS_HINT').format(
                            self._format_money_display(money_cost),
                            self._format_money_display(self.get_player_money(player))))

                    if used_free_blocks > 0:
                        current_free_blocks = self.get_player_free_land_blocks(player)
                        new_free_blocks = max(0, current_free_blocks - used_free_blocks)
                        self.set_player_free_land_blocks(player, new_free_blocks)
                        player.send_message(self.language_manager.GetText('USE_FREE_BLOCKS_HINT').format(used_free_blocks))

                self.clear_new_land_creation_info_memory(player)
                self.show_own_land_detail_panel(player, land_id, self.get_land_info(land_id))
            else:
                player.send_message(self.language_manager.GetText('SYSTEM_ERROR'))
        else:
            player.send_message(self.language_manager.GetText('PAY_FAIL_NO_ENOUGH_MONEY').format(
                self._format_money_display(money_cost),
                self._format_money_display(self.get_player_money(player))))

    # ─── Sub-land UI ─────────────────────────────────────────────────────────────

    def show_sub_land_manage_panel(self, player: Player, land_id: int):
        """领地主人管理子领地：查看所有子领地 + 新建"""
        sub_lands = self.get_sub_lands_by_parent(land_id)
        land_info = self.get_land_info(land_id)
        if not land_info:
            player.send_message(self.language_manager.GetText('SYSTEM_ERROR'))
            return
        panel = ActionForm(
            title=self.language_manager.GetText('SUB_LAND_MANAGE_PANEL_TITLE'),
            content=self.language_manager.GetText('SUB_LAND_MANAGE_PANEL_CONTENT').format(len(sub_lands)),
            on_close=lambda p=player, l_id=land_id, l_info=land_info: self.show_own_land_detail_panel(p, l_id, l_info)
        )
        panel.add_button(
            self.language_manager.GetText('SUB_LAND_CREATE_BUTTON_TEXT'),
            on_click=lambda p=player, l_id=land_id: self.show_create_sub_land_form(p, l_id)
        )
        for sl_id, sl_info in sub_lands.items():
            owner_name = self.get_player_name_by_xuid(sl_info['owner_xuid']) or sl_info['owner_xuid']
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
            player.send_message(self.language_manager.GetText('SYSTEM_ERROR'))
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
                    p.send_message(self.language_manager.GetText(f'CHECK_SUB_LAND_FAIL_{reason}'))
                    return

                sl_id = self.create_sub_land(land_id, str(p.xuid), sub_land_name, min_x, max_x, min_y, max_y, min_z, max_z)
                if sl_id is not None:
                    p.send_message(self.language_manager.GetText('SUB_LAND_CREATE_SUCCESS').format(sl_id, sub_land_name))
                    self.display_land_particle_boundary(p, {'min_x': min_x, 'max_x': max_x, 'min_y': min_y, 'max_y': max_y, 'min_z': min_z, 'max_z': max_z})
                    self.show_sub_land_detail_panel(p, sl_id)
                else:
                    p.send_message(self.language_manager.GetText('SYSTEM_ERROR'))
            except Exception as e:
                self.logger.error(f"Create sub land form submit error: {str(e)}")
                p.send_message(self.language_manager.GetText('SYSTEM_ERROR'))

        form = ModalForm(
            title=self.language_manager.GetText('SUB_LAND_CREATE_FORM_TITLE'),
            controls=controls,
            on_submit=on_submit,
            on_close=_back
        )
        player.send_form(form)

    def show_sub_land_detail_panel(self, player: Player, sub_land_id: int):
        """显示子领地详情面板"""
        sl_info = self.get_sub_land_info(sub_land_id)
        if not sl_info:
            player.send_message(self.language_manager.GetText('SYSTEM_ERROR'))
            return

        parent_land_id = sl_info['parent_land_id']
        is_owner = sl_info['owner_xuid'] == str(player.xuid) or player.is_op
        shared_names = [self.get_player_name_by_xuid(uid) or uid for uid in sl_info['shared_users']]
        shared_str = ', '.join(shared_names) if shared_names else self.language_manager.GetText('LAND_DETAIL_NO_SHARED_USER_TEXT')

        content = self.language_manager.GetText('SUB_LAND_DETAIL_CONTENT').format(
            sub_land_id,
            sl_info['sub_land_name'],
            (sl_info['min_x'], sl_info['min_y'], sl_info['min_z']),
            (sl_info['max_x'], sl_info['max_y'], sl_info['max_z']),
            self.get_player_name_by_xuid(sl_info['owner_xuid']) or sl_info['owner_xuid'],
            shared_str
        )

        def _back(p):
            self.show_sub_land_manage_panel(p, parent_land_id)

        panel = ActionForm(
            title=self.language_manager.GetText('SUB_LAND_DETAIL_PANEL_TITLE'),
            content=content,
            on_close=_back
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
            player.send_message(self.language_manager.GetText('SYSTEM_ERROR'))
            return

        def on_submit(p: Player, json_str: str):
            data = json.loads(json_str)
            new_name = (data[0] or '').strip()
            if not new_name:
                p.send_message(self.language_manager.GetText('CREATE_HOME_EMPTY_NAME_ERROR'))
                return
            self.rename_sub_land(sub_land_id, new_name)
            self.show_sub_land_detail_panel(p, sub_land_id)

        form = ModalForm(
            title=self.language_manager.GetText('SUB_LAND_RENAME_PANEL_TITLE'),
            controls=[TextInput(
                label=self.language_manager.GetText('RENAME_OWN_LAND_PANEL_INPUT_LABEL').format(sub_land_id),
                placeholder=sl_info['sub_land_name'],
                default_value=sl_info['sub_land_name']
            )],
            on_submit=on_submit,
            on_close=lambda p=player, sl=sub_land_id: self.show_sub_land_detail_panel(p, sl)
        )
        player.send_form(form)

    def confirm_delete_sub_land(self, player: Player, sub_land_id: int):
        sl_info = self.get_sub_land_info(sub_land_id)
        if not sl_info:
            player.send_message(self.language_manager.GetText('SYSTEM_ERROR'))
            return

        parent_land_id = sl_info['parent_land_id']

        def _back(p):
            self.show_sub_land_manage_panel(p, parent_land_id)

        panel = ActionForm(
            title=self.language_manager.GetText('SUB_LAND_CONFIRM_DELETE_TITLE').format(sub_land_id),
            content=self.language_manager.GetText('SUB_LAND_CONFIRM_DELETE_CONTENT').format(sub_land_id, sl_info['sub_land_name']),
            on_close=lambda p=player, sl=sub_land_id: self.show_sub_land_detail_panel(p, sl)
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
            player.send_message(self.language_manager.GetText('SYSTEM_ERROR'))
            return

        panel = ActionForm(
            title=self.language_manager.GetText('LAND_AUTH_MANAGE_TITLE'),
            content=self.language_manager.GetText('SUB_LAND_AUTH_PANEL_CONTENT').format(sub_land_id, sl_info['sub_land_name']),
            on_close=lambda p=player, sl=sub_land_id: self.show_sub_land_detail_panel(p, sl)
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
            player.send_message(self.language_manager.GetText('SYSTEM_ERROR'))
            return
        online_players = [p for p in self.server.online_players if str(p.xuid) != str(player.xuid) and str(p.xuid) != sl_info['owner_xuid'] and str(p.xuid) not in sl_info['shared_users']]
        if not online_players:
            player.send_message(self.language_manager.GetText('LAND_AUTH_NO_SHARED_USERS'))
            self.show_sub_land_auth_manage_panel(player, sub_land_id)
            return
        panel = ActionForm(
            title=self.language_manager.GetText('LAND_AUTH_ADD_PANEL_TITLE'),
            content=self.language_manager.GetText('LAND_AUTH_SELECT_PLAYER_CONTENT'),
            on_close=lambda p=player, sl=sub_land_id: self.show_sub_land_auth_manage_panel(p, sl)
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
            on_close=lambda p=player, sl=sub_land_id: self.show_sub_land_auth_manage_panel(p, sl)
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
    def show_op_main_panel(self, player: Player):
        op_main_panel = ActionForm(
            title=self.language_manager.GetText('OP_PANEL_TITLE')
        )
        op_main_panel.add_button(self.language_manager.GetText('OP_PANEL_SWITCH_GAME_MODE'),
                                 on_click=self.switch_player_game_mode)
        op_main_panel.add_button(self.language_manager.GetText('CLEAR_DROP_ITEM'),
                                 on_click=self.clear_drop_item)
        op_main_panel.add_button(self.language_manager.GetText('OP_PANEL_MONEY_MANAGE'),
                                 on_click=self.show_money_manage_menu)
        op_main_panel.add_button(self.language_manager.GetText('OP_PANEL_MANAGE_ALL_LANDS'),
                                 on_click=self.show_op_all_lands_panel)
        op_main_panel.add_button(self.language_manager.GetText('OP_PANEL_MANAGE_LAND_AT_POS'),
                                 on_click=self.show_op_land_at_pos)
        op_main_panel.add_button(self.language_manager.GetText('OP_PANEL_REBUILD_CHUNK_MAPPING'),
                                 on_click=self.show_op_rebuild_chunk_mapping_confirm)
        op_main_panel.add_button(self.language_manager.GetText('INVITE_REWARD_CONFIG_BUTTON'),
                                 on_click=self.show_invite_reward_config_panel)
        op_main_panel.add_button(self.language_manager.GetText('OP_PANEL_RELOAD_CONFIG_BUTTON'),
                                 on_click=self.op_reload_config)
        op_main_panel.add_button(self.language_manager.GetText('RECORD_COOR_1'),
                                 on_click=self.record_coordinate_1)
        op_main_panel.add_button(self.language_manager.GetText('RECORD_COOR_2'),
                                 on_click=self.record_coordinate_2)
        op_main_panel.add_button(self.language_manager.GetText('RUN_COMMAND'),
                                 on_click=self.run_command_as_self)
        # 返回
        op_main_panel.add_button(self.language_manager.GetText('RETURN_BUTTON_TEXT'),
                                  on_click=self.show_main_menu)
        player.send_form(op_main_panel)

    def show_op_land_at_pos(self, player: Player):
        """OP 直接获取脚下私人领地并进入管理面板"""
        x = math.floor(player.location.x)
        y = math.floor(player.location.y)
        z = math.floor(player.location.z)
        dimension = player.dimension.name.lower()

        # 查找该位置的私人领地（get_land_at_pos 已优先返回非公共领地）
        land_id = self.get_land_at_pos(dimension, x, z, y)
        if land_id is None or self.is_public_land(land_id):
            result_panel = ActionForm(
                title=self.language_manager.GetText('OP_LAND_AT_POS_TITLE'),
                content=self.language_manager.GetText('OP_LAND_AT_POS_NOT_FOUND').format(x, y, z),
                on_close=self.show_op_main_panel
            )
            result_panel.add_button(self.language_manager.GetText('RETURN_BUTTON_TEXT'),
                                    on_click=self.show_op_main_panel)
            player.send_form(result_panel)
            return

        self.show_op_land_detail_panel(player, land_id, from_page=0)

    def show_op_rebuild_chunk_mapping_confirm(self, player: Player):
        """OP 确认重建领地区块映射"""
        confirm = ActionForm(
            title=self.language_manager.GetText('OP_REBUILD_CHUNK_MAPPING_TITLE'),
            content=self.language_manager.GetText('OP_REBUILD_CHUNK_MAPPING_CONFIRM_CONTENT'),
            on_close=self.show_op_main_panel
        )
        confirm.add_button(
            self.language_manager.GetText('OP_REBUILD_CHUNK_MAPPING_CONFIRM_BUTTON'),
            on_click=self._do_op_rebuild_chunk_mapping
        )
        confirm.add_button(
            self.language_manager.GetText('RETURN_BUTTON_TEXT'),
            on_click=self.show_op_main_panel
        )
        player.send_form(confirm)

    def _do_op_rebuild_chunk_mapping(self, player: Player):
        """执行重建区块映射并反馈结果"""
        success, message = self.rebuild_chunk_land_mapping()
        result_panel = ActionForm(
            title=self.language_manager.GetText('OP_REBUILD_CHUNK_MAPPING_TITLE'),
            content=message,
            on_close=self.show_op_main_panel
        )
        result_panel.add_button(self.language_manager.GetText('RETURN_BUTTON_TEXT'), on_click=self.show_op_main_panel)
        player.send_form(result_panel)
        if success:
            player.send_message(self.language_manager.GetText('OP_REBUILD_CHUNK_MAPPING_DONE'))

    def show_op_force_delete_land_confirm(self, player: Player, land_id: int, from_page: int):
        """OP 强制删除领地确认面板（私人领地全额退款给主人，公共领地不退款）"""
        land_info = self.get_land_info(land_id)
        if not land_info:
            player.send_message(self.language_manager.GetText('SYSTEM_ERROR'))
            self.show_op_land_detail_panel(player, land_id, from_page)
            return

        is_public = self.is_public_land(land_id)
        owner_xuid = land_info['owner_xuid']
        owner_name = self.get_player_name_by_xuid(owner_xuid) or owner_xuid if not is_public else ''
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
            on_close=lambda p=player, l_id=land_id, pg=from_page: self.show_op_land_detail_panel(p, l_id, pg)
        )
        confirm_panel.add_button(
            self.language_manager.GetText('OP_FORCE_DELETE_LAND_CONFIRM_BUTTON'),
            on_click=lambda p=player, l_id=land_id, o_name=owner_name, r=refund, pg=from_page: self._do_op_force_delete_land(p, l_id, o_name, r, pg)
        )
        confirm_panel.add_button(
            self.language_manager.GetText('RETURN_BUTTON_TEXT'),
            on_click=lambda p=player, l_id=land_id, pg=from_page: self.show_op_land_detail_panel(p, l_id, pg)
        )
        player.send_form(confirm_panel)

    def _do_op_force_delete_land(self, player: Player, land_id: int, owner_name: str, refund: float, from_page: int):
        """OP 执行强制删除领地；私人领地全额退款给主人，公共领地不退款"""
        if self.delete_land(land_id):
            if refund > 0 and owner_name:
                self.increase_player_money_by_name(owner_name, refund, notify=True)
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
            self.language_manager.ReloadCurrentLanguage()
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
            try:
                self.land_min_size = int(self.setting_manager.GetSetting('LAND_MIN_SIZE'))
            except (ValueError, TypeError):
                self.land_min_size = 5
            try:
                self.land_min_distance = int(self.setting_manager.GetSetting('MIN_LAND_DISTANCE'))
            except (ValueError, TypeError):
                self.land_min_distance = 0
            self.max_player_home_num = self.setting_manager.GetSetting('MAX_PLAYER_HOME_NUM')
            try:
                self.max_player_home_num = int(self.max_player_home_num)
            except (ValueError, TypeError):
                self.max_player_home_num = 3
            self.enable_random_teleport = self.setting_manager.GetSetting('ENABLE_RANDOM_TELEPORT')
            if self.enable_random_teleport is None:
                self.enable_random_teleport = True
            else:
                try:
                    self.enable_random_teleport = str(self.enable_random_teleport).lower() in ['true', '1', 'yes']
                except (ValueError, AttributeError):
                    self.enable_random_teleport = True
            try:
                self.random_teleport_center_x = int(self.setting_manager.GetSetting('RANDOM_TELEPORT_CENTER_X'))
            except (ValueError, TypeError):
                self.random_teleport_center_x = 0
            try:
                self.random_teleport_center_z = int(self.setting_manager.GetSetting('RANDOM_TELEPORT_CENTER_Z'))
            except (ValueError, TypeError):
                self.random_teleport_center_z = 0
            try:
                self.random_teleport_radius = int(self.setting_manager.GetSetting('RANDOM_TELEPORT_RADIUS'))
            except (ValueError, TypeError):
                self.random_teleport_radius = 5000
            def _int_setting(key: str, default: int) -> int:
                raw = self.setting_manager.GetSetting(key)
                try:
                    return int(raw) if raw is not None else default
                except (ValueError, TypeError):
                    return default
            self.teleport_cost_public_warp = _int_setting('TELEPORT_COST_PUBLIC_WARP', 0)
            self.teleport_cost_home = _int_setting('TELEPORT_COST_HOME', 0)
            self.teleport_cost_land = _int_setting('TELEPORT_COST_LAND', 0)
            self.teleport_cost_death_location = _int_setting('TELEPORT_COST_DEATH_LOCATION', 0)
            self.teleport_cost_random = _int_setting('TELEPORT_COST_RANDOM', 100)
            self.teleport_cost_player = _int_setting('TELEPORT_COST_PLAYER', 50)
            self.hide_op_in_money_ranking = self.setting_manager.GetSetting('HIDE_OP_IN_MONEY_RANKING')
            if self.hide_op_in_money_ranking is None:
                self.hide_op_in_money_ranking = True
            else:
                try:
                    self.hide_op_in_money_ranking = str(self.hide_op_in_money_ranking).lower() in ['true', '1', 'yes']
                except (ValueError, AttributeError):
                    self.hide_op_in_money_ranking = True
            self._init_cleaner_system()
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
            except Exception:
                result_panel = ActionForm(
                    title=self.language_manager.GetText('INVITE_REWARD_CONFIG_TITLE'),
                    content=self.language_manager.GetText('FILL_INVITER_FAIL_SYSTEM_ERROR'),
                    on_close=self.show_op_main_panel
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
                on_close=self.show_op_main_panel
            )
            p.send_form(result_panel)

        config_panel = ModalForm(
            title=self.language_manager.GetText('INVITE_REWARD_CONFIG_TITLE'),
            controls=[item_name_input, item_count_input, money_input, free_blocks_input],
            on_close=self.show_op_main_panel,
            on_submit=try_save_reward_config
        )
        player.send_form(config_panel)

    def switch_player_game_mode(self, player: Player):
        if player.game_mode == GameMode.CREATIVE:
            self.server.dispatch_command(self.server.command_sender, f'gamemode 0 {player.name}')
        else:
            self.server.dispatch_command(self.server.command_sender, f'gamemode 1 {player.name}')

    def clear_drop_item(self, player: Player):
        self.server.scheduler.run_task(self, self.delay_drop_item, delay=150)
        self.server.broadcast_message(self.language_manager.GetText('READY_TO_CLEAR_DROP_ITEM_BROADCAST'))

    def delay_drop_item(self):
        self.execute_cleaner()

    def record_coordinate_1(self, player: Player):
        if not player.name in self.op_coordinate1_dict:
            self.op_coordinate1_dict[player.name] = None
        self.op_coordinate1_dict[player.name] = self.get_player_position_vector(player)

    def record_coordinate_2(self, player: Player):
        if not player.name in self.op_coordinate2_dict:
            self.op_coordinate2_dict[player.name] = None
        self.op_coordinate2_dict[player.name] = self.get_player_position_vector(player)
        self.show_op_main_panel(player)

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
            on_close=self.show_op_main_panel,
            on_submit=try_execute_command
        )
        player.send_form(command_input_form)
    
    # Money Management UI
    def show_money_manage_menu(self, player: Player):
        """显示金钱管理菜单"""
        money_menu = ActionForm(
            title=self.language_manager.GetText('MONEY_MANAGE_MENU_TITLE'),
            content=self.language_manager.GetText('MONEY_MANAGE_MENU_CONTENT'),
            on_close=self.show_op_main_panel
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
                on_close=self.show_money_manage_menu
            )
            player.send_form(no_players_panel)
            return
        
        select_player_menu = ActionForm(
            title=self.language_manager.GetText('MONEY_MANAGE_SELECT_PLAYER_TITLE'),
            content=self.language_manager.GetText('MONEY_MANAGE_SELECT_PLAYER_CONTENT'),
            on_close=self.show_money_manage_menu
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
            
            # 返回 OP 面板
            self.show_op_main_panel(player)
        
        amount_input_form = ModalForm(
            title=self.language_manager.GetText('MONEY_MANAGE_INPUT_AMOUNT_TITLE'),
            controls=[amount_input],
            on_close=lambda p=player: self.show_money_manage_select_player(p, operation_type),
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
                on_close=self.show_op_main_panel
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
            on_close=self.show_op_main_panel
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
            on_click=self.show_op_main_panel
        )
        player.send_form(menu)
    
    def show_op_land_detail_panel(self, player: Player, land_id: int, from_page: int = 0):
        """OP 查看单个领地详情（可传送前往）"""
        land_info = self.get_land_info(land_id)
        if not land_info:
            player.send_message(self.language_manager.GetText('SYSTEM_ERROR'))
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
        
        detail_panel = ActionForm(
            title=self.language_manager.GetText('OP_LAND_DETAIL_TITLE').format(land_id),
            content=content,
            on_close=lambda p=player, pg=from_page: self.show_op_all_lands_panel(p, pg)
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
            player.send_message(self.language_manager.GetText('SYSTEM_ERROR'))
            return
        self.server.scheduler.run_task(
            self,
            lambda: self.delay_teleport_to_land(player, land_id, tp_target_pos),
            delay=45
        )
        player.send_message(self.language_manager.GetText('READY_TELEPORT_TO_LAND').format(land_id))
    
    def show_op_confirm_set_land_public(self, player: Player, land_id: int, from_page: int):
        """OP 确认设为公共领地面板"""
        land_info = self.get_land_info(land_id)
        if not land_info:
            player.send_message(self.language_manager.GetText('SYSTEM_ERROR'))
            self.show_op_all_lands_panel(player, from_page)
            return
        confirm_panel = ActionForm(
            title=self.language_manager.GetText('OP_CONFIRM_SET_PUBLIC_TITLE'),
            content=self.language_manager.GetText('OP_CONFIRM_SET_PUBLIC_CONTENT').format(land_id),
            on_close=lambda p=player, pg=from_page: self.show_op_land_detail_panel(p, land_id, pg)
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
            player.send_message(self.language_manager.GetText('SYSTEM_ERROR'))
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
            on_close=lambda p=player, l_id=land_id, pg=from_page: self.show_op_land_detail_panel(p, l_id, pg),
            on_submit=try_change_name
        )
        player.send_form(rename_panel)

    def show_op_land_auth_manage_panel(self, player: Player, land_id: int, from_page: int):
        """OP 领地授权管理面板（添加/移除授权玩家）"""
        land_info = self.get_land_info(land_id)
        if not land_info:
            player.send_message(self.language_manager.GetText('SYSTEM_ERROR'))
            self.show_op_all_lands_panel(player, from_page)
            return
        auth_panel = ActionForm(
            title=self.language_manager.GetText('OP_LAND_MANAGE_AUTH_BUTTON'),
            content=self.language_manager.GetText('LAND_AUTH_MANAGE_TITLE'),
            on_close=lambda p=player, l_id=land_id, pg=from_page: self.show_op_land_detail_panel(p, l_id, pg)
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
                on_close=lambda p=player, l_id=land_id, pg=from_page: self.show_op_land_auth_manage_panel(p, l_id, pg)
            )
            player.send_form(no_players_panel)
            return
        add_panel = ActionForm(
            title=self.language_manager.GetText('LAND_AUTH_ADD_PANEL_TITLE'),
            content=self.language_manager.GetText('LAND_AUTH_SELECT_PLAYER_CONTENT'),
            on_close=lambda p=player, l_id=land_id, pg=from_page: self.show_op_land_auth_manage_panel(p, l_id, pg)
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
                on_close=lambda p=player, l_id=land_id, pg=from_page: self.show_op_land_auth_manage_panel(p, l_id, pg)
            )
            player.send_form(no_auth_panel)
            return
        remove_panel = ActionForm(
            title=self.language_manager.GetText('LAND_AUTH_REMOVE_PANEL_TITLE'),
            content=self.language_manager.GetText('LAND_AUTH_SELECT_REMOVE_CONTENT'),
            on_close=lambda p=player, l_id=land_id, pg=from_page: self.show_op_land_auth_manage_panel(p, l_id, pg)
        )
        for shared_xuid in land_info['shared_users']:
            user_name = self.get_player_name_by_xuid(shared_xuid)
            if user_name:
                remove_panel.add_button(
                    self.language_manager.GetText('LAND_AUTH_REMOVE_TARGET_BUTTON').format(user_name),
                    on_click=lambda p=player, l_id=land_id, uid=shared_xuid, name=user_name, pg=from_page: self.op_remove_land_auth(p, l_id, uid, name, pg)
                )
        player.send_form(remove_panel)

    def op_add_land_auth(self, player: Player, land_id: int, target_player: Player, from_page: int):
        """OP 执行添加领地授权"""
        try:
            land_info = self.get_land_info(land_id)
            if not land_info:
                player.send_message(self.language_manager.GetText('SYSTEM_ERROR'))
                self.show_op_land_auth_manage_panel(player, land_id, from_page)
                return
            target_xuid = str(target_player.xuid)
            if target_xuid in land_info['shared_users']:
                player.send_message(self.language_manager.GetText('LAND_AUTH_ALREADY_EXISTS').format(target_player.name))
                self.show_op_land_auth_manage_panel(player, land_id, from_page)
                return
            shared_users = list(land_info['shared_users'])
            shared_users.append(target_xuid)
            success = self.database_manager.execute(
                "UPDATE lands SET shared_users = ? WHERE land_id = ?",
                (json.dumps(shared_users), land_id)
            )
            if success:
                player.send_message(self.language_manager.GetText('LAND_AUTH_SUCCESS_ADD').format(land_id, target_player.name))
                target_player.send_message(self.language_manager.GetText('LAND_AUTH_NOTIFICATION').format(
                    player.name, land_id, land_info['land_name']
                ))
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
                player.send_message(self.language_manager.GetText('SYSTEM_ERROR'))
                self.show_op_land_auth_manage_panel(player, land_id, from_page)
                return
            if target_xuid not in land_info['shared_users']:
                player.send_message(self.language_manager.GetText('LAND_AUTH_NOT_EXISTS').format(target_name))
                self.show_op_land_auth_manage_panel(player, land_id, from_page)
                return
            shared_users = list(land_info['shared_users'])
            shared_users.remove(target_xuid)
            success = self.database_manager.execute(
                "UPDATE lands SET shared_users = ? WHERE land_id = ?",
                (json.dumps(shared_users), land_id)
            )
            if success:
                player.send_message(self.language_manager.GetText('LAND_AUTH_SUCCESS_REMOVE').format(target_name, land_id))
                target_player = self.server.get_player(target_name)
                if target_player:
                    target_player.send_message(self.language_manager.GetText('LAND_AUTH_REMOVE_NOTIFICATION').format(
                        player.name, land_id, land_info['land_name']
                    ))
            else:
                player.send_message(self.language_manager.GetText('LAND_AUTH_FAILED_REMOVE'))
        except Exception as e:
            self.logger.error(f"OP remove land auth error: {str(e)}")
            player.send_message(self.language_manager.GetText('LAND_AUTH_FAILED_REMOVE'))
        self.show_op_land_auth_manage_panel(player, land_id, from_page)

    def show_op_public_land_settings_panel(self, player: Player, land_id: int, from_page: int):
        """OP 公共领地设置面板：开放互动/开放爆炸/开放生物互动/开放生物伤害"""
        land_info = self.get_land_info(land_id)
        if not land_info:
            player.send_message(self.language_manager.GetText('SYSTEM_ERROR'))
            self.show_op_all_lands_panel(player, from_page)
            return
        status_lines = []
        status_lines.append('开放方块互动: ' + (self.language_manager.GetText('LAND_PUBLIC_INTERACT_STATUS_ENABLED') if land_info.get('allow_public_interact') else self.language_manager.GetText('LAND_PUBLIC_INTERACT_STATUS_DISABLED')))
        status_lines.append('开放爆炸: ' + (self.language_manager.GetText('LAND_EXPLOSION_STATUS_ENABLED') if land_info.get('allow_explosion') else self.language_manager.GetText('LAND_EXPLOSION_STATUS_DISABLED')))
        status_lines.append('开放生物互动: ' + (self.language_manager.GetText('LAND_ACTOR_INTERACTION_STATUS_ENABLED') if land_info.get('allow_actor_interaction') else self.language_manager.GetText('LAND_ACTOR_INTERACTION_STATUS_DISABLED')))
        status_lines.append('开放生物伤害: ' + (self.language_manager.GetText('LAND_ACTOR_DAMAGE_STATUS_ENABLED') if land_info.get('allow_actor_damage') else self.language_manager.GetText('LAND_ACTOR_DAMAGE_STATUS_DISABLED')))
        anpl_enabled = self.language_manager.GetText('ALLOW_NON_PUBLIC_LAND_STATUS_ENABLED') if land_info.get('allow_non_public_land') else self.language_manager.GetText('ALLOW_NON_PUBLIC_LAND_STATUS_DISABLED')
        status_lines.append(self.language_manager.GetText('ALLOW_NON_PUBLIC_LAND_CURRENT_STATUS').format(anpl_enabled))
        content = '\n'.join(status_lines)
        settings_panel = ActionForm(
            title=self.language_manager.GetText('OP_PUBLIC_LAND_SETTINGS_BUTTON'),
            content=content,
            on_close=lambda p=player, l_id=land_id, pg=from_page: self.show_op_land_detail_panel(p, l_id, pg)
        )
        settings_panel.add_button(
            self.language_manager.GetText('LAND_PUBLIC_INTERACT_SETTING_BUTTON_TEXT'),
            on_click=lambda p=player, l_id=land_id, pg=from_page: self.show_op_public_land_toggle_panel(p, l_id, 'allow_public_interact', pg)
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
            self.language_manager.GetText('LAND_ACTOR_DAMAGE_SETTING_BUTTON_TEXT'),
            on_click=lambda p=player, l_id=land_id, pg=from_page: self.show_op_public_land_toggle_panel(p, l_id, 'allow_actor_damage', pg)
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
    
    def show_op_public_land_toggle_panel(self, player: Player, land_id: int, setting_key: str, from_page: int):
        """OP 公共领地单项设置切换面板"""
        land_info = self.get_land_info(land_id)
        if not land_info:
            player.send_message(self.language_manager.GetText('SYSTEM_ERROR'))
            self.show_op_all_lands_panel(player, from_page)
            return
        current = land_info.get(setting_key, False)
        if setting_key == 'allow_public_interact':
            status_text = self.language_manager.GetText('LAND_PUBLIC_INTERACT_STATUS_ENABLED') if current else self.language_manager.GetText('LAND_PUBLIC_INTERACT_STATUS_DISABLED')
            title = self.language_manager.GetText('LAND_PUBLIC_INTERACT_SETTING_TITLE')
        elif setting_key == 'allow_explosion':
            status_text = self.language_manager.GetText('LAND_EXPLOSION_STATUS_ENABLED') if current else self.language_manager.GetText('LAND_EXPLOSION_STATUS_DISABLED')
            title = self.language_manager.GetText('LAND_EXPLOSION_SETTING_TITLE')
        elif setting_key == 'allow_actor_interaction':
            status_text = self.language_manager.GetText('LAND_ACTOR_INTERACTION_STATUS_ENABLED') if current else self.language_manager.GetText('LAND_ACTOR_INTERACTION_STATUS_DISABLED')
            title = self.language_manager.GetText('LAND_ACTOR_INTERACTION_SETTING_TITLE')
        elif setting_key == 'allow_non_public_land':
            status_text = self.language_manager.GetText('ALLOW_NON_PUBLIC_LAND_STATUS_ENABLED') if current else self.language_manager.GetText('ALLOW_NON_PUBLIC_LAND_STATUS_DISABLED')
            title = self.language_manager.GetText('ALLOW_NON_PUBLIC_LAND_SETTING_BUTTON_TEXT')
        else:  # allow_actor_damage
            status_text = self.language_manager.GetText('LAND_ACTOR_DAMAGE_STATUS_ENABLED') if current else self.language_manager.GetText('LAND_ACTOR_DAMAGE_STATUS_DISABLED')
            title = self.language_manager.GetText('LAND_ACTOR_DAMAGE_SETTING_TITLE')
        toggle_panel = ActionForm(
            title=title,
            content=status_text,
            on_close=lambda p=player, l_id=land_id, pg=from_page: self.show_op_public_land_settings_panel(p, l_id, pg)
        )
        enable_key = {
            'allow_public_interact': ('LAND_PUBLIC_INTERACT_TOGGLE_ENABLE_BUTTON', 'LAND_PUBLIC_INTERACT_TOGGLE_DISABLE_BUTTON'),
            'allow_explosion': ('LAND_EXPLOSION_TOGGLE_ENABLE_BUTTON', 'LAND_EXPLOSION_TOGGLE_DISABLE_BUTTON'),
            'allow_actor_interaction': ('LAND_ACTOR_INTERACTION_TOGGLE_ENABLE_BUTTON', 'LAND_ACTOR_INTERACTION_TOGGLE_DISABLE_BUTTON'),
            'allow_actor_damage': ('LAND_ACTOR_DAMAGE_TOGGLE_ENABLE_BUTTON', 'LAND_ACTOR_DAMAGE_TOGGLE_DISABLE_BUTTON'),
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
        column_map = {
            'allow_public_interact': ('allow_public_interact', 'LAND_PUBLIC_INTERACT_SETTING_UPDATED_ENABLE', 'LAND_PUBLIC_INTERACT_SETTING_UPDATED_DISABLE', 'LAND_PUBLIC_INTERACT_SETTING_FAILED'),
            'allow_explosion': ('allow_explosion', 'LAND_EXPLOSION_SETTING_UPDATED_ENABLE', 'LAND_EXPLOSION_SETTING_UPDATED_DISABLE', 'LAND_EXPLOSION_SETTING_FAILED'),
            'allow_actor_interaction': ('allow_actor_interaction', 'LAND_ACTOR_INTERACTION_SETTING_UPDATED_ENABLE', 'LAND_ACTOR_INTERACTION_SETTING_UPDATED_DISABLE', 'LAND_ACTOR_INTERACTION_SETTING_FAILED'),
            'allow_actor_damage': ('allow_actor_damage', 'LAND_ACTOR_DAMAGE_SETTING_UPDATED_ENABLE', 'LAND_ACTOR_DAMAGE_SETTING_UPDATED_DISABLE', 'LAND_ACTOR_DAMAGE_SETTING_FAILED'),
            'allow_non_public_land': ('allow_non_public_land', 'ALLOW_NON_PUBLIC_LAND_UPDATED_ENABLE', 'ALLOW_NON_PUBLIC_LAND_UPDATED_DISABLE', 'ALLOW_NON_PUBLIC_LAND_FAILED'),
        }
        col, msg_enable, msg_disable, msg_fail = column_map[setting_key]
        try:
            success = self.database_manager.execute(
                f"UPDATE lands SET {col} = ? WHERE land_id = ?",
                (1 if enable else 0, land_id)
            )
            if success:
                msg_key = msg_enable if enable else msg_disable
                player.send_message(self.language_manager.GetText(msg_key).format(land_id))
            else:
                player.send_message(self.language_manager.GetText(msg_fail))
            self.show_op_public_land_settings_panel(player, land_id, from_page)
        except Exception as e:
            self.logger.error(f"OP toggle land setting error: {str(e)}")
            player.send_message(self.language_manager.GetText('SYSTEM_ERROR'))
            self.show_op_public_land_settings_panel(player, land_id, from_page)
    
    # DTWT Plugin related functions
    def show_dtwt_panel(self, player: Player):
        player.perform_command('dtwt')
    
    # Stock Market Plugin related functions
    def show_stock_ui(self, player: Player):
        player.perform_command('stock ui')

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
        """
        获取所有玩家的金钱数据
        :return: 字典，键为玩家名称，值为金钱数量
        """
        try:
            results = self.database_manager.query_all(
                "SELECT xuid, money FROM player_economy"
            )
            money_data = {}
            for entry in results:
                try:
                    xuid_str = entry['xuid']
                    player_name = self.get_player_name_by_xuid(xuid_str)
                    if player_name:
                        money_data[player_name] = entry['money']
                except Exception:
                    continue
            return money_data
        except Exception as e:
            self.logger.error(f"{ColorFormat.RED}[ARC Core]Get money data error: {str(e)}")
            return {}

    def api_get_player_money(self, player_name: str) -> float:
        """
        获取目标玩家的金钱（API封装器）
        :param player_name: 玩家名称
        :return: 玩家金钱数量（支持小数，精确到分）
        """
        return self.get_player_money_by_name(player_name)

    def api_get_richest_player_money_data(self) -> list:
        """
        获取最富有玩家的信息
        :return: [玩家名称, 金钱数量]
        """
        try:
            result = self.database_manager.query_one(
                "SELECT xuid, money FROM player_economy ORDER BY money DESC LIMIT 1"
            )
            if result:
                player_name = self.get_player_name_by_xuid(result['xuid'])
                if player_name:
                    return [player_name, self._round_money(result['money'])]
            return ["", 0.0]
        except Exception as e:
            self.logger.error(f"{ColorFormat.RED}[ARC Core]Get player money top error: {str(e)}")
            return ["", 0.0]

    def api_get_poorest_player_money_data(self) -> list:
        """
        获取最贫穷玩家的信息
        :return: [玩家名称, 金钱数量]
        """
        try:
            result = self.database_manager.query_one(
                "SELECT xuid, money FROM player_economy ORDER BY money ASC LIMIT 1"
            )
            if result:
                player_name = self.get_player_name_by_xuid(result['xuid'])
                if player_name:
                    return [player_name, self._round_money(result['money'])]
            return ["", 0.0]
        except Exception as e:
            self.logger.error(f"{ColorFormat.RED}[ARC Core]Get player money bottom error: {str(e)}")
            return ["", 0.0]

    def api_change_player_money(self, player_name: str, money_to_change: float) -> bool:
        """
        改变目标玩家的金钱（API封装器）
        :param player_name: 玩家名称
        :param money_to_change: 要改变的金钱数量（正数为增加，负数为减少），支持小数精确到分
        :return: 是否操作成功
        """
        if self._round_money(money_to_change) == 0:
            self.logger.error(f'{ColorFormat.RED}[ARC Core]Money change cannot be zero...')
            return False
        
        return self.change_player_money_by_name(player_name, money_to_change, notify=True)
    
    def api_if_position_in_land(self, dimension: str, position: tuple) -> int:
        """
        判断位置是否在玩家领地内，不在的话返回None，存在的话返回领地id
        """
        return self.get_land_at_pos(dimension, math.floor(position[0]), math.floor(position[2]))
    
    def api_get_land_info(self, land_id: int) -> dict:
        """
        获取领地信息
        :return: 领地信息字典 {
            'land_name': 领地名称,
            'dimension': 维度,
            'min_x': 最小X坐标,
            'max_x': 最大X坐标,
            'min_z': 最小Z坐标,
            'max_z': 最大Z坐标,
            'tp_x': 传送点X坐标,
            'tp_y': 传送点Y坐标,
            'tp_z': 传送点Z坐标,
            'shared_users': 共享玩家XUID列表,
            'owner_xuid': 拥有者XUID
        } 不存在则返回空字典
        """
        return self.get_land_info(land_id)

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

    def _send_death_broadcast(self, event: PlayerDeathEvent):
        """发送死亡播报消息"""
        try:
            player_name = event.player.name
            dimension_raw = event.player.location.dimension.name
            dimension = self._translate_dimension_name(dimension_raw)
            x = int(event.player.location.x)
            y = int(event.player.location.y)
            z = int(event.player.location.z)
            
            # 尝试获取死亡原因
            death_cause_raw = self._get_death_cause(event)
            death_cause_translated = self._translate_death_cause(death_cause_raw) if death_cause_raw else ""
            
            # 尝试获取攻击者信息
            attacker_name = self._get_entity_name_from_damage_source(event)
            
            # 根据死亡原因和攻击者信息选择消息格式
            if attacker_name and death_cause_translated in ['生物攻击', '玩家攻击']:
                # 被生物或玩家杀死的情况
                game_message = self.language_manager.GetText('DEATH_BROADCAST_MESSAGE_WITH_CAUSE').format(
                    player_name, dimension, x, y, z, f"{death_cause_translated}({attacker_name})"
                )
                qq_message = self.language_manager.GetText('DEATH_QQ_MESSAGE_WITH_ENTITY').format(
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
            
            # 发送到QQ群
            self._send_to_qq_group(qq_message)
                
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
        """从伤害源获取生物名称"""
        try:
            if hasattr(event, 'damage_source') and event.damage_source:
                damage_source = event.damage_source
                
                # 优先尝试从 damage_source 的 actor 属性获取名称
                if hasattr(damage_source, 'actor') and damage_source.actor:
                    return self._translate_entity_name(damage_source.actor)
                
                # 尝试 damaging_actor 属性
                if hasattr(damage_source, 'damaging_actor') and damage_source.damaging_actor:
                    return self._translate_entity_name(damage_source.damaging_actor)
                
                # 尝试获取攻击者实体对象
                if hasattr(damage_source, 'damaging_entity'):
                    entity = damage_source.damaging_entity
                    if entity:
                        return self._translate_entity_name(entity)
                
                # 尝试其他可能的属性
                if hasattr(damage_source, 'entity'):
                    entity = damage_source.entity
                    if entity:
                        return self._translate_entity_name(entity)
                
                if hasattr(damage_source, 'attacker'):
                    entity = damage_source.attacker
                    if entity:
                        return self._translate_entity_name(entity)
            
            return ""
        except Exception as e:
            self.logger.error(f"[ARC Core] 获取生物名称错误: {str(e)}")
            return ""

    def _translate_entity_name(self, entity) -> str:
        """翻译生物名称"""
        try:
            if not entity:
                return ""
            
            # 尝试获取实体的名称
            if hasattr(entity, 'name') and entity.name:
                return str(entity.name).strip()
            
            # 如果没有名称，返回实体类型
            return str(type(entity).__name__)
            
        except Exception as e:
            self.logger.error(f"[ARC Core] 翻译生物名称错误: {str(e)}")
            return str(entity) if entity else ""

    def _translate_dimension_name(self, dimension_name: str) -> str:
        """翻译维度名称"""
        try:
            if not dimension_name:
                return ""
            
            # 将维度名称转换为大写并添加前缀
            dimension_key = f"DIMENSION_{dimension_name.upper()}"
            
            # 使用 LanguageManager 获取翻译
            translation = self.language_manager.GetText(dimension_key)
            
            # 如果找到了翻译，返回翻译结果
            if translation:
                return translation
            
            # 如果没找到翻译，返回原字符串
            return dimension_name
            
        except Exception as e:
            self.logger.error(f"[ARC Core] 翻译维度名称错误: {str(e)}")
            return dimension_name

    def _send_to_qq_group(self, message: str):
        """
        发送消息到QQ群
        :param message: 要发送的消息
        """
        try:
            # 获取 qqsync_plugin 插件
            qqsync = self.server.plugin_manager.get_plugin('qqsync_plugin')
            if qqsync is None:
                self.logger.warning("[ARC Core] QQSync 插件未找到，无法发送群消息")
                return
            
            # 发送消息到QQ群
            success = qqsync.api_send_message(message)
            if success:
                self.logger.info(f"[ARC Core] 死亡消息已发送到QQ群: {message}")
            else:
                self.logger.warning(f"[ARC Core] QQ群消息发送失败: {message}")
        except Exception as e:
            self.logger.error(f"[ARC Core] QQ群消息发送异常: {str(e)}")
            # 即使QQ群发送失败，也不影响游戏正常运行

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