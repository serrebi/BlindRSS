import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gui.mainframe as mainframe


class _DummyKeyEvent:
    def __init__(self, key, *, ctrl=False, shift=False, alt=False, meta=False):
        self._key = int(key)
        self._ctrl = bool(ctrl)
        self._shift = bool(shift)
        self._alt = bool(alt)
        self._meta = bool(meta)
        self.skipped = False

    def GetKeyCode(self):
        return int(self._key)

    def ControlDown(self):
        return bool(self._ctrl)

    def ShiftDown(self):
        return bool(self._shift)

    def AltDown(self):
        return bool(self._alt)

    def MetaDown(self):
        return bool(self._meta)

    def Skip(self):
        self.skipped = True


class _DummyHost:
    on_char_hook = mainframe.MainFrame.on_char_hook

    def __init__(self):
        self.tree = object()
        self.list_ctrl = object()
        self.player_window = None
        self._media_hotkeys = None
        self.calls = []
        self._focus = None

    def _get_focused_window(self):
        return self._focus

    def on_edit_feed(self, event):
        self.calls.append(("edit_feed", event))

    def on_find_feed(self, event):
        self.calls.append(("find_feed", event))

    def on_delete_article(self):
        self.calls.append(("delete_article", None))

    def on_remove_feed(self, event):
        self.calls.append(("remove_feed", event))

    def on_article_activate(self, event):
        self.calls.append(("article_activate", event))

    def _make_list_activate_event(self, idx):
        self.calls.append(("make_list_evt", idx))
        return object()


def test_f2_shortcut_opens_edit_feed_when_tree_focused():
    host = _DummyHost()
    host._focus = host.tree
    evt = _DummyKeyEvent(mainframe.wx.WXK_F2)

    host.on_char_hook(evt)

    assert ("edit_feed", None) in host.calls
    assert evt.skipped is False


def test_ctrl_shift_f_shortcut_opens_feed_search():
    host = _DummyHost()
    evt = _DummyKeyEvent(ord("F"), ctrl=True, shift=True)

    host.on_char_hook(evt)

    assert ("find_feed", None) in host.calls
    assert evt.skipped is False

