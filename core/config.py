import json
import os
import sys

if getattr(sys, 'frozen', False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.getcwd()

CONFIG_FILE = os.path.join(APP_DIR, "config.json")

DEFAULT_CONFIG = {
    "refresh_interval": 300,  # seconds
    "active_provider": "local",
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
                    return json.load(f)
            except Exception as e:
                print(f"Error loading config: {e}")
                return DEFAULT_CONFIG
        return DEFAULT_CONFIG

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
