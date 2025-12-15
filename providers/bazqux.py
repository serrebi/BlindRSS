import requests
from typing import List, Dict, Any
from .base import RSSProvider, Feed, Article
from core import utils

class BazQuxProvider(RSSProvider):
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.conf = config.get("providers", {}).get("bazqux", {})
        self.email = self.conf.get("email", "")
        self.password = self.conf.get("password", "")
        self.token = None
        self.base_url = "https://www.bazqux.com/reader/api/0"

    def get_name(self) -> str:
        return "BazQux"

    def _login(self):
        if self.token: return True
        try:
            resp = requests.post("https://www.bazqux.com/accounts/ClientLogin", data={
                "Email": self.email,
                "Passwd": self.password,
                "service": "reader",
                "output": "json"
            }, headers=utils.HEADERS)
            resp.raise_for_status()
            for line in resp.text.splitlines():
                if line.startswith("Auth="):
                    self.token = line.split("=", 1)[1]
                    return True
            return False
        except Exception as e:
            print(f"BazQux Login Error: {e}")
            return False

    def _headers(self):
        h = utils.HEADERS.copy()
        if self.token:
            h["Authorization"] = f"GoogleLogin auth={self.token}"
        return h

    def refresh(self, progress_cb=None) -> bool:
        return self._login()

    def get_feeds(self) -> List[Feed]:
        if not self._login(): return []
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
                    icon_url=""
                ))
            return feeds
        except Exception as e:
            print(f"BazQux Feeds Error: {e}")
            return []

    def get_articles(self, feed_id: str) -> List[Article]:
        if not self._login(): return []
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
                
                media_url = None
                media_type = None
                if "enclosure" in item and item["enclosure"]:
                    encs = item["enclosure"]
                    if isinstance(encs, list) and encs:
                        media_url = encs[0].get("href")
                        media_type = encs[0].get("type")
                
                article_id = item["id"]
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
            print(f"BazQux Articles Error: {e}")
            return []

    def get_article_chapters(self, article_id: str) -> List[Dict]:
        return utils.get_chapters_from_db(article_id)

    def mark_read(self, article_id: str) -> bool:
        if not self._login(): return False
        try:
            requests.post(f"{self.base_url}/edit-tag", headers=self._headers(), data={
                "i": article_id,
                "a": "user/-/state/com.google/read"
            })
            return True
        except Exception as e:
            print(f"BazQux Mark Read Error: {e}")
            return False

    def add_feed(self, url: str, category: str = None) -> bool:
        if not self._login(): return False
        try:
            data = {
                "s": f"feed/{url}",
                "ac": "subscribe"
            }
            if category:
                data["t"] = category
            
            resp = requests.post(f"{self.base_url}/subscription/edit", headers=self._headers(), data=data)
            resp.raise_for_status()
            return True
        except Exception as e:
            print(f"BazQux Add Feed Error: {e}")
            return False

    def remove_feed(self, feed_id: str) -> bool:
        if not self._login(): return False
        try:
            requests.post(f"{self.base_url}/subscription/edit", headers=self._headers(), data={
                "s": feed_id,
                "ac": "unsubscribe"
            })
            return True
        except Exception as e:
            print(f"BazQux Remove Feed Error: {e}")
            return False

    def get_categories(self) -> List[str]:
        if not self._login(): return []
        try:
            resp = requests.get(f"{self.base_url}/tag/list", headers=self._headers(), params={"output": "json"})
            resp.raise_for_status()
            data = resp.json()
            cats = []
            for tag in data.get("tags", []):
                # Filter system tags
                tag_id = tag.get("id", "")
                if tag_id.startswith("user/") and "/label/" in tag_id:
                    label = tag_id.split("/label/", 1)[1]
                    cats.append(label)
            return sorted(cats)
        except Exception as e:
            print(f"BazQux Get Categories Error: {e}")
            return []

    def add_category(self, title: str) -> bool:
        # Google Reader API doesn't support empty categories.
        # They are created when a feed is assigned to them.
        return True

    def rename_category(self, old_title: str, new_title: str) -> bool:
        if not self._login(): return False
        try:
            # Try /rename-tag endpoint
            user_id = "-" # usually works as wildcard for current user
            # Find user ID prefix? usually user/0/label/... or user/-/label/...
            # Let's try standard 'user/-/label/'
            source = f"user/-/label/{old_title}"
            dest = f"user/-/label/{new_title}"
            
            resp = requests.post(f"{self.base_url}/rename-tag", headers=self._headers(), data={
                "s": source,
                "dest": dest
            })
            return resp.ok
        except Exception as e:
            print(f"BazQux Rename Category Error: {e}")
            return False

    def delete_category(self, title: str) -> bool:
        if not self._login(): return False
        try:
            tag = f"user/-/label/{title}"
            requests.post(f"{self.base_url}/disable-tag", headers=self._headers(), data={
                "s": tag
            })
            return True
        except Exception as e:
            print(f"BazQux Delete Category Error: {e}")
            return False
