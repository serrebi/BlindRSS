import sys
import multiprocessing

if __name__ == "__main__":
    multiprocessing.freeze_support()

    # Ensure dependencies/media tools are present (even when frozen)
    try:
        from core.dependency_check import check_and_install_dependencies
        check_and_install_dependencies()
    except Exception:
        pass 

    import wx
    from core.config import ConfigManager
    from core.factory import get_provider
    from gui.mainframe import MainFrame

    class GlobalMediaKeyFilter(wx.EventFilter):
        """Capture media shortcuts globally so they work in dialogs too."""

        def __init__(self, frame: MainFrame):
            super().__init__()
            self.frame = frame

        def FilterEvent(self, event):
            try:
                if isinstance(event, wx.KeyEvent) and event.ControlDown():
                    key = event.GetKeyCode()

                    # Ctrl+P: toggle player window
                    if key in (ord('P'), ord('p')):
                        try:
                            self.frame.toggle_player_visibility()
                        except Exception:
                            pass
                        return wx.EventFilter.Event_Processed

                    pw = getattr(self.frame, "player_window", None)
                    if pw:
                        # Volume keys should work even before a track is loaded.
                        if key == wx.WXK_UP:
                            pw.adjust_volume(int(getattr(pw, "volume_step", 5)))
                            return wx.EventFilter.Event_Processed
                        if key == wx.WXK_DOWN:
                            pw.adjust_volume(-int(getattr(pw, "volume_step", 5)))
                            return wx.EventFilter.Event_Processed

                        # Seek only makes sense when media is loaded.
                        if getattr(pw, "has_media_loaded", lambda: False)():
                            if key == wx.WXK_LEFT:
                                pw.seek_relative_ms(-int(getattr(pw, "seek_back_ms", 10000)))
                                return wx.EventFilter.Event_Processed
                            if key == wx.WXK_RIGHT:
                                pw.seek_relative_ms(int(getattr(pw, "seek_forward_ms", 30000)))
                                return wx.EventFilter.Event_Processed
            except Exception:
                pass
            return wx.EventFilter.Event_Skip

    class RSSApp(wx.App):
        def OnInit(self):
            self.config_manager = ConfigManager()
            self.provider = get_provider(self.config_manager)
            
            self.frame = MainFrame(self.provider, self.config_manager)
            self.frame.Show()

            # Install a global filter so media shortcuts work everywhere (including modal dialogs)
            try:
                # Keep a reference so it is not garbage-collected.
                self._media_filter = GlobalMediaKeyFilter(self.frame)
                wx.EvtHandler.AddFilter(self._media_filter)
            except Exception:
                pass
            return True

    app = RSSApp()
    app.MainLoop()