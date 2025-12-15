import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from core import utils


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
                head = requests.head(candidate, headers=utils.HEADERS, timeout=5)
                if head.status_code == 200 and "xml" in head.headers.get("Content-Type", ""):
                    return candidate
            except Exception:
                pass
                
    except Exception:
        pass
        
    return None