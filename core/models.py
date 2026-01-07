from core.utils import parse_datetime_utc

class Article:
    def __init__(self, title: str, url: str, content: str, date: str, author: str, feed_id: str, is_read: bool = False, id: str = None, media_url: str = None, media_type: str = None, chapters: list = None, is_favorite: bool = False):
        self.id = id or url  # Use URL as ID if generic ID not provided
        self.title = title
        self.url = url
        self.content = content
        self.date = date
        self.author = author
        self.feed_id = feed_id
        self.is_read = is_read
        self.is_favorite = bool(is_favorite)
        self.media_url = media_url
        self.media_type = media_type
        self.chapters = chapters or []
        
        self.timestamp = 0.0
        if self.date:
            dt = parse_datetime_utc(self.date)
            if dt:
                self.timestamp = dt.timestamp()

class Feed:
    def __init__(self, id: str, title: str, url: str, category: str = "Uncategorized", icon_url: str = None):
        self.id = id
        self.title = title
        self.url = url
        self.category = category
        self.icon_url = icon_url
        self.unread_count = 0
