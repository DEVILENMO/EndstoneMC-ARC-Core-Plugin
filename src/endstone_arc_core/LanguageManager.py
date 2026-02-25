from pathlib import Path

MAIN_PATH = 'plugins/ARCCore'

class LanguageManager:
    language_dict = {}  # Class variable shared across instances

    def __init__(self, default_language_code):
        self.language_code = default_language_code.upper()
        if self.language_code not in LanguageManager.language_dict:
            LanguageManager.language_dict[self.language_code] = {}

        # Use Path for cross-platform compatibility
        self.language_file_path = Path(MAIN_PATH) / f"{self.language_code}.txt"
        self._load_language_file()

    def _load_language_file(self):
        # Create config directory if not exists
        self.language_file_path.parent.mkdir(exist_ok=True)

        # Create language file if not exists
        if not self.language_file_path.exists():
            self.language_file_path.touch()

        # Load language file content
        with self.language_file_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and "=" in line:
                    key, value = line.split("=", 1)
                    LanguageManager.language_dict[self.language_code][key.strip()] = value.strip()

    def GetText(self, key, lang_code=None):
        # If no language code provided, use instance's language code
        target_lang = (lang_code or self.language_code).upper()

        # If the target language hasn't been loaded yet, load it
        if target_lang not in LanguageManager.language_dict:
            LanguageManager(target_lang)  # 触发加载该语言文件到 language_dict

        # If key doesn't exist in target language, add it
        if key not in LanguageManager.language_dict[target_lang]:
            target_file_path = Path(MAIN_PATH) / f"{target_lang}.txt"
            with target_file_path.open("a", encoding="utf-8") as f:
                f.write(f"\n{key}=")
            LanguageManager.language_dict[target_lang][key] = ""

        if not LanguageManager.language_dict[target_lang][key]:
            print(f'[ARC Core]Key {key} not found in language file {target_lang}.txt.')
            return ''
        else:
            return LanguageManager.language_dict[target_lang][key].replace('\\n', '\n')

    def ReloadCurrentLanguage(self):
        """重新从文件加载当前语言（实例对应的 language_code）"""
        if self.language_code in LanguageManager.language_dict:
            LanguageManager.language_dict[self.language_code].clear()
        self._load_language_file()