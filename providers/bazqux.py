import requests
import logging
from typing import List, Dict, Any
from .base import RSSProvider
from core.models import Feed, Article
from core import utils

log = logging.getLogger(__name__)

class BazQuxProvider(RSSProvider):
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.conf = config.get("providers", {}).get("bazqux", {})
        self.email = self.conf.get("email", "")
        self.password = self.conf.get("password", "")
        self.token = None
        self.base_url = "https://www.bazqux.com/reader/api/0"
        self.session = requests.Session()
        self.session.headers.update(utils.HEADERS)
        self._categories_cache = set()

    def get_name(self) -> str:
        return "BazQux"

    def _login(self):
        if self.token: return True
        try:
            # Login usually requires a clean session or specific headers, 
            # but reusing session is fine as long as we handle Auth header manually later.
            resp = self.session.post("https://www.bazqux.com/accounts/ClientLogin", data={
                "Email": self.email,
                "Passwd": self.password,
                "service": "reader",
                "output": "json"
            })
            resp.raise_for_status()
            
            # Try parsing as JSON first
            try:
                data = resp.json()
                if "Auth" in data:
                    self.token = data["Auth"]
                    return True
            except ValueError:
                pass # Not JSON, try line-based
            
            for line in resp.text.splitlines():
                if line.startswith("Auth="):
                    self.token = line.split("=", 1)[1]
                    return True
            return False
        except Exception as e:
            log.error(f"BazQux Login Error: {e}")
            return False

    def _headers(self):
        # We return a dict of headers to overlay on the session.
        # utils.HEADERS is already in session, but we need Authorization.
        h = {}
        if self.token:
            h["Authorization"] = f"GoogleLogin auth={self.token}"
        return h

    def refresh(self, progress_cb=None, force: bool = False) -> bool:
        if not self.email or not self.password:
            log.warning("BazQux credentials missing.")
            return False
        return self._login()

    def get_feeds(self) -> List[Feed]:
        if not self._login(): return []
        try:
            # Fetch subscriptions
            resp = self.session.get(f"{self.base_url}/subscription/list", headers=self._headers(), params={"output": "json"})
            resp.raise_for_status()
            data = resp.json()
            
            # Fetch unread counts (quick follow-up on same connection)
            unread_map = {}
            try:
                # API: /unread-count?output=json
                uc_resp = self.session.get(f"{self.base_url}/unread-count", headers=self._headers(), params={"output": "json"})
                if uc_resp.ok:
                    uc_data = uc_resp.json()
                    # format: {"unreadcounts": [{"id": "feed/...", "count": 123, "newestItemTimestampUsec": ...}, ...]}
                    for entry in uc_data.get("unreadcounts", []):
                        fid = entry.get("id", "")
                        if fid.startswith("feed/"):
                            # our Feed IDs preserve the "feed/" prefix in this provider?
                            # In get_feeds below: id=sub["id"] which is "feed/..."
                            # So we key by the full ID.
                            unread_map[fid] = entry.get("count", 0)
            except Exception as e:
                log.warning(f"BazQux Unread Count Error: {e}")

            feeds = []
            self._categories_cache = set()

            for sub in data.get("subscriptions", []):
                cat = "Uncategorized"
                if sub.get("categories"):
                    cat = sub["categories"][0]["label"]
                    self._categories_cache.add(cat)
                
                # 'id' is typically "feed/http://..."
                feed_url = sub.get("url", sub["id"].replace("feed/", "", 1))
                feed_id = sub["id"]

                f = Feed(
                    id=feed_id,
                    title=sub["title"],
                    url=feed_url,
                    category=cat,
                    icon_url=""
                )
                f.unread_count = unread_map.get(feed_id, 0)
                feeds.append(f)
            return feeds
        except Exception as e:
            log.error(f"BazQux Feeds Error: {e}")
            return []

    def _fetch_articles(self, feed_id: str, count: int = 50, continuation: str = None) -> List[Article]:
        if not self._login(): return []
        try:
            real_feed_id = feed_id
            params = {"output": "json", "n": count}
            if continuation:
                params["c"] = continuation
            
            # Handle status prefixes
            filter_unread = False
            filter_favorites = False
            filter_read = False
            
            if feed_id.startswith("unread:"):
                filter_unread = True
                real_feed_id = feed_id[7:]
                params["xt"] = "user/-/state/com.google/read"
            elif feed_id.startswith("read:"):
                filter_read = True
                real_feed_id = feed_id[5:]
                # We can use 'it' param, OR just query the read stream directly if it's 'all'
                # params["it"] = "user/-/state/com.google/read" 
            elif feed_id.startswith("favorites:") or feed_id.startswith("starred:"):
                filter_favorites = True
                real_feed_id = "user/-/state/com.google/starred"
                if ":" in feed_id:
                     suffix = feed_id.split(":", 1)[1]
                     pass

            # Handle special IDs
            if real_feed_id == "all":
                if filter_read:
                    real_feed_id = "user/-/state/com.google/read"
                else:
                    real_feed_id = "user/-/state/com.google/reading-list"
            elif real_feed_id.startswith("category:"):
                cat_name = real_feed_id.split(":", 1)[1]
                real_feed_id = f"user/-/label/{cat_name}"
                if filter_read:
                    params["it"] = "user/-/state/com.google/read"

            url = f"{self.base_url}/stream/contents/{real_feed_id}"
            resp = self.session.get(url, headers=self._headers(), params=params)
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
                    content,
                    item.get("alternate", [{}])[0].get("href", ""),
                )
                
                chapters = chapters_map.get(article_id, [])
                
                is_fav = False
                is_read_flag = False
                for cat in item.get("categories", []):
                    if "com.google" in cat:
                        if cat.endswith("/starred"):
                            is_fav = True
                        if cat.endswith("/read"):
                            is_read_flag = True

                # Client-side safety filter
                if filter_unread and is_read_flag:
                    continue
                if filter_read and not is_read_flag:
                    continue
                if filter_favorites and not is_fav:
                    continue

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
            log.error(f"BazQux Articles Error: {e}")
            return []

    def get_articles(self, feed_id: str) -> List[Article]:
        return self._fetch_articles(feed_id, count=50)

    def get_articles_page(self, feed_id: str, offset: int = 0, limit: int = 200) -> tuple[List[Article], int | None]:
        count = offset + limit
        articles = self._fetch_articles(feed_id, count=count)
        total = None
        if len(articles) < count:
            total = len(articles)
        sliced_articles = articles[offset:offset + limit]
        return sliced_articles, total

    def get_article_chapters(self, article_id: str) -> List[Dict]:
        return utils.get_chapters_from_db(article_id)

    def mark_read(self, article_id: str) -> bool:
        if not self._login(): return False
        try:
            self.session.post(f"{self.base_url}/edit-tag", headers=self._headers(), data={
                "i": article_id,
                "a": "user/-/state/com.google/read"
            })
            return True
        except Exception as e:
            log.error(f"BazQux Mark Read Error: {e}")
            return False

    def mark_unread(self, article_id: str) -> bool:
        if not self._login(): return False
        try:
            self.session.post(f"{self.base_url}/edit-tag", headers=self._headers(), data={
                "i": article_id,
                "r": "user/-/state/com.google/read"
            })
            return True
        except Exception as e:
            log.error(f"BazQux Mark Unread Error: {e}")
            return False

    def supports_favorites(self) -> bool:
        return True

    def set_favorite(self, article_id: str, is_favorite: bool) -> bool:
        if not self._login(): return False
        try:
            action = "a" if is_favorite else "r"
            self.session.post(f"{self.base_url}/edit-tag", headers=self._headers(), data={
                "i": article_id,
                action: "user/-/state/com.google/starred"
            })
            return True
        except Exception as e:
            log.error(f"BazQux Set Favorite Error: {e}")
            return False

    def toggle_favorite(self, article_id: str):
        try:
            resp = self.session.get(f"{self.base_url}/stream/items/ids", headers=self._headers(), params={"i": article_id, "output": "json"})
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
        if not self._login(): return False
        from core.discovery import get_ytdlp_feed_url, discover_feed
        real_url = get_ytdlp_feed_url(url) or discover_feed(url) or url
        try:
            data = {
                "s": f"feed/{real_url}",
                "ac": "subscribe"
            }
            if category:
                data["t"] = category
            
            resp = self.session.post(f"{self.base_url}/subscription/edit", headers=self._headers(), data=data)
            resp.raise_for_status()
            return True
        except Exception as e:
            log.error(f"BazQux Add Feed Error: {e}")
            return False

    def remove_feed(self, feed_id: str) -> bool:
        if not self._login(): return False
        try:
            self.session.post(f"{self.base_url}/subscription/edit", headers=self._headers(), data={
                "s": feed_id,
                "ac": "unsubscribe"
            })
            return True
        except Exception as e:
            log.error(f"BazQux Remove Feed Error: {e}")
            return False

    def supports_feed_edit(self) -> bool:
        return True

    def supports_feed_url_update(self) -> bool:
        return False

    def update_feed(self, feed_id: str, title: str = None, url: str = None, category: str = None) -> bool:
        if not self._login():
            return False
        data = {"s": feed_id, "ac": "edit"}
        if title is not None:
            data["t"] = title

        if category is not None:
            current_cat = None
            try:
                for f in self.get_feeds():
                    if f.id == feed_id:
                        current_cat = f.category or "Uncategorized"
                        break
            except Exception:
                current_cat = None
            if current_cat and current_cat != category:
                if current_cat and current_cat != "Uncategorized":
                    data["r"] = f"user/-/label/{current_cat}"
                if category and category != "Uncategorized":
                    data["a"] = f"user/-/label/{category}"

        try:
            resp = self.session.post(f"{self.base_url}/subscription/edit", headers=self._headers(), data=data)
            return resp.ok
        except Exception as e:
            log.error(f"BazQux Update Feed Error: {e}")
            return False

    def get_categories(self) -> List[str]:
        if self._categories_cache:
            return sorted(list(self._categories_cache))
            
        if not self._login(): return []
        try:
            resp = self.session.get(f"{self.base_url}/tag/list", headers=self._headers(), params={"output": "json"})
            resp.raise_for_status()
            data = resp.json()
            cats = []
            for tag in data.get("tags", []):
                # Filter system tags
                tag_id = tag.get("id", "")
                if tag_id.startswith("user/") and "/label/" in tag_id:
                    label = tag_id.split("/label/", 1)[1]
                    cats.append(label)
                    self._categories_cache.add(label)
            return sorted(cats)
        except Exception as e:
            log.error(f"BazQux Get Categories Error: {e}")
            return []

    def add_category(self, title: str) -> bool:
        return True

    def rename_category(self, old_title: str, new_title: str) -> bool:
        if not self._login(): return False
        try:
            # Try /rename-tag endpoint
            user_id = "-" # usually works as wildcard for current user
            source = f"user/-/label/{old_title}"
            dest = f"user/-/label/{new_title}"
            
            resp = self.session.post(f"{self.base_url}/rename-tag", headers=self._headers(), data={
                "s": source,
                "dest": dest
            })
            return resp.ok
        except Exception as e:
            log.error(f"BazQux Rename Category Error: {e}")
            return False

    def delete_category(self, title: str) -> bool:
        if not self._login(): return False
        try:
            tag = f"user/-/label/{title}"
            self.session.post(f"{self.base_url}/disable-tag", headers=self._headers(), data={
                "s": tag
            })
            return True
        except Exception as e:
            log.error(f"BazQux Delete Category Error: {e}")
            return False
