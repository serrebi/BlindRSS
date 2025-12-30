import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core import discovery


class _DummyResp:
    def __init__(self, text: str, url: str = "https://example.com/") -> None:
        self.text = text
        self.url = url
        self.status_code = 200
        self.headers = {"Content-Type": "text/html; charset=utf-8"}

    def raise_for_status(self) -> None:
        return None


class _DummyHeadResp:
    def __init__(self, status_code: int = 200, content_type: str = "application/rss+xml") -> None:
        self.status_code = status_code
        self.headers = {"Content-Type": content_type}


class DiscoverFeedsTests(unittest.TestCase):
    def test_discover_feeds_returns_existing_feed_url(self) -> None:
        self.assertEqual(discovery.discover_feeds("https://example.com/feed.xml"), ["https://example.com/feed.xml"])

    def test_discover_feeds_collects_link_and_anchor_candidates(self) -> None:
        html = """
        <html><head>
          <link rel="alternate" type="application/rss+xml" href="/feed.xml" />
          <link rel="alternate" type="application/atom+xml" href="https://example.com/atom.xml" />
        </head><body>
          <a href="/rss">RSS</a>
          <a href="/not-a-feed">No</a>
        </body></html>
        """

        def head_side_effect(url: str, **_kwargs):
            if url.endswith("/rss.xml"):
                return _DummyHeadResp(200, "application/rss+xml")
            return _DummyHeadResp(404, "text/html")

        with patch("core.discovery.utils.safe_requests_get", return_value=_DummyResp(html)):
            with patch("core.discovery.utils.safe_requests_head", side_effect=head_side_effect):
                feeds = discovery.discover_feeds("https://example.com")

        self.assertIn("https://example.com/feed.xml", feeds)
        self.assertIn("https://example.com/atom.xml", feeds)
        self.assertIn("https://example.com/rss", feeds)
        # From common-path probing via HEAD (stubbed above).
        self.assertIn("https://example.com/rss.xml", feeds)

    def test_discover_feeds_deduplicates(self) -> None:
        html = """
        <html><head>
          <link rel="alternate" type="application/rss+xml" href="/feed.xml" />
          <link rel="alternate" type="application/rss+xml" href="/feed.xml" />
        </head><body>
          <a href="/feed.xml">Feed</a>
        </body></html>
        """
        with patch("core.discovery.utils.safe_requests_get", return_value=_DummyResp(html)):
            with patch("core.discovery.utils.safe_requests_head", return_value=_DummyHeadResp(404, "text/html")):
                feeds = discovery.discover_feeds("https://example.com")

        self.assertEqual(feeds.count("https://example.com/feed.xml"), 1)


if __name__ == "__main__":
    unittest.main()

