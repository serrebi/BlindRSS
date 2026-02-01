"""
Test to verify player loading doesn't block the GUI.

This test measures time spent in synchronous operations during media load.
The player should open immediately and perform network operations in background threads.
"""

import time
import threading
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_proxify_blocking():
    """Test that proxify() doesn't block for network operations."""
    from core.range_cache_proxy import get_range_cache_proxy
    
    # A URL that requires redirect resolution (simplecast uses op3.dev redirects)
    test_url = "https://op3.dev/e/injector.simplecastaudio.com/d838244c-2029-41b5-aa66-d28628ab36fa/episodes/fccd857a-7dd2-4517-9af9-fdb945de72d0/audio/128/default.mp3?aid=rss_feed&awCollectionId=d838244c-2029-41b5-aa66-d28628ab36fa&awEpisodeId=fccd857a-7dd2-4517-9af9-fdb945de72d0&feed=MhX_XZQZ"
    
    proxy = get_range_cache_proxy()
    
    # Measure time for proxify call
    start = time.perf_counter()
    proxied_url = proxy.proxify(test_url, headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    })
    elapsed = time.perf_counter() - start
    
    print(f"proxify() took {elapsed:.3f} seconds")
    print(f"Proxied URL: {proxied_url}")
    
    # Should complete in under 500ms (no network blocking)
    if elapsed > 0.5:
        print(f"FAIL: proxify() blocked for {elapsed:.3f}s - should be <0.5s")
        return False
    else:
        print(f"PASS: proxify() completed quickly ({elapsed:.3f}s)")
        return True


def test_maybe_range_cache_url_nonblocking():
    """Test that _maybe_range_cache_url doesn't block the GUI thread."""
    import wx
    
    # Initialize wx App for testing
    app = wx.App(False)
    
    # Import after wx.App exists
    from gui.player import PlayerFrame
    from core.config import ConfigManager
    
    # Create a minimal config manager
    config = ConfigManager()
    
    # Create player frame (hidden)
    frame = PlayerFrame(None, config)
    frame.Hide()
    
    test_url = "https://op3.dev/e/injector.simplecastaudio.com/d838244c-2029-41b5-aa66-d28628ab36fa/episodes/fccd857a-7dd2-4517-9af9-fdb945de72d0/audio/128/default.mp3"
    
    # Measure time for _maybe_range_cache_url
    start = time.perf_counter()
    result_url = frame._maybe_range_cache_url(test_url, headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    })
    elapsed = time.perf_counter() - start
    
    print(f"_maybe_range_cache_url() took {elapsed:.3f} seconds")
    print(f"Result URL: {result_url}")
    
    # Cleanup
    frame.Destroy()
    
    # Should complete in under 500ms
    if elapsed > 0.5:
        print(f"FAIL: _maybe_range_cache_url() blocked for {elapsed:.3f}s - should be <0.5s")
        return False
    else:
        print(f"PASS: _maybe_range_cache_url() completed quickly ({elapsed:.3f}s)")
        return True


if __name__ == "__main__":
    print("=" * 60)
    print("Testing player load blocking behavior")
    print("=" * 60)
    
    results = []
    
    print("\n1. Testing proxify() blocking...")
    results.append(("proxify blocking", test_proxify_blocking()))
    
    print("\n2. Testing _maybe_range_cache_url() blocking...")
    results.append(("_maybe_range_cache_url blocking", test_maybe_range_cache_url_nonblocking()))
    
    print("\n" + "=" * 60)
    print("Results:")
    for name, passed in results:
        status = "PASS" if passed else "FAIL"
        print(f"  {name}: {status}")
    
    all_passed = all(r[1] for r in results)
    sys.exit(0 if all_passed else 1)
