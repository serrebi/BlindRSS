import sys
import multiprocessing

# Ensure dependencies are present before importing GUI
# Only runs when running from source (not frozen)
if not getattr(sys, 'frozen', False):
    try:
        from core.dependency_check import check_and_install_dependencies
        check_and_install_dependencies()
    except ImportError:
        pass 

import wx
from core.config import ConfigManager
from core.factory import get_provider
from gui.mainframe import MainFrame

class RSSApp(wx.App):
    def OnInit(self):
        self.config_manager = ConfigManager()
        self.provider = get_provider(self.config_manager)
        
        self.frame = MainFrame(self.provider, self.config_manager)
        self.frame.Show()
        return True

if __name__ == "__main__":
    multiprocessing.freeze_support()
    app = RSSApp()
    app.MainLoop()
