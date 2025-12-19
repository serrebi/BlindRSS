import sys
import os
import time

# Ensure we can import from core
sys.path.append(os.getcwd())

from core.audio_silence import scan_audio_for_silence
from core.discovery import is_ytdlp_supported
import yt_dlp

url = "https://www.youtube.com/watch?v=kKE9OHPN09o"

print(f"Testing Skip Silence logic for: {url}")
print("-" * 60)

if not is_ytdlp_supported(url):
    print("URL not supported by yt-dlp. Skipping.")
    sys.exit(1)

try:
    print("Resolving media URL via yt-dlp...")
    ydl_opts = {
        'format': 'bestaudio/best',
        'quiet': True,
        'no_warnings': True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        media_url = info['url']
        headers = info.get('http_headers', {})
        print(f"  Title: {info.get('title')}")
        print("  Media URL resolved successfully.")

    print("\nStarting silence scan (this may take a few moments)...")
    start_time = time.time()
    
    # We pass the resolved media URL to scan_audio_for_silence
    # Note: ffmpeg will use the same path detection logic we refined.
    ranges = scan_audio_for_silence(media_url)
    
    duration = time.time() - start_time
    print(f"\nScan completed in {duration:.2f} seconds.")
    print(f"Found {len(ranges)} silent ranges.")
    
    if ranges:
        print("\nDetected Ranges (ms):")
        for i, (s, e) in enumerate(ranges[:10]): # Show first 10
            print(f"  {i+1}: {s} - {e} (Duration: {e-s}ms)")
        if len(ranges) > 10:
            print(f"  ... and {len(ranges)-10} more.")
    else:
        print("No silence detected (or file is very short/loud).")

except Exception as e:
    print(f"\nFATAL ERROR during test: {e}")
    import traceback
    traceback.print_exc()
