#!/usr/bin/env python
"""Test URL encoding issue with apostrophe in media URLs."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.range_cache_proxy import get_range_cache_proxy

def test_apostrophe_url():
    """Test URL with encoded apostrophe (%27)."""
    # URL from the bug report - has %27 (encoded apostrophe)
    url = "https://onj.me/media/stroongecast/72_-_You%27ve_Changed.mp3"
    
    print(f"Testing URL: {url}")
    
    proxy = get_range_cache_proxy()
    proxy.start()
    
    proxy_url = proxy.proxify(url)
    print(f"Proxy URL: {proxy_url}")
    
    # Extract the session ID
    import re
    sid_match = re.search(r'id=([a-f0-9]+)', proxy_url)
    if sid_match:
        sid = sid_match.group(1)
        print(f"Session ID: {sid}")
        
        # Check entry
        with proxy._lock:
            ent = proxy._entries.get(sid)
        
        if ent:
            print(f"Entry URL: {ent.url}")
            print(f"Entry real_url: {ent.real_url}")
            
            # Try probing
            print("\nProbing...")
            ent.probe()
            print(f"Range supported: {ent.range_supported}")
            print(f"Total length: {ent.total_length}")
            print(f"Content type: {ent.content_type}")
        else:
            print("ERROR: No entry found!")
    
    # Now test actual GET via requests to the proxy
    import requests
    print(f"\nTesting GET via proxy...")
    try:
        # Just get first 1KB
        r = requests.get(proxy_url, headers={"Range": "bytes=0-1023"}, timeout=10)
        print(f"Status: {r.status_code}")
        print(f"Content-Length: {r.headers.get('Content-Length')}")
        print(f"Content-Range: {r.headers.get('Content-Range')}")
        print(f"Received bytes: {len(r.content)}")
    except Exception as e:
        print(f"ERROR: {e}")
    
    proxy.stop()
    print("\nTest complete.")


def test_vlc_playback():
    """Test actual VLC playback through proxy."""
    import vlc
    import time
    
    url = "https://onj.me/media/stroongecast/72_-_You%27ve_Changed.mp3"
    
    proxy = get_range_cache_proxy()
    proxy.start()
    
    proxy_url = proxy.proxify(url)
    print(f"\nTesting VLC playback through proxy: {proxy_url}")
    
    instance = vlc.Instance()
    player = instance.media_player_new()
    media = instance.media_new(proxy_url)
    player.set_media(media)
    
    print("Playing...")
    player.play()
    time.sleep(5)
    
    state = player.get_state()
    print(f"State after 5s: {state}")
    if state in (vlc.State.Error, vlc.State.Ended, vlc.State.NothingSpecial):
        print("ERROR: VLC failed to play via proxy")
    else:
        print("SUCCESS: VLC is playing via proxy")
        
    player.stop()
    proxy.stop()

if __name__ == "__main__":
    test_apostrophe_url()
    test_vlc_playback()
