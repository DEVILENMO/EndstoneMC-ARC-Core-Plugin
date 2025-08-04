import hashlib
import json
import math
from pathlib import Path
from typing import Dict, Any, Optional, Set

from endstone import ColorFormat, Player, GameMode
from endstone._internal.endstone_python import ActionForm, TextInput, ModalForm
from endstone.command import Command, CommandSender
from endstone.event import event_handler, PlayerJoinEvent, PlayerQuitEvent, BlockBreakEvent, BlockPlaceEvent, PlayerDeathEvent 
from endstone.plugin import Plugin

from endstone_arc_core.DatabaseManager import DatabaseManager
from endstone_arc_core.LanguageManager import LanguageManager
from endstone_arc_core.SettingManager import SettingManager

MAIN_PATH = 'plugins/ARCCore'

class ARCCorePlugin(Plugin):
    api_version = "0.7"
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

    def on_load(self) -> None:
        self.logger.info(f"{ColorFormat.YELLOW}[ARC Core]Plugin loaded!")

    def on_enable(self) -> None:
        self.register_events(self)
        self.logger.info(f"{ColorFormat.YELLOW}[ARC Core]Plugin enabled!")

        # Scheduler tasks
        self.server.scheduler.run_task(self, self.player_position_listener, delay=0, period=25)
        self.server.scheduler.run_task(self, self.cleanup_expired_teleport_requests, delay=0, period=100)  # 每5秒清理一次过期请求

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
        if not self.operation_check(event.player, event.block.location.dimension.name,
                                    (event.block.location.x, event.block.location.y, event.block.location.z)):
            event.is_cancelled = True
        return

    @event_handler
    def on_block_place(self, event: BlockPlaceEvent):
        if event.player.is_op:
            return
        if not self.operation_check(event.player, event.block.location.dimension.name,
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

    def operation_check(self, player: Player, dimension: str, pos: tuple):
        if self.if_protect_spawn and len(self.spawn_pos_dict):
            if not self.spawn_protect_check(dimension, pos[0], pos[2]):
                player.send_message(self.language_manager.GetText('SPAWN_PROTECT_HINT').format(self.spawn_protect_range))
                return False
        land_id = self.get_land_at_pos(dimension, pos[0], pos[2])
        print(f'land_id: {land_id}')
        if land_id is not None:
            owner_uuid = self.get_land_owner(land_id)
            if owner_uuid != str(player.unique_id):
                player.send_message(self.language_manager.GetText('LAND_PROTECT_HINT').format(self.get_player_name_by_uuid(owner_uuid)))
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
    def init_player_basic_table(self) -> bool:
        """初始化玩家基本信息表"""
        fields = {
            'uuid': 'TEXT PRIMARY KEY',  # 玩家UUID作为主键
            'xuid': 'TEXT NOT NULL',  # 玩家XUID
            'name': 'TEXT NOT NULL',  # 玩家名称
            'password': 'TEXT'  # 玩家密码(加密后的)，允许为NULL
        }
        return self.database_manager.create_table('player_basic_info', fields)

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
                'password': None  # 初始密码为空
            }
            return self.database_manager.insert('player_basic_info', player_data)
        except Exception as e:
            self.logger.error(f"{ColorFormat.RED}[ARC Core]Init player basic info error: {str(e)}")
            return False

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
                    self.logger.info(f"Player {current_info['name']} changed name to {player.name}")
                return success

            return True  # 名称没有变化，视为成功
        except Exception as e:
            self.logger.error(f"{ColorFormat.RED}[ARC Core]Update player name error: {str(e)}")
            return False

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
            arc_menu.add_button(self.language_manager.GetText('SHOP_MENU_NAME'), on_click=self.show_shop_menu)
            if player.is_op:
                arc_menu.add_button(self.language_manager.GetText('OP_PANEL_NAME'), on_click=self.show_op_main_panel)
            arc_menu.on_close = None
            player.send_form(arc_menu)

    # Register and login
    def login_successfully(self, player: Player):
        self.player_authentication_state[player.name] = True

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
        rank_dict = self.get_top_richest_players(10)
        rank_list = []
        for i, (player_name, player_money) in enumerate(rank_dict.items()):
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
        if player.location.dimension.name != warp_info['dimension']:
            player.send_message(self.language_manager.GetText('TELEPORT_DIMENSION_ERROR').format(warp_name, warp_info['dimension']))
            return
        
        self.start_teleport_countdown(player, warp_name, (warp_info['x'], warp_info['y'], warp_info['z']), 'PUBLIC_WARP')

    def teleport_to_home(self, player: Player, home_name: str, home_info: Dict[str, Any]):
        """传送到玩家传送点"""
        if player.location.dimension.name != home_info['dimension']:
            player.send_message(self.language_manager.GetText('TELEPORT_DIMENSION_ERROR').format(home_name, home_info['dimension']))
            return
        
        self.start_teleport_countdown(player, home_name, (home_info['x'], home_info['y'], home_info['z']), 'HOME')

    def start_teleport_countdown(self, player: Player, destination_name: str, position: tuple, teleport_type: str):
        """开始传送倒计时"""
        self.server.scheduler.run_task(
            self, 
            lambda: self.execute_teleport(player, destination_name, position, teleport_type), 
            delay=45
        )
        
        if teleport_type == 'PUBLIC_WARP':
            message = self.language_manager.GetText('TELEPORT_TO_WARP_COUNTDOWN').format(destination_name)
        elif teleport_type == 'HOME':
            message = self.language_manager.GetText('TELEPORT_TO_HOME_COUNTDOWN').format(destination_name)
        else:
            message = self.language_manager.GetText('TELEPORT_COUNTDOWN').format(destination_name)
        
        player.send_message(message)

    def execute_teleport(self, player: Player, destination_name: str, position: tuple, teleport_type: str):
        """执行传送"""
        if teleport_type == 'PUBLIC_WARP':
            message = self.language_manager.GetText('TELEPORT_TO_WARP_SUCCESS').format(destination_name)
        elif teleport_type == 'HOME':
            message = self.language_manager.GetText('TELEPORT_TO_HOME_SUCCESS').format(destination_name)
        else:
            message = self.language_manager.GetText('TELEPORT_SUCCESS').format(destination_name)
        
        player.send_message(message)
        self.server.dispatch_command(self.server.command_sender, self.generate_tp_command(player.name, position))

    # Death Location Teleport
    def teleport_to_death_location(self, player: Player):
        """传送到死亡地点"""
        if player.name not in self.player_death_locations:
            player.send_message(self.language_manager.GetText('NO_DEATH_LOCATION_RECORDED'))
            return
        
        death_location = self.player_death_locations[player.name]
        
        # 检查维度
        if player.location.dimension.name != death_location['dimension']:
            player.send_message(self.language_manager.GetText('TELEPORT_DIMENSION_ERROR').format(
                self.language_manager.GetText('DEATH_LOCATION_NAME'), 
                death_location['dimension']
            ))
            return
        
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
        
        # 执行传送
        player.send_message(self.language_manager.GetText('TELEPORT_TO_DEATH_LOCATION_SUCCESS'))
        self.server.dispatch_command(self.server.command_sender, self.generate_tp_command(player.name, position))
        
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
        
        # 检查维度
        if sender.location.dimension.name != player.location.dimension.name:
            player.send_message(self.language_manager.GetText('TELEPORT_REQUEST_DIMENSION_ERROR'))
            sender.send_message(self.language_manager.GetText('TELEPORT_REQUEST_DIMENSION_ERROR'))
            del self.teleport_requests[player.name]
            return
        
        # 执行传送
        if request['type'] == 'tpa':
            # TPA: 发送者传送到接受者处
            self.start_teleport_countdown(sender, player.name, self.get_player_position_vector(player), 'PLAYER_REQUEST')
            player.send_message(self.language_manager.GetText('TPA_REQUEST_ACCEPTED').format(sender.name))
            sender.send_message(self.language_manager.GetText('TPA_REQUEST_ACCEPTED_BY_TARGET').format(player.name))
        else:
            # TPHERE: 接受者传送到发送者处
            self.start_teleport_countdown(player, sender.name, self.get_player_position_vector(sender), 'PLAYER_REQUEST')
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
                'shared_users': 'TEXT'  # 共有人UUID列表(JSON字符串)
            }

            return self.database_manager.create_table('lands', land_fields)
        except Exception as e:
            self.logger.error(f"Init land tables error: {str(e)}")
            return False

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
                "INSERT INTO lands (owner_uuid, land_name, dimension, min_x, max_x, min_z, max_z, tp_x, tp_y, tp_z, shared_users) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (owner_uuid, land_name, dimension, min_x, max_x, min_z, max_z, tp_x, tp_y, tp_z, '[]')
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
                    'shared_users': json.loads(land['shared_users'])
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
                    'owner_uuid': result['owner_uuid']
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
        land_dimension = self.get_land_dimension(land_id)
        if player.location.dimension.name != land_dimension:
            player.send_message(self.language_manager.GetText('TELEPORT_TO_LAND_FAIL_DIMENSION_ERROR').format(land_id, land_dimension))
            return
        tp_target_pos = self.get_land_teleport_point(land_id)
        self.server.scheduler.run_task(self, lambda p=player, l_id=land_id, pos=tp_target_pos: self.delay_teleport_to_land(p, l_id, pos), delay=45)
        player.send_message(self.language_manager.GetText('READY_TELEPORT_TO_LAND').format(land_id))

    def delay_teleport_to_land(self, player: Player, land_id: int, position: tuple):
        player.send_message(self.language_manager.GetText('TELEPORT_TO_LAND_START_HINT').format(land_id))
        self.server.dispatch_command(self.server.command_sender, self.generate_tp_command(player.name, position))

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
        self.server.dispatch_command(self.server.command_sender, 'kill @e[type=item]')

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

    # Tool
    @staticmethod
    def generate_tp_command(player_name: str, position: tuple):
        return f'tp {player_name} {' '.join([str(int(_)) for _ in position])}'

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
                        online_player.send_message(f'{ColorFormat.YELLOW}{self.language_manager.GetText("MONEY_CHANGE")}: '
                                                f'{ColorFormat.RED}-{abs(money_to_change)}\n'
                                                f'{ColorFormat.YELLOW}{self.language_manager.GetText("YOUR_MONEY")}: '
                                                f'{ColorFormat.WHITE}{new_money}')
                    else:
                        online_player.send_message(f'{ColorFormat.YELLOW}{self.language_manager.GetText("MONEY_CHANGE")}: '
                                                f'{ColorFormat.GREEN}+{money_to_change}\n'
                                                f'{ColorFormat.YELLOW}{self.language_manager.GetText("YOUR_MONEY")}: '
                                                f'{ColorFormat.WHITE}{new_money}')
        except Exception as e:
            self.logger.error(f"{ColorFormat.RED}[ARC Core]Change player money error: {str(e)}")