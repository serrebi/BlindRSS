import unittest
from urllib.parse import urlparse

import core.range_cache_proxy as rcp


class LocalSeekingProxyTests(unittest.TestCase):
    def setUp(self):
        rcp._RANGE_PROXY_SINGLETON = None
        self.proxy = rcp.get_range_cache_proxy(
            cache_dir=None,
            prefetch_kb=64,
            inline_window_kb=64,
            background_download=False,
            background_chunk_kb=64,
            initial_burst_kb=128,
            initial_inline_prefetch_kb=32,
        )
        self.proxy.start()

    def tearDown(self):
        try:
            self.proxy.stop()
        except Exception:
            pass
        rcp._RANGE_PROXY_SINGLETON = None

    def test_health_endpoint_is_ready(self):
        base = self.proxy.base_url
        parsed = urlparse(base)
        self.assertEqual(parsed.hostname, "127.0.0.1")
        # A quick sanity check that /health responds
        import http.client

        conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=3)
        conn.request("GET", "/health")
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        resp.read()
        conn.close()

    def test_parse_range_header_clamps(self):
        # When total length is known, clamp end; when unknown, keep request end.
        self.assertEqual(rcp._parse_range_header("bytes=0-100", 50), (0, 49))
        self.assertEqual(rcp._parse_range_header("bytes=100-200", None), (100, 200))
        self.assertEqual(rcp._parse_range_header("bytes=200-", 500), (200, 499))

    def test_proxify_returns_local_media_url(self):
        proxied = self.proxy.proxify("http://example.com/audio.mp3")
        parsed = urlparse(proxied)
        self.assertEqual(parsed.hostname, "127.0.0.1")
        self.assertTrue(parsed.path.endswith("/media"))
        self.assertIn("id=", parsed.query)


if __name__ == "__main__":
    unittest.main()
