# -*- coding: utf-8 -*-
"""core_setting.yml 的 OP 面板目录：类型、分组、选项。

跨服配置分层（与 dist/ARCCore/core_setting.yml 注释、[全服]/[本服] 标记一致）：
- 数据表：SYNC_CLIENT_SYNC_PLAYER / _ECONOMY / _TITLE / _GUILD（见 sync_config.SYNC_CATEGORY_TABLES）
- [全服] 规则：sync_config.ALL_SHARED_SETTING_KEYS（仅同步中心可改，从服只读）
- [本服] 玩法：目录中其余键（签到、传送、领地、邀请奖励等），各服 yml 独立
"""
from typing import Dict, List, Optional, Tuple

# stype: bool | choice | int | float | string | csv_list | json_triples
# json_triples: [[id, count, weight], ...] 目前仅 CHECKIN_REWARD_LIST

BOOL_FALSE = "False"
BOOL_TRUE = "True"

SettingSpec = Dict[str, object]


def _s(
    key: str,
    title: str,
    stype: str,
    *,
    choices: Optional[List[Tuple[str, str]]] = None,
    restart: bool = False,
    placeholder: str = "",
) -> SettingSpec:
    spec: SettingSpec = {
        "key": key,
        "title": title,
        "stype": stype,
        "restart": restart,
        "placeholder": placeholder,
    }
    if choices:
        spec["choices"] = choices
    return spec


BOOL_CHOICES: List[Tuple[str, str]] = [(BOOL_FALSE, "False"), (BOOL_TRUE, "True")]

SPAWN_MODE_CHOICES: List[Tuple[str, str]] = [
    ("whitelist", "whitelist（白名单）"),
    ("blacklist", "blacklist（黑名单）"),
]

SPAWN_SCOPE_CHOICES: List[Tuple[str, str]] = [
    ("public", "public（仅公共领地）"),
    ("all", "all（全部领地）"),
]

QQ_DEATH_BROADCAST_CHOICES: List[Tuple[str, str]] = [
    ("off", "不播报"),
    ("pvp", "仅播报PvP死亡"),
    ("all", "全部播报"),
]

SETTING_GROUPS: List[Dict[str, object]] = [
    {
        "id": "general",
        "title": "通用",
        "items": [
            _s("DEFAULT_LANGUAGE_CODE", "默认语言", "string", placeholder="ZH-CN", restart=True),
            _s("FORCE_LOGIN", "强制登录", "bool", choices=BOOL_CHOICES),
            _s("BROADCAST_INTERVAL", "广播间隔（秒）", "int", placeholder="120"),
            _s(
                "ENABLE_DEATH_BROADCAST",
                "游戏内死亡播报",
                "bool",
                choices=BOOL_CHOICES,
            ),
            _s(
                "QQ_DEATH_BROADCAST_MODE",
                "群聊死亡播报",
                "choice",
                choices=QQ_DEATH_BROADCAST_CHOICES,
            ),
            _s("ENABLE_CLEANER", "启用清道夫", "bool", choices=BOOL_CHOICES),
            _s("CLEANER_INTERVAL", "清道夫间隔（秒）", "int", placeholder="300"),
            _s("SMALL_HORN_PRICE_PER_HOUR", "小喇叭每小时费用", "int", placeholder="60"),
            _s("BLOCK_ALL_EXPLOSIONS", "全局拦截爆炸", "bool", choices=BOOL_CHOICES),
            _s("ENABLE_MSPT_EMERGENCY_SHUTDOWN", "MSPT 应急关服", "bool", choices=BOOL_CHOICES),
            _s("MSPT_EMERGENCY_SHUTDOWN_LIMIT", "MSPT 关服阈值", "int", placeholder="200"),
        ],
    },
    {
        "id": "sync",
        "title": "跨服同步",
        "items": [
            _s("DATABASE_PATH", "主数据库文件名（[本服]）", "string", placeholder="ARCCore.db", restart=True),
            _s("ENABLE_SYNC_SERVER", "启用同步中心", "bool", choices=BOOL_CHOICES, restart=True),
            _s("SYNC_SERVER_PORT", "同步中心端口", "int", placeholder="19999", restart=True),
            _s("SYNC_SERVER_AUTH_KEY", "同步中心密钥", "string", placeholder="", restart=True),
            _s("ENABLE_SYNC_CLIENT", "启用同步客户端", "bool", choices=BOOL_CHOICES, restart=True),
            _s("SYNC_SERVER_IP", "同步中心 IP", "string", placeholder="127.0.0.1", restart=True),
            _s("SYNC_CLIENT_PORT", "同步客户端端口", "int", placeholder="19999", restart=True),
            _s("SYNC_CLIENT_SERVER_ID", "本服 ID", "string", placeholder="server_001", restart=True),
            _s("SYNC_CLIENT_SERVER_NAME", "本服名称", "string", placeholder="服务器01", restart=True),
            _s("SYNC_CLIENT_AUTH_KEY", "同步客户端密钥", "string", placeholder="", restart=True),
            _s("SYNC_CLIENT_RECONNECT_INTERVAL", "重连间隔（秒）", "int", placeholder="10"),
            _s("SYNC_CLIENT_SYNC_PLAYER", "同步玩家账号数据", "bool", choices=BOOL_CHOICES, restart=True),
            _s("SYNC_CLIENT_SYNC_ECONOMY", "同步银行存款数据", "bool", choices=BOOL_CHOICES, restart=True),
            _s("SYNC_CLIENT_SYNC_TITLE", "同步头衔数据", "bool", choices=BOOL_CHOICES, restart=True),
            _s("SYNC_CLIENT_SYNC_GUILD", "同步公会数据", "bool", choices=BOOL_CHOICES, restart=True),
            _s(
                "CONDITIONAL_TITLE_AUTHORITY",
                "条件头衔权威服（[本服] 身份）",
                "bool",
                choices=BOOL_CHOICES,
                restart=True,
            ),
        ],
    },
    {
        "id": "economy",
        "title": "经济",
        "items": [
            _s("PLAYER_INIT_MONEY_NUM", "[全服] 新玩家初始存款", "float", placeholder="2000"),
            _s("HIDE_OP_IN_MONEY_RANKING", "[全服] 排行榜隐藏 OP", "bool", choices=BOOL_CHOICES),
            _s("RICHEST_TITLE_NAME", "[全服] 首富头衔名", "string", placeholder="首富"),
        ],
    },
    {
        "id": "spawn",
        "title": "出生点保护",
        "items": [
            _s("IF_PROTECT_SPAWN", "启用出生点保护", "bool", choices=BOOL_CHOICES),
            _s("SPAWN_PROTECT_RANGE", "出生点保护半径", "int", placeholder="8"),
        ],
    },
    {
        "id": "land",
        "title": "领地",
        "items": [
            _s("ENABLE_LAND_SYSTEM", "启用领地系统", "bool", choices=BOOL_CHOICES),
            _s("ALLOW_LAND_CLAIM", "允许圈地", "bool", choices=BOOL_CHOICES),
            _s("MIN_LAND_DISTANCE", "领地最小间距", "int", placeholder="1"),
            _s("LAND_PRICE", "领地单价", "int", placeholder="50"),
            _s("LAND_SELL_REFUND_COEFFICIENT", "删除领地退款系数", "float", placeholder="0.9"),
            _s("LAND_SALE_VAT_RATE", "私人领地成交增值税率", "float", placeholder="0.1"),
            _s("LAND_MIN_SIZE", "领地最小边长", "int", placeholder="5"),
            _s("DEFAULT_FREE_LAND_BLOCKS", "新玩家免费领地格", "int", placeholder="100"),
            _s("LAND_ONLY_PLACE_BLOCKS", "仅允许领地内放置的方块", "csv_list", placeholder="minecraft:beacon"),
            _s("DISABLED_BLOCKS", "全局禁用方块", "csv_list", placeholder="minecraft:hopper"),
            _s("PUBLIC_LAND_PROTECTED_ENTITIES", "公共领地保护生物", "csv_list", placeholder="minecraft:villager_v2"),
            _s(
                "PUBLIC_LAND_BLOCK_ACTOR_SPAWN_MODE",
                "公共领地拦截生物生成模式",
                "choice",
                choices=SPAWN_MODE_CHOICES,
            ),
            _s(
                "LAND_BLOCK_ACTOR_SPAWN_SCOPE",
                "拦截生物生成作用范围",
                "choice",
                choices=SPAWN_SCOPE_CHOICES,
            ),
            _s(
                "PUBLIC_LAND_BLOCK_ACTOR_SPAWN_LIST",
                "公共领地拦截生物生成名单",
                "csv_list",
                placeholder="minecraft:zombie",
            ),
            _s(
                "PUBLIC_LAND_INTERACT_BLOCK_BLACKLIST",
                "公共领地禁止交互方块",
                "csv_list",
                placeholder="minecraft:frame",
            ),
        ],
    },
    {
        "id": "teleport",
        "title": "传送",
        "items": [
            _s("MAX_PLAYER_HOME_NUM", "玩家 Home 上限", "int", placeholder="10"),
            _s("ENABLE_TELEPORT_PUBLIC_WARP", "启用公共传送点", "bool", choices=BOOL_CHOICES),
            _s("ENABLE_TELEPORT_HOME", "启用私人传送点", "bool", choices=BOOL_CHOICES),
            _s("ENABLE_RANDOM_TELEPORT", "启用随机传送", "bool", choices=BOOL_CHOICES),
            _s(
                "ENABLE_TELEPORT_DEATH_LOCATION",
                "启用死亡点传送",
                "bool",
                choices=BOOL_CHOICES,
            ),
            _s("ENABLE_TELEPORT_PLAYER", "启用玩家互传", "bool", choices=BOOL_CHOICES),
            _s(
                "ENABLE_TELEPORT_CROSS_SERVER",
                "启用跨服传送",
                "bool",
                choices=BOOL_CHOICES,
            ),
            _s("RANDOM_TELEPORT_CENTER_X", "随机传送中心 X", "int", placeholder="0"),
            _s("RANDOM_TELEPORT_CENTER_Z", "随机传送中心 Z", "int", placeholder="0"),
            _s("RANDOM_TELEPORT_RADIUS", "随机传送半径", "int", placeholder="4096"),
            _s("TELEPORT_COST_PUBLIC_WARP", "公共传送点费用", "int", placeholder="0"),
            _s("TELEPORT_COST_HOME", "Home 传送费用", "int", placeholder="0"),
            _s("TELEPORT_COST_LAND", "领地传送费用", "int", placeholder="0"),
            _s("TELEPORT_COST_DEATH_LOCATION", "死亡点传送费用", "int", placeholder="0"),
            _s("TELEPORT_COST_RANDOM", "随机传送费用", "int", placeholder="0"),
            _s("TELEPORT_COST_PLAYER", "传送到玩家费用", "int", placeholder="0"),
        ],
    },
    {
        "id": "checkin",
        "title": "每日签到",
        "items": [
            _s("CHECKIN_DAILY_MONEY", "签到基础存款", "float", placeholder="1000"),
            _s("CHECKIN_TOP_RANK_LIMIT", "签到前 X 名额外奖励", "int", placeholder="5"),
            _s("CHECKIN_TOP_RANK_BONUS_ITEM_COUNT", "前排额外物品条数", "int", placeholder="1"),
            _s("CHECKIN_TOP_RANK_BONUS_MONEY_STEP", "前排额外金钱步长", "float", placeholder="200"),
            _s("CHECKIN_CONTINUOUS_DAYS_MONEY_INCREMENT", "连续签到金钱步长", "float", placeholder="100"),
            _s("CHECKIN_GUILD_CONTRIBUTION_POINTS", "签到公会贡献点", "int", placeholder="100"),
            _s("CHECKIN_REWARD_PICK_MIN", "随机物品抽取最少条数", "int", placeholder="1"),
            _s("CHECKIN_REWARD_PICK_MAX", "随机物品抽取最多条数", "int", placeholder="2"),
            _s("CHECKIN_REWARD_LIST", "签到物品奖励池", "json_triples"),
        ],
    },
    {
        "id": "invite",
        "title": "邀请奖励",
        "items": [
            _s("INVITE_REWARD_ITEM_NAME", "邀请奖励物品 ID", "string", placeholder="minecraft:diamond"),
            _s("INVITE_REWARD_ITEM_COUNT", "邀请奖励物品数量", "int", placeholder="1"),
            _s("INVITE_REWARD_MONEY", "邀请奖励金钱", "float", placeholder="0"),
            _s("INVITE_REWARD_FREE_LAND_BLOCKS", "邀请奖励免费领地格", "int", placeholder="10"),
        ],
    },
    {
        "id": "title",
        "title": "头衔",
        "items": [
            _s("DEFAULT_TITLE", "[全服] 默认头衔（列表）", "csv_list", placeholder="见习冒险家"),
            _s("OP_TITLE", "[全服] OP 专属头衔", "string", placeholder="管理员"),
        ],
    },
    {
        "id": "skyeye",
        "title": "天眼",
        "items": [
            _s("ENABLE_SKY_EYE", "启用天眼", "bool", choices=BOOL_CHOICES),
            _s("SKY_EYE_LOG_ACTOR_DAMAGE", "记录生物/玩家攻击天眼", "bool", choices=BOOL_CHOICES),
            _s("SKY_EYE_MAX_RETENTION_DAYS", "天眼保留天数", "int", placeholder="7"),
        ],
    },
    {
        "id": "guild",
        "title": "公会",
        "items": [
            _s("GUILD_CREATE_COST", "[全服] 创建公会费用", "float", placeholder="100000"),
            _s("GUILD_SIZE_SMALL_MAX", "[全服] 小型公会人数上限", "int", placeholder="10"),
            _s("GUILD_SIZE_MEDIUM_MAX", "[全服] 中型公会人数上限", "int", placeholder="20"),
            _s("GUILD_SIZE_LARGE_MAX", "[全服] 大型公会人数上限", "int", placeholder="40"),
            _s("GUILD_UPGRADE_TO_MEDIUM_COST", "[全服] 升级中型消耗贡献", "int", placeholder="10000"),
            _s("GUILD_UPGRADE_TO_LARGE_COST", "[全服] 升级大型消耗贡献", "int", placeholder="100000"),
            _s("GUILD_RENAME_COST", "[全服] 公会改名费用", "float", placeholder="0"),
            _s("GUILD_LAND_TELEPORT_CONTRIB_COST", "[本服] 公会领地传送贡献消耗", "int", placeholder="10"),
            _s("KILL_REWARD_GUILD_CONTRIB_RATIO", "[本服] 击杀金钱转贡献比例", "float", placeholder="0.5"),
        ],
    },
    {
        "id": "sidebar",
        "title": "侧边栏",
        "items": [
            _s("SIDEBAR_ENABLE", "启用侧边栏", "bool", choices=BOOL_CHOICES),
            _s("SIDEBAR_DEFAULT_ON", "新玩家默认开启", "bool", choices=BOOL_CHOICES),
            _s("SIDEBAR_TITLE", "侧边栏标题", "string", placeholder="§6弧光服务器"),
            _s("SIDEBAR_SWITCH_INTERVAL", "翻页间隔（秒）", "int", placeholder="10"),
            _s("SIDEBAR_REFRESH_TICKS", "刷新周期（tick；20=1秒，默认60≈3秒）", "int", placeholder="60"),
            _s("SIDEBAR_JOIN_DELAY_TICKS", "进服后延迟显示（tick，默认40）", "int", placeholder="40"),
            _s("SIDEBAR_MAX_LINES", "最大行数", "int", placeholder="15"),
            _s(
                "SIDEBAR_MAIN_LINES",
                "主页面行模板（\\n 分隔）",
                "string",
                placeholder="留空=内置默认",
            ),
        ],
    },
]


def get_group(group_id: str) -> Optional[Dict[str, object]]:
    for group in SETTING_GROUPS:
        if group["id"] == group_id:
            return group
    return None


def get_spec(group_id: str, key: str) -> Optional[SettingSpec]:
    group = get_group(group_id)
    if not group:
        return None
    for item in group["items"]:
        if item["key"] == key:
            return item
    return None
