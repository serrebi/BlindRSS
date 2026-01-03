import requests
import logging
from typing import List, Dict, Any
from .base import RSSProvider
from core.models import Feed, Article
from core import utils

log = logging.getLogger(__name__)

class InoreaderProvider(RSSProvider):
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.conf = config.get("providers", {}).get("inoreader", {})
        self.base_url = "https://www.inoreader.com/reader/api/0"
        self.token = self.conf.get("token", "")

    def get_name(self) -> str:
        return "Inoreader"

    def _headers(self):
        h = utils.HEADERS.copy()
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def refresh(self, progress_cb=None) -> bool:
        return True

    def get_feeds(self) -> List[Feed]:
        if not self.token: return []
        try:
            resp = requests.get(f"{self.base_url}/subscription/list", headers=self._headers(), params={"output": "json"})
            resp.raise_for_status()
            data = resp.json()
            
            feeds = []
            for sub in data.get("subscriptions", []):
                cat = "Uncategorized"
                if sub.get("categories"):
                    cat = sub["categories"][0]["label"]
                
                feeds.append(Feed(
                    id=sub["id"],
                    title=sub["title"],
                    url=sub["url"],
                    category=cat,
                    icon_url=sub.get("iconUrl", "")
                ))
            return feeds
        except Exception as e:
            log.error(f"Inoreader Feeds Error: {e}")
            return []

    def get_articles(self, feed_id: str) -> List[Article]:
        if not self.token: return []
        try:
            real_feed_id = feed_id
            params = {"output": "json", "n": 50}

            if feed_id.startswith("unread:"):
                real_feed_id = feed_id[7:]
                params["xt"] = "user/-/state/com.google/read"
            elif feed_id.startswith("read:"):
                real_feed_id = feed_id[5:]
                params["it"] = "user/-/state/com.google/read"
            elif feed_id.startswith("favorites:") or feed_id.startswith("starred:"):
                real_feed_id = "user/-/state/com.google/starred"

            url = f"{self.base_url}/stream/contents/{real_feed_id}"
            resp = requests.get(url, headers=self._headers(), params=params)
            resp.raise_for_status()
            data = resp.json()
            
            items = data.get("items", [])
            article_ids = [item["id"] for item in items]
            chapters_map = utils.get_chapters_batch(article_ids)
            
            articles = []
            for item in items:
                content = ""
                if "summary" in item: content = item["summary"]["content"]
                if "content" in item: content = item["content"]["content"]
                
                # Media
                media_url = None
                media_type = None
                if "enclosure" in item and item["enclosure"]:
                    # Inoreader might return list or single? API says list usually.
                    encs = item["enclosure"]
                    if isinstance(encs, list) and encs:
                        media_url = encs[0].get("href")
                        media_type = encs[0].get("type")
                
                article_id = item["id"]
                
                # Date
                date = utils.normalize_date(
                    str(item.get("published", "")),
                    item.get("title", ""),
                    content,
                    item.get("alternate", [{}])[0].get("href", ""),
                )

                chapters = chapters_map.get(article_id, [])

                is_fav = False
                is_read_flag = False
                for cat in item.get("categories", []):
                    if "starred" in cat:
                        is_fav = True
                    if "read" in cat and "com.google" in cat:
                        is_read_flag = True

                articles.append(Article(
                    id=article_id,
                    feed_id=item["origin"]["streamId"],
                    title=item.get("title", "No Title"),
                    url=item.get("alternate", [{}])[0].get("href", ""),
                    content=content,
                    date=date,
                    author=item.get("author", "Unknown"),
                    is_read=is_read_flag,
                    is_favorite=is_fav,
                    media_url=media_url,
                    media_type=media_type,
                    chapters=chapters
                ))
            return articles
        except Exception as e:
            log.error(f"Inoreader Articles Error: {e}")
            return []

    def get_article_chapters(self, article_id: str) -> List[Dict]:
        # Similar to Miniflux, we need media info.
        # For now, try to check DB.
        chapters = utils.get_chapters_from_db(article_id)
        if chapters: return chapters
        # We can't easily fetch single item details without knowing feed ID in Google Reader API sometimes,
        # but strictly speaking /stream/items/ids works if supported.
        return []

    def mark_read(self, article_id: str) -> bool:
        if not self.token: return False
        try:
            requests.post(f"{self.base_url}/edit-tag", headers=self._headers(), data={
                "i": article_id,
                "a": "user/-/state/com.google/read"
            })
            return True
        except Exception as e:
            log.error(f"Inoreader Mark Read Error: {e}")
            return False

    def supports_favorites(self) -> bool:
        return True

    def set_favorite(self, article_id: str, is_favorite: bool) -> bool:
        if not self.token: return False
        try:
            action = "a" if is_favorite else "r"
            requests.post(f"{self.base_url}/edit-tag", headers=self._headers(), data={
                "i": article_id,
                action: "user/-/state/com.google/starred"
            })
            return True
        except Exception as e:
            log.error(f"Inoreader Set Favorite Error: {e}")
            return False

    def toggle_favorite(self, article_id: str):
        if not self.token: return None
        try:
            resp = requests.get(f"{self.base_url}/stream/items/ids", headers=self._headers(), params={"i": article_id, "output": "json"})
            if resp.ok:
                items = resp.json().get("items", [])
                if items:
                    cats = items[0].get("categories", [])
                    is_fav = any("starred" in c for c in cats)
                    new_state = not is_fav
                    self.set_favorite(article_id, new_state)
                    return new_state
        except:
            pass
        return None

    def add_feed(self, url: str, category: str = None) -> bool:
        if not self.token: return False
        from core.discovery import get_ytdlp_feed_url, discover_feed
        real_url = get_ytdlp_feed_url(url) or discover_feed(url) or url
        try:
            data = {
                "s": f"feed/{real_url}",
                "ac": "subscribe"
            }
            if category:
                data["t"] = category
            
            resp = requests.post(f"{self.base_url}/subscription/edit", headers=self._headers(), data=data)
            resp.raise_for_status()
            return True
        except Exception as e:
            log.error(f"Inoreader Add Feed Error: {e}")
            return False

    def remove_feed(self, feed_id: str) -> bool:
        if not self.token: return False
        try:
            requests.post(f"{self.base_url}/subscription/edit", headers=self._headers(), data={
                "s": feed_id,
                "ac": "unsubscribe"
            })
            return True
        except Exception as e:
            log.error(f"Inoreader Remove Feed Error: {e}")
            return False

    def get_categories(self) -> List[str]:
        if not self.token: return []
        try:
            resp = requests.get(f"{self.base_url}/tag/list", headers=self._headers(), params={"output": "json"})
            resp.raise_for_status()
            data = resp.json()
            cats = []
            for tag in data.get("tags", []):
                tag_id = tag.get("id", "")
                if tag_id.startswith("user/") and "/label/" in tag_id:
                    label = tag_id.split("/label/", 1)[1]
                    cats.append(label)
            return sorted(cats)
        except Exception as e:
            log.error(f"Inoreader Get Categories Error: {e}")
            return []

    def add_category(self, title: str) -> bool:
        return True

    def rename_category(self, old_title: str, new_title: str) -> bool:
        if not self.token: return False
        try:
            source = f"user/-/label/{old_title}"
            dest = f"user/-/label/{new_title}"
            resp = requests.post(f"{self.base_url}/rename-tag", headers=self._headers(), data={
                "s": source,
                "dest": dest
            })
            return resp.ok
        except Exception as e:
            log.error(f"Inoreader Rename Category Error: {e}")
            return False

    def delete_category(self, title: str) -> bool:
        if not self.token: return False
        try:
            tag = f"user/-/label/{title}"
            requests.post(f"{self.base_url}/disable-tag", headers=self._headers(), data={
                "s": tag
            })
            return True
        except Exception as e:
            log.error(f"Inoreader Delete Category Error: {e}")
            return False
