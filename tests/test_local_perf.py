
import pytest
import sqlite3
import time
import os
from unittest.mock import MagicMock, patch
from providers.local import LocalProvider
from core.db import init_db, get_connection
from core.models import Feed

# Mock feedparser response
class MockDict(dict):
    def __getattr__(self, name):
        if name in self:
            return self[name]
        raise AttributeError(name)

class MockEntry(MockDict):
    def __init__(self, i):
        self['id'] = f"item-{i}"
        self['title'] = f"Title {i}"
        self['link'] = f"http://example.com/item-{i}"
        self['published'] = "2023-01-01 12:00:00"
        self['content'] = [MockDict({"value": "Content"})]
        self['enclosures'] = []
        # No need to manually set attributes due to __getattr__

class MockFeed:
    def __init__(self):
        self.entries = [MockEntry(i) for i in range(100)]
        self.feed = {"title": "Mock Feed"}
        self.bozo = False

@pytest.fixture
def provider(tmp_path):
    # Setup temporary DB
    db_path = tmp_path / "rss.db"
    with patch("core.db.DB_FILE", str(db_path)):
        # Initialize DB
        init_db()
        config = {"feed_timeout_seconds": 1, "feed_retry_attempts": 0}
        p = LocalProvider(config)
        yield p

def test_refresh_performance(provider):
    # Add a feed
    provider.add_feed("http://example.com/feed.xml")
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT id FROM feeds")
    feed_id = c.fetchone()[0]
    c.execute("UPDATE feeds SET url = ? WHERE id = ?", ("http://example.com/feed.xml", feed_id))
    conn.commit()
    conn.close()

    # Mock fetching
    with patch("core.utils.safe_requests_get") as mock_get, \
         patch("feedparser.parse") as mock_parse, \
         patch("core.utils.fetch_and_store_chapters") as mock_chapters:
        
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b"xml"
        mock_resp.text = "xml"
        mock_resp.headers = {}
        mock_get.return_value = mock_resp
        
        mock_parse.return_value = MockFeed()
        
        # Run refresh
        start_time = time.time()
        provider.refresh_feed(feed_id)
        duration = time.time() - start_time
        
        print(f"Refresh took {duration:.4f}s")
        
        # Verify articles inserted
        conn = get_connection()
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM articles")
        count = c.fetchone()[0]
        conn.close()
        
        assert count == 100
        
        # Verify fetch_and_store_chapters called with cursor
        assert mock_chapters.call_count == 100
        # Check that cursor was passed (keyword argument)
        args, kwargs = mock_chapters.call_args
        assert "cursor" in kwargs
        assert kwargs["cursor"] is not None

if __name__ == "__main__":
    # Manually run if executed as script
    pass
