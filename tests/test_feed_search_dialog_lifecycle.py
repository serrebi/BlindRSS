import os
import sys
import threading

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import gui.dialogs as dialogs


class _ExplodingCtrl:
    def Enable(self):
        raise AssertionError("UI control should not be touched after close")

    def SetLabel(self, _label):
        raise AssertionError("UI control should not be touched after close")

    def SetFocus(self):
        raise AssertionError("UI control should not be touched after close")

    def InsertItem(self, *_args, **_kwargs):
        raise AssertionError("UI control should not be touched after close")

    def SetItem(self, *_args, **_kwargs):
        raise AssertionError("UI control should not be touched after close")


class _DeletedCtrl:
    def Enable(self):
        raise RuntimeError("wrapped C/C++ object of type SearchCtrl has been deleted")

    def SetLabel(self, _label):
        raise RuntimeError("wrapped C/C++ object deleted")

    def SetFocus(self):
        raise RuntimeError("wrapped C/C++ object deleted")

    def InsertItem(self, *_args, **_kwargs):
        raise RuntimeError("wrapped C/C++ object deleted")

    def SetItem(self, *_args, **_kwargs):
        raise RuntimeError("wrapped C/C++ object deleted")


class _Host:
    _on_search_complete = dialogs.FeedSearchDialog._on_search_complete

    def __init__(self):
        self._stop_event = threading.Event()
        self.search_ctrl = _ExplodingCtrl()
        self.search_btn = _ExplodingCtrl()
        self.status_lbl = _ExplodingCtrl()
        self.results_list = _ExplodingCtrl()
        self.results_data = []


def test_feed_search_on_search_complete_returns_when_dialog_closed():
    host = _Host()
    host._stop_event.set()

    host._on_search_complete([{"title": "x", "provider": "YouTube", "detail": "", "url": "u"}])

    assert host.results_data == []


def test_feed_search_on_search_complete_swallows_deleted_widget_error():
    host = _Host()
    host.search_ctrl = _DeletedCtrl()
    host.search_btn = _DeletedCtrl()
    host.status_lbl = _DeletedCtrl()
    host.results_list = _DeletedCtrl()

    host._on_search_complete([{"title": "x", "provider": "YouTube", "detail": "", "url": "u"}])

    # No exception is the regression check.
    assert host.results_data == []
