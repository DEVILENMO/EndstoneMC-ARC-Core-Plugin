import hashlib
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, Set

from endstone import ColorFormat, Player, GameMode
from endstone._internal.endstone_python import ActionForm, TextInput, ModalForm
from endstone.command import Command, CommandSender
from endstone.event import event_handler, PlayerJoinEvent, PlayerQuitEvent, BlockBreakEvent, BlockPlaceEvent, PlayerDeathEvent, PlayerInteractEvent, ActorExplodeEvent 
from endstone.plugin import Plugin

from endstone_arc_core.DatabaseManager import DatabaseManager
from endstone_arc_core.LanguageManager import LanguageManager
from endstone_arc_core.SettingManager import SettingManager

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
        "addmoney":{
            "description": "Add money for player, op only.",
            "usages": ["/addmoney [PlayerName: string] [Amount: int]"]
        },
        "removemoney":{
            "description": "Remove money from player, op only.",
            "usages": ["/removemoney [PlayerName: string] [Amount: int]"]
        },
        "pos1": {
            "description": "Set new land corner 1.",
            "usages": ["/pos1"],
            "permissions": ["arc_core.command.set_land_corner"],
        },
        "pos2": {
            "description": "Set new land corner 2.",
            "usages": ["/pos2"],
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
            self.land_price = 1000
        self.land_sell_refund_coefficient = self.setting_manager.GetSetting('LAND_SELL_REFUND_COEFFICIENT')
        try:
            self.land_sell_refund_coefficient = float(self.land_sell_refund_coefficient)
        except (ValueError, TypeError):
            self.land_sell_refund_coefficient = 0.9
        self.player_new_land_creation_info = {}

        # OP坐标记录
        self.op_coordinate1_dict = {}
        self.op_coordinate2_dict = {}

        # 玩家出入领地
        self.player_in_land_id_dict = {}

        # 死亡回归系统
        self.player_death_locations = {}  # 存储玩家死亡位置 {player_name: {'dimension': str, 'x': float, 'y': float, 'z': float}}

        # 传送系统
        self.max_player_home_num = self.setting_manager.GetSetting('MAX_PLAYER_HOME_NUM')
        try:
            self.max_player_home_num = int(self.max_player_home_num)
        except (ValueError, TypeError):
            self.max_player_home_num = 3
        self.teleport_requests = {}  # 存储传送请求 {player_name: {'type': 'tpa'/'tphere', 'target': target_name, 'expire_time': time}}

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

        # 初始化公告系统和清道夫系统
        self._load_broadcast_messages()
        self._init_cleaner_system()

        # Scheduler tasks
        self.server.scheduler.run_task(self, self.player_position_listener, delay=0, period=25)
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

    def on_disable(self) -> None:
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
        if command.name == "addmoney":
            # 检查参数数量
            if len(args) != 2:
                sender.send_message(self.language_manager.GetText('MONEY_SYSTEM_ADD_MONEY_COMMAND_USAGE_ERROR'))
                return True
            # 获取目标玩家
            if args[0] == '@s':
                player_name = sender.name
            else:
                player_name = args[0]
            target_player = self.server.get_player(player_name)
            if not target_player:
                sender.send_message(self.language_manager.GetText('PLAYER_NOT_FOUND').format(player_name))
                return True
            # 检查金额是否合法
            try:
                amount = int(args[1])
                if amount <= 0:
                    raise ValueError
            except ValueError:
                sender.send_message(self.language_manager.GetText('MONEY_SYSTEM_INVALID_AMOUNT').format(args[1]))
                return True
            # 添加金钱
            if self.increase_player_money(target_player, amount):
                sender.send_message(self.language_manager.GetText('MONEY_SYSTEM_ADD_MONEY_SUCCESS').format(player_name, amount, self.get_player_money(target_player)))
            else:
                sender.send_message(self.language_manager.GetText('MONEY_SYSTEM_ADD_MONEY_FAILED'))
            return True
        if command.name == "removemoney":
            # 检查参数数量
            if len(args) != 2:
                sender.send_message(self.language_manager.GetText('MONEY_SYSTEM_REMOVE_MONEY_COMMAND_USAGE_ERROR'))
                return True
            # 获取目标玩家
            if args[0] == '@s':
                player_name = sender.name
            else:
                player_name = args[0]
            target_player = self.server.get_player(player_name)
            if not target_player:
                sender.send_message(self.language_manager.GetText('PLAYER_NOT_FOUND').format(player_name))
                return True
            # 检查金额是否合法
            try:
                amount = int(args[1])
                if amount <= 0:
                    raise ValueError
            except ValueError:
                sender.send_message(self.language_manager.GetText('MONEY_SYSTEM_INVALID_AMOUNT').format(args[1]))
                return True
            # 扣除金钱
            if self.decrease_player_money(target_player, amount):
                sender.send_message(self.language_manager.GetText('MONEY_SYSTEM_REMOVE_MONEY_SUCCESS').format(player_name, amount, self.get_player_money(target_player)))
            else:
                sender.send_message(self.language_manager.GetText('MONEY_SYSTEM_REMOVE_MONEY_FAILED'))
            return True
        if command.name == 'pos1':
            if not isinstance(sender, Player):
                sender.send_message(f'[ARC Core]This command only works for players.')
                return True
            if not self.if_player_logined(sender):
                self.show_main_menu(sender)
                return True
            self.player_new_land_creation_info[sender.name] = [sender.location.dimension.name, (int(sender.location.x), int(sender.location.z))]
            sender.send_message(self.language_manager.GetText('CREATE_NEW_LAND_POS1_SET').format(
                self.player_new_land_creation_info[sender.name][0],
                self.player_new_land_creation_info[sender.name][1])
            )
            return True
        if command.name == 'pos2':
            if not isinstance(sender, Player):
                sender.send_message(f'[ARC Core]This command only works for players.')
                return True
            if not self.if_player_logined(sender):
                self.show_main_menu(sender)
                return True
            if not sender.name in self.player_new_land_creation_info or len(self.player_new_land_creation_info[sender.name]) != 2:
                sender.send_message(self.language_manager.GetText('CREATE_NEW_LAND_POS2_SET_FAIL_POS1_NOT_SET'))
                return True
            if sender.location.dimension.name != self.player_new_land_creation_info[sender.name][0]:
                sender.send_message(self.language_manager.GetText('CREATE_NEW_LAND_POS2_SET_FAIL_DIMENSION_CHANGED'))
                return True
            self.player_new_land_creation_info[sender.name].append((int(sender.location.x), int(sender.location.z)))
            self.show_new_land_info(sender)
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

    @event_handler
    def on_player_quit(self, event: PlayerQuitEvent):
        self.server.broadcast_message(self.language_manager.GetText('PLAYER_QUIT_MESSAGE').format(event.player.name))
        self.player_authentication_state[event.player.name] = False
        
        # 清理死亡位置记录
        if event.player.name in self.player_death_locations:
            del self.player_death_locations[event.player.name]

    @event_handler
    def on_block_break(self, event: BlockBreakEvent):
        if event.player.is_op:
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

    @event_handler
    def on_player_interact(self, event: PlayerInteractEvent):
        """处理玩家交互事件，保护领地免受非法交互"""
        # OP玩家跳过检查
        if event.player.is_op:
            return
        
        # 只检查有方块的交互事件
        if not event.has_block:
            return
            
        # 获取交互位置
        block_location = event.block.location
        dimension = event.player.location.dimension.name
        pos = (block_location.x, block_location.y, block_location.z)
        
        # 检查是否在领地内且不是领地主人
        if not self.land_interact_check(event.player, dimension, pos):
            event.is_cancelled = True

    @event_handler
    def on_actor_explode(self, event: ActorExplodeEvent):
        """处理爆炸事件，保护领地免受爆炸伤害"""
        try:
            explosion_location = event.location
            dimension = explosion_location.dimension.name
            
            # 检查爆炸位置是否在任何领地内
            land_id = self.get_land_at_pos(dimension, int(explosion_location.x), int(explosion_location.z))
            if land_id is not None:
                land_info = self.get_land_info(land_id)
                if land_info and not land_info.get('allow_explosion', False):
                    # 如果领地不允许爆炸，则取消爆炸事件
                    event.is_cancelled = True
                    return
                    
            # 检查爆炸影响的方块是否在领地内
            filtered_blocks = []
            for block in event.block_list:
                block_land_id = self.get_land_at_pos(dimension, int(block.location.x), int(block.location.z))
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

    def land_operation_check(self, player: Player, dimension: str, pos: tuple):
        land_id = self.get_land_at_pos(dimension, pos[0], pos[2])
        if land_id is not None:
            land_info = self.get_land_info(land_id)
            if not land_info:
                return True
                
            owner_uuid = land_info['owner_uuid']
            shared_users = land_info['shared_users']
            
            # 检查是否是领地主人或授权用户
            if owner_uuid != str(player.unique_id) and str(player.unique_id) not in shared_users:
                player.send_message(self.language_manager.GetText('LAND_PROTECT_HINT').format(self.get_player_name_by_uuid(owner_uuid)))
                return False
        return True

    def land_interact_check(self, player: Player, dimension: str, pos: tuple):
        """检查玩家是否有权限在领地内进行方块互动"""
        land_id = self.get_land_at_pos(dimension, pos[0], pos[2])
        if land_id is not None:
            land_info = self.get_land_info(land_id)
            if not land_info:
                return True
            
            # 如果领地设置为对所有人开放方块互动，直接允许
            if land_info.get('allow_public_interact', False):
                return True
                
            owner_uuid = land_info['owner_uuid']
            shared_users = land_info['shared_users']
            
            # 检查是否是领地主人或授权用户
            if owner_uuid != str(player.unique_id) and str(player.unique_id) not in shared_users:
                player.send_message(self.language_manager.GetText('LAND_PROTECT_HINT').format(self.get_player_name_by_uuid(owner_uuid)))
                return False
        return True
    
    def spawn_protect_check(self, player: Player, dimension: str, pos: tuple):
        if self.if_protect_spawn and len(self.spawn_pos_dict):
            if not self.spawn_protect_check(dimension, pos[0], pos[2]):
                player.send_message(self.language_manager.GetText('SPAWN_PROTECT_HINT').format(self.spawn_protect_range))
                return False
        return True

    # Listeners
    def player_position_listener(self):
        for player in self.server.online_players:
            player_pos = self.get_player_position_vector(player)
            land_id = self.get_land_at_pos(player.location.dimension.name, player_pos[0], player_pos[2])

            if not player.name in self.player_in_land_id_dict:
                self.player_in_land_id_dict[player.name] = None

            if self.is_land_id_changed(self.player_in_land_id_dict[player.name], land_id):
                self.player_in_land_id_dict[player.name] = land_id
                if land_id is not None:
                    new_land_name = self.get_land_name(land_id)
                    land_owner = self.get_player_name_by_uuid(self.get_land_owner(land_id))
                    player.send_title(self.language_manager.GetText('STEP_IN_LAND_TITLE').format(new_land_name),
                                      self.language_manager.GetText('STEP_IN_LAND_SUBTITLE').format(land_owner),
                                      5, 20, 5)

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
        self.init_land_tables()
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
            
            # 可以在这里添加其他字段的升级逻辑
            # if not self._add_column_if_not_exists('player_basic_info', 'other_field', 'TEXT'):
            #     success = False
            
            return success
        except Exception as e:
            # 在__init__期间不能使用self.logger，使用print代替
            print(f"[ARC Core]Upgrade player basic table error: {str(e)}")
            return False

    def init_player_basic_table(self) -> bool:
        """初始化玩家基本信息表"""
        fields = {
            'uuid': 'TEXT PRIMARY KEY',  # 玩家UUID作为主键
            'xuid': 'TEXT NOT NULL',  # 玩家XUID
            'name': 'TEXT NOT NULL',  # 玩家名称
            'password': 'TEXT',  # 玩家密码(加密后的)，允许为NULL
            'is_op': 'INTEGER DEFAULT 0'  # 玩家是否为OP，默认为0(false)
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
            player_data = {
                'uuid': str(player.unique_id),
                'xuid': str(player.xuid),
                'name': player.name,
                'password': None,  # 初始密码为空
                'is_op': 1 if player.is_op else 0  # 根据玩家当前OP状态设置
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
            player_uuid = str(player.unique_id)
            # 检查玩家经济数据是否已存在
            existing_data = self.database_manager.query_one(
                "SELECT uuid FROM player_economy WHERE uuid = ?",
                (player_uuid,)
            )
            if existing_data:
                return True  # 已存在，无需重复创建

            # 获取初始金钱设置
            player_init_money_num = self.setting_manager.GetSetting('PLAYER_INIT_MONEY_NUM')
            try:
                init_money = int(player_init_money_num)
            except (ValueError, TypeError):
                init_money = 0

            # 创建经济数据
            economy_data = {
                'uuid': player_uuid,
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
            player_uuid = str(player.unique_id)
            success = True
            is_new_player = False

            # 检查并初始化玩家基本信息
            basic_info = self.database_manager.query_one(
                "SELECT uuid FROM player_basic_info WHERE uuid = ?",
                (player_uuid,)
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
                "SELECT * FROM player_basic_info WHERE uuid = ?",
                (str(player.unique_id),)
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
                where='uuid = ?',
                params=(str(player.unique_id),)
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
                "SELECT password FROM player_basic_info WHERE uuid = ?",
                (str(player.unique_id),)
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
                "SELECT name FROM player_basic_info WHERE uuid = ?",
                (str(player.unique_id),)
            )

            if not current_info:
                return False

            if current_info['name'] != player.name:
                # 名称发生变化，需要更新
                success = self.database_manager.update(
                    table='player_basic_info',
                    data={'name': player.name},
                    where='uuid = ?',
                    params=(str(player.unique_id),)
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
                "SELECT is_op FROM player_basic_info WHERE uuid = ?",
                (str(player.unique_id),)
            )
            
            if current_info is not None:
                stored_op_status = current_info.get('is_op', 0)
                if stored_op_status != current_op_status:
                    # OP状态发生变化，更新数据库
                    success = self.database_manager.update(
                        table='player_basic_info',
                        data={'is_op': current_op_status},
                        where='uuid = ?',
                        params=(str(player.unique_id),)
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

    def get_offline_player_op_status_by_uuid(self, player_uuid: str) -> Optional[bool]:
        """
        通过UUID获取离线玩家的OP状态
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

    def get_player_name_by_uuid(self, player_uuid: str) -> Optional[str]:
        """
        通过UUID获取玩家名称
        :param player_uuid: 玩家UUID字符串
        :return: 玩家名称，如果未找到则返回None
        """
        try:
            result = self.database_manager.query_one(
                "SELECT name FROM player_basic_info WHERE uuid = ?",
                (player_uuid,)
            )
            return result['name'] if result else None
        except Exception as e:
            self.logger.error(f"{ColorFormat.RED}[ARC Core]Get player name by UUID error: {str(e)}")
            return None
    
    def get_player_uuid_by_name(self, player_name: str) -> Optional[str]:
        """
        通过玩家名称获取UUID
        :param player_name: 玩家名称
        :return: 玩家UUID字符串，如果未找到则返回None
        """
        try:
            result = self.database_manager.query_one(
                "SELECT uuid FROM player_basic_info WHERE name = ?",
                (player_name,)
            )
            return result['uuid'] if result else None
        except Exception as e:
            self.logger.error(f"{ColorFormat.RED}[ARC Core]Get player UUID by name error: {str(e)}")
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
            if self.server.plugin_manager.get_plugin('ushop'):
                arc_menu.add_button(self.language_manager.GetText('SHOP_MENU_NAME'), on_click=self.show_shop_menu)
            if self.server.plugin_manager.get_plugin('arc_dtwt'):
                arc_menu.add_button(self.language_manager.GetText('DTWT_MENU_NAME'), on_click=self.show_dtwt_panel)
            if player.is_op:
                arc_menu.add_button(self.language_manager.GetText('OP_PANEL_NAME'), on_click=self.show_op_main_panel)
            arc_menu.add_button(self.language_manager.GetText('SUICIDE_FUNC_BUTTON'), on_click=self.execute_suicide)
            arc_menu.on_close = None
            player.send_form(arc_menu)
    
    def execute_suicide(self, player: Player):
        player.perform_command('suicide')

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

    # Economy system
    def init_economy_table(self) -> bool:
        """初始化经济系统表格"""
        fields = {
            'uuid': 'TEXT PRIMARY KEY',  # 玩家UUID字符串作为主键
            'money': 'INTEGER NOT NULL DEFAULT 0'  # 玩家金钱数量，默认值0
        }
        return self.database_manager.create_table('player_economy', fields)

    def _set_player_money(self, player: Player, amount: int) -> bool:
        """
        设置玩家金钱
        :param player: 玩家对象
        :param amount: 金钱数量
        :return: 是否设置成功
        """
        try:
            # 限制金钱范围在32位整数范围内
            amount = max(-2147483648, min(2147483647, amount))
            player_uuid = str(player.unique_id)  # 转换UUID为字符串
            return self.database_manager.update(
                table='player_economy',
                data={'money': amount},
                where='uuid = ?',
                params=(player_uuid,)
            )
        except Exception as e:
            self.logger.error(f"{ColorFormat.RED}[ARC Core]Set player money error: {str(e)}")
            return False

    def get_player_money(self, player: Player) -> int:
        try:
            player_uuid = str(player.unique_id)  # 转换UUID为字符串
            result = self.database_manager.query_one(
                "SELECT money FROM player_economy WHERE uuid = ?",
                (player_uuid,)
            )
            if result is None:
                # 玩家不存在，创建新记录
                player_init_money_num = self.setting_manager.GetSetting('PLAYER_INIT_MONEY_NUM')
                try:
                    init_money = int(player_init_money_num)
                except (ValueError, TypeError):
                    init_money = 0
                self.database_manager.insert(
                    'player_economy',
                    {'uuid': player_uuid, 'money': init_money}
                )
                return init_money
            return result['money']
        except Exception as e:
            self.logger.error(f"{ColorFormat.RED}[ARC Core]Get player money error: {str(e)}")
            return 0

    def increase_player_money(self, player: Player, amount: int) -> bool:
        try:
            if amount < 0:
                amount *= -1
            current_money = self.get_player_money(player)
            return self._set_player_money(player, current_money + amount)
        except Exception as e:
            self.logger.error(f"{ColorFormat.RED}[ARC Core]Add player money error: {str(e)}")
            return False

    def decrease_player_money(self, player: Player, amount: int) -> bool:
        try:
            if amount < 0:
                amount *= -1
            current_money = self.get_player_money(player)
            return self._set_player_money(player, current_money - amount)
        except Exception as e:
            self.logger.error(f"{ColorFormat.RED}[ARC Core]Remove player money error: {str(e)}")
            return False

    def get_top_richest_players(self, top_count: int) -> Dict[str, int]:
        try:
            results = self.database_manager.query_all(
                "SELECT * FROM player_economy ORDER BY money DESC LIMIT ?",
                (top_count,)
            )

            rich_list = {}
            for entry in results:
                try:
                    uuid_str = entry['uuid']
                    player_name = self.get_player_name_by_uuid(uuid_str)
                    if player_name:
                        rich_list[player_name] = entry['money']
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
                    SELECT uuid, money,
                    ROW_NUMBER() OVER (ORDER BY money DESC) as rank
                    FROM player_economy
                )
                SELECT rank 
                FROM RankedPlayers 
                WHERE uuid = ?
            """, (str(player.unique_id),))

            return result['rank'] if result else None
        except Exception as e:
            self.logger.error(f"{ColorFormat.RED}[ARC Core]Get player money rank error: {str(e)}")
            return None

    def judge_if_player_has_enough_money(self, player: Player, amount: int) -> bool:
        return self.get_player_money(player) >= abs(amount)

    # Bank
    def show_bank_main_menu(self, player: Player):
        bank_main_menu = ActionForm(
            title=self.language_manager.GetText('BANK_MAIN_MENU_TITLE'),
            content=self.language_manager.GetText('BANK_MAIN_MENU_BALANCE_CONTENT').format(self.get_player_money(player))
        )
        bank_main_menu.add_button(self.language_manager.GetText('BANK_MAIN_MENU_TRANSFER_BUTTON_TEXT'), on_click=self.show_transfer_panel)
        bank_main_menu.add_button(self.language_manager.GetText('BANK_MAIN_MENU_MONEY_RANK_BUTTON_TEXT'),on_click=self.show_money_rank_panel)
        # 返回
        bank_main_menu.add_button(self.language_manager.GetText('RETURN_BUTTON_TEXT'),
                                  on_click=self.show_main_menu)
        player.send_form(bank_main_menu)

    def show_transfer_panel(self, player: Player):
        player_name_input = TextInput(
            label=self.language_manager.GetText('TRANSFER_PANEL_PLAYER_NAME_INPUT_LABEL'),
            placeholder=self.language_manager.GetText('TRANSFER_PANEL_PLAYER_NAME_INPUT_PLACEHOLDER')
        )
        money_amount_input = TextInput(
            label=self.language_manager.GetText('TRANSFER_PANEL_MONEY_AMOUNT_INPUT_LABEL'),
            placeholder=self.language_manager.GetText('TRANSFER_PANEL_MONEY_AMOUNT_INPUT_PLACEHOLDER'),
            default_value='0'
        )

        def try_transfer(player: Player, json_str: str):
            data = json.loads(json_str)
            error_code, receive_player, amount = self._validate_transfer_data(player, data)
            if error_code == 0:
                self.decrease_player_money(player, amount)
                self.increase_player_money(receive_player, amount)
                receive_player.send_message(self.language_manager.GetText('RECEIVE_PLAYER_TRANSFER_MESSAGE').format(
                    player.name,
                    amount,
                    self.get_player_money(receive_player)))
                result_str = self.language_manager.GetText('TRANSFER_COMPLETED_HINT_TEXT').format(
                    receive_player.name,
                    amount,
                    self.get_player_money(player)
                )
            else:
                result_str = self.language_manager.GetText(f'TRANSFER_ERROR_{error_code}_TEXT')
                if error_code == 2:
                    result_str = result_str.format(data[0])
            result_form = ActionForm(
                title=self.language_manager.GetText('TRANSFER_RESULT_PANEL_TITLE'),
                content=result_str,
                on_close=self.show_bank_main_menu
            )
            player.send_form(result_form)
        transfer_panel = ModalForm(
            title=self.language_manager.GetText('TRANSFER_PANEL_TITLE'),
            controls=[player_name_input, money_amount_input],
            on_close=self.show_bank_main_menu,
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
        if self.judge_if_player_has_enough_money(player, amount):
            return 4, receive_player, amount

        return error_code, receive_player, amount

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
                self.language_manager.GetText('MONEY_RANK_INFO_TEXT').format(i + 1, player_name, player_money))
        
        rank_panel = ActionForm(
            title=self.language_manager.GetText('MONEY_RANK_PANEL_TITLE'),
            content='\n'.join(rank_list) + '\n' + self.language_manager.GetText('MONEY_RANK_PLYAER_RANK_INFO_TEXT').format(self.get_player_money(player), self.get_player_money_rank(player)),
            on_close=self.show_bank_main_menu
        )
        player.send_form(rank_panel)
    
    # Shop menu
    def show_shop_menu(self, player: Player):
        player.perform_command('us')

    # Teleport menu
    def show_teleport_menu(self, player: Player):
        teleport_main_menu = ActionForm(
            title=self.language_manager.GetText('TELEPORT_MAIN_MENU_TITLE'),
            content=self.language_manager.GetText('TELEPORT_MAIN_MENU_CONTENT')
        )
        teleport_main_menu.add_button(self.language_manager.GetText('TELEPORT_MAIN_MENU_PUBLIC_WARP_BUTTON'),
                                      on_click=self.show_public_warp_menu)
        teleport_main_menu.add_button(self.language_manager.GetText('TELEPORT_MAIN_MENU_HOME_BUTTON'),
                                      on_click=self.show_home_menu)
        
        # 如果玩家有死亡位置记录，显示返回死亡地点的按钮
        if player.name in self.player_death_locations:
            death_location = self.player_death_locations[player.name]
            teleport_main_menu.add_button(
                self.language_manager.GetText('TELEPORT_MAIN_MENU_DEATH_LOCATION_BUTTON').format(death_location['dimension']),
                on_click=self.teleport_to_death_location
            )
        
        teleport_main_menu.add_button(self.language_manager.GetText('TELEPORT_MAIN_MENU_PLAYER_REQUEST_BUTTON'),
                                      on_click=self.show_player_teleport_request_menu)
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
                'created_by': 'TEXT NOT NULL',  # 创建者UUID
                'created_time': 'INTEGER NOT NULL'  # 创建时间戳
            }
            
            # 玩家私人传送点表
            home_fields = {
                'home_id': 'INTEGER PRIMARY KEY AUTOINCREMENT',
                'owner_uuid': 'TEXT NOT NULL',
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
    def create_public_warp(self, warp_name: str, dimension: str, x: float, y: float, z: float, creator_uuid: str) -> bool:
        """创建公共传送点"""
        try:
            import time
            warp_data = {
                'warp_name': warp_name,
                'dimension': dimension,
                'x': x,
                'y': y,
                'z': z,
                'created_by': creator_uuid,
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
    def create_player_home(self, owner_uuid: str, home_name: str, dimension: str, x: float, y: float, z: float) -> bool:
        """创建玩家传送点"""
        try:
            import time
            home_data = {
                'owner_uuid': owner_uuid,
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

    def delete_player_home(self, owner_uuid: str, home_name: str) -> bool:
        """删除玩家传送点"""
        try:
            return self.database_manager.delete('player_homes', 'owner_uuid = ? AND home_name = ?', (owner_uuid, home_name))
        except Exception as e:
            self.logger.error(f"Delete player home error: {str(e)}")
            return False

    def get_player_home(self, owner_uuid: str, home_name: str) -> Optional[Dict[str, Any]]:
        """获取玩家传送点信息"""
        try:
            return self.database_manager.query_one(
                "SELECT * FROM player_homes WHERE owner_uuid = ? AND home_name = ?",
                (owner_uuid, home_name)
            )
        except Exception as e:
            self.logger.error(f"Get player home error: {str(e)}")
            return None

    def get_player_homes(self, owner_uuid: str) -> Dict[str, Dict[str, Any]]:
        """获取玩家所有传送点"""
        try:
            results = self.database_manager.query_all(
                "SELECT * FROM player_homes WHERE owner_uuid = ? ORDER BY home_name",
                (owner_uuid,)
            )
            return {row['home_name']: row for row in results}
        except Exception as e:
            self.logger.error(f"Get player homes error: {str(e)}")
            return {}

    def get_player_home_count(self, owner_uuid: str) -> int:
        """获取玩家传送点数量"""
        try:
            result = self.database_manager.query_one(
                "SELECT COUNT(*) as count FROM player_homes WHERE owner_uuid = ?",
                (owner_uuid,)
            )
            return result['count'] if result else 0
        except Exception as e:
            self.logger.error(f"Get player home count error: {str(e)}")
            return 0

    def player_home_exists(self, owner_uuid: str, home_name: str) -> bool:
        """检查玩家传送点是否存在"""
        return self.get_player_home(owner_uuid, home_name) is not None

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
            creator_name = self.get_player_name_by_uuid(warp_info['created_by']) or 'Unknown'
            warp_menu.add_button(
                self.language_manager.GetText('PUBLIC_WARP_BUTTON_TEXT').format(warp_name, warp_info['dimension'], creator_name),
                on_click=lambda p=player, w_name=warp_name, w_info=warp_info: self.teleport_to_public_warp(p, w_name, w_info)
            )
        
        player.send_form(warp_menu)

    def show_home_menu(self, player: Player):
        """显示玩家传送点菜单"""
        player_homes = self.get_player_homes(str(player.unique_id))
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
        
        detail_menu.add_button(
            self.language_manager.GetText('HOME_TELEPORT_BUTTON'),
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
            if self.player_home_exists(str(player.unique_id), home_name):
                player.send_message(self.language_manager.GetText('CREATE_HOME_NAME_EXISTS_ERROR').format(home_name))
                self.show_create_home_panel(player)
                return
            
            # 创建传送点
            success = self.create_player_home(
                str(player.unique_id),
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
        success = self.delete_player_home(str(player.unique_id), home_name)
        if success:
            player.send_message(self.language_manager.GetText('DELETE_HOME_SUCCESS').format(home_name))
        else:
            player.send_message(self.language_manager.GetText('DELETE_HOME_FAILED'))
        self.show_home_menu(player)

    # Teleport Functions
    def teleport_to_public_warp(self, player: Player, warp_name: str, warp_info: Dict[str, Any]):
        """传送到公共传送点"""
        self.start_teleport_to_position_countdown(player, warp_name, (warp_info['x'], warp_info['y'], warp_info['z']), 'PUBLIC_WARP', warp_info['dimension'])

    def teleport_to_home(self, player: Player, home_name: str, home_info: Dict[str, Any]):
        """传送到玩家传送点"""
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
        return f'execute in {formatted_dimension} run tp {formatted_name} {' '.join([str(int(_)) for _ in position])}'

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
            player.send_message(self.language_manager.GetText('TPA_REQUEST_ACCEPTED').format(sender.name))
            sender.send_message(self.language_manager.GetText('TPA_REQUEST_ACCEPTED_BY_TARGET').format(player.name))
        else:
            # TPHERE: 接受者传送到发送者处
            self.start_teleport_to_player_countdown(player, sender)
            player.send_message(self.language_manager.GetText('TPHERE_REQUEST_ACCEPTED').format(sender.name))
            sender.send_message(self.language_manager.GetText('TPHERE_REQUEST_ACCEPTED_BY_TARGET').format(player.name))
        
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
                str(player.unique_id)
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
            creator_name = self.get_player_name_by_uuid(warp_info['created_by']) or 'Unknown'
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
                'owner_uuid': 'TEXT NOT NULL',  # 领地主人UUID
                'land_name': 'TEXT NOT NULL',  # 领地名称
                'dimension': 'TEXT NOT NULL',  # 领地所在维度
                'min_x': 'INTEGER NOT NULL',  # 最小X坐标
                'max_x': 'INTEGER NOT NULL',  # 最大X坐标
                'min_z': 'INTEGER NOT NULL',  # 最小Z坐标
                'max_z': 'INTEGER NOT NULL',  # 最大Z坐标
                'tp_x': 'REAL NOT NULL',  # 传送点X坐标
                'tp_y': 'REAL NOT NULL',  # 传送点Y坐标
                'tp_z': 'REAL NOT NULL',  # 传送点Z坐标
                'shared_users': 'TEXT',  # 共有人UUID列表(JSON字符串)
                'allow_explosion': 'INTEGER DEFAULT 0',  # 是否允许爆炸 (0=不允许, 1=允许)
                'allow_public_interact': 'INTEGER DEFAULT 0'  # 是否对所有人开放方块互动 (0=不开放, 1=开放)
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
        """升级领地数据表结构，为老用户添加新字段"""
        try:
            # 尝试添加allow_explosion字段，如果字段已存在会失败但不影响功能
            try:
                self.database_manager.execute("ALTER TABLE lands ADD COLUMN allow_explosion INTEGER DEFAULT 0")
                print("[ARC Core]Upgraded land table: added allow_explosion column")
            except Exception as alter_error:
                # 字段可能已经存在，这是正常的
                if "duplicate column name" in str(alter_error).lower() or "already exists" in str(alter_error).lower():
                    print("[ARC Core]Land table already has allow_explosion column")
                else:
                    print(f"[ARC Core]Could not add allow_explosion column: {str(alter_error)}")
            
            # 尝试添加allow_public_interact字段，如果字段已存在会失败但不影响功能
            try:
                self.database_manager.execute("ALTER TABLE lands ADD COLUMN allow_public_interact INTEGER DEFAULT 0")
                print("[ARC Core]Upgraded land table: added allow_public_interact column")
            except Exception as alter_error:
                # 字段可能已经存在，这是正常的
                if "duplicate column name" in str(alter_error).lower() or "already exists" in str(alter_error).lower():
                    print("[ARC Core]Land table already has allow_public_interact column")
                else:
                    print(f"[ARC Core]Could not add allow_public_interact column: {str(alter_error)}")
            
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

    def create_land(self, owner_uuid: str, land_name: str, dimension: str,
                    min_x: int, max_x: int, min_z: int, max_z: int,
                    tp_x: float, tp_y: float, tp_z: float) -> Optional[int]:
        """创建新领地"""
        try:
            # 确保维度表存在
            if not self._ensure_dimension_table(dimension):
                return None

            # 插入领地基本信息
            self.database_manager.execute(
                "INSERT INTO lands (owner_uuid, land_name, dimension, min_x, max_x, min_z, max_z, tp_x, tp_y, tp_z, shared_users, allow_explosion, allow_public_interact) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (owner_uuid, land_name, dimension, min_x, max_x, min_z, max_z, tp_x, tp_y, tp_z, '[]', 0, 0)
            )
            result = self.database_manager.query_one("SELECT last_insert_rowid() as land_id")
            land_id = result['land_id']

            # 获取对应维度的区块表
            chunk_table = self._get_dimension_table(dimension)

            # 更新区块映射
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
                        {
                            'chunk_key': chunk_key,
                            'land_ids': json.dumps([land_id])
                        }
                    )

            return land_id
        except Exception as e:
            self.logger.error(f"Create land error: {str(e)}")
            return None

    def get_land_at_pos(self, dimension: str, x: int, z: int) -> Optional[int]:
        """获取指定位置的领地ID"""
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
            for land_id in land_ids:
                land_info = self.database_manager.query_one(
                    "SELECT * FROM lands WHERE land_id = ?",
                    (land_id,)
                )
                if land_info and (
                        land_info['min_x'] <= x <= land_info['max_x'] and
                        land_info['min_z'] <= z <= land_info['max_z']
                ):
                    return land_id

            return None
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

    def check_land_availability(self, dimension: str, min_x: int, max_x: int, min_z: int, max_z: int) -> tuple[bool, str]:
        try:
            # 确保坐标顺序正确
            min_x, max_x = min(min_x, max_x), max(min_x, max_x)
            min_z, max_z = min(min_z, max_z), max(min_z, max_z)

            # 扩展检查范围以包含最小距离
            check_min_x = min_x - self.land_min_distance
            check_max_x = max_x + self.land_min_distance
            check_min_z = min_z - self.land_min_distance
            check_max_z = max_z + self.land_min_distance

            # 获取可能受影响的所有区块
            affected_chunks = self._get_affected_chunks(check_min_x, check_max_x, check_min_z, check_max_z)

            # 确保维度表存在
            if not self._ensure_dimension_table(dimension):
                return False, 'SYSTEM_ERROR'

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

            # 检查每个相关领地
            for land_id in nearby_land_ids:
                land_info = self.database_manager.query_one(
                    "SELECT * FROM lands WHERE land_id = ? AND dimension = ?",
                    (land_id, dimension)
                )

                if not land_info:
                    continue

                # 使用扩展后的范围检查是否与现有领地重叠
                if (check_min_x <= land_info['max_x'] and check_max_x >= land_info['min_x'] and
                        check_min_z <= land_info['max_z'] and check_max_z >= land_info['min_z']):
                    return False, 'LAND_MIN_DISTANCE_NOT_SATISFIED'

            return True, None

        except Exception as e:
            self.logger.error(f"[ARC Core]Check land availability error: {str(e)}")
            return False, 'SYSTEM_ERROR'

    def get_player_land_count(self, uuid: str) -> int:
        """
        获取玩家拥有的领地数量
        :param uuid: 玩家UUID
        :return: 领地数量
        """
        try:
            result = self.database_manager.query_one(
                "SELECT COUNT(*) as count FROM lands WHERE owner_uuid = ?",
                (uuid,)
            )
            return result['count'] if result else 0
        except Exception as e:
            self.logger.error(f"Get player land count error: {str(e)}")
            return 0

    def get_player_lands(self, uuid: str) -> dict[int, dict]:
        """
        获取玩家拥有的所有领地信息
        :param uuid: 玩家UUID
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
            'shared_users': 共享玩家UUID列表
        }}
        """
        try:
            results = self.database_manager.query_all(
                "SELECT * FROM lands WHERE owner_uuid = ?",
                (uuid,)
            )

            lands_info = {}
            for land in results:
                lands_info[land['land_id']] = {
                    'land_name': land['land_name'],
                    'dimension': land['dimension'],
                    'min_x': land['min_x'],
                    'max_x': land['max_x'],
                    'min_z': land['min_z'],
                    'max_z': land['max_z'],
                    'tp_x': land['tp_x'],
                    'tp_y': land['tp_y'],
                    'tp_z': land['tp_z'],
                    'shared_users': json.loads(land['shared_users']),
                    'allow_explosion': bool(land.get('allow_explosion', 0)),
                    'allow_public_interact': bool(land.get('allow_public_interact', 0))
                }

            return lands_info

        except Exception as e:
            self.logger.error(f"Get player lands error: {str(e)}")
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
            'shared_users': 共享玩家UUID列表,
            'owner_uuid': 拥有者UUID
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
                    'min_z': result['min_z'],
                    'max_z': result['max_z'],
                    'tp_x': result['tp_x'],
                    'tp_y': result['tp_y'],
                    'tp_z': result['tp_z'],
                    'shared_users': json.loads(result['shared_users']),
                    'owner_uuid': result['owner_uuid'],
                    'allow_explosion': bool(result.get('allow_explosion', 0)),
                    'allow_public_interact': bool(result.get('allow_public_interact', 0))
                }
            return {}

        except Exception as e:
            self.logger.error(f"Get land info error: {str(e)}")
            return {}

    def get_land_owner(self, land_id: int) -> str:
        """
        获取领地拥有者的UUID
        :param land_id: 领地ID
        :return: 拥有者UUID，不存在则返回空字符串
        """
        try:
            result = self.database_manager.query_one(
                "SELECT owner_uuid FROM lands WHERE land_id = ?",
                (land_id,)
            )
            return result['owner_uuid'] if result else ""

        except Exception as e:
            self.logger.error(f"Get land owner error: {str(e)}")
            return ""

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
                self.get_player_land_count(str(player.unique_id)))
        )
        land_main_menu.add_button(self.language_manager.GetText('LAND_MAIN_MENU_MANAGE_LAND_TEXT'),
                                  on_click=self.show_own_land_menu)
        land_main_menu.add_button(self.language_manager.GetText('LAND_MAIN_MENU_CREATE_NEW_LAND_TEXT'),
                                  on_click=self.show_create_new_land_guide)
        # 返回
        land_main_menu.add_button(self.language_manager.GetText('RETURN_BUTTON_TEXT'),
                                  on_click=self.show_main_menu)
        player.send_form(land_main_menu)

    def show_own_land_menu(self, player: Player):
        player_land_num = self.get_player_land_count(str(player.unique_id))
        if player_land_num == 0:
            own_land_panel = ActionForm(
                title=self.language_manager.GetText('OWN_LAND_PANEL_TITLE'),
                content=self.language_manager.GetText('OWN_LAND_PANEL_NO_LAND_EXIST_CONTENT').format(
                    self.get_player_land_count(str(player.unique_id))),
                on_close=self.show_land_main_menu
            )
            player.send_form(own_land_panel)
            return
        else:
            own_land_panel = ActionForm(
                title=self.language_manager.GetText('OWN_LAND_PANEL_TITLE'),
                on_close=self.show_land_main_menu
            )
            player_lands = self.get_player_lands(str(player.unique_id))
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
            shared_user_names = [self.get_player_name_by_uuid(uu_id) for uu_id in land_info['shared_users']]
            shared_user_name_str = '\n'.join(shared_user_names)
        else:
            shared_user_name_str = self.language_manager.GetText('LAND_DETAIL_NO_SHARED_USER_TEXT')
        land_detail_panel = ActionForm(
            title=self.language_manager.GetText('LAND_DETAIL_PANEL_TITLE'),
            content=self.language_manager.GetText('LAND_DETAIL_PANEL_CONTENT').replace('\\n', '\n').format(
                land_id,
                land_info['land_name'],
                land_info['dimension'],
                (int(land_info['min_x']), int(land_info['min_z'])),
                (int(land_info['max_x']), int(land_info['max_z'])),
                (int(land_info['tp_x']), int(land_info['tp_y']), int(land_info['tp_z'])),
                shared_user_name_str
            ),
            on_close=self.show_own_land_menu
        )
        land_detail_panel.add_button(self.language_manager.GetText('LAND_DETAIL_PANEL_TELEPORT_BUTTON_TEXT'),
                                     on_click=lambda p=player, l_id=land_id: self.teleport_to_land(p, l_id))
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
        land_detail_panel.add_button(self.language_manager.GetText('LAND_PUBLIC_INTERACT_SETTING_BUTTON_TEXT'),
                                     on_click=lambda p=player, l_id=land_id: self.show_land_public_interact_setting_panel(p, l_id)
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
        on_land_id = self.get_land_at_pos(player.location.dimension.name, int(player.location.x), int(player.location.z))
        if on_land_id is None or on_land_id != land_id:
            result = self.language_manager.GetText('SET_LAND_TP_POS_FAIL_OUT_LAND')
        else:
            new_pos = (int(player.location.x), int(player.location.y), int(player.location.z))
            self.set_land_teleport_point(land_id, new_pos[0], new_pos[1], new_pos[2])
            result = self.language_manager.GetText('SET_LAND_TP_POS_SUCCESS').format(land_id, new_pos)
        result_panel = ActionForm(
            title=self.language_manager.GetText('SET_LAND_TP_POS_RESULT_TITLE'),
            content=result,
            on_close=lambda p=player, l_id=land_id, l_info=self.get_land_info(land_id): self.show_own_land_detail_panel(p, l_id, l_info)
        )
        player.send_form(result_panel)

    def teleport_to_land(self, player: Player, land_id: int):
        tp_target_pos = self.get_land_teleport_point(land_id)
        self.server.scheduler.run_task(self, lambda p=player, l_id=land_id, pos=tp_target_pos: self.delay_teleport_to_land(p, l_id, pos), delay=45)
        player.send_message(self.language_manager.GetText('READY_TELEPORT_TO_LAND').format(land_id))

    def delay_teleport_to_land(self, player: Player, land_id: int, position: tuple):
        player.send_message(self.language_manager.GetText('TELEPORT_TO_LAND_START_HINT').format(land_id))
        land_dimension = self.get_land_dimension(land_id)
        self.server.dispatch_command(self.server.command_sender, self.generate_tp_command_to_position(player.name, position, land_dimension))

    def confirm_delete_land(self, player: Player, land_id: int):
        deleta_land_info = self.get_land_info(land_id)
        land_area = (deleta_land_info['max_x'] - deleta_land_info['min_x']) * (deleta_land_info['max_z'] - deleta_land_info['min_z'])
        return_money = int(land_area * self.land_price * self.land_sell_refund_coefficient)
        confirm_panel = ActionForm(
            title=self.language_manager.GetText('CONFIRM_DELETE_LAND_TITLE').format(land_id),
            content=self.language_manager.GetText('CONFIRM_DELETE_LAND_CONTENT').replace('\\n', '\n').format(land_id, deleta_land_info['land_name'], self.land_sell_refund_coefficient, return_money),
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
            player.send_message(self.language_manager.GetText('DELETE_LAND_SUCCESS').format(land_id, return_money, self.get_player_money(player)))
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
            content=self.language_manager.GetText('CONFIRM_TRANSFER_LAND_CONTENT').replace('\\n', '\n').format(
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

    def transfer_land(self, land_id: int, new_owner_uuid: str) -> bool:
        """
        移交领地给新的拥有者
        :param land_id: 领地ID
        :param new_owner_uuid: 新拥有者的UUID
        :return: 是否成功
        """
        try:
            # 检查领地是否存在
            if not self.get_land_info(land_id):
                return False

            # 更新领地拥有者
            self.database_manager.execute(
                "UPDATE lands SET owner_uuid = ? WHERE land_id = ?",
                (new_owner_uuid, land_id)
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
        success = self.transfer_land(land_id, str(target_player.unique_id))
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
            user_name = self.get_player_name_by_uuid(shared_uuid)
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

            target_uuid = str(target_player.unique_id)
            
            # 检查是否已经授权
            if target_uuid in land_info['shared_users']:
                player.send_message(self.language_manager.GetText('LAND_AUTH_ALREADY_EXISTS').format(target_player.name))
                self.show_land_auth_manage_panel(player, land_id)
                return

            # 添加授权
            shared_users = land_info['shared_users']
            shared_users.append(target_uuid)
            
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

    def show_create_new_land_guide(self, player: Player):
        player.send_message(self.language_manager.GetText('CREATE_NEW_LAND_GUIDE'))

    def show_new_land_info(self, player: Player):
        if_allowed, reason = self.check_land_availability(self.player_new_land_creation_info[player.name][0],
                                         int(self.player_new_land_creation_info[player.name][1][0]),
                                         int(self.player_new_land_creation_info[player.name][2][0]),
                                         int(self.player_new_land_creation_info[player.name][1][1]),
                                         int(self.player_new_land_creation_info[player.name][2][1]))
        if not if_allowed:
            player.send_message(self.language_manager.GetText(f'CHECK_NEW_LAND_AVAILABILITY_FAIL_{reason}'))
            return
        else:
            min_x = min(int(self.player_new_land_creation_info[player.name][1][0]),
                        int(self.player_new_land_creation_info[player.name][2][0]))
            max_x = max(int(self.player_new_land_creation_info[player.name][1][0]),
                        int(self.player_new_land_creation_info[player.name][2][0]))
            min_z = min(int(self.player_new_land_creation_info[player.name][1][1]),
                        int(self.player_new_land_creation_info[player.name][2][1]))
            max_z = max(int(self.player_new_land_creation_info[player.name][1][1]),
                        int(self.player_new_land_creation_info[player.name][2][1]))
            area = (max_x - min_x + 1) * (max_z - min_z + 1)
            money_cost = area * self.land_price
            new_land_form = ActionForm(
                title=self.language_manager.GetText('NEW_LAND_TITLE'),
                content=self.language_manager.GetText('NEW_LAND_INFO_TEXT').replace('\\n', '\n').format(
                    self.player_new_land_creation_info[player.name][0],
                    (min_x, min_z),
                    (max_x, max_z),
                    area,
                    money_cost,
                ))
            new_land_form.add_button(self.language_manager.GetText('BUY_NEW_LAND_TEXT'),
                on_click=lambda player: self.player_buy_new_land(
                player,
                self.player_new_land_creation_info[player.name][0],  # dimension
                (min_x, min_z),  # start_pos
                (max_x, max_z),  # end_pos
                area,
                money_cost
            ))
            player.send_form(new_land_form)
            return

    def clear_new_land_creation_info_memory(self, player: Player):
        self.player_new_land_creation_info.pop(player.name)

    def player_buy_new_land(self, player: Player, dimension: str, start_pos: tuple, end_pos: tuple, area: int, money_cost: int):
        if self.judge_if_player_has_enough_money(player, money_cost) or player.is_op:
            land_id = self.create_land(str(player.unique_id), self.language_manager.GetText('DEFAULT_LAND_NAME').format(player.name, self.get_player_land_count(str(player.unique_id)) + 1), dimension, start_pos[0], end_pos[0], start_pos[1], end_pos[1], player.location.x, player.location.y, player.location.z)
            if land_id is not None:
                if not player.is_op:
                    self.decrease_player_money(player, money_cost)
                    player.send_message(self.language_manager.GetText('PAY_SUCCESS_HINT').format(money_cost, self.get_player_money(player)))
                self.clear_new_land_creation_info_memory(player)
                self.show_own_land_detail_panel(player, land_id, self.get_land_info(land_id))
            else:
                player.send_message(self.language_manager.GetText('SYSTEM_ERROR'))
        else:
            player.send_message(self.language_manager.GetText('PAY_FAIL_NO_ENOUGH_MONEY').format(money_cost, self.get_player_money(player)))

    # OP Panel
    def show_op_main_panel(self, player: Player):
        op_main_panel = ActionForm(
            title=self.language_manager.GetText('OP_PANEL_TITLE')
        )
        op_main_panel.add_button(self.language_manager.GetText('OP_PANEL_SWITCH_GAME_MODE'),
                                 on_click=self.switch_player_game_mode)
        op_main_panel.add_button(self.language_manager.GetText('CLEAR_DROP_ITEM'),
                                 on_click=self.clear_drop_item)
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

    def switch_player_game_mode(self, player: Player):
        if player.game_mode == GameMode.CREATIVE:
            self.server.dispatch_command(self.server.command_sender, f'gamemode 0 {player.name}')
        else:
            self.server.dispatch_command(self.server.command_sender, f'gamemode 1 {player.name}')

    def clear_drop_item(self, player: Player):
        self.server.scheduler.run_task(self, self.delay_drop_item, delay=150)
        self.server.broadcast_message(self.language_manager.GetText('READY_TO_CLEAR_DROP_ITEM_BROADCAST'))

    def delay_drop_item(self):
        self.server.broadcast_message(self.language_manager.GetText('CLEAR_DROP_ITEM_BROADCAST'))
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
            placeholder=self.language_manager.GetText('RUN_COMMAND_PANEL_COMMAND_INPUT_PLACEHOLDER').format(player.name)
        )
        def try_execute_command(player: Player, json_str: str):
            data = json.loads(json_str)
            if not len(data):
                return
            command_str = data[0]
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
    
    # DTWT Plugin related functions
    def show_dtwt_panel(self, player: Player):
        player.perform_command('dtwt')

    # Tool
    @staticmethod
    def get_player_position_vector(player: Player):
        return (int(player.location.x), int(player.location.y), int(player.location.z))

    # Economy API methods for other plugins
    def api_get_all_money_data(self) -> dict:
        """
        获取所有玩家的金钱数据
        :return: 字典，键为玩家名称，值为金钱数量
        """
        try:
            results = self.database_manager.query_all(
                "SELECT uuid, money FROM player_economy"
            )
            money_data = {}
            for entry in results:
                try:
                    uuid_str = entry['uuid']
                    player_name = self.get_player_name_by_uuid(uuid_str)
                    if player_name:
                        money_data[player_name] = entry['money']
                except Exception:
                    continue
            return money_data
        except Exception as e:
            self.logger.error(f"{ColorFormat.RED}[ARC Core]Get money data error: {str(e)}")
            return {}

    def api_get_player_money(self, player_name: str) -> int:
        """
        获取目标玩家的金钱
        :param player_name: 玩家名称
        :return: 玩家金钱数量
        """
        try:
            player_uuid = self.get_player_uuid_by_name(player_name)
            if not player_uuid:
                return 0
            
            result = self.database_manager.query_one(
                "SELECT money FROM player_economy WHERE uuid = ?",
                (player_uuid,)
            )
            return result['money'] if result else 0
        except Exception as e:
            self.logger.error(f"{ColorFormat.RED}[ARC Core]Get player money error: {str(e)}")
            return 0

    def api_get_richest_player_money_data(self) -> list:
        """
        获取最富有玩家的信息
        :return: [玩家名称, 金钱数量]
        """
        try:
            result = self.database_manager.query_one(
                "SELECT uuid, money FROM player_economy ORDER BY money DESC LIMIT 1"
            )
            if result:
                player_name = self.get_player_name_by_uuid(result['uuid'])
                if player_name:
                    return [player_name, result['money']]
            return ["", 0]
        except Exception as e:
            self.logger.error(f"{ColorFormat.RED}[ARC Core]Get player money top error: {str(e)}")
            return ["", 0]

    def api_get_poorest_player_money_data(self) -> list:
        """
        获取最贫穷玩家的信息
        :return: [玩家名称, 金钱数量]
        """
        try:
            result = self.database_manager.query_one(
                "SELECT uuid, money FROM player_economy ORDER BY money ASC LIMIT 1"
            )
            if result:
                player_name = self.get_player_name_by_uuid(result['uuid'])
                if player_name:
                    return [player_name, result['money']]
            return ["", 0]
        except Exception as e:
            self.logger.error(f"{ColorFormat.RED}[ARC Core]Get player money bottom error: {str(e)}")
            return ["", 0]

    def api_change_player_money(self, player_name: str, money_to_change: int) -> None:
        """
        改变目标玩家的金钱
        :param player_name: 玩家名称
        :param money_to_change: 要改变的金钱数量（正数为增加，负数为减少）
        """
        try:
            if money_to_change == 0:
                self.logger.error(f'{ColorFormat.RED}[ARC Core]Money change cannot be zero...')
                return
            
            player_uuid = self.get_player_uuid_by_name(player_name)
            if not player_uuid:
                self.logger.error(f"{ColorFormat.RED}[ARC Core]Player {player_name} not found")
                return
            
            # 获取当前金钱
            current_money = self.api_get_player_money(player_name)
            new_money = current_money + money_to_change
            
            # 限制金钱范围在32位整数范围内
            new_money = max(-2147483648, min(2147483647, new_money))
            
            success = self.database_manager.update(
                table='player_economy',
                data={'money': new_money},
                where='uuid = ?',
                params=(player_uuid,)
            )
            
            if success:
                # 如果玩家在线，发送消息
                online_player = self.server.get_player(player_name)
                if online_player is not None:
                    if money_to_change < 0:
                        online_player.send_message(self.language_manager.GetText('MONEY_REDUCE_HINT').format(abs(money_to_change), new_money))
                    else:
                        online_player.send_message(self.language_manager.GetText('MONEY_ADD_HINT').format(money_to_change, new_money))
        except Exception as e:
            self.logger.error(f"{ColorFormat.RED}[ARC Core]Change player money error: {str(e)}")

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