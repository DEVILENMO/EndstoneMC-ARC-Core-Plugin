# EndStone ARC Core Plugin

## 概述

EndStone ARC Core 是一个功能完整的 EndStone (Minecraft 基岩版服务器) 插件，为服务器提供全方位的核心功能模块。该插件包含玩家管理、经济系统、领地管理、传送系统、公告系统、清道夫系统等丰富功能，是构建现代化 Minecraft 服务器的理想选择。

## 作者信息

- **作者**: DEVILENMO
- **邮箱**: DEVILENMO@gmail.com
- **版本**: 0.0.1.3
- **API 版本**: 0.7+

## ✨ 主要功能

### 🗄️ 数据库管理
- 基于 SQLite 的高性能数据库支持
- 线程安全的数据库连接管理
- 自动创建数据库文件和目录
- 支持复杂查询和事务处理

### 🌍 多语言支持
- 完整的国际化系统
- 动态语言文件加载
- 默认支持中文 (ZH-CN)
- 可扩展其他语言包

### 👤 玩家管理系统
- 密码认证登录系统
- 玩家数据持久化存储
- 在线状态实时管理
- 玩家加入/离开消息提示

### 💰 银行经济系统
- 完整的货币管理系统
- 玩家余额存储和查询
- 玩家间转账功能
- 富豪榜排行系统
- 管理员金钱操作命令
- 实时余额变动提醒

### 🏠 领地管理系统
- 矩形领地圈地功能
- **领地保护机制**（防止破坏/建造/方块互动）
- **领地授权系统** - 可将领地权限授权给其他玩家
- **领地移交功能** - 可将领地转移给其他玩家
- **爆炸保护设置** - 可单独控制领地内是否允许爆炸
- **方块互动开放设置** - 可设置领地对所有人开放方块互动（如开箱子、按按钮等）
- 领地传送点设置和管理
- 领地重命名功能
- 可配置的领地价格和最小距离
- 智能传送命令生成（自动处理包含空格的玩家名）

### 📍 传送系统
- **私人传送点 (Home)** - 玩家可设置多个传送点
- **公共传送点 (Warp)** - 管理员可创建公共传送点
- **玩家传送请求 (TPA/TPHERE)** - 玩家间传送请求系统
- **死亡回归系统** - 玩家死亡后可传送回死亡地点
- **跨维度传送支持** - 支持在主世界、下界、末地之间自由传送
- **智能维度处理** - 自动使用 `execute in <dimension> run tp` 指令格式
- 传送倒计时提示

### 💴 商店系统
- 适配了 `ushop` 商店插件，如果你安装了 `ushop` ，弧光核心的主菜单中会有 “商店” 按钮

### 📢 公告系统 (新增)
- 定时循环播放公告消息
- 支持多条公告轮播
- **动态占位符支持**：
  - `{date}` - 当前日期 (年-月-日)
  - `{time}` - 当前时间 (小时:分钟)
  - `{online_player_number}` - 当前在线玩家数
- 可配置公告发送间隔
- 从 `broadcast.txt` 文件读取公告内容

### 🧹 清道夫系统 (新增)
- 定时自动清理掉落物
- 可配置清理时间间隔
- 清理前10秒倒计时警告
- 清理过程状态提示
- 可通过配置开启/关闭

### 🛡️ 出生点保护
- 可配置的出生点保护范围
- 防止玩家在出生点附近建筑/破坏
- 多维度出生点支持

### ⚙️ OP 管理面板
- 游戏模式切换
- 手动触发掉落物清理
- 坐标记录功能
- 命令执行面板

### 🔌 插件 API 系统
- **经济系统 API** - 完整的金钱管理接口
- **线程安全设计** - 支持多插件并发调用
- **错误处理机制** - 自动处理异常情况
- **详细文档支持** - 提供完整的使用示例
- **未来扩展计划** - 领地、传送等系统API

## 命令列表

| 命令 | 描述 | 权限 | 用法 |
|------|------|------|------|
| `/arc` | 打开 ARC Core 主菜单 | 默认 | `/arc` |
| `/updatespawnpos` | 更新当前维度的出生点位置 | OP | `/updatespawnpos` |
| `/suicide` | 自杀命令 | 默认 | `/suicide` |
| `/spawn` | 传送到出生点 | 默认 | `/spawn` |
| `/addmoney` | 为玩家添加金钱 (仅OP) | OP | `/addmoney [玩家名] [数量]` |
| `/removemoney` | 从玩家扣除金钱 (仅OP) | OP | `/removemoney [玩家名] [数量]` |
| `/pos1` | 设置土地边界点1 | 默认 | `/pos1` |
| `/pos2` | 设置土地边界点2 | 默认 | `/pos2` |

## 📂 文件结构

插件会在 `plugins/ARCCore/` 目录下创建以下文件：

- `core_setting.yml` - 主要配置文件
- `broadcast.txt` - 公告消息文件
- `{语言代码}.txt` - 语言文件 (如 ZH-CN.txt)
- SQLite 数据库文件

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
MIN_LAND_DISTANCE=1                  # 领地最小距离
LAND_PRICE=1000                      # 领地价格 (每格)
LAND_SELL_REFUND_COEFFICIENT=0.9     # 领地出售退款系数

# 传送系统
MAX_PLAYER_HOME_NUM=5                # 玩家最大家园数量

# 公告系统
BROADCAST_INTERVAL=180               # 公告发送间隔 (秒)

# 清道夫系统
ENABLE_CLEANER=True                  # 是否启用清道夫
CLEANER_INTERVAL=600                 # 清理间隔 (秒)
```

### broadcast.txt - 公告消息文件

每行一条公告，支持占位符：

```txt
欢迎来到ARC弧光基岩服务器！你可以在聊天框发送/arc命令打开服务器操作菜单
请遵守服务器规则，文明游戏，共建和谐游戏环境！
现在是北京时间{date} {time}，请注意休息，爱护眼睛你我做起。
当前服务器在线人数{online_player_number}，求生者们请互帮互助
```

### 支持的占位符

| 占位符 | 描述 | 示例输出 |
|--------|------|----------|
| `{date}` | 当前日期 | `2024-01-15` |
| `{time}` | 当前时间 | `14:30` |
| `{online_player_number}` | 在线玩家数 | `5` |

## 安装说明

1. 确保您的服务器运行 EndStone 框架
2. 将插件文件放入服务器的 `plugins` 目录
3. 重启服务器或使用插件管理命令加载
4. 插件会自动创建必要的配置文件和数据库

## 依赖要求

- EndStone 框架 (API 版本 0.7+)
- Python 3.x
- SQLite3 (通常内置于 Python)

## 🎮 使用指南

### 快速开始
1. 玩家进入服务器后，使用 `/arc` 命令打开主菜单
2. 首次使用需要注册账户并设置密码
3. 登录后可使用各种功能：银行、领地、传送等

### 功能操作指南
- **银行系统**: 在主菜单点击"银行"进行转账、查看余额等
- **领地系统**: 
  - 使用 `/pos1` 和 `/pos2` 设置领地边界，然后在菜单中购买
  - 在领地详情中可设置爆炸保护、方块互动开放等高级选项
  - 支持将领地权限授权给其他玩家或完全移交领地
- **传送系统**: 在主菜单的"传送系统"中管理传送点和发送传送请求
- **公告查看**: 定时播放的公告会自动显示当前时间和在线人数

## 🗃️ 数据存储

插件使用 SQLite 数据库存储以下数据：
- **玩家信息**: 用户名、UUID、密码哈希、注册时间
- **经济数据**: 玩家余额、交易记录
- **领地信息**: 领地坐标、拥有者、传送点、共享用户、爆炸保护设置、方块互动开放设置
- **传送点**: 私人传送点、公共传送点坐标信息
- **服务器配置**: 出生点坐标、系统设置

## 🛠️ 开发信息

### 项目结构
```
EndStone-ARC-CORE/
├── src/endstone_arc_core/
│   ├── __init__.py              # 插件初始化
│   ├── arc_core_plugin.py       # 主插件类 (2800+ 行代码)
│   ├── DatabaseManager.py       # 数据库管理器
│   ├── LanguageManager.py       # 语言管理器
│   └── SettingManager.py        # 设置管理器
├── dist/ARCCore/
│   ├── core_setting.yml         # 配置文件
│   ├── broadcast.txt            # 公告文件
│   └── ZH-CN.txt               # 中文语言包
└── pyproject.toml              # 项目配置
```

### 核心技术特性
- **线程安全**: 数据库操作完全线程安全
- **事件驱动**: 基于 EndStone 事件系统
- **定时任务**: 使用 Scheduler 实现定时功能
- **模块化设计**: 各功能模块独立，易于维护
- **动态配置**: 支持运行时配置重载

### API 兼容性
- **EndStone API**: 0.7+
- **Python**: 3.8+
- **数据库**: SQLite 3.x

## 📈 性能特性

- **高效的区块索引**: 领地系统使用区块映射，快速定位
- **内存优化**: 合理的缓存策略，减少数据库查询
- **异步处理**: 耗时操作使用定时任务处理
- **资源清理**: 自动清理过期的传送请求和临时数据

## 🔒 安全特性

- **密码保护**: 玩家密码使用 SHA-256 哈希存储
- **权限系统**: 基于 EndStone 权限系统
- **输入验证**: 所有用户输入都经过严格验证
- **SQL 注入防护**: 使用参数化查询

## 🔌 API 接口

ARC Core 插件提供了丰富的 API 接口供其他插件调用，主要包括经济系统相关的功能。

### 💰 经济系统 API

#### 1. 获取所有玩家金钱数据
```python
def api_get_all_money_data(self) -> dict
```
- **功能**: 获取所有玩家的金钱数据
- **返回值**: `dict` - 键为玩家名称，值为金钱数量
- **示例**:
```python
arc_plugin = server.get_plugin('ARCCore')
money_data = arc_plugin.api_get_all_money_data()
# 返回: {'PlayerA': 10000, 'PlayerB': 5000, ...}
```

#### 2. 获取单个玩家金钱
```python
def api_get_player_money(self, player_name: str) -> int
```
- **功能**: 获取指定玩家的金钱数量
- **参数**: `player_name` (str) - 玩家名称
- **返回值**: `int` - 玩家金钱数量，玩家不存在时返回 0
- **示例**:
```python
money = arc_plugin.api_get_player_money('PlayerName')
```

#### 3. 获取最富有玩家信息
```python
def api_get_richest_player_money_data(self) -> list
```
- **功能**: 获取服务器中最富有玩家的信息
- **返回值**: `list` - [玩家名称, 金钱数量]，无数据时返回 ['', 0]
- **示例**:
```python
richest = arc_plugin.api_get_richest_player_money_data()
# 返回: ['RichPlayer', 999999]
```

#### 4. 获取最贫穷玩家信息
```python
def api_get_poorest_player_money_data(self) -> list
```
- **功能**: 获取服务器中最贫穷玩家的信息
- **返回值**: `list` - [玩家名称, 金钱数量]，无数据时返回 ['', 0]
- **示例**:
```python
poorest = arc_plugin.api_get_poorest_player_money_data()
# 返回: ['PoorPlayer', 100]
```

#### 5. 修改玩家金钱
```python
def api_change_player_money(self, player_name: str, money_to_change: int) -> None
```
- **功能**: 增加或减少指定玩家的金钱
- **参数**: 
  - `player_name` (str) - 玩家名称
  - `money_to_change` (int) - 要改变的金钱数量（正数为增加，负数为减少）
- **返回值**: 无
- **注意事项**:
  - 金钱范围限制在32位整数范围内 (-2,147,483,648 到 2,147,483,647)
  - 如果玩家在线，会自动发送金钱变动提示消息
  - 不能传入 0 作为变动数量
- **示例**:
```python
# 给玩家增加 1000 金钱
arc_plugin.api_change_player_money('PlayerName', 1000)

# 从玩家扣除 500 金钱
arc_plugin.api_change_player_money('PlayerName', -500)
```

### 🔧 API 使用示例

#### 完整的插件集成示例
```python
from endstone.plugin import Plugin

class MyPlugin(Plugin):
    def on_enable(self):
        # 获取 ARC Core 插件实例
        self.arc_core = self.server.get_plugin('ARCCore')
        
        if self.arc_core is None:
            self.logger.error("ARC Core plugin not found!")
            return
    
    def give_reward_to_player(self, player_name: str, amount: int):
        """给玩家发放奖励金钱"""
        try:
            # 检查玩家当前金钱
            current_money = self.arc_core.api_get_player_money(player_name)
            self.logger.info(f"Player {player_name} current money: {current_money}")
            
            # 增加金钱
            self.arc_core.api_change_player_money(player_name, amount)
            
            # 获取更新后的金钱
            new_money = self.arc_core.api_get_player_money(player_name)
            self.logger.info(f"Player {player_name} new money: {new_money}")
            
        except Exception as e:
            self.logger.error(f"Failed to give reward: {e}")
    
    def get_server_economy_stats(self):
        """获取服务器经济统计"""
        try:
            # 获取所有玩家金钱数据
            all_money = self.arc_core.api_get_all_money_data()
            
            # 获取最富有和最贫穷的玩家
            richest = self.arc_core.api_get_richest_player_money_data()
            poorest = self.arc_core.api_get_poorest_player_money_data()
            
            # 计算统计信息
            total_money = sum(all_money.values())
            player_count = len(all_money)
            average_money = total_money / player_count if player_count > 0 else 0
            
            self.logger.info(f"Economy Stats:")
            self.logger.info(f"- Total Players: {player_count}")
            self.logger.info(f"- Total Money: {total_money}")
            self.logger.info(f"- Average Money: {average_money:.2f}")
            self.logger.info(f"- Richest: {richest[0]} ({richest[1]})")
            self.logger.info(f"- Poorest: {poorest[0]} ({poorest[1]})")
            
        except Exception as e:
            self.logger.error(f"Failed to get economy stats: {e}")
```

### 📋 API 注意事项

1. **插件依赖**: 确保您的插件在 `plugin.yml` 中声明了对 ARC Core 的依赖
2. **错误处理**: 所有 API 调用都应该包含适当的错误处理
3. **线程安全**: 所有 API 方法都是线程安全的，可以在任何线程中调用
4. **性能考虑**: 频繁调用 `api_get_all_money_data()` 可能影响性能，建议缓存结果
5. **玩家存在性**: API 会自动处理不存在的玩家，但建议在调用前验证玩家是否存在

### 🚀 未来 API 计划

- **领地系统 API**: 查询、创建、管理领地的接口
- **传送系统 API**: 程序化传送点管理
- **权限系统 API**: 玩家权限查询和管理
- **数据统计 API**: 服务器统计数据接口

## 📄 许可证

本项目采用开源许可证，详见 LICENSE 文件。

## 🤝 支持与反馈

- **作者邮箱**: DEVILENMO@gmail.com
- **问题反馈**: 请详细描述问题和复现步骤
- **功能建议**: 欢迎提供改进建议

## 📋 更新日志

### v0.0.1.3 (当前版本)
- ✅ **跨维度传送系统重构** - 全面升级传送机制
  - 支持主世界 (overworld)、下界 (nether)、末地 (the_end) 之间的自由传送
  - 传送指令格式升级为 `execute in <dimension> run tp player_name x y z`
  - 移除了维度检查限制，玩家可以跨维度使用所有传送功能
  - 智能维度名称处理，自动转换完整维度名称格式
  - 涵盖所有传送功能：Home传送、Warp传送、死亡回归、领地传送、玩家间传送

### 历史版本已实现功能
- ✅ 完整的玩家管理和认证系统
- ✅ 银行经济系统（转账、富豪榜）
- ✅ 领地管理系统（圈地、保护、移交、授权管理）
- ✅ **领地高级设置**（爆炸保护、方块互动开放设置）
- ✅ 传送系统（Home、Warp、TPA、死亡回归）
- ✅ **智能传送命令**（自动处理包含空格的玩家名）
- ✅ 公告系统（定时播放、动态占位符）
- ✅ 清道夫系统（定时清理掉落物）
- ✅ 出生点保护系统
- ✅ OP 管理面板
- ✅ 多语言支持 (ZH-CN)
- ✅ 完整的 GUI 界面
- ✅ **经济系统 API** - 供其他插件调用的完整经济接口

### 计划中的功能
- 🔄 商店系统
- 🔄 更多语言包支持
- 🔄 数据备份和恢复
- 🔄 领地系统 API 扩展
- 🔄 传送系统 API 扩展

---

*ARC Core 是一个功能完整、性能优异的 EndStone 插件，为服务器管理者提供了一站式的解决方案。*