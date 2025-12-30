import os
import subprocess
import json
import platform
from functools import lru_cache
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse, parse_qs
from core import utils


@lru_cache(maxsize=2048)
def is_ytdlp_supported(url: str) -> bool:
    """Return True only when yt-dlp has a non-generic extractor for this URL.

    IMPORTANT:
    We intentionally use yt-dlp's URL-pattern matching (no network) rather than a
    "does extraction succeed" check. Many normal article pages contain embedded
    players (HTML5 audio/video, YouTube iframes, etc.) and yt-dlp can often
    extract *something* from them, which would incorrectly classify articles as
    playable media.
    """
    if not url:
        return False

    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme and scheme not in ("http", "https", "lbry"):
        return False

    domain = (parsed.netloc or "").lower()
    if scheme in ("http", "https") and not domain:
        return False

    # Fast allowlist for common media domains (keeps UI snappy).
    known_domains = [
        "youtube.com", "youtu.be", "vimeo.com", "twitch.tv", "dailymotion.com",
        "soundcloud.com", "facebook.com", "twitter.com", "x.com", "tiktok.com",
        "instagram.com", "rumble.com", "bilibili.com", "mixcloud.com",
        "odysee.com", "lbry.tv",
    ]
    if any(kd in domain for kd in known_domains):
        return True

    # Use yt-dlp's extractor regexes (offline) and ignore Generic.
    try:
        from yt_dlp.extractor import gen_extractor_classes

        for extractor_cls in gen_extractor_classes():
            try:
                if not extractor_cls.suitable(url):
                    continue
                if extractor_cls.ie_key() == "Generic":
                    continue
                return True
            except Exception:
                continue
    except Exception:
        return False

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

    # 2. Rumble note:
    # Rumble previously exposed /feeds/rss/... endpoints, but these are unreliable
    # (often 404/410). BlindRSS supports Rumble via HTML listing parsing + a
    # custom media resolver, so we intentionally do NOT return an RSS URL here.
            
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
