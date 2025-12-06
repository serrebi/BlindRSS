import json
import os
import sys

# When frozen (PyInstaller) use the exe directory; otherwise use the directory
# of the main script so config.json stays alongside the app regardless of
# where the user launches it from.
if getattr(sys, 'frozen', False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(sys.argv[0]))

CONFIG_FILE = os.path.join(APP_DIR, "config.json")

DEFAULT_CONFIG = {
    "refresh_interval": 300,  # seconds
    "max_concurrent_refreshes": 12,
    "per_host_max_connections": 3,
    "feed_timeout_seconds": 15,
    "feed_retry_attempts": 1,
    "active_provider": "local",
    "skip_silence": False,
    "close_to_tray": False,
    "minimize_to_tray": True,
    "playback_speed": 1.0,
    "downloads_enabled": False,
    "download_path": os.path.join(APP_DIR, "podcasts"),
    "download_retention": "Unlimited",
    "providers": {
        "local": {
            "feeds": [] # List of feed URLs/data
        },
        "theoldreader": {
            "username": "",
            "password": ""
        },
        "miniflux": {
            "url": "",
            "api_key": ""
        },
        "theoldreader": {
            "email": "",
            "password": ""
        },
        "inoreader": {
            "token": ""
        },
        "bazqux": {
            "email": "",
            "password": ""
        }
    }
}

class ConfigManager:
    def __init__(self):
        self.config = self.load_config()

    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r') as f:
                    loaded = json.load(f)
                    return self._apply_defaults(loaded)
            except Exception as e:
                print(f"Error loading config: {e}")
                return DEFAULT_CONFIG
        return DEFAULT_CONFIG

    def _apply_defaults(self, cfg: dict) -> dict:
        """
        Merge any missing default keys into an existing config without clobbering
        user settings. Ensures new options (e.g., skip_silence) are present.
        """
        def merge(defaults, target):
            for key, val in defaults.items():
                if isinstance(val, dict):
                    if key not in target or not isinstance(target.get(key), dict):
                        target[key] = {}
                    merge(val, target[key])
                else:
                    target.setdefault(key, val)
        merged = cfg if isinstance(cfg, dict) else {}
        merge(DEFAULT_CONFIG, merged)
        return merged

    def save_config(self):
        try:
            with open(CONFIG_FILE, 'w') as f:
                json.dump(self.config, f, indent=4)
        except Exception as e:
            print(f"Error saving config: {e}")

    def get(self, key, default=None):
        return self.config.get(key, default)

    def set(self, key, value):
        self.config[key] = value
        self.save_config()
        
    def get_provider_config(self, provider_name):
        return self.config.get("providers", {}).get(provider_name, {})
    
    def update_provider_config(self, provider_name, data):
        if "providers" not in self.config:
            self.config["providers"] = {}
        if provider_name not in self.config["providers"]:
            self.config["providers"][provider_name] = {}
        self.config["providers"][provider_name].update(data)
        self.save_config()
