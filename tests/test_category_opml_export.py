import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gui.mainframe as mainframe
from core.models import Feed


class _ProviderStub:
    def __init__(self, feeds):
        self._feeds = list(feeds or [])

    def get_feeds(self):
        return list(self._feeds)


class _DummyMain:
    _normalize_category_title_for_export = mainframe.MainFrame._normalize_category_title_for_export
    _collect_category_feeds_for_export = mainframe.MainFrame._collect_category_feeds_for_export
    _export_category_opml_to_path = mainframe.MainFrame._export_category_opml_to_path

    def __init__(self, feeds):
        self.provider = _ProviderStub(feeds)


def _feed(feed_id, title, url, category):
    return Feed(id=feed_id, title=title, url=url, category=category)


def test_collect_category_feeds_for_export_filters_exact_category():
    host = _DummyMain(
        [
            _feed("1", "P1", "https://example.com/p1.xml", "Podcasts"),
            _feed("2", "N1", "https://example.com/n1.xml", "News"),
            _feed("3", "P2", "https://example.com/p2.xml", "Podcasts"),
        ]
    )

    feeds = host._collect_category_feeds_for_export("Podcasts")

    assert [f.id for f in feeds] == ["1", "3"]


def test_collect_category_feeds_for_export_treats_blank_as_uncategorized():
    host = _DummyMain(
        [
            _feed("1", "A", "https://example.com/a.xml", ""),
            _feed("2", "B", "https://example.com/b.xml", None),
            _feed("3", "C", "https://example.com/c.xml", "Uncategorized"),
            _feed("4", "D", "https://example.com/d.xml", "Podcasts"),
        ]
    )

    feeds = host._collect_category_feeds_for_export("Uncategorized")

    assert [f.id for f in feeds] == ["1", "2", "3"]


def test_export_category_opml_to_path_uses_filtered_feeds(monkeypatch):
    host = _DummyMain(
        [
            _feed("1", "Pod A", "https://example.com/a.xml", "Podcasts"),
            _feed("2", "News A", "https://example.com/n.xml", "News"),
        ]
    )

    captured = {}

    def _fake_write_opml(feeds, path):
        captured["ids"] = [f.id for f in feeds]
        captured["path"] = path
        return True

    monkeypatch.setattr(mainframe.utils, "write_opml", _fake_write_opml)

    ok, err = host._export_category_opml_to_path("Podcasts", "C:\\tmp\\podcasts.opml")

    assert ok is True
    assert err is None
    assert captured["ids"] == ["1"]
    assert captured["path"] == "C:\\tmp\\podcasts.opml"


def test_export_category_opml_to_path_returns_message_when_empty(monkeypatch):
    host = _DummyMain([_feed("1", "News A", "https://example.com/n.xml", "News")])

    def _unexpected_write_opml(*args, **kwargs):
        raise AssertionError("write_opml should not be called for empty category export")

    monkeypatch.setattr(mainframe.utils, "write_opml", _unexpected_write_opml)

    ok, err = host._export_category_opml_to_path("Podcasts", "C:\\tmp\\podcasts.opml")

    assert ok is False
    assert "No feeds found" in (err or "")
    assert "Podcasts" in (err or "")

