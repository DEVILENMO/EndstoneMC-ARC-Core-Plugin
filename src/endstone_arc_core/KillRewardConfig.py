# -*- coding: utf-8 -*-
"""击杀生物金钱奖励：独立配置文件 kill_reward.txt，格式 类型ID=金额（如 minecraft:creeper=10）。"""
import threading
from pathlib import Path
from typing import Dict


def normalize_entity_type_id(entity_type: str) -> str:
    """统一为小写，便于配置键一致。"""
    s = str(entity_type or "").strip()
    if not s:
        return ""
    if ":" in s:
        ns, name = s.split(":", 1)
        return f"{ns.lower()}:{name.lower()}"
    return s.lower()


class KillRewardConfig:
    """读取/补写 kill_reward.txt；未知生物首次击杀时追加为 0 元。"""

    def __init__(self, base_path: Path, logger=None):
        self.base_path = Path(base_path)
        self.logger = logger
        self._file_path = self.base_path / "kill_reward.txt"
        self._lock = threading.Lock()
        self._data: Dict[str, float] = {}
        self._load()

    def _load(self) -> None:
        self._data.clear()
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        if not self._file_path.exists():
            self._file_path.write_text(
                "# 击杀生物金钱奖励：每行 生物类型ID=金额（如 minecraft:creeper=10）\n"
                "# 首次击杀未列出的生物时，会自动追加一行 类型ID=0，可在文件中修改金额后重载或重启生效。\n",
                encoding="utf-8",
            )
            return
        with self._file_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = normalize_entity_type_id(key.strip())
                if not key:
                    continue
                try:
                    self._data[key] = round(float(value.strip()), 2)
                except (ValueError, TypeError):
                    self._data[key] = 0.0

    def reload(self) -> None:
        with self._lock:
            self._load()

    def get_reward_and_ensure_key(self, entity_type: str) -> float:
        """
        返回该类型击杀奖励金额；若配置中无此键则追加 entity_type=0 并返回 0。
        """
        key = normalize_entity_type_id(entity_type)
        if not key:
            return 0.0
        with self._lock:
            if key in self._data:
                return float(self._data[key])
            self._append_line(key, 0.0)
            self._data[key] = 0.0
            return 0.0

    def _append_line(self, entity_key: str, reward: float) -> None:
        try:
            with self._file_path.open("a", encoding="utf-8") as f:
                f.write(f"\n{entity_key}={reward}")
            if self.logger:
                self.logger.info(f"[ARC Core] kill_reward.txt 已追加: {entity_key}={reward}")
        except Exception as e:
            if self.logger:
                self.logger.error(f"[ARC Core] 写入 kill_reward.txt 失败: {e}")
