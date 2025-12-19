import sys
import os
import platform
import json

# Ensure we can import from core
sys.path.append(os.getcwd())

from core.discovery import is_ytdlp_supported, get_ytdlp_feed_url
import yt_dlp

test_urls = [
    "https://www.youtube.com/watch?v=kKE9OHPN09o",
    "https://soundcloud.com/bennicky/ben-nicky-2025-trance-mix?in=jesse-zomer/sets/w17-2025",
    "https://www.mixcloud.com/wilderkeks/a-state-of-trance-1248-special-ade-2025/",
    "https://rumble.com/c/ClownfishTV",
    "https://rumble.com/v1qyv1e-worlds-first-ever-commercial-hydrogen-flight.html",
    "https://www.twitch.tv/asot",
    "https://www.instagram.com/asotlive/"
]

print(f"Testing on {platform.system()}...")
print("-" * 60)

for url in test_urls:
    supported = is_ytdlp_supported(url)
    feed_url = get_ytdlp_feed_url(url)
    print(f"URL: {url}")
    print(f"  Recognized as supported: {supported}")
    if feed_url:
        print(f"  Converted to RSS Feed: {feed_url}")
    
    if supported:
        try:
            ydl_opts = {
                'format': 'bestaudio/best',
                'quiet': True,
                'no_warnings': True,
                'simulate': True,
                'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                title = info.get('title', 'Unknown')
                print(f"  Successfully extracted title: {title}")
                print(f"  Has media URL: {bool(info.get('url'))}")
                if info.get('url'):
                    print(f"  Media URL starts with: {info.get('url')[:50]}...")
        except Exception as e:
            print(f"  Extraction failed: {e}")
    else:
        print("  SKIPPING EXTRACTION (not recognized)")
    print("-" * 60)