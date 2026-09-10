# -*- coding: utf-8 -*-
"""跨服数据同步后端服务端"""
import json
import socket
import threading
import time
import uuid
from contextlib import suppress
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from dataclasses import dataclass, field

from endstone_arc_core.sync_config import (
    categories_from_tables,
    filter_incoming_settings,
    snapshot_shared_settings,
)
from endstone_arc_core.sync_plugin_api import (
    build_create_table_sql,
    is_plugin_logical_name,
    normalize_fields,
    normalize_primary_keys,
    parse_logical_name,
    physical_from_logical,
    select_all_physical_rows,
    query_physical_rows,
)
from endstone_arc_core.sync_protocol import (
    SyncMessageType,
    SyncTable,
    TABLE_TO_ENUM,
    ENUM_TO_TABLE,
    PROTOCOL_VERSION,
    REQUEST_TO_RESPONSE,
    decode_message,
    build_auth_response,
    build_query_response,
    build_data_response,
    build_batch_sync_response,
    build_full_sync_response,
    build_heartbeat,
    build_push_notify,
    build_error_response,
    build_settings_push,
)
from endstone_arc_core.sync_write import iter_mirror_write_actions, query_sync_table, select_all_sync_table


@dataclass(eq=False)
class ConnectedClient:
    """已连接的客户端（按对象身份参与 set，字段可变）"""
    conn: socket.socket
    addr: tuple
    server_id: str = ""
    server_name: str = ""
    authenticated: bool = False
    # 全量同步完成前不接受 PUSH，避免与 request/response 粘包错位
    accepts_push: bool = False
    last_heartbeat: float = field(default_factory=time.time)
    sync_tables: Set[str] = field(default_factory=set)
    # 第三方插件逻辑表名 plugin_id:table
    plugin_tables: Set[str] = field(default_factory=set)
    protocol_version: int = 1

    def is_alive(self) -> bool:
        """检查连接是否存活（心跳超时 60 秒）"""
        return time.time() - self.last_heartbeat < 60


class SyncServer:
    """跨服数据同步后端服务端
    
    运行在独立的线程中，接收来自多个插件端服务器的连接请求，
    并将数据变更同步给所有已连接的客户端。
    """

    def __init__(
        self,
        database_manager,
        auth_key: str = "",
        bind_host: str = "0.0.0.0",  # nosec B104 — 跨服同步中心需监听所有网卡
        bind_port: int = 19999,
        logger=None,
        setting_manager=None,
        on_economy_mutated: Optional[Callable[[], None]] = None,
    ):
        """
        初始化同步服务器
        
        :param database_manager: 数据库管理器实例
        :param auth_key: 认证密钥
        :param bind_host: 绑定地址
        :param bind_port: 绑定端口
        :param logger: 日志记录器
        :param setting_manager: 配置管理器（用于向从服下发玩法配置）
        :param on_economy_mutated: 从服成功写入 player_economy 后的回调（主服条件头衔刷新）
        """
        self.db = database_manager
        self.settings = setting_manager
        self.auth_key = auth_key
        self.bind_host = bind_host
        self.bind_port = bind_port
        self.logger = logger
        self._on_economy_mutated = on_economy_mutated
        
        self._socket: Optional[socket.socket] = None
        self._running = False
        self._server_thread: Optional[threading.Thread] = None
        self._clients: Set[ConnectedClient] = set()
        self._clients_lock = threading.Lock()
        
        # 需要同步的表列表
        self._sync_tables = set(TABLE_TO_ENUM.keys())
        # 逻辑表名 -> {"fields", "primary_keys"}（来自客户端 auth 或本机插件注册）
        self._plugin_schemas: Dict[str, Dict[str, Any]] = {}
        self._plugin_schema_lock = threading.Lock()

        # 变更记录队列（用于异步推送给客户端）
        self._change_queue: List[Dict[str, Any]] = []
        self._change_queue_lock = threading.Lock()

        # 全量同步锁（防止同步期间数据不一致）
        self._full_sync_lock = threading.Lock()

    def _log(self, level: str, message: str):
        """安全日志记录"""
        if self.logger:
            getattr(self.logger, level.lower(), self.logger.info)(f"[ARC SyncServer] {message}")
        else:
            print(f"[{level.upper()}] [ARC SyncServer] {message}")

    def start(self) -> bool:
        """启动同步服务器"""
        if self._running:
            self._log("warning", "Server is already running")
            return True
        
        try:
            self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._socket.bind((self.bind_host, self.bind_port))
            self._socket.listen(10)
            self._socket.settimeout(5.0)  # 5秒超时，用于检查_running标志
            
            self._running = True
            self._server_thread = threading.Thread(target=self._server_loop, daemon=True)
            self._server_thread.start()
            
            self._log("info", f"Sync server started on {self.bind_host}:{self.bind_port}")
            return True
        except Exception as e:
            self._log("error", f"Failed to start sync server: {e}")
            self._running = False
            return False

    def stop(self):
        """停止同步服务器"""
        if not self._running:
            return
        
        self._running = False
        
        # 关闭所有客户端连接
        with self._clients_lock:
            for client in self._clients:
                with suppress(OSError):
                    client.conn.close()
            self._clients.clear()

        if self._socket:
            with suppress(OSError):
                self._socket.close()
            self._socket = None
        
        if self._server_thread and self._server_thread.is_alive():
            self._server_thread.join(timeout=5)
        
        self._log("info", "Sync server stopped")

    def is_running(self) -> bool:
        """检查服务器是否运行中"""
        return self._running

    def _server_loop(self):
        """服务器主循环"""
        while self._running:
            try:
                client_socket, addr = self._socket.accept()
                client_thread = threading.Thread(
                    target=self._handle_client,
                    args=(client_socket, addr),
                    daemon=True
                )
                client_thread.start()
            except socket.timeout:
                continue
            except Exception as e:
                if self._running:
                    self._log("error", f"Accept connection error: {e}")
                break
        
        # 清理客户端
        self._cleanup_dead_clients()

    def _handle_client(self, conn: socket.socket, addr: tuple):
        """处理客户端连接"""
        client = ConnectedClient(conn=conn, addr=addr)
        buffer = b""
        
        try:
            conn.settimeout(30.0)
            
            while self._running:
                try:
                    data = conn.recv(4096)
                    if not data:
                        break
                    
                    buffer += data
                    
                    # 处理粘包
                    while len(buffer) >= 5:
                        msg_len = int.from_bytes(buffer[:4], 'big')
                        if len(buffer) < 5 + msg_len:
                            break  # 数据不完整，等待更多数据
                        
                        raw_msg = buffer[:5 + msg_len]
                        buffer = buffer[5 + msg_len:]
                        
                        self._process_message(client, raw_msg)
                        
                except socket.timeout:
                    if not client.authenticated:
                        break  # 未认证的客户端超时断开
                    continue
                except OSError as e:
                    # 关服时 stop() 会从其它线程 close 套接字；Windows 上 recv 常报
                    # WinError 10038（非套接字操作），属预期清理，勿当故障打 ERROR。
                    if self._running:
                        self._log("error", f"Client {addr} error: {e}")
                    break
                except Exception as e:
                    if self._running:
                        self._log("error", f"Client {addr} error: {e}")
                    break
            
        except Exception as e:
            if self._running:
                self._log("error", f"Client {addr} handler error: {e}")
        finally:
            # 移除客户端
            with self._clients_lock:
                self._clients.discard(client)
            try:
                conn.close()
            except Exception:
                pass
            if self._running:
                self._log("info", f"Client {addr} disconnected")

    def _process_message(self, client: ConnectedClient, raw_msg: bytes):
        """处理接收到的消息"""
        try:
            msg_type, data = decode_message(raw_msg)
            client.last_heartbeat = time.time()

            if msg_type == SyncMessageType.AUTH_REQUEST:
                self._handle_auth(client, data)
                return
            if msg_type == SyncMessageType.HEARTBEAT:
                self._handle_heartbeat(client)
                return
            if not client.authenticated:
                client.conn.sendall(build_error_response(1, "Not authenticated"))
                return

            handler = self._AUTHED_HANDLERS.get(msg_type)
            if handler:
                handler(self, client, data)
            else:
                client.conn.sendall(
                    build_error_response(2, f"Unknown message type: {msg_type}")
                )
        except Exception as e:
            self._log("error", f"Process message error: {e}")
            try:
                client.conn.sendall(build_error_response(3, str(e)))
            except Exception:
                pass

    # 已认证后的消息分发（避免长 elif 链抬高圈复杂度）
    _AUTHED_HANDLERS = {
        SyncMessageType.QUERY_REQUEST: lambda self, c, d: self._handle_query(c, d),
        SyncMessageType.INSERT_REQUEST: lambda self, c, d: self._handle_insert(c, d),
        SyncMessageType.UPDATE_REQUEST: lambda self, c, d: self._handle_update(c, d),
        SyncMessageType.DELETE_REQUEST: lambda self, c, d: self._handle_delete(c, d),
        SyncMessageType.BATCH_SYNC_REQUEST: lambda self, c, d: self._handle_batch_sync(c, d),
        SyncMessageType.FULL_SYNC_REQUEST: lambda self, c, d: self._handle_full_sync(c, d),
        SyncMessageType.PULL_REQUEST: lambda self, c, d: self._handle_pull(c, d),
        SyncMessageType.SETTINGS_PULL_REQUEST: lambda self, c, d: self._handle_settings_pull(c, d),
    }

    def _handle_auth(self, client: ConnectedClient, data: Dict):
        """处理认证请求"""
        server_id = data.get('server_id', '')
        server_name = data.get('server_name', '')
        auth_key = data.get('auth_key', '')

        if self.auth_key and auth_key != self.auth_key:
            client.conn.sendall(build_auth_response(False, "Invalid auth key"))
            self._log("warning", f"Auth failed for {client.addr}: invalid key")
            return

        client.server_id = server_id
        client.server_name = server_name
        client.authenticated = True
        try:
            client.protocol_version = int(data.get('protocol_version') or 1)
        except (TypeError, ValueError):
            client.protocol_version = 1

        requested_tables = data.get('sync_tables')
        if isinstance(requested_tables, list):
            # 空列表表示仅事件转发、不同步任何表
            client.sync_tables = {
                str(t) for t in requested_tables if str(t) in self._sync_tables
            }
        else:
            client.sync_tables = set(self._sync_tables)

        client.plugin_tables = self._absorb_plugin_tables(data.get('plugin_tables'))

        with self._clients_lock:
            self._clients.add(client)

        auth_settings = None
        if client.protocol_version >= 2:
            auth_settings = self._settings_for_client(client)
        client.conn.sendall(build_auth_response(
            True,
            "Authentication successful",
            settings=auth_settings,
            protocol_version=PROTOCOL_VERSION,
        ))
        self._log("info", f"Client authenticated: {server_name} ({server_id}) from {client.addr}")

    def _absorb_plugin_tables(self, raw: Any) -> Set[str]:
        """登记客户端声明的插件表并确保中心物理表存在（字段类型与主键均校验）。"""
        allowed: Set[str] = set()
        if not isinstance(raw, list):
            return allowed
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = str(item.get('name') or '').strip()
            parsed = parse_logical_name(name)
            if not parsed:
                continue
            fields = item.get('fields')
            pks = item.get('primary_keys')
            if not isinstance(fields, dict) or not fields:
                with self._plugin_schema_lock:
                    if name not in self._plugin_schemas:
                        continue
            else:
                try:
                    self._ensure_plugin_table(name, fields, pks)
                except Exception as e:
                    self._log("warning", f"Ensure plugin table {name} failed: {e}")
                    continue
            allowed.add(name)
        return allowed

    def register_plugin_namespace(
        self, logical_tables: Dict[str, Any]
    ) -> None:
        """本机插件注册：记录 schema 并建物理表（同步中心侧）。

        logical_tables: {logical_name: {"fields": {...}, "primary_keys": [...]}}
        或兼容旧调用 {logical_name: fields_dict}。
        """
        for name, meta in (logical_tables or {}).items():
            try:
                if isinstance(meta, dict) and "fields" in meta:
                    self._ensure_plugin_table(
                        name, meta.get("fields"), meta.get("primary_keys")
                    )
                else:
                    self._ensure_plugin_table(name, meta, None)
            except Exception as e:
                self._log("error", f"Register plugin table {name} error: {e}")

    def _ensure_plugin_table(
        self, logical: str, fields: Any, primary_keys: Any = None
    ) -> str:
        phys = physical_from_logical(logical)
        if not phys:
            raise ValueError(f"invalid plugin table name: {logical!r}")
        norm_fields = normalize_fields(fields)
        # 已有 schema 且未给 PK 时沿用旧 PK，避免重复注册丢主键
        with self._plugin_schema_lock:
            prev = self._plugin_schemas.get(logical) or {}
        if primary_keys is None and prev.get("primary_keys"):
            pks = list(prev["primary_keys"])
        else:
            pks = normalize_primary_keys(primary_keys, norm_fields)
        create_sql = build_create_table_sql(phys, norm_fields, pks)
        with self._plugin_schema_lock:
            self._plugin_schemas[logical] = {
                "fields": dict(norm_fields),
                "primary_keys": list(pks),
            }
        if not self.db.table_exists(phys):
            # 不用 create_table：需要表级复合主键
            if not self.db.execute(create_sql):
                raise RuntimeError(f"create plugin table failed: {phys}")
        return phys

    def _resolve_request_tables(
        self, client: ConnectedClient, data: Dict
    ) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """返回 (core_table, plugin_logical, physical)；均未授权时 physical 为 None。"""
        table_name = data.get('table_name')
        if is_plugin_logical_name(table_name):
            logical = str(table_name).strip()
            if client is not None and logical not in client.plugin_tables:
                return None, logical, None
            phys = physical_from_logical(logical)
            with self._plugin_schema_lock:
                known = logical in self._plugin_schemas
            if not phys or not known:
                return None, logical, None
            return None, logical, phys
        try:
            table_enum = SyncTable(data.get('table', 0))
        except ValueError:
            return None, None, None
        core = ENUM_TO_TABLE.get(table_enum)
        if core not in self._sync_tables:
            return None, None, None
        return core, None, core

    def _handle_heartbeat(self, client: ConnectedClient):
        """处理心跳包"""
        client.last_heartbeat = time.time()
        # 客户端进入 listen 循环后才会发心跳，此时全量同步已结束
        client.accepts_push = True
        try:
            client.conn.sendall(build_heartbeat())
        except Exception:
            pass


    def _handle_query(self, client: ConnectedClient, data: Dict):
        """处理查询请求"""
        try:
            core_table, plugin_logical, phys = self._resolve_request_tables(client, data)
            where = data.get('where', '1=1')
            params = data.get('params', [])

            if not phys:
                client.conn.sendall(build_query_response(False, [], "Table not allowed"))
                return

            if plugin_logical:
                results = query_physical_rows(self.db, phys, where, tuple(params))
            else:
                results = query_sync_table(self.db, core_table, where, tuple(params))
            client.conn.sendall(build_query_response(True, results))
        except Exception as e:
            client.conn.sendall(build_query_response(False, [], str(e)))
            self._log("error", f"Query error: {e}")

    def _notify_economy_mutated(self, table_name: Optional[str]) -> None:
        if table_name != "player_economy":
            return
        cb = self._on_economy_mutated
        if cb is None:
            return
        try:
            cb()
        except Exception as e:
            self._log("error", f"on_economy_mutated error: {e}")

    def _apply_client_mutation(
        self,
        client: ConnectedClient,
        data: Dict,
        *,
        log_label: str,
        mutate,
        push_op: str,
        push_data: Dict,
        request_type: SyncMessageType,
    ) -> None:
        """校验表权限 → 抑制通知写库 → 成功则广播 → 回响应（带 seq）。"""
        seq = data.get("seq")
        try:
            seq = int(seq) if seq is not None else None
        except (TypeError, ValueError):
            seq = None
        resp_type = REQUEST_TO_RESPONSE.get(
            request_type, SyncMessageType.INSERT_RESPONSE
        )
        try:
            core_table, plugin_logical, phys = self._resolve_request_tables(client, data)
            if not phys:
                client.conn.sendall(
                    build_data_response(
                        resp_type, False, 0, "Table not allowed", seq=seq
                    )
                )
                return
            with self.db.suppress_write_notify():
                success = mutate(phys)
            if success:
                self._broadcast_push_resolved(
                    core_table, plugin_logical, push_op, push_data, exclude=client
                )
                self._notify_economy_mutated(core_table)
            client.conn.sendall(
                build_data_response(
                    resp_type, success, 1 if success else 0, seq=seq
                )
            )
        except Exception as e:
            client.conn.sendall(
                build_data_response(resp_type, False, 0, str(e), seq=seq)
            )
            self._log("error", f"{log_label} error: {e}")

    def _handle_insert(self, client: ConnectedClient, data: Dict):
        """处理插入/整行 upsert 请求"""
        row_data = data.get('data', {})
        self._apply_client_mutation(
            client,
            data,
            log_label="Insert",
            mutate=lambda t: self.db.upsert(t, row_data),
            push_op="insert",
            push_data=row_data,
            request_type=SyncMessageType.INSERT_REQUEST,
        )

    def _handle_update(self, client: ConnectedClient, data: Dict):
        """处理更新请求"""
        row_data = data.get('data', {})
        where = data.get('where', '')
        params = data.get('params', [])
        self._apply_client_mutation(
            client,
            data,
            log_label="Update",
            mutate=lambda t: self.db.update(t, row_data, where, tuple(params)),
            push_op="update",
            push_data={**row_data, '_where': where, '_params': params},
            request_type=SyncMessageType.UPDATE_REQUEST,
        )

    def _handle_delete(self, client: ConnectedClient, data: Dict):
        """处理删除请求"""
        where = data.get('where', '')
        params = data.get('params', [])
        self._apply_client_mutation(
            client,
            data,
            log_label="Delete",
            mutate=lambda t: self.db.delete(t, where, tuple(params)),
            push_op="delete",
            push_data={'_where': where, '_params': params},
            request_type=SyncMessageType.DELETE_REQUEST,
        )

    def _run_batch_op(self, op_type: str, table_name: str, op: Dict) -> Dict:
        runners = {
            "insert": lambda: self.db.upsert(table_name, op.get("data", {})),
            "update": lambda: self.db.update(
                table_name,
                op.get("data", {}),
                op.get("where", ""),
                tuple(op.get("params", [])),
            ),
            "delete": lambda: self.db.delete(
                table_name, op.get("where", ""), tuple(op.get("params", []))
            ),
        }
        runner = runners.get(op_type)
        if not runner:
            return {"success": False, "error": "Unknown operation type"}
        with self.db.suppress_write_notify():
            return {"success": runner()}

    def _broadcast_batch_op(self, client: ConnectedClient, op: Dict) -> None:
        table_enum = SyncTable(op.get("table", 0))
        op_type = op.get("type")
        if op_type == "insert":
            self._broadcast_push(table_enum, "insert", op.get("data", {}), exclude=client)
        elif op_type == "update":
            self._broadcast_push(table_enum, "update", op.get("data", {}), exclude=client)
        elif op_type == "delete":
            self._broadcast_push(
                table_enum,
                "delete",
                {"_where": op.get("where", ""), "_params": op.get("params", [])},
                exclude=client,
            )

    def _handle_batch_sync(self, client: ConnectedClient, data: Dict):
        """处理批量同步请求"""
        try:
            operations = data.get("operations", [])
            results = []
            economy_touched = False
            for op in operations:
                table_name = ENUM_TO_TABLE.get(SyncTable(op.get("table", 0)))
                if table_name not in self._sync_tables:
                    results.append({"success": False, "error": "Table not allowed"})
                    continue
                result = self._run_batch_op(op.get("type"), table_name, op)
                results.append(result)
                if result.get("success") and table_name == "player_economy":
                    economy_touched = True

            client.conn.sendall(build_batch_sync_response(True, results))
            for op, result in zip(operations, results):
                if result.get("success"):
                    self._broadcast_batch_op(client, op)
            if economy_touched:
                self._notify_economy_mutated("player_economy")
        except Exception as e:
            client.conn.sendall(build_batch_sync_response(False, [], str(e)))
            self._log("error", f"Batch sync error: {e}")

    def _handle_full_sync(self, client: ConnectedClient, data: Dict):
        """处理全量同步请求。

        只在读库时短暂加锁，发送响应不占锁，避免多从服互相堵到超时。
        """
        try:
            core_table, plugin_logical, phys = self._resolve_request_tables(client, data)

            if not phys:
                client.conn.sendall(build_full_sync_response(False, [], "Table not allowed"))
                return

            with self._full_sync_lock:
                if plugin_logical:
                    rows = select_all_physical_rows(self.db, phys)
                else:
                    rows = select_all_sync_table(self.db, core_table)
            client.conn.sendall(build_full_sync_response(True, rows))
            label = plugin_logical or core_table
            self._log(
                "info",
                f"Full sync for {label}: {len(rows)} rows to {client.server_name}",
            )
        except Exception as e:
            try:
                client.conn.sendall(build_full_sync_response(False, [], str(e)))
            except Exception:
                pass
            self._log("error", f"Full sync error: {e}")

    def _handle_pull(self, client: ConnectedClient, data: Dict):
        """处理拉取请求"""
        try:
            core_table, plugin_logical, phys = self._resolve_request_tables(client, data)
            where = data.get('where', '1=1')
            params = data.get('params', [])

            if not phys:
                client.conn.sendall(build_query_response(False, [], "Table not allowed"))
                return

            if plugin_logical:
                results = query_physical_rows(self.db, phys, where, tuple(params))
            else:
                results = query_sync_table(self.db, core_table, where, tuple(params))
            client.conn.sendall(build_query_response(True, results))
        except Exception as e:
            client.conn.sendall(build_query_response(False, [], str(e)))

    def _settings_for_client(self, client: ConnectedClient) -> Dict[str, str]:
        if self.settings is None:
            return {}
        cats = categories_from_tables(client.sync_tables)
        return snapshot_shared_settings(self.settings, cats)

    def _handle_settings_pull(self, client: ConnectedClient, data: Dict) -> None:
        try:
            settings = self._settings_for_client(client)
            client.conn.sendall(build_settings_push(settings))
        except Exception as e:
            self._log("error", f"Settings pull error: {e}")

    def broadcast_settings(self, settings: Optional[Dict[str, str]] = None) -> None:
        """向协议版本 >=2 的从服推送玩法配置（可部分键；None 表示按客户端类别全量快照）。"""
        disconnected = []
        with self._clients_lock:
            for client in self._clients:
                if not client.authenticated or client.protocol_version < 2:
                    continue
                cats = categories_from_tables(client.sync_tables)
                if settings is None:
                    payload = self._settings_for_client(client)
                else:
                    payload = filter_incoming_settings(settings, cats)
                if not payload:
                    continue
                try:
                    client.conn.sendall(build_settings_push(payload))
                except Exception:
                    disconnected.append(client)
            for client in disconnected:
                self._clients.discard(client)

    def _broadcast_push(self, table: SyncTable, operation: str, data: Dict, exclude: Optional[ConnectedClient] = None):
        """广播内置表推送通知给所有已连接的客户端"""
        table_name = ENUM_TO_TABLE.get(table)
        self._broadcast_push_resolved(table_name, None, operation, data, exclude=exclude)

    def _broadcast_push_resolved(
        self,
        core_table: Optional[str],
        plugin_logical: Optional[str],
        operation: str,
        data: Dict,
        exclude: Optional[ConnectedClient] = None,
    ):
        """按 core 表名或插件逻辑表名广播 PUSH。"""
        table_enum = TABLE_TO_ENUM.get(core_table) if core_table else None
        msg = build_push_notify(
            table_enum if table_enum is not None else 0,
            operation,
            data,
            table_name=plugin_logical,
        )
        disconnected = []

        with self._clients_lock:
            for client in self._clients:
                if client is exclude:
                    continue
                if not client.accepts_push:
                    continue
                if plugin_logical:
                    if plugin_logical not in client.plugin_tables:
                        continue
                elif core_table and client.sync_tables and core_table not in client.sync_tables:
                    continue
                if not client.is_alive():
                    disconnected.append(client)
                    continue
                try:
                    client.conn.sendall(msg)
                except Exception:
                    disconnected.append(client)

            for client in disconnected:
                self._clients.discard(client)

    def apply_plugin_upsert(self, logical: str, row: Dict[str, Any]) -> bool:
        """同步中心本机插件写：落物理表并广播（不经 outbox）。"""
        if not is_plugin_logical_name(logical) or not row:
            return False
        with self._plugin_schema_lock:
            known = logical in self._plugin_schemas
        if not known:
            return False
        phys = physical_from_logical(logical)
        if not phys:
            return False
        with self.db.suppress_write_notify():
            ok = self.db.upsert(phys, dict(row))
        if ok:
            self._broadcast_push_resolved(None, logical, "insert", dict(row))
        return bool(ok)

    def apply_plugin_delete(
        self, logical: str, where: str, params: Optional[List[Any]] = None
    ) -> bool:
        """同步中心本机插件删：落物理表并广播。"""
        if not is_plugin_logical_name(logical) or not where or ";" in str(where):
            return False
        with self._plugin_schema_lock:
            known = logical in self._plugin_schemas
        if not known:
            return False
        phys = physical_from_logical(logical)
        if not phys:
            return False
        params_t = tuple(params or ())
        with self.db.suppress_write_notify():
            ok = self.db.delete(phys, str(where), params_t)
        if ok:
            self._broadcast_push_resolved(
                None,
                logical,
                "delete",
                {"_where": str(where), "_params": list(params_t)},
            )
        return bool(ok)

    def _cleanup_dead_clients(self):
        """清理已断开的客户端"""
        disconnected = []
        
        with self._clients_lock:
            for client in self._clients:
                if not client.is_alive():
                    disconnected.append(client)
            
            for client in disconnected:
                self._clients.discard(client)
                try:
                    client.conn.close()
                except Exception:
                    pass
        
        if disconnected:
            self._log("info", f"Cleaned up {len(disconnected)} dead clients")

    def mirror_local_write(self, kind: str, table: str, **kwargs) -> None:
        """主机本地写库后广播给已连接的子服（库已改好，只推送）。"""
        if table not in self._sync_tables:
            return
        table_enum = TABLE_TO_ENUM.get(table)
        if table_enum is None:
            return
        try:
            for action in iter_mirror_write_actions(self.db, kind, table, **kwargs):
                if action[0] == "delete":
                    _, where, params = action
                    self._broadcast_push(
                        table_enum, "delete", {"_where": where, "_params": params}
                    )
                else:
                    self._broadcast_push(table_enum, "insert", action[1])
        except Exception as e:
            self._log("error", f"Mirror local write {table}/{kind} error: {e}")


    def get_connected_count(self) -> int:
        """获取已连接的客户端数量"""
        with self._clients_lock:
            return len([c for c in self._clients if c.authenticated])

    def get_client_list(self) -> List[Dict[str, str]]:
        """获取客户端列表"""
        with self._clients_lock:
            return [
                {
                    'server_id': c.server_id,
                    'server_name': c.server_name,
                    'addr': f"{c.addr[0]}:{c.addr[1]}",
                    'last_heartbeat': c.last_heartbeat,
                }
                for c in self._clients if c.authenticated
            ]