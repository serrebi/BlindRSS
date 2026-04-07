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

    def test_discover_feed_prefers_page_specific_alternate_feed_over_site_feed(self) -> None:
        html = """
        <html><head>
          <link rel="alternate" type="application/rss+xml" title="The Verge" href="/rss/index.xml" />
          <link rel="alternate" type="application/rss+xml" title="Vergecast" href="/rss/the-vergecast/index.xml" />
        </head><body></body></html>
        """

        with patch("core.discovery.utils.safe_requests_get", return_value=_DummyResp(html)):
            out = discovery.discover_feed("https://www.theverge.com/the-vergecast")

        self.assertEqual(out, "https://www.theverge.com/rss/the-vergecast/index.xml")

    def test_discover_feeds_orders_page_specific_alternate_feed_first(self) -> None:
        html = """
        <html><head>
          <link rel="alternate" type="application/rss+xml" title="The Verge" href="/rss/index.xml" />
          <link rel="alternate" type="application/rss+xml" title="Vergecast" href="/rss/the-vergecast/index.xml" />
        </head><body></body></html>
        """

        with patch("core.discovery.utils.safe_requests_get", return_value=_DummyResp(html)):
            with patch("core.discovery.utils.safe_requests_head", return_value=_DummyHeadResp(404, "text/html")):
                feeds = discovery.discover_feeds("https://www.theverge.com/the-vergecast")

        self.assertGreaterEqual(len(feeds), 2)
        self.assertEqual(feeds[0], "https://www.theverge.com/rss/the-vergecast/index.xml")
        self.assertIn("https://www.theverge.com/rss/index.xml", feeds)

    def test_discover_feed_ignores_generic_json_alternate_links(self) -> None:
        html = """
        <html><head>
          <link rel="alternate" type="application/json" href="/wp-json/wp/v2/pages/2042942" />
          <link rel="alternate" type="application/rss+xml" href="/feed.xml" />
        </head><body></body></html>
        """

        with patch("core.discovery.utils.safe_requests_get", return_value=_DummyResp(html)):
            out = discovery.discover_feed("https://example.com/tech")

        self.assertEqual(out, "https://example.com/feed.xml")


if __name__ == "__main__":
    unittest.main()

