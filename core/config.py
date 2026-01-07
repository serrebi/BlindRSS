import copy
import json
import os
import sys
import logging

log = logging.getLogger(__name__)

# When frozen (PyInstaller) use the exe directory; otherwise use the directory
# of the main script so config.json stays alongside the app regardless of
# where the user launches it from.
if getattr(sys, 'frozen', False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(sys.argv[0]))

CONFIG_FILE = os.path.join(APP_DIR, "config.json")
# ... (rest of the file)

DEFAULT_CONFIG = {
    "max_downloads": 32,
    "auto_download_podcasts": False,
    "auto_download_period": "unlimited",
    "refresh_interval": 300,  # seconds
    # Keep refresh concurrency conservative to avoid starving the GUI thread on large feed lists.
    "max_concurrent_refreshes": 10,
    "per_host_max_connections": 4,
    "feed_timeout_seconds": 15,
    "feed_retry_attempts": 5,
    "active_provider": "local",
    "debug_mode": False,
    "refresh_on_startup": True,
    "auto_check_updates": True,
    "sounds_enabled": True,
    "sound_refresh_complete": "sounds/refresh_complete.wav",
    "sound_refresh_error": "sounds/refresh_error.wav",
    "skip_silence": True,
    "silence_vad_aggressiveness": 2,  # 0-3 (3 = most aggressive)
    "silence_vad_frame_ms": 30,  # 10, 20, or 30
    "silence_skip_threshold_db": -38.0,  # used only as RMS fallback
    "silence_skip_min_ms": 700,
    "silence_skip_window_ms": 25,
    "silence_skip_padding_ms": 60,
    "silence_skip_merge_gap_ms": 260,
    "silence_skip_resume_backoff_ms": 360,
    "silence_skip_retrigger_backoff_ms": 1400,
    "close_to_tray": True,
    "minimize_to_tray": True,
    "start_maximized": False,
    "max_cached_views": 15,
    "playback_speed": 1.0,
    "volume": 100,
    "volume_step": 5,
    "seek_back_ms": 10000,
    "seek_forward_ms": 10000,
    "resume_playback": True,
    "resume_save_interval_s": 15,
    "resume_back_ms": 10000,
    "resume_min_ms": 0,
    "resume_complete_threshold_ms": 60000,
    "show_player_on_play": False,
    "vlc_network_caching_ms": 1000,
    "vlc_local_proxy_network_caching_ms": 1000,  # keep VLC buffering low for local range-cache proxy
    "vlc_local_proxy_file_caching_ms": 1000,  # keep VLC buffering low for local range-cache proxy
    "range_cache_enabled": False,
    "range_cache_apply_all_hosts": True,  # apply local range-cache proxy to all HTTP(S) hosts
    "range_cache_initial_burst_kb": 131072,  # initial background burst (KB)
    "range_cache_initial_inline_prefetch_kb": 16384,  # small inline prefetch cushion per seek/read (KB)
    "range_cache_prefetch_kb": 32768,  # per seek/read; larger reduces round-trips on high latency
    "range_cache_inline_window_kb": 4096,  # max bytes served per VLC request; smaller = lower seek latency
    "range_cache_hosts": [],  # allowlist when range_cache_apply_all_hosts is False
    "range_cache_dir": "",  # empty => use OS temp directory
    "range_cache_background_download": False,  # download ahead in background to make later seeks faster
    "range_cache_background_chunk_kb": 16384,  # chunk size for background download
    "downloads_enabled": False,
    "download_path": os.path.join(APP_DIR, "podcasts"),
    "download_retention": "Unlimited",
    "providers": {
        "local": {
            "feeds": []  # List of feed URLs/data
        },
        "theoldreader": {
            "email": "",
            "password": ""
        },
        "miniflux": {
            "url": "",
            "api_key": ""
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


import threading

class ConfigManager:
    def __init__(self):
        self._lock = threading.Lock()
        self.config = self.load_config()
        try:
            if self._apply_migrations():
                self.save_config()
        except Exception:
            log.exception("Failed to apply config migrations")

    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with self._lock:
                    with open(CONFIG_FILE, 'r') as f:
                        loaded = json.load(f)
                        return self._apply_defaults(loaded)
            except Exception as e:
                log.error(f"Error loading config: {e}")
                return copy.deepcopy(DEFAULT_CONFIG)
        return copy.deepcopy(DEFAULT_CONFIG)

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

    def _apply_migrations(self) -> bool:
        """
        Apply in-place migrations for older config.json files.

        Returns True if any changes were made.
        """
        cfg = self.config
        if not isinstance(cfg, dict):
            return False

        changed = False

        # v1.49.x: resume_min_ms default changed from 20000ms -> 0ms.
        # Migrate old default values so users get consistent behavior after upgrade.
        try:
            resume_min_ms = cfg.get("resume_min_ms", None)
            if resume_min_ms is not None and int(resume_min_ms) == 20000:
                cfg["resume_min_ms"] = 0
                changed = True
        except (TypeError, ValueError):
            log.warning("Could not migrate 'resume_min_ms' due to invalid value in config.json; leaving it as is.")

        return bool(changed)

    def save_config(self):
        try:
            with self._lock:
                with open(CONFIG_FILE, 'w') as f:
                    json.dump(self.config, f, indent=4)
        except Exception as e:
            log.error(f"Error saving config: {e}")

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
