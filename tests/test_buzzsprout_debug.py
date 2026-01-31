"""
Debug test for Buzzsprout feed display issue.
Runs the actual GUI and checks if articles load correctly.
"""

import os
import sys
import time
import threading
import sqlite3

# CRITICAL: Set sys.argv[0] to main.py BEFORE importing core modules
# This ensures APP_DIR is calculated correctly
sys.argv[0] = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py")

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import wx
from core import db
from core.config import ConfigManager
from providers.local import LocalProvider


class DebugFrame(wx.Frame):
    """Minimal frame to test article loading."""
    
    def __init__(self, provider, feed_id, feed_title):
        super().__init__(None, title=f"Debug: {feed_title}", size=(800, 600))
        
        self.provider = provider
        self.feed_id = feed_id
        self.results = {}
        
        # Create list control
        self.list_ctrl = wx.ListCtrl(self, style=wx.LC_REPORT)
        self.list_ctrl.InsertColumn(0, "Title", width=400)
        self.list_ctrl.InsertColumn(1, "Date", width=150)
        self.list_ctrl.InsertColumn(2, "Status", width=100)
        
        # Status bar
        self.status = wx.StatusBar(self)
        self.SetStatusBar(self.status)
        self.status.SetStatusText("Loading articles...")
        
        # Layout
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(self.list_ctrl, 1, wx.EXPAND | wx.ALL, 5)
        self.SetSizer(sizer)
        
        # Start loading in background (like the real app does)
        self.load_thread = threading.Thread(target=self._load_articles_thread, daemon=True)
        self.load_thread.start()
        
        # Timer to check completion
        self.check_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self.on_check_complete, self.check_timer)
        self.check_timer.Start(500)
        
        self.Show()
    
    def _load_articles_thread(self):
        """Load articles like the real MainFrame does."""
        try:
            print(f"[Thread] Loading articles for feed_id: {self.feed_id}")
            
            page, total = self.provider.get_articles_page(self.feed_id, offset=0, limit=200)
            
            print(f"[Thread] Got {len(page)} articles, total={total}")
            
            self.results['page'] = page
            self.results['total'] = total
            self.results['error'] = None
            
            # Call UI update on main thread
            wx.CallAfter(self._populate_articles, page, total)
            
        except Exception as e:
            print(f"[Thread] ERROR: {e}")
            import traceback
            traceback.print_exc()
            self.results['error'] = str(e)
            wx.CallAfter(self._show_error, str(e))
    
    def _populate_articles(self, articles, total):
        """Populate the list control with articles."""
        print(f"[UI] Populating {len(articles)} articles")
        
        self.list_ctrl.DeleteAllItems()
        
        if not articles:
            self.list_ctrl.InsertItem(0, "No articles found.")
            self.status.SetStatusText(f"No articles found (total={total})")
            return
        
        for i, article in enumerate(articles):
            idx = self.list_ctrl.InsertItem(i, article.title or "(no title)")
            self.list_ctrl.SetItem(idx, 1, article.date or "")
            self.list_ctrl.SetItem(idx, 2, "Read" if article.is_read else "Unread")
        
        self.status.SetStatusText(f"Loaded {len(articles)} articles (total={total})")
        print(f"[UI] Done populating")
    
    def _show_error(self, error):
        self.list_ctrl.DeleteAllItems()
        self.list_ctrl.InsertItem(0, f"ERROR: {error}")
        self.status.SetStatusText(f"Error: {error}")
    
    def on_check_complete(self, event):
        """Check if loading is complete and close."""
        if 'page' in self.results or 'error' in self.results:
            self.check_timer.Stop()
            
            # Keep window open for 3 seconds so we can see the result
            wx.CallLater(3000, self.Close)


def main():
    print("=" * 60)
    print("DEBUG: Buzzsprout feed article loading")
    print("=" * 60)
    
    # Initialize
    print(f"CWD: {os.getcwd()}")
    print(f"DB module DB_FILE: {db.DB_FILE}")
    
    db.init_db()
    
    print(f"DB module DB_FILE after init: {db.DB_FILE}")
    config = ConfigManager()
    provider = LocalProvider(config)
    
    # Get feed ID from database
    conn = sqlite3.connect('rss.db')
    c = conn.cursor()
    c.execute('SELECT id, title FROM feeds WHERE url LIKE ? OR title LIKE ?', 
              ('%buzzsprout%', '%disability%'))
    row = c.fetchone()
    conn.close()
    
    if not row:
        print("ERROR: Buzzsprout feed not found in database!")
        return 1
    
    feed_id, feed_title = row
    print(f"Feed ID: {feed_id}")
    print(f"Feed Title: {feed_title}")
    
    # First, verify directly with provider
    print("\nDirect provider test:")
    page, total = provider.get_articles_page(feed_id, offset=0, limit=200)
    print(f"  get_articles_page returned: {len(page)} articles, total={total}")
    
    if not page:
        print("  ERROR: Provider returned no articles!")
        return 1
    
    print(f"  First article: {page[0].title[:50]}...")
    
    # Now test with GUI
    print("\nStarting GUI test...")
    app = wx.App()
    frame = DebugFrame(provider, feed_id, feed_title)
    
    # Run for a few seconds
    app.MainLoop()
    
    # Check results
    print("\nResults:")
    if frame.results.get('error'):
        print(f"  ERROR: {frame.results['error']}")
        return 1
    
    if 'page' in frame.results:
        print(f"  Articles loaded: {len(frame.results['page'])}")
        print(f"  Total: {frame.results['total']}")
        
        if len(frame.results['page']) == 0:
            print("  FAIL: GUI showed no articles!")
            return 1
        else:
            print("  SUCCESS: Articles loaded correctly in GUI!")
            return 0
    
    print("  Unknown state")
    return 1


if __name__ == "__main__":
    sys.exit(main())
