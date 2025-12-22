import requests
import time
import urllib.parse
import logging
from typing import List, Dict, Any
from datetime import datetime, timezone
from .base import RSSProvider
from core.models import Feed, Article
from core import utils

log = logging.getLogger(__name__)

class TheOldReaderProvider(RSSProvider):
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.conf = config.get("providers", {}).get("theoldreader", {})
        self.email = self.conf.get("email", "")
        self.password = self.conf.get("password", "")
        self.token = None
        self.base_url = "https://theoldreader.com/reader/api/0"

    def get_name(self) -> str:
        return "TheOldReader"

    def _login(self):
        if self.token:
            log.debug("TheOldReader: Using existing token.")
            return True
        log.info(f"TheOldReader: Attempting login for {self.email}...")
        try:
            resp = requests.post("https://theoldreader.com/accounts/ClientLogin", data={
                "client": "BlindRSS",
                "accountType": "HOSTED_OR_GOOGLE",
                "service": "reader",
                "Email": self.email,
                "Passwd": self.password,
                "output": "json"
            }, headers=utils.HEADERS)
            
            if resp.status_code != 200:
                log.error(f"TheOldReader: Login Failed! Status: {resp.status_code}, Body: {resp.text[:200]}...")
                return False
                
            for line in resp.text.splitlines():
                if line.startswith("Auth="):
                    self.token = line.split("=", 1)[1]
                    log.info("TheOldReader: Login Success - Token found in text.")
                    return True
            
            try:
                data = resp.json()
                if "Auth" in data:
                    self.token = data["Auth"]
                    log.info("TheOldReader: Login Success - Token found in JSON.")
                    return True
            except: pass # Not JSON
            
            log.error("TheOldReader: Login Failed! No Auth token found in response.")
            return False
        except Exception as e:
            log.exception(f"TheOldReader Login Error: {e}")
            return False

    def _headers(self):
        h = utils.HEADERS.copy()
        if self.token:
            h["Authorization"] = f"GoogleLogin auth={self.token}"
        return h

    def refresh(self, progress_cb=None) -> bool:
        if not self._login():
            log.warning("TheOldReader: Refresh skipped due to login failure.")
            return False
        return True

    def get_feeds(self) -> List[Feed]:
        if not self._login(): 
            log.warning("TheOldReader: Get Feeds skipped due to login failure.")
            return []
        
        log.info("TheOldReader: Fetching feeds...")
        try:
            resp = requests.get(f"{self.base_url}/subscription/list", headers=self._headers(), params={"output": "json"})
            resp.raise_for_status()
            data = resp.json()
            
            resp_counts = requests.get(f"{self.base_url}/unread-count", headers=self._headers(), params={"output": "json"})
            counts = {}
            if resp_counts.ok:
                for item in resp_counts.json().get("unreadcounts", []):
                    counts[item["id"]] = item["count"]
            
            feeds = []
            for sub in data.get("subscriptions", []):
                feed_id = sub["id"]
                cat = "Uncategorized"
                if sub.get("categories"):
                    cat = sub["categories"][0]["label"]
                
                feeds.append(Feed(
                    id=feed_id,
                    title=sub["title"],
                    url=sub["url"],
                    category=cat,
                    icon_url=sub.get("iconUrl", "")
                ))
                feeds[-1].unread_count = counts.get(feed_id, 0)
            log.info(f"TheOldReader: Found {len(feeds)} feeds.")
            return feeds
        except Exception as e:
            log.exception(f"TheOldReader Feeds Error: {e}")
            return []

    def get_articles(self, feed_id: str) -> List[Article]:
        if not self._login(): 
            log.warning("TheOldReader: Login failed, cannot get articles.")
            return []
        
        try:
            real_feed_id = feed_id
            params = {"output": "json", "n": 50}

            if feed_id.startswith("unread:"):
                real_feed_id = feed_id[7:]
                params["xt"] = "user/-/state/com.google/read"
            elif feed_id.startswith("read:"):
                real_feed_id = feed_id[5:]
                params["it"] = "user/-/state/com.google/read"

            if real_feed_id == "all":
                stream_id = "user/-/state/com.google/reading-list"
            elif real_feed_id.startswith("category:"):
                label = real_feed_id.split(":", 1)[1]
                stream_id = f"user/-/label/{label}"
            else:
                stream_id = real_feed_id
            
            # Use 's' parameter for stream ID to avoid path encoding issues with TheOldReader
            url = f"{self.base_url}/stream/contents"
            params["s"] = stream_id
            
            log.debug(f"TheOldReader: Fetching articles for {stream_id} -> {url} params={params}")
            resp = requests.get(url, headers=self._headers(), params=params)
            log.debug(f"TheOldReader: Article fetch status: {resp.status_code}. Final URL: {resp.url}")
            resp.raise_for_status()
            data = resp.json()
            
            items = data.get("items", [])
            log.info(f"TheOldReader: Found {len(items)} items in API response.")
            
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
                pub_timestamp = item.get("published")
                date = "0001-01-01 00:00:00"
                if pub_timestamp:
                    try:
                        dt = datetime.fromtimestamp(int(pub_timestamp), timezone.utc)
                        date = utils.format_datetime(dt)
                        log.debug(f"TheOldReader: Parsed date from {pub_timestamp} to {date}")
                    except Exception as date_e:
                        log.debug(f"TheOldReader: Date parsing error for {pub_timestamp}: {date_e}. Falling back to normalize_date.")
                        date = utils.normalize_date(
                            str(pub_timestamp),
                            item.get("title", ""),
                            content,
                            item.get("alternate", [{}])[0].get("href", "")
                        )
                else:
                    log.debug("TheOldReader: 'published' field missing. Falling back to normalize_date.")
                    date = utils.normalize_date(
                        "",
                        item.get("title", ""),
                        content,
                        item.get("alternate", [{}])[0].get("href", "")
                    )
                log.debug(f"TheOldReader: Final article date for '{item.get('title', 'N/A')[:30]}...': {date}")
                
                chapters = chapters_map.get(article_id, [])

                articles.append(Article(
                    id=article_id,
                    feed_id=item.get("origin", {}).get("streamId", feed_id),
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
            log.info(f"TheOldReader: Returning {len(articles)} processed articles.")
            return articles
        except requests.exceptions.HTTPError as he:
            log.error(f"TheOldReader Articles HTTP Error: {he.response.status_code} - {he.response.text[:200]}")
            return []
        except Exception as e:
            log.exception(f"TheOldReader Articles General Error: {e}")
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
        except:
            return False

    def add_feed(self, url: str, category: str = None) -> bool:
        if not self._login(): return False
        from core.discovery import get_ytdlp_feed_url, discover_feed
        real_url = get_ytdlp_feed_url(url) or discover_feed(url) or url
        try:
            requests.post(f"{self.base_url}/subscription/edit", headers=self._headers(), data={
                "s": f"feed/{real_url}",
                "ac": "subscribe",
                "t": category or ""
            })
            return True
        except:
            return False

    def remove_feed(self, feed_id: str) -> bool:
        if not self._login(): return False
        try:
            requests.post(f"{self.base_url}/subscription/edit", headers=self._headers(), data={
                "s": feed_id,
                "ac": "unsubscribe"
            })
            return True
        except:
            return False

    def get_categories(self) -> List[str]:
        if not self._login(): return []
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
            log.exception(f"TheOldReader Get Categories Error: {e}")
            return []

    def add_category(self, title: str) -> bool:
        return True

    def rename_category(self, old_title: str, new_title: str) -> bool:
        if not self._login(): return False
        try:
            source = f"user/-/label/{old_title}"
            dest = f"user/-/label/{new_title}"
            resp = requests.post(f"{self.base_url}/rename-tag", headers=self._headers(), data={
                "s": source,
                "dest": dest
            })
            return resp.ok
        except Exception as e:
            log.exception(f"TheOldReader Rename Category Error: {e}")
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
            log.exception(f"TheOldReader Delete Category Error: {e}")
            return False