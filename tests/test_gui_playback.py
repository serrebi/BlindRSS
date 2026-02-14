#!/usr/bin/env python
"""End-to-end GUI playback test for URL with apostrophe."""

import sys
import os
import time
import threading
import logging

# Enable debug logging
logging.basicConfig(level=logging.DEBUG, format='%(name)s - %(levelname)s - %(message)s')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Initialize the database first
from core import db
db.init_db()

# Must import wx before anything else that might use it
import wx

def test_playback():
    """Test playback through the actual player window."""
    
    # Initialize wx app
    app = wx.App(False)
    
    # Import after wx.App is created
    from gui.player import PlayerFrame
    from core.config import ConfigManager
    
    # Create a minimal config manager
    config = ConfigManager()
    
    url = "https://onj.me/media/stroongecast/72_-_You%27ve_Changed.mp3"
    print(f"Testing URL: {url}")
    
    # Create player window
    print("Creating player window...")
    player = PlayerFrame(None, config_manager=config)
    player.Show()
    
    # Track state
    results = {"started": False, "playing": False, "error": None, "states": []}
    
    def check_state():
        """Check player state periodically."""
        try:
            if player.player:
                import vlc
                state = player.player.get_state()
                pos = player.player.get_time()
                results["states"].append((time.time(), str(state), pos))
                print(f"  State: {state}, Position: {pos}ms")
                
                # Also check internal state
                last_vlc_url = getattr(player, '_last_vlc_url', None)
                print(f"  _last_vlc_url: {last_vlc_url[:80] if last_vlc_url else 'None'}")
                
                if state == vlc.State.Playing:
                    results["playing"] = True
                elif state == vlc.State.Error:
                    results["error"] = "VLC Error state"
        except Exception as e:
            results["error"] = str(e)
            print(f"  Error checking state: {e}")
    
    # Load media
    print("Loading media...")
    try:
        player.load_media(url, use_ytdlp=False, title="Test Episode 72")
        results["started"] = True
        print("load_media returned")
    except Exception as e:
        import traceback
        traceback.print_exc()
        results["error"] = f"load_media failed: {e}"
        print(f"ERROR: {e}")
    
    # Use wx.App.MainLoop with a timer to process events properly
    class TimeoutFrame(wx.Frame):
        def __init__(self, app, player, results, max_seconds=20):
            super().__init__(None, title="Test Timer", size=(1, 1))
            self.app = app
            self.player = player
            self.results = results
            self.start_time = time.time()
            self.max_seconds = max_seconds
            self.check_count = 0
            
            self.timer = wx.Timer(self)
            self.Bind(wx.EVT_TIMER, self.on_timer, self.timer)
            self.timer.Start(500)  # Check every 500ms
            
        def on_timer(self, event):
            self.check_count += 1
            elapsed = time.time() - self.start_time
            print(f"\nCheck #{self.check_count} at {elapsed:.1f}s:")
            
            check_state()
            
            if self.results["playing"]:
                print("\nSUCCESS: Audio is playing!")
                self.finish()
            elif self.results["error"]:
                print(f"\nERROR: {self.results['error']}")
                self.finish()
            elif elapsed > self.max_seconds:
                print("\nTIMEOUT: Max time exceeded")
                self.finish()
                
        def finish(self):
            self.timer.Stop()
            # Cleanup
            print("\nCleaning up...")
            try:
                self.player.stop()
            except Exception:
                pass
            try:
                self.player.Close()
                self.player.Destroy()
            except Exception:
                pass
            self.Close()
            self.app.ExitMainLoop()
    
    timeout_frame = TimeoutFrame(app, player, results)
    timeout_frame.Show(False)
    
    # Run the event loop
    app.MainLoop()
    
    # Print summary
    print("\n=== SUMMARY ===")
    print(f"Started: {results['started']}")
    print(f"Playing: {results['playing']}")
    print(f"Error: {results['error']}")
    print(f"State history: {len(results['states'])} entries")
    for ts, state, pos in results["states"][-5:]:
        print(f"  {state} @ {pos}ms")
    
    assert results["started"], "Expected load_media to run without throwing"
    assert results["error"] is None, f"Unexpected playback error: {results['error']}"
    assert results["playing"], "Expected VLC to enter Playing state within timeout"


if __name__ == "__main__":
    try:
        test_playback()
        sys.exit(0)
    except AssertionError as e:
        print(f"FAIL: {e}")
        sys.exit(1)
