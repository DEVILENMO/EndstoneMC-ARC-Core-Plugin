# -*- coding: utf-8 -*-
"""恶性错误写入 plugins/ARCCore/error_log.txt（与核心配置同目录）"""
import threading
import traceback
from datetime import datetime
from pathlib import Path
from typing import List, Optional

_file_lock = threading.Lock()


def append_arc_error_log(
    log_file_path: str,
    error_code: str,
    detail: str,
    exception: Optional[BaseException] = None,
    extra_lines: Optional[List[str]] = None,
) -> None:
    """线程安全追加一行或多行可读记录到 error_log.txt。"""
    timestamp = datetime.now().isoformat(sep=" ", timespec="seconds")
    lines: List[str] = [f"[{timestamp}] {error_code}", f"  detail: {detail}"]
    if extra_lines:
        for line in extra_lines:
            lines.append(f"  {line}")
    if exception is not None:
        lines.append(f"  exception: {type(exception).__name__}: {exception}")
        try:
            tb = "".join(
                traceback.format_exception(
                    type(exception), exception, exception.__traceback__
                )
            )
            for tb_line in tb.rstrip().splitlines():
                lines.append(f"  traceback: {tb_line}")
        except Exception:
            pass
    lines.append("")
    text = "\n".join(lines)
    with _file_lock:
        try:
            path_obj = Path(log_file_path)
            path_obj.parent.mkdir(parents=True, exist_ok=True)
            with path_obj.open("a", encoding="utf-8") as log_file:
                log_file.write(text)
        except Exception:
            pass


def format_context_lines(context: Optional[dict]) -> Optional[List[str]]:
    if not context:
        return None
    result: List[str] = []
    for context_key, context_value in sorted(context.items(), key=lambda x: x[0]):
        result.append(f"{context_key}={context_value}")
    return result if result else None
