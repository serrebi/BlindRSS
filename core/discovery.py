import os
import subprocess
import json
import platform
import re
from functools import lru_cache
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse, parse_qs
from core import utils


_ARTICLE_DATE_PATH_RE = re.compile(r"/\d{4}/\d{2}/\d{2}/")
_ARTICLE_PATH_HINTS = (
    "/news/",
    "/article",
    "/story/",
)
_MEDIA_PATH_HINTS = (
    "/video/",
    "/videos/",
    "/watch",
    "/clip",
    "/player",
    "/av/",
    "/reel/",
    "/embed",
    "/podcast",
    "/audio",
    "/episode",
    "/track",
)

# Extractors whose URL patterns are too broad to treat as "playable media" by
# default. For these, require explicit media-ish URL hints (see _MEDIA_PATH_HINTS)
# to avoid classifying arbitrary articles as playable.
_EXTRACTORS_REQUIRE_MEDIA_HINTS = {
    "VoxMedia",  # Matches most pages on theverge.com/vox.com/etc, not just media
}


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

    path_low = (parsed.path or "").lower()
    # Heuristic: don't treat obvious article/news URLs as playable media just
    # because yt-dlp has a dedicated extractor for the publisher site.
    # (e.g., NYTimesArticle, CNN, BBC can match standard articles).
    looks_like_media = any(hint in path_low for hint in _MEDIA_PATH_HINTS)
    if not looks_like_media:
        if _ARTICLE_DATE_PATH_RE.search(path_low) or any(hint in path_low for hint in _ARTICLE_PATH_HINTS):
            return False

    # Use yt-dlp's extractor regexes (offline) and ignore Generic.
    try:
        from yt_dlp.extractor import gen_extractor_classes

        for extractor_cls in gen_extractor_classes():
            try:
                if not extractor_cls.suitable(url):
                    continue
                key = extractor_cls.ie_key()
                if key == "Generic":
                    continue
                # Many publisher sites have dedicated "...Article" extractors,
                # which are not a good signal that a URL is a playable media page.
                if str(key).lower().endswith("article"):
                    continue
                # Some extractors (e.g. VoxMedia) match most publisher pages, so
                # only treat them as supported when the URL itself looks like a
                # media page.
                if key in _EXTRACTORS_REQUIRE_MEDIA_HINTS and not looks_like_media:
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
        if "/@" in url:
            # Handle @handle URLs by using yt-dlp to get the channel ID
            pass
        
        # Use yt-dlp to find channel ID for custom URLs
        try:
            from core.dependency_check import _get_startup_info
            creationflags = 0
            if platform.system().lower() == "windows":
                creationflags = 0x08000000
                
            # extract_flat gives us channel info without downloading every video info
            # Use cookies to avoid "Sign in to confirm you’re not a bot" errors
            cmd = ["yt-dlp", "--dump-json", "--playlist-items", "0", url]
            
            # Add cookies if available
            cookies = get_ytdlp_cookie_sources(url)
            if cookies:
                # Use the first available source
                browser = cookies[0][0]
                cmd.extend(["--cookies-from-browser", browser])
                if len(cookies[0]) > 1:
                    cmd.append(cookies[0][1]) # profile

            res = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                creationflags=creationflags,
                startupinfo=_get_startup_info(),
                timeout=15 # Increased timeout for cookie processing
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


def discover_feeds(url: str) -> list[str]:
    """Return a list of discovered RSS/Atom/JSON feeds for a webpage/site URL.

    This is a more general form of `discover_feed()` intended for UI helpers
    (e.g. "Find a podcast or RSS feed"). It tries to enumerate multiple
    candidates rather than returning the first match.
    """
    if not url:
        return []

    # If it already looks like a feed, return it as-is.
    low = str(url).lower()
    if low.endswith(".xml") or low.endswith(".rss") or low.endswith(".atom") or "feed" in low:
        return [url]

    feeds: list[str] = []

    def _add(candidate: str) -> None:
        if not candidate:
            return
        if candidate not in feeds:
            feeds.append(candidate)

    try:
        resp = utils.safe_requests_get(url, timeout=10)
        resp.raise_for_status()
        html = resp.text or ""

        soup = BeautifulSoup(html, "html.parser")

        # 1) <link rel="alternate" ...>
        for link in soup.find_all("link", href=True):
            try:
                rel = link.get("rel")
                rel_vals: list[str] = []
                if isinstance(rel, str):
                    rel_vals = [rel]
                elif isinstance(rel, list):
                    rel_vals = [str(r) for r in rel]
                rel_vals = [r.lower().strip() for r in rel_vals if r]
                if "alternate" not in rel_vals:
                    continue

                ctype = (link.get("type") or "").lower().strip()
                if ctype not in (
                    "application/rss+xml",
                    "application/atom+xml",
                    "application/xml",
                    "text/xml",
                    "application/feed+json",
                    "application/json",
                ):
                    continue

                href = link.get("href")
                if href:
                    _add(urljoin(url, href))
            except Exception:
                continue

        # 2) Obvious <a href> candidates (best-effort)
        for a in soup.find_all("a", href=True):
            try:
                href = a.get("href")
                if not isinstance(href, str) or not href:
                    continue
                h = href.lower()
                if any(h.endswith(ext) for ext in (".rss", ".atom", ".xml", ".json")) or "/feed" in h or "rss" in h:
                    _add(urljoin(url, href))
            except Exception:
                continue

        # 3) Common paths (HEAD check)
        common_paths = ["/feed", "/rss", "/rss.xml", "/atom.xml", "/feed.xml", "/index.xml"]
        base = url.rstrip("/")
        for path in common_paths:
            candidate = base + path
            try:
                head = utils.safe_requests_head(candidate, timeout=5, allow_redirects=True)
                if head.status_code == 200:
                    ct = (head.headers.get("Content-Type", "") or "").lower()
                    if any(x in ct for x in ("xml", "rss", "atom", "json")):
                        _add(candidate)
            except Exception:
                continue

    except Exception:
        pass

    # Normalize/uniq while preserving order.
    out: list[str] = []
    seen: set[str] = set()
    for f in feeds:
        try:
            fu = str(f).strip()
        except Exception:
            continue
        if not fu or fu in seen:
            continue
        seen.add(fu)
        out.append(fu)
    return out

def detect_media(url: str, timeout: int = 20) -> tuple[str | None, str | None]:
    """
    Attempt to detect media (audio/video) for a given URL using yt-dlp and other heuristics.
    Returns (media_url, media_type) or (None, None).
    """
    if not url:
        return None, None

    # 1. NPR specific
    if "npr.org" in url:
        from core import npr
        murl, mtype = npr.extract_npr_audio(url, timeout_s=float(timeout))
        if murl:
            return murl, mtype

    # 2. yt-dlp (with cookies)
    try:
        from core.dependency_check import _get_startup_info
        creationflags = 0
        if platform.system().lower() == "windows":
            creationflags = 0x08000000

        cmd = ["yt-dlp", "--dump-json", "--no-playlist", url]
        
        # Add cookies if available
        cookies = get_ytdlp_cookie_sources(url)
        if cookies:
            browser = cookies[0][0]
            cmd.extend(["--cookies-from-browser", browser])
            if len(cookies[0]) > 1:
                cmd.append(cookies[0][1])

        res = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
            startupinfo=_get_startup_info(),
            timeout=timeout
        )
        
        if res.returncode == 0 and res.stdout:
            data = json.loads(res.stdout)
            media_url = data.get("url")
            if media_url:
                # Determine type
                ext = data.get("ext", "")
                if ext == "mp3": mtype = "audio/mpeg"
                elif ext == "m4a": mtype = "audio/mp4"
                elif ext == "mp4": mtype = "video/mp4"
                else: mtype = "application/octet-stream" # Generic
                
                # Check if it's strictly video but we prefer audio? 
                # For now just return what we found.
                return media_url, mtype
    except Exception:
        pass
        
    return None, None