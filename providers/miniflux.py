import requests
import re
import os
from typing import List, Dict, Any
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta
from dateutil import parser as dateparser
from .base import RSSProvider, Feed, Article
from core import utils
from core.signals import SignalManager
import logging
from core.db import get_connection

log = logging.getLogger(__name__)

class MinifluxProvider(RSSProvider):
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.conf = config.get("providers", {}).get("miniflux", {})
        url = self.conf.get("url", "").rstrip("/")
        self.base_url = re.sub(r'/v1/?$', '', url)
        self.headers = {
            "X-Auth-Token": self.conf.get("api_key", ""),
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "BlindRSS/1.0"
        }
        # Do not merge with utils.HEADERS as RSS accept headers break JSON APIs
        
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
            # Uses self.headers which now includes User-Agent
            resp = requests.request(method, url, headers=self.headers, json=json, params=params, timeout=10)
            
            if resp.status_code == 401:
                print(f"Miniflux Authentication Failed (401) for {url}. Please check your API Key in config.json.")
                return None
                
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

    def refresh(self) -> bool:
        log.info("MinifluxProvider.refresh() called.")
        # Step 1: Tell Miniflux server to refresh its feeds (gets new items into Miniflux)
        self._req("PUT", "/v1/feeds/refresh")

        # Step 2: Fetch all feeds from Miniflux
        miniflux_feeds_data = self._req("GET", "/v1/feeds")
        if not miniflux_feeds_data:
            log.error("MinifluxProvider: Failed to fetch feeds from Miniflux.")
            return False
        log.debug(f"MinifluxProvider: Fetched {len(miniflux_feeds_data)} feeds from Miniflux.")

        # Optional throttles for testing/CI to avoid syncing tens of thousands of entries
        max_test_feeds = int(os.getenv("MINIFLUX_TEST_FEEDS", "0") or "0")
        page_limit_override = int(os.getenv("MINIFLUX_PAGE_LIMIT", "0") or "0")

        def latest_ts_for_feed(cur, feed_id: str):
            cur.execute("SELECT MAX(date) FROM articles WHERE feed_id=?", (feed_id,))
            row = cur.fetchone()
            if not row or not row[0]:
                return None
            try:
                dt = dateparser.parse(row[0])
                if not dt.tzinfo:
                    dt = dt.replace(tzinfo=timezone.utc)
                now_ts = datetime.now(timezone.utc).timestamp()
                ts = int(dt.timestamp())
                # Guard against bad stored dates that are far in the future which would stop incremental sync.
                if ts > now_ts + 2*24*3600:
                    return None
                return ts
            except Exception:
                return None

        with get_connection() as conn: # Use 'with' statement for connection management
            c = conn.cursor()

            # Update local feeds table and fetch articles for each
            for mf_feed in miniflux_feeds_data:
                feed_id = str(mf_feed["id"])
                feed_title = mf_feed["title"]
                feed_url = mf_feed["site_url"]
                feed_category = mf_feed.get("category", {}).get("title", "Uncategorized")
                
                log.debug(f"MinifluxProvider: Processing feed '{feed_title}' (ID: {feed_id}).")

                # Ensure feed exists in local DB
                c.execute("INSERT OR IGNORE INTO feeds (id, url, title, category, icon_url) VALUES (?, ?, ?, ?, ?)",
                          (feed_id, feed_url, feed_title, feed_category, mf_feed.get("icon", {}).get("data", "")))
                
                # Now fetch all entries for this specific feed from Miniflux
                offset = 0
                limit = page_limit_override if page_limit_override > 0 else 100 # Max limit for Miniflux API
                total_entries_processed = 0

                # Incremental: if we already have items for this feed, only fetch newer ones.
                # Initial sync (no stored date) will page through the whole history.
                published_after_ts = latest_ts_for_feed(c, feed_id)
                # If we have very few items stored, treat as cold start to backfill the full feed.
                c.execute("SELECT COUNT(*) FROM articles WHERE feed_id=?", (feed_id,))
                existing_count = c.fetchone()[0] or 0
                cold_start = existing_count < 100
                if cold_start:
                    published_after_ts = None
                # Fetch both unread and read to build a complete local cache.
                for status in ["unread", "read"]:
                    offset = 0
                    while True:
                        params = {
                            "feed_id": feed_id,
                            "direction": "desc",
                            "order": "published_at",
                            "limit": limit,
                            "status": status
                        }
                        if published_after_ts:
                            params["published_after"] = published_after_ts - 60  # small overlap to avoid boundary misses
                        params["offset"] = offset

                        data = self._req("GET", "/v1/entries", params=params)
                        if not data or not data.get("entries"):
                            break
                        
                        entries = data.get("entries", [])
                        log.debug(f"MinifluxProvider: Fetched {len(entries)} entries (status={status}) for feed '{feed_title}' at offset {offset}.")
                        
                        # Process and store this batch immediately
                        for entry in entries:
                            self._process_miniflux_entry_into_db(entry, feed_id, c)
                            
                        total_entries_processed += len(entries)
                        
                        # Commit and emit signal for this batch
                        conn.commit()
                        if entries:
                            SignalManager.emit("feed_update", {"feed_id": feed_id, "count": len(entries)})
                        
                        if len(entries) < limit: # Last page
                            break

                        if os.getenv("MINIFLUX_TEST_STOP_FIRST_PAGE"):
                            break

                        offset += limit
                
                log.debug(f"MinifluxProvider: Total {total_entries_processed} entries retrieved for feed '{feed_title}'.")

                if max_test_feeds:
                    max_test_feeds -= 1
                    if max_test_feeds <= 0:
                        break
            
            # The 'with' statement ensures commit/rollback and close
        log.info("MinifluxProvider: All feed articles processed and transaction committed.")
        log.info("MinifluxProvider.refresh() completed.")
        return True

    def _process_miniflux_entry_into_db(self, entry, feed_id, cursor):
        article_id = str(entry["id"])
        
        # Check if article exists in local DB for this feed
        cursor.execute("SELECT date FROM articles WHERE id = ? AND feed_id = ?", (article_id, feed_id))
        row = cursor.fetchone()
        
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
            entry.get("url") or ""
        )
        # If feed date is far in the future or normalization failed, fall back to created_at or 'now'
        try:
            if date == "0001-01-01 00:00:00":
                raise ValueError("sentinel date")
            dt = dateparser.parse(date)
            if not dt.tzinfo:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt - datetime.now(timezone.utc) > timedelta(days=2):
                raise ValueError("future skew")
        except Exception:
            fallback = utils.normalize_date(
                entry.get("created_at"),
                entry.get("title") or "",
                entry.get("content") or entry.get("summary") or "",
                entry.get("url") or ""
            )
            if fallback == "0001-01-01 00:00:00":
                date = utils.format_datetime(datetime.now(timezone.utc))
            else:
                date = fallback
        
        if row:
            # Article exists, check if date needs updating
            existing_date = row[0] or ""
            if existing_date != date:
                cursor.execute("UPDATE articles SET title=?, url=?, content=?, date=?, author=?, is_read=?, media_url=?, media_type=? WHERE id=? AND feed_id=?",
                               (entry["title"], entry["url"], entry.get("content", ""), date, entry.get("author", ""), 
                                (1 if entry.get("status") == "read" else 0), media_url, media_type, article_id, feed_id))
        else:
            # New article, insert it
            try:
                cursor.execute("INSERT INTO articles (id, feed_id, title, url, content, date, author, is_read, media_url, media_type) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                               (article_id, feed_id, entry["title"], entry["url"], entry.get("content", ""), date, entry.get("author", ""), 
                                (1 if entry.get("status") == "read" else 0), media_url, media_type))
                # Chapter fetching is now lazy-loaded on selection/play
            except Exception as e:
                log.error(f"ERROR_MINIFLUX_DB: MinifluxProvider failed to insert article {article_id} for feed {feed_id}: {e}")


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

    def get_articles(self, feed_id: str, max_items: int = None) -> List[Article]:
        conn = get_connection()
        c = conn.cursor()
        
        if feed_id == "all":
            query = "SELECT id, feed_id, title, url, content, date, author, is_read, media_url, media_type FROM articles ORDER BY date DESC"
            if max_items:
                query += f" LIMIT {int(max_items)}"
            c.execute(query)
        elif feed_id.startswith("category:"):
            cat_name = feed_id.split(":", 1)[1]
            query = """
                SELECT a.id, a.feed_id, a.title, a.url, a.content, a.date, a.author, a.is_read, a.media_url, a.media_type
                FROM articles a
                JOIN feeds f ON a.feed_id = f.id
                WHERE f.category = ?
                ORDER BY a.date DESC
            """
            if max_items:
                query += f" LIMIT {int(max_items)}"
            c.execute(query, (cat_name,))
        else:
            query = "SELECT id, feed_id, title, url, content, date, author, is_read, media_url, media_type FROM articles WHERE feed_id = ? ORDER BY date DESC"
            if max_items:
                query += f" LIMIT {int(max_items)}"
            c.execute(query, (feed_id,))
            
        rows = c.fetchall()
        
        article_ids = [r[0] for r in rows]
        chapters_map = utils.get_chapters_batch(article_ids)
        
        articles = []
        for row in rows:
            chs = chapters_map.get(row[0], [])
            chs.sort(key=lambda x: x["start"])
            
            articles.append(Article(
                id=row[0], feed_id=row[1], title=row[2], url=row[3], content=row[4], date=row[5], author=row[6], is_read=bool(row[7]),
                media_url=row[8], media_type=row[9], chapters=chs
            ))
        conn.close()
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

    def mark_read(self, article_id: str) -> bool:
        res = self._req("PUT", "/v1/entries", json={"entry_ids": [int(article_id)], "status": "read"})
        
        # Also update local database
        if res is not None:
            conn = get_connection()
            c = conn.cursor()
            c.execute("UPDATE articles SET is_read = 1 WHERE id = ?", (article_id,))
            conn.commit()
            conn.close()
            return True
        return False

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
        if not self.base_url: return False
        url = f"{self.base_url}/v1/import"
        
        try:
            if target_category:
                content = ""
                for enc in ['utf-8', 'utf-8-sig', 'latin-1', 'cp1252']:
                    try:
                        with open(path, 'r', encoding=enc) as f:
                            content = f.read()
                        break
                    except: continue
                
                if not content: return False
                
                try:
                    soup = BeautifulSoup(content, 'xml')
                    if not soup.find('opml'):
                        soup = BeautifulSoup(content, 'html.parser')
                        
                    body = soup.find('body')
                    if body:
                        feeds = [t for t in soup.find_all('outline') if t.get('xmlUrl') or t.get('xmlurl')]
                        body.clear()
                        wrapper = soup.new_tag('outline', text=target_category, title=target_category)
                        for feed in feeds:
                            wrapper.append(feed)
                        body.append(wrapper)
                        file_content = str(soup).encode('utf-8')
                        files = {'file': ('import.opml', file_content)}
                        
                        # Use self.headers (merged)
                        resp = requests.post(url, headers=self.headers, files=files, timeout=120)
                        resp.raise_for_status()
                        return True
                except Exception as e:
                    print(f"OPML modification error: {e}")

            with open(path, 'rb') as f:
                files = {'file': f}
                resp = requests.post(url, headers=self.headers, files=files, timeout=120)
                resp.raise_for_status()
                return True
                
        except Exception as e:
            print(f"Miniflux native import error: {e}")
            return False

    def export_opml(self, path: str) -> bool:
        return False

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
