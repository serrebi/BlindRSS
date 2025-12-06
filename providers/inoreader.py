import requests
from typing import List, Dict, Any
from .base import RSSProvider, Feed, Article
from core import utils

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
            print(f"Inoreader Feeds Error: {e}")
            return []

    def get_articles(self, feed_id: str) -> List[Article]:
        if not self.token: return []
        try:
            url = f"{self.base_url}/stream/contents/{feed_id}"
            resp = requests.get(url, headers=self._headers(), params={"output": "json", "n": 50})
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
                    content
                )

                chapters = chapters_map.get(article_id, [])

                articles.append(Article(
                    id=article_id,
                    feed_id=item["origin"]["streamId"],
                    title=item.get("title", "No Title"),
                    url=item.get("alternate", [{}])[0].get("href", ""),
                    content=content,
                    date=date,
                    author=item.get("author", "Unknown"),
                    is_read=False,
                    media_url=media_url,
                    media_type=media_type,
                    chapters=chapters
                ))
            return articles
        except Exception as e:
            print(f"Inoreader Articles Error: {e}")
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
        return False 

    def add_feed(self, url: str, category: str = None) -> bool:
        return False

    def remove_feed(self, feed_id: str) -> bool:
        return False
        
    def import_opml(self, path: str, target_category: str = None) -> bool:
        return False

    def export_opml(self, path: str) -> bool:
        return False

    def get_categories(self) -> List[str]:
        return []

    def add_category(self, title: str) -> bool:
        return False

    def rename_category(self, old_title: str, new_title: str) -> bool:
        return False

    def delete_category(self, title: str) -> bool:
        return False
