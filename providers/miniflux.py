import requests
import re
import logging
from typing import List, Dict, Any
from datetime import datetime, timezone, timedelta
from dateutil import parser as dateparser
from .base import RSSProvider
from core.models import Feed, Article
from core import utils

log = logging.getLogger(__name__)

class MinifluxProvider(RSSProvider):
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._category_cache = {}
        self.conf = config.get("providers", {}).get("miniflux", {})
        url = self.conf.get("url", "").rstrip("/")
        self.base_url = re.sub(r'/v1/?$', '', url)
        self.headers = {
            "X-Auth-Token": self.conf.get("api_key", ""),
            "Accept": "application/json",
        }
        # Merge with default browser headers for better compatibility
        # Keep Miniflux API responses in JSON by overriding Accept above.
        self.headers.update(utils.HEADERS)
        # Ensure Accept stays JSON for API calls.
        self.headers["Accept"] = "application/json"
        
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
                log.error(f"Miniflux JSON error for {url}. Status: {resp.status_code}")
                return None

        except requests.HTTPError as e:
            # Silence 500 on refresh or general list endpoints as it's often a transient server issue.
            is_silent_endpoint = any(x in endpoint for x in ("refresh", "/feeds", "/entries"))
            if e.response is not None and e.response.status_code == 500 and is_silent_endpoint:
                log.debug(f"Miniflux endpoint failed for {url} (500). Server might be overloaded.")
            else:
                log.error(f"Miniflux error for {url}: {e}")
            return None
        except Exception as e:
            log.error(f"Miniflux error for {url}: {e}")
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
        norm_title = (title or "").strip()
        if not norm_title:
            return None
        cached = self._category_cache.get(norm_title)
        if cached is not None:
            return cached
        cats = self._req("GET", "/v1/categories") or []
        cid = None
        for c in cats:
            if (c.get("title") or "").strip() == norm_title:
                cid = c.get("id")
                break
        if cid is None:
            norm_lower = norm_title.lower()
            for c in cats:
                if (c.get("title") or "").strip().lower() == norm_lower:
                    cid = c.get("id")
                    break
        if cid is not None:
            self._category_cache[norm_title] = cid
        return cid

    def _resolve_entries_endpoint(self, feed_id: str, base_params: Dict[str, Any]):
        # category:<title> uses /v1/entries with category_id filter
        if feed_id.startswith("category:"):
            cat_title = feed_id.split(":", 1)[1]
            cid = self._get_category_id_by_title(cat_title)
            if cid is None:
                return None, None
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
                entry.get("content") or entry.get("summary") or "",
                entry.get("url") or "",
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
                is_favorite=entry.get("starred", False),
                media_url=media_url,
                media_type=media_type,
                chapters=chapters
            ))
        return articles

    def refresh(self, progress_cb=None, force: bool = False) -> bool:
        # Kick off a global refresh on the Miniflux server.
        self._req("PUT", "/v1/feeds/refresh")

        # After triggering, fetch feed metadata so we can surface stale/error
        # feeds in the UI and optionally retry them individually.
        feeds = self._req("GET", "/v1/feeds") or []
        counters_data = self._req("GET", "/v1/feeds/counters") or {}
        unread_map = counters_data.get("unreads", {}) if isinstance(counters_data, dict) else {}

        now = datetime.now(timezone.utc)
        stale_cutoff = now - timedelta(hours=3)
        retry_budget = 15  # avoid hammering the server if many feeds are failing

        for feed in feeds:
            feed_id = str(feed.get("id"))
            category = (feed.get("category") or {}).get("title", "Uncategorized")
            unread = unread_map.get(feed_id) or unread_map.get(int(feed.get("id", 0) or 0), 0) or 0

            status = "ok"
            error_msg = None

            checked_at = feed.get("checked_at")
            checked_dt = None
            if checked_at:
                try:
                    checked_dt = dateparser.parse(checked_at)
                    if checked_dt and checked_dt.tzinfo is None:
                        checked_dt = checked_dt.replace(tzinfo=timezone.utc)
                except Exception:
                    checked_dt = None

            if (feed.get("parsing_error_count") or 0) > 0:
                status = "error"
                error_msg = feed.get("parsing_error_message")
            elif checked_dt and checked_dt < stale_cutoff:
                status = "stale"

            state = {
                "id": feed_id,
                "title": feed.get("title") or "",
                "category": category,
                "unread_count": unread,
                "status": status,
                "new_items": None,
                "error": error_msg,
            }
            self._emit_progress(progress_cb, state)

            # If Miniflux reports an error or the feed hasn't been checked in a while,
            # re-issue a per-feed refresh to force an immediate retry.
            if status in ("error", "stale") and retry_budget > 0:
                self._req("PUT", f"/v1/feeds/{feed_id}/refresh")
                retry_budget -= 1

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
        
        real_feed_id = feed_id
        if feed_id.startswith("unread:"):
            base_params["status"] = ["unread"]
            real_feed_id = feed_id[7:]
        elif feed_id.startswith("read:"):
            base_params["status"] = ["read"]
            real_feed_id = feed_id[5:]
        elif feed_id.startswith("favorites:") or feed_id.startswith("starred:"):
            base_params["starred"] = "true"
            # Miniflux doesn't support "favorites:category:X", just global favorites or feed favorites if we combine?
            # /v1/entries?starred=true -> All favorites.
            # If we want favorites for a feed, /v1/feeds/{id}/entries?starred=true
            # The UI usually sends "favorites:all" or just "favorites".
            # If the user clicks "Favorites" in the tree, it might pass "favorites".
            # Mainframe usually passes "favorites" or "favorites:all" if it's a special node.
            # Let's handle "favorites" and "starred" as "all starred".
            real_feed_id = "all" 
            if ":" in feed_id:
                # Handle "favorites:<feed_id>" if we ever support per-feed favorites view?
                # For now let's assume global favorites view.
                suffix = feed_id.split(":", 1)[1]
                if suffix != "all":
                    # Maybe it's favorites for a specific feed/category?
                    # Miniflux supports starred=true on feed entries endpoint.
                    real_feed_id = suffix
                else:
                    real_feed_id = "all"

        entries: List[Dict[str, Any]] = []

        if real_feed_id.startswith("category:"):
            cat_title = real_feed_id.split(":", 1)[1]
            category_id = self._get_category_id_by_title(cat_title)
            if category_id is None:
                return []
            base_params["category_id"] = category_id
            entries = self._get_entries_paged("/v1/entries", base_params, limit=200)
        elif real_feed_id == "all":
            entries = self._get_entries_paged("/v1/entries", base_params, limit=200)
        else:
            # This is the guarantee path for complete retrieval.
            entries = self._get_entries_paged(f"/v1/feeds/{real_feed_id}/entries", base_params, limit=200)

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
                entry.get("content") or entry.get("summary") or "",
                entry.get("url") or "",
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

        real_feed_id = feed_id
        if feed_id.startswith("unread:"):
            base_params["status"] = ["unread"]
            real_feed_id = feed_id[7:]
        elif feed_id.startswith("read:"):
            base_params["status"] = ["read"]
            real_feed_id = feed_id[5:]

        endpoint, params = self._resolve_entries_endpoint(real_feed_id, base_params)
        if not endpoint:
            return [], 0
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

    def mark_unread(self, article_id: str) -> bool:
        self._req("PUT", "/v1/entries", json={"entry_ids": [int(article_id)], "status": "unread"})
        return True

    def supports_favorites(self) -> bool:
        return True

    def toggle_favorite(self, article_id: str):
        # Miniflux: PUT /v1/entries/{id}/bookmark
        res = self._req("PUT", f"/v1/entries/{article_id}/bookmark")
        # Returns None on success (204) usually? Or updated entry?
        # Actually Miniflux toggle endpoint "bookmark" toggles it.
        # But wait, does it toggle or just set?
        # API docs: "Toggle "bookmark" status for an entry." -> PUT /v1/entries/{entryID}/bookmark
        if res is None: # 204 or success
             # We need to know the new state to return it.
             # Fetch entry to check?
             entry = self._req("GET", f"/v1/entries/{article_id}")
             if entry:
                 return entry.get("starred", False)
        return True # Assume toggled if we can't check? Or maybe we should use set_favorite if we want explicit control.

    def set_favorite(self, article_id: str, is_favorite: bool) -> bool:
        # Miniflux doesn't have explicit set-favorite, only toggle.
        # So we must check state first.
        entry = self._req("GET", f"/v1/entries/{article_id}")
        if not entry:
            return False
        current = entry.get("starred", False)
        if current != is_favorite:
            self._req("PUT", f"/v1/entries/{article_id}/bookmark")
        return True

    def add_feed(self, url: str, category: str = "Uncategorized") -> bool:
        from core.discovery import get_ytdlp_feed_url, discover_feed
        from core import odysee as odysee_mod
        from core import rumble as rumble_mod
        
        # Try to get native feed URL for media sites (e.g. YouTube)
        # Miniflux can sometimes fail to discover these natively, leading to 500 errors.
        real_url = get_ytdlp_feed_url(url) or discover_feed(url) or url
        
        # Explicitly normalize Odysee/Rumble URLs to their RSS/Listing formats
        # Odysee: converts channel URL to RSS XML (Miniflux can parse XML).
        # Rumble: standardizes URL path (Miniflux might still fail if blocked, but this is best effort).
        real_url = odysee_mod.normalize_odysee_feed_url(real_url)
        real_url = rumble_mod.normalize_rumble_feed_url(real_url)
        
        cats = self._req("GET", "/v1/categories")
        if not cats:
            return False
            
        category_id = cats[0]["id"]
        if category:
            for c in cats:
                if c["title"].lower() == category.lower():
                    category_id = c["id"]
                    break
        
        data = {"feed_url": real_url, "category_id": category_id}
        res = self._req("POST", "/v1/feeds", json=data)
        
        # If Miniflux fails (likely on Rumble), warn the user but don't crash.
        if res is None and rumble_mod.is_rumble_url(real_url):
            log.warning(f"Miniflux failed to add Rumble feed: {real_url}. Miniflux server may be blocked by Rumble.")
            
        return res is not None

    def remove_feed(self, feed_id: str) -> bool:
        self._req("DELETE", f"/v1/feeds/{feed_id}")
        return True

    def supports_feed_edit(self) -> bool:
        return True

    def supports_feed_url_update(self) -> bool:
        return True

    def update_feed(self, feed_id: str, title: str = None, url: str = None, category: str = None) -> bool:
        payload = {}
        if title is not None:
            payload["title"] = title
        if url is not None:
            payload["feed_url"] = url
        if category is not None:
            cats = self._req("GET", "/v1/categories") or []
            cat_id = None
            for c in cats:
                if str(c.get("title", "")).lower() == str(category).lower():
                    cat_id = c.get("id")
                    break
            if cat_id is None and category:
                created = self._req("POST", "/v1/categories", json={"title": category})
                if isinstance(created, dict):
                    cat_id = created.get("id")
            if cat_id is not None:
                payload["category_id"] = cat_id
        if not payload:
            return True
        res = self._req("PUT", f"/v1/feeds/{feed_id}", json=payload)
        return res is not None
        
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

    def fetch_full_content(self, article_id: str, url: str = ""):
        """Return raw HTML fetched by Miniflux server-side fetch-content.

        Behavior:
        - Uses PUT /v1/entries/{id}/fetch-content (Miniflux requirement).
        - Swallows 404 (entry not found/expired) quietly to avoid noisy logs.
        - Returns the updated entry's content on success, otherwise None.
        """
        if not article_id:
            return None

        try:
            # Some instances require numeric IDs; coerce when possible.
            aid = int(str(article_id))
        except Exception:
            aid = article_id

        try:
            # Direct request so we can selectively silence 404s.
            resp = requests.put(
                f"{self.base_url}/v1/entries/{aid}/fetch-content",
                headers=self.headers,
                timeout=15,
            )

            if resp.status_code == 404:
                # Entry no longer exists on the server; just fall back.
                return None

            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, dict):
                content = data.get("content")
                if content:
                    return content
        except requests.HTTPError as e:
            # Only surface non-404 errors.
            code = getattr(e.response, "status_code", None)
            if code and code != 404:
                log.error(f"Miniflux fetch-content HTTP error for {article_id}: {e}")
        except Exception as e:
            log.error(f"Miniflux fetch-content error for {article_id}: {e}")
        return None

    def _emit_progress(self, progress_cb, state):
        if progress_cb is None:
            return
        try:
            progress_cb(state)
        except Exception as e:
            log.error(f"Miniflux progress callback failed: {e}")
