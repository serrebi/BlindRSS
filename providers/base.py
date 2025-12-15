import abc
from typing import List, Dict, Any, Optional, Tuple
from core import utils

class Article:
    def __init__(self, title: str, url: str, content: str, date: str, author: str, feed_id: str, is_read: bool = False, id: str = None, media_url: str = None, media_type: str = None, chapters: list = None):
        self.id = id or url  # Use URL as ID if generic ID not provided
        self.title = title
        self.url = url
        self.content = content
        self.date = date
        self.author = author
        self.feed_id = feed_id
        self.is_read = is_read
        self.media_url = media_url
        self.media_type = media_type
        self.chapters = chapters or []

class Feed:
    def __init__(self, id: str, title: str, url: str, category: str = "Uncategorized", icon_url: str = None):
        self.id = id
        self.title = title
        self.url = url
        self.category = category
        self.icon_url = icon_url
        self.unread_count = 0

class RSSProvider(abc.ABC):
    """Abstract base class for RSS providers (Local, Feedly, etc.)"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config

    @abc.abstractmethod
    def get_name(self) -> str:
        pass

    @abc.abstractmethod
    def refresh(self, progress_cb=None) -> bool:
        """
        Triggers a sync/refresh of feeds.
        progress_cb: optional callable accepting a feed-state dict per completed feed.
        """
        pass

    @abc.abstractmethod
    def get_feeds(self) -> List[Feed]:
        pass

    @abc.abstractmethod
    def get_articles(self, feed_id: str) -> List[Article]:
        pass



    def get_articles_page(self, feed_id: str, offset: int = 0, limit: int = 200) -> Tuple[List[Article], int]:
        """Optional pagination helper.

        Providers that can do server-side paging should override this for speed.
        Default implementation calls get_articles() and slices the result.
        """
        articles = self.get_articles(feed_id) or []
        total = len(articles)
        if offset < 0:
            offset = 0
        if limit is None or int(limit) <= 0:
            return [], total
        limit = int(limit)
        return articles[offset:offset + limit], total

    @abc.abstractmethod
    def mark_read(self, article_id: str) -> bool:
        pass

    def mark_read_batch(self, article_ids: List[str]) -> bool:
        """Default implementation: loop over single mark_read."""
        success = True
        for aid in article_ids:
            if not self.mark_read(aid):
                success = False
        return success
    
    @abc.abstractmethod
    def add_feed(self, url: str, category: str = None) -> bool:
        pass
    
    @abc.abstractmethod
    def remove_feed(self, feed_id: str) -> bool:
        pass
        
    def import_opml(self, path: str, target_category: str = None) -> bool:
        """Default implementation using utils.parse_opml and add_feed."""
        count = 0
        for title, url, category in utils.parse_opml(path):
            cat = target_category if target_category else category
            if self.add_feed(url, cat):
                count += 1
        return count > 0
        
    def export_opml(self, path: str) -> bool:
        """Default implementation using get_feeds and utils.write_opml."""
        feeds = self.get_feeds()
        return utils.write_opml(feeds, path)

    @abc.abstractmethod
    def get_categories(self) -> List[str]:
        """Returns a list of category names."""
        pass

    @abc.abstractmethod
    def add_category(self, title: str) -> bool:
        pass

    @abc.abstractmethod
    def rename_category(self, old_title: str, new_title: str) -> bool:
        pass

    @abc.abstractmethod
    def delete_category(self, title: str) -> bool:
        pass
