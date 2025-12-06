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
            # Uses self.headers which now includes User-Agent
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
        params = {"direction": "desc", "order": "published_at", "limit": 300}
        
        if feed_id.startswith("category:"):
            cat_title = feed_id.split(":", 1)[1]
            cats = self._req("GET", "/v1/categories")
            if cats:
                for c in cats:
                    if c["title"] == cat_title:
                        params["category_id"] = c["id"]
                        break
        elif feed_id != "all":
            params["feed_id"] = feed_id
            
        data = self._req("GET", "/v1/entries", params=params)
        if not data: return []
        
        entries = data.get("entries", [])
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
                    try:
                        soup = BeautifulSoup(content, 'xml')
                    except Exception as parser_exc:
                        print(f"OPML xml parser unavailable, falling back to html.parser: {parser_exc}")
                        soup = BeautifulSoup(content, 'html.parser')

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
