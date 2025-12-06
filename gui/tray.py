import wx
import wx.adv

class BlindRSSTrayIcon(wx.adv.TaskBarIcon):
    def __init__(self, frame):
        super().__init__()
        self.frame = frame
        
        # Set Icon
        self.set_default_icon()
        
        self.Bind(wx.adv.EVT_TASKBAR_LEFT_DOWN, self.on_left_down)
        
    def set_default_icon(self):
        # Create a simple colored block icon
        icon_size = wx.SystemSettings.GetMetric(wx.SYS_ICON_X)
        bmp = wx.Bitmap(icon_size, icon_size)
        dc = wx.MemoryDC(bmp)
        dc.SetBackground(wx.Brush("ORANGE"))
        dc.Clear()
        dc.SetTextForeground("WHITE")
        font = wx.SystemSettings.GetFont(wx.SYS_DEFAULT_GUI_FONT)
        font.SetWeight(wx.FONTWEIGHT_BOLD)
        dc.SetFont(font)
        dc.DrawText("R", 2, 2)
        dc.SelectObject(wx.NullBitmap)
        
        icon = wx.Icon()
        icon.CopyFromBitmap(bmp)
        self.SetIcon(icon, "BlindRSS")

    def CreatePopupMenu(self):
        menu = wx.Menu()
        
        restore_item = menu.Append(wx.ID_ANY, "Restore")
        menu.AppendSeparator()
        
        refresh_item = menu.Append(wx.ID_ANY, "Refresh Feeds")
        menu.AppendSeparator()
        
        # Media Controls
        play_item = menu.Append(wx.ID_ANY, "Play")
        pause_item = menu.Append(wx.ID_ANY, "Pause")
        stop_item = menu.Append(wx.ID_ANY, "Stop")
        
        # Volume Submenu
        vol_menu = wx.Menu()
        for vol in [100, 80, 60, 40, 20, 5]:
            item = vol_menu.Append(wx.ID_ANY, f"{vol}%")
            self.Bind(wx.EVT_MENU, lambda e, v=vol: self.on_volume(v), item)
        menu.AppendSubMenu(vol_menu, "Volume")
        
        menu.AppendSeparator()
        exit_item = menu.Append(wx.ID_EXIT, "Exit")
        
        self.Bind(wx.EVT_MENU, self.on_restore, restore_item)
        self.Bind(wx.EVT_MENU, self.on_refresh, refresh_item)
        self.Bind(wx.EVT_MENU, self.on_play, play_item)
        self.Bind(wx.EVT_MENU, self.on_pause, pause_item)
        self.Bind(wx.EVT_MENU, self.on_stop, stop_item)
        self.Bind(wx.EVT_MENU, self.on_exit, exit_item)
        
        return menu

    def on_left_down(self, event):
        self.on_restore(event)

    def on_restore(self, event):
        if self.frame.IsIconized():
            self.frame.Iconize(False)
        if not self.frame.IsShown():
            self.frame.Show()
        self.frame.Raise()

    def on_refresh(self, event):
        self.frame.on_refresh_feeds(None)

    def on_play(self, event):
        if self.frame.player_window and self.frame.player_window.panel:
            self.frame.player_window.panel.on_play(None)

    def on_pause(self, event):
        if self.frame.player_window and self.frame.player_window.panel:
            self.frame.player_window.panel.on_pause(None)

    def on_stop(self, event):
        if self.frame.player_window and self.frame.player_window.panel:
            self.frame.player_window.panel.on_stop(None)

    def on_volume(self, vol):
        if self.frame.player_window and self.frame.player_window.panel:
            panel = self.frame.player_window.panel
            panel.volume_slider.SetValue(vol)
            panel.on_volume_change(None)

    def on_exit(self, event):
        self.RemoveIcon()
        self.frame.real_close()
