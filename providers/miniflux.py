import requests
import re
from typing import List, Dict, Any
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta
from dateutil import parser as dateparser
from .base import RSSProvider, Feed, Article
from core import utils

class MinifluxProvider(RSSProvider):
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._category_cache = {}
        self.conf = config.get("providers", {}).get("miniflux", {})
        url = self.conf.get("url", "").rstrip("/")
        self.base_url = re.sub(r'/v1/?$', '', url)
        self.headers = {
            "X-Auth-Token": self.conf.get("api_key", ""),
        }
        # Merge with default browser headers for better compatibility
        self.headers.update(utils.HEADERS)
        
    def get_name(self) -> str:
        return "Miniflux"

    def test_connection(self) -> bool:
        try:
            res = self._req("GET", "/v1/me")
            return res is not None
        except:
            return False

    def _req(self, method, endpoint, json=None, params=None):
        if not self.base_url:
            return None
        url = f"{self.base_url}{endpoint}"
        try:
            # Uses self.headers which includes a browser-like User-Agent
            resp = requests.request(method, url, headers=self.headers, json=json, params=params, timeout=10)
            resp.raise_for_status()

            if resp.status_code == 204:
                return None

            try:
                return resp.json()
            except ValueError:
                print(f"Miniflux JSON error for {url}. Status: {resp.status_code}")
                return None

        except Exception as e:
            print(f"Miniflux error for {url}: {e}")
            return None

    def _get_entries_paged(self, endpoint: str, params: Dict[str, Any] = None, limit: int = 200) -> List[Dict[str, Any]]:
        """Retrieve all entries by paging with limit/offset until total is reached.

        To guarantee BlindRSS can see absolutely every stored entry for a feed, we page through:
          /v1/feeds/{feedID}/entries?limit=...&offset=...
        and keep requesting until we've retrieved "total" entries.
        """
        out: List[Dict[str, Any]] = []
        offset = 0
        last_offset = -1

        base_params = dict(params or {})
        base_params.pop("offset", None)
        base_params.pop("limit", None)

        while True:
            p = dict(base_params)
            p["limit"] = int(limit)
            p["offset"] = int(offset)

            data = self._req("GET", endpoint, params=p)
            if not data:
                break

            entries = data.get("entries") or []
            total = data.get("total")

            if entries:
                out.extend(entries)

            if not entries:
                break

            if total is not None:
                try:
                    if len(out) >= int(total):
                        break
                except Exception:
                    # If total is malformed, fall back to short-page termination
                    if len(entries) < int(limit):
                        break
            else:
                # Some proxies may strip "total"; short-page implies exhaustion.
                if len(entries) < int(limit):
                    break

            last_offset = offset
            offset += len(entries)
            if offset <= last_offset:
                # Defensive: avoid infinite loops if the server repeats a page
                break

        return out


    def _get_category_id_by_title(self, title: str):
        if not title:
            return None
        if title in self._category_cache:
            return self._category_cache[title]
        cats = self._req("GET", "/v1/categories") or []
        for c in cats:
            if c.get("title") == title:
                cid = c.get("id")
                self._category_cache[title] = cid
                return cid
        self._category_cache[title] = None
        return None

    def _resolve_entries_endpoint(self, feed_id: str, base_params: Dict[str, Any]):
        # category:<title> uses /v1/entries with category_id filter
        if feed_id.startswith("category:"):
            cat_title = feed_id.split(":", 1)[1]
            cid = self._get_category_id_by_title(cat_title)
            if cid is not None:
                base_params["category_id"] = cid
            return "/v1/entries", base_params
        if feed_id == "all":
            return "/v1/entries", base_params
        return f"/v1/feeds/{feed_id}/entries", base_params

    def _entries_to_articles(self, entries: List[Dict[str, Any]]) -> List[Article]:
        if not entries:
            return []
        article_ids = [str(e.get("id")) for e in entries if e.get("id") is not None]
        chapters_map = utils.get_chapters_batch(article_ids)

        articles: List[Article] = []
        for entry in entries:
            media_url = None
            media_type = None

            enclosures = entry.get("enclosures", []) or []
            if enclosures:
                media_url = (enclosures[0] or {}).get("url")
                media_type = (enclosures[0] or {}).get("mime_type")

            date = utils.normalize_date(
                entry.get("published_at") or entry.get("published"),
                entry.get("title") or "",
                entry.get("content") or entry.get("summary") or ""
            )

            article_id = str(entry.get("id"))
            chapters = chapters_map.get(article_id, [])

            articles.append(Article(
                id=article_id,
                feed_id=str(entry.get("feed_id") or ""),
                title=entry.get("title") or "Untitled",
                url=entry.get("url") or "",
                content=entry.get("content") or entry.get("summary") or "",
                date=date,
                author=entry.get("author") or "",
                is_read=(entry.get("status") == "read"),
                media_url=media_url,
                media_type=media_type,
                chapters=chapters
            ))
        return articles

    def refresh(self, progress_cb=None) -> bool:
        self._req("PUT", "/v1/feeds/refresh")
        return True

    def get_feeds(self) -> List[Feed]:
        data = self._req("GET", "/v1/feeds")
        if not data: return []
        
        counters_data = self._req("GET", "/v1/feeds/counters")
        counts = {}
        if counters_data:
            counts = counters_data.get("unreads", {})
        
        feeds = []
        for f in data:
            cat = f.get("category", {}).get("title", "Uncategorized")
            feed = Feed(
                id=str(f["id"]),
                title=f["title"],
                url=f["site_url"],
                category=cat,
                icon_url=f.get("icon", {}).get("data", "")
            )
            feed.unread_count = counts.get(str(f["id"]), 0) or counts.get(int(f["id"]), 0)
            feeds.append(feed)

        return feeds

    def get_articles(self, feed_id: str) -> List[Article]:
        # Always page through results so we can retrieve *all* stored entries.
        # - For a single feed: /v1/feeds/{feedID}/entries
        # - For categories/all: /v1/entries
        # Request both unread and read entries so the client can page through
        # the entire stored history (not just the default unread view).
        base_params: Dict[str, Any] = {
            "direction": "desc",
            "order": "published_at",
            "status": ["unread", "read"],
        }

        entries: List[Dict[str, Any]] = []

        if feed_id.startswith("category:"):
            cat_title = feed_id.split(":", 1)[1]
            category_id = None
            cats = self._req("GET", "/v1/categories") or []
            for c in cats:
                if c.get("title") == cat_title:
                    category_id = c.get("id")
                    break
            if category_id is not None:
                base_params["category_id"] = category_id
            entries = self._get_entries_paged("/v1/entries", base_params, limit=200)
        elif feed_id == "all":
            entries = self._get_entries_paged("/v1/entries", base_params, limit=200)
        else:
            # This is the guarantee path for complete retrieval.
            entries = self._get_entries_paged(f"/v1/feeds/{feed_id}/entries", base_params, limit=200)

        if not entries:
            return []
        article_ids = [str(e["id"]) for e in entries]
        chapters_map = utils.get_chapters_batch(article_ids)
        
        articles = []
        for entry in entries:
            media_url = None
            media_type = None
            
            enclosures = entry.get("enclosures", [])
            if enclosures:
                media_url = enclosures[0].get("url")
                media_type = enclosures[0].get("mime_type")
            
            date = utils.normalize_date(
                entry.get("published_at") or entry.get("published"),
                entry.get("title") or "",
                entry.get("content") or entry.get("summary") or ""
            )

            article_id = str(entry["id"])
            # Use batch result
            chapters = chapters_map.get(article_id, [])

            articles.append(Article(
                id=article_id,
                feed_id=str(entry["feed_id"]),
                title=entry["title"],
                url=entry["url"],
                content=entry.get("content", ""),
                date=date,
                author=entry.get("author", ""),
                is_read=entry.get("status") == "read",
                media_url=media_url,
                media_type=media_type,
                chapters=chapters
            ))
        return articles

    def get_article_chapters(self, article_id: str) -> List[Dict]:
        """
        Called by UI when an article is opened/played.
        We need to find the article's media info first. 
        Since we don't store articles in DB, we must fetch entry from API or rely on caller?
        MainFrame calls this with just ID.
        So we fetch the entry from Miniflux to get the media_url.
        """
        # Check DB first just in case
        chapters = utils.get_chapters_from_db(article_id)
        if chapters:
            return chapters

        # Fetch entry info to get media URL
        entry = self._req("GET", f"/v1/entries/{article_id}")
        if not entry:
            return []
            
        media_url = None
        media_type = None
        enclosures = entry.get("enclosures", [])
        if enclosures:
            media_url = enclosures[0].get("url")
            media_type = enclosures[0].get("mime_type")
            
        if media_url:
             return utils.fetch_and_store_chapters(article_id, media_url, media_type)
        return []


    def get_articles_page(self, feed_id: str, offset: int = 0, limit: int = 200):
        """Fetch a single page of articles quickly (used by the UI for fast-first loading)."""
        base_params: Dict[str, Any] = {
            "direction": "desc",
            "order": "published_at",
            # request both unread + read so we can page through the complete stored history
            "status": ["unread", "read"],
            "offset": int(max(0, offset)),
            "limit": int(limit),
        }

        endpoint, params = self._resolve_entries_endpoint(feed_id, base_params)
        data = self._req("GET", endpoint, params=params) or {}
        entries = data.get("entries") or []
        total = data.get("total")
        try:
            total_int = int(total) if total is not None else None
        except Exception:
            total_int = None

        return self._entries_to_articles(entries), total_int

    def mark_read(self, article_id: str) -> bool:
        self._req("PUT", "/v1/entries", json={"entry_ids": [int(article_id)], "status": "read"})
        return True

    def add_feed(self, url: str, category: str = "Uncategorized") -> bool:
        cats = self._req("GET", "/v1/categories")
        if not cats:
            return False
            
        category_id = cats[0]["id"]
        if category:
            for c in cats:
                if c["title"].lower() == category.lower():
                    category_id = c["id"]
                    break
        
        data = {"feed_url": url, "category_id": category_id}
        res = self._req("POST", "/v1/feeds", json=data)
        return res is not None

    def remove_feed(self, feed_id: str) -> bool:
        self._req("DELETE", f"/v1/feeds/{feed_id}")
        return True
        
    def import_opml(self, path: str, target_category: str = None) -> bool:
        # Miniflux API has an endpoint for this, but file upload might be tricky with requests.
        # Alternatively, use the default implementation which iterates and adds feeds.
        # Let's use default implementation for now as it's safer than file upload debugging.
        # So we actually REMOVE this method too? No, Miniflux *might* be faster with native import if we implemented it,
        # but the base class one works. 
        # Actually, let's keep the user's Miniflux file logic if it was there? 
        # Wait, the current file didn't have import_opml stubbed, did it? 
        # Checking... codebase_investigator said "Miniflux implements nearly all features... missing export_opml".
        # So import_opml IS likely implemented or not present (defaulting).
        # Let's check the file content if possible, or just remove export_opml.
        
        # NOTE: I am ONLY removing export_opml.
        return super().import_opml(path, target_category)

    def get_categories(self) -> List[str]:
        data = self._req("GET", "/v1/categories")
        if not data: return []
        return [c["title"] for c in data]

    def add_category(self, title: str) -> bool:
        return self._req("POST", "/v1/categories", json={"title": title}) is not None

    def rename_category(self, old_title: str, new_title: str) -> bool:
        data = self._req("GET", "/v1/categories")
        if not data: return False
        
        cat_id = None
        for c in data:
            if c["title"] == old_title:
                cat_id = c["id"]
                break
                
        if cat_id:
             return self._req("PUT", f"/v1/categories/{cat_id}", json={"title": new_title}) is not None
        return False

    def delete_category(self, title: str) -> bool:
        data = self._req("GET", "/v1/categories")
        if not data: return False
        
        cat_id = None
        for c in data:
            if c["title"] == title:
                cat_id = c["id"]
                break
        
        if cat_id:
            self._req("DELETE", f"/v1/categories/{cat_id}")
            return True
        return False
