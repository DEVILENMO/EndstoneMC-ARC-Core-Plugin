from pathlib import Path

MAIN_PATH = 'plugins/ARCCore'

class SettingManager:
    setting_dict = {}  # Class variable to store all settings

    def __init__(self):
        self.setting_file_path = Path(MAIN_PATH) / "core_setting.yml"
        self._load_setting_file()

    def _load_setting_file(self):
        # Create config directory if not exists
        self.setting_file_path.parent.mkdir(exist_ok=True)

        # Create settings file if not exists
        if not self.setting_file_path.exists():
            self.setting_file_path.touch()

        # Load settings file content
        with self.setting_file_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and "=" in line:
                    key, value = line.split("=", 1)
                    SettingManager.setting_dict[key.strip()] = value.strip()

    def GetSetting(self, key):
        # If key doesn't exist in settings, add it
        if key not in SettingManager.setting_dict:
            with self.setting_file_path.open("a", encoding="utf-8") as f:
                f.write(f"\n{key}=")
            SettingManager.setting_dict[key] = ""

        return None if not SettingManager.setting_dict[key] else SettingManager.setting_dict[key]

    def SetSetting(self, key, value):
        # Update setting in memory
        SettingManager.setting_dict[key] = str(value)

        # Rewrite entire file with updated settings
        with self.setting_file_path.open("w", encoding="utf-8") as f:
            for k, v in SettingManager.setting_dict.items():
                f.write(f"{k}={v}\n")

    def Reload(self):
        """重新从文件加载配置（清空内存后重新读取 core_setting.yml）"""
        SettingManager.setting_dict.clear()
        self._load_setting_file()