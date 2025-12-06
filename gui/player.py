import wx
import wx.media
import threading
import yt_dlp
import tempfile
import os
from core import utils

class MediaPlayerPanel(wx.Panel):
    def __init__(self, parent):
        super().__init__(parent)
        
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
        
        # Chapters list (use simple list for better accessibility)
        self.chapters = wx.ListBox(self)

        # Status
        self.st_status = wx.StaticText(self, label="Ready")

        # Media Control (Backend - Hidden)
        self.media_ctrl = wx.media.MediaCtrl(self, style=wx.SIMPLE_BORDER)
        self.media_ctrl.Show(False)
        
        # Add to sizer in visual order
        # self.sizer.Add(self.media_ctrl, 1, wx.EXPAND|wx.ALL, 5) # Removed from view
        self.sizer.Add(self.ctrl_sizer, 0, wx.ALIGN_CENTER)
        self.sizer.Add(self.slider, 0, wx.EXPAND|wx.ALL, 5)
        self.sizer.Add(self.chapters, 1, wx.EXPAND|wx.LEFT|wx.RIGHT, 5)
        self.sizer.Add(self.vol_sizer, 0, wx.EXPAND|wx.ALL, 5)
        self.sizer.Add(self.st_status, 0, wx.ALIGN_CENTER|wx.ALL, 5)
        
        self.SetSizer(self.sizer)
        
        # Bindings
        self.Bind(wx.EVT_BUTTON, self.on_play, self.btn_play)
        self.Bind(wx.EVT_BUTTON, self.on_pause, self.btn_pause)
        self.Bind(wx.EVT_BUTTON, self.on_stop, self.btn_stop)
        self.Bind(wx.EVT_SLIDER, self.on_seek, self.slider)
        self.Bind(wx.EVT_SLIDER, self.on_volume_change, self.volume_slider)
        
        self.Bind(wx.media.EVT_MEDIA_LOADED, self.on_media_loaded, self.media_ctrl)
        self.Bind(wx.media.EVT_MEDIA_FINISHED, self.on_media_finished, self.media_ctrl)
        self.chapters.Bind(wx.EVT_LISTBOX_DCLICK, self.on_chapter_activated)
        self.chapters.Bind(wx.EVT_KEY_DOWN, self.on_chapter_key)
        self.chapters.Bind(wx.EVT_CHAR_HOOK, self.on_chapter_key)
        
        self.timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self.on_timer, self.timer)
        
        self.safety_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self.on_safety_timer, self.safety_timer)
        
        # Keyboard shortcuts
        self.Bind(wx.EVT_CHAR_HOOK, self.on_key)

        self.current_url = None
        self.current_chapters = []
        self.fallback_active = False
        self.temp_file = None

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
        self.media_ctrl.SetVolume(val / 100.0)

    def adjust_seek(self, delta_ms):
        current = self.media_ctrl.Tell()
        new_pos = current + delta_ms
        length = self.media_ctrl.Length()
        if length > 0:
            new_pos = max(0, min(length, new_pos))
            self.media_ctrl.Seek(new_pos)
            self.slider.SetValue(new_pos)

    def load_media(self, url, is_youtube=False, chapters=None):
        self.on_stop(None)
        self.st_status.SetLabel("Loading...")
        self.safety_attempts = 0
        self.pending_url = url
        # If no chapters provided, try to extract from media (ID3 CHAP)
        if not chapters:
            chapters = self._maybe_fetch_chapters(url, is_youtube)
        self._set_chapters(chapters or [])
        
        if is_youtube:
            threading.Thread(target=self._resolve_youtube, args=(url,), daemon=True).start()
        else:
            self._load_direct(url)

    def _resolve_youtube(self, url):
        try:
            import shutil
            import os
            
            ydl_opts = {'format': 'bestaudio/best', 'quiet': True}
            
            # Explicitly find ffmpeg if possible
            ffmpeg_path = shutil.which("ffmpeg")
            if ffmpeg_path:
                print(f"DEBUG: Found ffmpeg at {ffmpeg_path}")
                ydl_opts['ffmpeg_location'] = os.path.dirname(ffmpeg_path)
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                audio_url = info['url']
                wx.CallAfter(self._load_direct, audio_url)
        except Exception as e:
            print(f"DEBUG: YoutubeDL error: {e}")
            wx.CallAfter(self.st_status.SetLabel, f"Error: {e}")

    def _load_direct(self, url):
        print(f"DEBUG: _load_direct called with URL: {url}")
        if not url:
            self.st_status.SetLabel("No media URL provided.")
            return
        # Reset timers before a new load
        self.safety_timer.Stop()
        # Initiate load. Wait for EVT_MEDIA_LOADED to play.
        if not self.media_ctrl.Load(url):
            print("DEBUG: Load failed immediately. Triggering fallback.")
            self._trigger_fallback(url)
        else:
            self.st_status.SetLabel("Loading media stream...")
            print("DEBUG: Load initiated. Waiting for EVT_MEDIA_LOADED or Safety Timer.")
            # Start safety timer to force play if event doesn't fire quickly (common on Windows)
            self.safety_timer.Start(500)  # repeating until play succeeds or we give up

    def on_safety_timer(self, event):
        # Try a few times in case load is slow; stop once it starts playing
        try:
            state = self.media_ctrl.GetState()
        except Exception:
            state = None

        if state == wx.media.MEDIASTATE_PLAYING:
            self.safety_timer.Stop()
            self.st_status.SetLabel("Playing")
            return

        if getattr(self, "safety_attempts", 0) >= 10:
            self.safety_timer.Stop()
            print("DEBUG: Safety timer giving up. Triggering fallback.")
            self._trigger_fallback(self.pending_url)
            return

        self.safety_attempts += 1
        print(f"DEBUG: Safety timer attempt {self.safety_attempts} forcing playback. State: {state}")

        # Force Play() regardless of state (STOPPED, PAUSED, or unknown)
        # because we want to start playback.
        if not self.media_ctrl.Play():
            # If Play fails, sometimes seeking to start helps reset backend
            try:
                self.media_ctrl.Seek(0)
                self.media_ctrl.Play()
            except Exception:
                pass
        else:
            # If Play returns True, we might be playing now, or buffering.
            # We keep the timer running to verify state in next tick.
            self.timer.Start(500)
            self.st_status.SetLabel("Playing")

    def on_media_loaded(self, event):
        print("DEBUG: EVT_MEDIA_LOADED received.")
        self.safety_timer.Stop()
        self.st_status.SetLabel("Media Loaded")
        
        # Auto-play now that we are sure it's ready
        if not self.media_ctrl.Play():
            print("DEBUG: Play() failed in on_media_loaded; starting safety retries.")
            if not self.safety_timer.IsRunning():
                self.safety_timer.Start(500)
            self.st_status.SetLabel("Ready to Play")
        else:
            print("DEBUG: Play() successful in on_media_loaded")
            self.timer.Start(500)
            self.st_status.SetLabel("Playing")
            self.btn_pause.SetFocus()

    def on_play(self, event):
        self.media_ctrl.Play()
        self.timer.Start(500)
        self.st_status.SetLabel("Playing")
        self.btn_pause.SetFocus()

    def on_pause(self, event):
        self.media_ctrl.Pause()
        self.timer.Stop()
        self.st_status.SetLabel("Paused")
        self.btn_play.SetFocus()

    def on_stop(self, event):
        self.media_ctrl.Stop()
        self.timer.Stop()
        self.safety_timer.Stop()
        self.slider.SetValue(0)
        self.st_status.SetLabel("Stopped")
        self.btn_play.SetFocus()

    def on_media_finished(self, event):
        self.on_stop(None)
        self.st_status.SetLabel("Finished")

    def on_timer(self, event):
        offset = self.media_ctrl.Tell()
        length = self.media_ctrl.Length()
        if length > 0:
            self.slider.SetMax(length)
            self.slider.SetValue(offset)
            # Highlight current chapter
        self._highlight_chapter(offset / 1000.0)

    def on_seek(self, event):
        offset = self.slider.GetValue()
        self.media_ctrl.Seek(offset)

    def _trigger_fallback(self, url):
        if self.fallback_active:
            return
        self.fallback_active = True
        self.st_status.SetLabel("Streaming failed. Downloading...")
        print(f"DEBUG: Triggering download fallback for {url}")
        threading.Thread(target=self._download_and_play_thread, args=(url,), daemon=True).start()

    def _download_and_play_thread(self, url):
        try:
            # Download to temp file
            resp = utils.safe_requests_get(url, stream=True, timeout=30)
            resp.raise_for_status()
            
            suffix = ".mp3"
            if ".m4a" in url: suffix = ".m4a"
            
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                for chunk in resp.iter_content(chunk_size=8192):
                    tmp.write(chunk)
                tmp_path = tmp.name
            
            print(f"DEBUG: Downloaded to {tmp_path}")
            wx.CallAfter(self._load_local_file, tmp_path)
            
        except Exception as e:
            print(f"Fallback download error: {e}")
            wx.CallAfter(self.st_status.SetLabel, "Download failed.")

    def _load_local_file(self, path):
        # Clean up previous temp if exists
        if self.temp_file and self.temp_file != path and os.path.exists(self.temp_file):
            try:
                os.remove(self.temp_file)
            except: pass
            
        self.temp_file = path
        self.st_status.SetLabel("Playing Downloaded File")
        if self.media_ctrl.Load(path):
            self.media_ctrl.Play()
            self.timer.Start(500)
        else:
            self.st_status.SetLabel("Playback failed even after download.")

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
            # Some backends need Pause/Play sandwich for accurate seek
            self.media_ctrl.Pause()
            self.media_ctrl.Seek(ms)
            self.slider.SetValue(ms)
            self.media_ctrl.Play()
            # restart timer so highlight follows immediately
            self.timer.Start(500)
        except Exception as e:
            print(f"Chapter seek failed: {e}")

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
        if active_idx >= 0 and self.chapters.GetFirstSelected() != active_idx:
            self.chapters.Select(active_idx)
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
    def __init__(self, parent):
        super().__init__(parent, title="Media Player", size=(400, 200))
        self.panel = MediaPlayerPanel(self)
        
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
