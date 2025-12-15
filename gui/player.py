import wx
import vlc
import threading
import socket
import time
from core import utils
from core.casting import CastingManager
from urllib.parse import urlparse
from core.range_cache_proxy import get_range_cache_proxy


class CastDialog(wx.Dialog):
    def __init__(self, parent, manager: CastingManager):
        super().__init__(parent, title="Cast to Device", size=(400, 300))
        self.manager = manager
        self.devices = []
        
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
            self.EndModal(wx.ID_OK)

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
        
        # VLC Instance
        # Extra caching helps on high-latency streams and makes seeking less stuttery.
        cache_ms = int(self.config_manager.get("vlc_network_caching_ms", 5000))
        if cache_ms < 0:
            cache_ms = 0
        file_cache_ms = max(2000, cache_ms)
        self.instance = vlc.Instance(
            '--no-video',
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
        
        self.timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self.on_timer, self.timer)
        
        self.is_playing = False
        self.duration = 0
        self.current_chapters = []
        self.chapter_marks = []
        self.current_url = None
        self.current_title = "No Track Loaded"

        # Range-cache proxy recovery state
        self._last_orig_url = None
        self._last_vlc_url = None
        self._last_used_range_proxy = False
        self._last_range_proxy_headers = None
        self._last_range_proxy_cache_dir = None
        self._last_range_proxy_prefetch_kb = None
        self._range_proxy_retry_count = 0
        self._last_load_chapters = None
        self._last_load_title = None
        
        # Playback speed handling (init value before UI uses it)
        self.playback_speed = float(self.config_manager.get("playback_speed", 1.0))
        # Media key settings
        self.volume = int(self.config_manager.get("volume", 100))
        self.volume_step = int(self.config_manager.get("volume_step", 5))
        self.seek_back_ms = int(self.config_manager.get("seek_back_ms", 10000))
        self.seek_forward_ms = int(self.config_manager.get("seek_forward_ms", 30000))

        self.init_ui()
        self.Bind(wx.EVT_CLOSE, self.on_close)
        self.Bind(wx.EVT_CHAR_HOOK, self.on_char_hook)

        # Apply initial volume
        self.set_volume_percent(self.volume, persist=False)
        
        # Update UI with initial speed
        self.set_playback_speed(self.playback_speed)

    # ---------------------------------------------------------------------
    # Window helpers
    # ---------------------------------------------------------------------

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
    # VLC error handling for local range-cache proxy
    # ---------------------------------------------------------------------

    def _on_vlc_error(self, event) -> None:
        try:
            wx.CallAfter(self._handle_vlc_error)
        except Exception:
            pass

    def _handle_vlc_error(self) -> None:
        if self.is_casting:
            return
        if not self._last_vlc_url:
            return

        # Only auto-recover for the range-cache proxy URLs.
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
                )
                # Best-effort restart only if the server is truly dead.
                # Avoid proxy.stop() here: it can break the exact URL VLC is trying to load.
                try:
                    proxy.start()
                except Exception:
                    pass
                new_url = proxy.proxify(self._last_orig_url, headers=self._last_range_proxy_headers or {})
                self._last_vlc_url = new_url
                self._load_vlc_url(new_url)
                return
            except Exception:
                # Fall through to direct fallback below
                pass

        # Second: fall back to the original URL to avoid hard failure.
        if self._range_proxy_retry_count == 1:
            self._range_proxy_retry_count = 2
            try:
                self._last_used_range_proxy = False
                self._last_vlc_url = self._last_orig_url
                self._load_vlc_url(self._last_orig_url)
            except Exception:
                pass

    def _load_vlc_url(self, final_url: str) -> None:
        """Load a URL into the embedded VLC player (local playback only)."""
        try:
            self.player.stop()
        except Exception:
            pass
        media = self.instance.media_new(final_url)
        # Re-apply caching options at load time so changes in Settings take effect without restart.
        try:
            cache_ms = int(self.config_manager.get('vlc_network_caching_ms', 5000))
            if cache_ms < 0:
                cache_ms = 0
            # When playing through the local range-cache proxy, VLC's own network buffering
            # just adds seek delay. The proxy is doing the heavy lifting.
            try:
                if isinstance(final_url, str) and final_url.startswith('http://127.0.0.1:') and '/media?id=' in final_url:
                    cache_ms = min(cache_ms, 800)
            except Exception:
                pass
            file_cache_ms = max(2000, cache_ms)
            media.add_option(f':network-caching={cache_ms}')
            media.add_option(f':file-caching={file_cache_ms}')
            media.add_option(':http-reconnect')
        except Exception:
            pass
        self.player.set_media(media)
        self.player.play()
        self.is_playing = True
        try:
            self.play_btn.SetLabel('Pause')
        except Exception:
            pass
        try:
            if not self.timer.IsRunning():
                self.timer.Start(500)
        except Exception:
            pass
        # Restore speed
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
        self.slider.Bind(wx.EVT_SCROLL_THUMBTRACK, self.on_seek)
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
        
        # Forward 30s
        forward_btn = wx.Button(panel, label="+30s")
        forward_btn.Bind(wx.EVT_BUTTON, self.on_forward)
        btn_sizer.Add(forward_btn, 0, wx.ALL, 5)
        
        # Speed
        self.speed_btn = wx.Button(panel, label=f"Speed: {self.playback_speed}x")
        self.speed_btn.Bind(wx.EVT_BUTTON, self.on_toggle_speed)
        btn_sizer.Add(self.speed_btn, 0, wx.ALL, 5)
        
        # Cast
        self.cast_btn = wx.Button(panel, label="Cast")
        self.cast_btn.Bind(wx.EVT_BUTTON, self.on_cast)
        btn_sizer.Add(self.cast_btn, 0, wx.ALL, 5)
        
        sizer.Add(btn_sizer, 0, wx.ALIGN_CENTER)
        
        # Chapters
        self.chapter_choice = wx.ComboBox(panel, style=wx.CB_READONLY)
        self.chapter_choice.Bind(wx.EVT_COMBOBOX, self.on_chapter_select)
        sizer.Add(self.chapter_choice, 0, wx.EXPAND | wx.ALL, 5)
        
        panel.SetSizer(sizer)

    def on_cast(self, event):
        if self.is_casting:
            # Disconnect
            self.casting_manager.disconnect()
            self.is_casting = False
            self.cast_btn.SetLabel("Cast")
            self.title_lbl.SetLabel(f"{self.current_title} (Local)")
            # Should resume local playback?
            if self.current_url:
                self.load_media(self.current_url, is_youtube=False, chapters=self.current_chapters) # Re-load local
        else:
            # Show dialog
            dlg = CastDialog(self, self.casting_manager)
            if dlg.ShowModal() == wx.ID_OK:
                device = dlg.selected_device
                try:
                    self.casting_manager.connect(device)
                    self.is_casting = True
                    self.cast_btn.SetLabel("Disconnect")
                    self.title_lbl.SetLabel(f"{self.current_title} (Casting to {device.name})")
                    
                    # Stop local player if playing
                    self.player.stop()
                    
                    # Start casting if we have media
                    if self.current_url:
                        self.casting_manager.play(self.current_url, self.current_title)
                        self.is_playing = True
                        self.play_btn.SetLabel("Pause")
                        
                except Exception as e:
                    wx.MessageBox(f"Connection failed: {e}", "Error", wx.ICON_ERROR)
            dlg.Destroy()

    def _maybe_range_cache_url(self, url: str) -> str:
        """Wrap certain high-latency hosts with a local range-caching proxy to make seeking faster."""
        try:
            if not url:
                return url
            # Track the original URL for recovery/fallback
            self._last_orig_url = url
            self._last_used_range_proxy = False
            self._last_range_proxy_headers = None
            self._last_range_proxy_cache_dir = None
            self._last_range_proxy_prefetch_kb = None
            self._last_vlc_url = url
            self._range_proxy_retry_count = 0
            low = url.lower()
            if not (low.startswith('http://') or low.startswith('https://')):
                return url
            if not bool(self.config_manager.get('range_cache_enabled', True)):
                return url
            hosts = self.config_manager.get('range_cache_hosts', ['promodj.com']) or []
            try:
                host = urlparse(url).netloc.lower()
            except Exception:
                host = ''
            if not host or not any(str(h).lower() in host for h in hosts):
                return url
            cache_dir = self.config_manager.get('range_cache_dir', '') or None
            prefetch_kb = int(self.config_manager.get('range_cache_prefetch_kb', 16384) or 16384)
            inline_window_kb = int(self.config_manager.get('range_cache_inline_window_kb', 1024) or 1024)
            background_download = bool(self.config_manager.get('range_cache_background_download', True))
            background_chunk_kb = int(self.config_manager.get('range_cache_background_chunk_kb', 8192) or 8192)
            proxy = get_range_cache_proxy(cache_dir=cache_dir if cache_dir else None, prefetch_kb=prefetch_kb,
                                         background_download=background_download, background_chunk_kb=background_chunk_kb,
                                         inline_window_kb=inline_window_kb)
            req_headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
            }
            # Some hosts behave better (and allow reliable range seeks) with a browser referrer.
            if 'promodj.com' in host:
                req_headers['Referer'] = 'https://promodj.com/'
            self._last_used_range_proxy = True
            self._last_range_proxy_headers = dict(req_headers)
            self._last_range_proxy_cache_dir = cache_dir if cache_dir else None
            self._last_range_proxy_prefetch_kb = prefetch_kb
            proxied = proxy.proxify(url, headers=req_headers)
            # Preflight: ensure the local proxy port is actually listening before handing it to VLC.
            try:
                pu = urlparse(proxied)
                if pu.hostname in ("127.0.0.1", "localhost") and pu.port:
                    # Wait up to ~2 seconds for the port to accept connections.
                    # Do NOT call proxy.stop() here: stopping/rebinding breaks any in-flight VLC stream.
                    deadline = time.time() + 2.0
                    ok = False
                    while time.time() < deadline:
                        try:
                            s = socket.create_connection((pu.hostname, int(pu.port)), timeout=0.25)
                            ok = True
                            try:
                                s.close()
                            except Exception:
                                pass
                            break
                        except Exception:
                            ok = False
                            time.sleep(0.05)
                    if not ok:
                        # Fall back to direct URL if the local proxy isn't reachable.
                        self._last_used_range_proxy = False
                        self._last_vlc_url = url
                        return url
            except Exception:
                pass
            self._last_vlc_url = proxied
            return proxied
        except Exception:
            return url

    def load_media(self, url, is_youtube=False, chapters=None, title=None):
        if not url:
            return
            
        self.current_url = url
        # Need title?
        
        self.slider.SetValue(0)
        self.current_time_lbl.SetLabel("00:00")
        self.total_time_lbl.SetLabel("00:00")
        self.chapter_choice.Clear()
        self.chapter_choice.Disable()
        
        final_url = url
        if is_youtube:
            try:
                import yt_dlp
                with yt_dlp.YoutubeDL({'format': 'bestaudio'}) as ydl:
                    info = ydl.extract_info(url, download=False)
                    final_url = info['url']
                    self.current_title = info.get('title', 'YouTube Video')
            except Exception as e:
                print(f"YouTube resolve failed: {e}")
                wx.MessageBox("Could not resolve YouTube URL. python-vlc or yt-dlp might be missing.",
                              "Error", wx.ICON_ERROR)
                return
        else:
            self.current_title = title or "Playing Audio..."
            
        self.title_lbl.SetLabel(self.current_title)

        if self.is_casting:
            self.casting_manager.play(final_url, self.current_title)
            self.is_playing = True
            self.play_btn.SetLabel("Pause")
        else:
            # Local VLC
            final_url = self._maybe_range_cache_url(final_url)
            self._last_load_chapters = chapters
            self._last_load_title = self.current_title
            self._load_vlc_url(final_url)
        
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
            self.chapter_choice.Append(f"{mins:02d}:{secs:02d} - {title}", ch)

    def on_play_pause(self, event):
        self.toggle_play_pause()

    def on_stop(self, event):
        self.stop()
            
    def on_timer(self, event):
        if self.is_casting:
            # No status update for casting yet (needs callbacks/polling)
            return

        if not self.player.is_playing():
            return
            
        length = self.player.get_length()
        if length != self.duration and length > 0:
            self.duration = length
            self.total_time_lbl.SetLabel(self._format_time(length))
            
        cur = self.player.get_time()
        self.current_time_lbl.SetLabel(self._format_time(cur))
        
        if self.duration > 0:
            pos = int((cur / self.duration) * 1000)
            self.slider.SetValue(pos)
            
        # Update active chapter
        if self.current_chapters:
            cur_sec = cur / 1000.0
            idx = -1
            for i, ch in enumerate(self.current_chapters):
                if cur_sec >= ch.get("start", 0):
                    idx = i
                else:
                    break
            if idx != -1:
                self.chapter_choice.SetSelection(idx)

    def on_seek(self, event):
        val = self.slider.GetValue()
        if self.duration > 0:
            target = int((val / 1000.0) * self.duration)
            if self.is_casting:
                # Seek not implemented in CastingManager wrapper, need direct access
                pass # TODO: Implement casting seek
            else:
                self.player.set_time(target)

    def on_rewind(self, event):
        if self.is_casting:
            pass # TODO
        else:
            cur = self.player.get_time()
            self.player.set_time(max(0, cur - int(self.seek_back_ms)))

    def on_forward(self, event):
        if self.is_casting:
            pass # TODO
        else:
            cur = self.player.get_time()
            if self.duration > 0:
                self.player.set_time(min(self.duration, cur + int(self.seek_forward_ms)))

    def on_toggle_speed(self, event):
        speeds = utils.build_playback_speeds()
        try:
            cur_idx = speeds.index(self.playback_speed)
            next_idx = (cur_idx + 1) % len(speeds)
            new_speed = speeds[next_idx]
        except ValueError:
            new_speed = 1.0
            
        self.set_playback_speed(new_speed)

    def set_playback_speed(self, speed):
        self.playback_speed = speed
        if not self.is_casting:
            self.player.set_rate(speed)
        self.speed_btn.SetLabel(f"Speed: {speed}x")
        self.config_manager.set("playback_speed", speed)

    def on_chapter_select(self, event):
        idx = self.chapter_choice.GetSelection()
        if idx != wx.NOT_FOUND:
            data = self.chapter_choice.GetClientData(idx)
            start_sec = data.get("start", 0)
            if self.is_casting:
                pass # TODO
            else:
                self.player.set_time(int(start_sec * 1000))

    def _format_time(self, ms):
        seconds = ms // 1000
        mins = seconds // 60
        secs = seconds % 60
        return f"{mins:02d}:{secs:02d}"


    # ---------------------------------------------------------------------
    # Media control helpers (keyboard shortcuts + tray integration)
    # ---------------------------------------------------------------------

    def has_media_loaded(self) -> bool:
        return bool(getattr(self, "current_url", None))

    def set_volume_percent(self, percent: int, persist: bool = True) -> None:
        """Set volume in percent (0-100)."""
        try:
            percent = int(percent)
        except Exception:
            percent = 100
        percent = max(0, min(100, percent))
        self.volume = percent

        if self.is_casting:
            # Casting volume supported by core.casting BaseCaster, but the manager doesn't expose it.
            # We attempt to call it if an active caster is present.
            try:
                caster = getattr(self.casting_manager, "active_caster", None)
                if caster is not None and hasattr(caster, "set_volume"):
                    level = float(percent) / 100.0
                    # active_caster methods are async coroutines
                    self.casting_manager.dispatch(caster.set_volume(level))
            except Exception:
                # If casting volume fails, ignore rather than breaking playback
                pass
        else:
            try:
                self.player.audio_set_volume(percent)
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

    def seek_relative_ms(self, delta_ms: int) -> None:
        """Seek relative to current position (local playback only)."""
        if self.is_casting:
            # Seek not implemented for casting sessions yet
            return
        try:
            cur = int(self.player.get_time())
        except Exception:
            return
        if cur < 0:
            cur = 0
        target = cur + int(delta_ms)
        if target < 0:
            target = 0
        if self.duration and target > int(self.duration):
            target = int(self.duration)
        try:
            self.player.set_time(target)
        except Exception:
            pass

    def play(self) -> None:
        if not self.has_media_loaded():
            return
        if self.is_casting:
            try:
                self.casting_manager.resume()
                self.is_playing = True
                self.play_btn.SetLabel("Pause")
            except Exception:
                pass
        else:
            try:
                try:
                    self.player.set_pause(0)
                except Exception:
                    pass
                self.player.play()
                self.is_playing = True
                self.play_btn.SetLabel("Pause")
                if not self.timer.IsRunning():
                    self.timer.Start(500)
            except Exception:
                pass

    def pause(self) -> None:
        if not self.has_media_loaded():
            return
        if self.is_casting:
            try:
                self.casting_manager.pause()
                self.is_playing = False
                self.play_btn.SetLabel("Play")
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
                self.play_btn.SetLabel("Play")
            except Exception:
                pass

    def stop(self) -> None:
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

        self.is_playing = False
        try:
            self.play_btn.SetLabel("Play")
        except Exception:
            pass

        # Reset UI
        try:
            self.slider.SetValue(0)
            self.current_time_lbl.SetLabel("00:00")
            self.total_time_lbl.SetLabel(self._format_time(self.duration) if self.duration else "00:00")
        except Exception:
            pass

    def on_char_hook(self, event: wx.KeyEvent) -> None:
        if event.ControlDown():
            key = event.GetKeyCode()
            if key == wx.WXK_UP:
                self.adjust_volume(int(getattr(self, "volume_step", 5)))
                return
            if key == wx.WXK_DOWN:
                self.adjust_volume(-int(getattr(self, "volume_step", 5)))
                return
            if key == wx.WXK_LEFT:
                self.seek_relative_ms(-int(getattr(self, "seek_back_ms", 10000)))
                return
            if key == wx.WXK_RIGHT:
                self.seek_relative_ms(int(getattr(self, "seek_forward_ms", 30000)))
                return
        event.Skip()

    def on_close(self, event):
        self.player.stop()
        self.timer.Stop()
        self.casting_manager.stop()
        self.Hide()

