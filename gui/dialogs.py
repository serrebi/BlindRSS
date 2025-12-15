import wx
from urllib.parse import urlparse
from core.discovery import discover_feed
from core import utils


class AddFeedDialog(wx.Dialog):
    def __init__(self, parent, categories=None):
        super().__init__(parent, title="Add Feed", size=(400, 200))
        
        self.categories = categories or ["Uncategorized"]
        
        sizer = wx.BoxSizer(wx.VERTICAL)
        
        # URL Input
        sizer.Add(wx.StaticText(self, label="Feed URL:"), 0, wx.ALL, 5)
        self.url_ctrl = wx.TextCtrl(self)
        sizer.Add(self.url_ctrl, 0, wx.EXPAND | wx.ALL, 5)
        
        # Category Input
        sizer.Add(wx.StaticText(self, label="Category:"), 0, wx.ALL, 5)
        self.cat_ctrl = wx.ComboBox(self, choices=self.categories, style=wx.CB_DROPDOWN)
        if self.categories:
            self.cat_ctrl.SetSelection(0)
        sizer.Add(self.cat_ctrl, 0, wx.EXPAND | wx.ALL, 5)
        
        # Buttons
        btn_sizer = self.CreateButtonSizer(wx.OK | wx.CANCEL)
        sizer.Add(btn_sizer, 0, wx.ALIGN_CENTER | wx.ALL, 5)
        
        self.SetSizer(sizer)
        self.Centre()

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
        
        general_panel.SetSizer(general_sizer)
        notebook.AddPage(general_panel, "General")
        
        # Provider Tab
        provider_panel = wx.Panel(notebook)
        provider_sizer = wx.BoxSizer(wx.VERTICAL)
        
        provider_sizer.Add(wx.StaticText(provider_panel, label="Active Provider:"), 0, wx.ALL, 5)
        self.provider_choice = wx.Choice(provider_panel, choices=["local", "miniflux", "bazqux", "theoldreader", "inoreader"])
        self.provider_choice.SetStringSelection(config.get("active_provider", "local"))
        provider_sizer.Add(self.provider_choice, 0, wx.EXPAND | wx.ALL, 5)
        
        # Provider Configs (Simplified for now - just edit the JSON directly or add fields here)
        provider_sizer.Add(wx.StaticText(provider_panel, label="Note: Configure specific provider credentials in config.json"), 0, wx.ALL, 5)
        
        provider_panel.SetSizer(provider_sizer)
        notebook.AddPage(provider_panel, "Provider")
        
        # Main Sizer
        main_sizer = wx.BoxSizer(wx.VERTICAL)
        main_sizer.Add(notebook, 1, wx.EXPAND | wx.ALL, 5)
        
        btn_sizer = self.CreateButtonSizer(wx.OK | wx.CANCEL)
        main_sizer.Add(btn_sizer, 0, wx.ALIGN_CENTER | wx.ALL, 5)
        
        self.SetSizer(main_sizer)
        self.Centre()

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
            "active_provider": self.provider_choice.GetStringSelection()
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


class PodcastSearchDialog(wx.Dialog):
    def __init__(self, parent):
        super().__init__(parent, title="Search Podcast (iTunes)", size=(500, 400))
        
        self.selected_url = None
        
        sizer = wx.BoxSizer(wx.VERTICAL)
        
        # Search Box
        search_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.search_ctrl = wx.SearchCtrl(self, style=wx.TE_PROCESS_ENTER)
        self.search_ctrl.ShowCancelButton(True)
        search_sizer.Add(self.search_ctrl, 1, wx.ALL, 5)
        
        search_btn = wx.Button(self, label="Search")
        search_sizer.Add(search_btn, 0, wx.ALL, 5)
        
        sizer.Add(search_sizer, 0, wx.EXPAND)
        
        # Results List
        self.results_list = wx.ListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
        self.results_list.InsertColumn(0, "Podcast", width=250)
        self.results_list.InsertColumn(1, "Author", width=150)
        self.results_list.InsertColumn(2, "URL", width=0) # Hidden
        
        sizer.Add(self.results_list, 1, wx.EXPAND | wx.ALL, 5)
        
        # Buttons
        btn_sizer = self.CreateButtonSizer(wx.OK | wx.CANCEL)
        sizer.Add(btn_sizer, 0, wx.ALIGN_CENTER | wx.ALL, 5)
        
        self.SetSizer(sizer)
        self.Centre()
        
        # Bindings
        search_btn.Bind(wx.EVT_BUTTON, self.on_search)
        self.search_ctrl.Bind(wx.EVT_TEXT_ENTER, self.on_search)
        self.search_ctrl.Bind(wx.EVT_SEARCHCTRL_SEARCH_BTN, self.on_search)
        self.results_list.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self.on_item_activated)
        
        self.results_data = [] # List of dicts

    def on_search(self, event):
        term = self.search_ctrl.GetValue()
        if not term:
            return
            
        self.results_list.DeleteAllItems()
        self.results_data = []
        
        import requests
        import urllib.parse
        
        try:
            url = f"https://itunes.apple.com/search?media=podcast&term={urllib.parse.quote(term)}"
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            
            for item in data.get("results", []):
                title = item.get("collectionName", "Unknown")
                author = item.get("artistName", "Unknown")
                feed_url = item.get("feedUrl")
                
                if feed_url:
                    self.results_data.append({"title": title, "author": author, "url": feed_url})
                    
            for i, item in enumerate(self.results_data):
                idx = self.results_list.InsertItem(i, item["title"])
                self.results_list.SetItem(idx, 1, item["author"])
                
        except Exception as e:
            wx.MessageBox(f"Search failed: {e}", "Error", wx.ICON_ERROR)

    def on_item_activated(self, event):
        # Select item and close
        self.EndModal(wx.ID_OK)

    def get_selected_url(self):
        # Check selection
        idx = self.results_list.GetFirstSelected()
        if idx != -1:
            return self.results_data[idx]["url"]
        return None