# -*- coding: utf-8 -*-
"""跨服数据同步客户端：连接同步中心并拉取/接收推送；本地变更经 outbox 可靠上行。"""
import socket
import threading
import time
from contextlib import suppress
from typing import Any, Callable, Dict, Optional, Set

from endstone_arc_core import sync_outbox
from endstone_arc_core.sync_config import (
    filter_incoming_settings,
    get_client_sync_categories,
    get_client_sync_tables,
)
from endstone_arc_core.sync_plugin_api import (
    PluginSyncRegistry,
    is_plugin_logical_name,
)
from endstone_arc_core.sync_protocol import (
    SyncMessageType,
    SyncTable,
    TABLE_TO_ENUM,
    ENUM_TO_TABLE,
    PROTOCOL_VERSION,
    build_auth_request,
    build_data_request,
    build_full_sync_request,
    build_heartbeat,
    build_query_request,
    build_settings_pull_request,
    decode_message,
)
from endstone_arc_core.sync_write import iter_mirror_write_actions


class SyncClient:
    """连接远程同步中心，按配置分项同步数据到本地数据库。

    断线或主机不可达时，按 SYNC_CLIENT_RECONNECT_INTERVAL 秒定时重连。
    本地写库先入 sync_outbox，重连后按序重放，收到 ack 才删除。
    第三方插件表经 PluginSyncRegistry 注册，下行走 on_apply 回调。
    """

    def __init__(
        self,
        database_manager,
        setting_manager,
        logger=None,
        plugin_registry: Optional[PluginSyncRegistry] = None,
    ):
        self.db = database_manager
        self.settings = setting_manager
        self.logger = logger
        self.plugin_registry = plugin_registry or PluginSyncRegistry()

        self.server_ip = str(setting_manager.GetSetting("SYNC_SERVER_IP") or "127.0.0.1").strip()
        self.server_id = str(setting_manager.GetSetting("SYNC_CLIENT_SERVER_ID") or "server_001").strip()
        self.server_name = str(
            setting_manager.GetSetting("SYNC_CLIENT_SERVER_NAME") or "服务器01"
        ).strip()
        self.auth_key = str(setting_manager.GetSetting("SYNC_CLIENT_AUTH_KEY") or "").strip()
        self.server_port = self._setting_int("SYNC_CLIENT_PORT", 19999, fallback_key="SYNC_SERVER_PORT")
        self.reconnect_interval = max(1, self._setting_int("SYNC_CLIENT_RECONNECT_INTERVAL", 10))

        self.enabled_tables: Set[str] = get_client_sync_tables(setting_manager)
        self.enabled_categories: Set[str] = get_client_sync_categories(setting_manager)
        self._on_settings: Optional[Callable[[Dict[str, str]], None]] = None

        self._socket: Optional[socket.socket] = None
        self._active = False  # 希望保持连接（允许断线重连）
        self._worker_thread: Optional[threading.Thread] = None
        # 只保护 socket 指针与 recv 缓冲；禁止在持锁时 recv/dispatch/写库
        self._socket_lock = threading.Lock()
        self._outbox_lock = threading.Lock()
        self._pending_acks: Set[int] = set()
        self._server_protocol_version = 1
        self._last_error = ""
        self._flushing = False
        # 主线程 mirror 只入队；后台监听循环被唤醒后 flush，避免主线程 sendall
        self._outbox_wake = threading.Event()
        # TCP 粘包缓冲：跨多次 recv / request-response 保留半包，避免错位超时
        self._recv_buffer = b""
        self._connect_timeout = 30.0
        self._full_sync_timeout = 120.0
        self._listen_recv_timeout = 1.0

        try:
            sync_outbox.ensure_outbox_table(self.db)
        except Exception:
            pass

    def _setting_int(self, key: str, default: int, fallback_key: str = "") -> int:
        raw = self.settings.GetSetting(key)
        if (raw is None or not str(raw).strip()) and fallback_key:
            raw = self.settings.GetSetting(fallback_key)
        try:
            return int(raw) if raw is not None and str(raw).strip() else default
        except (ValueError, TypeError):
            return default

    def _log(self, level: str, message: str) -> None:
        if self.logger:
            getattr(self.logger, level.lower(), self.logger.info)(f"[ARC SyncClient] {message}")
        else:
            print(f"[{level.upper()}] [ARC SyncClient] {message}")

    def start(self) -> bool:
        """启动客户端后台线程（断线后自动重连）。"""
        if self._active:
            return True
        if not self.enabled_tables and not self.plugin_registry.logical_tables():
            self._log(
                "warning",
                "No sync categories enabled (SYNC_CLIENT_SYNC_* all False) "
                "and no plugin tables registered; client not started",
            )
            return False

        self._active = True
        self._worker_thread = threading.Thread(target=self._run_loop, daemon=True)
        self._worker_thread.start()
        self._log(
            "info",
            f"Sync client started → {self.server_ip}:{self.server_port} "
            f"(reconnect every {self.reconnect_interval}s)",
        )
        return True

    def notify_registry_changed(self) -> None:
        """插件表注册变更后触发重连，以便重新 auth + 全量。"""
        if self.is_running():
            self._close_socket()
            self._outbox_wake.set()

    def enqueue_plugin_upsert(self, logical: str, row: Dict[str, Any]) -> Optional[int]:
        """插件本地写后整行 upsert 入 outbox。"""
        if not is_plugin_logical_name(logical) or not row:
            return None
        if logical not in self.plugin_registry.logical_tables():
            return None
        seq = sync_outbox.enqueue(self.db, logical, "insert", {"row": dict(row)})
        if seq is not None:
            self._outbox_wake.set()
        return seq

    def enqueue_plugin_delete(
        self, logical: str, where: str, params: Optional[list] = None
    ) -> Optional[int]:
        if not is_plugin_logical_name(logical) or not where:
            return None
        if logical not in self.plugin_registry.logical_tables():
            return None
        seq = sync_outbox.enqueue(
            self.db,
            logical,
            "delete",
            {"where": str(where), "params": list(params or [])},
        )
        if seq is not None:
            self._outbox_wake.set()
        return seq

    def set_settings_callback(self, callback: Optional[Callable[[Dict[str, str]], None]]) -> None:
        self._on_settings = callback

    def _apply_remote_settings(self, raw: Any) -> None:
        settings = filter_incoming_settings(raw, self.enabled_categories)
        if not settings or self._on_settings is None:
            return
        try:
            self._on_settings(settings)
        except Exception as e:
            self._log("error", f"Apply remote settings error: {e}")

    def request_settings(self) -> None:
        """向同步中心请求当前玩法配置（重载本地配置后用于重新覆盖）。"""
        if not self.is_running():
            return
        try:
            self._send(build_settings_pull_request())
        except Exception as e:
            self._log("error", f"Request settings error: {e}")

    def pull_rows(
        self,
        table: str,
        where: str = "1=1",
        params: Optional[list] = None,
        timeout: Optional[float] = None,
    ) -> Optional[list]:
        """向同步中心查询行（不写本地库）。未连接返回 None。"""
        if not self.is_running() or table not in self.enabled_tables:
            return None
        table_enum = TABLE_TO_ENUM.get(table)
        if table_enum is None:
            return None
        try:
            msg_type, data = self._request_response(
                build_query_request(table_enum, where, list(params or [])),
                expect_types={SyncMessageType.QUERY_RESPONSE},
                timeout=timeout if timeout is not None else 8.0,
            )
            if msg_type != SyncMessageType.QUERY_RESPONSE:
                return None
            if not data.get("success"):
                self._log(
                    "warning",
                    f"Pull {table} failed: {data.get('error', '')}",
                )
                return None
            rows = data.get("results")
            return list(rows) if isinstance(rows, list) else []
        except Exception as e:
            self._log("warning", f"Pull {table} error: {e}")
            return None

    def pull_one(
        self,
        table: str,
        where: str,
        params: Optional[list] = None,
        timeout: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        rows = self.pull_rows(table, where, params, timeout=timeout)
        if not rows:
            return None
        row = rows[0]
        return dict(row) if isinstance(row, dict) else None

    def stop(self) -> None:
        """停止客户端并取消重连。"""
        self._active = False
        self._close_socket()
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=self.reconnect_interval + 5)
        self._worker_thread = None

    def is_active(self) -> bool:
        """是否已启动（含断线重连等待中）。"""
        return self._active

    def is_running(self) -> bool:
        """当前是否已与同步中心建立连接。"""
        with self._socket_lock:
            return self._active and self._socket is not None

    def get_status(self) -> Dict[str, Any]:
        """供 OP 面板展示的状态快照。"""
        pending = 0
        try:
            pending = sync_outbox.count_pending(self.db)
        except Exception:
            pending = 0
        err = self._last_error or sync_outbox.latest_error(self.db)
        return {
            "active": self._active,
            "connected": self.is_running(),
            "server": f"{self.server_ip}:{self.server_port}",
            "server_id": self.server_id,
            "server_name": self.server_name,
            "server_protocol": self._server_protocol_version,
            "outbox_pending": pending,
            "last_error": err,
            "enabled_tables": sorted(self.enabled_tables),
            "plugin_tables": sorted(self.plugin_registry.logical_tables()),
        }

    def mirror_local_write(self, kind: str, table: str, **kwargs) -> None:
        """本地业务写库后只入 outbox；由后台监听循环 flush，避免主线程抢 socket 锁/sendall。"""
        if table not in self.enabled_tables:
            return
        table_enum = TABLE_TO_ENUM.get(table)
        if table_enum is None:
            return
        queued = False
        try:
            for action in iter_mirror_write_actions(self.db, kind, table, **kwargs):
                if action[0] == "delete":
                    _, where, params = action
                    payload = {"where": where, "params": list(params or [])}
                    seq = sync_outbox.enqueue(self.db, table, "delete", payload)
                else:
                    payload = {"row": dict(action[1])}
                    seq = sync_outbox.enqueue(self.db, table, "insert", payload)
                if seq is None:
                    self._log("error", f"Outbox enqueue failed for {table}/{kind}")
                    continue
                queued = True
            if queued:
                self._outbox_wake.set()
        except Exception as e:
            self._last_error = str(e)
            self._log("error", f"Mirror local write {table}/{kind} error: {e}")

    def _send_outbox_item(
        self, seq: int, table: str, op: str, payload: Dict[str, Any]
    ) -> bool:
        use_ack = self._server_protocol_version >= 3
        try:
            if is_plugin_logical_name(table):
                if table not in self.plugin_registry.logical_tables():
                    sync_outbox.delete_seq(self.db, seq)
                    return True
                if self._server_protocol_version < 4:
                    # 不烧 attempts：中心升级到 v4 后可继续发送
                    self._last_error = "hub protocol < 4; plugin table sync skipped"
                    return True
                table_enum = 0
                table_name = table
            else:
                table_enum = TABLE_TO_ENUM.get(table)
                if table_enum is None:
                    sync_outbox.delete_seq(self.db, seq)
                    return True
                table_name = None

            if op == "delete":
                msg = build_data_request(
                    SyncMessageType.DELETE_REQUEST,
                    table_enum,
                    {},
                    where=str(payload.get("where") or ""),
                    params=list(payload.get("params") or []),
                    seq=seq if use_ack else None,
                    table_name=table_name,
                )
            else:
                msg = build_data_request(
                    SyncMessageType.INSERT_REQUEST,
                    table_enum,
                    dict(payload.get("row") or {}),
                    seq=seq if use_ack else None,
                    table_name=table_name,
                )
            self._send(msg)
            if use_ack:
                with self._outbox_lock:
                    self._pending_acks.add(int(seq))
            else:
                # 旧中心：发出即成功
                sync_outbox.delete_seq(self.db, seq)
            return True
        except Exception as e:
            self._last_error = str(e)
            attempts = sync_outbox.mark_attempt(self.db, seq, str(e))
            if attempts >= sync_outbox.OUTBOX_MAX_ATTEMPTS:
                self._log(
                    "error",
                    f"Outbox seq={seq} exceeded max attempts ({attempts}): {e}",
                )
            return False

    def flush_outbox(self) -> int:
        """按 seq 升序重放 outbox；返回本次尝试发送条数。"""
        if not self.is_running() or self._flushing:
            return 0
        self._flushing = True
        sent = 0
        try:
            with self._outbox_lock:
                pending_now = set(self._pending_acks)
            for item in sync_outbox.list_pending(self.db, limit=500):
                seq = int(item.get("seq") or 0)
                if not seq or seq in pending_now:
                    continue
                if int(item.get("attempts") or 0) >= sync_outbox.OUTBOX_MAX_ATTEMPTS:
                    continue
                table = str(item.get("table_name") or "")
                op = str(item.get("op") or "")
                payload = item.get("payload") or {}
                if is_plugin_logical_name(table):
                    if table not in self.plugin_registry.logical_tables():
                        sync_outbox.delete_seq(self.db, seq)
                        continue
                elif table not in self.enabled_tables:
                    sync_outbox.delete_seq(self.db, seq)
                    continue
                if self._send_outbox_item(seq, table, op, payload):
                    sent += 1
                else:
                    break
        finally:
            self._flushing = False
        return sent

    def _handle_data_ack(self, data: Dict[str, Any]) -> None:
        seq_raw = data.get("seq")
        if seq_raw is None:
            return
        try:
            seq = int(seq_raw)
        except (TypeError, ValueError):
            return
        with self._outbox_lock:
            self._pending_acks.discard(seq)
        if data.get("success"):
            sync_outbox.delete_seq(self.db, seq)
            return
        err = str(data.get("error") or "remote write failed")
        self._last_error = err
        attempts = sync_outbox.mark_attempt(self.db, seq, err)
        self._log(
            "warning",
            f"Outbox ack failure seq={seq} attempts={attempts}: {err}",
        )

    def enqueue_upsert_row(self, table: str, row: Dict[str, Any]) -> Optional[int]:
        """对账用：把一行整行 upsert 入 outbox（不依赖写通知）。"""
        if table not in self.enabled_tables or not row:
            return None
        return sync_outbox.enqueue(self.db, table, "insert", {"row": dict(row)})

    def _close_socket(self) -> None:
        with self._socket_lock:
            sock = self._socket
            self._socket = None
            self._recv_buffer = b""
        with self._outbox_lock:
            self._pending_acks.clear()
        if sock:
            with suppress(OSError):
                sock.close()

    def _interruptible_sleep(self, seconds: int) -> None:
        end = time.time() + seconds
        while self._active and time.time() < end:
            time.sleep(min(1.0, end - time.time()))

    def _run_loop(self) -> None:
        """连接 → 监听；失败或断线后等待间隔再重连。"""
        while self._active:
            try:
                if self._connect_session():
                    self._listen_loop()
            except Exception as e:
                if self._active:
                    self._log("error", f"Session error: {e}")
            finally:
                self._close_socket()

            if not self._active:
                break
            self._log(
                "info",
                f"Disconnected; retry in {self.reconnect_interval}s "
                f"→ {self.server_ip}:{self.server_port}",
            )
            self._interruptible_sleep(self.reconnect_interval)

        self._log("info", "Sync client stopped")

    def _connect_session(self) -> bool:
        """建立连接、认证、全量同步、flush outbox。成功返回 True。"""
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self._connect_timeout)
            sock.connect((self.server_ip, self.server_port))
            with self._socket_lock:
                self._socket = sock
                self._recv_buffer = b""
            sock = None  # ownership transferred

            if not self._authenticate():
                self._log("error", "Authentication failed")
                return False

            # 先拉全量（中心→本地），再把本地已启用表整表入 outbox 上行（全面对账），最后 flush。
            self._perform_full_sync()
            queued = self._enqueue_local_tables_for_reconcile(set(self.enabled_tables))
            if queued:
                self._log(
                    "info",
                    f"Auto-reconcile: enqueued {queued} local row(s) "
                    f"from {len(self.enabled_tables)} table(s)",
                )
            flushed = self.flush_outbox()
            if flushed:
                self._log("info", f"Flushed {flushed} outbox item(s) after connect")

            with self._socket_lock:
                if self._socket:
                    self._socket.settimeout(5.0)

            extras = []
            if self.enabled_tables:
                extras.append(f"syncing {len(self.enabled_tables)} table(s)")
            self._log(
                "info",
                f"Connected to {self.server_ip}:{self.server_port}"
                + (f"; {'; '.join(extras)}" if extras else ""),
            )
            return True
        except Exception as e:
            self._log("warning", f"Connect failed: {e}")
            return False
        finally:
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass

    def _send(self, payload: bytes) -> None:
        """短持锁取出 socket 后 sendall；不在持锁时阻塞 recv。"""
        with self._socket_lock:
            sock = self._socket
        if not sock:
            raise ConnectionError("Sync client socket is closed")
        sock.sendall(payload)

    def _pop_complete_message(self) -> Optional[tuple]:
        """从 _recv_buffer 弹出一条完整消息；数据不足则返回 None。须持有 _socket_lock。"""
        if len(self._recv_buffer) < 5:
            return None
        msg_len = int.from_bytes(self._recv_buffer[:4], "big")
        if msg_len < 0 or msg_len > 64 * 1024 * 1024:
            raise ConnectionError(f"Invalid sync message length: {msg_len}")
        if len(self._recv_buffer) < 5 + msg_len:
            return None
        raw = self._recv_buffer[: 5 + msg_len]
        self._recv_buffer = self._recv_buffer[5 + msg_len :]
        return decode_message(raw)

    def _drain_complete_messages_locked(self) -> list:
        """弹出缓冲中所有完整消息。须持有 _socket_lock。"""
        msgs = []
        while True:
            msg = self._pop_complete_message()
            if msg is None:
                break
            msgs.append(msg)
        return msgs

    def _append_recv_chunk(self, sock: socket.socket, chunk: bytes) -> list:
        """把 chunk 写入缓冲并弹出完整消息；socket 已更换则丢弃。"""
        with self._socket_lock:
            if self._socket is not sock:
                return []
            self._recv_buffer += chunk
            return self._drain_complete_messages_locked()

    def _recv_one_message(self, sock: socket.socket, timeout: float) -> Optional[tuple]:
        """读满一条消息（不持锁阻塞 recv）。返回 None 表示对端关闭。"""
        while True:
            with self._socket_lock:
                if self._socket is not sock:
                    raise ConnectionError("Sync client socket is closed")
                msg = self._pop_complete_message()
                if msg is not None:
                    return msg
            sock.settimeout(max(0.05, float(timeout)))
            chunk = sock.recv(65536)
            if not chunk:
                return None
            with self._socket_lock:
                if self._socket is not sock:
                    raise ConnectionError("Sync client socket is closed")
                self._recv_buffer += chunk

    def _handle_side_message_during_request(self, msg_type, data: Dict[str, Any]) -> None:
        """连接阶段等待响应时穿插到的 PUSH/心跳等，就地消化，避免帧错位。"""
        if msg_type == SyncMessageType.PUSH_NOTIFY:
            try:
                table_name = data.get("table_name")
                table_enum = None
                if not table_name:
                    try:
                        table_enum = SyncTable(data.get("table", 0))
                    except ValueError:
                        table_enum = None
                self._apply_push(
                    table_enum,
                    data.get("operation", ""),
                    data.get("data", {}),
                    table_name=table_name,
                )
            except Exception as e:
                self._log("warning", f"Side PUSH during request ignored error: {e}")
        elif msg_type == SyncMessageType.SETTINGS_PUSH:
            self._apply_remote_settings(data.get("settings"))
        elif msg_type in (
            SyncMessageType.INSERT_RESPONSE,
            SyncMessageType.UPDATE_RESPONSE,
            SyncMessageType.DELETE_RESPONSE,
            SyncMessageType.ERROR_RESPONSE,
        ):
            if "success" in data:
                self._handle_data_ack(data)
        elif msg_type == SyncMessageType.HEARTBEAT:
            return
        else:
            self._log(
                "warning",
                f"Ignoring unexpected side message during request: {msg_type}",
            )

    def _request_response(
        self,
        payload: bytes,
        *,
        expect_types: Optional[Set[SyncMessageType]] = None,
        timeout: Optional[float] = None,
    ) -> tuple:
        """发送请求并等待期望类型的响应；中途 PUSH/心跳不打断帧同步。

        recv 不持有 _socket_lock，避免连接/全量同步阶段卡住其它短临界区。
        """
        wait = self._connect_timeout if timeout is None else float(timeout)
        deadline = time.time() + wait
        with self._socket_lock:
            sock = self._socket
        if not sock:
            raise ConnectionError("Sync client socket is closed")
        sock.sendall(payload)
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                raise TimeoutError("timed out")
            try:
                result = self._recv_one_message(sock, remaining)
            except socket.timeout:
                raise TimeoutError("timed out") from None
            if result is None:
                raise ConnectionError("Sync server closed connection")
            msg_type, data = result
            if expect_types is None or msg_type in expect_types:
                return result
            self._handle_side_message_during_request(msg_type, data)

    def _authenticate(self) -> bool:
        payload = build_auth_request(
            self.server_id,
            self.server_name,
            self.auth_key,
            sorted(self.enabled_tables),
            protocol_version=PROTOCOL_VERSION,
            plugin_tables=self.plugin_registry.auth_plugin_tables_payload() or None,
        )
        msg_type, data = self._request_response(
            payload,
            expect_types={SyncMessageType.AUTH_RESPONSE},
            timeout=self._connect_timeout,
        )
        if msg_type != SyncMessageType.AUTH_RESPONSE:
            self._log("error", f"Unexpected auth response type: {msg_type}")
            return False
        if not data.get("success"):
            self._log("error", f"Authentication failed: {data.get('message', '')}")
            return False
        try:
            self._server_protocol_version = int(
                data.get("protocol_version") or 1
            )
        except (TypeError, ValueError):
            self._server_protocol_version = 1
        settings = data.get("settings")
        if settings:
            self._apply_remote_settings(settings)
        return True

    def _perform_full_sync(self) -> None:
        for table_name in sorted(self.enabled_tables):
            table_enum = TABLE_TO_ENUM.get(table_name)
            if table_enum is None:
                continue
            self._full_sync_one(table_name, table_enum, None)

        plugin_tables = sorted(self.plugin_registry.logical_tables())
        if plugin_tables and self._server_protocol_version < 4:
            self._log(
                "warning",
                f"Hub protocol {self._server_protocol_version} < 4; "
                f"skip full sync for {len(plugin_tables)} plugin table(s)",
            )
            return
        for logical in plugin_tables:
            self._full_sync_one(logical, 0, logical)

    def _full_sync_one(
        self, label: str, table_enum, table_name: Optional[str]
    ) -> None:
        try:
            msg_type, data = self._request_response(
                build_full_sync_request(table_enum, table_name=table_name),
                expect_types={SyncMessageType.FULL_SYNC_RESPONSE},
                timeout=self._full_sync_timeout,
            )
            if msg_type != SyncMessageType.FULL_SYNC_RESPONSE:
                self._log(
                    "warning",
                    f"Full sync {label}: unexpected response {msg_type}",
                )
                return
            if not data.get("success"):
                self._log(
                    "warning",
                    f"Full sync {label} failed: {data.get('error', '')}",
                )
                return
            rows = data.get("rows", [])
            if table_name:
                applied = self._apply_plugin_rows_full(table_name, rows)
            else:
                applied = 0
                for row in rows:
                    if self._upsert_row(label, row):
                        applied += 1
            self._log(
                "info",
                f"Full sync {label}: {applied}/{len(rows)} rows applied",
            )
        except Exception as e:
            self._log("error", f"Full sync {label} error: {e}")

    def _apply_plugin_rows_full(self, logical: str, rows: list) -> int:
        payload_rows = [dict(r) for r in (rows or []) if isinstance(r, dict)]
        try:
            ok = self.plugin_registry.dispatch_apply(
                logical, "full", {"rows": payload_rows}
            )
            return len(payload_rows) if ok else 0
        except Exception as e:
            self._log("error", f"Plugin full apply {logical} error: {e}")
            return 0

    def reconcile_tables(self, tables: Optional[Set[str]] = None) -> Dict[str, int]:
        """手动触发全面对账：断线重连后自动「拉全量 + 本地全表上行」。

        tables 参数保留兼容，实际始终对账全部已启用同步表。
        """
        _ = tables
        pending = 0
        try:
            pending = sync_outbox.count_pending(self.db)
        except Exception:
            pending = 0
        if self._active:
            self._close_socket()
        return {
            "requested_tables": len(self.enabled_tables),
            "outbox_pending": pending,
        }

    def _enqueue_local_tables_for_reconcile(self, tables: Set[str]) -> int:
        from endstone_arc_core.sync_write import select_all_sync_table

        queued = 0
        for table_name in sorted(tables):
            if table_name not in self.enabled_tables:
                continue
            try:
                for row in select_all_sync_table(self.db, table_name):
                    if self.enqueue_upsert_row(table_name, row):
                        queued += 1
            except Exception as e:
                self._log("error", f"Reconcile enqueue {table_name} error: {e}")
        return queued

    def _upsert_row(self, table: str, row: Dict[str, Any]) -> bool:
        if not row:
            return False
        with self.db.suppress_write_notify():
            return self.db.upsert(table, row)

    def _apply_push_update(self, table_name: str, data: Dict[str, Any]) -> None:
        row_data = {k: v for k, v in data.items() if not k.startswith("_")}
        where = data.get("_where", "")
        params = tuple(data.get("_params", []))
        if where and row_data:
            self.db.update(table_name, row_data, where, params)
        elif row_data:
            self._upsert_row(table_name, row_data)

    def _apply_push_delete(self, table_name: str, data: Dict[str, Any]) -> None:
        where = data.get("_where", "")
        if where:
            self.db.delete(table_name, where, tuple(data.get("_params", [])))

    def _apply_push(
        self,
        table_enum,
        operation: str,
        data: Dict[str, Any],
        table_name: Optional[str] = None,
    ) -> None:
        if table_name and is_plugin_logical_name(table_name):
            if table_name not in self.plugin_registry.logical_tables():
                return
            apply_op = {"insert": "upsert", "update": "upsert", "delete": "delete"}.get(
                operation
            )
            if not apply_op:
                return
            try:
                self.plugin_registry.dispatch_apply(table_name, apply_op, dict(data or {}))
            except Exception as e:
                self._log(
                    "error", f"Apply plugin push {table_name}/{operation} error: {e}"
                )
            return

        table = None
        if table_name and table_name in self.enabled_tables:
            table = table_name
        elif table_enum is not None:
            try:
                table = ENUM_TO_TABLE.get(table_enum)
            except Exception:
                table = None
        if not table or table not in self.enabled_tables:
            return
        apply_fn = {
            "insert": lambda: self._upsert_row(table, data),
            "update": lambda: self._apply_push_update(table, data),
            "delete": lambda: self._apply_push_delete(table, data),
        }.get(operation)
        if not apply_fn:
            return
        try:
            with self.db.suppress_write_notify():
                apply_fn()
        except Exception as e:
            self._log("error", f"Apply push {table}/{operation} error: {e}")

    def _dispatch_listen_message(self, msg_type, data: Dict[str, Any], heartbeat_ts: float) -> float:
        """处理监听循环中的单条消息，返回可能更新后的 heartbeat 时间戳。"""
        if msg_type == SyncMessageType.PUSH_NOTIFY:
            try:
                table_name = data.get("table_name")
                table_enum = None
                if not table_name:
                    try:
                        table_enum = SyncTable(data.get("table", 0))
                    except ValueError:
                        table_enum = None
                self._apply_push(
                    table_enum,
                    data.get("operation", ""),
                    data.get("data", {}),
                    table_name=table_name,
                )
            except Exception as e:
                self._log("error", f"Dispatch PUSH error: {e}")
        elif msg_type == SyncMessageType.SETTINGS_PUSH:
            self._apply_remote_settings(data.get("settings"))
        elif msg_type in (
            SyncMessageType.INSERT_RESPONSE,
            SyncMessageType.UPDATE_RESPONSE,
            SyncMessageType.DELETE_RESPONSE,
            # 兼容旧中心误用 ERROR_RESPONSE 回写操作结果
            SyncMessageType.ERROR_RESPONSE,
        ):
            if "success" in data:
                self._handle_data_ack(data)
        elif msg_type == SyncMessageType.HEARTBEAT:
            return time.time()
        return heartbeat_ts

    def _listen_loop(self) -> None:
        last_heartbeat = time.time()
        last_flush = time.time()
        while self._active:
            with self._socket_lock:
                sock = self._socket
            if not sock:
                break
            try:
                now = time.time()
                if now - last_heartbeat > 30:
                    try:
                        self._send(build_heartbeat())
                    except Exception:
                        break
                    last_heartbeat = now
                # 主线程入队后立即 flush；否则最多约 15s 兜底一次
                if self._outbox_wake.is_set() or (now - last_flush > 15):
                    self._outbox_wake.clear()
                    self.flush_outbox()
                    last_flush = now

                pending: list = []
                need_recv = False
                with self._socket_lock:
                    if self._socket is not sock:
                        break
                    pending = self._drain_complete_messages_locked()
                    if not pending:
                        need_recv = True

                if need_recv:
                    try:
                        sock.settimeout(self._listen_recv_timeout)
                        chunk = sock.recv(65536)
                    except socket.timeout:
                        chunk = None
                    if chunk is not None:
                        if not chunk:
                            break
                        pending = self._append_recv_chunk(sock, chunk)

                # dispatch / 写库绝不持有 _socket_lock
                for msg_type, data in pending:
                    last_heartbeat = self._dispatch_listen_message(
                        msg_type, data, last_heartbeat
                    )
            except socket.timeout:
                continue
            except Exception as e:
                if self._active:
                    self._log("error", f"Listen loop error: {e}")
                break

        self._log("info", "Sync client session ended")
