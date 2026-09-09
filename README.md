<div align="center">

# EndStone ARC Core Plugin / EndStone弧光核心

[![Codacy Grade](https://app.codacy.com/project/badge/Grade/2f830615baf347258558dcc2a5ab85a1)](https://app.codacy.com/gh/DEVILENMO/EndstoneMC-ARC-Core-Plugin/dashboard?utm_source=gh&utm_medium=referral&utm_content=&utm_campaign=Badge_grade)
[![Version](https://img.shields.io/badge/version-v0.9.47-blue)](https://github.com/ARC-Minecraft/EndstoneMC-ARC-Core-Plugin)
[![Python](https://img.shields.io/badge/python-3.13+-blue?logo=python&logoColor=white)](https://www.python.org/)
[![EndStone API](https://img.shields.io/badge/EndStone_API-0.7+-black)](https://github.com/EndstoneMC/endstone)
[![License](https://img.shields.io/github/license/ARC-Minecraft/EndstoneMC-ARC-Core-Plugin)](LICENSE)
[![GitHub stars](https://img.shields.io/github/stars/ARC-Minecraft/EndstoneMC-ARC-Core-Plugin)](https://github.com/ARC-Minecraft/EndstoneMC-ARC-Core-Plugin/stargazers)

</div>

## 概述

EndStone ARC Core 是一个功能完整的 EndStone (Minecraft 基岩版服务器) 插件，为服务器提供全方位的核心功能模块。该插件包含玩家管理、经济系统、领地管理、传送系统、公告系统、清道夫系统、天眼行为审计、**侧边栏总控**等丰富功能，是构建现代化 Minecraft 服务器的理想选择。插件体验服：IP：arcclub.top，端口：19132，你可以在这个服务器试用体验本插件。

## 作者信息

- **作者**: DEVILENMO
- **邮箱**: DEVILENMO@gmail.com
- **版本**: 0.9.38
- **API 版本**: 0.7+
- **推荐 Python 版本**: 3.13

## ✨ 主要功能

### 🗄️ 数据库管理
- 基于 SQLite 的高性能数据库支持
- 线程安全的数据库连接管理
- 自动创建数据库文件和目录
- 支持复杂查询和事务处理
- **XUID主键系统** - 全面使用XUID作为玩家主键，提升数据一致性和查询性能
- **说明** - 自 v0.2.3 起不再支持从 UUID 到 XUID 的自动迁移，请使用已迁移至 XUID 的数据库或旧版本完成迁移后再升级

### 🌍 多语言支持
- 完整的国际化系统
- 动态语言文件加载
- 默认支持中文 (ZH-CN)
- 可扩展其他语言包

### 🧭 主菜单与子命令（`/arc`）
- **进服自动弹出主菜单**：玩家加入服务器后约 **1 秒**（20 ticks）会 **自动弹出 ARC 主菜单一次**，**无需配置**；可直接 **关闭** 表单继续游玩，亦可随时再输入 **`/arc`** 打开。此项 **不是**「强制登录」——浏览菜单不设密码门槛；敏感操作仍见下文「敏感操作密码验证」。
- 主菜单前几项顺序为：**新手引导 → 传送系统 → 领地系统 → 银行 → 公会 → 每日签到 → 我的信息 → 工具**（含小喇叭与重生）→ …；浏览与一般入口 **无需** 预先输入账户密码；涉及资金与领地等安全步骤见「玩家管理系统」中的 **敏感操作密码验证**。
- **`/arc land`**、**`/arc tp`**、**`/arc bank`**、**`/arc guild`** 分别直接打开 **领地菜单**、**传送菜单**、**银行菜单**、**公会菜单**（若从控制台/命令方块执行，会按 **命令发送者名称** 解析在线玩家，与 `/connecttoserver` 相同机制，便于命令方块代为弹出表单）。进入菜单后，**转账、创建/管理领地、公会创建等敏感操作**仍会按需弹出密码验证（未设密会先引导设密），详见「玩家管理系统」。

### 👤 玩家管理系统
- **敏感操作密码验证（非进服门禁）**：打开 **`/arc`**、浏览主菜单 **无需** 预先输入密码；**转账、创建或管理领地、公会创建等敏感操作**会在执行前要求 **账户密码验证**。同一 **游戏会话**（自进服至退出）内验证成功 **一次** 即可重复使用；若尚未在数据库中设置密码，会先弹出 **设密（与注册相同流程）** 再继续敏感操作。（进服时主菜单会自动弹出一次，见上文「主菜单与子命令」。）
- **会话内验证状态**：已设密的玩家使用 **SHA-256** 存储的密码校验；验证通过后本会话内标记为已验证（修改密码会清除该标记，需重新验证）。语言提示见 **`dist/ARCCore/ZH-CN.txt`** 中 **`SENSITIVE_*`** 等键。
- **注册确认密码**（v0.3.0）：首次设密 / 注册时需输入两次密码，一致方可完成。
- **修改密码（我的信息）**：主菜单 **我的信息** →「修改密码」。已设置账户密码时需填写 **当前密码**、**新密码** 与 **确认新密码**（新密码不可与当前密码相同）；尚未设置密码时打开与首次注册相同的 **设密 + 确认** 表单（标题为单独提示文案），成功或关闭表单后均回到 **我的信息**。修改成功后，本会话内「敏感操作已验证」状态会清除，之后转账、领地等需用 **新密码** 再验证一次。相关语言键见 `dist/ARCCore/ZH-CN.txt` 中 `CHANGE_PASSWORD_*`
- 玩家数据持久化存储（跨服账号：`player_basic_info`；本服档案：`player_local_info`）
- 在线状态实时管理
- 玩家加入/离开消息提示
- **免费领地格子**、**OP 标记**、**签到** 均为本服数据，不随同步中心跨服覆盖
- **游戏时长 / 进服次数** 写在跨服表 `player_basic_info`，随同步中心或共享库跨服累计

### 👁️ 天眼系统（Sky Eye，v0.8.8）
- **用途**：可选开启的玩家行为审计；写入独立 SQLite，供排查与弧光天星即时查询
- **配置**（`core_setting.yml`）：**`ENABLE_SKY_EYE`**（`True`/`False`，默认关闭）、**`SKY_EYE_MAX_RETENTION_DAYS`**（保留天数，默认 **7**；更早记录会从库中删除；**`0`** 表示不自动删除）
- **存储**：**`plugins/ARCCore/sky_eye/skyeye.db`**（独立库，不进主库、不走跨服同步）。升级前的按日 **`YYYYMMDD.txt`** 仍会按同一保留天数清理，新事件不再写 txt
- **记录字段**：时间、行为、玩家名、XUID、维度、坐标、主手物品（含附魔摘要）、`detail`、**是否在领地内**、领地 ID/名称/主人、攻击对象（打了谁 / 被谁打）
- **战斗增强（v0.9.10）**：每次玩家攻击记录 `damage` / `hp_before` / `hp_after` / `max_hp` / `cause`；PvP 另记 `victim_armor`（被打方当时装备+附魔）；死亡记录 `armor`（头盔/胸甲/护腿/靴子/副手）与 `inv`（背包格子摘要）
- **已挂钩行为**：进服 / 离服、方块破坏与放置、对方块交互与无方块交互、与实体交互、**玩家造成的伤害（含 PvP）**、玩家死亡（含击杀者与死亡时装备/背包）；关闭开关时不写盘
- **对外查询 API**（其它插件 / 天星）：`api_sky_eye_query`、`api_sky_eye_query_text`、`api_sky_eye_player_now`。天星工具：`mc_skyeye_player` / `mc_skyeye_combat` / `mc_skyeye_events` / `mc_skyeye_location`（仅 OP / QQ 管理）
- **查询增强（v0.9.28）**：玩家名默认**模糊匹配**（子串、忽略大小写）；`action` 支持类别别名（`death`/`pvp`/`pve`/`combat`/`死亡`/`打怪` 等）及 PvP/PvE 细分；可不传玩家名仅按事件类型+时间查全服（如「最近 24 小时谁死了」）

### 💰 银行经济系统
- 完整的货币管理系统，**金钱精确到分**（float 存储，两位小数）
- 玩家余额存储和查询
- **升级转账功能** - 两步式转账流程，先选择玩家再输入金额，支持小数金额
- 富豪榜排行系统
- 管理员金钱操作命令
- 实时余额变动提醒
- **财富榜首富头衔（v0.4.0 / v0.8.22）** - 配置 `RICHEST_TITLE_NAME`（默认「首富」）、传奇稀有度；金钱变动后自动刷新财富榜第一；若首富易主则撤销旧头衔并授予新首富；同分按 xuid 稳定排序；跨服仅主服计算、从服只消费同步头衔；可在 **OP 面板 → 经济管理 → 经济参数配置** 中修改

### 📊 侧边栏总控（v0.9.0）
- **原生计分板 SIDE_BAR**：每玩家独立 `Scoreboard`，复用稳定 objective 原地刷新（避免频繁销毁重建导致客户端闪退）
- **多页面 + 定时翻页**：可见页 ≥ 2 时默认每 **10 秒**自动切换；可用 `/sidebar lock` 锁定
- **核心主页面 `arc_core_main`**：现实时间、金钱、**TPS**、在线人数、**玩家延迟**等（模板可配；默认不含生命/饱食）
- **其它插件注册页面**：`api_sidebar_register_page` + 行模板 `{key}`，数值变更用 `api_sidebar_set_value(s)` 推送（等定时 tick 渲染）
- **玩家命令**：`/sidebar`（别名 `/sb`）支持 `on` / `off` / `next` / `prev` / `lock` / `unlock` / `list`；开关与锁定偏好持久化到 SQLite
- **刷新策略（v0.9.32+）**：调度每 1 tick 检查到期玩家；进服时记录 `join_tick`，`(当前tick - join_tick) % SIDEBAR_REFRESH_TICKS == 0` 时重绘，自然错峰；TPS/时间等缓存在整服周期 tick 更新；插件 `set_value` 只写缓存；`/sidebar` 命令仍即时刷新
- **配置**：`SIDEBAR_ENABLE`、`SIDEBAR_DEFAULT_ON`、`SIDEBAR_TITLE`、`SIDEBAR_SWITCH_INTERVAL`、`SIDEBAR_REFRESH_TICKS`（20=1秒，默认 60≈3秒）、`SIDEBAR_JOIN_DELAY_TICKS`（默认 40）、`SIDEBAR_MAIN_LINES`、`SIDEBAR_MAX_LINES`（亦可在 OP 面板「侧边栏」分组修改）

### 🏠 领地管理系统
- **三维领地** - 按 min/max X/Y/Z 圈地，按体积计价；粒子显示立方体边界（与「进入领地」时边界粒子一致）
- **创建领地流程** - 菜单「创建新领地」后按提示 **交互四个方块**：水平矩形两角（取 X/Z）→ 最低 Y → 最高 Y，完成后进入 **购买确认面板**（可再次播放边界粒子、用六个整数框修改 min/max X/Y/Z、确认购买）；亦可用 **「手动输入六向坐标」** 或 **`/land pos1`、`/land pos2`、`/land buy`** 快捷选点
- **待购面板** - 购买前可随时用 **`/land buy`** 重新打开同一面板；该指令为打开面板而非直接扣款
- **领地保护机制**（防止破坏/建造/方块互动）
- **免费领地格子系统** - 新玩家可获得免费格子，购买领地时自动减免费用
- **领地授权系统** - 可将领地权限授权给其他玩家
- **子领地系统** - 领地主人可在领地内创建子领地并授权他人；子领地为三维、不可重叠、不可超出父领地；交互时先判子领地权限再判父领地
- **公共领地「允许圈私人领地」** - 公共领地可开启后，玩家可在其内购买私人领地；同一位置优先按私人领地权限判定
- **公共领地三级优先级** - 字段 `public_priority`（1/2/3，**3 最高**，默认 1）。高优先级公共可覆盖低优先级公共；同级不可重叠。生效顺序：**私人/公会 > 公共(3>2>1)**；私人子领地权限仍先于父私人领地。创建公共领地时 OP 选择等级；OP 公共领地设置中可修改（若与同级/更高公共冲突则拒绝）
- **公共领地「拦截生物生成」** - 每个公共领地可单独开关（数据库字段 `block_actor_spawn`）。全局配置 **`PUBLIC_LAND_BLOCK_ACTOR_SPAWN_MODE`** 仅决定拦截规则：**whitelist**（默认）= 名单上的不拦截、其余拦截（名单为空则拦截全部）；**blacklist** = 只拦截名单上的。名单见 **`PUBLIC_LAND_BLOCK_ACTOR_SPAWN_LIST`**（逗号分隔实体 ID）。**`LAND_BLOCK_ACTOR_SPAWN_SCOPE`**：`public`（默认）= 仅公共领地且看各领地开关；`all` = 任意领地（私人/公会/公共）均按全局名单拦截、不看各领地开关。通过 `ActorSpawnEvent` 取消匹配实体生成（含模组生物，不含玩家）
- **领地移交功能** - 可将领地转移给其他玩家
- **私人领地上架出售（v0.7.4）** - 领地详情中 **「出售领地（上架/改价/下架）」**：主人可设置正数标价并上架；其他玩家 **进入** 该私人领地时（非主人）在原有进入提示与边界粒子后，会收到 **购买表单**（领地名、标价、当前主人、购买/关闭）。购买时扣买家款、过户给买家、`owner_paid_money` 记为成交价，**清空授权列表**；卖家在线会收到成交通知。数据库 `lands` 表新增 **`for_sale`**、**`sale_price`**（旧库启动时自动 `ALTER`）。**公共领地 / 公会领地** 不适用此流程；若向卖家入账失败会尝试 **回滚过户并退款**（极端失败会提示联系管理员）
- **私人领地成交增值税（v0.7.6 文档化）** - 配置 **`LAND_SALE_VAT_RATE`**（`core_setting.yml`，默认 `0.1` 即 10%，取值 **0～1**；**`0` 关闭**）。成交时 **买家按标价全额付款**；**卖家实收** = 成交价 − 增值税额。**税基（溢价）** = `max(0, 成交价 − 过户前 owner_paid_money)`；**增值税额** = 税基 × 税率（金额按分四舍五入）。平价或低于买入价成交不产生增值税。卖家在线提示中含成交价、增值税、实收（语言键 **`LAND_SALE_BUY_SUCCESS_SELLER`** 等，见 `ZH-CN.txt`）。**OP 重载配置** 后刷新税率
- **爆炸保护设置** - 可单独控制领地内是否允许爆炸；全局 **`BLOCK_ALL_EXPLOSIONS`**（默认开）与领地禁止爆炸时优先 **清空 `block_list`**（不拆方块、尽量保留实体伤害），写回失败则回退取消整次爆炸；全局关闭时按领地 **`allow_explosion`** 做 **逐方块** 保护
- **方块互动开放设置** - 可设置领地对所有人开放方块互动（如开箱子、按按钮等）
- **生物保护系统** - 可控制领地内是否允许与生物交互和攻击生物
- **展示框权限设置** - 可禁止领地对展示框/发光展示框及各材质展示架的互动与破坏（默认禁止，防止他人取物）；**关闭展示框权限时**，领地主人、授权玩家、子领地权限持有者及（若开启「公会成员可交互」）同公会成员 **仍可** 操作展示框/架，不受此项拦截
- **领地范围重设（v0.7.5+）** - 私人/公会领地在 **我的领地 → 领地详情 →「重设领地范围」**；公共领地在 **OP 领地管理 → 领地详情 →「重设公共领地范围」**。流程与 **新建领地相同**（四角选点或手动改坐标），确认面板显示 **原/新体积与补差价或退差价**：私人领地扩大时优先消耗 **免费领地格** 再按 `LAND_PRICE` 补款，缩小按 `LAND_SELL_REFUND_COEFFICIENT` 退款并调整 `owner_paid_money`；**OP 改私人领地** 不扣款；**公会领地** 仅会长/管理者，扩大消耗 **公会公共贡献点**，缩小退还公共池；**公共领地** 仅 OP、不扣款。确认后更新 `lands` 边界、**重建 chunk 索引**（`land_id` 不变），传送点若超出新范围会 **自动移到新范围中心（Y 取新 min_y）**；**上架出售中** 不可重设；**子领地** 若超出新长方体范围会阻止并提示先调整子领地。文案键见 `dist/ARCCore/ZH-CN.txt` 中 `LAND_RESIZE_*`
- **全局禁用方块（v0.6.0）** - 新增 `DISABLED_BLOCKS` 配置；列表内方块对非 OP 玩家禁止放置与交互，OP 跳过检查
- **领地尺寸限制** - 可配置领地最小尺寸，防止创建过小的领地（默认长宽必须都大于5格）
- **领地信息查看功能** - 可查看当前位置的详细领地信息
- **领地边界可视化** - 用粒子效果显示领地边界范围
- **创建领地重叠提示** - 与已有领地重叠时提示与哪些领地重叠
- 领地传送点设置和管理
- 领地重命名功能
- 可配置的领地价格和最小距离
- 智能传送命令生成（自动处理包含空格的玩家名）

### 🎯 成就系统（已拆至独立插件）
- **独立插件**：`endstone_arc_achievement` / plugin id **`arc_achievement`**，仓库目录 `EndstoneMC-ARC-Achievement`，数据目录 `plugins/ARCAchievement/`
- **核心职责**：菜单入口转发（检测到插件时显示「我的成就」「成就管理」，执行 `/ach`、`/achop`）；头衔解锁与发奖仍走本核心 API
- **活动统计（v0.9.11）**：击杀 / 破坏 / 放置累计写入本服表 **`player_activity_stats`**；对外只读 API（见下文）。成就插件经 API 查询，**不得直写核心库**
- **定义文件**：`plugins/ARCAchievement/achievements.json`（首次启用时若仅有旧版 `plugins/ARCCore/achievements.json` 会自动复制）
- **说明**：未安装 `arc_achievement` 时，核心仍统计活动数据，但不显示成就入口

### 📊 玩家活动统计（v0.9.11）
- 表 **`player_activity_stats`**：`kill_total` / `kill:{entity_id}`（含 `minecraft:player`）、`break_total` / `break:{block_id}`、`place_total` / `place:{block_id}`
- 生物击杀在 `ActorDeathEvent` 记账；玩家击杀在 `PlayerDeathEvent` 记账；方块破坏/放置仅在事件**未取消**时记账
- 本服独立，不跨服同步；启动时将旧表 `player_achievement_stats` 中的 `kill_*` / `block_break_*` / `block_place_*` 一次性迁入本表后 **DROP** 旧表（`ach_unlock:*` 由成就插件迁入本地库）
- API：`api_get_player_stat` / `api_get_player_stats` / `api_get_player_kill_count` / `api_get_player_block_break_count` / `api_get_player_block_place_count`

### 📅 每日签到（v0.4.0 起，v0.4.2 / v0.6.0 增强）
- **可签到条件**：本服 `player_local_info.last_checkin_date` 与服务器本地日期（YYYY-MM-DD）不同即可在主菜单 **每日签到**（**每服独立**，不跨服共享）
- **连续签到奖励（v0.6.0）**：支持按连续签到天数发放递增金钱奖励（可配置步长）
- **前几名签到奖励（v0.6.0）**：支持配置每日前 X 名签到玩家的额外金钱与额外物品奖励
- **奖励**：配置存款 + 按权重 **不放回** 随机物品；每日抽取条数在 **`CHECKIN_REWARD_PICK_MIN`～`CHECKIN_REWARD_PICK_MAX`** 之间随机（未配置区间时沿用 `CHECKIN_REWARD_PICK_COUNT`）
- **统计与排行数据（v0.4.2）**：`total_checkin_count` 累计签到次数、`last_checkin_at`（ISO8601）记录最近一次签到时刻，用于 **当日签到先后** 与 **累计签到榜** 排序
- **全服广播（v0.4.2）**：签到成功后广播完成提示；**今日签到先后**（当日人数 ≤10 时列出全员；>10 时广播「最早前 10」与「最晚前 10」两段）；**累计签到榜前 10**；聊天中 **按行发送**，避免名次挤成一行难读
- **配置**：`CHECKIN_DAILY_MONEY`、`CHECKIN_REWARD_LIST`（JSON 数组，每项 `[物品ID, 数量, 权重]`）；**OP 面板 → 签到配置**（总览 + 存款/条数表单 + 奖励列表管理，见 OP 面板说明）
- **配置键速览（v0.6.0）**：
  - `CHECKIN_CONTINUOUS_DAYS_MONEY_INCREMENT`：连续签到金钱递增步长（连续第 N 天在基础金额上额外加 `(N-1)*步长`）
  - `CHECKIN_TOP_RANK_LIMIT`：每日前 X 名签到人数（设为 `0` 即关闭前几名奖励）
  - `CHECKIN_TOP_RANK_BONUS_MONEY_STEP`：前 X 名额外金钱步长（名次越靠前奖励越高）
  - `CHECKIN_TOP_RANK_BONUS_ITEM_COUNT`：前 X 名额外物品条数（每位前 X 名玩家额外获得的条目数）
  - `CHECKIN_REWARD_PICK_MIN` / `CHECKIN_REWARD_PICK_MAX`：每日随机抽取物品奖励条数区间
- **签到公会贡献点（v0.7.3）**：`CHECKIN_GUILD_CONTRIBUTION_POINTS`（默认 `10`）— 签到成功时，若玩家 **已加入公会**，则按 `GuildSystem.add_contribution_by_xuid` 同时增加 **私人贡献点** 与 **公会公共贡献点**；未加入公会则跳过（不报错）。设为 `0` 可关闭。可在 **OP 面板 → 签到配置 → 配置存款与随机条数** 表单最后一项编辑，或直接改 `core_setting.yml`

### 💀 击杀生物金钱奖励（v0.4.0）
- 独立配置文件 **`kill_reward.txt`**（与 `core_setting.yml` 同级目录），格式：`minecraft:creeper=10`（击杀一个苦力怕获得 10 元）
- 首次击杀某种生物且配置中无该类型时，自动追加 `类型ID=0`，不提示；仅当金额 **> 0** 时提示「击杀了 xx 获得 xx 元」
- 显示名优先通过 **`entity_display_name.txt`** 中 `entity.minecraft.xxx.name` 等键解析（`EntityDisplayNameManager.get_display_name_for_entity_type`）
- **击杀 → 公会贡献点（v0.7.5）**：`KILL_REWARD_GUILD_CONTRIB_RATIO`（默认 `0`）— 玩家在已加入公会时，每次成功扣发击杀金钱奖励后按 `floor(reward * ratio)` 额外获得公会贡献点；同步累加 **私人贡献点** 与 **公会公共贡献点**。例如 `kill_reward.txt` 配置 `minecraft:creeper=10` 且比例为 `0.5`，则击杀苦力怕在获得 10 元的同时获得 5 公会贡献点。比例 `0` 或 `floor(reward*ratio) <= 0` 或玩家未加入公会时静默跳过

### 📍 传送系统
- **功能开关（v0.8.19）** - 传送面板每项可单独开关，默认全开：`ENABLE_TELEPORT_PUBLIC_WARP`、`ENABLE_TELEPORT_HOME`、`ENABLE_RANDOM_TELEPORT`、`ENABLE_TELEPORT_DEATH_LOCATION`、`ENABLE_TELEPORT_PLAYER`、`ENABLE_TELEPORT_CROSS_SERVER`。关闭后传送系统面板不显示对应按钮（`/connecttoserver` 在跨服关闭时也不可用）。可在 **OP 面板 → 配置文件设置 → 传送** 中修改
- **私人传送点 (Home)** - 玩家可设置多个传送点
- **公共传送点 (Warp)** - 管理员可创建公共传送点
- **跨服传送（v0.6.0）** - 数据库维护跨服目标；`/connecttoserver` **无参数**时打开跨服目标 **选择面板**，有参数时按名称执行传送；控制台/命令方块执行时可通过发送者名称解析在线玩家（与下列命令解析方式一致）
- **玩家传送请求 (TPA/TPHERE)** - 玩家间传送请求；发送时用 **下拉框** 选择请求类型与目标玩家；**被请求方收到请求时自动弹出表单**（v0.4.2），并 **toast** 提示；亦可用 **`/tpa accept`** / **`/tpa deny`** 快速响应最近一条请求（v0.9.7）
- **死亡回归系统** - 玩家死亡后可传送回死亡地点；**死亡坐标在同一次服务器运行期间保持**（退出游戏不再清空；实际传送成功后仍会清除记录）
- **随机传送系统 (v0.1.12新增)** - 随机传送到指定范围内，自动附加缓降（羽落）效果（**30 秒**，v0.4.1 起；此前为 10 秒）
- **传送付费系统 (v0.1.12新增)** - 每种传送类型可独立配置收费，支持余额检查
- **跨维度传送支持** - 支持在主世界、下界、末地之间自由传送
- **智能维度处理** - 自动使用 `execute in <dimension> run tp`；原版三维度用短名（`overworld`/`nether`/`the_end`），自定义维度用完整 `namespace:id`
- 传送倒计时提示

### 💴 商店系统
- **ushop插件适配** ，如果你安装了 `ushop` ，弧光核心的主菜单中会有 "商店" 按钮
- **arc_button_shop适配** - 新增对arc_button_shop玩家按钮商店的集成支持，可通过主菜单直接访问按钮商店功能，提升玩家开店体验

### 📈 股票系统
- **up_and_down插件适配** - 新增对up_and_down股票插件的集成支持
- 在主菜单中新增"证券交易所"按钮，玩家可直接访问股票交易功能
- 提供便捷的股票系统入口，简化玩家投资操作流程

### 🏹 PvP KD 排行榜
- **arc_pvp_kd 插件适配** - 安装 `arc_pvp_kd`（弧光 PvP KD 排行榜插件，仓库 `EndstoneMC-ARC-PvP-KD-Ranking`）后，主菜单显示「PvP KD 排行榜」按钮
- 点击按钮委托执行 `/kd`，由 PvP KD 排行榜插件向该玩家单独弹出 Form 榜单（不全服广播）
- 未安装 `arc_pvp_kd` 时不显示该入口

### 📢 公告系统 (v0.1.2新增)
- 定时循环播放公告消息
- 支持多条公告轮播
- **动态占位符支持**：
  - `{date}` - 当前日期 (年-月-日)
  - `{time}` - 当前时间 (小时:分钟)
  - `{online_player_number}` - 当前在线玩家数
- 可配置公告发送间隔
- 从 `broadcast.txt` 文件读取公告内容

### 🧹 清道夫系统 (v0.1.2新增)
- 定时自动清理掉落物
- 可配置清理时间间隔
- 清理前10秒倒计时警告
- 清理过程状态提示
- 可通过配置开启/关闭

### 🎊 新人欢迎系统 (v0.1.4新增)
- **新玩家自动识别** - 基于数据库记录智能判断新玩家
- **自定义欢迎消息** - 通过 `newbie_welcome.txt` 文件设置欢迎内容
- **自动执行指令** - 通过 `newbie_commands.txt` 文件配置新人自动执行的指令
- **动态玩家名替换** - 指令中的 `{player}` 占位符自动替换为新玩家名称
- **数据库自动初始化** - 新玩家加入时自动创建基础数据和经济账户
- **初始资金设置** - 新玩家自动获得配置中设定的初始金钱
- **UTF-8 编码支持** - 完全支持中文和特殊字符
- **错误处理机制** - 文件读取失败不影响插件正常运行

### 🔐 OP状态追踪系统 (v0.1.4新增)
- **OP状态持久化** - 本服权限记在 **`player_local_info.is_op`**（**不跨服同步**，每服独立）；跨服排行排除用 **`player_basic_info.once_op`**（任意服以 OP 登录过即永久标记）
- **离线状态查询** - 即使玩家离线也能查询其本服 OP 状态
- **自动状态同步** - 玩家加入时自动检查并更新本服 OP 状态
- **金钱排行榜隐藏** - 可配置在金钱排行榜中隐藏 OP 玩家

### 🛡️ 出生点保护
- 可配置的出生点保护范围
- 防止玩家在出生点附近建筑/破坏
- 多维度出生点支持

### ⚙️ OP 管理面板（v0.4.0 整理）
- **主菜单顺序**（自上而下）：重载配置 → **配置文件设置** → **工具** → **经济管理** → 领地管理 → 传送管理 → 成就管理（需安装 `arc_achievement`）→ 签到配置 → 邀请奖励配置 → 头衔管理 → 返回
- **配置文件设置（v0.8.11）** - 按分类浏览并修改 `core_setting.yml`：开关/多选用下拉框；逗号分隔列表与签到奖励池为「条目按钮 + 增加新配置」，点进单条可删除。保存后即时写入并刷新缓存（路径/同步类项建议重启）
- **工具**：切换游戏模式、清除掉落物、记录坐标 1/2、调试模式、执行命令（`@p1`/`@p2`、留空重复上次命令）
- **经济管理**（原「金钱管理」）：**增减在线玩家存款**；**经济参数配置** 写入 `PLAYER_INIT_MONEY_NUM`、`HIDE_OP_IN_MONEY_RANKING`、`RICHEST_TITLE_NAME`（与 `core_setting.yml` 玩家经济段一致）
- **领地管理**：管理所有领地、管理脚下领地、重建领地区块映射；**公共领地** 详情内可 **重设公共领地范围**（与玩家重设流程一致，不扣款）（返回统一回到领地管理子菜单）
- **传送管理**：**管理公共传送点**（创建/删除 Warp）；**传送参数配置**（`MAX_PLAYER_HOME_NUM`、随机传送中心/半径、各类传送费用等）；各项传送开关见 **配置文件设置 → 传送**
- 邀请奖励配置、**签到配置**（v0.4.2：总览展示当前存款/随机条数区间/奖励条目数；**配置存款与随机条数** 弹窗表单，v0.7.3 起含 **每日签到公会贡献点**；**配置物品奖励列表** 支持按条目进入编辑/删除与新增）、头衔管理、成就管理
- **重载配置** - 重载 `core_setting`、广播、语言、**entity_display_name.txt**、**kill_reward.txt** 等
- **调试模式**（v0.3.0）：开启后，在方块破坏/放置、方块交互、生物攻击、生物交互时向该 OP 发送聊天调试消息（事件类型、目标、维度、位置）

### 🏷️ 头衔系统（v0.3.0，表结构 v0.7.1）
- **聊天头衔展示** - 远古 QQ 风格：首行 `[头衔]玩家名(年.月.日-时:分)：`，下一行消息内容；`[头衔]玩家名` 加粗并按稀有度上色（MC 格式码 §l、§r、§f/§9/§d/§6/§c），「玩家」前缀可在语言文件中配置（如英文 `Player-`）
- **数据（v0.7.1 / v0.9.13）** - 玩家解锁时间仅存 **`player_title_unlock_time`**（`xuid`、`title`、`unlocked_at`）；废弃表 **`player_title_extra`** 在启动时自动 DROP
- **头衔属性** - 每个头衔支持：**稀有度**（普通/稀有/史诗/传奇/神话，对应白/蓝/紫/橙/红）、**头衔介绍**、**解锁时间**（解锁时记录，默认头衔在首次进服或首次获得时记录；已进服但尚未有默认头衔的玩家在下一次进服时补发并记录时间）、**解锁奖励**（金钱 + 物品列表「物品ID 数量」）
- **默认头衔** - 配置 `DEFAULT_TITLE`（逗号分隔），**进服时**为每位玩家写入解锁记录（与成就无关）；默认稀有度为普通，介绍与奖励为空，OP 可在头衔属性管理中修改。
- **OP 专属头衔** - 配置 `OP_TITLE`（单个），仅 OP 拥有；非 OP 进服时若正佩戴该头衔则自动解除
- **头衔管理（玩家）** - 主菜单「我的信息」→「头衔管理」：选择佩戴/不佩戴（同入口下另有「修改密码」，见上文 **玩家管理系统**）
- **OP 头衔管理** - OP 面板→「头衔管理」：**头衔属性管理**（编辑各头衔的稀有度、介绍、解锁奖励）、**创建新头衔**（名称 + 稀有度 + 介绍 + 奖励）、**给所有玩家添加头衔**（选择已有头衔，为当前数据库内所有玩家解锁，新人不会自动获得）、**给玩家单独添加头衔**（先输入玩家名，再选择要添加的头衔）；解锁时若玩家在线则发放该头衔的解锁奖励（金钱与物品）
- **API** - 见下文「API 接口」：`api_unlock_title`、`api_set_title_definition`、`api_give_player_items` 等（供 `arc_achievement` 等插件调用）
- **解锁头衔自动佩戴（v0.4.0）** - 通过 `api_unlock_title` 等途径解锁头衔时，若当前未佩戴任何头衔，则自动佩戴新解锁的头衔

### 🏰 公会系统（v0.7.0；v0.7.2 拓展规模与贡献点；v0.7.3 浏览与入会审批）
- **模块**：`GuildSystem.py`；表 **`guilds`**、**`guild_members`**（每名玩家最多归属一个公会）；**`guild_invites`** 表仍保留，供历史数据或旧版待处理邀请读取，**当前版本的在线邀请不再写入该表**
- **入口**：主菜单 **公会**，或 **`/arc guild`**（菜单内 **创建公会** 等敏感步骤需按上文完成密码验证）
- **创建公会**：消耗可配置 **`GUILD_CREATE_COST`**（默认 `100000`）；公会名唯一、可选简介；创建者即为 **会长（owner）**
- **职级**：**会长**、**管理者（manager）**、**成员（member）** — 会长可踢管理者与成员、变更职级、解散公会；管理者可邀请与踢出普通成员；成员可退出
- **在线邀请（v0.7.0）**：**仅邀请当前在线且未加入任何公会的玩家** — 邀请方在列表中点选玩家名，被邀请方 **立即弹出接受/拒绝表单**，确认后直接写入成员表，**无需入库待处理邀请**
- **公会规模（v0.7.2）**：每个公会有 **小型 / 中型 / 大型** 三档规模等级，新建公会默认 **小型**；各档成员人数上限可在 `core_setting.yml` 中通过 **`GUILD_SIZE_SMALL_MAX`**、**`GUILD_SIZE_MEDIUM_MAX`**、**`GUILD_SIZE_LARGE_MAX`** 配置（默认 10 / 20 / 40）。邀请与接受邀请均会校验当前规模容量，超过上限直接报 **`GUILD_FULL`** 错误
- **公会规模升级（v0.7.2）**：会长 / 管理者可在 **我的公会 → 升级公会规模** 中花费 **公会公共贡献点** 升级；升级消耗在 `core_setting.yml` 中通过 **`GUILD_UPGRADE_TO_MEDIUM_COST`**（默认 `10000`）、**`GUILD_UPGRADE_TO_LARGE_COST`**（默认 `100000`）配置；仅可由低向高升级（small→medium / large、medium→large）；OP 在 **OP 面板 → 公会管理** 中可绕开贡献点直接升级或降级规模（降级时若当前人数已超过目标上限会被拒绝，且不会退还任何贡献点）；规模变更后会自动刷新该公会全体在线成员的头顶名（颜色随之变化）
- **公会改名（v0.7.2）**：会长可在 **我的公会 → 公会改名** 中输入新公会名（与创建公会一致：自动剥离 §X 颜色码、限长 32、去首尾空白）；新名 **不能与当前公会同名（去色后比较）**，**不能与其它公会重名**；改名费用由 `core_setting.yml` 中 **`GUILD_RENAME_COST`** 配置（默认 `0` 即免费），费用 > 0 时改名失败会自动回滚扣款；改名成功后立即刷新全体在线成员的头顶名
- **公会名颜色与防注入（v0.7.2）**：聊天、玩家头顶 `name_tag`、`get_player_name_by_xuid(..., True)` 等展示名中的 `[公会名]` 前缀会按公会规模上色 — **小型 §h / 中型 §s / 大型 §p**（可在语言文件中通过 `GUILD_SIZE_TIER_COLOR_SMALL / MEDIUM / LARGE` 覆盖）。`GuildSystem` 在创建公会、改名、按名查公会、读取公会、列出待处理邀请等所有出口处都会通过正则 **统一剥离 §X 格式码**（含残留的孤立 §），即便玩家在公会名中尝试粘贴 MC 颜色码也会被消除；旧库内若残留过格式码也会在读取时自动清洗。**创建 / 改名表单提交后**：若玩家输入的公会名包含 §X，会先静默剥离再入库，并通过 `GUILD_NAME_COLOR_STRIPPED_HINT` 向玩家发出「颜色码已被自动移除」的提示；若输入仅由颜色码组成，会按 `GUILD_INVALID_NAME` 拒绝并附带同一提示
- **公会贡献点（v0.7.2）**：
  - **私人公会贡献点**：保存在 `guild_members.contribution`；玩家通过 **API** 或 **每日签到（v0.7.3，见签到章节）** 等途径累加；**退出 / 被踢 / 公会解散** 时该玩家私人贡献点随成员行删除而 **清零**
  - **公共公会贡献点**：保存在 `guilds.total_contribution`；玩家每次获得私人贡献点时同步累加到所在公会公共值；**成员退出/被踢时公共值不会减少**（仅在公会解散时随公会行一并销毁）；当前 UI 中可被「升级公会规模」消耗
  - 玩家加入新公会时私人贡献点从 0 开始；新公会的公共贡献点也从 0 开始
  - **对外插件接口（查询 + 发放）**：按玩家名可继续用 `api_get_player_guild_info`、`api_add_guild_contribution`（私人与公共同时 +points）。按公会 id 请用 **`api_get_player_guild_id`**、**`api_get_guild_info`**、**`api_get_guild_total_contribution`** / **`api_change_guild_total_contribution`**、**`api_get_member_guild_contribution`** / **`api_change_member_guild_contribution`**（私人点单独增减）、**`api_list_guild_members`**。详见下方「公会系统 API」
  - **底层消费接口**：`GuildSystem.consume_guild_contribution(guild_id, points)`（仅扣减公共值，不影响私人值），供领地等系统消耗公共贡献点
- **全部公会浏览与入会（v0.7.3）**：主菜单 **公会 → 查看全部公会** — 列表按 **规模等级降序、同规模按公共贡献点降序**；支持 **按名称搜索**、分页；点选公会仅 **预览**（名称、简介、规模、人数/上限、公共贡献、入会说明）；**无公会** 玩家可 **申请加入 / 加入**（取决于 **入会审核**）；**已是本会成员** 仅提供 **我的公会** 跳转。会长 / 管理者在 **我的公会 → 入会审核设置** 中开关审核，在 **入会申请** 中处理待审。相关数据表：`guild_join_requests`、`guilds.join_requires_approval`
- **跨服同步**：远程客户端模式下通过 **`SYNC_CLIENT_SYNC_GUILD`** 控制是否同步公会数据
- **展示名统一**：聊天、玩家头顶 **`name_tag`**、`get_player_name_by_xuid(..., return_with_title=True)` 等为 **`[公会前缀][头衔]玩家名`**：有公会时前缀为带 MC 颜色码的 **`[公会名]`**（与「普通」稀有度头衔同色）；无公会时为 **`§f[无公会]§r`**（白色），再接头衔段与游戏名
- **数据库平滑升级**：插件加载时若旧库 `guilds` / `guild_members` 缺少 `size_tier` / `total_contribution` / `contribution` 列，会自动 `ALTER TABLE` 补齐（默认值：`size_tier='small'`、其余为 `0`），无需手动迁移

### 🔄 跨服数据同步（v0.8 / v0.9.42）
- **用途**：在多服架构下，让玩家账号级数据、经济、头衔、公会等在多个 ARC Core 实例之间保持一致
- **拓扑（纯网络）**：
  - **主服 / 同步中心**：**`ENABLE_SYNC_SERVER=True`** + **`ENABLE_SYNC_CLIENT=False`**，监听 **`SYNC_SERVER_PORT`**
  - **子服**：**`ENABLE_SYNC_CLIENT=True`**，连接同步中心（**`SYNC_SERVER_IP`** + **`SYNC_CLIENT_PORT`**），首次连接全量拉取、之后接收 **`PUSH_NOTIFY`** 推送；本地写库经 **`sync_outbox`** 可靠上行（断线不丢，重连后重放，协议 v3 带 seq ack）
- **分项同步开关（仅远程客户端）**：**`SYNC_CLIENT_SYNC_PLAYER`**、**`SYNC_CLIENT_SYNC_ECONOMY`**、**`SYNC_CLIENT_SYNC_TITLE`**、**`SYNC_CLIENT_SYNC_GUILD`** 可单独开关；关闭的类别不会拉取全量数据，也不会接收推送
- **条件头衔权威服（v0.9.14）**：**`CONDITIONAL_TITLE_AUTHORITY`** 显式指定是否由本机计算首富等条件头衔；留空则自动推断（远程客户端不计算，避免双算）
- **玩法配置以同步中心为准（v0.8.5）**：开启对应分项后，从服连接同步中心时会拉取并覆盖该类别的玩法配置（写入本机 `core_setting.yml`）。例如同步经济则统一 **初始金钱 / 签到存款 / 传送与圈地价格**；同步公会则统一 **创建费用 / 规模上限 / 升级贡献点**。主服 OP 改配置或重载后会推送给已连接从服。本机路径、端口、`SYNC_CLIENT_*`、清道夫、出生点保护等仍各服独立
- **模块**：`sync_protocol.py`、`sync_server.py`、`sync_client.py`、`sync_config.py`、`sync_outbox.py`
- **可同步数据表**：跨服玩家账号信息（`player_basic_info`，含 **`once_op` 粘性列**：任意服以 OP 登录过即永久排除排行）、经济（`player_economy`）、头衔（`title_definitions` / `player_title_unlock_time` / `player_title_equipped`）、公会（`guilds` / `guild_members` / `guild_invites`）
- **本服本地表（不同步）**：**`player_local_info`** — 本服 `is_op`、剩余免费领地格、**签到**（每服独立）。始终写在本服 **`DATABASE_PATH`**；跨服排行 OP 排除读 **`player_basic_info.once_op`**（不再用本服 `is_op` 镜像）
- **OP 面板**：点「跨服同步」即发起全面对账并显示运行状态；每次连上同步中心也会自动拉全量并上行本地已启用表
- **QQ 群消息**：跨服 QQ 互通由 **AstrBot 弧光 EndStone 消息中枢** + **endstone-arc-qq-sync-astrbot** 负责；ARCCore **不再**经 SyncServer 做 QQ 事件中继。死亡播报调用本机 QQ Sync 的 `api_send_event("death", …)`；成就等可用 `custom`
- **群聊死亡播报模式（v0.9.12）**：`QQ_DEATH_BROADCAST_MODE` = `off`（不播报）/ `pvp`（仅 PvP）/ `all`（全部，默认）；仅影响群聊，游戏内死亡播报仍始终发送
- **启动迁移**：签到迁入本服表；时长 / 进服次数保留在跨服 `player_basic_info`

### ⏱️ 游戏时长统计（跨服）
- 表：**`player_basic_info`**（`total_playtime` 秒、`session_count`、`last_join_time` / `last_quit_time`）
- 进服 / 离服自动记账；关服时结算在线会话；随 SyncServer / 共享库跨服累计
- 对外 API：**`api_get_player_playtime(raw_player_name="", xuid="")`**，供 QQ Sync `/who` 与进离服播报查询

### 🔌 插件 API 系统
- **统一玩家标识** - 多数接口同时支持游戏名与 **xuid**（填一个即可，xuid 优先）；旧的只传玩家名的调用仍可用
- **经济系统 API** - 查询/增减金钱、财富排名；变动接口可返回结构化结果
- **头衔系统 API** - 解锁/查询/列出定义、按 xuid 解锁、发放物品等（供成就等弧光系列插件调用）
- **领地系统 API** - 坐标生效领地、玩家/公会领地列表、静默权限检查
- **传送系统 API** - 传送在线玩家到坐标 / Home / 公共传送点，并列出 Home 与 Warp
- **公会系统 API** - 按公会 id / 玩家 xuid 查询与单独增减公共、私人贡献点
- **玩家解析 API** - `api_get_player_xuid_by_name` / `api_get_player_name_by_xuid`
- **游戏时长 API** - `plugin.api_get_player_playtime(...)` 查询跨服累计时长与进服次数
- **新手引导 API** - `plugin.api_get_newbie_guide_text()` 返回 `newbie_welcome.txt` 全文（供大模型聊天等插件使用）
- **线程安全设计** - 支持多插件并发调用
- **错误处理机制** - 自动处理异常情况
- **详细文档支持** - 提供完整的使用示例
- **调用入口**：`server.get_plugin("arc_core")`（与 pyproject entry-point 一致）

## 命令列表

| 命令 | 描述 | 权限 | 用法 |
|------|------|------|------|
| `/arc` | 打开 ARC Core 主菜单 | 默认 | `/arc` |
| `/arc op` | 直接打开 OP 面板（仅 OP） | OP | `/arc op` |
| `/arc land` | 直接打开领地系统菜单 | 默认 | `/arc land` |
| `/arc tp` | 直接打开传送系统菜单 | 默认 | `/arc tp` |
| `/arc bank` | 直接打开银行菜单 | 默认 | `/arc bank` |
| `/arc guild` | 直接打开公会菜单 | 默认 | `/arc guild` |
| `/pos1` | 记录当前坐标为坐标 1（OP 快捷，对应 OP 面板记录坐标 1） | OP | `/pos1` |
| `/pos2` | 记录当前坐标为坐标 2 并打开 OP 面板（OP 快捷） | OP | `/pos2` |
| `/updatespawnpos` | 更新当前维度的出生点位置 | OP | `/updatespawnpos` |
| `/suicide` | 自杀命令 | 默认 | `/suicide` |
| `/spawn` | 传送到出生点 | 默认 | `/spawn` |
| `/land pos1` | 领地选点 1（记录当前站立方块坐标） | 默认 | `/land pos1` |
| `/land pos2` | 领地选点 2 并打开待购面板 | 默认 | `/land pos2` |
| `/land buy` | 打开待购领地购买面板 | 默认 | `/land buy` |
| `/tpa accept` | 接受最近一条传送请求 | 默认 | `/tpa accept` |
| `/tpa deny` | 拒绝最近一条传送请求 | 默认 | `/tpa deny` |
| `/connecttoserver` | 无参数时打开跨服目标列表；有参数时按名称传送 | 默认 | `/connecttoserver` 或 `/connecttoserver <名称>` |

## 📂 文件结构

插件会在 `plugins/ARCCore/` 目录下创建以下文件：

- `core_setting.yml` - 主要配置文件
- `broadcast.txt` - 公告消息文件
- `{语言代码}.txt` - 语言文件 (如 ZH-CN.txt)
- `entity_display_name.txt` - 生物显示名翻译（v0.3.1+，死亡播报等）
- `kill_reward.txt` - 击杀生物金钱奖励（v0.4.0，每行 `类型ID=金额`）
- SQLite 数据库文件

成就定义与成就语言文件见独立插件 `plugins/ARCAchievement/`（`endstone_arc_achievement`）。

## ⚙️ 配置文件

### core_setting.yml - 主要配置选项

```yaml
# 基础设置
DEFAULT_LANGUAGE_CODE=ZH-CN          # 默认语言
DATABASE_PATH=ARCCore.db             # 数据库文件路径
PLAYER_INIT_MONEY_NUM=10000          # 玩家初始金钱

# 出生点保护
IF_PROTECT_SPAWN=True                # 是否保护出生点
SPAWN_PROTECT_RANGE=8                # 出生点保护范围

# 领地系统
ENABLE_LAND_SYSTEM=True              # 领地系统总开关；False 时不启位置追踪线程（无进出领地提示）
ALLOW_LAND_CLAIM=True                # 是否允许圈地（本服独立，不跨服同步；False 时无法新建领地，管理/调整范围仍可用）
MIN_LAND_DISTANCE=1                  # 领地最小距离
LAND_PRICE=100                       # 领地价格 (每格)
LAND_SELL_REFUND_COEFFICIENT=0.9     # 领地出售退款系数
LAND_MIN_SIZE=5                      # 领地最小尺寸 (长宽必须都大于此值)
LAND_SALE_VAT_RATE=0.1               # 私人领地上架成交增值税：对 (成交价−过户前owner_paid_money) 的溢价按比例征税，从卖家实收扣除；0=关闭

# 传送系统
MAX_PLAYER_HOME_NUM=5                # 玩家最大家园数量

# 传送系统面板各项开关（v0.8.19，默认全开；关闭后传送面板不显示对应按钮）
ENABLE_TELEPORT_PUBLIC_WARP=True     # 公共传送点
ENABLE_TELEPORT_HOME=True            # 私人传送点
ENABLE_RANDOM_TELEPORT=True          # 随机传送
ENABLE_TELEPORT_DEATH_LOCATION=True  # 死亡点传送
ENABLE_TELEPORT_PLAYER=True          # 玩家互传（TPA/TPHERE）
ENABLE_TELEPORT_CROSS_SERVER=True    # 跨服传送

# 随机传送范围 (v0.1.12新增)
RANDOM_TELEPORT_CENTER_X=0           # 随机传送中心点X坐标
RANDOM_TELEPORT_CENTER_Z=0           # 随机传送中心点Z坐标
RANDOM_TELEPORT_RADIUS=5000          # 随机传送半径 (格)

# 传送收费配置 (v0.1.12新增，0表示免费)
TELEPORT_COST_PUBLIC_WARP=0          # 公共传送点费用
TELEPORT_COST_HOME=0                 # 私人传送点费用
TELEPORT_COST_LAND=0                 # 领地传送费用
TELEPORT_COST_DEATH_LOCATION=0       # 死亡地点传送费用
TELEPORT_COST_RANDOM=100             # 随机传送费用
TELEPORT_COST_PLAYER=50              # 玩家互传费用 (TPA/TPHERE)

# 公告系统
BROADCAST_INTERVAL=180               # 公告发送间隔 (秒)

# 清道夫系统
ENABLE_CLEANER=True                  # 是否启用清道夫
CLEANER_INTERVAL=600                 # 清理间隔 (秒)

# 全局爆炸拦截（默认开启）：True=禁止破坏方块（清空 block_list，尽量保留伤害；失败则取消事件）；False=仅按领地 allow_explosion 保护
BLOCK_ALL_EXPLOSIONS=True

# 天眼系统（Sky Eye，v0.8.8）：独立 SQLite plugins/ARCCore/sky_eye/skyeye.db
ENABLE_SKY_EYE=False                 # 是否启用天眼日志
SKY_EYE_MAX_RETENTION_DAYS=7         # 按自然日保留天数，0=不自动删旧记录

# 新人欢迎系统和OP设置（部分项也可在 OP 面板「经济管理」中修改）
HIDE_OP_IN_MONEY_RANKING=True        # 金钱排行榜是否隐藏OP玩家

# 首富头衔（v0.4.0，亦可 OP 经济管理）
RICHEST_TITLE_NAME=首富

# 领地系统
DEFAULT_FREE_LAND_BLOCKS=100         # 新玩家默认免费领地格子数

# 公共领地白名单保护生物 (v0.2.1，逗号分隔)
PUBLIC_LAND_PROTECTED_ENTITIES=minecraft:villager,minecraft:iron_golem,minecraft:snow_golem

# 公共领地拦截生物生成 (v0.8.18 / v0.8.21)
# 模式：whitelist=白名单（名单上的不拦截，空名单则拦截全部）；blacklist=黑名单（只拦截名单上的）
# 各公共领地在 OP 公共领地设置中单独开关拦截；旧配置 False/off 视为 whitelist
PUBLIC_LAND_BLOCK_ACTOR_SPAWN_MODE=whitelist
# 作用范围：public=仅公共领地且看各领地开关（默认）；all=任意领地均按名单拦截（不看各领地开关）
LAND_BLOCK_ACTOR_SPAWN_SCOPE=public
# 拦截名单（逗号分隔实体 ID）：白名单=名单上的不拦截；黑名单=只拦截名单上的
PUBLIC_LAND_BLOCK_ACTOR_SPAWN_LIST=

# 头衔系统 (v0.3.0)：逗号分隔为默认头衔；OP_TITLE 仅一个，仅 OP 拥有。对应头衔的稀有度、介绍、解锁奖励可在 OP 面板→头衔管理→头衔属性管理中编辑，也可创建新头衔
DEFAULT_TITLE=创始玩家, 核心成员, ARC Player
OP_TITLE=管理员

# 公会系统 (v0.7.0)
GUILD_CREATE_COST=100000
# 公会规模 (v0.7.2)：每个公会有 small / medium / large 三档；下列三个值为各档成员人数上限（含会长）
GUILD_SIZE_SMALL_MAX=10
GUILD_SIZE_MEDIUM_MAX=20
GUILD_SIZE_LARGE_MAX=40
# 公会规模升级所消耗的公会公共贡献点（会长 / 管理者可在「我的公会 → 升级公会规模」中花费）
GUILD_UPGRADE_TO_MEDIUM_COST=10000
GUILD_UPGRADE_TO_LARGE_COST=100000
# 会长改名公会需支付的金钱（0 表示免费）
GUILD_RENAME_COST=0

# 跨服数据同步（v0.9.42+）
# 主服 ENABLE_SYNC_SERVER=True；子服 ENABLE_SYNC_CLIENT=True 连接同步中心

# ----- 同步中心（可选） -----
ENABLE_SYNC_SERVER=False
SYNC_SERVER_PORT=19999
SYNC_SERVER_AUTH_KEY=

# ----- 远程客户端（子服） -----
ENABLE_SYNC_CLIENT=False
SYNC_SERVER_IP=127.0.0.1
SYNC_CLIENT_PORT=19999
SYNC_CLIENT_SERVER_ID=server_001
SYNC_CLIENT_SERVER_NAME=服务器01
SYNC_CLIENT_AUTH_KEY=
SYNC_CLIENT_SYNC_PLAYER=True
SYNC_CLIENT_SYNC_ECONOMY=True
SYNC_CLIENT_SYNC_TITLE=True
SYNC_CLIENT_SYNC_GUILD=True
# 开启某类别时，该类别相关玩法配置也以同步中心为准（初始金钱、公会升级消耗等）
```

### broadcast.txt - 公告消息文件

每行一条公告，支持占位符：

```txt
欢迎来到ARC弧光基岩服务器！你可以在聊天框发送/arc命令打开服务器操作菜单
请遵守服务器规则，文明游戏，共建和谐游戏环境！
现在是北京时间{date} {time}，请注意休息，爱护眼睛你我做起。
当前服务器在线人数{online_player_number}，求生者们请互帮互助
```

### newbie_welcome.txt - 新人欢迎消息文件 (v0.1.4新增)

新玩家第一次加入服务器时显示的欢迎消息：

```txt
欢迎来到ARC弧光大陆服务器！这里是一个恐怖+种田+模拟生活的多模组服务器，拥有丰富的玩法和特色系统！在聊天框输入/arc命令即可打开服务器操作菜单，进行购物、传送、领地管理等操作。

```

### newbie_commands.txt - 新人自动执行指令文件 (v0.1.4新增)

新玩家第一次加入服务器时自动执行的指令，每行一个指令：

```txt
# 新人指令文件
# 每行一个指令，{player} 会被替换为玩家名称
# 示例：
gamemode 0 {player}
# clear {player}
give {player} minecraft:bread 5
give {player} krep:m1911
give {player} krep:acp45 42
```

#### 新人指令文件说明
- **注释支持**: 以 `#` 开头的行为注释，不会执行
- **占位符替换**: `{player}` 会自动替换为新玩家的名称
- **指令格式**: 使用标准的Minecraft指令格式，无需添加 `/` 前缀
- **错误处理**: 单个指令执行失败不会影响其他指令

### 支持的占位符

| 占位符 | 描述 | 示例输出 |
|--------|------|----------|
| `{date}` | 当前日期 | `2024-01-15` |
| `{time}` | 当前时间 | `14:30` |
| `{online_player_number}` | 在线玩家数 | `5` |
| `{player}` | 玩家名称 (仅新人指令文件) | `PlayerName` |

## 安装说明

1. 确保您的服务器运行 EndStone 框架
2. 将插件文件放入服务器的 `plugins` 目录
3. 重启服务器
4. 插件会自动创建必要的配置文件和数据库

## 依赖要求

- EndStone 框架 (API 版本 0.7+)
- Python 3.x
- SQLite3 (通常内置于 Python)

## 🎮 使用指南

### 快速开始
1. 玩家进入服务器后，约 **1 秒** 内会 **自动弹出主菜单一次**（可直接关闭）；也可随时用 **`/arc`** 手动打开。
2. **首次**进行转账、圈地购地、创建公会等 **敏感操作** 时，若尚未设置账户密码，会引导 **设密（两次确认）**；已设密则弹出 **密码验证**，本会话内验证通过一次后，同类验证在一段时间内不必重复输入（退出游戏后失效）。
3. 可在主菜单使用银行、领地、传送、公会等功能；具体步骤中凡涉及资金安全或领地变更的环节仍会按需验证密码（见「玩家管理系统」）。

### 功能操作指南
- **银行系统**: 在主菜单点击"银行"进行转账、查看余额等
  - **转账操作**: 使用全新的两步式转账流程，先从在线玩家列表中选择目标玩家，再输入转账金额
- **领地系统**: 
  - 推荐：主菜单 **领地 → 创建新领地**，按提示交互四个方块；或使用 **`/land pos1` / `/land pos2`** 在对角两点定范围，再用 **`/land buy`** 打开购买面板
  - 领地长宽必须都大于配置的最小尺寸（默认 5 格）；**`/pos1` `/pos2` 为 OP 记录坐标指令，与圈地无关**
  - 新玩家享有免费领地格子，购买时会自动使用免费格子抵扣费用
  - 在领地详情中可设置爆炸保护、方块互动开放、展示框权限等高级选项；**重设领地范围** 与新建圈地流程一致，确认前可预览粒子、改坐标
  - 支持将领地权限授权给其他玩家或完全移交领地
- **传送系统**: 在主菜单的"传送系统"中管理传送点和发送传送请求
- **公告查看**: 定时播放的公告会自动显示当前时间和在线人数
- **新人欢迎系统**: 
  - 编辑 `newbie_welcome.txt` 自定义新玩家欢迎消息
  - 编辑 `newbie_commands.txt` 配置新玩家自动执行的指令
  - 使用 `{player}` 占位符在指令中引用玩家名称
  - 新玩家首次加入时自动获得初始资金和执行欢迎流程

## 🗃️ 数据存储

插件使用 SQLite 数据库存储以下数据：
- **玩家信息**: 用户名、XUID、密码哈希、OP状态、剩余免费领地格子数、邀请人(inviter_xuid)、待领取邀请奖励次数、注册时间
- **经济数据**: 玩家余额、交易记录
- **领地信息**: 领地坐标、拥有者、传送点、共享用户、爆炸保护设置、方块互动开放设置、生物保护设置、展示框权限设置
- **传送点**: 私人传送点、公共传送点坐标信息
- **玩家活动统计（v0.9.11）**: **`player_activity_stats`** — 击杀 / 破坏 / 放置累计（只读 API）；成就解锁标记由成就插件本地库维护
- **成就定义**: JSON 在 **`plugins/ARCAchievement/achievements.json`**（独立插件）
- **服务器配置**: 出生点坐标、系统设置
- **天眼审计（v0.8.8）**：独立 SQLite **`plugins/ARCCore/sky_eye/skyeye.db`**，按 `SKY_EYE_MAX_RETENTION_DAYS` 滚动删除；见「天眼系统」

### 🆕 数据库自动升级系统 (v0.1.4新增)
- **智能检测**: 自动检测数据库版本并执行必要的升级
- **字段添加**: 为旧数据库自动添加新字段（如is_op字段）
- **向后兼容**: 完全兼容旧版本数据，无需手动迁移
- **安全升级**: 升级过程包含完整的错误处理机制
- **XUID主键系统** (v0.1.8 引入): 使用 XUID 作为玩家主键（v0.2.3 起不再支持 UUID→XUID 自动迁移）

## 🛠️ 开发信息

### 项目结构
```
EndStone-ARC-CORE/
├── src/endstone_arc_core/
│   ├── __init__.py              # 插件初始化
│   ├── arc_core_plugin.py       # 主插件类
│   ├── setting_catalog.py       # OP 配置文件设置目录（v0.8.11）
│   ├── op_settings_ui.py        # OP 配置文件设置 UI（v0.8.11）
│   ├── sky_eye_log.py           # 天眼独立 SQLite 与滚动清理（v0.8.8）
│   ├── SidebarSystem.py         # 侧边栏总控（v0.9.0）
│   ├── TitleSystem.py           # 头衔系统
│   ├── LandSystem.py            # 领地系统
│   ├── KillRewardConfig.py      # 击杀奖励配置（v0.4.0+）
│   ├── EntityDisplayNameManager.py
│   ├── TitleSystem.py
│   ├── GuildSystem.py           # 公会系统（v0.7.0+）
│   ├── sync_protocol.py         # 跨服同步协议（v0.8+）
│   ├── sync_server.py           # 跨服同步后端服务端（v0.8+）
│   ├── sync_client.py           # 跨服同步远程客户端（v0.8+）
│   ├── sync_config.py           # 跨服同步模式与分项开关（v0.8+）
│   ├── DatabaseManager.py       # 数据库管理器
│   ├── LanguageManager.py       # 语言管理器
│   └── SettingManager.py        # 设置管理器
├── dist/ARCCore/
│   ├── core_setting.yml         # 配置文件
│   ├── broadcast.txt            # 公告文件
│   ├── entity_display_name.txt
│   ├── kill_reward.txt
│   ├── newbie_welcome.txt       # 新人欢迎消息文件
│   ├── newbie_commands.txt      # 新人自动执行指令文件
│   └── ZH-CN.txt               # 中文语言包
└── pyproject.toml              # 项目配置
```

### 核心技术特性
- **线程安全**: 数据库操作完全线程安全
- **多线程架构**: 位置检测系统使用独立线程，提升60%响应速度
- **事件驱动**: 基于 EndStone 事件系统
- **定时任务**: 使用 Scheduler 实现定时功能
- **模块化设计**: 各功能模块独立，易于维护
- **动态配置**: 支持运行时配置重载
- **精确坐标计算**: 使用 math.floor() 确保负坐标位置计算准确
- **XUID主键系统**: 全面使用XUID作为玩家标识，提升数据一致性和查询性能
- **数据库结构升级**: 支持表结构自动升级（自 v0.2.3 起不再提供 UUID→XUID 迁移）
- **统一接口设计**: API和内部功能基于同一套底层接口，提升代码复用性和维护性
- **坐标处理统一**: 所有坐标计算统一使用math.floor()，确保负坐标处理正确
- **可视化领地系统**: 支持粒子效果显示领地边界，提供直观的领地范围展示

### API 兼容性
- **EndStone API**: 0.11+
- **Python**: 3.13+

## 📈 性能特性

- **高效的区块索引**: 领地系统使用区块映射，快速定位
- **内存优化**: 合理的缓存策略，减少数据库查询
- **异步处理**: 耗时操作使用定时任务处理
- **资源清理**: 自动清理过期的传送请求和临时数据

## 🔒 安全特性

- **密码保护**：玩家密码使用 SHA-256 哈希存储；**敏感操作**（如转账、领地与部分公会管理）在 **会话内** 验证一次密码即可，兼顾安全与操作流畅度。
- **权限系统**: 基于 EndStone 权限系统
- **输入验证**: 所有用户输入都经过严格验证
- **SQL 注入防护**: 使用参数化查询

## 🔌 API 接口

其它 EndStone 插件通过 **`server.get_plugin("arc_core")`** 获取核心实例后调用下列方法。多数接口同时支持 **游戏名** 与 **xuid**（填一个即可，**xuid 优先**）；只传游戏名的旧写法仍然有效。接口线程安全。圈地 / 转账 / 建会等敏感写操作仍走游戏内密码验证，不对外提供。

插件 id：`arc_core`（与 pyproject entry-point 一致）。成就插件 id 为 `arc_achievement`，详见上文「成就系统」。

### API 引用示例

以查询指定玩家金钱为例：

```python
from endstone.plugin import Plugin

class MyPlugin(Plugin):
    def on_enable(self):
        self.arc = self.server.get_plugin("arc_core")
        if self.arc is None:
            self.logger.error("未找到 arc_core，请先安装 EndStone ARC Core")
            return

    def check_balance(self, player):
        # 推荐：用 xuid（改名不影响）
        money = self.arc.api_get_player_money(xuid=player.xuid)
        # 亦可：只传游戏名
        # money = self.arc.api_get_player_money(player.name)
        self.logger.info(f"{player.name} 当前金钱: {money}")
```

### API 一览表

#### 经济

| 函数 | 参数 | 返回值 |
|------|------|--------|
| `api_get_player_money` | `player_name=""`，`xuid=""` | `float`：余额；找不到为 `0.0` |
| `api_get_player_stat` | `stat_key`，`player_name=""`，`xuid=""` | `int`：单项活动统计（如 `kill_total`、`kill:minecraft:player`） |
| `api_get_player_stats` | `prefix=""`，`player_name=""`，`xuid=""` | `dict[str,int]`：按前缀筛选；空前缀返回全部 |
| `api_get_player_kill_count` | `entity_id="*"`，`player_name=""`，`xuid=""` | `int`：`*` 为总击杀，否则指定生物 |
| `api_get_player_block_break_count` | `block_id="*"`，`player_name=""`，`xuid=""` | `int`：破坏方块累计 |
| `api_get_player_block_place_count` | `block_id="*"`，`player_name=""`，`xuid=""` | `int`：放置方块累计 |
| `api_change_player_money` | `player_name=""`，`money_to_change=0`，`xuid=""`，`notify=True` | `bool`：是否成功。正加负减；`notify=False` 不发余额提示 |
| `api_adjust_player_money` | `delta`，`player_name=""`，`xuid=""`，`notify=True` | `dict`：`ok`，`error`，`xuid`，`money`（变动后），`delta`。错误码：`PLAYER_NOT_FOUND` / `MONEY_INVALID_AMOUNT` / `MONEY_DB_ERROR` |
| `api_get_player_money_rank` | `player_name=""`，`xuid=""` | `int`：财富排名（从 1 起）；找不到为 `0` |
| `api_get_all_money_data` | 无 | `dict`：`{玩家名: 金钱}` |
| `api_get_richest_player_money_data` | 无 | `list`：`[玩家名, 金钱]`；无数据为 `["", 0]` |
| `api_get_poorest_player_money_data` | 无 | `list`：`[玩家名, 金钱]`；无数据为 `["", 0]` |

#### 侧边栏（v0.9.0）

其它插件通过 `get_plugin("arc_core")` 注册页面并推送键值。行模板里的 `{key}` 按 **玩家私有值 → 页面全局值 → 核心内置变量** 解析；缺失键且 `hide_line_if_missing=True` 时整行隐藏。

内置变量：`{time}` `{date}` `{player}` `{money}` `{hp}` `{max_hp}` `{food}` `{tps}` `{mspt}` `{online}` `{max_players}` `{ping}` `{mc_time}` `{title}` `{guild}` `{page}` `{page_total}`。

```python
arc = self.server.get_plugin("arc_core")
arc.api_sidebar_register_page(
    "ars_health",
    "§a健康状态",
    ["§b口渴 §f{thirst}", "§c感染 §f{infection}%", "§e营养 §f{nutrition}"],
    owner="arc_realistic_survival",
    priority=10,
)
# 数值变化时
arc.api_sidebar_set_values(
    "ars_health",
    {"thirst": 78, "infection": 0, "nutrition": "良好"},
    xuid=player.xuid,
)
```

| 函数 | 参数 | 返回值 |
|------|------|--------|
| `api_sidebar_register_page` | `page_id`，`title`，`lines`，`owner=""`，`priority=0`，`hide_line_if_missing=True` | `bool` |
| `api_sidebar_unregister_page` | `page_id` | `bool`（不可注销 `arc_core_main`） |
| `api_sidebar_set_page_lines` | `page_id`，`lines` | `bool` |
| `api_sidebar_set_page_title` | `page_id`，`title` | `bool` |
| `api_sidebar_set_value` | `page_id`，`key`，`value`，`player_name=""`，`xuid=""` | `bool`：有玩家参数则写私有值，否则写全局值 |
| `api_sidebar_set_values` | `page_id`，`values: dict`，`player_name=""`，`xuid=""` | `bool` |
| `api_sidebar_get_value` | `page_id`，`key`，`player_name=""`，`xuid=""`，`default=None` | 任意 |
| `api_sidebar_clear_values` | `page_id`，`player_name=""`，`xuid=""` | `bool` |
| `api_sidebar_set_global_value` | `page_id`，`key`，`value` | `bool` |
| `api_sidebar_set_page_visible` | `page_id`，`visible`，`player_name=""`，`xuid=""` | `bool`：按玩家显隐（主页不可隐） |
| `api_sidebar_refresh` | `player_name=""`，`xuid=""` | `None`：立即重绘；不传玩家则刷新全部在线 |
| `api_sidebar_list_pages` | 无 | `list[dict]`：`page_id` / `title` / `owner` / `priority` / … |

#### 头衔 / 发奖

| 函数 | 参数 | 返回值 |
|------|------|--------|
| `api_unlock_title` | `player`（Player），`title` | `bool`：成功；新解锁且在线则发奖，未佩戴时自动佩戴 |
| `api_unlock_title_by_xuid` | `xuid`，`title` | `bool`：离线可记解锁；在线且新解锁则发奖并可能自动佩戴 |
| `api_set_title_definition` | `title`，`rarity`，`description`，`reward_money`，`reward_items=None` | `bool`：创建或**覆盖**定义。`reward_items` 形如 `[{"item_name":"minecraft:diamond","count":1}]`（亦接受键 `id`） |
| `api_ensure_title_definition` | `title`，`rarity="普通"`，`description=""`，`reward_money=0.0`，`reward_items=None` | `bool`：仅当头衔不存在时插入，不覆盖已有定义 |
| `api_get_title_definition` | `title` | `dict \| None`：`title` / `rarity` / `description` / `reward_money` / `reward_items` |
| `api_list_title_definitions` | 无 | `list[dict]`：全部头衔定义（字段同上） |
| `api_has_unlocked_title` | `title`，关键字：`player=None`，`player_name=""`，`xuid=""` | `bool`：解析顺序 `xuid` → `player` → `player_name` |
| `api_get_equipped_title` | `player_name=""`，`xuid=""` | `str`：当前佩戴头衔；未佩戴 / 找不到为 `""` |
| `api_list_unlocked_titles` | `player_name=""`，`xuid=""` | `list[str]`：已解锁头衔名 |
| `api_give_player_items` | `player=None`，`items=None`，`player_name=""`，`xuid=""` | `bool`：向**在线**玩家 `give`；至少发出一条有效物品为 `True` |

#### 玩家 / 其它

| 函数 | 参数 | 返回值 |
|------|------|--------|
| `api_get_player_xuid_by_name` | `player_name` | `str \| None`：在线优先，其次数据库（大小写不敏感） |
| `api_get_player_name_by_xuid` | `xuid`，`with_title=False` | `str`：找不到为 `""`；`with_title=True` 时为公会/头衔展示名 |
| `api_get_player_playtime` | `raw_player_name=""`，`xuid=""` | `dict`：`session_count`，`total_playtime`（秒，含当前会话），`is_online`，`last_join_time`，`last_quit_time`，`xuid`；找不到时时长为 0 |
| `api_get_newbie_guide_text` | 无 | `str`：`newbie_welcome.txt` 全文；失败为 `""` |

#### 领地

维度会规范化（如 `Overworld` → `minecraft:overworld`）；坐标按三维 AABB（含 Y）。生效顺序：私人/公会 > 公共(`public_priority` 3>2>1)。

| 函数 | 参数 | 返回值 |
|------|------|--------|
| `api_if_position_in_land` | `dimension`，`position=(x,y,z)` | `int \| None`：生效主领地 `land_id`；不在领地为 `None` |
| `api_resolve_land_at_position` | `dimension`，`position` | `dict`：`dimension`，`land_id`，`sub_land_id`，`is_public`，`public_priority`，`owner_xuid`，`covering_land_ids` |
| `api_list_lands_at_position` | `dimension`，`position` | `list[dict]`：覆盖该点的全部主领地（含 `land_id`），按生效优先级降序 |
| `api_get_land_info` | `land_id` | `dict`：领地详情；不存在为 `{}`。常见键：`land_name`，`dimension`，`min_/max_x/y/z`，`tp_x/y/z`，`shared_users`，`owner_xuid`，`for_sale`，`sale_price`，各类开关，`public_priority`，`block_actor_spawn`（该领地是否开启拦截生物生成；开启后按全局黑/白名单生效），`owner_paid_money` |
| `api_get_player_lands` | `player_name=""`，`xuid=""` | `list[dict]`：该玩家私人领地（含 `land_id`） |
| `api_get_guild_lands` | `guild_id` | `list[dict]`：该公会领地（含 `land_id`） |
| `api_check_land_access` | `dimension`，`position`，`player_name=""`，`xuid=""`，`action="build"` | `dict`：`allowed`，`land_id`，`sub_land_id`，`is_public`，`wilderness`，`action`。静默检查，不发聊天。`action` 为 `build` 或 `interact` |

#### 传送

仅对**在线**玩家生效。

| 函数 | 参数 | 返回值 |
|------|------|--------|
| `api_teleport_player_to` | `dimension`，`x`，`y`，`z`，`player_name=""`，`xuid=""` | `dict`：`ok`，`error`。错误码：`PLAYER_NOT_ONLINE` / `TELEPORT_FAILED` |
| `api_teleport_player_to_home` | `home_name`，`player_name=""`，`xuid=""` | `dict`：`ok`，`error`。另含 `PLAYER_NOT_FOUND` / `HOME_NOT_FOUND` |
| `api_teleport_player_to_warp` | `warp_name`，`player_name=""`，`xuid=""` | `dict`：`ok`，`error`。另含 `WARP_NOT_FOUND` |
| `api_list_player_homes` | `player_name=""`，`xuid=""` | `list[dict]`：`home_name`，`dimension`，`x`，`y`，`z` |
| `api_list_public_warps` | 无 | `list[dict]`：`warp_name`，`dimension`，`x`，`y`，`z` |
| `api_list_spawn_locations` | 无 | `list[dict]`：`dimension`，`display`，`x`，`y`，`z` |
| `api_list_public_lands` | `limit=50` | `list[dict]`：公共领地摘要（`land_id`，`land_name`，`dimension`，传送点） |
| `api_get_server_landmarks_text` | 无 | `str`：出生点 + Warp + 公共领地，供 AI 指路 |

#### 公会

| 函数 | 参数 | 返回值 |
|------|------|--------|
| `api_get_player_guild_info` | `player_name=""`，`xuid=""` | `dict`：含 `guild_id`，`name`，`role`，`size_tier`，`capacity`，`member_count`，`total_contribution`，`personal_contribution`，`motto`，`owner_xuid`，`join_requires_approval`；未入会为 `{}` |
| `api_get_player_guild_id` | `player_name=""`，`xuid=""` | `int`：公会 id；未入会为 `0` |
| `api_get_guild_info` | `guild_id` | `dict`：公会公开信息；不存在为 `{}` |
| `api_list_guild_members` | `guild_id` | `list[dict]`：每项 `xuid`，`role`，`joined_at`，`contribution` |
| `api_add_guild_contribution` | `player_name=""`，`points=0`，`xuid=""` | `dict`：`ok`，`error`，`personal_contribution`，`guild_total_contribution`，`guild_id`。私人与公共**同时各 +points**（须为正整数） |
| `api_get_player_guild_contribution` | `player_name=""`，`xuid=""` | `int`：私人贡献点；未入会为 `0` |
| `api_get_guild_total_contribution_by_player` | `player_name=""`，`xuid=""` | `int`：所在公会公共贡献；未入会为 `0` |
| `api_get_guild_total_contribution` | `guild_id` | `int`：公会公共贡献点 |
| `api_change_guild_total_contribution` | `guild_id`，`delta` | `dict`：`ok`，`error`，`guild_id`，`total_contribution`，`delta`。只改公共池；`delta` 可负；低于 0 失败 |
| `api_get_member_guild_contribution` | `guild_id`，`player_name=""`，`xuid=""` | `int`：该成员私人贡献；非成员为 `0` |
| `api_change_member_guild_contribution` | `guild_id`，`delta`，`player_name=""`，`xuid=""` | `dict`：`ok`，`error`，`guild_id`，`personal_contribution`，`delta`。只改私人；`delta` 可负 |
| `api_set_guild_size_tier` | `guild_name`，`tier`（`small`/`medium`/`large`） | `bool`：目标容量低于当前人数时拒绝 |

公会常见错误码：`GUILD_INVALID_PLAYER`、`GUILD_NOT_IN_GUILD`、`GUILD_NOT_FOUND`、`GUILD_CONTRIB_INVALID_POINTS`、`GUILD_CONTRIB_NOT_ENOUGH`、`GUILD_DB_ERROR`。

## 📄 许可证

本项目采用开源许可证，详见 LICENSE 文件。

## 🤝 支持与反馈

- **作者邮箱**: DEVILENMO@gmail.com
- **问题反馈**: 请详细描述问题和复现步骤
- **功能建议**: 欢迎提供改进建议

## 📋 近期更新日志

### v0.9.47（当前版本）

- ✅ **自杀改回单次 `/kill`**：不再循环抽血；杀不死再另说

### v0.9.46

- ✅ **对接 DMZ**：主菜单在检测到 `dmz` 插件时显示「开放世界PVE搜打撤模式」，打开 `/dmz` 菜单
- ✅ **自杀曾改为多次抽血+kill**（v0.9.47 已改回单次 kill）

### v0.9.42

- ✅ **移除共享文件跨服同步**：删除 `*_DATABASE_PATH` 配置与 `legacy_recovery` 一次性导入；跨服仅支持 SyncServer + SyncClient 网络后端

### v0.9.41

- ✅ **传送倒计时改用屏幕 title**：Home / Warp / TPA / 死亡回归 / 随机传送倒计时改为屏幕中央大数字 + 字幕提示，不再刷聊天栏

### v0.9.40

- ✅ **修复同步主线程假死**：关服侧边栏不再写失效 Player

### v0.9.38

- ✅ **修复定时任务 purecall 崩服**：新增 `run_player_task` / `run_two_player_task` / `_resolve_online_player`；传送倒计时（45 tick）、进服延迟任务、领地边界粒子分批任务均改为闭包只存 xuid/name、回调时重取在线玩家；玩家退出时取消未完成的粒子任务

### v0.9.37

- ✅ **条件头衔稀有度修正**：首富等自动授予头衔按定义取最高稀有度（首富为「传奇」）；同持有者迁移时同步升级佩戴与解锁记录

### v0.9.36

- ✅ **生物显示名翻译修复**：死亡播报等处 `minecraft:skeleton` 类类型 ID 可正确解析为中文；`entity.ns:id.name` 与 `entity.ns.id.name` / `ns:id` 互通查找
- ✅ **补全 entity_display_name.txt**：原版与常见模组双格式条目（`entity.*.*.name` + `ns:id`），避免空键挡住翻译

### v0.9.35

- ✅ **迎新文案/指令改内存缓存**：`newbie_welcome.txt`、`newbie_commands.txt` 启动与 OP 重载时读入内存；进服欢迎、新手引导面板、API 不再每次读盘。公告 `broadcast.txt` 原本已是内存轮播。

### v0.9.34

- ✅ **主线程减负**：侧边栏调度改为每 5 tick，到期玩家小顶堆取代全员扫描；金钱/头衔/公会改后台缓存
- ✅ **天眼异步落库**：事件只入内存队列，SQLite 由后台线程写入；主手物品按玩家缓存，避免反复 `item_in_main_hand`
- ✅ **活动统计批量写库**：方块破坏/放置先内存累加，定时刷盘
- ✅ **领地 chunk LRU 缓存**：`get_land_at_pos` / 爆炸判定复用区块结果；领地粒子边界分 tick 发送

### v0.9.33

- ✅ **统一领地权限检查** `check_land_permission`：破坏/放置/方块交互/实体交互/攻击/展示框/爆炸共用；`ENABLE_LAND_SYSTEM=False` 或坐标无领地时直接跳过领地部分
- ✅ **领地系统关闭时**不再做爆炸拦截、生物生成拦截、仅领地可放置方块等领地相关判定

### v0.9.32

- ✅ **侧边栏进服 tick 错峰**：记录玩家进服 tick，调度每 1 tick 仅刷新到期玩家，自然分散负载；单人周期仍为 `SIDEBAR_REFRESH_TICKS`
- ✅ **领地系统总开关**：新增 `ENABLE_LAND_SYSTEM`；关闭时不启位置追踪线程（无进出领地提示），OP 面板可配

### v0.9.31

- ✅ **侧边栏键值不再即时重绘**：`set_value` / `set_values` 恢复为只写缓存，由定时 tick 按 `SIDEBAR_REFRESH_TICKS` 渲染

### v0.9.30

- ✅ **修复侧边栏数值延迟十几秒才更新**：分片桶数与调度周期叠乘导致单人刷新周期被平方；调度固定每 10 tick，桶数 = refresh_ticks/10
- ✅ **刷新周期可配 1 秒**：移除 `SIDEBAR_REFRESH_TICKS=20` 被静默改回 60 的限制；OP 面板改配置后热重载即生效

### v0.9.29

- ✅ **侧边栏纯定时刷新**：去掉进出服、金钱变动、插件 `set_value` 的事件重绘；约 3 秒 tick 按 xuid 分批渲染，降低单 tick 峰值；`/sidebar on|off|next|prev|lock` 仍即时响应
- ✅ **进服/离服减负**：`on_player_join` 头衔、时长、邀请提示、天眼、侧边栏分帧延迟执行；离服天眼与时长结算延后 1 tick，避免同帧阻塞主线程

### v0.9.28

- ✅ **天眼查询增强**：玩家名默认模糊匹配；`action` 支持 `death`/`pvp`/`pve`/`combat` 等别名与 PvP 细分；可不传玩家名按事件类型查全服；展示区分 PvP 攻击/死亡与攻击生物

### v0.9.27

- ✅ **PvP KD 排行榜插件适配**：检测到 `arc_pvp_kd` 时在主菜单显示「PvP KD 排行榜」入口，委托 `/kd` 打开玩家个人榜单 Form（原 `arc_hunter` / `/hunter` 已弃用）

### v0.9.26

- ✅ **修复侧边栏崩溃服务器**：`Objective.setDisplay` 改为每个 objective 只调一次；标题变化改 `display_name` 不再 `unregister` 重建；看板对象全程强引用，仅下线释放；进服延迟 `SIDEBAR_JOIN_DELAY_TICKS`（默认 40）后再写显示槽

### v0.9.25

- ✅ **侧边栏事件驱动刷新**：默认去掉生命/饱食；`SIDEBAR_REFRESH_TICKS` 默认 60（约 3 秒）只刷时间/TPS/延迟；金钱写库成功、进出服更新在线、插件 `set_value` 时触发刷新（旧配置 20 自动迁到 60）

### v0.9.24

- ✅ **侧边栏 TPS/延迟改为 3 秒取样**：去掉延迟 10ms 量化；TPS、MSPT、ping 每 3 秒取一次，其它行仍按 `SIDEBAR_REFRESH_TICKS` 刷新

### v0.9.23

- ✅ **修复文件同步模式下头衔迁移跨库删表**：`title_definitions` 等表重建改为 `rebuild_table_copy`（同连接事务 + VACUUM 备份），避免临时表落子服本地库、`DROP` 却打到主服共享库导致定义表丢失；启动前后校验表存在；对路由表 `DROP` 打 warning

### v0.9.22

- ✅ **备份库按 xuid 合并 `player_basic_info`**：主库缺表时先建表；按 xuid 插入/补空（密码、真实 uuid、名字），不覆盖已有密码；可从旧 `ARCCore.db` 备份恢复账号

### v0.9.21

- ✅ **修复跨库 DDL 误删 `player_basic_info`**：重建表时临时表不再落到默认库，避免共享库真表被 DROP；不可逆重建前 `VACUUM INTO` 备份，事务内行数校验失败则回滚
- ✅ **一次性数据恢复**：主服配置 `LEGACY_IMPORT_DATABASE_PATHS` 可把旧共享库里的经济/头衔/公会导回，并用天眼/本服表重建 `player_basic_info`（无备份时密码需玩家重设）
- ✅ **推荐纯网络同步**：主服开同步中心、子服 `ENABLE_SYNC_CLIENT`，清空四个 `*_DATABASE_PATH`；两种模式同时开时打 WARN

### v0.9.20

- ✅ **修复侧边栏导致客户端闪退**：不再每秒双缓冲销毁/重建 objective；改为复用稳定 `arc_sb` 并原地更新/清理行；假名截断至 40 字符；TPS/延迟按较低频率取样，降低发包频率

### v0.9.19

- ✅ **跨服排行改用 `once_op`**：`player_basic_info` 去掉镜像 `is_op`，改为粘性 `once_op`——任意服以 OP 登录过即置 1、卸任不回落；首富/金钱榜排除读此列。本服权限仍只看 `player_local_info.is_op`
- ✅ **启动迁移**：旧 `basic.is_op=1` 与本服 `local.is_op=1` 自动写入 `once_op`，并重建表去掉旧列

### v0.9.18

- ✅ **修复全量同步超时**：连接阶段 TCP 粘包缓冲保留半包；等待 `FULL_SYNC_RESPONSE` 时消化穿插的 PUSH/心跳，避免帧错位后 `title_definitions` 等表 `timed out`
- ✅ **全量同步期间不推送**：从服首心跳前 `accepts_push=False`，避免认证后立刻广播打断 request/response
- ✅ **全量读库锁缩短**：同步中心 `_full_sync_lock` 只包住 SELECT，不再在 `sendall` 期间堵住其他从服；单表等待超时提到 120s

### v0.9.17

- ✅ **修复共享库 DDL 路由**：`CREATE` / `ALTER` / `DROP` / `PRAGMA table_info` 按表走 `PLAYER_*_DATABASE_PATH`，不再误改子服本地库；避免「提示已加 is_op 列、写共享库仍缺列」刷屏
- ✅ **同步落库容忍列差**：`insert` / `upsert` / `update` 自动忽略目标表不存在的列（如远端多出的 `is_op`、误带的 `money`），避免网络同步协议服刷 `no column named …`
- ✅ **本服表缺列补齐**：`player_local_info` 启动时补加 `is_op` 等遗漏列

### v0.9.16

- ✅ **连接即全面对账**：远程客户端每次连上同步中心，在全量拉取后自动把本地已启用同步表整表上行
- ✅ **OP 一键对账**：配置面板点「跨服同步」即发起全面对账并显示状态，去掉多余确认步骤

### v0.9.15

- ✅ **猎手榜插件适配**：检测到 `arc_hunter` 时在主菜单显示「猎手榜」入口，委托 `/hunter` 打开玩家个人榜单 Form

### v0.9.14

- ✅ **跨服上行可靠投递**：子服本地写库先入 `sync_outbox`，断线不丢；重连先全量拉取再按序重放；协议升至 v3，数据操作响应带 `seq` ack（旧中心降级为发出即成功）
- ✅ **修复数据操作响应类型**：`INSERT/UPDATE/DELETE_RESPONSE` 不再误用 `ERROR_RESPONSE`，客户端可确认上行成败
- ✅ **条件头衔权威服**：新增 `CONDITIONAL_TITLE_AUTHORITY`；文件模式子服默认不再与主服双算首富
- ✅ **首富排除 OP**：`player_basic_info` 增加跨服 `is_op` 镜像列，榜一查询不再依赖本服独有的 `player_local_info`
- ✅ **OP 对账与状态**：核心设置 → 跨服同步 → 同步运行状态 / 立即对账（头衔）

### v0.9.13

- ✅ **核心库过时表清理**：启动时将旧 `player_achievement_stats` 的击杀/破坏/放置键迁入 `player_activity_stats` 后删除旧表；同步 DROP `richest_title_state`、`player_title_extra`、`player_achievement_unlocked`、`migration_history`（数据已迁至条件头衔表 / 成就插件本地库）

### v0.9.12

- ✅ **群聊死亡播报对接修复**：正确查找 `arc_qq_sync_astrbot`（Endstone 将 entry-point `-` 转为 `_`）；原始文本优先 `api_send_raw`
- ✅ **群聊死亡播报模式**：新增配置 `QQ_DEATH_BROADCAST_MODE`（`off` / `pvp` / `all`，默认 `all`），可在 OP 配置面板「通用」中调整

### v0.9.11

- ✅ **玩家活动统计**：本服表 `player_activity_stats` 记录击杀（含玩家）、破坏/放置方块累计；只读 API 供成就等插件查询；旧 `player_achievement_stats` 的 `kill_*` 自动迁移

### v0.9.10

- ✅ **天眼战斗留档增强**：攻击记录伤害量、受击前/后血量、最大血量、伤害类型；PvP 附带被打方装备与附魔；死亡记录全身装备与背包摘要；主手物品格式含附魔（如 `netherite_sword{sharpness:5}x1`）

### v0.9.9

- ✅ **Toast 图标微调**：默认 Windows；公会 剑；账户 护甲；传送 智能体；小喇叭 阅读

### v0.9.8

- ✅ **Toast 标题加基岩字形图标**：金钱、领地、传送、账户、邀请、公会、Home、小喇叭 等；语言文件未含字形时也会自动补到标题最前

### v0.9.7

- ✅ **重要提示改用 toast**：金钱变动、转账、领地创建/买卖/移交/授权、传送请求与结果、注册、公会创建、邀请奖励、Home、小喇叭等走 `send_toast`；保护拦截、击杀奖励等仍用聊天栏
- ✅ **`/tpa accept` / `/tpa deny`**：快速响应最近一条 TPA/TPHERE 请求（弹窗仍可用）
- ✅ **头衔支持同名不同稀有度**：`title_definitions` / 解锁 / 佩戴以 `(title, rarity)` 为完整标识；`api_has_title_definition` / `api_unlock_title` 等 API 按名称+稀有度匹配

### v0.9.6

- ✅ **头衔解锁不再发奖**：`api_unlock_title` / `api_unlock_title_by_xuid` 只负责解锁与自动佩戴；金钱/物品改由成就等业务插件发放。新增 `api_has_title_definition`
- ✅ **进出领地提示改用 tip**：进入/离开领地由 `send_popup` 改为 `send_tip`（屏幕下方快捷栏上方），避免挡视野

### v0.9.5

- ✅ **允许圈地开关**：新增本服独立配置 `ALLOW_LAND_CLAIM`（默认 `True`，不随主服同步）；关闭后隐藏「创建新领地」并拦截 `/land` 与新建确认，管理与调整已有领地范围不受影响

### v0.9.4

- ✅ **侧边栏灰橙配色**：重点色由 §b 改为 §6（橙金）；标题/TPS 等强调项同步

### v0.9.3

- ✅ **侧边栏主页面**：默认不再显示 MSPT，仅保留 TPS

### v0.9.2

- ✅ **侧边栏配色与排版**：§8/§7/§f/§b 四色约定；去掉加粗与空隙标题；主页顺序改为时间→性能→在线/延迟→生命/饱食→金钱；时间仅显示 `HH:MM`

### v0.9.1

- ✅ **侧边栏主页面**：新增 TPS / MSPT、在线人数上限、玩家延迟（`{tps}` `{mspt}` `{max_players}` `{ping}`）

### v0.9.0

- ✅ **侧边栏总控系统**：原生计分板多页面、默认 10 秒翻页、每玩家独立数据与开关偏好
- ✅ **核心主页面**：现实时间、金钱、生命、饱食度、在线人数等；模板与标题可配置
- ✅ **对外 API**：`api_sidebar_register_page` / `api_sidebar_set_value(s)` 等，供真实生存等插件注册页面并推送键值
- ✅ **玩家命令**：`/sidebar`（`/sb`）开关、翻页、锁定、列表；OP 面板新增「侧边栏」配置分组

### v0.8.22

- ✅ **条件头衔（首富）**：抽出可复用迁移层；同分 `ORDER BY money DESC, xuid ASC` 防抖动；持有者不变绝不 revoke；易主时曾戴则回退其它最高稀有度头衔，新持有者无佩戴则自动戴上
- ✅ **跨服仅主服计算首富**：同步从服 `refresh` no-op；主服在收到从服 `player_economy` 写入后刷新；解锁/佩戴仍走现有头衔同步

### v0.8.21

- ✅ **拦截生物生成作用范围**：新增 **`LAND_BLOCK_ACTOR_SPAWN_SCOPE`**（`public` 默认 / `all`）。`public` 仅公共领地且看各领地开关；`all` 时任意领地按全局黑/白名单拦截

### v0.8.20

- ✅ **爆炸保护保留伤害**：`BLOCK_ALL_EXPLOSIONS` 与领地禁止爆炸时，优先清空 `block_list`（不拆方块、保留实体伤害）；写回失败或仍非空则回退 `is_cancelled`，外层异常同样取消，避免保护失效

### v0.8.19

- ✅ **传送功能独立开关**：公共传送点 / Home / 随机传送 / 死亡点 / 玩家互传 / 跨服传送均可单独关闭（默认全开）；关闭后传送系统面板不显示对应按钮
- ✅ **玩家互传改为下拉框**：发送 TPA/TPHERE 时用下拉框选择请求类型与目标玩家

### v0.8.18

- ✅ **公共领地拦截生物生成去掉全局 Off**：`PUBLIC_LAND_BLOCK_ACTOR_SPAWN_MODE` 仅保留 **whitelist / blacklist**（默认 whitelist；旧值 False/off 视为白名单）。每个公共领地可单独开启/关闭拦截，开启后按全局名单模式生效

### v0.8.17

- 主菜单商店：优先对接木牌商店 `/ss`（`arc_sign_shop`），无则回退按钮商店 `/bs`

### v0.8.16

- ✅ **天眼指令可读性**：查玩家时同时匹配 `target_name`（能看到天星代其执行的指令）；新增动作 `AgentCommand`（天星指令）；热重载后已在线玩家自动纳入追踪

### v0.8.15

- ✅ **热修进服崩服**：天眼仅在 `PlayerJoin` 完成后再追踪该玩家；加载期 `GameModeChange` 直接忽略（不再读 `location`）
- ✅ `api_sky_eye_log(..., resolve_online=False)`：可不解析在线玩家、不读坐标

### v0.8.14

- ✅ **天眼全量留档**：玩家聊天、玩家指令、控制台/插件指令、游戏模式变更；动作标签 `PlayerChat` / `PlayerCommand` / `ConsoleCommand` / `GameModeChange` / `AiAgent`

### v0.8.13

- ✅ **地标 API**：`api_list_spawn_locations` / `api_list_public_lands` / `api_get_server_landmarks_text`，供弧光 Agent 回答出生点、公共传送点、功能区

### v0.8.12

- ✅ **天眼扩展**：银行变动、领地创建/删除、弧光传送、按钮商店（由 arc_button_shop 调用）、丢弃/拾取/切换主手/消耗物品、玩家传送事件
- ✅ **公开 API**：`api_sky_eye_log()` 供其他插件写入天眼

### v0.8.11

- ✅ **OP 配置文件设置**：`core_setting.yml` 全部项可在 OP 面板按分类用 UI 修改。布尔/多选为下拉框；列表为动态按钮 +「增加新配置」，点进单条可删除

### v0.8.10

- ✅ **拦截生物生成：全局模式 + 领地开关**：`PUBLIC_LAND_BLOCK_ACTOR_SPAWN_MODE` 为 **False** 时各公共领地固定显示「不开启拦截」且无开关按钮；为 **blacklist** / **whitelist** 时每个领地可单独开启/关闭拦截。名单仍为 `PUBLIC_LAND_BLOCK_ACTOR_SPAWN_LIST`

### v0.8.9

- ✅ **公共领地拦截生物生成改为模式**：OP 设置由开关改为 **Off / 黑名单 / 白名单**（默认 Off）。配置 **`PUBLIC_LAND_BLOCK_ACTOR_SPAWN_LIST`**（逗号分隔实体 ID）：白名单=名单上的不拦截，黑名单=只拦截名单上的。旧库 `block_actor_spawn=1` 迁移为白名单（名单为空则仍拦截全部）

### v0.8.8

- ✅ **天眼改独立 SQLite**：事件写入 `plugins/ARCCore/sky_eye/skyeye.db`，按 `SKY_EYE_MAX_RETENTION_DAYS` 滚动删除；每条记录带领地内外、领地名/主人，并记录玩家攻击与死亡击杀者
- ✅ **天星查询接口**：`api_sky_eye_query` / `api_sky_eye_query_text` / `api_sky_eye_player_now`；AstrBot 工具 `mc_skyeye_player`、`mc_skyeye_combat`、`mc_skyeye_location`（仅管理员）

### v0.8.7

- ✅ **OP 圈地冲突面板**：选区与现有领地重叠时，普通玩家仍直接拦住；OP 可进入待购面板创建「允许私人/公会覆盖」的公共领地，并默认勾选允许覆盖

### v0.8.6

- ✅ **进服时长中断修复**：语言文件末尾若有空的 `PLAYER_JOIN_MESSAGE=`，会覆盖文案并使 `broadcast_message` 抛错，游戏时长/进服次数因此记不上。空文案改为跳过；加载语言时也不再用空键覆盖已有翻译

### v0.8.5

- ✅ **公共领地拦截生物生成修复**：原先仅取消 EndStone `Mob` 类型，模组生物常被包成普通 Actor 因而漏拦。开启 `block_actor_spawn` 后改为取消该公共领地内**除玩家外的全部实体**生成
- ✅ **跨服玩法配置以主服为准**：远程客户端按已开启的同步类别，从同步中心拉取并覆盖对应 `core_setting.yml` 项（初始金钱、公会升级消耗、签到存款、传送/圈地价格等）。主服改配置或重载后推送；从服本地修改这些键会被主服覆盖。共享文件模式不自动同步配置
- ✅ **公会 API 补齐**：按公会 id / 玩家 xuid 查询与单独增减公共、私人贡献点（`api_get_player_guild_id`、`api_get_guild_info`、`api_get_guild_total_contribution`、`api_change_guild_total_contribution`、`api_get_member_guild_contribution`、`api_change_member_guild_contribution`、`api_list_guild_members`）
- ✅ **其它系统对外 API**：经济 / 头衔 / 领地 / 传送 / 玩家解析统一支持 **xuid**；新增 `api_adjust_player_money`、财富排名、头衔列表与佩戴查询、玩家/公会领地、静默领地权限检查、坐标/Home/Warp 传送、`api_get_player_name_by_xuid` 等。旧签名保持兼容

### v0.8.4

- ✅ **成就系统拆出**：成就迁至独立插件 `endstone_arc_achievement`（`arc_achievement`）；核心仅转发菜单入口并提供头衔/发奖 API。关服时忽略同步套接字已关闭后的 `recv` 噪声

### v0.8.2

- ✅ **玩家表拆分**：跨服 **`player_basic_info`**（密码、邀请、**游戏时长 / 进服次数**）；本服 **`player_local_info`**（`is_op`、剩余免费领地格、**签到**）。启动自动迁移
- ✅ **QQ 中继移除**：不再经 SyncServer 转发 QQ 事件 / 群聊下行（原 `QQ_RELAY_MODE` / `EVENT_FORWARD` 已移除）。群服互通由 AstrBot 弧光 EndStone 消息中枢 + QQ Sync 插件负责；死亡使用 `api_send_event("death", …)`，成就等可用 `custom`
- ✅ **公共领地三级优先级**：`lands.public_priority`（1/2/3，**3 最高**，默认 1）。高优先级公共可覆盖低优先级；同级不可重叠。位置生效顺序：**私人/公会 > 公共(3>2>1)**；私人子领地仍先于父领地。创建公共领地时 OP 选择等级；OP 公共领地设置可改级（升高时校验冲突）
- ✅ **公共领地拦截生物生成**：`block_actor_spawn`（默认关闭）；开启后经 `ActorSpawnEvent` 取消该公共领地内 `Mob`（不含玩家）生成
- ✅ **传送点校验修复**：设置领地传送点改为按目标领地三维 AABB（含维度/Y）判定，不再用「脚下生效领地 ID」比较，避免嵌套私人地/高层公共覆盖时误报「不在领地内」
- ✅ **领地外接 API**：`api_if_position_in_land` 适配三维 Y、维度规范化与多层生效；新增 **`api_resolve_land_at_position`**、**`api_list_lands_at_position`**。维度支持规范 ID（如 `minecraft:overworld`）及自定义维度
- ✅ **跨维传送指令修复**：`/execute in` 对原版三维度使用短名（`overworld` / `nether` / `the_end`），去掉 `minecraft:`；自定义维度仍使用完整 `namespace:dimension_identifier`
- ✅ **版本号方案**：历史版本号由 `0.0.x` 调整为 `0.x`（如原 `0.0.8.1` → `0.8.1`）

### v0.8.1

- ✅ **跨服数据同步**：游戏服 **远程客户端**（**`ENABLE_SYNC_CLIENT`**）与 **共享文件路径**（**`PLAYER_DATABASE_PATH`** 等）**互斥**；远程模式支持分项开关 **`SYNC_CLIENT_SYNC_PLAYER` / `_ECONOMY` / `_TITLE` / `_GUILD`**。新增 **`sync_client.py`**、**`sync_config.py`**；同步中心 **`ENABLE_SYNC_SERVER`** 可选开启。详见上文「跨服数据同步」
- ✅ **爆炸监听修复**：修复 **`ActorExplodeEvent`** 在 **`BLOCK_ALL_EXPLOSIONS=False`** 时按领地保护的流程错误。改为用 **`block.x/y/z`** 直接取坐标；对需保留方块通过 **`get_block_at`** 重建 **`block_list`** 再写回；写回失败时回退为取消整次爆炸

### 计划中的功能
- 🔄 更多语言包支持
- 🔄 数据备份和恢复

---

*ARC Core 是一个功能完整、性能优异的 EndStone 插件，为服务器管理者提供了一站式的解决方案。*
