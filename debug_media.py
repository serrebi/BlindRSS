import requests
from core import utils

url = "https://itunes.radiorecord.ru/tmp_audio/itunes1/rc_-_rc_2025-11-27.mp3"

print(f"Testing URL: {url}")

try:
    # Test with default headers (like python-requests/x.y.z)
    print("\n--- Test 1: Default User-Agent ---")
    r1 = requests.head(url, allow_redirects=True, timeout=10)
    print(f"Status: {r1.status_code}")
    print(f"Final URL: {r1.url}")
    print(f"Headers: {r1.headers}")

    # Test with Browser headers
    print("\n--- Test 2: Browser User-Agent ---")
    r2 = requests.head(url, headers=utils.HEADERS, allow_redirects=True, timeout=10)
    print(f"Status: {r2.status_code}")
    print(f"Final URL: {r2.url}")
    
except Exception as e:
    print(f"Error: {e}")
