import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.discovery import is_ytdlp_supported


class YtDlpSupportDetectionTests(unittest.TestCase):
    def test_article_page_with_embeds_is_not_auto_supported(self) -> None:
        # 9to5mac pages commonly embed YouTube/audio players. We should not treat
        # arbitrary article URLs as playable media just because yt-dlp could
        # extract an embedded player after downloading the webpage.
        self.assertFalse(
            is_ytdlp_supported("https://9to5mac.com/2025/12/30/some-article/")
        )

    def test_youtube_is_supported(self) -> None:
        self.assertTrue(is_ytdlp_supported("https://www.youtube.com/watch?v=dQw4w9WgXcQ"))

    def test_random_site_is_not_supported(self) -> None:
        self.assertFalse(is_ytdlp_supported("https://example.com/some/path"))

    def test_non_http_scheme_is_not_supported(self) -> None:
        self.assertFalse(is_ytdlp_supported("ftp://example.com/video"))


if __name__ == "__main__":
    unittest.main()
