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
        super().__init__(parent, title="Find a Podcast or RSS Feed", size=(650, 480))
        
        self.selected_url = None
        
        sizer = wx.BoxSizer(wx.VERTICAL)

        # Provider choice
        provider_sizer = wx.BoxSizer(wx.HORIZONTAL)
        provider_sizer.Add(wx.StaticText(self, label="Search using:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.provider_choice = wx.Choice(
            self,
            choices=[
                "Apple Podcasts (iTunes) - keyword search",
                "Feedsearch.dev - find feeds for a website URL",
                "BlindRSS - discover feeds from a website URL",
            ],
        )
        self.provider_choice.SetSelection(0)
        provider_sizer.Add(self.provider_choice, 1, wx.EXPAND | wx.ALL, 5)
        sizer.Add(provider_sizer, 0, wx.EXPAND)
        
        # Search Box
        self.input_lbl = wx.StaticText(self, label="Search term:")
        sizer.Add(self.input_lbl, 0, wx.LEFT | wx.RIGHT | wx.TOP, 5)

        search_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.search_ctrl = wx.SearchCtrl(self, style=wx.TE_PROCESS_ENTER)
        self.search_ctrl.ShowCancelButton(True)
        search_sizer.Add(self.search_ctrl, 1, wx.ALL, 5)
        
        self.search_btn = wx.Button(self, label="Search")
        search_sizer.Add(self.search_btn, 0, wx.ALL, 5)
        
        sizer.Add(search_sizer, 0, wx.EXPAND)

        # External tools (opens browser)
        tools_box = wx.StaticBoxSizer(wx.StaticBox(self, label="External feed finders (opens browser)"), wx.VERTICAL)
        tools_wrap = wx.WrapSizer(wx.HORIZONTAL)

        self.btn_rssfinder = wx.Button(self, label="RSSFinder.app")
        self.btn_getrssfeed = wx.Button(self, label="GetRSSFeed.com")
        self.btn_feedsearch_site = wx.Button(self, label="Feedsearch.dev")
        self.btn_castos = wx.Button(self, label="Castos")
        self.btn_awesome = wx.Button(self, label="Awesome RSS Feeds (GitHub)")

        for b in (self.btn_rssfinder, self.btn_getrssfeed, self.btn_feedsearch_site, self.btn_castos, self.btn_awesome):
            tools_wrap.Add(b, 0, wx.ALL, 3)

        tools_box.Add(tools_wrap, 0, wx.EXPAND | wx.ALL, 2)
        sizer.Add(tools_box, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)
        
        # Results List
        self.results_list = wx.ListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
        self.results_list.InsertColumn(0, "Feed", width=320)
        self.results_list.InsertColumn(1, "Details", width=220)
        self.results_list.InsertColumn(2, "URL", width=0) # Hidden
        
        sizer.Add(self.results_list, 1, wx.EXPAND | wx.ALL, 5)

        # Feedsearch attribution (required when showing Feedsearch-powered results)
        self._feedsearch_attrib = None
        try:
            import wx.adv as wxadv

            self._feedsearch_attrib = wxadv.HyperlinkCtrl(self, label="powered by Feedsearch", url="https://feedsearch.dev")
        except Exception:
            self._feedsearch_attrib = wx.Button(self, label="powered by Feedsearch")
            self._feedsearch_attrib.Bind(wx.EVT_BUTTON, lambda _e: webbrowser.open("https://feedsearch.dev"))
        try:
            self._feedsearch_attrib.Hide()
        except Exception:
            pass
        sizer.Add(self._feedsearch_attrib, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        
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
        self.provider_choice.Bind(wx.EVT_CHOICE, self.on_provider_changed)

        self.btn_rssfinder.Bind(wx.EVT_BUTTON, self.on_open_rssfinder)
        self.btn_getrssfeed.Bind(wx.EVT_BUTTON, self.on_open_getrssfeed)
        self.btn_feedsearch_site.Bind(wx.EVT_BUTTON, self.on_open_feedsearch_site)
        self.btn_castos.Bind(wx.EVT_BUTTON, self.on_open_castos)
        self.btn_awesome.Bind(wx.EVT_BUTTON, self.on_open_awesome)
        
        self.results_data = [] # List of dicts
        self.on_provider_changed(None)

    def _provider_key(self) -> str:
        try:
            idx = int(self.provider_choice.GetSelection())
        except Exception:
            idx = 0
        if idx == 1:
            return "feedsearch"
        if idx == 2:
            return "builtin"
        return "itunes"

    def on_provider_changed(self, event):
        key = self._provider_key()
        if key == "itunes":
            self.input_lbl.SetLabel("Search term:")
        else:
            self.input_lbl.SetLabel("Website URL:")
        try:
            if self._feedsearch_attrib:
                self._feedsearch_attrib.Show(key == "feedsearch")
                self.Layout()
        except Exception:
            pass

    def _open_url(self, url: str) -> None:
        try:
            if url:
                webbrowser.open(url)
        except Exception:
            pass

    def on_open_rssfinder(self, event) -> None:
        term = (self.search_ctrl.GetValue() or "").strip()
        if term:
            import urllib.parse

            self._open_url(f"https://rssfinder.app/?q={urllib.parse.quote(term)}")
        else:
            self._open_url("https://rssfinder.app/")

    def on_open_getrssfeed(self, event) -> None:
        self._open_url("https://getrssfeed.com/")

    def on_open_feedsearch_site(self, event) -> None:
        term = (self.search_ctrl.GetValue() or "").strip()
        if term:
            import urllib.parse

            self._open_url(f"https://feedsearch.dev/api/v1/search?url={urllib.parse.quote(term)}&result=true")
        else:
            self._open_url("https://feedsearch.dev/")

    def on_open_castos(self, event) -> None:
        self._open_url("https://castos.com/tools/find-podcast-rss-feed/")

    def on_open_awesome(self, event) -> None:
        self._open_url("https://github.com/plenaryapp/awesome-rss-feeds")

    def on_search(self, event):
        term = (self.search_ctrl.GetValue() or "").strip()
        if not term:
            return
            
        self.results_list.DeleteAllItems()
        self.results_data = []
        
        # Disable UI
        self.search_ctrl.Disable()
        try:
            self.search_btn.Disable()
        except Exception:
            pass
        wx.BeginBusyCursor()
        
        import threading
        provider = self._provider_key()
        threading.Thread(target=self._search_thread, args=(term, provider), daemon=True).start()

    def _search_thread(self, term, provider):
        import urllib.parse

        data = None
        error = None

        try:
            if provider == "itunes":
                url = f"https://itunes.apple.com/search?media=podcast&term={urllib.parse.quote(term)}"
                resp = utils.safe_requests_get(url, timeout=10)
                resp.raise_for_status()
                data = resp.json()
            elif provider == "feedsearch":
                url = f"https://feedsearch.dev/api/v1/search?url={urllib.parse.quote(term)}"
                resp = utils.safe_requests_get(url, timeout=15)
                resp.raise_for_status()
                data = resp.json()
            else:
                # builtin discovery
                data = {"feeds": []}
                try:
                    from core.discovery import discover_feeds

                    candidates = discover_feeds(term)
                except Exception:
                    candidates = []

                if not candidates:
                    # Best-effort: try with https:// prefix when missing scheme.
                    if "://" not in term and not term.lower().startswith("lbry:"):
                        try:
                            from core.discovery import discover_feeds

                            candidates = discover_feeds("https://" + term)
                        except Exception:
                            candidates = []

                if not candidates:
                    try:
                        f = discover_feed(term)
                        if f:
                            candidates = [f]
                    except Exception:
                        candidates = []

                data["feeds"] = candidates
        except Exception as e:
            error = str(e)
            
        wx.CallAfter(self._on_search_complete, data, error)

    def _on_search_complete(self, data, error):
        wx.EndBusyCursor()
        self.search_ctrl.Enable()
        try:
            self.search_btn.Enable()
        except Exception:
            pass
        self.search_ctrl.SetFocus()
        
        if error:
            wx.MessageBox(f"Search failed: {error}", "Error", wx.ICON_ERROR)
            return

        if not data:
            return

        provider = self._provider_key()
        if provider == "itunes":
            for item in data.get("results", []):
                title = item.get("collectionName", "Unknown")
                author = item.get("artistName", "Unknown")
                feed_url = item.get("feedUrl")
                
                if feed_url:
                    self.results_data.append({"title": title, "detail": author, "url": feed_url})
        elif provider == "feedsearch":
            items = data if isinstance(data, list) else []

            def _rank(it):
                try:
                    score = float(it.get("score") or 0.0) if isinstance(it, dict) else 0.0
                except Exception:
                    score = 0.0
                is_podcast = bool(it.get("is_podcast")) if isinstance(it, dict) else False
                try:
                    item_count = int(it.get("item_count") or 0) if isinstance(it, dict) else 0
                except Exception:
                    item_count = 0
                return (is_podcast, score, item_count)

            items = sorted(items, key=_rank, reverse=True)

            for it in items[:250]:
                if not isinstance(it, dict):
                    continue
                feed_url = it.get("url")
                if not isinstance(feed_url, str) or not feed_url.strip():
                    continue
                feed_url = feed_url.strip()
                title = it.get("title") if isinstance(it.get("title"), str) and it.get("title").strip() else feed_url
                site = it.get("site_name") or it.get("site_url") or "Feedsearch"
                if not isinstance(site, str):
                    site = "Feedsearch"
                if bool(it.get("is_podcast")):
                    site = f"{site} (podcast)"
                self.results_data.append({"title": title, "detail": site, "url": feed_url})
        else:
            feeds = data.get("feeds") if isinstance(data, dict) else []
            if isinstance(feeds, list):
                for feed_url in feeds[:250]:
                    if not isinstance(feed_url, str) or not feed_url.strip():
                        continue
                    self.results_data.append({"title": feed_url.strip(), "detail": "Discovered", "url": feed_url.strip()})
                
        for i, item in enumerate(self.results_data):
            idx = self.results_list.InsertItem(i, item["title"])
            self.results_list.SetItem(idx, 1, item.get("detail", ""))

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
