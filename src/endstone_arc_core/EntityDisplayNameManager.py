# -*- coding: utf-8 -*-
"""生物名称翻译：当生物名称中包含 ':' 时视为 MC 未提供对应翻译，从 entity_display_name.txt 读取用户配置的显示名。"""
from pathlib import Path
from typing import Optional


class EntityDisplayNameManager:
    """从 entity_display_name.txt 读取/补写生物显示名。仅对名称中含 ':' 的键进行查询。"""

    def __init__(self, base_path: Path, logger=None):
        self.base_path = Path(base_path)
        self.logger = logger
        self._file_path = self.base_path / "entity_display_name.txt"
        self._cache: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        """从文件加载 key=value，key 为生物原始名（如 entity.ns_ab:vfx_dragon_fire.name），value 为显示名。"""
        self._cache.clear()
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        if not self._file_path.exists():
            self._file_path.touch()
            with self._file_path.open("w", encoding="utf-8") as f:
                f.write("# 生物显示名翻译：每行 原始名称=显示名\n")
                f.write("# 当死亡播报等处的生物名称含有 ':' 时会在此查找；未找到的键会自动追加到文件末尾，请补写显示名。\n")
            return
        with self._file_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                self._cache[key.strip()] = value.strip()

    def get_display_name(self, raw_name: str) -> str:
        """
        获取生物显示名。若 raw_name 中含 ':'，则从 entity_display_name.txt 查找；
        若文件中有非空翻译则返回翻译，否则将键追加到文件中并返回 raw_name。
        """
        if not raw_name:
            return ""
        raw_name = str(raw_name).strip()
        if ":" not in raw_name:
            return raw_name
        if raw_name in self._cache:
            translated = self._cache[raw_name]
            if translated:
                return translated
            return raw_name
        self._append_key(raw_name)
        return raw_name

    def _append_key(self, key: str) -> None:
        """将未存在的键追加到文件末尾，便于用户补写翻译。"""
        self._cache[key] = ""
        try:
            with self._file_path.open("a", encoding="utf-8") as f:
                f.write(f"\n{key}=")
            if self.logger:
                self.logger.info(f"[ARC Core] 生物显示名未配置，已追加到 entity_display_name.txt: {key}")
        except Exception as e:
            if self.logger:
                self.logger.error(f"[ARC Core] 追加 entity_display_name 失败: {str(e)}")

    def reload(self) -> None:
        """重新从文件加载。"""
        self._load()

    def get_display_name_for_entity_type(self, entity_type_id: str) -> str:
        """
        根据类型 ID（如 minecraft:creeper）解析显示名。
        优先查 entity.minecraft.creeper.name 与文件中其它键；无则返回简短 ID（如 creeper）。
        """
        if not entity_type_id:
            return ""
        et = str(entity_type_id).strip()
        if ":" in et:
            ns, short = et.split(":", 1)
            key = f"entity.{ns}.{short}.name"
        else:
            short = et
            key = f"entity.minecraft.{short}.name"
        if key in self._cache and self._cache[key]:
            return self._cache[key]
        et_lower = et.lower()
        if et_lower in self._cache and self._cache[et_lower]:
            return self._cache[et_lower]
        if et in self._cache and self._cache[et]:
            return self._cache[et]
        if ":" in et:
            return et.split(":", 1)[1]
        return et
