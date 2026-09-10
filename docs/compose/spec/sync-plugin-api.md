---
feature: sync-plugin-api
status: delivered
updated: 2026-02-14
branch: master
commits: 20527ee5e3ae39b5a3028f19e36195c05e6b3b88..working-tree
---

# 跨服同步插件化 API

## Report

**What was built** — ARC Core 跨服同步升级到协议 v4，新增第三方插件命名空间 API。其它 EndStone 插件可通过 `api_sync_register_namespace` 声明自有表（字段 + 主键）并提供 `on_apply` 回调；业务数据仍写在插件自己的 SQLite。同步中心将逻辑表 `plugin_id:table` 落成 `psync_*` 物理表（字段类型经白名单校验，主键落到 CREATE TABLE）；从服经 outbox 上行、全量/推送下行回调。内置 player/economy/title/guild 表保持旧 `SyncTable` 路径，旧客户端不受影响。中心协议 &lt; 4 时插件表 outbox 跳过且不烧 attempts，升级后可续传。

**Verification** — `PYTHONPATH=src python -m unittest discover -s tests -v`：25 tests OK（含逻辑名/注册表/协议字段/建表 SQL 注入拒绝）。`py_compile` 对 `sync_plugin_api` / `sync_protocol` / `sync_server` / `sync_client` / `arc_core_plugin` 通过。本机未安装 `endstone`，无法做运行时插件联调。

**Journey log**
- 表标识从写死 IntEnum 扩展为可选 `table_name`，避免破坏 v3 客户端。
- 中心建表必须复用客户端的 `normalize_fields` + 主键，否则认证 JSON 可注入 SQL 或 upsert 变重复插入。
- 协议 &lt; 4 的 outbox 项不应 `mark_attempt`，否则中心升级后永远卡在 max attempts。

## [S1] Problem

ARC Core 已有 SyncServer（同步中心）+ SyncClient（从服）跨服镜像，但表集合写死在 `SyncTable` 枚举里（player/economy/title/guild）。其它 EndStone 插件（含未来拆出的公会）无法：

1. 注册自己的数据表进入跨服同步；
2. 在从服上把下行数据写回**自己的插件目录** SQLite；
3. 通过进程内 API 上报本地变更到中心，并接收其它从服的推送。

结果是：任何新玩法要么被塞进核心本体，要么放弃跨服一致。

## [S2] Design

### 目标行为

- 第三方插件在 `on_enable` 通过 `server.get_plugin('arc_core')` 注册命名空间与表结构，并提供 `on_apply` 回调。
- 插件仍**只读写自己的本地库**；跨服传输由 ARC Core 的 SyncServer/SyncClient 承担。
- 同步中心把插件表落成 ARC Core 中心库中的命名空间物理表；从服收到全量/推送后调用插件回调，由插件写入自己的库。
- 内置 player/economy/title/guild 表**继续走现有枚举与 mirror 路径**，不迁移。

### 接入模型（进程内 API）

| API | 说明 |
|---|---|
| `api_sync_register_namespace(plugin_id, tables, on_apply)` | 注册；`tables` 为 `{table: {"fields": {col: sql_type}, "primary_keys": [..]}}` |
| `api_sync_unregister_namespace(plugin_id)` | 注销 |
| `api_sync_upsert(plugin_id, table, row)` | 本地写成功后整行 upsert 上行 |
| `api_sync_delete(plugin_id, table, where, params)` | 本地删成功后上行删除 |
| `api_sync_list_namespaces()` / `api_sync_namespace_status(plugin_id)` | 查询 |

`on_apply(namespace, table, op, data) -> bool`：

- `op="full"`：`data={"rows": [row, ...]}`，插件应整表或按主键合并
- `op="upsert"`：`data=row`
- `op="delete"`：`data={"_where": str, "_params": list}`

回调在 SyncClient 后台线程调用，插件需自行保证线程安全。

### 逻辑名与物理表

- 逻辑表名：`{plugin_id}:{table}`，如 `arc_guild:guilds`
- 校验：`plugin_id`、`table` 均为 `^[a-z][a-z0-9_]{0,31}$` / `^[a-z][a-z0-9_]{0,62}$`
- 中心物理表：`psync_{plugin_id}_{table}`（满足 `DatabaseManager` 标识符规则，且不与内置表名冲突）
- 中心在收到带 schema 的注册后 `CREATE TABLE IF NOT EXISTS`（`normalize_fields` 校验列类型；`primary_keys` 写入列级或表级 PRIMARY KEY）；中心**不要求**装载对应插件

### 协议 v4

- `PROTOCOL_VERSION = 4`
- 数据/全量/推送报文可选字段 `table_name: "ns:table"`；存在且为插件逻辑表时忽略 `table` 整型枚举
- 认证请求增加可选 `plugin_tables: [{"name", "fields", "primary_keys"}]`；中心据此建表并把逻辑名记入该连接允许集合
- 客户端仅在 `server_protocol >= 4` 时发送插件表操作；更旧中心上插件表 outbox 项跳过且不增加 attempts（中心升级后可续传）
- 内置表继续使用 v3 语义（`table` 枚举），与旧客户端兼容

### 数据流

```text
插件本地写自己的 SQLite
  → api_sync_upsert / api_sync_delete
  → SyncClient outbox (table_name="ns:table")
  → 中心 INSERT/DELETE (table_name)
  → 中心写 psync_* 物理表
  → PUSH 广播给其它已认证且订阅该逻辑表的从服
  → 从服 SyncClient 调 on_apply
  → 对端插件写自己的 SQLite
```

断线时 outbox 持久化，重连后按 seq 重放（与内置表相同机制）。

连接建立时对每个已注册插件表做全量拉取，经 `on_apply(op="full")` 合并。

### 权限与隔离

- 延续共享 `auth_key` 信任模型（与现状一致）：认证通过即可写中心已知逻辑表
- 客户端**只**在 auth 时声明本地已注册的插件表；中心按连接的 `plugin_tables` 过滤
- 未在 auth 声明的逻辑表写入/全量 → `Table not allowed`
- 插件表不得伪装成内置表名；物理前缀 `psync_` 保留
- 中心对认证 JSON 中的列类型做与本地注册相同的白名单校验，拒绝注入

### 注册时序

- 插件可在 SyncClient 启动前注册：表进入首次 auth 的 `plugin_tables`
- 客户端已连接后注册：触发一次重连（重新 auth + 全量），保证中心建表并拉取
- 注销后：不再接收推送；outbox 中该命名空间残留项在 flush 时丢弃

### 同步未启用

- `ENABLE_SYNC_SERVER` / `ENABLE_SYNC_CLIENT` 均关：注册仍成功，`api_sync_*` 为本地 no-op（返回 `synced=False`），便于单机开发
- 从服已连接但中心协议 &lt; 4：返回 `synced=False, queued=True, reason=hub_protocol_lt_4`

## [S3] Out of Scope

- 公会从核心拆出（后续以本 API 为第一消费者）
- 内置表迁移到插件命名空间
- 按插件独立 auth key / 跨插件读隔离策略
- 插件全服配置（shared settings）同步
- 中心侧 SQL 查询 API 暴露给第三方
- 插件表结构变更（ALTER）与版本迁移
- BATCH_SYNC 路径的插件表（outbox 单条 INSERT/DELETE 已覆盖）

## Tasks

- [x] T1: 协议 v4 与逻辑表名工具 — acceptance: `sync_protocol` 版本与可选 `table_name`/`plugin_tables` 字段；`sync_plugin_api` 校验/物理名/注册表单元测试通过 (covers: S2)
- [x] T2: 中心侧插件表路由 — acceptance: SyncServer 能按 auth 的 plugin_tables 建表，并对 `table_name` 做 insert/delete/full sync/broadcast，未授权表拒绝 (covers: S2; depends: T1)
- [x] T3: 客户端插件注册与回调 — acceptance: SyncClient 支持 register/unregister、auth 带表、full/push 进 `on_apply`、outbox 上行插件表；协议 <4 时不发送 (covers: S2; depends: T1)
- [x] T4: ARCCorePlugin 公开 API — acceptance: `api_sync_register_namespace` 等方法可用，未启用同步时 no-op 返回结构一致 (covers: S2; depends: T2, T3)
- [x] T5: 回归测试与验证 — acceptance: 新增单测覆盖逻辑名/注册/协议字段/中心解析；`python -m unittest discover tests` 通过 (covers: S2; depends: T1-T4)
