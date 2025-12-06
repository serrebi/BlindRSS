import wx
import threading
import yt_dlp
import tempfile
import os
import vlc
import sys
import subprocess
import shutil
import requests
import re
import time
from urllib.parse import urlsplit, urlunsplit
from core import utils

class MediaPlayerPanel(wx.Panel):
    def __init__(self, parent, config_manager=None):
        super().__init__(parent)
        self.config_manager = config_manager
        self.skip_silence_enabled = bool(self.config_manager.get("skip_silence", False)) if self.config_manager else False
        self.speed_values = utils.build_playback_speeds()
        self.playback_speed = self._coerce_speed(self.config_manager.get("playback_speed", 1.0) if self.config_manager else 1.0)
        self.last_good_speed = self.playback_speed
        
        # Create controls first (for tab order)
        
        # UI Sizer
        self.sizer = wx.BoxSizer(wx.VERTICAL)
        
        # Controls Row
        self.ctrl_sizer = wx.BoxSizer(wx.HORIZONTAL)
        
        self.btn_play = wx.Button(self, label="Play")
        self.btn_pause = wx.Button(self, label="Pause")
        self.btn_stop = wx.Button(self, label="Stop")
        
        self.ctrl_sizer.Add(self.btn_play, 0, wx.ALL, 5)
        self.ctrl_sizer.Add(self.btn_pause, 0, wx.ALL, 5)
        self.ctrl_sizer.Add(self.btn_stop, 0, wx.ALL, 5)
        
        # Seek Slider
        self.slider = wx.Slider(self, minValue=0, maxValue=100)
        
        # Volume Slider
        self.vol_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.st_vol = wx.StaticText(self, label="Volume")
        self.volume_slider = wx.Slider(self, value=100, minValue=0, maxValue=100, style=wx.SL_HORIZONTAL)
        self.vol_sizer.Add(self.st_vol, 0, wx.ALIGN_CENTER_VERTICAL|wx.ALL, 5)
        self.vol_sizer.Add(self.volume_slider, 1, wx.EXPAND|wx.ALL, 5)

        # Playback speed
        self.speed_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.st_speed = wx.StaticText(self, label="Speed")
        self.speed_combo = wx.ComboBox(
            self,
            choices=[self._display_speed(v) for v in self.speed_values],
            style=wx.CB_READONLY
        )
        self.speed_combo.SetValue(self._display_speed(self.playback_speed))
        self.speed_sizer.Add(self.st_speed, 0, wx.ALIGN_CENTER_VERTICAL|wx.ALL, 5)
        self.speed_sizer.Add(self.speed_combo, 1, wx.EXPAND|wx.ALL, 5)

        # Skip silence toggle
        self.chk_skip_silence = wx.CheckBox(self, label="Skip silence (requires ffmpeg)")
        self.chk_skip_silence.SetValue(self.skip_silence_enabled)
        
        # Chapters list (use simple list for better accessibility)
        self.chapters = wx.ListBox(self)

        # Status
        self.st_status = wx.StaticText(self, label="Ready")

        # VLC backend (no visible widget)
        self.vlc_instance = self._init_vlc()
        self.player = self.vlc_instance.media_player_new() if self.vlc_instance else None
        self._bind_vlc_events()
        
        # Add to sizer in visual order
        self.sizer.Add(self.ctrl_sizer, 0, wx.ALIGN_CENTER)
        self.sizer.Add(self.slider, 0, wx.EXPAND|wx.ALL, 5)
        self.sizer.Add(self.chapters, 1, wx.EXPAND|wx.LEFT|wx.RIGHT, 5)
        self.sizer.Add(self.vol_sizer, 0, wx.EXPAND|wx.ALL, 5)
        self.sizer.Add(self.speed_sizer, 0, wx.EXPAND|wx.LEFT|wx.RIGHT, 5)
        self.sizer.Add(self.chk_skip_silence, 0, wx.LEFT|wx.RIGHT, 5)
        self.sizer.Add(self.st_status, 0, wx.ALIGN_CENTER|wx.ALL, 5)
        
        self.SetSizer(self.sizer)
        
        # Bindings
        self.Bind(wx.EVT_BUTTON, self.on_play, self.btn_play)
        self.Bind(wx.EVT_BUTTON, self.on_pause, self.btn_pause)
        self.Bind(wx.EVT_BUTTON, self.on_stop, self.btn_stop)
        self.Bind(wx.EVT_SLIDER, self.on_seek, self.slider)
        self.Bind(wx.EVT_SLIDER, self.on_volume_change, self.volume_slider)
        self.Bind(wx.EVT_CHECKBOX, self.on_skip_silence_toggle, self.chk_skip_silence)
        self.Bind(wx.EVT_COMBOBOX, self.on_speed_change, self.speed_combo)
        
        self.chapters.Bind(wx.EVT_LISTBOX_DCLICK, self.on_chapter_activated)
        self.chapters.Bind(wx.EVT_KEY_DOWN, self.on_chapter_key)
        self.chapters.Bind(wx.EVT_CHAR_HOOK, self.on_chapter_key)
        
        self.timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self.on_timer, self.timer)
        
        self.safety_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self.on_safety_timer, self.safety_timer)
        
        # Keyboard shortcuts
        self.Bind(wx.EVT_CHAR_HOOK, self.on_key)
        self._bind_accessibility_shortcuts()

        self.current_url = None
        self.current_chapters = []
        self.fallback_active = False
        self.temp_file = None
        self.installing_vlc = False
        
        # Set initial volume on backend
        if self.player:
            self.player.audio_set_volume(self.volume_slider.GetValue())

    def _init_vlc(self):
        try:
            return vlc.Instance(self._vlc_options())
        except Exception as e:
            # Try to install VLC automatically in the background, then retry once
            if not self.installing_vlc:
                self.installing_vlc = True
                threading.Thread(target=self._background_install_and_retry, daemon=True).start()
            return None

    def _vlc_options(self):
        """
        Enable VLC time-stretching to keep speech natural when changing speed.
        """
        return [
            "--audio-time-stretch",
            "--http-reconnect",
            "--network-caching=1500",
            "--quiet",
            "--verbose=0",
            "--no-stats"
        ]

    def _vlc_media_options(self):
        """
        Per-media options to stabilize HTTP playback (mitigate cancellation/teardown noise).
        """
        return [
            "http-reconnect=true",
            "network-caching=1500"
        ]

    def _background_install_and_retry(self):
        if self._install_vlc():
            try:
                self._maybe_add_windows_vlc_path()
                inst = vlc.Instance(self._vlc_options())
                player = inst.media_player_new()
                wx.CallAfter(self._on_vlc_ready, inst, player)
                return
            except Exception as e:
                pass
        wx.CallAfter(self._on_vlc_install_failed)
        self.installing_vlc = False

    def _on_vlc_ready(self, inst, player):
        self.vlc_instance = inst
        self.player = player
        self._bind_vlc_events()
        self.installing_vlc = False
        # Set initial volume based on slider
        if self.player:
            self.player.audio_set_volume(self.volume_slider.GetValue())
        # If a URL was pending when we failed earlier, try to resume
        if getattr(self, "pending_url", None):
            self._load_direct(self.pending_url)

    def _on_vlc_install_failed(self):
        self.installing_vlc = False
        self.st_status.SetLabel("VLC not available. Install manually.")

    def _install_vlc(self):
        """Attempt to install VLC via common package managers. Returns True if command succeeded."""
        cmds = []
        plat = sys.platform
        if plat.startswith("win"):
            if shutil.which("winget"):
                cmds.append(["winget", "install", "-e", "--id", "VideoLAN.VLC", "--silent"])
        elif plat == "darwin":
            if shutil.which("brew"):
                cmds.append(["brew", "install", "--cask", "vlc"])
        else:
            # Linux variants
            if shutil.which("apt-get"):
                cmds.append(["sudo", "apt-get", "update"])
                cmds.append(["sudo", "apt-get", "install", "-y", "vlc"])
            elif shutil.which("apt"):
                cmds.append(["sudo", "apt", "update"])
                cmds.append(["sudo", "apt", "install", "-y", "vlc"])
            elif shutil.which("pacman"):
                cmds.append(["sudo", "pacman", "-Syu", "--noconfirm", "vlc"])
            elif shutil.which("dnf"):
                cmds.append(["sudo", "dnf", "install", "-y", "vlc"])
            elif shutil.which("zypper"):
                cmds.append(["sudo", "zypper", "--non-interactive", "install", "vlc"])
        success = False
        for cmd in cmds:
            try:
                creationflags = 0
                if sys.platform.startswith("win") and hasattr(subprocess, "CREATE_NO_WINDOW"):
                    creationflags = subprocess.CREATE_NO_WINDOW
                result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=600, creationflags=creationflags)
                if result.returncode == 0:
                    success = True
            except Exception:
                pass
            if success:
                break
        # If install succeeded but vlc binary not in PATH (Windows), add likely path
        if success and plat.startswith("win"):
            self._maybe_add_windows_vlc_path()
        return success

    def _maybe_add_windows_vlc_path(self):
        if sys.platform.startswith("win"):
            candidates = [
                r"C:\\Program Files\\VideoLAN\\VLC",
                r"C:\\Program Files (x86)\\VideoLAN\\VLC"
            ]
            for path in candidates:
                if os.path.isdir(path) and path not in os.environ.get("PATH", ""):
                    os.environ["PATH"] = path + os.pathsep + os.environ.get("PATH", "")

    def _bind_vlc_events(self):
        if not self.player:
            return
        em = self.player.event_manager()
        em.event_attach(vlc.EventType.MediaPlayerEndReached, self._on_vlc_finished)
        em.event_attach(vlc.EventType.MediaPlayerEncounteredError, self._on_vlc_error)
        em.event_attach(vlc.EventType.MediaPlayerPlaying, self._on_vlc_playing)

    def _on_vlc_finished(self, event):
        wx.CallAfter(self.on_media_finished, None)

    def _on_vlc_error(self, event):
        wx.CallAfter(self._trigger_fallback, getattr(self, "pending_url", None))

    def _on_vlc_playing(self, event):
        wx.CallAfter(self._mark_playing)

    def on_key(self, event):
        code = event.GetKeyCode()
        ctrl = event.ControlDown()
        
        if code == wx.WXK_SPACE:
            if self.st_status.GetLabel() == "Playing":
                self.on_pause(None)
            else:
                self.on_play(None)
        elif code == wx.WXK_ESCAPE:
             self.GetParent().Close()
        elif ctrl and code == wx.WXK_UP:
            self.adjust_volume(10)
        elif ctrl and code == wx.WXK_DOWN:
            self.adjust_volume(-10)
        elif ctrl and code == wx.WXK_RIGHT:
            self.adjust_seek(10000) # 10s
        elif ctrl and code == wx.WXK_LEFT:
            self.adjust_seek(-10000)
        else:
            event.Skip()

    def adjust_volume(self, delta):
        val = self.volume_slider.GetValue() + delta
        val = max(0, min(100, val))
        self.volume_slider.SetValue(val)
        self.on_volume_change(None)

    def on_volume_change(self, event):
        val = self.volume_slider.GetValue()
        if self.player:
            self.player.audio_set_volume(val)

    def on_speed_change(self, event):
        new_speed = self._parse_speed(self.speed_combo.GetValue())
        if new_speed is None:
            return
        self.set_playback_speed(new_speed, update_config=True, apply_now=True)

    def set_playback_speed(self, speed, update_config=True, apply_now=True):
        coerced = self._coerce_speed(speed)
        self.playback_speed = coerced
        self.speed_combo.SetValue(self._display_speed(coerced))
        success = True
        if apply_now:
            success = self._apply_playback_speed()
        if success:
            self.last_good_speed = coerced
        if update_config and self.config_manager:
            try:
                self.config_manager.set("playback_speed", coerced)
            except Exception:
                pass

    def _apply_playback_speed(self):
        # If backend isn't ready yet, keep the choice; it'll be applied on play.
        if not self.player:
            return True
        try:
            res = self.player.set_rate(float(self.playback_speed))
            if res == -1:
                self.st_status.SetLabel(f"Speed {self.playback_speed:.2f}x unsupported by backend.")
                return False
            # Nudge the player with current timestamp so the new rate applies instantly.
            try:
                if self.player.get_state() == vlc.State.Playing:
                    pos = self.player.get_time()
                    if pos is not None and pos >= 0:
                        self.player.set_time(pos)
            except Exception:
                pass
            return True
        except Exception:
            return False

    # --- Accessibility / keyboard helpers ---
    def _bind_accessibility_shortcuts(self):
        # Buttons: allow Space / Enter to activate when focused
        btn_map = {
            self.btn_play: self.on_play,
            self.btn_pause: self.on_pause,
            self.btn_stop: self.on_stop
        }
        for btn, handler in btn_map.items():
            btn.Bind(wx.EVT_CHAR_HOOK, lambda e, h=handler: self._on_button_key(e, h))

        # Sliders: allow arrow keys to adjust
        self.slider.Bind(wx.EVT_CHAR_HOOK, self.on_seek_key)
        self.volume_slider.Bind(wx.EVT_CHAR_HOOK, self.on_volume_key)

        # Speed combo: handle up/down/home/end explicitly to avoid platform quirks
        self.speed_combo.Bind(wx.EVT_KEY_DOWN, self.on_speed_keydown)

    def _on_button_key(self, event, handler):
        code = event.GetKeyCode()
        if code in (wx.WXK_SPACE, wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            handler(None)
            return
        event.Skip()

    def on_seek_key(self, event):
        code = event.GetKeyCode()
        handled = False
        step_ms = 5000  # 5 seconds per arrow step
        if code == wx.WXK_LEFT:
            self.adjust_seek(-step_ms)
            handled = True
        elif code == wx.WXK_RIGHT:
            self.adjust_seek(step_ms)
            handled = True
        if handled:
            return
        event.Skip()

    def on_volume_key(self, event):
        code = event.GetKeyCode()
        handled = False
        step = 5
        if code in (wx.WXK_RIGHT, wx.WXK_UP):
            self.adjust_volume(step)
            handled = True
        elif code in (wx.WXK_LEFT, wx.WXK_DOWN):
            self.adjust_volume(-step)
            handled = True
        if handled:
            return
        event.Skip()

    def on_speed_keydown(self, event):
        code = event.GetKeyCode()
        if code in (wx.WXK_UP, wx.WXK_LEFT):
            if self._bump_speed(-1):
                return
        elif code in (wx.WXK_DOWN, wx.WXK_RIGHT):
            if self._bump_speed(1):
                return
        elif code == wx.WXK_HOME:
            self.set_playback_speed(self.speed_values[0], update_config=True, apply_now=True)
            return
        elif code == wx.WXK_END:
            self.set_playback_speed(self.speed_values[-1], update_config=True, apply_now=True)
            return
        event.Skip()

    def _bump_speed(self, delta):
        if not self.speed_values:
            return False
        current = self._parse_speed(self.speed_combo.GetValue())
        if current is None:
            current = self.playback_speed
        try:
            idx = self.speed_values.index(self._coerce_speed(current))
        except ValueError:
            idx = 0
        new_idx = idx + delta
        if new_idx < 0 or new_idx >= len(self.speed_values):
            return False
        new_speed = self.speed_values[new_idx]
        self.set_playback_speed(new_speed, update_config=True, apply_now=True)
        return True


    def _coerce_speed(self, speed):
        try:
            speed = float(speed)
        except Exception:
            speed = 1.0
        # Snap near-1.0 selections to exactly 1.00 to avoid 0.98 rounding
        if abs(speed - 1.0) < 0.02:
            speed = 1.0
        if not self.speed_values:
            return 1.0
        speed = max(self.speed_values[0], min(self.speed_values[-1], speed))
        return min(self.speed_values, key=lambda v: abs(v - speed))

    def _parse_speed(self, text):
        import re
        m = re.search(r"[0-9]+(?:\.[0-9]+)?", str(text))
        if not m:
            return None
        try:
            return float(m.group(0))
        except Exception:
            return None

    def _format_speed(self, speed):
        try:
            return f"{float(speed):.2f}"
        except Exception:
            return "1.00"

    def _display_speed(self, speed):
        val = self._format_speed(speed)
        if abs(float(val) - 1.0) < 1e-6:
            return f"Normal ({val}x)"
        return f"{val}x"

    def on_skip_silence_toggle(self, event):
        self.skip_silence_enabled = self.chk_skip_silence.GetValue()
        if self.config_manager:
            try:
                self.config_manager.set("skip_silence", self.skip_silence_enabled)
            except Exception as e:
                pass

    def adjust_seek(self, delta_ms):
        if not self.player:
            return
        current = self.player.get_time() or 0
        new_pos = current + delta_ms
        length = self.player.get_length() or 0
        if length > 0:
            new_pos = max(0, min(length, new_pos))
        self.player.set_time(max(0, int(new_pos)))
        self.slider.SetValue(max(0, int(new_pos)))

    def load_media(self, url, is_youtube=False, chapters=None):
        self.on_stop(None)
        self.st_status.SetLabel("Loading...")
        self.safety_attempts = 0
        url = self._sanitize_url(url)
        self.pending_url = url
        # If no chapters provided, try to extract from media (ID3 CHAP)
        if not chapters:
            chapters = self._maybe_fetch_chapters(url, is_youtube)
        self._set_chapters(chapters or [])

        if is_youtube:
            threading.Thread(target=self._resolve_youtube, args=(url,), daemon=True).start()
        else:
            if self.skip_silence_enabled:
                # Attempt streaming through ffmpeg filter, with fallback to download+trim
                threading.Thread(target=self._stream_with_skip_silence, args=(url,), daemon=True).start()
            else:
                self._load_direct(url)

    def _resolve_youtube(self, url):
        try:
            import shutil
            import os
            url = self._sanitize_url(url)
            ydl_opts = {'format': 'bestaudio/best', 'quiet': True}
            
            # Explicitly find ffmpeg if possible
            ffmpeg_path = shutil.which("ffmpeg")
            if ffmpeg_path:
                ydl_opts['ffmpeg_location'] = os.path.dirname(ffmpeg_path)
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                audio_url = info['url']
                wx.CallAfter(self._load_direct, audio_url)
        except Exception as e:
            wx.CallAfter(self.st_status.SetLabel, f"Error: {e}")

    def _load_direct(self, url):
        url = self._sanitize_url(url)
        if not url:
            self.st_status.SetLabel("No media URL provided.")
            return
        if not self.player:
            if self.installing_vlc:
                self.st_status.SetLabel("Installing VLC in background...")
            else:
                self.st_status.SetLabel("VLC backend unavailable.")
            return
        
        self.fallback_active = False
        self.safety_timer.Stop()

        if self.skip_silence_enabled:
            # We should never reach here now; handled earlier
            return
        
        if not self._set_media(url):
            self._trigger_fallback(url)
            return

        self.slider.SetValue(0)
        self.st_status.SetLabel("Loading media stream...")
        self._start_play()

    def _start_play(self):
        if not self.player:
            self.st_status.SetLabel("VLC backend unavailable.")
            return
        res = self.player.play()
        if res == -1:
            self._trigger_fallback(getattr(self, "pending_url", None))
            return
        self._apply_playback_speed()
        self.safety_attempts = 0
        self.safety_timer.Start(500)
        self.timer.Start(500)
        self.st_status.SetLabel("Buffering...")

    def _mark_playing(self):
        self.st_status.SetLabel("Playing")
        self._apply_playback_speed()
        if not self.timer.IsRunning():
            self.timer.Start(500)
        if self.safety_timer.IsRunning():
            self.safety_timer.Stop()

    def _set_media(self, url_or_path):
        try:
            media = self.vlc_instance.media_new(url_or_path)
            opts = list(self._vlc_media_options())
            # Friendly UA can help some hosts keep connections open
            if isinstance(url_or_path, str) and url_or_path.startswith(("http://", "https://")):
                try:
                    ua = utils.HEADERS.get("User-Agent", None)
                    if ua:
                        opts.append(f"http-user-agent={ua}")
                except Exception:
                    pass
            for opt in opts:
                media.add_option(f":{opt}")
            self.player.set_media(media)
            return True
        except Exception as e:
            return False

    def on_safety_timer(self, event):
        if not self.player:
            return
        try:
            state = self.player.get_state()
        except Exception:
            state = None

        if state == vlc.State.Playing:
            self.safety_timer.Stop()
            self.st_status.SetLabel("Playing")
            return

        if state == vlc.State.Error:
            self.safety_timer.Stop()
            self._trigger_fallback(getattr(self, "pending_url", None))
            return

        if getattr(self, "safety_attempts", 0) >= 10:
            self.safety_timer.Stop()
            self._trigger_fallback(getattr(self, "pending_url", None))
            return

        self.safety_attempts += 1
        self.player.play()

    def on_play(self, event):
        if self.player:
            self.player.play()
            self.timer.Start(500)
            self.st_status.SetLabel("Playing")
            self.btn_pause.SetFocus()

    def on_pause(self, event):
        if self.player:
            self.player.pause()
        self.timer.Stop()
        self.st_status.SetLabel("Paused")
        self.btn_play.SetFocus()

    def on_stop(self, event):
        if self.player:
            self.player.stop()
        # Stop any running ffmpeg stream transcoder
        if hasattr(self, "ffmpeg_proc") and self.ffmpeg_proc:
            try:
                self.ffmpeg_proc.kill()
            except Exception:
                pass
            self.ffmpeg_proc = None
        self.timer.Stop()
        self.safety_timer.Stop()
        self.slider.SetValue(0)
        self.st_status.SetLabel("Stopped")
        self.btn_play.SetFocus()

    def on_media_finished(self, event):
        self.on_stop(None)
        self.st_status.SetLabel("Finished")

    def on_timer(self, event):
        if not self.player:
            return
        offset = self.player.get_time() or 0
        length = self.player.get_length() or 0
        if length > 0:
            if self.slider.GetMax() != length:
                self.slider.SetRange(0, length)
            self.slider.SetValue(max(0, offset))
        # Highlight current chapter
        self._highlight_chapter((offset or 0) / 1000.0)

    def on_seek(self, event):
        offset = self.slider.GetValue()
        if self.player:
            self.player.set_time(int(offset))

    def _trigger_fallback(self, url):
        if self.fallback_active:
            return
        if not url:
            self.st_status.SetLabel("No media URL available for playback.")
            return
        self.fallback_active = True
        label = "Streaming failed. Downloading..."
        if self.skip_silence_enabled:
            label += " (skip silence)"
        self.st_status.SetLabel(label)
        threading.Thread(target=self._download_and_play_thread, args=(url,), daemon=True).start()

    def _download_and_play_thread(self, url, apply_skip=None, label=None):
        url = self._sanitize_url(url)
        use_skip = self.skip_silence_enabled if apply_skip is None else apply_skip
        try:
            # Download to temp file
            resp = utils.safe_requests_get(url, stream=True, timeout=30)
            resp.raise_for_status()
            
            suffix = self._guess_suffix(url, resp.headers.get("Content-Type"))
            
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                for chunk in resp.iter_content(chunk_size=8192):
                    tmp.write(chunk)
                tmp_path = tmp.name
            
            target_path = tmp_path
            final_label = label or "Playing Downloaded File"
            if use_skip:
                processed, removed_sec, status = self._apply_skip_silence(tmp_path)
                if processed != tmp_path:
                    target_path = processed
                if status == "ffmpeg_not_found":
                    final_label = "ffmpeg missing; playing original audio."
                elif status == "ffmpeg_error":
                    final_label = "Skip-silence failed; playing original audio."
                elif removed_sec is not None and removed_sec >= 0.1:
                    trimmed = int(removed_sec)
                    final_label = f"Playing (silence skipped ~{trimmed}s)"
                else:
                    final_label = "No silence detected; playing original audio."
            
            wx.CallAfter(self._load_local_file, target_path, final_label)
            
        except Exception as e:
            wx.CallAfter(self.st_status.SetLabel, "Download failed.")

    def _load_local_file(self, path, label="Playing Downloaded File", stop_current=True):
        # Clean up previous temp if exists
        if self.temp_file and self.temp_file != path and os.path.exists(self.temp_file):
            try:
                os.remove(self.temp_file)
            except: pass
            
        self.temp_file = path
        self.st_status.SetLabel(label)
        if not self.player:
            self.st_status.SetLabel("VLC backend unavailable.")
            return

        if stop_current and self.player:
            try:
                self.player.stop()
            except Exception:
                pass

        if self._set_media(path):
            self._start_play()
            self.st_status.SetLabel(label)
        else:
            self.st_status.SetLabel("Playback failed even after download.")

    def _guess_suffix(self, url, content_type=None):
        lower = (url or "").lower()
        for ext in (".mp3", ".m4a", ".m4b", ".aac", ".ogg", ".opus", ".wav", ".flac"):
            if lower.endswith(ext):
                return ext
        if content_type:
            ctype = content_type.lower()
            mapping = {
                "audio/mpeg": ".mp3",
                "audio/mp4": ".m4a",
                "audio/aac": ".aac",
                "audio/ogg": ".ogg",
                "audio/opus": ".opus",
                "audio/x-wav": ".wav",
                "audio/wav": ".wav",
                "audio/flac": ".flac"
            }
            for prefix, ext in mapping.items():
                if ctype.startswith(prefix):
                    return ext
        return ".mp3"

    def _apply_skip_silence(self, input_path):
        ffmpeg_bin = shutil.which("ffmpeg")
        if not ffmpeg_bin:
            wx.CallAfter(self.st_status.SetLabel, "ffmpeg not found; playing original audio.")
            return input_path, None, "ffmpeg_not_found"

        before_dur = self._probe_duration(input_path)
        suffix = os.path.splitext(input_path)[1] or ".mp3"
        out_tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        out_tmp.close()
        cmd = [
            ffmpeg_bin, "-hide_banner", "-loglevel", "error", "-y",
            "-i", input_path,
            "-af", "silenceremove=start_periods=0:stop_periods=-1:stop_duration=0.35:stop_threshold=-45dB,adelay=250|250,apad=pad_dur=0.25",
            out_tmp.name
        ]
        try:
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if result.returncode != 0:
                try:
                    os.remove(out_tmp.name)
                except Exception:
                    pass
                return input_path, None, "ffmpeg_error"
            after_dur = self._probe_duration(out_tmp.name)
            try:
                if input_path != out_tmp.name and os.path.exists(input_path):
                    os.remove(input_path)
            except Exception:
                pass
            removed = None
            if before_dur and after_dur:
                removed = max(0.0, before_dur - after_dur)
            return out_tmp.name, removed, "ok"
        except Exception as e:
            try:
                os.remove(out_tmp.name)
            except Exception:
                pass
            return input_path, None, "ffmpeg_error"

    def _probe_duration(self, path):
        ffprobe_bin = shutil.which("ffprobe")
        if not ffprobe_bin:
            return None
        try:
            result = subprocess.run(
                [ffprobe_bin, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", path],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )
            if result.returncode != 0:
                return None
            return float(result.stdout.strip())
        except Exception:
            return None

    def _sanitize_url(self, url: str):
        if not url:
            return url
        # strip any whitespace/newlines anywhere
        cleaned = re.sub(r"\s+", "", url)
        try:
            parts = urlsplit(cleaned)
            # Re-quote path and query safely
            path = requests.utils.requote_uri(parts.path)
            query = requests.utils.requote_uri(parts.query)
            return urlunsplit((parts.scheme, parts.netloc, path, query, parts.fragment))
        except Exception:
            return cleaned

    def _stream_with_skip_silence(self, url):
        """
        Stream through ffmpeg with silenceremove, writing to a growing temp file.
        Instead of ffmpeg doing HTTP (which some hosts reject), we fetch with requests
        (browser-like headers) and feed ffmpeg via stdin. VLC plays the growing file.
        """
        url = self._sanitize_url(url)
        if not shutil.which("ffmpeg"):
            wx.CallAfter(self.st_status.SetLabel, "ffmpeg not found; streaming without skip-silence.")
            wx.CallAfter(self._load_direct, url)
            return

        try:
            resp = utils.safe_requests_get(url, stream=True, timeout=10)
            resp.raise_for_status()
        except Exception as e:
            wx.CallAfter(self._trigger_fallback, url)
            return

        # Use MP3 output for streaming-friendly incremental reads
        suffix = ".mp3"
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        tmp_path = tmp.name
        tmp.close()
        # ensure fresh target
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass

        ffmpeg_bin = shutil.which("ffmpeg")
        err_tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".log")
        err_tmp_path = err_tmp.name
        err_tmp.close()
        print(f"[SKIPSIL] ffmpeg streaming start -> {tmp_path}")
        self.ffmpeg_proc = subprocess.Popen(
            [
                ffmpeg_bin, "-hide_banner", "-loglevel", "warning",
                "-y",
                "-i", "pipe:0",
                "-vn",
                "-af", "silenceremove=start_periods=0:stop_periods=-1:stop_duration=0.35:stop_threshold=-45dB,adelay=250|250,apad=pad_dur=0.25",
                "-acodec", "libmp3lame",
                "-b:a", "160k",
                "-f", "mp3",
                tmp_path
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=open(err_tmp_path, "wb"),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )

        started_playback = False
        bytes_written = 0
        try:
            for chunk in resp.iter_content(chunk_size=8192):
                if not chunk:
                    continue
                bytes_written += len(chunk)
                if self.ffmpeg_proc.poll() is not None:
                    print(f"[SKIPSIL] ffmpeg exited early, bytes_written={bytes_written}")
                    break
                try:
                    self.ffmpeg_proc.stdin.write(chunk)
                except Exception as e:
                    print(f"[SKIPSIL] ffmpeg stdin write failed: {e}")
                    break

                cur_size = os.path.getsize(tmp_path) if os.path.exists(tmp_path) else 0
                if not started_playback and cur_size > 96_000:
                    started_playback = True
                    print(f"[SKIPSIL] starting playback at size {cur_size} bytes")
                    wx.CallAfter(self.st_status.SetLabel, "Streaming (skip silence)...")
                    wx.CallAfter(self._load_local_file, tmp_path, "Streaming (skip silence)", False)

            # finish ffmpeg
            try:
                self.ffmpeg_proc.stdin.close()
            except Exception:
                pass
            self.ffmpeg_proc.wait(timeout=5)
        except Exception as e:
            print(f"[SKIPSIL] streaming loop error: {e}")
        finally:
            if self.ffmpeg_proc and self.ffmpeg_proc.poll() is None:
                try:
                    self.ffmpeg_proc.kill()
                except Exception:
                    pass
            self.ffmpeg_proc = None

        if not started_playback:
            # No data ever got big enough; fall back
            tail = ""
            try:
                with open(err_tmp_path, "rb") as f:
                    tail = f.read(500).decode(errors="ignore")
            except Exception:
                pass
            print(f"[SKIPSIL] ffmpeg produced no playable data. bytes_written={bytes_written}. stderr: {tail}")
            wx.CallAfter(self._trigger_fallback, url)
        # Cleanup err log
        try:
            os.remove(err_tmp_path)
        except Exception:
            pass

    def update_chapters(self, chapters):
        wx.CallAfter(self._set_chapters, chapters)

    def _set_chapters(self, chapters):
        self.current_chapters = chapters
        self.chapters.Clear()
        for ch in chapters:
            start = ch.get("start", 0)
            mins = int(start // 60)
            secs = int(start % 60)
            start_str = f"{mins:02d}:{secs:02d}"
            title = ch.get("title", "")
            display = f"{start_str}  {title}"
            self.chapters.Append(display)
            # store ms in a parallel list by stuffing into client data map
        self.chapters_ms = [int((ch.get("start", 0) or 0) * 1000) for ch in chapters]

    def on_chapter_activated(self, event):
        # For ListBox, selection is on the event
        idx = event.GetSelection()
        self._activate_chapter(idx)

    def on_chapter_key(self, event):
        code = event.GetKeyCode()
        if code in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            idx = self.chapters.GetSelection()
            if idx != -1:
                self._activate_chapter(idx)
                return
        event.Skip()

    def _activate_chapter(self, idx):
        if idx < 0 or idx >= self.chapters.GetCount():
            return
        ms = self.chapters_ms[idx] if hasattr(self, "chapters_ms") and idx < len(self.chapters_ms) else 0
        try:
            if self.player:
                self.player.set_time(ms)
                self.slider.SetValue(ms)
                self.player.play()
                # restart timer so highlight follows immediately
                self.timer.Start(500)
        except Exception as e:
            pass

    def _highlight_chapter(self, current_seconds):
        if not self.current_chapters:
            return
        active_idx = -1
        for i, ch in enumerate(self.current_chapters):
            start = ch.get("start", 0)
            next_start = self.current_chapters[i+1].get("start", 1e12) if i+1 < len(self.current_chapters) else 1e12
            if start <= current_seconds < next_start:
                active_idx = i
                break
        if active_idx >= 0 and self.chapters.GetSelection() != active_idx:
            self.chapters.SetSelection(active_idx)
            self.chapters.EnsureVisible(active_idx)

    def _maybe_fetch_chapters(self, url, is_youtube):
        if is_youtube or not url or not url.lower().endswith((".mp3", ".m4a", ".m4b", ".aac", ".ogg", ".opus")):
            return []
        try:
            import requests, io
            from mutagen.id3 import ID3
            head = requests.get(url, headers={"Range": "bytes=0-4000000"}, timeout=12).content
            id3 = ID3(io.BytesIO(head))
            chapters = []
            for frame in id3.getall("CHAP"):
                start = frame.start_time / 1000.0 if frame.start_time else 0
                title_ch = ""
                tit2 = frame.sub_frames.get("TIT2")
                if tit2 and tit2.text:
                    title_ch = tit2.text[0]
                chapters.append({"start": float(start), "title": title_ch, "href": None})
            return chapters
        except Exception:
            return []

class PlayerFrame(wx.Frame):
    def __init__(self, parent, config_manager=None):
        super().__init__(parent, title="Media Player", size=(400, 200))
        self.panel = MediaPlayerPanel(self, config_manager)
        
        # Center the panel
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(self.panel, 1, wx.EXPAND)
        self.SetSizer(sizer)
        
        self.Bind(wx.EVT_CLOSE, self.on_close)

    def on_close(self, event):
        self.Hide() # Just hide, don't destroy
        
    def load_media(self, url, is_youtube=False, chapters=None):
        self.panel.load_media(url, is_youtube, chapters)
        if not self.IsShown():
            self.Show()
            self.Raise()

    def update_chapters(self, chapters):
        self.panel.update_chapters(chapters)

    def stop(self):
        self.panel.on_stop(None)

    def set_playback_speed(self, speed):
        self.panel.set_playback_speed(speed, update_config=False, apply_now=True)
