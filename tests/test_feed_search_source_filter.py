import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import gui.dialogs as dialogs


class _Host:
    _SOURCE_ALL = dialogs.FeedSearchDialog._SOURCE_ALL
    _build_search_targets = dialogs.FeedSearchDialog._build_search_targets
    _is_url_like_term = staticmethod(dialogs.FeedSearchDialog._is_url_like_term)

    def _search_itunes(self, term, queue):
        _ = (term, queue)

    def _search_gpodder(self, term, queue):
        _ = (term, queue)

    def _search_feedly(self, term, queue):
        _ = (term, queue)

    def _search_youtube_channels(self, term, queue):
        _ = (term, queue)

    def _search_newsblur(self, term, queue):
        _ = (term, queue)

    def _search_reddit(self, term, queue):
        _ = (term, queue)

    def _search_fediverse(self, term, queue):
        _ = (term, queue)

    def _search_feedsearch(self, term, queue):
        _ = (term, queue)

    def _search_blindrss(self, term, queue):
        _ = (term, queue)

    def _search_mastodon(self, term, queue):
        _ = (term, queue)

    def _search_bluesky(self, term, queue):
        _ = (term, queue)

    def _search_piefed(self, term, queue):
        _ = (term, queue)

    def _search_lemmy(self, term, queue):
        _ = (term, queue)


def _target_names(targets):
    return [name for name, _ in targets]


def test_feed_search_all_sources_excludes_url_only_targets_for_keyword():
    host = _Host()
    targets = host._build_search_targets("blue sky", host._SOURCE_ALL)

    assert _target_names(targets) == [
        "iTunes",
        "gPodder",
        "Feedly",
        "YouTube",
        "NewsBlur",
        "Reddit",
        "Fediverse",
    ]


def test_feed_search_all_sources_includes_url_only_targets_for_url_like_terms():
    host = _Host()
    targets = host._build_search_targets("example.com", host._SOURCE_ALL)

    assert _target_names(targets) == [
        "iTunes",
        "gPodder",
        "Feedly",
        "YouTube",
        "NewsBlur",
        "Reddit",
        "Fediverse",
        "Feedsearch",
        "BlindRSS",
    ]


def test_feed_search_bluesky_only_source_is_supported():
    host = _Host()
    targets = host._build_search_targets("tech", "bluesky")

    assert _target_names(targets) == ["Bluesky"]


def test_feed_search_feedsearch_selection_still_applies_url_term_guard():
    host = _Host()
    targets = host._build_search_targets("keyword", "feedsearch")

    assert targets == []
