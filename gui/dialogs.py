import wx
import copy
import threading
import webbrowser
from urllib.parse import urlparse
from core.discovery import discover_feed, is_ytdlp_supported
from core import utils
from core.casting import CastingManager


class AddFeedDialog(wx.Dialog):
    def __init__(self, parent, categories=None):
        super().__init__(parent, title="Add Feed", size=(400, 250))
        
        self.categories = categories or ["Uncategorized"]
        self._check_timer = None
        
        sizer = wx.BoxSizer(wx.VERTICAL)
        
        # URL Input
        sizer.Add(wx.StaticText(self, label="Feed or Media URL:"), 0, wx.ALL, 5)
        self.url_ctrl = wx.TextCtrl(self)
        self.url_ctrl.SetFocus()
        sizer.Add(self.url_ctrl, 0, wx.EXPAND | wx.ALL, 5)
        
        # Compatibility Hint
        self.status_lbl = wx.StaticText(self, label="")
        self.status_lbl.SetForegroundColour(wx.Colour(0, 128, 0)) # Greenish
        sizer.Add(self.status_lbl, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)
        
        # Category Input
        sizer.Add(wx.StaticText(self, label="Category:"), 0, wx.ALL, 5)
        self.cat_ctrl = wx.ComboBox(self, choices=self.categories, style=wx.CB_DROPDOWN)
        if self.categories:
            # Try to select 'YouTube' if it exists
            yt_idx = self.cat_ctrl.FindString("YouTube")
            if yt_idx != wx.NOT_FOUND:
                self.cat_ctrl.SetSelection(yt_idx)
            else:
                self.cat_ctrl.SetSelection(0)
        sizer.Add(self.cat_ctrl, 0, wx.EXPAND | wx.ALL, 5)
        
        # Buttons
        btn_sizer = self.CreateButtonSizer(wx.OK | wx.CANCEL)
        sizer.Add(btn_sizer, 0, wx.ALIGN_CENTER | wx.ALL, 5)
        
        self.SetSizer(sizer)
        self.Centre()
        
        self.url_ctrl.Bind(wx.EVT_TEXT, self.on_url_text)

    def on_url_text(self, event):
        url = self.url_ctrl.GetValue().strip()
        if not url:
            self.status_lbl.SetLabel("")
            return
            
        if self._check_timer:
            self._check_timer.Stop()
            
        self._check_timer = wx.CallLater(500, self._perform_compatibility_check, url)

    def _perform_compatibility_check(self, url):
        # Quick check first
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        
        if "youtube.com" in domain or "youtu.be" in domain:
            self.status_lbl.SetLabel("OK: Recognized as YouTube source")
            # Auto-switch category to YouTube if available
            yt_idx = self.cat_ctrl.FindString("YouTube")
            if yt_idx != wx.NOT_FOUND:
                self.cat_ctrl.SetSelection(yt_idx)
            return

        self.status_lbl.SetLabel("Checking compatibility...")
        # Background thread for heavier yt-dlp check
        threading.Thread(target=self._heavy_check, args=(url,), daemon=True).start()

    def _heavy_check(self, url):
        if is_ytdlp_supported(url):
            wx.CallAfter(self.status_lbl.SetLabel, "OK: Supported by yt-dlp")
        else:
            wx.CallAfter(self.status_lbl.SetLabel, "")

    def get_data(self):
        return self.url_ctrl.GetValue(), self.cat_ctrl.GetValue()


class SettingsDialog(wx.Dialog):
    def __init__(self, parent, config):
        super().__init__(parent, title="Settings", size=(500, 450))
        
        self.config = config
        
        notebook = wx.Notebook(self)
        
        # General Tab
        general_panel = wx.Panel(notebook)
        general_sizer = wx.BoxSizer(wx.VERTICAL)
        
        refresh_sizer = wx.BoxSizer(wx.HORIZONTAL)
        refresh_sizer.Add(wx.StaticText(general_panel, label="Refresh Interval (seconds):"), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.refresh_ctrl = wx.SpinCtrl(general_panel, min=60, max=3600, initial=int(config.get("refresh_interval", 300)))
        refresh_sizer.Add(self.refresh_ctrl, 0, wx.ALL, 5)
        general_sizer.Add(refresh_sizer, 0, wx.EXPAND | wx.ALL, 5)
        
        concurrency_sizer = wx.BoxSizer(wx.HORIZONTAL)
        concurrency_sizer.Add(wx.StaticText(general_panel, label="Max Concurrent Refreshes:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.concurrent_ctrl = wx.SpinCtrl(general_panel, min=1, max=50, initial=int(config.get("max_concurrent_refreshes", 12)))
        concurrency_sizer.Add(self.concurrent_ctrl, 0, wx.ALL, 5)
        general_sizer.Add(concurrency_sizer, 0, wx.EXPAND | wx.ALL, 5)
        
        per_host_sizer = wx.BoxSizer(wx.HORIZONTAL)
        per_host_sizer.Add(wx.StaticText(general_panel, label="Max Connections Per Host:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.per_host_ctrl = wx.SpinCtrl(general_panel, min=1, max=10, initial=int(config.get("per_host_max_connections", 3)))
        per_host_sizer.Add(self.per_host_ctrl, 0, wx.ALL, 5)
        general_sizer.Add(per_host_sizer, 0, wx.EXPAND | wx.ALL, 5)
        
        timeout_sizer = wx.BoxSizer(wx.HORIZONTAL)
        timeout_sizer.Add(wx.StaticText(general_panel, label="Feed Timeout (seconds):"), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.timeout_ctrl = wx.SpinCtrl(general_panel, min=5, max=120, initial=int(config.get("feed_timeout_seconds", 15)))
        timeout_sizer.Add(self.timeout_ctrl, 0, wx.ALL, 5)
        general_sizer.Add(timeout_sizer, 0, wx.EXPAND | wx.ALL, 5)
        
        retry_sizer = wx.BoxSizer(wx.HORIZONTAL)
        retry_sizer.Add(wx.StaticText(general_panel, label="Feed Retry Attempts:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.retry_ctrl = wx.SpinCtrl(general_panel, min=0, max=5, initial=int(config.get("feed_retry_attempts", 1)))
        retry_sizer.Add(self.retry_ctrl, 0, wx.ALL, 5)
        general_sizer.Add(retry_sizer, 0, wx.EXPAND | wx.ALL, 5)
        
        self.skip_silence_chk = wx.CheckBox(general_panel, label="Skip Silence (Experimental)")
        self.skip_silence_chk.SetValue(config.get("skip_silence", False))
        general_sizer.Add(self.skip_silence_chk, 0, wx.ALL, 5)
        
        # Playback speed
        speed_sizer = wx.BoxSizer(wx.HORIZONTAL)
        speed_sizer.Add(wx.StaticText(general_panel, label="Default Playback Speed:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        
        # Build speed choices using utils
        speeds = utils.build_playback_speeds()
        self.speed_choices = [f"{s:.2f}x" for s in speeds]
        current_speed = float(config.get("playback_speed", 1.0))
        
        self.speed_ctrl = wx.ComboBox(general_panel, choices=self.speed_choices, style=wx.CB_READONLY)
        
        # Find nearest selection
        sel_idx = 0
        min_diff = 999.0
        for i, s in enumerate(speeds):
            diff = abs(s - current_speed)
            if diff < min_diff:
                min_diff = diff
                sel_idx = i
        self.speed_ctrl.SetSelection(sel_idx)
        
        speed_sizer.Add(self.speed_ctrl, 0, wx.ALL, 5)
        general_sizer.Add(speed_sizer, 0, wx.EXPAND | wx.ALL, 5)

        # Player window behavior
        self.show_player_on_play_chk = wx.CheckBox(general_panel, label="Show player window when starting playback")
        self.show_player_on_play_chk.SetValue(bool(config.get("show_player_on_play", True)))
        general_sizer.Add(self.show_player_on_play_chk, 0, wx.ALL, 5)

        # VLC network caching (helps on high latency streams)
        cache_net_sizer = wx.BoxSizer(wx.HORIZONTAL)
        cache_net_sizer.Add(wx.StaticText(general_panel, label="Network Cache (ms):"), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.vlc_cache_ctrl = wx.SpinCtrl(general_panel, min=500, max=60000, initial=int(config.get("vlc_network_caching_ms", 5000)))
        cache_net_sizer.Add(self.vlc_cache_ctrl, 0, wx.ALL, 5)
        general_sizer.Add(cache_net_sizer, 0, wx.EXPAND | wx.ALL, 5)
        
        # Cache views
        cache_sizer = wx.BoxSizer(wx.HORIZONTAL)
        cache_sizer.Add(wx.StaticText(general_panel, label="Max Cached Views:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.cache_ctrl = wx.SpinCtrl(general_panel, min=5, max=100, initial=int(config.get("max_cached_views", 15)))
        cache_sizer.Add(self.cache_ctrl, 0, wx.ALL, 5)
        general_sizer.Add(cache_sizer, 0, wx.EXPAND | wx.ALL, 5)
        
        # Downloads
        self.downloads_chk = wx.CheckBox(general_panel, label="Enable Downloads")
        self.downloads_chk.SetValue(config.get("downloads_enabled", False))
        general_sizer.Add(self.downloads_chk, 0, wx.ALL, 5)
        
        dl_path_sizer = wx.BoxSizer(wx.HORIZONTAL)
        dl_path_sizer.Add(wx.StaticText(general_panel, label="Download Path:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.dl_path_ctrl = wx.TextCtrl(general_panel, value=config.get("download_path", ""))
        dl_path_sizer.Add(self.dl_path_ctrl, 1, wx.ALL, 5)
        browse_btn = wx.Button(general_panel, label="Browse...")
        browse_btn.Bind(wx.EVT_BUTTON, self.on_browse_dl_path)
        dl_path_sizer.Add(browse_btn, 0, wx.ALL, 5)
        general_sizer.Add(dl_path_sizer, 0, wx.EXPAND | wx.ALL, 5)
        
        retention_sizer = wx.BoxSizer(wx.HORIZONTAL)
        retention_sizer.Add(wx.StaticText(general_panel, label="Retention Policy:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        retention_opts = ["1 day", "3 days", "1 week", "2 weeks", "3 weeks", "1 month", "2 months", "6 months", "1 year", "2 years", "5 years", "Unlimited"]
        self.retention_ctrl = wx.ComboBox(general_panel, choices=retention_opts, style=wx.CB_READONLY)
        self.retention_ctrl.SetValue(config.get("download_retention", "Unlimited"))
        retention_sizer.Add(self.retention_ctrl, 0, wx.ALL, 5)
        general_sizer.Add(retention_sizer, 0, wx.EXPAND | wx.ALL, 5)
        
        # Tray settings
        self.close_tray_chk = wx.CheckBox(general_panel, label="Close to Tray")
        self.close_tray_chk.SetValue(config.get("close_to_tray", False))
        general_sizer.Add(self.close_tray_chk, 0, wx.ALL, 5)
        
        self.min_tray_chk = wx.CheckBox(general_panel, label="Minimize to Tray")
        self.min_tray_chk.SetValue(config.get("minimize_to_tray", True))
        general_sizer.Add(self.min_tray_chk, 0, wx.ALL, 5)

        self.debug_mode_chk = wx.CheckBox(general_panel, label="Debug mode (show console on startup)")
        self.debug_mode_chk.SetValue(bool(config.get("debug_mode", False)))
        general_sizer.Add(self.debug_mode_chk, 0, wx.ALL, 5)

        self.auto_update_chk = wx.CheckBox(general_panel, label="Check for updates on startup")
        self.auto_update_chk.SetValue(bool(config.get("auto_check_updates", True)))
        general_sizer.Add(self.auto_update_chk, 0, wx.ALL, 5)
        
        general_panel.SetSizer(general_sizer)
        notebook.AddPage(general_panel, "General")
        
        # Provider Tab
        provider_panel = wx.Panel(notebook)
        provider_sizer = wx.BoxSizer(wx.VERTICAL)

        provider_sizer.Add(wx.StaticText(provider_panel, label="Active Provider:"), 0, wx.ALL, 5)

        # Build provider list from config (keeps future providers visible).
        cfg_providers = list((config.get("providers") or {}).keys()) if isinstance(config, dict) else []
        if not cfg_providers:
            cfg_providers = ["local", "miniflux", "bazqux", "theoldreader", "inoreader"]
        preferred_order = ["local", "miniflux", "bazqux", "theoldreader", "inoreader"]
        providers_sorted = [p for p in preferred_order if p in cfg_providers] + [p for p in cfg_providers if p not in preferred_order]

        self.provider_choice = wx.Choice(provider_panel, choices=providers_sorted)
        self.provider_choice.SetStringSelection(config.get("active_provider", "local"))
        provider_sizer.Add(self.provider_choice, 0, wx.EXPAND | wx.ALL, 5)

        # Provider-specific settings panels
        self._provider_panels = {}  # name -> (panel, controls_dict)

        def _add_simple_info_panel(name: str, info_text: str):
            pnl = wx.Panel(provider_panel)
            s = wx.BoxSizer(wx.VERTICAL)
            s.Add(wx.StaticText(pnl, label=info_text), 0, wx.ALL, 5)
            pnl.SetSizer(s)
            provider_sizer.Add(pnl, 0, wx.EXPAND | wx.ALL, 5)
            self._provider_panels[name] = (pnl, {})
            pnl.Hide()

        def _add_fields_panel(name: str, fields):
            # fields: [(label, key, style)]
            pnl = wx.Panel(provider_panel)
            fg = wx.FlexGridSizer(cols=2, hgap=8, vgap=8)
            fg.AddGrowableCol(1, 1)
            ctrls = {}
            p_cfg = (config.get("providers") or {}).get(name, {}) if isinstance(config, dict) else {}
            for label, key, style in fields:
                fg.Add(wx.StaticText(pnl, label=label), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 2)
                tc = wx.TextCtrl(pnl, style=style)
                tc.SetValue(str(p_cfg.get(key, "") or ""))
                fg.Add(tc, 1, wx.EXPAND | wx.ALL, 2)
                ctrls[key] = tc
            pnl.SetSizer(fg)
            provider_sizer.Add(pnl, 0, wx.EXPAND | wx.ALL, 5)
            self._provider_panels[name] = (pnl, ctrls)
            pnl.Hide()

        _add_simple_info_panel("local", "Local provider uses the feeds you add inside the app (Add Feed / Import OPML).")
        _add_fields_panel("miniflux", [
            ("Miniflux URL:", "url", 0),
            ("Miniflux API Key:", "api_key", 0),
        ])
        _add_fields_panel("theoldreader", [
            ("The Old Reader Email:", "email", 0),
            ("The Old Reader Password:", "password", wx.TE_PASSWORD),
        ])
        _add_fields_panel("inoreader", [
            ("Inoreader Token:", "token", 0),
        ])
        _add_fields_panel("bazqux", [
            ("BazQux Email:", "email", 0),
            ("BazQux Password:", "password", wx.TE_PASSWORD),
        ])

        self.provider_choice.Bind(wx.EVT_CHOICE, self.on_provider_choice)
        self._update_provider_panels()

        provider_panel.SetSizer(provider_sizer)
        notebook.AddPage(provider_panel, "Provider")
        
        # Main Sizer
        main_sizer = wx.BoxSizer(wx.VERTICAL)
        main_sizer.Add(notebook, 1, wx.EXPAND | wx.ALL, 5)
        
        btn_sizer = self.CreateButtonSizer(wx.OK | wx.CANCEL)
        main_sizer.Add(btn_sizer, 0, wx.ALIGN_CENTER | wx.ALL, 5)
        
        self.SetSizer(main_sizer)
        self.Centre()

    def on_provider_choice(self, event):
        self._update_provider_panels()

    def _update_provider_panels(self):
        try:
            sel = self.provider_choice.GetStringSelection()
        except Exception:
            sel = "local"
        for name, (pnl, _ctrls) in getattr(self, "_provider_panels", {}).items():
            try:
                pnl.Show(name == sel)
            except Exception:
                pass
        try:
            # Refresh layout so controls become reachable in tab order immediately.
            self.Layout()
            self.FitInside() if hasattr(self, "FitInside") else None
        except Exception:
            pass

    def on_browse_dl_path(self, event):
        dlg = wx.DirDialog(self, "Choose download directory", self.dl_path_ctrl.GetValue(), style=wx.DD_DEFAULT_STYLE | wx.DD_DIR_MUST_EXIST)
        if dlg.ShowModal() == wx.ID_OK:
            self.dl_path_ctrl.SetValue(dlg.GetPath())
        dlg.Destroy()

    def get_data(self):
        # Parse speed back to float
        speed_str = self.speed_ctrl.GetValue().replace("x", "")
        try:
            speed = float(speed_str)
        except ValueError:
            speed = 1.0
            
        providers = {}
        try:
            providers = copy.deepcopy(self.config.get("providers", {})) if isinstance(self.config, dict) else {}
        except Exception:
            providers = {}

        # Collect provider settings from UI controls (preserves existing keys like local feeds).
        for name, (_pnl, ctrls) in getattr(self, "_provider_panels", {}).items():
            if not ctrls:
                continue
            p_cfg = providers.get(name, {})
            if not isinstance(p_cfg, dict):
                p_cfg = {}
            for key, tc in ctrls.items():
                try:
                    p_cfg[key] = (tc.GetValue() or "").strip()
                except Exception:
                    p_cfg[key] = ""
            providers[name] = p_cfg

        return {
            "refresh_interval": self.refresh_ctrl.GetValue(),
            "max_concurrent_refreshes": self.concurrent_ctrl.GetValue(),
            "per_host_max_connections": self.per_host_ctrl.GetValue(),
            "feed_timeout_seconds": self.timeout_ctrl.GetValue(),
            "feed_retry_attempts": self.retry_ctrl.GetValue(),
            "skip_silence": self.skip_silence_chk.GetValue(),
            "playback_speed": speed,
            "show_player_on_play": self.show_player_on_play_chk.GetValue(),
            "vlc_network_caching_ms": self.vlc_cache_ctrl.GetValue(),
            "max_cached_views": self.cache_ctrl.GetValue(),
            "downloads_enabled": self.downloads_chk.GetValue(),
            "download_path": self.dl_path_ctrl.GetValue(),
            "download_retention": self.retention_ctrl.GetValue(),
            "close_to_tray": self.close_tray_chk.GetValue(),
            "minimize_to_tray": self.min_tray_chk.GetValue(),
            "debug_mode": self.debug_mode_chk.GetValue(),
            "auto_check_updates": self.auto_update_chk.GetValue(),
            "active_provider": self.provider_choice.GetStringSelection(),
            "providers": providers,
        }


class FeedPropertiesDialog(wx.Dialog):
    def __init__(self, parent, feed, categories):
        super().__init__(parent, title="Feed Properties", size=(400, 200))
        
        self.feed = feed
        self.categories = categories
        
        sizer = wx.BoxSizer(wx.VERTICAL)
        
        sizer.Add(wx.StaticText(self, label=f"Title: {feed.title}"), 0, wx.ALL, 5)
        sizer.Add(wx.StaticText(self, label=f"URL: {feed.url}"), 0, wx.ALL, 5)
        
        sizer.Add(wx.StaticText(self, label="Category:"), 0, wx.ALL, 5)
        self.cat_ctrl = wx.ComboBox(self, choices=self.categories, style=wx.CB_DROPDOWN)
        self.cat_ctrl.SetValue(feed.category or "Uncategorized")
        sizer.Add(self.cat_ctrl, 0, wx.EXPAND | wx.ALL, 5)
        
        btn_sizer = self.CreateButtonSizer(wx.OK | wx.CANCEL)
        sizer.Add(btn_sizer, 0, wx.ALIGN_CENTER | wx.ALL, 5)
        
        self.SetSizer(sizer)
        self.Centre()

    def get_category(self):
        return self.cat_ctrl.GetValue()


class FeedSearchDialog(wx.Dialog):
    def __init__(self, parent):
        super().__init__(parent, title="Find a Podcast or RSS Feed", size=(800, 600))
        
        self.selected_url = None
        self._threads = []
        self._stop_event = threading.Event()
        
        sizer = wx.BoxSizer(wx.VERTICAL)
        
        # Search Box
        input_sizer = wx.BoxSizer(wx.HORIZONTAL)
        input_sizer.Add(wx.StaticText(self, label="Search term or URL:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        
        self.search_ctrl = wx.SearchCtrl(self, style=wx.TE_PROCESS_ENTER)
        self.search_ctrl.ShowCancelButton(True)
        input_sizer.Add(self.search_ctrl, 1, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        
        self.search_btn = wx.Button(self, label="Search")
        input_sizer.Add(self.search_btn, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        
        sizer.Add(input_sizer, 0, wx.EXPAND | wx.ALL, 5)

        # Provider Status (optional, to show what's happening)
        self.status_lbl = wx.StaticText(self, label="Ready. Enter a keyword or URL.")
        sizer.Add(self.status_lbl, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)
        
        # Results List
        self.results_list = wx.ListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
        self.results_list.InsertColumn(0, "Title", width=350)
        self.results_list.InsertColumn(1, "Provider", width=120)
        self.results_list.InsertColumn(2, "Details", width=250)
        self.results_list.InsertColumn(3, "URL", width=0) # Hidden
        
        sizer.Add(self.results_list, 1, wx.EXPAND | wx.ALL, 5)

        # Attribution / Help
        help_sizer = wx.BoxSizer(wx.HORIZONTAL)
        help_sizer.Add(wx.StaticText(self, label="Aggregates results from: iTunes, gPodder, Feedly, Feedsearch.dev, NewsBlur, Reddit, BlindRSS"), 0, wx.ALL, 5)
        sizer.Add(help_sizer, 0, wx.ALIGN_RIGHT | wx.ALL, 5)
        
        # Buttons
        btn_sizer = self.CreateButtonSizer(wx.OK | wx.CANCEL)
        sizer.Add(btn_sizer, 0, wx.ALIGN_CENTER | wx.ALL, 5)
        
        self.SetSizer(sizer)
        self.Centre()
        
        # Bindings
        self.search_btn.Bind(wx.EVT_BUTTON, self.on_search)
        self.search_ctrl.Bind(wx.EVT_TEXT_ENTER, self.on_search)
        self.search_ctrl.Bind(wx.EVT_SEARCHCTRL_SEARCH_BTN, self.on_search)
        self.results_list.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self.on_item_activated)
        self.Bind(wx.EVT_CLOSE, self.on_close)

        self.results_data = [] # List of dicts: title, provider, detail, url

    def on_close(self, event):
        self._stop_event.set()
        event.Skip()

    def on_search(self, event):
        term = (self.search_ctrl.GetValue() or "").strip()
        if not term:
            return
            
        self.results_list.DeleteAllItems()
        self.results_data = []
        self._stop_event.clear()
        
        # Update UI
        self.search_ctrl.Disable()
        self.search_btn.Disable()
        self.status_lbl.SetLabel("Searching...")
        
        # Start unified search thread
        threading.Thread(target=self._unified_search_manager, args=(term,), daemon=True).start()

    def _unified_search_manager(self, term):
        import urllib.parse
        from queue import Queue

        results_queue = Queue()
        active_threads = []

        # Helper to launch a provider thread
        def launch(target, name):
            t = threading.Thread(target=target, args=(term, results_queue), name=name, daemon=True)
            t.start()
            active_threads.append(t)

        # 1. iTunes (Podcasts)
        launch(self._search_itunes, "iTunes")
        
        # 2. gPodder (Podcasts)
        launch(self._search_gpodder, "gPodder")
        
        # 3. Feedly (RSS/General)
        launch(self._search_feedly, "Feedly")
        
        # 4. NewsBlur (Autocomplete)
        launch(self._search_newsblur, "NewsBlur")

        # 5. Reddit (Subreddits)
        launch(self._search_reddit, "Reddit")

        # 6. Feedsearch.dev + BlindRSS (URL based)
        # Only run these if it looks like a URL or domain, OR if user wants broad search
        # Feedsearch.dev claims to search by URL. If we pass a keyword, it might fail, but let's try.
        # BlindRSS discovery is strictly URL based.
        if "." in term or "://" in term or term.lower().startswith("lbry:"):
            launch(self._search_feedsearch, "Feedsearch")
            launch(self._search_blindrss, "BlindRSS")
        
        # Wait for threads
        for t in active_threads:
            t.join(timeout=15) # Global timeout per provider

        # Process results
        all_results = []
        seen_urls = set()

        while not results_queue.empty():
            try:
                provider, items = results_queue.get_nowait()
                for item in items:
                    url = item.get("url", "").strip()
                    if not url or url in seen_urls:
                        continue
                    seen_urls.add(url)
                    all_results.append({
                        "title": item.get("title", url),
                        "provider": provider,
                        "detail": item.get("detail", ""),
                        "url": url
                    })
            except Exception:
                pass

        wx.CallAfter(self._on_search_complete, all_results)

    # --- Provider Implementations ---

    def _search_itunes(self, term, queue):
        try:
            import urllib.parse
            url = f"https://itunes.apple.com/search?media=podcast&term={urllib.parse.quote(term)}"
            resp = utils.safe_requests_get(url, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                results = []
                for item in data.get("results", []):
                    results.append({
                        "title": item.get("collectionName", "Unknown"),
                        "detail": item.get("artistName", "Unknown"),
                        "url": item.get("feedUrl")
                    })
                queue.put(("iTunes", results))
        except Exception:
            pass

    def _search_gpodder(self, term, queue):
        try:
            import urllib.parse
            url = f"https://gpodder.net/search.json?q={urllib.parse.quote(term)}"
            resp = utils.safe_requests_get(url, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                results = []
                for it in data:
                    if not isinstance(it, dict): continue
                    results.append({
                        "title": it.get("title") or it.get("url"),
                        "detail": it.get("author") or "",
                        "url": it.get("url")
                    })
                queue.put(("gPodder", results))
        except Exception:
            pass

    def _search_feedly(self, term, queue):
        try:
            import urllib.parse
            url = f"https://cloud.feedly.com/v3/search/feeds?q={urllib.parse.quote(term)}"
            resp = utils.safe_requests_get(url, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                results = []
                items = data.get("results", [])
                for it in items:
                    feed_id = it.get("feedId")
                    if feed_id and feed_id.startswith("feed/"):
                        results.append({
                            "title": it.get("title") or feed_id[5:],
                            "detail": it.get("description") or "Feedly",
                            "url": feed_id[5:]
                        })
                queue.put(("Feedly", results))
        except Exception:
            pass

    def _search_newsblur(self, term, queue):
        try:
            import urllib.parse
            # Try autocomplete first
            url = f"https://newsblur.com/rss_feeds/feed_autocomplete?term={urllib.parse.quote(term)}"
            resp = utils.safe_requests_get(url, timeout=10)
            if resp.status_code == 200:
                data = resp.json() # usually a list of dicts
                results = []
                for it in data:
                    if not isinstance(it, dict): continue
                    # NewsBlur structure: {'value': 'url', 'label': 'Title', ...} or similar
                    # Check actual response structure. 
                    # Assuming standard list of dicts with 'value' (ID/URL) and 'label' (Title) 
                    # OR {'feeds': [...]}
                    # Actually standard NewsBlur autocomplete returns list of dicts: {value, label, tagline, num_subscribers}
                    
                    # Also checking /search_feed endpoint if autocomplete is sparse?
                    # sticking to autocomplete for now.
                    
                    feed_url = it.get("value")
                    if not feed_url: continue
                    
                    # Sometimes value is integer ID, sometimes URL.
                    # If it's an integer, we might not get the URL easily without auth.
                    # But for 'feed_autocomplete', it often returns the feed URL in 'address' or 'value' if looking up by address.
                    # Let's check keys carefully.
                    u = it.get("address") or it.get("value")
                    if str(u).isdigit(): continue # Skip internal IDs
                    
                    results.append({
                        "title": it.get("label") or u,
                        "detail": f"{it.get('tagline', '')} ({it.get('num_subscribers', 0)} subs)",
                        "url": u
                    })
                queue.put(("NewsBlur", results))
        except Exception:
            pass

    def _search_reddit(self, term, queue):
        try:
            import urllib.parse
            # Search subreddits
            url = f"https://www.reddit.com/subreddits/search.json?q={urllib.parse.quote(term)}&limit=10"
            headers = {"User-Agent": "BlindRSS/1.0"}
            resp = utils.safe_requests_get(url, headers=headers, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                results = []
                # Reddit API structure: data -> children -> [ { data: { display_name, public_description, subscribers, ... } } ]
                children = data.get("data", {}).get("children", [])
                for child in children:
                    d = child.get("data", {})
                    name = d.get("display_name")
                    if not name: continue
                    
                    # Construct RSS URL
                    rss_url = f"https://www.reddit.com/r/{name}/.rss"
                    desc = d.get("public_description") or d.get("title") or f"r/{name}"
                    subs = d.get("subscribers")
                    if subs:
                        desc = f"{desc} ({subs} subs)"
                        
                    results.append({
                        "title": f"r/{name}",
                        "detail": desc,
                        "url": rss_url
                    })
                queue.put(("Reddit", results))
        except Exception:
            pass

    def _search_feedsearch(self, term, queue):
        try:
            import urllib.parse
            url = f"https://feedsearch.dev/api/v1/search?url={urllib.parse.quote(term)}"
            resp = utils.safe_requests_get(url, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                results = []
                for it in data:
                    results.append({
                        "title": it.get("title") or it.get("url"),
                        "detail": it.get("site_name", "Feedsearch"),
                        "url": it.get("url")
                    })
                queue.put(("Feedsearch", results))
        except Exception:
            pass

    def _search_blindrss(self, term, queue):
        # Local discovery
        try:
            from core.discovery import discover_feeds, discover_feed
            
            candidates = []
            
            # 1. discover_feeds (list)
            try:
                c1 = discover_feeds(term)
                candidates.extend(c1)
            except: pass
            
            # 2. discover_feed (single, maybe different logic)
            if not candidates:
                 try:
                    c2 = discover_feed(term)
                    if c2: candidates.append(c2)
                 except: pass
                 
            # 3. Try with https:// if missing
            if not candidates and "://" not in term:
                 try:
                    c3 = discover_feeds("https://" + term)
                    candidates.extend(c3)
                 except: pass

            results = []
            seen = set()
            for c in candidates:
                if c not in seen:
                    seen.add(c)
                    results.append({
                        "title": c,
                        "detail": "Local Discovery",
                        "url": c
                    })
            if results:
                queue.put(("BlindRSS", results))

        except Exception:
            pass


    def _on_search_complete(self, results):
        self.search_ctrl.Enable()
        self.search_btn.Enable()
        self.status_lbl.SetLabel(f"Found {len(results)} results.")
        self.search_ctrl.SetFocus()
        
        self.results_data = results
        
        for i, item in enumerate(self.results_data):
            idx = self.results_list.InsertItem(i, item["title"])
            self.results_list.SetItem(idx, 1, item["provider"])
            self.results_list.SetItem(idx, 2, item["detail"])

    def on_item_activated(self, event):
        # Select item and close
        self.EndModal(wx.ID_OK)

    def get_selected_url(self):
        # Check selection
        idx = self.results_list.GetFirstSelected()
        if idx != -1:
            return self.results_data[idx]["url"]
        return None


# Backwards-compatible name (menu item was historically called "Search Podcast").
PodcastSearchDialog = FeedSearchDialog
