"""
Config access for the settings GUI.

Reads/writes the daemon's own config.json in place — same keys, same
format, unknown keys preserved. Defaults are imported from the daemon's
ConfigManager so the GUI and daemon can never disagree about them.
"""

import json
import sys
from pathlib import Path

LIB_DIR = Path(__file__).resolve().parent.parent
if str(LIB_DIR / 'src') not in sys.path:
    sys.path.insert(0, str(LIB_DIR / 'src'))

from paths import CONFIG_FILE  # noqa: E402


def _daemon_defaults() -> dict:
    from config_manager import ConfigManager
    return ConfigManager(verbose=False).default_config


class ConfigStore:
    """In-place editor for ~/.config/hyprwhspr/config.json."""

    def __init__(self):
        self.path = Path(CONFIG_FILE)
        try:
            self.defaults = _daemon_defaults()
        except Exception:
            self.defaults = {}
        self.data = {}
        self.load()

    def load(self):
        try:
            self.data = json.loads(self.path.read_text())
        except (FileNotFoundError, ValueError):
            self.data = {}

    def get(self, key, default=None):
        if key in self.data:
            return self.data[key]
        if key in self.defaults:
            return self.defaults[key]
        return default

    def set(self, key, value):
        self.data[key] = value
        self.save()

    def unset(self, key):
        if key in self.data:
            del self.data[key]
            self.save()

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2, ensure_ascii=False) + '\n')
