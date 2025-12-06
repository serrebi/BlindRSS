import abc
from typing import List, Dict, Any, Optional

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

    @abc.abstractmethod
    def mark_read(self, article_id: str) -> bool:
        pass
    
    @abc.abstractmethod
    def add_feed(self, url: str, category: str = None) -> bool:
        pass
    
    @abc.abstractmethod
    def remove_feed(self, feed_id: str) -> bool:
        pass
        
    @abc.abstractmethod
    def import_opml(self, path: str, target_category: str = None) -> bool:
        pass
        
    @abc.abstractmethod
    def export_opml(self, path: str) -> bool:
        pass

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
