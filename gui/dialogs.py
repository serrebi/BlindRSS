import wx
import re
import os
from core import utils
from core.config import APP_DIR

class AddFeedDialog(wx.Dialog):
    def __init__(self, parent, categories):
        super().__init__(parent, title="Add Feed")
        
        vbox = wx.BoxSizer(wx.VERTICAL)
        
        hbox1 = wx.BoxSizer(wx.HORIZONTAL)
        hbox1.Add(wx.StaticText(self, label="URL:"), flag=wx.RIGHT, border=8)
        self.tc_url = wx.TextCtrl(self)
        hbox1.Add(self.tc_url, proportion=1)
        btn_search = wx.Button(self, label="Search Podcasts")
        btn_search.Bind(wx.EVT_BUTTON, self.on_search)
        hbox1.Add(btn_search, flag=wx.LEFT, border=8)
        vbox.Add(hbox1, flag=wx.EXPAND|wx.LEFT|wx.RIGHT|wx.TOP, border=10)
        
        hbox2 = wx.BoxSizer(wx.HORIZONTAL)
        hbox2.Add(wx.StaticText(self, label="Category:"), flag=wx.RIGHT, border=8)
        self.cb_category = wx.ComboBox(self, choices=categories, style=wx.CB_DROPDOWN)
        if "Uncategorized" in categories:
            self.cb_category.SetValue("Uncategorized")
        hbox2.Add(self.cb_category, proportion=1)
        vbox.Add(hbox2, flag=wx.EXPAND|wx.LEFT|wx.RIGHT|wx.TOP, border=10)
        
        btns = self.CreateButtonSizer(wx.OK | wx.CANCEL)
        vbox.Add(btns, flag=wx.EXPAND|wx.ALL, border=10)
        
        self.SetSizer(vbox)
        self.Fit()

    def get_data(self):
        return self.tc_url.GetValue(), self.cb_category.GetValue()

    def on_search(self, event):
        dlg = PodcastSearchDialog(self)
        if dlg.ShowModal() == wx.ID_OK:
            url = dlg.get_selected_url()
            if url:
                self.tc_url.SetValue(url)
        dlg.Destroy()

class SettingsDialog(wx.Dialog):
    def __init__(self, parent, config):
        super().__init__(parent, title="Settings", size=(500, 600))
        self.config = config
        self.speed_choices = [self._display_speed(v) for v in utils.build_playback_speeds()]
        self.retention_choices = [
            "1 day", "3 days", "1 week", "2 weeks", "3 weeks",
            "1 month", "2 months", "6 months",
            "1 year", "2 years", "5 years", "Unlimited"
        ]
        
        main_sizer = wx.BoxSizer(wx.VERTICAL)
        
        # Top-level notebook: General / Providers
        nb_main = wx.Notebook(self)

        # --- General tab ---
        p_general = wx.Panel(nb_main)
        gen_sizer = wx.BoxSizer(wx.VERTICAL)

        hbox1 = wx.BoxSizer(wx.HORIZONTAL)
        hbox1.Add(wx.StaticText(p_general, label="Refresh Interval (seconds):"), flag=wx.RIGHT, border=8)
        self.sp_refresh = wx.SpinCtrl(p_general, min=30, max=3600, initial=int(self.config.get("refresh_interval", 300)))
        hbox1.Add(self.sp_refresh, proportion=1)
        gen_sizer.Add(hbox1, flag=wx.EXPAND|wx.ALL, border=10)

        hbox_conc = wx.BoxSizer(wx.HORIZONTAL)
        hbox_conc.Add(wx.StaticText(p_general, label="Max concurrent refreshes:"), flag=wx.RIGHT, border=8)
        self.sp_max_concurrent = wx.SpinCtrl(p_general, min=1, max=64, initial=int(self.config.get("max_concurrent_refreshes", 12)))
        hbox_conc.Add(self.sp_max_concurrent, proportion=1)
        gen_sizer.Add(hbox_conc, flag=wx.EXPAND|wx.LEFT|wx.RIGHT|wx.TOP, border=10)

        hbox_host = wx.BoxSizer(wx.HORIZONTAL)
        hbox_host.Add(wx.StaticText(p_general, label="Per-host concurrency:"), flag=wx.RIGHT, border=8)
        self.sp_per_host = wx.SpinCtrl(p_general, min=1, max=16, initial=int(self.config.get("per_host_max_connections", 3)))
        hbox_host.Add(self.sp_per_host, proportion=1)
        gen_sizer.Add(hbox_host, flag=wx.EXPAND|wx.LEFT|wx.RIGHT|wx.TOP, border=10)

        hbox_timeout = wx.BoxSizer(wx.HORIZONTAL)
        hbox_timeout.Add(wx.StaticText(p_general, label="Feed timeout (seconds):"), flag=wx.RIGHT, border=8)
        self.sp_feed_timeout = wx.SpinCtrl(p_general, min=5, max=120, initial=int(self.config.get("feed_timeout_seconds", 15)))
        hbox_timeout.Add(self.sp_feed_timeout, proportion=1)
        gen_sizer.Add(hbox_timeout, flag=wx.EXPAND|wx.LEFT|wx.RIGHT|wx.TOP, border=10)

        hbox_retries = wx.BoxSizer(wx.HORIZONTAL)
        hbox_retries.Add(wx.StaticText(p_general, label="Retry attempts per feed:"), flag=wx.RIGHT, border=8)
        self.sp_feed_retries = wx.SpinCtrl(p_general, min=0, max=5, initial=int(self.config.get("feed_retry_attempts", 1)))
        hbox_retries.Add(self.sp_feed_retries, proportion=1)
        gen_sizer.Add(hbox_retries, flag=wx.EXPAND|wx.LEFT|wx.RIGHT|wx.TOP, border=10)

        self.cb_skip_silence = wx.CheckBox(p_general, label="Skip silence during playback (requires ffmpeg)")
        self.cb_skip_silence.SetValue(bool(self.config.get("skip_silence", False)))
        gen_sizer.Add(self.cb_skip_silence, flag=wx.EXPAND|wx.ALL, border=10)

        self.cb_close_to_tray = wx.CheckBox(p_general, label="Close button sends app to system tray")
        self.cb_close_to_tray.SetValue(bool(self.config.get("close_to_tray", False)))
        gen_sizer.Add(self.cb_close_to_tray, flag=wx.EXPAND|wx.LEFT|wx.RIGHT|wx.TOP, border=10)

        self.cb_minimize_to_tray = wx.CheckBox(p_general, label="Minimize to system tray")
        self.cb_minimize_to_tray.SetValue(bool(self.config.get("minimize_to_tray", True)))
        gen_sizer.Add(self.cb_minimize_to_tray, flag=wx.EXPAND|wx.LEFT|wx.RIGHT|wx.TOP, border=10)

        hbox_speed = wx.BoxSizer(wx.HORIZONTAL)
        hbox_speed.Add(wx.StaticText(p_general, label="Playback speed:"), flag=wx.RIGHT|wx.ALIGN_CENTER_VERTICAL, border=8)
        self.cb_playback_speed = wx.ComboBox(
            p_general,
            choices=self.speed_choices,
            style=wx.CB_READONLY
        )
        nearest = self._nearest_speed_value(self.config.get("playback_speed", 1.0))
        self.cb_playback_speed.SetValue(self._display_speed(nearest))
        hbox_speed.Add(self.cb_playback_speed, proportion=1)
        gen_sizer.Add(hbox_speed, flag=wx.EXPAND|wx.LEFT|wx.RIGHT|wx.TOP, border=10)

        p_general.SetSizer(gen_sizer)
        nb_main.AddPage(p_general, "General")

        # --- Downloads tab ---
        p_downloads = wx.Panel(nb_main)
        self._init_downloads_tab(p_downloads)
        nb_main.AddPage(p_downloads, "Downloads")

        # --- Providers tab ---
        p_prov = wx.Panel(nb_main)
        prov_sizer = wx.BoxSizer(wx.VERTICAL)

        hbox_prov = wx.BoxSizer(wx.HORIZONTAL)
        hbox_prov.Add(wx.StaticText(p_prov, label="Active Provider:"), flag=wx.RIGHT, border=8)
        self.cb_provider = wx.ComboBox(p_prov, choices=["local", "miniflux", "theoldreader", "inoreader", "bazqux"], style=wx.CB_READONLY)
        self.cb_provider.SetValue(self.config.get("active_provider", "local"))
        hbox_prov.Add(self.cb_provider, proportion=1)
        prov_sizer.Add(hbox_prov, flag=wx.EXPAND|wx.ALL, border=10)

        nb_prov = wx.Notebook(p_prov)

        self.p_mf = wx.Panel(nb_prov)
        self._init_miniflux_tab(self.p_mf)
        nb_prov.AddPage(self.p_mf, "Miniflux")

        self.p_tor = wx.Panel(nb_prov)
        self._init_tor_tab(self.p_tor)
        nb_prov.AddPage(self.p_tor, "TheOldReader")

        self.p_ino = wx.Panel(nb_prov)
        self._init_ino_tab(self.p_ino)
        nb_prov.AddPage(self.p_ino, "Inoreader")

        self.p_bz = wx.Panel(nb_prov)
        self._init_bz_tab(self.p_bz)
        nb_prov.AddPage(self.p_bz, "BazQux")

        prov_sizer.Add(nb_prov, 1, wx.EXPAND|wx.ALL, 10)
        p_prov.SetSizer(prov_sizer)
        nb_main.AddPage(p_prov, "Providers")

        main_sizer.Add(nb_main, 1, wx.EXPAND|wx.ALL, 5)
        
        btns = self.CreateButtonSizer(wx.OK | wx.CANCEL)
        main_sizer.Add(btns, flag=wx.EXPAND|wx.ALL, border=10)
        
        self.SetSizer(main_sizer)

    def _init_downloads_tab(self, p):
        sizer = wx.BoxSizer(wx.VERTICAL)

        self.chk_enable_downloads = wx.CheckBox(p, label="Enable downloads")
        self.chk_enable_downloads.SetValue(bool(self.config.get("downloads_enabled", False)))
        self.chk_enable_downloads.Bind(wx.EVT_CHECKBOX, self._on_download_toggle)
        sizer.Add(self.chk_enable_downloads, 0, wx.EXPAND|wx.ALL, 10)

        hbox_path = wx.BoxSizer(wx.HORIZONTAL)
        hbox_path.Add(wx.StaticText(p, label="Download location:"), 0, wx.ALIGN_CENTER_VERTICAL|wx.RIGHT, 8)
        default_path = self.config.get("download_path") or os.path.join(APP_DIR, "podcasts")
        self.tc_download_path = wx.TextCtrl(p, value=str(default_path))
        hbox_path.Add(self.tc_download_path, 1, wx.EXPAND)
        self.btn_browse_download = wx.Button(p, label="Browse...")
        self.btn_browse_download.Bind(wx.EVT_BUTTON, self._on_browse_download)
        hbox_path.Add(self.btn_browse_download, 0, wx.LEFT, 8)
        sizer.Add(hbox_path, 0, wx.EXPAND|wx.LEFT|wx.RIGHT|wx.TOP, 10)

        hbox_keep = wx.BoxSizer(wx.HORIZONTAL)
        hbox_keep.Add(wx.StaticText(p, label="Keep episodes for:"), 0, wx.ALIGN_CENTER_VERTICAL|wx.RIGHT, 8)
        self.cb_retention = wx.ComboBox(p, choices=self.retention_choices, style=wx.CB_READONLY)
        current_retention = self.config.get("download_retention", "Unlimited")
        if current_retention not in self.retention_choices:
            current_retention = "Unlimited"
        self.cb_retention.SetValue(current_retention)
        hbox_keep.Add(self.cb_retention, 1)
        sizer.Add(hbox_keep, 0, wx.EXPAND|wx.LEFT|wx.RIGHT|wx.TOP, 10)

        p.SetSizer(sizer)
        self._update_download_controls_state()

    def _on_download_toggle(self, event):
        self._update_download_controls_state()

    def _update_download_controls_state(self):
        enabled = self.chk_enable_downloads.GetValue()
        for ctrl in (self.tc_download_path, self.btn_browse_download, self.cb_retention):
            ctrl.Enable(enabled)

    def _on_browse_download(self, event):
        style = wx.DD_DEFAULT_STYLE | wx.DD_NEW_DIR_BUTTON
        dlg = wx.DirDialog(self, "Choose download folder", defaultPath=self.tc_download_path.GetValue(), style=style)
        if dlg.ShowModal() == wx.ID_OK:
            self.tc_download_path.SetValue(dlg.GetPath())
        dlg.Destroy()

    def _init_miniflux_tab(self, p):
        sizer = wx.BoxSizer(wx.VERTICAL)
        grid = wx.FlexGridSizer(3, 2, 5, 5)
        
        grid.Add(wx.StaticText(p, label="URL:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.tc_mf_url = wx.TextCtrl(p)
        self.tc_mf_url.SetValue(self.config.get("providers", {}).get("miniflux", {}).get("url", ""))
        grid.Add(self.tc_mf_url, 1, wx.EXPAND)
        
        grid.Add(wx.StaticText(p, label="API Key:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.tc_mf_key = wx.TextCtrl(p)
        self.tc_mf_key.SetValue(self.config.get("providers", {}).get("miniflux", {}).get("api_key", ""))
        grid.Add(self.tc_mf_key, 1, wx.EXPAND)
        
        btn_test = wx.Button(p, label="Test Connection")
        btn_test.Bind(wx.EVT_BUTTON, self.on_test_miniflux)
        grid.Add(btn_test, 0)
        
        grid.AddGrowableCol(1, 1)
        sizer.Add(grid, 1, wx.EXPAND|wx.ALL, 10)
        p.SetSizer(sizer)

    def _init_tor_tab(self, p):
        sizer = wx.BoxSizer(wx.VERTICAL)
        grid = wx.FlexGridSizer(2, 2, 5, 5)
        
        grid.Add(wx.StaticText(p, label="Email:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.tc_tor_email = wx.TextCtrl(p)
        self.tc_tor_email.SetValue(self.config.get("providers", {}).get("theoldreader", {}).get("email", ""))
        grid.Add(self.tc_tor_email, 1, wx.EXPAND)
        
        grid.Add(wx.StaticText(p, label="Password:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.tc_tor_pass = wx.TextCtrl(p, style=wx.TE_PASSWORD)
        self.tc_tor_pass.SetValue(self.config.get("providers", {}).get("theoldreader", {}).get("password", ""))
        grid.Add(self.tc_tor_pass, 1, wx.EXPAND)
        
        grid.AddGrowableCol(1, 1)
        sizer.Add(grid, 1, wx.EXPAND|wx.ALL, 10)
        p.SetSizer(sizer)
        
    def _init_ino_tab(self, p):
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(wx.StaticText(p, label="API Token:"), 0, wx.ALL, 5)
        self.tc_ino_token = wx.TextCtrl(p)
        self.tc_ino_token.SetValue(self.config.get("providers", {}).get("inoreader", {}).get("token", ""))
        sizer.Add(self.tc_ino_token, 0, wx.EXPAND|wx.ALL, 5)
        p.SetSizer(sizer)

    def _init_bz_tab(self, p):
        sizer = wx.BoxSizer(wx.VERTICAL)
        grid = wx.FlexGridSizer(2, 2, 5, 5)
        
        grid.Add(wx.StaticText(p, label="Email:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.tc_bz_email = wx.TextCtrl(p)
        self.tc_bz_email.SetValue(self.config.get("providers", {}).get("bazqux", {}).get("email", ""))
        grid.Add(self.tc_bz_email, 1, wx.EXPAND)
        
        grid.Add(wx.StaticText(p, label="Password:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.tc_bz_pass = wx.TextCtrl(p, style=wx.TE_PASSWORD)
        self.tc_bz_pass.SetValue(self.config.get("providers", {}).get("bazqux", {}).get("password", ""))
        grid.Add(self.tc_bz_pass, 1, wx.EXPAND)
        
        grid.AddGrowableCol(1, 1)
        sizer.Add(grid, 1, wx.EXPAND|wx.ALL, 10)
        p.SetSizer(sizer)

    def on_test_miniflux(self, event):
        # Logic moved here or duplicated for quick fix, ideally shared
        try:
            from providers.miniflux import MinifluxProvider
            prov = MinifluxProvider({"providers": {"miniflux": {"url": self.tc_mf_url.GetValue(), "api_key": self.tc_mf_key.GetValue()}}})
            if prov.test_connection():
                wx.MessageBox("Connection Successful!", "Success")
            else:
                wx.MessageBox("Connection Failed.", "Error")
        except: pass

    def get_data(self):
        # Update config structure
        if "providers" not in self.config: self.config["providers"] = {}
        
        # Miniflux
        if "miniflux" not in self.config["providers"]: self.config["providers"]["miniflux"] = {}
        self.config["providers"]["miniflux"]["url"] = self.tc_mf_url.GetValue()
        self.config["providers"]["miniflux"]["api_key"] = self.tc_mf_key.GetValue()
        
        # TheOldReader
        if "theoldreader" not in self.config["providers"]: self.config["providers"]["theoldreader"] = {}
        self.config["providers"]["theoldreader"]["email"] = self.tc_tor_email.GetValue()
        self.config["providers"]["theoldreader"]["password"] = self.tc_tor_pass.GetValue()
        
        # Inoreader
        if "inoreader" not in self.config["providers"]: self.config["providers"]["inoreader"] = {}
        self.config["providers"]["inoreader"]["token"] = self.tc_ino_token.GetValue()
        
        # BazQux
        if "bazqux" not in self.config["providers"]: self.config["providers"]["bazqux"] = {}
        self.config["providers"]["bazqux"]["email"] = self.tc_bz_email.GetValue()
        self.config["providers"]["bazqux"]["password"] = self.tc_bz_pass.GetValue()
        
        return {
            "refresh_interval": self.sp_refresh.GetValue(),
            "max_concurrent_refreshes": self.sp_max_concurrent.GetValue(),
            "per_host_max_connections": self.sp_per_host.GetValue(),
            "feed_timeout_seconds": self.sp_feed_timeout.GetValue(),
            "feed_retry_attempts": self.sp_feed_retries.GetValue(),
            "active_provider": self.cb_provider.GetValue(),
            "skip_silence": self.cb_skip_silence.GetValue(),
            "close_to_tray": self.cb_close_to_tray.GetValue(),
            "minimize_to_tray": self.cb_minimize_to_tray.GetValue(),
            "playback_speed": self._parse_speed(self.cb_playback_speed.GetValue()),
            "downloads_enabled": self.chk_enable_downloads.GetValue(),
            "download_path": self.tc_download_path.GetValue(),
            "download_retention": self.cb_retention.GetValue(),
            "providers": self.config["providers"]
        }

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

    def _parse_speed(self, text):
        m = re.search(r"[0-9]+(?:\.[0-9]+)?", str(text))
        if not m:
            return 1.0
        try:
            return float(m.group(0))
        except Exception:
            return 1.0

    def _nearest_speed_value(self, speed):
        try:
            speed = float(speed)
        except Exception:
            speed = 1.0
        speeds = utils.build_playback_speeds()
        return min(speeds, key=lambda v: abs(v - speed))


class PodcastSearchDialog(wx.Dialog):
    def __init__(self, parent):
        super().__init__(parent, title="Search Podcasts", size=(600, 500))
        vbox = wx.BoxSizer(wx.VERTICAL)

        hbox = wx.BoxSizer(wx.HORIZONTAL)
        hbox.Add(wx.StaticText(self, label="Query:"), flag=wx.RIGHT|wx.ALIGN_CENTER_VERTICAL, border=5)
        self.tc_query = wx.TextCtrl(self)
        hbox.Add(self.tc_query, 1, wx.EXPAND|wx.RIGHT, 5)
        btn_search = wx.Button(self, label="Search")
        btn_search.Bind(wx.EVT_BUTTON, self.on_search)
        hbox.Add(btn_search, 0)
        vbox.Add(hbox, 0, wx.EXPAND|wx.ALL, 10)

        self.list = wx.ListCtrl(self, style=wx.LC_REPORT|wx.BORDER_SUNKEN)
        self.list.InsertColumn(0, "Title", width=300)
        self.list.InsertColumn(1, "Author", width=180)
        self.list.InsertColumn(2, "Feed URL", width=400)
        vbox.Add(self.list, 1, wx.EXPAND|wx.LEFT|wx.RIGHT, 10)

        btns = self.CreateButtonSizer(wx.OK | wx.CANCEL)
        vbox.Add(btns, 0, wx.EXPAND|wx.ALL, 10)

        self.SetSizer(vbox)
        self.results = []

    def on_search(self, event):
        import requests, urllib.parse
        term = self.tc_query.GetValue().strip()
        if not term:
            return
        url = f"https://itunes.apple.com/search?media=podcast&term={urllib.parse.quote(term)}&limit=20"
        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            self.results = data.get("results", [])
            self.populate_results()
        except Exception as e:
            wx.MessageBox(f"Search failed: {e}", "Error", wx.ICON_ERROR)

    def populate_results(self):
        self.list.DeleteAllItems()
        for i, r in enumerate(self.results):
            title = r.get("collectionName") or r.get("trackName") or ""
            author = r.get("artistName", "")
            feed = r.get("feedUrl", "")
            idx = self.list.InsertItem(i, title)
            self.list.SetItem(idx, 1, author)
            self.list.SetItem(idx, 2, feed)

    def get_selected_url(self):
        idx = self.list.GetFirstSelected()
        if idx == -1 or idx >= len(self.results):
            return None
        return self.results[idx].get("feedUrl")
