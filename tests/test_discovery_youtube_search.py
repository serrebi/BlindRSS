import os
import sys
import unittest
import json
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core import discovery


class YouTubeSearchConversionTests(unittest.TestCase):
    def test_youtube_search_entries_to_channel_feeds_dedupes_video_hits(self) -> None:
        entries = [
            {
                "id": "UC4gD0czpXVv_LpADTSU624g",
                "title": "Clownfish TV",
                "channel": "Clownfish TV",
                "channel_id": "UC4gD0czpXVv_LpADTSU624g",
                "channel_url": "https://www.youtube.com/channel/UC4gD0czpXVv_LpADTSU624g",
                "uploader_id": "@ClownfishTV",
                "url": "https://www.youtube.com/channel/UC4gD0czpXVv_LpADTSU624g",
            },
            {
                "id": "video-1",
                "title": "Some video",
                "channel": "Clownfish TV",
                "channel_id": "UC4gD0czpXVv_LpADTSU624g",
                "channel_url": "https://www.youtube.com/channel/UC4gD0czpXVv_LpADTSU624g",
                "uploader_id": "@ClownfishTV",
                "url": "https://www.youtube.com/watch?v=abc",
            },
        ]

        out = discovery._youtube_search_entries_to_channel_feeds(entries, limit=10)

        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["title"], "Clownfish TV")
        self.assertEqual(
            out[0]["url"],
            "https://www.youtube.com/feeds/videos.xml?channel_id=UC4gD0czpXVv_LpADTSU624g",
        )
        self.assertIn("@ClownfishTV", out[0]["detail"])

    def test_youtube_search_entries_to_channel_feeds_uses_channel_url_fallback(self) -> None:
        entries = [
            {
                "title": "Example Creator",
                "channel": "Example Creator",
                "channel_url": "https://www.youtube.com/@ExampleCreator",
                "uploader_id": "@ExampleCreator",
                "url": "https://www.youtube.com/watch?v=xyz",
            }
        ]

        with patch(
            "core.discovery.get_ytdlp_feed_url",
            return_value="https://www.youtube.com/feeds/videos.xml?channel_id=UCEXAMPLE123",
        ) as mock_get_feed:
            out = discovery._youtube_search_entries_to_channel_feeds(entries, limit=10)

        mock_get_feed.assert_called_once_with("https://www.youtube.com/@ExampleCreator")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["title"], "Example Creator")
        self.assertEqual(out[0]["url"], "https://www.youtube.com/feeds/videos.xml?channel_id=UCEXAMPLE123")

    def test_search_youtube_channels_accepts_zero_returncode(self) -> None:
        payload = {
            "entries": [
                {
                    "channel": "Clownfish TV",
                    "channel_id": "UC4gD0czpXVv_LpADTSU624g",
                    "channel_url": "https://www.youtube.com/channel/UC4gD0czpXVv_LpADTSU624g",
                    "uploader_id": "@ClownfishTV",
                    "url": "https://www.youtube.com/watch?v=abc",
                }
            ]
        }
        fake_proc = SimpleNamespace(returncode=0, stdout=json.dumps(payload).encode("utf-8"))

        with patch("core.discovery.subprocess.run", return_value=fake_proc):
            results = discovery.search_youtube_channels("clownfishtv", limit=5, timeout=10)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "Clownfish TV")
        self.assertIn("channel_id=UC4gD0czpXVv_LpADTSU624g", results[0]["url"])


if __name__ == "__main__":
    unittest.main()
