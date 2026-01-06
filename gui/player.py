import wx
import vlc
import threading
import socket
import time
import sqlite3
import platform
import logging
from core import utils
from core import discovery
from core import playback_state
from core.casting import CastingManager
from urllib.parse import urlparse
from core.range_cache_proxy import get_range_cache_proxy
from core.audio_silence import merge_ranges, merge_ranges_with_gap, scan_audio_for_silence
from core.dependency_check import _log
from .hotkeys import HoldRepeatHotkeys

log = logging.getLogger(__name__)

MIN_FORCE_SAVE_MS = 2000
MIN_TRIVIAL_POSITION_MS = 1000


def _should_reapply_seek(target_ms: int, current_ms: int, tolerance_ms: int, remaining_retries: int) -> bool:
    try:
        if remaining_retries <= 0:
            return False
        if current_ms < 0:
            return True
        return abs(int(current_ms) - int(target_ms)) > int(tolerance_ms)
    except Exception:
        return False


class CastDialog(wx.Dialog):
    def __init__(self, parent, manager: CastingManager):
        super().__init__(parent, title="Cast to Device", size=(400, 300))
        self.manager = manager
        self.devices = []
        self.selected_device = None
        
        sizer = wx.BoxSizer(wx.VERTICAL)
        
        self.list_box = wx.ListBox(self, style=wx.LB_SINGLE)
        sizer.Add(self.list_box, 1, wx.EXPAND | wx.ALL, 5)
        
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        refresh_btn = wx.Button(self, label="Refresh")
        refresh_btn.Bind(wx.EVT_BUTTON, self.on_refresh)
        btn_sizer.Add(refresh_btn, 0, wx.ALL, 5)
        
        connect_btn = wx.Button(self, label="Connect")
        connect_btn.Bind(wx.EVT_BUTTON, self.on_connect)
        btn_sizer.Add(connect_btn, 0, wx.ALL, 5)
        
        cancel_btn = wx.Button(self, label="Cancel")
        cancel_btn.Bind(wx.EVT_BUTTON, self.on_cancel)
        btn_sizer.Add(cancel_btn, 0, wx.ALL, 5)
        
        sizer.Add(btn_sizer, 0, wx.ALIGN_CENTER | wx.ALL, 5)
        
        self.SetSizer(sizer)
        self.Centre()
        
        # Initial scan
        self.on_refresh(None)

    def on_refresh(self, event):
        self.list_box.Clear()
        self.list_box.Append("Scanning...")
        threading.Thread(target=self._scan, daemon=True).start()

    def _scan(self):
        self.devices = self.manager.discover_all()
        wx.CallAfter(self._update_list)

    def _update_list(self):
        self.list_box.Clear()
        if not self.devices:
            self.list_box.Append("No devices found")
            return
            
        for dev in self.devices:
            self.list_box.Append(dev.display_name)

    def on_connect(self, event):
        sel = self.list_box.GetSelection()
        if sel != wx.NOT_FOUND and sel < len(self.devices):
            self.selected_device = self.devices[sel]
            
            # Disable UI while connecting
            self.list_box.Disable()
            self.FindWindowByLabel("Connect").Disable()
            self.FindWindowByLabel("Refresh").Disable()
            self.FindWindowByLabel("Cancel").Disable()
            
            # Show busy cursor
            wx.BeginBusyCursor()
            
            threading.Thread(target=self._connect_thread, args=(self.selected_device,), daemon=True).start()

    def _connect_thread(self, device):
        success = False
        try:
            # This blocks the thread, not the GUI
            self.manager.connect(device)
            success = True
        except Exception as e:
            wx.CallAfter(self._on_connect_error, str(e))
        finally:
            wx.CallAfter(self._on_connect_complete, success)

    def _on_connect_error(self, error_msg):
        wx.MessageBox(f"Connection failed: {error_msg}", "Error", wx.ICON_ERROR)

    def _on_connect_complete(self, success):
        wx.EndBusyCursor()
        if success:
            self.EndModal(wx.ID_OK)
        else:
            # Re-enable UI
            self.list_box.Enable()
            self.FindWindowByLabel("Connect").Enable()
            self.FindWindowByLabel("Refresh").Enable()
            self.FindWindowByLabel("Cancel").Enable()

    def on_cancel(self, event):
        self.EndModal(wx.ID_CANCEL)


class PlayerFrame(wx.Frame):
    def __init__(self, parent, config_manager):
        super().__init__(parent, title="Audio Player", size=(500, 200), style=wx.DEFAULT_FRAME_STYLE | wx.STAY_ON_TOP)
        self.config_manager = config_manager
        
        # Casting
        self.casting_manager = CastingManager()
        self.casting_manager.start()
        self.is_casting = False
        self._cast_last_pos_ms = 0
        self._cast_local_was_playing = False
        self._cast_poll_ts = 0.0

        # Cast handoff tracking
        self._cast_handoff_source_url = None
        self._cast_handoff_target_ms = 0
        self._cast_handoff_attempts_left = 0
        self._timer_interval_ms = 0

        # Resume Seek State
        self._pending_resume_seek_ms = None
        self._pending_resume_seek_attempts = 0
        self._pending_resume_seek_max_attempts = 25
        self._pending_resume_paused = False

        # Slider State
        self._is_dragging_slider = False

        # VLC Instance
        cache_ms = int(self.config_manager.get("vlc_network_caching_ms", 500))
        if cache_ms < 0: cache_ms = 0
        file_cache_ms = max(500, cache_ms)
        self.instance = None
        self.player = None
        self.initialized = False
        
        try:
            self.instance = vlc.Instance(
                '--no-video',
                '--input-fast-seek',
                '--aout=directsound',
                f'--network-caching={cache_ms}',
                f'--file-caching={file_cache_ms}',
                '--http-reconnect'
            )
            self.player = self.instance.media_player_new()
            self.event_manager = self.player.event_manager()
            try:
                self.event_manager.event_attach(vlc.EventType.MediaPlayerEncounteredError, self._on_vlc_error)
            except Exception:
                pass
            self.initialized = True
        except Exception as e:
            wx.CallAfter(wx.MessageBox, 
                f"VLC could not be initialized: {e}\n\n"
                "Please ensure VLC media player is installed (64-bit version recommended).",
                "VLC Error", wx.OK | wx.ICON_ERROR)
            self.initialized = False
        
        self.timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self.on_timer, self.timer)
        
        self.is_playing = False
        self.duration = 0
        self.current_chapters = []
        self.chapter_marks = []
        self.current_url = None
        self._load_seq = 0
        self._active_load_seq = 0
        self.current_title = "No Track Loaded"

        # Persistent playback resume (stored locally in SQLite, keyed by the input URL).
        self._resume_id = None
        self._resume_last_save_ts = 0.0
        self._resume_restore_inflight = False
        self._resume_restore_id = None
        self._resume_restore_target_ms = None
        self._resume_restore_attempts = 0
        self._resume_restore_last_attempt_ts = 0.0
        self._resume_seek_save_calllater = None
        self._resume_seek_save_id = None
        self._stopped_needs_resume = False
        self._shutdown_done = False

        # Seek coalescing / debounce
        self._seek_target_ms = None
        self._seek_target_ts = 0.0

        self._last_vlc_time_ms = 0

        # When the user taps seek keys rapidly, repeatedly calling VLC set_time()
        # causes audio stalls (buffer flush + re-buffer). We coalesce seek inputs:
        # - UI jumps immediately on each input.
        # - VLC gets at most one seek every _seek_apply_max_rate_s while holding.
        # - After the last input, we apply the final target after _seek_apply_debounce_s.
        self._seek_apply_last_ts = 0.0  # last time we actually called VLC set_time()
        self._seek_apply_target_ms = None
        self._seek_apply_calllater = None
        self._seek_input_ts = 0.0  # last seek input timestamp

        try:
            self._seek_apply_debounce_s = float(self.config_manager.get("seek_apply_debounce_s", 0.18) or 0.18)
        except Exception:
            self._seek_apply_debounce_s = 0.18
        try:
            self._seek_apply_max_rate_s = float(self.config_manager.get("seek_apply_max_rate_s", 0.35) or 0.35)
        except Exception:
            self._seek_apply_max_rate_s = 0.35

        # Clamp to sane values
        self._seek_apply_debounce_s = max(0.06, min(0.50, float(self._seek_apply_debounce_s)))
        self._seek_apply_max_rate_s = max(0.12, min(1.00, float(self._seek_apply_max_rate_s)))

# Authoritative position tracking
        self._pos_ms = 0
        self._pos_ts = time.monotonic()
        self._pos_allow_backwards_until_ts = 0.0
        self._pos_last_timer_ts = 0.0

        # Seek guard
        self._seek_guard_target_ms = None
        self._seek_guard_attempts_left = 0
        self._seek_guard_reapply_left = 0
        self._seek_guard_calllater = None

        # Range-cache proxy recovery state
        self._last_orig_url = None
        self._last_vlc_url = None
        self._last_used_range_proxy = False
        self._last_range_proxy_headers = None
        self._last_range_proxy_cache_dir = None
        self._last_range_proxy_prefetch_kb = None
        self._last_range_proxy_initial_burst_kb = None
        self._last_range_proxy_initial_inline_kb = None
        self._range_proxy_retry_count = 0
        self._last_load_chapters = None
        self._last_load_title = None

        # Silence skip
        self._silence_scan_thread = None
        self._silence_scan_abort = None
        self._silence_ranges = []
        self._silence_scan_ready = False
        self._silence_skip_active_target = None
        self._silence_skip_last_idx = None
        self._silence_skip_last_ts = 0.0
        self._silence_skip_last_target_ms = None
        self._silence_skip_last_seek_ts = 0.0
        
        # Playback speed handling
        self.playback_speed = float(self.config_manager.get("playback_speed", 1.0))
        # Media key settings
        self.volume = int(self.config_manager.get("volume", 100))
        self.volume_step = int(self.config_manager.get("volume_step", 5))
        self.seek_back_ms = int(self.config_manager.get("seek_back_ms", 10000))
        self.seek_forward_ms = int(self.config_manager.get("seek_forward_ms", self.seek_back_ms))
        if self.seek_forward_ms != self.seek_back_ms:
            self.seek_forward_ms = int(self.seek_back_ms)
            try:
                self.config_manager.set("seek_forward_ms", int(self.seek_forward_ms))
            except Exception:
                pass

        self.init_ui()
        self.Bind(wx.EVT_CLOSE, self.on_close)
        self.Bind(wx.EVT_CHAR_HOOK, self.on_char_hook)

        self._media_hotkeys = HoldRepeatHotkeys(self, hold_delay_s=0.2, repeat_interval_s=0.3, poll_interval_ms=200)

        # Apply initial volume
        self.set_volume_percent(self.volume, persist=False)
        
        # Update UI with initial speed
        self.set_playback_speed(self.playback_speed)

    # ---------------------------------------------------------------------
    # Window helpers
    # ---------------------------------------------------------------------

    def _current_position_ms(self) -> int:
        """
        Best-effort current position in ms, favoring recent seek targets and
        UI-tracked position with elapsed time when playing.
        """
        now = time.monotonic()
        try:
            tgt = getattr(self, "_seek_target_ms", None)
            tgt_ts = float(getattr(self, "_seek_target_ts", 0.0) or 0.0)
        except Exception:
            tgt = None
            tgt_ts = 0.0

        base = 0
        if tgt is not None and (now - tgt_ts) < 2.5:
            try:
                base = int(tgt)
            except Exception:
                base = 0
        else:
            try:
                base = int(getattr(self, "_pos_ms", 0) or 0)
            except Exception:
                base = 0

        try:
            if bool(getattr(self, "is_playing", False)):
                pos_ts = float(getattr(self, "_pos_ts", 0.0) or 0.0)
                if pos_ts > 0:
                    base += int(max(0.0, now - pos_ts) * 1000.0)
        except Exception:
            pass

        if base < 0:
            base = 0
        try:
            dur = int(getattr(self, "duration", 0) or 0)
            if dur > 0 and base > dur:
                base = dur
        except Exception:
            pass
        return int(base)

    # ---------------------------------------------------------------------
    # Persistent resume (SQLite overlay)
    # ---------------------------------------------------------------------

    def _get_config_int(self, key: str, default: int) -> int:
        try:
            return int(self.config_manager.get(key, default))
        except (TypeError, ValueError):
            return default

    def _get_config_bool(self, key: str, default: bool) -> bool:
        val = self.config_manager.get(key, default)
        if isinstance(val, str):
            norm = val.strip().lower()
            if norm in ("true", "1", "yes", "on"):
                return True
            if norm in ("false", "0", "no", "off"):
                return False
            return bool(default)
        return bool(val)

    def _resume_feature_enabled(self) -> bool:
        return self._get_config_bool("resume_playback", True)

    def _get_resume_id(self) -> str | None:
        rid = getattr(self, "_resume_id", None)
        if rid:
            return str(rid)
        url = getattr(self, "current_url", None)
        if url:
            return str(url)
        return None

    def _stop_calllater(self, attr_name: str, log_message: str) -> None:
        try:
            calllater = getattr(self, attr_name, None)
            if calllater is not None:
                calllater.Stop()
        except Exception:
            log.exception(log_message)
        finally:
            try:
                setattr(self, attr_name, None)
            except Exception:
                pass

    def _cancel_scheduled_resume_save(self) -> None:
        try:
            self._stop_calllater("_resume_seek_save_calllater", "Failed to cancel scheduled resume save")
        finally:
            self._resume_seek_save_id = None

    def _note_user_seek(self) -> None:
        try:
            self._stopped_needs_resume = False
            # User-initiated seeks should override any pending auto-resume seek.
            if getattr(self, "_resume_restore_inflight", False) and getattr(self, "_pending_resume_seek_ms", None) is not None:
                self._pending_resume_seek_ms = None
                self._pending_resume_seek_attempts = 0
                self._pending_resume_paused = False
                self._resume_restore_inflight = False
                self._resume_restore_id = None
                self._resume_restore_target_ms = None
                self._resume_restore_attempts = 0
                self._resume_restore_last_attempt_ts = 0.0
        except Exception:
            log.exception("Error resetting resume state on user seek")

    def _schedule_resume_save_after_seek(self, delay_ms: int = 900) -> None:
        if not self._resume_feature_enabled():
            return
        resume_id = self._get_resume_id()
        if not resume_id:
            return

        try:
            delay = max(0, int(delay_ms))
        except (TypeError, ValueError):
            delay = 900

        self._cancel_scheduled_resume_save()
        self._resume_seek_save_id = str(resume_id)

        def _tick() -> None:
            try:
                if (self._get_resume_id() or "") != str(self._resume_seek_save_id or ""):
                    return
                if getattr(self, "_resume_restore_inflight", False) and getattr(self, "_pending_resume_seek_ms", None) is not None:
                    return
                if self.is_casting:
                    pos_ms = int(getattr(self, "_cast_last_pos_ms", 0) or 0)
                else:
                    pos_ms = int(self._current_position_ms())
            except Exception:
                log.exception("Failed to get position in scheduled resume save tick")
                return

            if pos_ms < MIN_TRIVIAL_POSITION_MS:
                return

            try:
                self._persist_playback_position(force=True)
            except Exception:
                log.exception("Failed to persist playback position after seek")

        try:
            self._resume_seek_save_calllater = wx.CallLater(int(delay), _tick)
        except Exception:
            log.exception("Failed to schedule resume save")
            self._resume_seek_save_calllater = None

    def _maybe_restore_playback_position(self, resume_id: str, title: str | None) -> None:
        if not resume_id:
            return
        if not self._resume_feature_enabled():
            return

        try:
            state = playback_state.get_playback_state(resume_id)
        except sqlite3.Error:
            log.exception("Failed to read playback_state for resume")
            return
        except Exception:
            log.exception("Unexpected error while reading playback_state for resume")
            return
        if not state or state.completed:
            return
        if state.seek_supported is False:
            # We previously learned this stream is not seekable, so avoid an auto-resume loop.
            return

        pos_ms = state.position_ms

        min_ms = self._get_config_int("resume_min_ms", 0)
        if pos_ms < max(0, min_ms):
            return

        complete_threshold_ms = self._get_config_int("resume_complete_threshold_ms", 60000)

        dur_ms = state.duration_ms or 0
        if dur_ms > 0 and (dur_ms - pos_ms) <= max(0, complete_threshold_ms):
            # Treat items close to the end as completed (avoid resuming to the credits).
            try:
                playback_state.upsert_playback_state(
                    resume_id,
                    0,
                    duration_ms=dur_ms,
                    title=title,
                    completed=True,
                )
            except Exception:
                log.exception("Failed to mark playback_state as completed")
            return

        back_ms = self._get_config_int("resume_back_ms", 10000)
        back_ms = max(0, back_ms)
        # If the saved position is very early in the file, don't rewind back past 0
        # (otherwise it looks like resume did not work at all).
        if pos_ms <= back_ms:
            target_ms = pos_ms
        else:
            target_ms = pos_ms - back_ms

        self._pending_resume_seek_ms = target_ms
        self._pending_resume_seek_attempts = 0
        self._pending_resume_paused = False
        self._resume_restore_inflight = True
        self._resume_restore_id = resume_id
        self._resume_restore_target_ms = target_ms
        self._resume_restore_attempts = 0
        self._resume_restore_last_attempt_ts = 0.0
        # Avoid writing a 0-position back to the DB while the resume seek is still pending.
        self._resume_last_save_ts = time.monotonic()

    def _persist_playback_position(self, force: bool = False) -> None:
        if not self._resume_feature_enabled():
            return
        resume_id = self._get_resume_id()
        if not resume_id:
            return

        restore_pending = bool(getattr(self, "_resume_restore_inflight", False)) and getattr(self, "_pending_resume_seek_ms", None) is not None

        # Don't overwrite saved progress while the initial resume seek is pending.
        if restore_pending and not force:
            return

        # Even for force saves, avoid overwriting stored progress with a near-zero position while restore is pending.
        if restore_pending and force:
            try:
                if self.is_casting:
                    cur_pos_ms = int(getattr(self, "_cast_last_pos_ms", 0) or 0)
                else:
                    cur_pos_ms = int(self._current_position_ms())
            except Exception:
                cur_pos_ms = 0
            if cur_pos_ms < MIN_FORCE_SAVE_MS:
                return

        try:
            interval_s = float(self.config_manager.get("resume_save_interval_s", 15) or 15)
        except Exception:
            interval_s = 15.0
        interval_s = max(2.0, float(interval_s))

        now = float(time.monotonic())
        if not force:
            try:
                last = float(getattr(self, "_resume_last_save_ts", 0.0) or 0.0)
            except Exception:
                last = 0.0
            if (now - last) < interval_s:
                return

        try:
            if self.is_casting:
                pos_ms = int(getattr(self, "_cast_last_pos_ms", 0) or 0)
            else:
                pos_ms = int(self._current_position_ms())
        except Exception:
            pos_ms = 0

        if not force and pos_ms < MIN_TRIVIAL_POSITION_MS:
            # Avoid creating state rows for trivial playback attempts.
            return

        try:
            dur_ms = int(getattr(self, "duration", 0) or 0)
        except Exception:
            dur_ms = 0
        if dur_ms <= 0:
            dur_ms = None

        try:
            complete_threshold_ms = int(self.config_manager.get("resume_complete_threshold_ms", 60000) or 60000)
        except Exception:
            complete_threshold_ms = 60000

        completed = False
        if dur_ms is not None and int(dur_ms) > 0:
            remaining = int(dur_ms) - int(pos_ms)
            if remaining <= max(0, int(complete_threshold_ms)):
                completed = True
                pos_ms = 0

        title = getattr(self, "current_title", None)

        try:
            playback_state.upsert_playback_state(
                resume_id,
                int(pos_ms),
                duration_ms=(int(dur_ms) if dur_ms is not None else None),
                title=(str(title) if title else None),
                completed=bool(completed),
            )
            self._resume_last_save_ts = float(now)
        except Exception:
            log.exception("Failed to persist playback_state")

    def focus_play_pause(self) -> None:
        try:
            if getattr(self, "play_btn", None):
                self.play_btn.SetFocus()
        except Exception:
            pass

    def show_and_focus(self) -> None:
        try:
            if not self.IsShown():
                self.Show()
            self.Raise()
            wx.CallAfter(self.focus_play_pause)
        except Exception:
            pass

    # ---------------------------------------------------------------------
    # VLC error handling
    # ---------------------------------------------------------------------

    def _on_vlc_error(self, event) -> None:
        _log("VLC encountered an error event.")
        print("DEBUG: VLC error event")
        try:
            wx.CallAfter(self._handle_vlc_error)
        except Exception:
            pass

    def _handle_vlc_error(self) -> None:
        print("DEBUG: Handling VLC error")
        if self.is_casting:
            return
        if not self._last_vlc_url:
            return

        if not self._last_used_range_proxy or not self._last_orig_url:
            return

        # First: restart proxy and retry once.
        if self._range_proxy_retry_count == 0:
            self._range_proxy_retry_count = 1
            try:
                inline_window_kb = int(self.config_manager.get('range_cache_inline_window_kb', 1024) or 1024)
                proxy = get_range_cache_proxy(
                    cache_dir=self._last_range_proxy_cache_dir,
                    prefetch_kb=int(self._last_range_proxy_prefetch_kb or 16384),
                    background_download=bool(self.config_manager.get('range_cache_background_download', True)),
                    background_chunk_kb=int(self.config_manager.get('range_cache_background_chunk_kb', 8192) or 8192),
                    inline_window_kb=inline_window_kb,
                    initial_burst_kb=int(self._last_range_proxy_initial_burst_kb or self.config_manager.get('range_cache_initial_burst_kb', 65536) or 65536),
                    initial_inline_prefetch_kb=int(self._last_range_proxy_initial_inline_kb or self.config_manager.get('range_cache_initial_inline_prefetch_kb', 1024) or 1024),
                )
                try:
                    proxy.start()
                except Exception:
                    pass
                new_url = proxy.proxify(self._last_orig_url, headers=self._last_range_proxy_headers or {})
                self._last_vlc_url = new_url
                self._load_vlc_url(new_url)
                return
            except Exception:
                pass

        # Second: fall back to the original URL
        if self._range_proxy_retry_count == 1:
            self._range_proxy_retry_count = 2
            try:
                self._last_used_range_proxy = False
                self._last_vlc_url = self._last_orig_url
                self._load_vlc_url(self._last_orig_url)
            except Exception:
                pass

    def _load_vlc_url(self, final_url: str, load_seq: int | None = None) -> None:
        print(f"DEBUG: _load_vlc_url {final_url}")
        try:
            if load_seq is None:
                load_seq = int(getattr(self, '_active_load_seq', 0))
            else:
                load_seq = int(load_seq)
        except Exception:
            load_seq = 0
        try:
            self.player.stop()
        except Exception:
            pass
        media = self.instance.media_new(final_url)
        try:
            cache_ms = int(self.config_manager.get('vlc_network_caching_ms', 500))
            if cache_ms < 0: cache_ms = 0

            if isinstance(final_url, str) and final_url.startswith('http://127.0.0.1:') and '/media?id=' in final_url:
                cache_ms = int(self.config_manager.get('vlc_local_proxy_network_caching_ms', 50))
                if cache_ms < 0: cache_ms = 0
                file_cache_ms = int(self.config_manager.get('vlc_local_proxy_file_caching_ms', 50))
                if file_cache_ms < 0: file_cache_ms = 0
            else:
                file_cache_ms = max(500, cache_ms)

            print(f"DEBUG: VLC options: network-caching={cache_ms}, file-caching={file_cache_ms}")
            media.add_option(f':network-caching={cache_ms}')
            media.add_option(f':file-caching={file_cache_ms}')
            media.add_option(':http-reconnect')
            media.add_option(':http-user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36')
        except Exception:
            pass
        self.player.set_media(media)
        def _do_play():
            try:
                if int(getattr(self, '_active_load_seq', 0)) != int(load_seq):
                    print("DEBUG: _do_play aborted (stale seq)")
                    return
            except Exception:
                pass
            try:
                print("DEBUG: Calling self.player.play()")
                self.player.play()
            except Exception:
                return
            try:
                self.player.audio_set_volume(int(getattr(self, 'volume', 100)))
            except Exception:
                pass
            self.is_playing = True
            try:
                self.play_btn.SetLabel('Pause')
            except Exception:
                pass

        try:
            wx.CallLater(50, _do_play)
        except Exception:
            _do_play()

        try:
            desired = 2000
            try:
                if getattr(self, '_pending_resume_seek_ms', None) is not None:
                    desired = 250
            except Exception:
                desired = 2000
            # Run the timer faster when skip-silence is enabled so jumps feel snappier.
            try:
                if bool(self.config_manager.get("skip_silence", False)):
                    desired = min(desired, 280)
            except Exception:
                pass
            if (not self.timer.IsRunning()) or int(getattr(self, '_timer_interval_ms', 0) or 0) != int(desired):
                self.timer.Start(int(desired))
                self._timer_interval_ms = int(desired)
        except Exception:
            pass
        try:
            self.set_playback_speed(self.playback_speed)
        except Exception:
            pass

    def init_ui(self):
        panel = wx.Panel(self)
        self.panel = panel
        sizer = wx.BoxSizer(wx.VERTICAL)
        
        # Title
        self.title_lbl = wx.StaticText(panel, label="No Track Loaded")
        sizer.Add(self.title_lbl, 0, wx.ALL | wx.CENTER, 5)
        
        # Slider
        self.slider = wx.Slider(panel, value=0, minValue=0, maxValue=1000)
        self.slider.SetName("Playback Position")
        # FIX: Separate tracking (dragging) from release (seeking)
        self.slider.Bind(wx.EVT_SCROLL_THUMBTRACK, self.on_slider_track)
        self.slider.Bind(wx.EVT_SCROLL_THUMBRELEASE, self.on_slider_release)
        # Also catch CLICK/CHANGED for non-drag clicks on the bar
        self.slider.Bind(wx.EVT_SCROLL_CHANGED, self.on_slider_release)
        
        sizer.Add(self.slider, 0, wx.EXPAND | wx.ALL, 5)
        
        # Time Labels
        time_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.current_time_lbl = wx.StaticText(panel, label="00:00")
        self.total_time_lbl = wx.StaticText(panel, label="00:00")
        time_sizer.Add(self.current_time_lbl, 0, wx.LEFT, 5)
        time_sizer.AddStretchSpacer()
        time_sizer.Add(self.total_time_lbl, 0, wx.RIGHT, 5)
        sizer.Add(time_sizer, 0, wx.EXPAND | wx.BOTTOM, 5)
        
        # Controls
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        
        # Rewind 10s
        rewind_btn = wx.Button(panel, label="-10s")
        rewind_btn.SetName("Rewind 10 seconds")
        rewind_btn.Bind(wx.EVT_BUTTON, self.on_rewind)
        btn_sizer.Add(rewind_btn, 0, wx.ALL, 5)
        
        # Play/Pause
        self.play_btn = wx.Button(panel, label="Play")
        self.play_btn.Bind(wx.EVT_BUTTON, self.on_play_pause)
        btn_sizer.Add(self.play_btn, 0, wx.ALL, 5)

        # Stop
        self.stop_btn = wx.Button(panel, label="Stop")
        self.stop_btn.Bind(wx.EVT_BUTTON, self.on_stop)
        btn_sizer.Add(self.stop_btn, 0, wx.ALL, 5)
        
        # Forward 10s
        forward_btn = wx.Button(panel, label="+10s")
        forward_btn.SetName("Fast Forward 10 seconds")
        forward_btn.Bind(wx.EVT_BUTTON, self.on_forward)
        btn_sizer.Add(forward_btn, 0, wx.ALL, 5)
        
        # Speed
        speeds = utils.build_playback_speeds()
        self.speed_combo = wx.ComboBox(panel, choices=[f"{s}x" for s in speeds], style=wx.CB_READONLY)
        self.speed_combo.SetName("Playback Speed")
        self.speed_combo.Bind(wx.EVT_COMBOBOX, self.on_speed_select)
        btn_sizer.Add(self.speed_combo, 0, wx.ALL, 5)
        
        # Cast
        self.cast_btn = wx.Button(panel, label="Cast")
        self.cast_btn.Bind(wx.EVT_BUTTON, self.on_cast)
        btn_sizer.Add(self.cast_btn, 0, wx.ALL, 5)
        
        sizer.Add(btn_sizer, 0, wx.ALIGN_CENTER)
        
        # Chapters
        self.chapter_choice = wx.ComboBox(panel, style=wx.CB_READONLY)
        self.chapter_choice.SetName("Chapters")
        self.chapter_choice.Bind(wx.EVT_COMBOBOX, self.on_chapter_select)
        sizer.Add(self.chapter_choice, 0, wx.EXPAND | wx.ALL, 5)
        
        panel.SetSizer(sizer)

    def on_cast(self, event):
        if self.is_casting:
            cast_pos_ms = None
            try:
                pos_sec = self.casting_manager.get_position()
                if pos_sec is not None:
                    cast_pos_ms = int(float(pos_sec) * 1000.0)
            except Exception:
                cast_pos_ms = None

            if cast_pos_ms is None:
                try:
                    cast_pos_ms = int(getattr(self, '_cast_last_pos_ms', 0) or 0)
                except Exception:
                    cast_pos_ms = 0

            cast_was_playing = bool(self.is_playing)

            try:
                self.casting_manager.disconnect()
            except Exception:
                pass

            self.is_casting = False
            try:
                self.cast_btn.SetLabel('Cast')
            except Exception:
                pass
            try:
                self.title_lbl.SetLabel(f"{self.current_title} (Local)")
            except Exception:
                pass
            try:
                # Stop remote playback so audio does not continue on the cast device.
                self.casting_manager.stop_playback()
            except Exception:
                pass

            if self.current_url:
                same_media = False
                try:
                    same_media = (getattr(self, '_cast_handoff_source_url', None) == self.current_url)
                except Exception:
                    same_media = False

                if same_media:
                    try:
                        if self._resume_local_from_cast(int(cast_pos_ms), bool(cast_was_playing)):
                            self._cast_handoff_source_url = None
                            return
                    except Exception:
                        pass

                self._pending_resume_seek_ms = max(0, int(cast_pos_ms))
                self._pending_resume_seek_attempts = 0
                self._pending_resume_paused = (not cast_was_playing)
                self.load_media(self.current_url, is_youtube=False, chapters=self.current_chapters)
                self._cast_handoff_source_url = None
            return

        local_pos_ms = 0
        try:
            if getattr(self, '_seek_target_ms', None) is not None:
                local_pos_ms = int(self._seek_target_ms or 0)
            else:
                local_pos_ms = int(self._current_position_ms())
            if local_pos_ms < 0:
                local_pos_ms = 0
        except Exception:
            local_pos_ms = 0

        local_was_playing = bool(self.is_playing)
        self._cast_local_was_playing = local_was_playing
        self._cast_last_pos_ms = local_pos_ms

        dlg = CastDialog(self, self.casting_manager)
        try:
            if dlg.ShowModal() != wx.ID_OK:
                return
            device = dlg.selected_device
            if not device:
                return

            try:
                self.casting_manager.connect(device)
                self.is_casting = True
                self.cast_btn.SetLabel('Disconnect')
                self.title_lbl.SetLabel(f"{self.current_title} (Casting to {device.name})")

                if local_was_playing:
                    try:
                        self.player.pause()
                    except Exception:
                        pass
                else:
                    try:
                        self.player.set_pause(1)
                    except Exception:
                        pass

                if self.current_url:
                    start_sec = None
                    try:
                        if local_pos_ms and int(local_pos_ms) > 0:
                            start_sec = float(local_pos_ms) / 1000.0
                    except Exception:
                        start_sec = None
                    self._cast_handoff_source_url = self.current_url
                    self.casting_manager.play(self.current_url, self.current_title, content_type='audio/mpeg', start_time_seconds=start_sec)
                    if local_pos_ms > 0:
                        try:
                            self._cast_handoff_target_ms = int(local_pos_ms)
                            self._cast_handoff_attempts_left = 4
                            wx.CallLater(1200, self._cast_handoff_seek_tick)
                        except Exception:
                            pass

                    if not local_was_playing:
                        try:
                            self.casting_manager.pause()
                        except Exception:
                            pass
                        self.is_playing = False
            except Exception as e:
                wx.MessageBox(f"Connection failed: {e}", 'Error', wx.ICON_ERROR)
        finally:
            try:
                dlg.Destroy()
            except Exception:
                pass

    def _cast_handoff_seek_tick(self):
        try:
            if not bool(getattr(self, 'is_casting', False)):
                return
            target_ms = int(getattr(self, '_cast_handoff_target_ms', 0) or 0)
            if target_ms <= 0:
                return

            try:
                pos_sec = self.casting_manager.get_position()
                if pos_sec is not None:
                    cur_ms = int(float(pos_sec) * 1000.0)
                    self._cast_last_pos_ms = int(cur_ms)
                    if abs(int(cur_ms) - int(target_ms)) <= 2000:
                        return
            except Exception:
                pass

            try:
                self.casting_manager.seek(float(target_ms) / 1000.0)
            except Exception:
                pass

            try:
                left = int(getattr(self, '_cast_handoff_attempts_left', 0) or 0)
            except Exception:
                left = 0
            left -= 1
            self._cast_handoff_attempts_left = left
            if left > 0:
                wx.CallLater(1200, self._cast_handoff_seek_tick)
        except Exception:
            pass

    def _resume_local_from_cast(self, position_ms: int, was_playing: bool) -> bool:
        try:
            position_ms = max(0, int(position_ms))
        except Exception:
            position_ms = 0

        try:
            media_obj = None
            try:
                media_obj = self.player.get_media()
            except Exception:
                media_obj = None
            if media_obj is None:
                return False

            try:
                desired = 250
                if (not self.timer.IsRunning()) or int(getattr(self, '_timer_interval_ms', 0) or 0) != int(desired):
                    self.timer.Start(int(desired))
                    self._timer_interval_ms = int(desired)
            except Exception:
                pass

            try:
                self.player.play()
            except Exception:
                pass

            self._pending_resume_seek_ms = int(position_ms)
            self._pending_resume_seek_attempts = 0
            self._pending_resume_paused = (not bool(was_playing))
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Silence skipping
    # ------------------------------------------------------------------

    def _cancel_silence_scan(self):
        try:
            if self._silence_scan_abort is not None:
                self._silence_scan_abort.set()
        except Exception:
            pass
        self._silence_scan_abort = None
        self._silence_scan_thread = None
        self._silence_ranges = []
        self._silence_scan_ready = False
        self._silence_skip_active_target = None
        self._silence_skip_last_idx = None
        self._silence_skip_last_target_ms = None
        self._silence_skip_last_seek_ts = 0.0

    def _start_silence_scan(self, url: str, load_seq: int, headers: dict = None) -> None:
        if not self.config_manager.get("skip_silence", False):
            return
        if not url or self.is_casting:
            return
        try:
            self._silence_scan_ready = False
        except Exception:
            pass
        self._silence_scan_ready = False
        self._silence_ranges = []
        abort_evt = threading.Event()
        self._silence_scan_abort = abort_evt

        def _worker() -> None:
            try:
                window_ms = int(self.config_manager.get("silence_skip_window_ms", 30) or 30)
                min_ms = int(self.config_manager.get("silence_skip_min_ms", 600) or 600)
                threshold_db = float(self.config_manager.get("silence_skip_threshold_db", -42.0) or -42.0)
                pad_ms = int(self.config_manager.get("silence_skip_padding_ms", 120) or 120)
                merge_gap = int(self.config_manager.get("silence_skip_merge_gap_ms", 200) or 200)
                vad_aggr = int(self.config_manager.get("silence_vad_aggressiveness", 2) or 2)
                vad_frame_ms = int(self.config_manager.get("silence_vad_frame_ms", 30) or 30)
                ranges = scan_audio_for_silence(
                    url,
                    window_ms=window_ms,
                    min_silence_ms=min_ms,
                    threshold_db=threshold_db,
                    detection_mode="vad",
                    vad_aggressiveness=vad_aggr,
                    vad_frame_ms=vad_frame_ms,
                    merge_gap_ms=merge_gap,
                    abort_event=abort_evt,
                    headers=headers,
                )
                if abort_evt.is_set():
                    return
                padded = []
                for s, e in ranges:
                    start = max(0, int(s) - pad_ms)
                    end = int(e) + pad_ms
                    padded.append((start, end))
                merged = merge_ranges_with_gap(padded, gap_ms=merge_gap)
                if abort_evt.is_set() or int(getattr(self, "_active_load_seq", 0)) != int(load_seq):
                    return
                self._silence_ranges = merged
                self._silence_scan_ready = True
                try:
                    print(f"DEBUG: silence scan ready ({len(merged)} ranges)")
                except Exception:
                    pass
            except Exception as e:
                print(f"DEBUG: silence scan failed: {e}")
                self._silence_scan_ready = False

        try:
            t = threading.Thread(target=_worker, daemon=True)
            t.start()
            self._silence_scan_thread = t
        except Exception:
            pass

    def _maybe_skip_silence(self, pos_ms: int) -> None:
        if not self.config_manager.get("skip_silence", False):
            return
        if self.is_casting:
            return
        if not bool(getattr(self, "_silence_scan_ready", False)):
            return
        if not getattr(self, "_silence_ranges", None):
            return
        
        now = time.monotonic()
        if getattr(self, "_is_dragging_slider", False):
            return

        # 1. Much longer cooldown for remote streams (YouTube DASH is jittery)
        url = getattr(self, "current_url", "") or ""
        is_remote = url.startswith("http") and not ("127.0.0.1" in url or "localhost" in url)
        
        cooldown = 5.0 if is_remote else 2.5
        
        try:
            last_jump_ts = float(getattr(self, "_silence_skip_last_ts", 0.0) or 0.0)
            if now - last_jump_ts < cooldown:
                return
        except Exception:
            pass

        try:
            current_target = getattr(self, "_silence_skip_active_target", None)
        except Exception:
            current_target = None

        # 2. Increase cushion past silence (2000ms for remote)
        resume_backoff = 2000 if is_remote else 800
        
        for idx, (start, end) in enumerate(self._silence_ranges):
            if pos_ms < start - 1000:
                # Ranges are sorted; no need to continue.
                break
            
            # If we are currently inside a silent span...
            if start - 100 <= pos_ms <= end - 100:
                target_ms = int(end) + resume_backoff
                
                # 3. Robust landing verification:
                # If we just tried to jump to this exact target, don't loop!
                try:
                    last_target = getattr(self, "_silence_skip_last_target_ms", None)
                    if last_target is not None and abs(int(last_target) - int(target_ms)) <= 1000:
                        return
                except Exception:
                    pass

                try:
                    self._silence_skip_active_target = int(target_ms)
                    self._silence_skip_last_ts = float(now)
                    self._silence_skip_last_idx = int(idx)
                    self._silence_skip_last_target_ms = int(target_ms)
                    self._silence_skip_last_seek_ts = float(now)
                    _log(f"Skipping silence: {pos_ms}ms -> {target_ms}ms")
                except Exception:
                    pass
                
                # Seek immediately
                self._apply_seek_time_ms(int(target_ms), force=True)
                return

        try:
            if current_target is not None and pos_ms > int(current_target) + 500:
                self._silence_skip_active_target = None
            if self._silence_skip_last_idx is not None:
                last_idx = int(self._silence_skip_last_idx)
                if last_idx < len(self._silence_ranges):
                    _, last_end = self._silence_ranges[last_idx]
                    if pos_ms > last_end + retrigger_backoff + 300:
                        self._silence_skip_last_idx = None
            if self._silence_skip_last_target_ms is not None and (now - float(getattr(self, "_silence_skip_last_seek_ts", 0.0) or 0.0)) > 2.0:
                if abs(pos_ms - int(self._silence_skip_last_target_ms)) > retrigger_backoff:
                    self._silence_skip_last_target_ms = None
        except Exception:
            pass

    def _maybe_range_cache_url(self, url: str, headers: dict | None = None) -> str:
        try:
            if not url:
                return url
            self._last_orig_url = url
            self._last_used_range_proxy = False
            self._last_range_proxy_headers = headers or {}
            self._last_range_proxy_cache_dir = None
            self._last_range_proxy_prefetch_kb = None
            self._last_range_proxy_initial_burst_kb = None
            self._last_range_proxy_initial_inline_kb = None
            self._last_vlc_url = url
            self._range_proxy_retry_count = 0
            low = url.lower()
            if not (low.startswith('http://') or low.startswith('https://')):
                return url
            # HLS playlists often contain relative segment URLs; proxying them through
            # the range cache breaks resolution and also isn't helpful for caching.
            if ".m3u8" in low:
                return url
            if not bool(self.config_manager.get('range_cache_enabled', True)):
                return url
            apply_all = bool(self.config_manager.get('range_cache_apply_all_hosts', True))
            hosts = self.config_manager.get('range_cache_hosts', []) or []
            try:
                if any(str(h).strip() in ('*', 'all', 'ALL') for h in hosts):
                    apply_all = True
            except Exception:
                pass
            try:
                host = urlparse(url).netloc.lower()
            except Exception:
                host = ''
            if not apply_all:
                if not host or not hosts:
                    return url
                host_ok = False
                for h in hosts:
                    try:
                        hs = str(h).strip().lower()
                    except Exception:
                        continue
                    if not hs:
                        continue
                    if hs.startswith('*.') and host.endswith(hs[1:]):
                        host_ok = True
                        break
                    if host == hs or host.endswith('.' + hs):
                        host_ok = True
                        break
                    if hs in host:
                        host_ok = True
                        break
                if not host_ok:
                    return url
            cache_dir = self.config_manager.get('range_cache_dir', '') or None
            prefetch_kb = int(self.config_manager.get('range_cache_prefetch_kb', 16384) or 16384)
            inline_window_kb = int(self.config_manager.get('range_cache_inline_window_kb', 1024) or 1024)
            background_download = bool(self.config_manager.get('range_cache_background_download', True))
            background_chunk_kb = int(self.config_manager.get('range_cache_background_chunk_kb', 8192) or 8192)
            initial_burst_kb = int(self.config_manager.get('range_cache_initial_burst_kb', 65536) or 65536)
            initial_inline_kb = int(self.config_manager.get('range_cache_initial_inline_prefetch_kb', 1024) or 1024)
            proxy = get_range_cache_proxy(cache_dir=cache_dir if cache_dir else None, prefetch_kb=prefetch_kb,
                                         background_download=background_download, background_chunk_kb=background_chunk_kb,
                                         inline_window_kb=inline_window_kb,
                                         initial_burst_kb=initial_burst_kb,
                                         initial_inline_prefetch_kb=initial_inline_kb)
            
            # Default headers
            req_headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
            }
            # Merge with passed headers (e.g. from yt-dlp)
            if headers:
                req_headers.update(headers)

            if 'promodj.com' in host:
                req_headers['Referer'] = 'https://promodj.com/'
            
            self._last_used_range_proxy = True
            self._last_range_proxy_headers = dict(req_headers)
            self._last_range_proxy_cache_dir = cache_dir if cache_dir else None
            self._last_range_proxy_prefetch_kb = prefetch_kb
            self._last_range_proxy_initial_burst_kb = initial_burst_kb
            self._last_range_proxy_initial_inline_kb = initial_inline_prefetch_kb
            
            proxied = proxy.proxify(url, headers=req_headers)
            print(f"DEBUG: Proxy URL generated: {proxied}")
            try:
                pu = urlparse(proxied)
                if pu.hostname in ("127.0.0.1", "localhost") and pu.port:
                    deadline = time.time() + 3.0
                    ok = False
                    while time.time() < deadline:
                        try:
                            s = socket.create_connection((pu.hostname, int(pu.port)), timeout=0.5)
                            ok = True
                            try:
                                s.close()
                            except Exception:
                                pass
                            break
                        except Exception as e:
                            # print(f"DEBUG: Proxy connection check failed: {e}")
                            ok = False
                            time.sleep(0.1)
                    if not ok:
                        print("DEBUG: Proxy skipped - connection check timed out.")
                        self._last_used_range_proxy = False
                        self._last_vlc_url = url
                        return url
            except Exception as e:
                print(f"DEBUG: Proxy connection check error: {e}")
                pass
            
            print("DEBUG: Proxy connection verified. Using proxy.")
            self._last_vlc_url = proxied
            return proxied
        except Exception as e:
            print(f"DEBUG: _maybe_range_cache_url exception: {e}")
            return url

    def load_media(self, url, use_ytdlp=False, chapters=None, title=None):
        if not self.initialized and not self.is_casting:
            wx.MessageBox("VLC is not initialized. Playback is unavailable.", "Error", wx.OK | wx.ICON_ERROR)
            return
        _log(f"load_media: {url} (ytdlp={use_ytdlp})")
        print(f"DEBUG: load_media url={url}, is_casting={self.is_casting}")
        if not url:
            return

        # Persist the previous item's position before switching to the new one.
        try:
            self._persist_playback_position(force=True)
        except Exception:
            log.exception("Failed to persist playback position on media load")
        try:
            self._cancel_scheduled_resume_save()
            self._stop_calllater("_seek_apply_calllater", "Error handling seek apply calllater on media load")
        except Exception:
            log.exception("Error during media load cleanup")
        finally:
            self._stopped_needs_resume = False
            
        self.current_url = url
        self._resume_id = str(url)
        self._resume_restore_inflight = False
        self._resume_restore_id = None
        self._resume_restore_target_ms = None
        try:
            self._pending_resume_seek_ms = None
            self._pending_resume_seek_attempts = 0
            self._pending_resume_paused = False
        except Exception:
            pass
        try:
            self._load_seq += 1
            self._active_load_seq = self._load_seq
        except Exception:
            pass
        self._cancel_silence_scan()

        try:
            self._pos_ms = 0
            self._pos_ts = time.monotonic()
            self._pos_allow_backwards_until_ts = 0.0
            self._pos_last_timer_ts = 0.0
            self._last_vlc_time_ms = 0
            self._seek_target_ms = None
            self._seek_target_ts = 0.0
        except Exception:
            pass
        try:
            self._seek_guard_target_ms = None
            self._seek_guard_attempts_left = 0
            self._seek_guard_reapply_left = 0
            if getattr(self, '_seek_guard_calllater', None) is not None:
                try:
                    self._seek_guard_calllater.Stop()
                except Exception:
                    pass
                self._seek_guard_calllater = None
        except Exception:
            pass
        
        self.slider.SetValue(0)
        self.current_time_lbl.SetLabel("00:00")
        self.total_time_lbl.SetLabel("00:00")
        self.chapter_choice.Clear()
        self.chapter_choice.Disable()
        
        final_url = url
        ytdlp_headers = {}
        if use_ytdlp:
            rumble_handled = False
            try:
                from core import rumble as rumble_mod

                if rumble_mod.is_rumble_url(url):
                    resolved = rumble_mod.resolve_rumble_media(url)
                    final_url = resolved.media_url
                    ytdlp_headers = resolved.headers or {}
                    self.current_title = resolved.title or title or 'Media Stream'
                    rumble_handled = True
            except Exception as e:
                try:
                    _log(f"Rumble resolve failed: {e}")
                except Exception:
                    pass

            if not rumble_handled:
                try:
                    import yt_dlp
                    from core.dependency_check import _get_startup_info

                    class _YtdlpQuietLogger:
                        def __init__(self):
                            self.errors = []

                        def debug(self, msg):
                            return

                        def warning(self, msg):
                            return

                        def error(self, msg):
                            try:
                                self.errors.append(str(msg))
                            except Exception:
                                pass

                    ytdlp_logger = _YtdlpQuietLogger()

                    # Resolve a direct media URL via yt-dlp. We intentionally try
                    # *without* browser cookies first to avoid Windows cookie/DPAPI
                    # issues and reduce noisy stderr output.
                    base_opts = {
                        'format': 'bestaudio/best',
                        'quiet': True,
                        'no_warnings': True,
                        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
                        'referer': url,
                        'noprogress': True,
                        'color': 'never',
                        'logger': ytdlp_logger,
                    }
                    if platform.system().lower() == "windows":
                        # Hide internal yt-dlp subprocess windows (ffmpeg/ffprobe)
                        base_opts['subprocess_startupinfo'] = _get_startup_info()

                    def _extract_with_opts(opts):
                        with yt_dlp.YoutubeDL(opts) as ydl:
                            return ydl.extract_info(url, download=False)

                    info = None
                    err_no_cookies = None
                    err_with_cookies = None

                    try:
                        info = _extract_with_opts(dict(base_opts))
                    except Exception as e:
                        err_no_cookies = e

                    if info is None:
                        tried_cookie_sources = []
                        for source in (discovery.get_ytdlp_cookie_sources(url) or []):
                            if source in tried_cookie_sources:
                                continue
                            tried_cookie_sources.append(source)
                            opts = dict(base_opts)
                            opts["cookiesfrombrowser"] = source
                            try:
                                info = _extract_with_opts(opts)
                                _log(f"yt-dlp cookies OK ({source[0]})")
                                break
                            except Exception as e:
                                err_with_cookies = e
                                _log(f"yt-dlp cookies failed ({source[0]}): {e}")

                    if info is None:
                        raise err_no_cookies or err_with_cookies or RuntimeError("yt-dlp extraction failed")

                    # Handle playlists/multi-video pages
                    if 'entries' in info:
                        entries = list(info['entries'])
                        if entries:
                            info = entries[0]

                    final_url = info.get('url')
                    if not final_url:
                         raise RuntimeError("No media URL found in yt-dlp info")

                    ytdlp_headers = info.get('http_headers', {})
                    self.current_title = info.get('title', title or 'Media Stream')
                except Exception as e:
                    print(f"yt-dlp resolve failed: {e}")
                    _log(f"yt-dlp resolve failed: {e}")
                    wx.MessageBox(f"Could not resolve media URL via yt-dlp: {e}",
                                  "Error", wx.ICON_ERROR)
                    return
        else:
            self.current_title = title or "Playing Audio..."
            try:
                maxr = int(self.config_manager.get('http_max_redirects', 30))
            except Exception:
                maxr = 30
            final_url = utils.resolve_final_url(final_url, max_redirects=maxr)
            final_url = utils.normalize_url_for_vlc(final_url)
                
        self.title_lbl.SetLabel(self.current_title)

        # Apply local resume state (if any) before starting playback.
        try:
            self._maybe_restore_playback_position(str(url), self.current_title)
        except Exception:
            pass

        if self.is_casting:
            try:
                start_ms = getattr(self, "_pending_resume_seek_ms", None)       
            except Exception:
                start_ms = None
            if start_ms is not None and int(start_ms) > 0:
                try:
                    self._cast_last_pos_ms = int(start_ms)
                except Exception:
                    pass
                self.casting_manager.play(
                    final_url,
                    self.current_title,
                    content_type="audio/mpeg",
                    start_time_seconds=float(int(start_ms)) / 1000.0,
                )
            else:
                self.casting_manager.play(final_url, self.current_title, content_type="audio/mpeg")
            self.is_playing = True
        else:
            final_url = self._maybe_range_cache_url(final_url, headers=ytdlp_headers)
            self._last_load_chapters = chapters
            self._last_load_title = self.current_title
            self._start_silence_scan(final_url, int(getattr(self, "_active_load_seq", 0)), headers=ytdlp_headers)
            self._load_vlc_url(final_url, load_seq=int(getattr(self,'_active_load_seq',0)))
        
        if chapters:
            self.update_chapters(chapters)

    def toggle_play_pause(self) -> None:
        if self.is_playing:
            self.pause()
        else:
            self.play()

    def update_chapters(self, chapters):
        self.current_chapters = chapters
        self.chapter_choice.Clear()
        if not chapters:
            self.chapter_choice.Disable()
            return
            
        self.chapter_choice.Enable()
        for ch in chapters:
            start = ch.get("start", 0)
            mins = int(start // 60)
            secs = int(start % 60)
            title = ch.get("title", f"Chapter {start}")
            self.chapter_choice.Append(f"{title} - {mins:02d}:{secs:02d}", ch)

    def on_play_pause(self, event):
        self.toggle_play_pause()

    def on_stop(self, event):
        self.stop()
            
    def on_timer(self, event):
        if self.is_casting:
            try:
                now = time.time()
                if now - float(getattr(self, '_cast_poll_ts', 0.0)) >= 1.0:
                    self._cast_poll_ts = now
                    pos_sec = self.casting_manager.get_position()
                    if pos_sec is not None:
                        self._cast_last_pos_ms = int(float(pos_sec) * 1000.0)
            except Exception:
                pass
            try:
                self._persist_playback_position(force=False)
            except Exception:
                pass
            return

        try:
            length = int(self.player.get_length() or 0)
            if length > 0 and length != int(getattr(self, 'duration', 0) or 0):
                self.duration = int(length)
                try:
                    self.total_time_lbl.SetLabel(self._format_time(int(length)))
                except Exception:
                    pass
        except Exception:
            pass

        playing_now = False
        try:
            playing_now = bool(self.player.is_playing())
        except Exception:
            playing_now = False

        now_mono = time.monotonic()
        try:
            self._pos_last_timer_ts = float(now_mono)
        except Exception:
            pass

        vlc_cur = 0
        try:
            vlc_cur = int(self.player.get_time() or 0)
        except Exception:
            vlc_cur = 0
        if vlc_cur < 0:
            vlc_cur = 0

        try:
            ui_cur = int(getattr(self, "_pos_ms", 0) or 0)
        except Exception:
            ui_cur = 0

        try:
            recent_seek_target = getattr(self, "_seek_target_ms", None)
            recent_seek_ts = float(getattr(self, "_seek_target_ts", 0.0) or 0.0)
        except Exception:
            recent_seek_target = None
            recent_seek_ts = 0.0

        # Simplified logic: Trust our seek target for a few seconds after seeking.
        # Otherwise, trust VLC. This prevents "fighting" where VLC reports old time
        # during buffering and the UI jumps back and forth.
        if recent_seek_target is not None and (now_mono - float(recent_seek_ts)) < 4.0:
            try:
                tgt = int(recent_seek_target)
                # If VLC has actually jumped to the target (or close), we can sync early.
                if abs(int(vlc_cur) - int(tgt)) <= 2000:
                    ui_cur = int(vlc_cur)
                else:
                    ui_cur = int(tgt)
            except Exception:
                ui_cur = int(vlc_cur)
        else:
            ui_cur = int(vlc_cur)

        try:
            self._pos_ms = int(ui_cur)
            self._pos_ts = float(now_mono)
            self._last_vlc_time_ms = int(ui_cur)
        except Exception:
            pass

        cur = int(vlc_cur)

        if getattr(self, '_pending_resume_seek_ms', None) is not None:
            try:
                restore_inflight = bool(getattr(self, "_resume_restore_inflight", False))
                restore_id = getattr(self, "_resume_restore_id", None)

                target_ms = int(self._pending_resume_seek_ms)
                if target_ms < 0: target_ms = 0
                if getattr(self, 'duration', 0) and int(self.duration) > 0 and target_ms > int(self.duration):
                    target_ms = int(self.duration)

                if restore_inflight:
                    # Restore from persisted position: request a single seek once VLC is ready, then wait.
                    if abs(int(cur) - int(target_ms)) <= 1500:
                        self._pending_resume_seek_ms = None
                        if restore_id:
                            try:
                                playback_state.set_seek_supported(str(restore_id), True)
                            except Exception:
                                log.exception("Failed to update seek_supported=True for playback_state")
                        try:
                            self._resume_restore_inflight = False
                        except Exception:
                            pass
                    else:
                        state_i = None
                        try:
                            state_i = int(self.player.get_state())
                        except Exception:
                            state_i = None

                        # If VLC reports the stream is not seekable, stop trying and remember it.
                        try:
                            already_tried = getattr(self, "_resume_restore_attempts", 0)
                            if (
                                state_i is not None
                                and state_i not in (1, 2)
                                and already_tried > 0
                                and restore_id
                                and hasattr(self.player, "is_seekable")
                                and (self.player.is_seekable() is False)
                            ):
                                playback_state.set_seek_supported(str(restore_id), False)
                                self._pending_resume_seek_ms = None
                                self._resume_restore_inflight = False
                                restore_inflight = False
                        except Exception:
                            pass

                        if restore_inflight:
                            # Don't spam play() while VLC is Opening/Buffering; load already starts playback.
                            if state_i in (1, 2):
                                pass
                            else:
                                now_seek = time.monotonic()
                                try:
                                    last_attempt = float(getattr(self, "_resume_restore_last_attempt_ts", 0.0) or 0.0)
                                except Exception:
                                    last_attempt = 0.0
                                if (now_seek - last_attempt) >= 0.9:
                                    try:
                                        attempts = int(getattr(self, "_resume_restore_attempts", 0) or 0)
                                    except Exception:
                                        attempts = 0
                                    if attempts < 1:
                                        try:
                                            self.player.set_time(int(target_ms))
                                            try:
                                                ts = time.monotonic()
                                                self._seek_target_ms = int(target_ms)
                                                self._seek_target_ts = float(ts)
                                                self._pos_ms = int(target_ms)
                                                self._pos_ts = float(ts)
                                                self._last_vlc_time_ms = int(target_ms)
                                            except Exception:
                                                pass
                                        except Exception:
                                            pass
                                        try:
                                            self._resume_restore_attempts = int(attempts) + 1
                                            self._resume_restore_last_attempt_ts = float(now_seek)
                                        except Exception:
                                            pass
                                    else:
                                        # After we requested a seek once, avoid re-seeking (it can cause audio loops).
                                        # If VLC does not land close enough within a few seconds, give up for this
                                        # session without marking the source as unseekable.
                                        try:
                                            if (now_seek - float(last_attempt)) >= 8.0:
                                                self._pending_resume_seek_ms = None
                                                self._resume_restore_inflight = False
                                        except Exception:
                                            pass

                else:
                    # Legacy/in-flight resume path (cast handoff): keep it more aggressive.
                    if not playing_now:
                        try:
                            self.player.play()
                        except Exception:
                            pass
                        try:
                            playing_now = bool(self.player.is_playing())
                        except Exception:
                            playing_now = False

                    if abs(int(cur) - int(target_ms)) > 1500:
                        try:
                            self.player.set_time(int(target_ms))
                            try:
                                ts = time.monotonic()
                                self._seek_target_ms = int(target_ms)
                                self._seek_target_ts = float(ts)
                                self._pos_ms = int(target_ms)
                                self._pos_ts = float(ts)
                                self._last_vlc_time_ms = int(target_ms)
                            except Exception:
                                pass
                            try:
                                self._start_seek_guard(int(target_ms))
                            except Exception:
                                pass
                        except Exception:
                            pass
                    else:
                        self._pending_resume_seek_ms = None
                        if bool(getattr(self, '_pending_resume_paused', False)):
                            try:
                                self.player.set_pause(1)
                            except Exception:
                                try:
                                    self.player.pause()
                                except Exception:
                                    pass
                            self.is_playing = False

                    try:
                        self._pending_resume_seek_attempts = int(getattr(self, '_pending_resume_seek_attempts', 0) or 0) + 1
                    except Exception:
                        self._pending_resume_seek_attempts = 1
                    if (
                        self._pending_resume_seek_ms is not None
                        and int(getattr(self, '_pending_resume_seek_attempts', 0) or 0)
                        >= int(getattr(self, '_pending_resume_seek_max_attempts', 25) or 25)
                    ):
                        self._pending_resume_seek_ms = None
            except Exception:
                pass

        try:
            self._maybe_skip_silence(int(ui_cur))
        except Exception:
            pass

        try:
            if not getattr(self, '_is_dragging_slider', False):
                self.current_time_lbl.SetLabel(self._format_time(int(ui_cur)))
        except Exception:
            pass

        try:
            if getattr(self, 'duration', 0) and int(self.duration) > 0:
                # Do NOT update the slider while the user is dragging it
                if not getattr(self, '_is_dragging_slider', False):
                    pos = int((float(ui_cur) / float(self.duration)) * 1000.0)
                    if pos < 0: pos = 0
                    if pos > 1000: pos = 1000
                    self.slider.SetValue(int(pos))
        except Exception:
            pass

        try:
            if self.current_chapters:
                cur_sec = float(ui_cur) / 1000.0
                idx = -1
                for i, ch in enumerate(self.current_chapters):
                    if cur_sec >= float(ch.get("start", 0) or 0):
                        idx = i
                    else:
                        break
                if idx != -1:
                    try:
                        if not self._is_focus_in_chapter_choice():
                            self.chapter_choice.SetSelection(int(idx))
                    except Exception:
                        pass
        except Exception:
            pass

        try:
            self._persist_playback_position(force=False)
        except Exception:
            pass

    def on_slider_track(self, event):
        """Called repeatedly while dragging the slider."""
        self._is_dragging_slider = True
        try:
            val = self.slider.GetValue()
            if self.duration > 0:
                ms = int((val / 1000.0) * self.duration)
                self.current_time_lbl.SetLabel(self._format_time(ms))
        except Exception:
            pass
        # Do not call Skip to prevent interference, but usually safe to skip.
        event.Skip()

    def on_slider_release(self, event):
        """Called when slider is released (or clicked). Performs the seek."""
        self._is_dragging_slider = False
        self.on_seek(event) # Delegate to the actual seek logic

    def on_seek(self, event):
        """Handle final seek action."""
        if self.is_casting:
            try:
                if not self.duration or int(self.duration) <= 0:
                    return
                value = self.slider.GetValue()
                fraction = float(value) / 1000.0
                target_ms = int(fraction * int(self.duration))
                self._cast_last_pos_ms = int(target_ms)
                self.casting_manager.seek(float(target_ms) / 1000.0)
            except Exception:
                pass
            return

        if not self.duration or int(self.duration) <= 0:
            return
        value = self.slider.GetValue()
        fraction = float(value) / 1000.0
        target_ms = int(fraction * int(self.duration))
        try:
            self._note_user_seek()
        except Exception:
            log.exception("Error noting user seek on slider seek")
        # Force immediate seek on release
        self._apply_seek_time_ms(int(target_ms), force=True)
        try:
            self._schedule_resume_save_after_seek(delay_ms=400)
        except Exception:
            log.exception("Error scheduling resume save after slider seek")

    def on_rewind(self, event):
        if self.is_casting:
            try:
                cur_ms = int(getattr(self, '_cast_last_pos_ms', 0) or 0)
                if cur_ms <= 0:
                    pos_sec = self.casting_manager.get_position()
                    if pos_sec is not None:
                        cur_ms = int(float(pos_sec) * 1000.0)
                step = int(getattr(self, 'seek_back_ms', 10000) or 10000)
                target_ms = max(0, int(cur_ms) - int(step))
                self._cast_last_pos_ms = int(target_ms)
                self.casting_manager.seek(float(target_ms) / 1000.0)
            except Exception:
                pass
            return

        step = int(getattr(self, 'seek_back_ms', 10000) or 10000)
        self.seek_relative_ms(-int(step))

    def on_forward(self, event):
        if self.is_casting:
            try:
                cur_ms = int(getattr(self, '_cast_last_pos_ms', 0) or 0)
                if cur_ms <= 0:
                    pos_sec = self.casting_manager.get_position()
                    if pos_sec is not None:
                        cur_ms = int(float(pos_sec) * 1000.0)
                step = int(getattr(self, 'seek_forward_ms', 10000) or 10000)
                target_ms = int(cur_ms) + int(step)
                if getattr(self, 'duration', 0) and int(self.duration) > 0 and target_ms > int(self.duration):
                    target_ms = int(self.duration)
                self._cast_last_pos_ms = int(target_ms)
                self.casting_manager.seek(float(target_ms) / 1000.0)
            except Exception:
                pass
            return

        step = int(getattr(self, 'seek_forward_ms', 10000) or 10000)
        self.seek_relative_ms(int(step))

    def on_speed_select(self, event):
        idx = self.speed_combo.GetSelection()
        if idx != wx.NOT_FOUND:
            speeds = utils.build_playback_speeds()
            if idx < len(speeds):
                speed = speeds[idx]
                self.set_playback_speed(speed)

    def set_playback_speed(self, speed):
        self.playback_speed = speed
        if not self.is_casting:
            try:
                self.player.set_rate(speed)
            except Exception:
                pass
        # Set combo selection
        speeds = utils.build_playback_speeds()
        try:
            idx = speeds.index(speed)
            self.speed_combo.SetSelection(idx)
        except ValueError:
            pass
        self.config_manager.set("playback_speed", speed)

    def on_chapter_select(self, event):
        # Do not seek on selection change (arrow-key browsing should be safe).
        # Seeking is committed explicitly via Enter (see on_char_hook).
        try:
            self._chapter_pending_idx = int(self.chapter_choice.GetSelection())
        except Exception:
            self._chapter_pending_idx = None

    def _is_focus_in_chapter_choice(self) -> bool:
        try:
            chapter_choice = getattr(self, "chapter_choice", None)
            if chapter_choice is None:
                return False
        except Exception:
            return False

        focus = None
        try:
            focus = wx.Window.FindFocus()
        except Exception:
            focus = None

        try:
            while focus is not None:
                if focus == chapter_choice:
                    return True
                focus = focus.GetParent()
        except Exception:
            return False

        return False

    def _commit_chapter_selection(self) -> None:
        try:
            idx = int(self.chapter_choice.GetSelection())
        except Exception:
            idx = wx.NOT_FOUND

        if idx == wx.NOT_FOUND:
            return

        data = {}
        try:
            data = self.chapter_choice.GetClientData(int(idx)) or {}
        except Exception:
            data = {}

        try:
            start_sec = float(data.get("start", 0) or 0)
        except Exception:
            start_sec = 0.0

        if start_sec < 0:
            start_sec = 0.0

        if self.is_casting:
            # TODO: map chapters to casting seek when supported.
            return

        try:
            self._note_user_seek()
        except Exception:
            log.exception("Error noting user seek on chapter selection")
        try:
            self._apply_seek_time_ms(int(start_sec * 1000.0), force=True)
        except Exception:
            log.exception("Error applying seek on chapter selection")
        try:
            self._schedule_resume_save_after_seek(delay_ms=400)
        except Exception:
            log.exception("Error scheduling resume save after chapter selection")

    def _format_time(self, ms):
        seconds = ms // 1000
        mins = seconds // 60
        secs = seconds % 60
        return f"{mins:02d}:{secs:02d}"

    # ---------------------------------------------------------------------
    # Media control helpers
    # ---------------------------------------------------------------------

    def has_media_loaded(self) -> bool:
        return bool(getattr(self, "current_url", None))

    def is_audio_playing(self) -> bool:
        """Return True only when audio is actively playing."""
        try:
            if bool(getattr(self, "is_casting", False)):
                return bool(getattr(self, "is_playing", False))
            try:
                return bool(self.player.is_playing())
            except Exception:
                return bool(getattr(self, "is_playing", False))
        except Exception:
            return False

    def set_volume_percent(self, percent: int, persist: bool = True) -> None:
        try:
            percent = int(percent)
        except Exception:
            percent = 100
        percent = max(0, min(100, percent))
        self.volume = percent

        if not self.is_casting:
            try:
                self.player.audio_set_volume(int(percent))
            except Exception:
                pass

        if self.is_casting:
            try:
                caster = getattr(self.casting_manager, "active_caster", None)
                if caster is not None and hasattr(caster, "set_volume"):
                    level = float(percent) / 100.0
                    self.casting_manager.dispatch(caster.set_volume(level))
            except Exception:
                pass
        if persist and self.config_manager:
            try:
                self.config_manager.set("volume", percent)
            except Exception:
                pass

    def adjust_volume(self, delta_percent: int) -> None:
        cur = int(getattr(self, "volume", 100))
        self.set_volume_percent(cur + int(delta_percent), persist=True)

    # ---------------------------------------------------------------------
    # Seek guard (local VLC)
    # ---------------------------------------------------------------------

    def _start_seek_guard(self, target_ms: int) -> None:
        if self.is_casting:
            return
        try:
            t = int(target_ms)
        except Exception:
            return
        if t < 0:
            t = 0
        self._seek_guard_target_ms = int(t)
        self._seek_guard_attempts_left = 10
        self._seek_guard_reapply_left = 3
        try:
            if self._seek_guard_calllater is not None:
                try:
                    self._seek_guard_calllater.Stop()
                except Exception:
                    pass
                self._seek_guard_calllater = None
        except Exception:
            pass
        try:
            self._seek_guard_calllater = wx.CallLater(200, self._seek_guard_tick)
        except Exception:
            self._seek_guard_calllater = None

    def _seek_guard_tick(self) -> None:
        try:
            if self.is_casting:
                return
            left = int(getattr(self, "_seek_guard_attempts_left", 0) or 0)
            if left <= 0:
                return
            
            # 1. Check player state. If Opening (1) or Buffering (2), wait.
            # This prevents the Seek Guard from re-seeking while VLC is still filling its buffer.
            try:
                state = self.player.get_state()
                if state in (1, 2):
                    self._seek_guard_calllater = wx.CallLater(500, self._seek_guard_tick)
                    return
            except Exception:
                pass

            target = getattr(self, "_seek_guard_target_ms", None)
            if target is None:
                return
            target_i = int(target)

            cur = -1
            try:
                cur = int(self.player.get_time() or 0)
            except Exception:
                cur = -1
            
            # 2. Remote streams (YouTube etc) have very unreliable seeking.
            url = getattr(self, "current_url", "") or ""
            is_remote = url.startswith("http") and not ("127.0.0.1" in url or "localhost" in url)
            
            tolerance = 5000 if is_remote else 3000
            
            if cur >= 0 and abs(int(cur) - int(target_i)) <= tolerance:
                self._seek_guard_attempts_left = 0
                return

            # 3. Be extremely lenient with remote: stop trying much faster
            # to avoid the 'repeating' audio loop caused by constant re-seeks.
            if is_remote and left < 7:
                self._seek_guard_attempts_left = 0
                return

            # Limited re-apply: be very conservative with re-seeking.
            try:
                retries = int(getattr(self, "_seek_guard_reapply_left", 0) or 0)
            except Exception:
                retries = 0
            
            if _should_reapply_seek(target_i, cur, tolerance, retries):
                try:
                    self.player.set_time(int(target_i))
                except Exception:
                    pass
                retries -= 1
                self._seek_guard_reapply_left = retries

            try:
                self._pos_ms = int(target_i)
                self._pos_ts = time.monotonic()
            except Exception:
                pass

            left -= 1
            self._seek_guard_attempts_left = int(left)
            if left > 0:
                try:
                    # Increased check interval to 500ms to reduce overhead
                    self._seek_guard_calllater = wx.CallLater(500, self._seek_guard_tick)
                except Exception:
                    self._seek_guard_calllater = None
        except Exception:
            pass


    def _apply_pending_seek(self) -> None:
        try:
            target = self._seek_apply_target_ms
            if target is None:
                return
            target_i = int(target)
        except Exception:
            return

        self._seek_apply_calllater = None
        now = time.monotonic()
        self._seek_apply_last_ts = now

        try:
            self._pos_ms = int(target_i)
            self._pos_ts = float(now)
            self._last_vlc_time_ms = int(target_i)
        except Exception:
            pass

        try:
            self._start_seek_guard(int(target_i))
        except Exception:
            pass

        try:
            self.player.set_time(target_i)
        except Exception:
            pass

        try:
            if self.duration and self.duration > 0:
                pos = max(0.0, min(1.0, float(target_i) / float(self.duration)))
                # Only update slider if we are not dragging it
                if not getattr(self, '_is_dragging_slider', False):
                    self.slider.SetValue(int(pos * 1000))
        except Exception:
            pass
        try:
            # Only update label if we are not dragging (dragging updates it separately)
            if not getattr(self, '_is_dragging_slider', False):
                self.current_time_lbl.SetLabel(self._format_time(target_i))
        except Exception:
            pass

    def _apply_debounced_seek(self) -> None:

        """Apply the most recent seek target once inputs have been idle."""

        try:

            self._seek_apply_calllater = None

        except Exception:

            pass

    

        now = time.monotonic()

        try:

            debounce = float(getattr(self, "_seek_apply_debounce_s", 0.18) or 0.18)

        except Exception:

            debounce = 0.18

        try:

            last_in = float(getattr(self, "_seek_input_ts", 0.0) or 0.0)

        except Exception:

            last_in = 0.0

    

        remain = float(debounce) - float(now - last_in)

        if remain > 0.02:

            try:

                self._seek_apply_calllater = wx.CallLater(max(1, int(remain * 1000)), self._apply_debounced_seek)

            except Exception:

                self._seek_apply_calllater = None

            return

    

        self._apply_pending_seek()

    

    def _apply_seek_time_ms(self, target_ms: int, force: bool = False) -> None:
        print(f"DEBUG: _apply_seek_time_ms target={target_ms} force={force}")
        if self.is_casting:
            return
        try:
            t = int(target_ms)
        except Exception:
            return
        if t < 0:
            t = 0

        self._seek_apply_target_ms = int(t)
        now = time.monotonic()

        try:
            self._seek_input_ts = float(now)
        except Exception:
            pass

        try:
            self._seek_target_ms = int(t)
            self._seek_target_ts = float(now)
        except Exception:
            pass

        try:
            if int(t) + 1200 < int(getattr(self, "_pos_ms", 0) or 0):
                self._pos_allow_backwards_until_ts = float(now) + 3.0
        except Exception:
            pass

        # Cancel any pending debounced apply
        try:
            if self._seek_apply_calllater is not None:
                try:
                    self._seek_apply_calllater.Stop()
                except Exception:
                    pass
                self._seek_apply_calllater = None
        except Exception:
            pass

        if force:
            self._apply_pending_seek()
            return

        # If paused/stopped, apply immediately so it feels instant.
        playing_now = False
        try:
            playing_now = bool(self.player.is_playing())
        except Exception:
            playing_now = bool(getattr(self, "is_playing", False))

        if not playing_now:
            self._apply_pending_seek()
            return

        # While playing, limit how often we ask VLC to seek during a hold.
        try:
            last_apply = float(getattr(self, "_seek_apply_last_ts", 0.0) or 0.0)
        except Exception:
            last_apply = 0.0
        try:
            max_rate = float(getattr(self, "_seek_apply_max_rate_s", 0.35) or 0.35)
        except Exception:
            max_rate = 0.35

        if (now - last_apply) >= float(max_rate):
            self._apply_pending_seek()
            return

        # Otherwise debounce until inputs stop.
        try:
            debounce = float(getattr(self, "_seek_apply_debounce_s", 0.18) or 0.18)
        except Exception:
            debounce = 0.18
        try:
            self._seek_apply_calllater = wx.CallLater(max(1, int(float(debounce) * 1000)), self._apply_debounced_seek)
        except Exception:
            self._seek_apply_calllater = None


    def seek_relative_ms(self, delta_ms: int) -> None:
        if self.is_casting:
            return

        try:
            delta = int(delta_ms)
        except Exception:
            return

        try:
            self._note_user_seek()
        except Exception:
            log.exception("Error noting user seek in seek_relative_ms")

        now = time.monotonic()
        base = None
        try:
            if self._seek_target_ms is not None and (now - float(self._seek_target_ts)) < 1.0:
                base = int(self._seek_target_ms)
        except Exception:
            base = None

        if base is None:

            # Prefer our UI-tracked position (fast), but also consult VLC time so seeks

            # are correct even between slow timer ticks.

            try:

                ui_base = int(getattr(self, "_pos_ms", 0) or 0)

            except Exception:

                ui_base = 0

        

            vlc_base = None

            try:

                v = int(self.player.get_time() or 0)

                if v >= 0:

                    vlc_base = int(v)

            except Exception:

                vlc_base = None

        

            try:

                allow_back = float(getattr(self, "_pos_allow_backwards_until_ts", 0.0) or 0.0)

            except Exception:

                allow_back = 0.0

        

            if int(delta) < 0 or now < float(allow_back):

                # When rewinding (or shortly after), trust the UI target so repeated rewinds chain.

                base = int(ui_base)

            else:

                # Normal forward playback: VLC time may be more up-to-date than our 2s timer,

                # but VLC can briefly report stale/behind values after a seek. Use whichever is ahead.

                if vlc_base is not None:

                    base = int(max(int(ui_base), int(vlc_base)))

                    try:

                        self._pos_ms = int(base)

                        self._last_vlc_time_ms = int(base)

                    except Exception:

                        pass

                else:

                    base = int(ui_base)

        
        target = int(base) + delta

        try:
            if self.duration and self.duration > 0:
                target = max(0, min(int(target), int(self.duration)))
            else:
                target = max(0, int(target))
        except Exception:
            try:
                target = max(0, int(target))
            except Exception:
                return

        try:
            if int(delta) < 0:
                self._pos_allow_backwards_until_ts = float(now) + 3.0
        except Exception:
            pass

        self._seek_target_ms = int(target)
        self._seek_target_ts = float(now)
        try:
            self._pos_ms = int(target)
            self._pos_ts = float(now)
            self._last_vlc_time_ms = int(target)
        except Exception:
            pass

        try:
            if self.duration and self.duration > 0:
                pos = max(0.0, min(1.0, float(target) / float(self.duration)))
                self.slider.SetValue(int(pos * 1000))
            self.current_time_lbl.SetLabel(self._format_time(int(target)))
        except Exception:
            pass

        self._apply_seek_time_ms(int(target), force=False)
        try:
            self._schedule_resume_save_after_seek()
        except Exception:
            log.exception("Error scheduling resume save in seek_relative_ms")

    def play(self) -> None:
        print("DEBUG: play called")
        if not self.has_media_loaded():
            return
        if self.is_casting:
            try:
                self.casting_manager.resume()
            except Exception:
                pass
            self.is_playing = True
        else:
            try:
                try:
                    if getattr(self, "_stopped_needs_resume", False):
                        resume_id = self._get_resume_id()
                        if resume_id:
                            self._maybe_restore_playback_position(str(resume_id), getattr(self, "current_title", None))
                except Exception:
                    log.exception("Error handling resume on play")
                finally:
                    self._stopped_needs_resume = False

                try:
                    self.player.set_pause(0)
                except Exception:
                    pass
                self.player.play()
                self.is_playing = True
                if not self.timer.IsRunning():
                    interval = 500
                    try:
                        if getattr(self, "_pending_resume_seek_ms", None) is not None:
                            interval = 250
                        elif bool(self.config_manager.get("skip_silence", False)):
                            interval = 250
                    except Exception:
                        interval = 500
                    self.timer.Start(int(interval))
            except Exception:
                pass

    def pause(self) -> None:
        print("DEBUG: pause called")
        if not self.has_media_loaded():
            return
        if self.is_casting:
            try:
                self.casting_manager.pause()
                self.is_playing = False
            except Exception:
                pass
        else:
            try:
                try:
                    self.player.set_pause(1)
                except Exception:
                    # Fallback to toggle pause
                    self.player.pause()
                self.is_playing = False
            except Exception:
                pass
        try:
            self._persist_playback_position(force=True)
        except Exception:
            pass

    def stop(self) -> None:
        try:
            self._persist_playback_position(force=True)
        except Exception:
            log.exception("Failed to persist playback position on stop")
        try:
            self._cancel_scheduled_resume_save()
        except Exception:
            log.exception("Error canceling scheduled resume save on stop")
        self._stop_calllater("_seek_apply_calllater", "Error handling seek apply calllater on stop")
        if self.is_casting:
            try:
                self.casting_manager.stop_playback()
            except Exception:
                pass
        else:
            try:
                self.player.stop()
            except Exception:
                pass

        try:
            self.timer.Stop()
        except Exception:
            pass

        self._cancel_silence_scan()
        self.is_playing = False

        try:
            self.slider.SetValue(0)
            self.current_time_lbl.SetLabel("00:00")
            self.total_time_lbl.SetLabel(self._format_time(self.duration) if self.duration else "00:00")
        except Exception:
            pass
        self._stopped_needs_resume = True

    def on_char_hook(self, event: wx.KeyEvent) -> None:
        try:
            key = int(event.GetKeyCode())
        except Exception:
            key = None

        if key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            try:
                if self._is_focus_in_chapter_choice():
                    self._commit_chapter_selection()
                    return
            except Exception:
                pass

        if event.ControlDown() and not event.ShiftDown() and not event.AltDown() and not event.MetaDown():
            if self.is_audio_playing():
                actions = {
                    wx.WXK_UP: lambda: self.adjust_volume(int(getattr(self, "volume_step", 5))),
                    wx.WXK_DOWN: lambda: self.adjust_volume(-int(getattr(self, "volume_step", 5))),
                    wx.WXK_LEFT: lambda: self.seek_relative_ms(-int(getattr(self, "seek_back_ms", 10000))),
                    wx.WXK_RIGHT: lambda: self.seek_relative_ms(int(getattr(self, "seek_forward_ms", 10000))),
                }
                try:
                    if getattr(self, "_media_hotkeys", None) and self._media_hotkeys.handle_ctrl_key(event, actions):
                        return
                except Exception:
                    pass
        event.Skip()

    def on_close(self, event):
        try:
            self.shutdown()
        except Exception:
            log.exception("Error during player shutdown")
        try:
            self.Hide()
        except Exception:
            pass

    def shutdown(self) -> None:
        """Stop playback/timers so the app can exit cleanly."""
        if getattr(self, "_shutdown_done", False):
            return
        self._shutdown_done = True

        try:
            self._persist_playback_position(force=True)
        except Exception:
            log.exception("Failed to persist playback position during shutdown")

        try:
            self._cancel_scheduled_resume_save()
        except Exception:
            log.exception("Error canceling scheduled resume save during shutdown")

        self._stop_calllater("_seek_apply_calllater", "Error handling seek apply calllater during shutdown")
        self._stop_calllater("_seek_guard_calllater", "Error handling seek guard calllater during shutdown")

        try:
            self._cancel_silence_scan()
        except Exception:
            log.exception("Error canceling silence scan during shutdown")

        try:
            self.timer.Stop()
        except Exception:
            log.exception("Error stopping timer during shutdown")

        try:
            if bool(getattr(self, "is_casting", False)):
                try:
                    self.casting_manager.stop_playback()
                except Exception:
                    log.exception("Error stopping casting playback during shutdown")
                try:
                    self.casting_manager.disconnect()
                except Exception:
                    log.exception("Error disconnecting from cast device during shutdown")
        except Exception:
            log.exception("Error during casting shutdown")

        if getattr(self, "player", None) is not None:
            try:
                self.player.stop()
            except Exception:
                log.exception("Error stopping VLC player during shutdown")
            try:
                self.player.release()
            except Exception:
                log.exception("Error releasing VLC player during shutdown")

        if getattr(self, "instance", None) is not None:
            try:
                self.instance.release()
            except Exception:
                log.exception("Error releasing VLC instance during shutdown")

        try:
            if getattr(self, "casting_manager", None) is not None:
                self.casting_manager.stop()
        except Exception:
            log.exception("Error stopping casting manager during shutdown")

        try:
            if getattr(self, "_media_hotkeys", None):
                self._media_hotkeys.stop()
        except Exception:
            log.exception("Error stopping media hotkeys during shutdown")
