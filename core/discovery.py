import os
import requests
import subprocess
import json
import platform
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse, parse_qs
from core import utils


def is_ytdlp_supported(url: str) -> bool:
    """Check if yt-dlp supports this URL without doing a full extract."""
    if not url:
        return False
    
    # Quick check for known domains to avoid spawning process for every character
    parsed = urlparse(url)
    domain = parsed.netloc.lower()
    if not domain:
        return False
        
    known_domains = [
        "youtube.com", "youtu.be", "vimeo.com", "twitch.tv", "dailymotion.com",
        "soundcloud.com", "facebook.com", "twitter.com", "x.com", "tiktok.com",
        "instagram.com", "rumble.com", "bilibili.com", "mixcloud.com"
    ]
    
    if any(kd in domain for kd in known_domains):
        return True

    # Fallback to asking yt-dlp (throttled/debounced by caller)
    try:
        from core.dependency_check import _get_startup_info
        creationflags = 0
        if platform.system().lower() == "windows":
            creationflags = 0x08000000
            
        # --simulate ensures no download, --get-id is a fast way to verify support
        cmd = ["yt-dlp", "--simulate", "--get-id", "--quiet", "--no-warnings", url]
        res = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
            startupinfo=_get_startup_info(),
            timeout=10
        )
        return res.returncode == 0
    except:
        return False


def is_rumble_url(url: str) -> bool:
    if not url:
        return False
    try:
        domain = urlparse(url).netloc.lower()
    except Exception:
        return False
    return "rumble.com" in domain


def _build_cookie_sources() -> list[tuple]:
    sources: list[tuple] = []

    def _add(browser: str, profile: str | None = None) -> None:
        tup = (browser,) if profile is None else (browser, profile)
        if tup not in sources:
            sources.append(tup)

    if platform.system().lower() == "windows":
        local = os.environ.get("LOCALAPPDATA", "")
        chromium_root = os.path.join(local, "Chromium") if local else ""
        chromium_user_data = os.path.join(chromium_root, "User Data") if chromium_root else ""
        if chromium_user_data and os.path.isdir(chromium_user_data):
            _add("chromium", chromium_user_data)
        elif chromium_root and os.path.isdir(chromium_root):
            _add("chromium", chromium_root)

        browser_dirs = [
            ("edge", os.path.join(local, "Microsoft", "Edge", "User Data")),
            ("brave", os.path.join(local, "BraveSoftware", "Brave-Browser", "User Data")),
            ("chrome", os.path.join(local, "Google", "Chrome", "User Data")),
        ]
        for name, path in browser_dirs:
            if path and os.path.isdir(path):
                _add(name)

    if not sources:
        for name in ("chromium", "edge", "brave", "chrome"):
            _add(name)

    return sources


def get_rumble_cookie_sources(url: str) -> list[tuple]:
    """Return cookiesfrombrowser candidates for rumble URLs."""
    if not is_rumble_url(url):
        return []
    return _build_cookie_sources()


def get_ytdlp_cookie_sources(url: str | None = None) -> list[tuple]:
    """Return cookiesfrombrowser candidates for yt-dlp extraction."""
    return _build_cookie_sources()


def get_ytdlp_feed_url(url: str) -> str:
    """Try to get a native RSS feed for a yt-dlp supported URL (e.g. YouTube)."""
    if not url:
        return None
        
    parsed = urlparse(url)
    domain = parsed.netloc.lower()
    
    # 1. YouTube specific logic (fastest)
    if "youtube.com" in domain or "youtu.be" in domain:
        # Check for channel_id or user in URL
        if "/channel/" in url:
            channel_id = url.split("/channel/")[1].split("/")[0].split("?")[0]
            return f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
        if "/user/" in url:
            user = url.split("/user/")[1].split("/")[0].split("?")[0]
            return f"https://www.youtube.com/feeds/videos.xml?user={user}"
        if "/playlist?list=" in url:
            qs = parse_qs(parsed.query)
            playlist_id = qs.get("list", [None])[0]
            if playlist_id:
                return f"https://www.youtube.com/feeds/videos.xml?playlist_id={playlist_id}"
        
        # Use yt-dlp to find channel ID for custom URLs
        try:
            from core.dependency_check import _get_startup_info
            creationflags = 0
            if platform.system().lower() == "windows":
                creationflags = 0x08000000
                
            # extract_flat gives us channel info without downloading every video info
            cmd = ["yt-dlp", "--dump-json", "--playlist-items", "0", url]
            res = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                creationflags=creationflags,
                startupinfo=_get_startup_info(),
                timeout=10
            )
            if res.returncode == 0 and res.stdout:
                data = json.loads(res.stdout)
                channel_id = data.get("channel_id") or data.get("id")
                if channel_id and data.get("_type") in ("playlist", "channel"):
                    return f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
        except:
            pass

    # 2. Rumble specific logic
    if "rumble.com" in domain:
        # Rumble RSS: https://rumble.com/feeds/rss/channel/ClownfishTV.xml
        # Paths: /c/NAME, /user/NAME, /channel/NAME
        path_parts = parsed.path.strip("/").split("/")
        if len(path_parts) >= 2:
            kind = path_parts[0].lower()
            name = path_parts[1]
            if kind in ("c", "channel"):
                return f"https://rumble.com/feeds/rss/channel/{name}.xml"
            if kind == "user":
                return f"https://rumble.com/feeds/rss/user/{name}.xml"
            
    return None


def discover_feed(url: str) -> str:
    """
    Given a URL, try to find the RSS/Atom feed URL.
    Returns None if not found.
    """
    if not url:
        return None
    
    # If it looks like a feed already
    if url.endswith(".xml") or url.endswith(".rss") or url.endswith(".atom") or "feed" in url:
        return url
        
    try:
        resp = utils.safe_requests_get(url, timeout=10)
        resp.raise_for_status()
        
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        # 1. <link rel="alternate" type="application/rss+xml" href="...">
        links = soup.find_all("link", rel="alternate")
        for link in links:
            if link.get("type") in ["application/rss+xml", "application/atom+xml", "text/xml"]:
                href = link.get("href")
                if href:
                    return urljoin(url, href)
                    
        # 2. Check for common patterns if no link tag
        # e.g. /feed, /rss, /atom.xml
        # This is a bit brute force but helpful
        common_paths = ["/feed", "/rss", "/rss.xml", "/atom.xml", "/feed.xml"]
        base = url.rstrip("/")
        for path in common_paths:
            # Avoid re-checking
            candidate = base + path
            try:
                head = utils.safe_requests_head(candidate, timeout=5, allow_redirects=True)
                if head.status_code == 200 and "xml" in head.headers.get("Content-Type", ""):
                    return candidate
            except Exception:
                pass
                
    except Exception:
        pass
        
    return None
