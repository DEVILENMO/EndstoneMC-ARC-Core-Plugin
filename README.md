# EndStone ARC Core Plugin

## 概述

EndStone ARC Core 是一个功能强大的 EndStone (Minecraft 基岩版服务器) 插件，为服务器提供核心功能模块。该插件是 ARC 插件系列的核心组件，提供了数据库管理、多语言支持、玩家管理、经济系统、土地管理等多种功能。

## 作者信息

- **作者**: DEVILENMO
- **邮箱**: DEVILENMO@gmail.com
- **版本**: 0.0.1
- **API 版本**: 0.7

## 主要功能

### 🗄️ 数据库管理
- 基于 SQLite 的数据库支持
- 线程安全的数据库连接管理
- 自动创建数据库文件和目录

### 🌍 多语言支持
- 完整的国际化系统
- 动态语言文件加载
- 默认支持中文 (ZH-CN)
- 可扩展其他语言

### ⚙️ 配置管理
- YAML 格式的配置文件
- 动态配置加载和保存
- 默认值自动设置

### 👤 玩家管理系统
- 玩家认证系统
- 玩家数据持久化存储
- 在线状态管理

### 💰 经济系统
- 完整的货币管理
- 玩家余额存储
- 管理员金钱操作命令

### 🏠 土地管理系统
- 土地圈地功能
- 土地保护机制
- 土地买卖系统
- 可配置的土地价格和退款系数

### 📍 传送系统
- 家园传送 (Home)
- 玩家间传送请求 (TPA)
- 出生点传送
- 死亡回归系统

### 🛡️ 出生点保护
- 可配置的出生点保护范围
- 防止玩家在出生点附近建筑/破坏

## 命令列表

| 命令 | 描述 | 权限 | 用法 |
|------|------|------|------|
| `/arc` | 打开 ARC Core 主菜单 | `arc_core.command.arc` | `/arc` |
| `/updatespawnpos` | 更新当前维度的出生点位置 | 默认 | `/updatespawnpos` |
| `/suicide` | 自杀命令 | `arc_core.command.suicide` | `/suicide` |
| `/spawn` | 传送到出生点 | `arc_core.command.spawn` | `/spawn` |
| `/addmoney` | 为玩家添加金钱 (仅OP) | 默认 | `/addmoney [玩家名] [数量]` |
| `/removemoney` | 从玩家扣除金钱 (仅OP) | 默认 | `/removemoney [玩家名] [数量]` |
| `/pos1` | 设置土地边界点1 | `arc_core.command.set_land_corner` | `/pos1` |
| `/pos2` | 设置土地边界点2 | `arc_core.command.set_land_corner` | `/pos2` |

## 权限系统

| 权限节点 | 描述 | 默认值 |
|----------|------|---------|
| `arc_core.command.arc` | 使用主菜单命令 | true |
| `arc_core.command.suicide` | 使用自杀命令 | true |
| `arc_core.command.spawn` | 使用出生点传送 | true |
| `arc_core.command.set_land_corner` | 设置土地边界 | true |

## 配置文件

插件会在 `plugins/ARCCore/` 目录下创建以下文件：

- `core_setting.yml` - 主要配置文件
- `{语言代码}.txt` - 语言文件 (如 ZH-CN.txt)
- SQLite 数据库文件

### 主要配置选项

```yaml
DEFAULT_LANGUAGE_CODE=ZH-CN          # 默认语言
DATABASE_PATH=arc_core.db            # 数据库文件路径
IF_PROTECT_SPAWN=false               # 是否保护出生点
SPAWN_PROTECT_RANGE=8                # 出生点保护范围
MIN_LAND_DISTANCE=0                  # 土地最小距离
LAND_PRICE=1000                      # 土地价格
LAND_SELL_REFUND_COEFFICIENT=0.9     # 土地出售退款系数
MAX_PLAYER_HOME_NUM=3                # 玩家最大家园数量
```

## 安装说明

1. 确保您的服务器运行 EndStone 框架
2. 将插件文件放入服务器的 `plugins` 目录
3. 重启服务器或使用插件管理命令加载
4. 插件会自动创建必要的配置文件和数据库

## 依赖要求

- EndStone 框架 (API 版本 0.7+)
- Python 3.x
- SQLite3 (通常内置于 Python)

## 数据存储

插件使用 SQLite 数据库存储以下数据：
- 玩家基础信息
- 经济数据 (玩家余额)
- 土地信息
- 家园传送点
- 服务器配置信息

## 开发信息

### 项目结构
```
src/endstone_arc_core/
├── __init__.py              # 插件初始化
├── arc_core_plugin.py       # 主插件类
├── DatabaseManager.py       # 数据库管理器
├── LanguageManager.py       # 语言管理器
└── SettingManager.py        # 设置管理器
```

### 核心组件

1. **ARCCorePlugin**: 主插件类，继承自 EndStone Plugin
2. **DatabaseManager**: 提供线程安全的 SQLite 数据库操作
3. **LanguageManager**: 处理多语言支持和文本本地化
4. **SettingManager**: 管理插件配置和设置

## 许可证

本项目遵循项目根目录下的 LICENSE 文件中指定的许可证。

## 支持与反馈

如有问题或建议，请联系作者：
- 邮箱: DEVILENMO@gmail.com

## 更新日志

### v0.0.1
- 初始版本发布
- 实现基础功能模块
- 完成数据库、语言和设置管理系统
- 添加玩家管理和经济系统
- 实现土地管理和传送功能

---

*此插件是 ARC 插件系列的核心组件，为其他 ARC 系列插件提供基础服务和 API 支持。*