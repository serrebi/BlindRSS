import wx

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
        
        main_sizer = wx.BoxSizer(wx.VERTICAL)
        
        panel = wx.Panel(self)
        panel_sizer = wx.BoxSizer(wx.VERTICAL)
        
        # Provider Selection
        hbox_prov = wx.BoxSizer(wx.HORIZONTAL)
        hbox_prov.Add(wx.StaticText(panel, label="Active Provider:"), flag=wx.RIGHT, border=8)
        self.cb_provider = wx.ComboBox(panel, choices=["local", "miniflux", "theoldreader", "inoreader", "bazqux"], style=wx.CB_READONLY)
        self.cb_provider.SetValue(self.config.get("active_provider", "local"))
        hbox_prov.Add(self.cb_provider, proportion=1)
        panel_sizer.Add(hbox_prov, flag=wx.EXPAND|wx.ALL, border=10)
        
        # Refresh Interval
        hbox1 = wx.BoxSizer(wx.HORIZONTAL)
        hbox1.Add(wx.StaticText(panel, label="Refresh Interval (seconds):"), flag=wx.RIGHT, border=8)
        self.sp_refresh = wx.SpinCtrl(panel, min=30, max=3600, initial=int(self.config.get("refresh_interval", 300)))
        hbox1.Add(self.sp_refresh, proportion=1)
        panel_sizer.Add(hbox1, flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=10)
        
        # Notebook for Provider Settings
        nb = wx.Notebook(panel)
        
        # Miniflux Tab
        self.p_mf = wx.Panel(nb)
        self._init_miniflux_tab(self.p_mf)
        nb.AddPage(self.p_mf, "Miniflux")
        
        # TheOldReader Tab
        self.p_tor = wx.Panel(nb)
        self._init_tor_tab(self.p_tor)
        nb.AddPage(self.p_tor, "TheOldReader")
        
        # Inoreader Tab
        self.p_ino = wx.Panel(nb)
        self._init_ino_tab(self.p_ino)
        nb.AddPage(self.p_ino, "Inoreader")
        
        # BazQux Tab
        self.p_bz = wx.Panel(nb)
        self._init_bz_tab(self.p_bz)
        nb.AddPage(self.p_bz, "BazQux")
        
        panel_sizer.Add(nb, 1, wx.EXPAND|wx.ALL, 10)
        
        panel.SetSizer(panel_sizer)
        
        main_sizer.Add(panel, 1, wx.EXPAND)
        
        btns = self.CreateButtonSizer(wx.OK | wx.CANCEL)
        main_sizer.Add(btns, flag=wx.EXPAND|wx.ALL, border=10)
        
        self.SetSizer(main_sizer)

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
            "active_provider": self.cb_provider.GetValue(),
            "providers": self.config["providers"]
        }


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
