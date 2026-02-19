import pytest
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

wx = pytest.importorskip("wx")
pytest.importorskip("vlc")

from gui.player import PlayerFrame


class _DummyChoice:
    def __init__(self, selection: int = 0, client_data: dict | None = None):
        self._selection = int(selection)
        self._client_data = client_data or {0: {"start": 0.0}}

    def GetSelection(self):
        return int(self._selection)

    def GetClientData(self, idx):
        return self._client_data.get(int(idx))

    def SetSelection(self, idx):
        self._selection = int(idx)


class _DummyEvent:
    def __init__(self):
        self.skipped = False

    def Skip(self):
        self.skipped = True


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


class _HotkeysStub:
    def __init__(self):
        self.calls = []

    def handle_ctrl_key(self, event, actions):
        key = int(event.GetKeyCode())
        self.calls.append(key)
        action = actions.get(key)
        if action is not None:
            action()
            return True
        return False


class _HotkeysAlwaysFalse:
    def __init__(self):
        self.calls = []

    def handle_ctrl_key(self, event, actions):
        self.calls.append(int(event.GetKeyCode()))
        _ = actions
        return False


def test_on_chapter_select_keeps_keyboard_browse_safe_when_closeup_supported():
    class _Frame:
        def __init__(self):
            self.chapter_choice = _DummyChoice(selection=2)
            self._chapter_closeup_supported = True
            self._chapter_pending_idx = None
            self.commit_calls = 0

        def _commit_chapter_selection(self):
            self.commit_calls += 1

    frame = _Frame()
    PlayerFrame.on_chapter_select(frame, None)

    assert frame._chapter_pending_idx == 2
    assert frame.commit_calls == 0


def test_on_chapter_select_commits_when_closeup_event_unavailable():
    class _Frame:
        def __init__(self):
            self.chapter_choice = _DummyChoice(selection=1)
            self._chapter_closeup_supported = False
            self._chapter_pending_idx = None
            self.commit_calls = 0

        def _commit_chapter_selection(self):
            self.commit_calls += 1

    frame = _Frame()
    PlayerFrame.on_chapter_select(frame, None)

    assert frame._chapter_pending_idx == 1
    assert frame.commit_calls == 1


def test_on_chapter_closeup_commits_selected_chapter():
    class _Frame:
        def __init__(self):
            self.commit_calls = 0

        def _commit_chapter_selection(self):
            self.commit_calls += 1

    frame = _Frame()
    event = _DummyEvent()

    PlayerFrame.on_chapter_closeup(frame, event)

    assert frame.commit_calls == 1
    assert event.skipped is True


def test_commit_chapter_selection_dedupes_back_to_back_commits():
    class _Frame:
        def __init__(self):
            self.chapter_choice = _DummyChoice(selection=0, client_data={0: {"start": 12.5}})
            self.is_casting = False
            self._chapter_last_commit_idx = None
            self._chapter_last_commit_ts = 0.0
            self.seek_calls = []
            self.note_calls = 0
            self.save_calls = 0

        def _note_user_seek(self):
            self.note_calls += 1

        def _apply_seek_time_ms(self, target_ms, force=False, reason=None):
            self.seek_calls.append((int(target_ms), bool(force), reason))

        def _schedule_resume_save_after_seek(self, delay_ms=0):
            self.save_calls += 1

    frame = _Frame()

    PlayerFrame._commit_chapter_selection(frame)
    PlayerFrame._commit_chapter_selection(frame)

    assert frame.seek_calls == [(12500, True, "chapter")]
    assert frame.note_calls == 1
    assert frame.save_calls == 1


def test_jump_to_chapter_index_selects_and_commits():
    class _Frame:
        def __init__(self):
            self.current_chapters = [{"start": 0.0}, {"start": 20.0}]
            self.chapter_choice = _DummyChoice(selection=0)
            self._chapter_pending_idx = None
            self.committed = 0

        def _commit_chapter_selection(self):
            self.committed += 1

    frame = _Frame()
    PlayerFrame._jump_to_chapter_index(frame, 1)

    assert frame.chapter_choice.GetSelection() == 1
    assert frame._chapter_pending_idx == 1
    assert frame.committed == 1


def test_on_char_hook_shortcuts_move_between_chapters():
    class _Frame:
        def __init__(self):
            self.calls = []

        def _is_focus_in_chapter_choice(self):
            return False

        def prev_chapter(self):
            self.calls.append("prev")

        def next_chapter(self):
            self.calls.append("next")

    frame = _Frame()

    left_evt = _DummyKeyEvent(wx.WXK_LEFT, ctrl=True, shift=True)
    PlayerFrame.on_char_hook(frame, left_evt)
    right_evt = _DummyKeyEvent(wx.WXK_RIGHT, ctrl=True, shift=True)
    PlayerFrame.on_char_hook(frame, right_evt)

    assert frame.calls == ["prev", "next"]
    assert left_evt.skipped is False
    assert right_evt.skipped is False


def test_on_char_hook_ctrl_arrows_trigger_volume_and_seek_actions():
    class _Frame:
        def __init__(self):
            self.calls = []
            self.volume_step = 7
            self.seek_back_ms = 11000
            self.seek_forward_ms = 15000
            self._media_hotkeys = _HotkeysStub()

        def _is_focus_in_chapter_choice(self):
            return False

        def is_audio_playing(self):
            return True

        def adjust_volume(self, delta):
            self.calls.append(("volume", int(delta)))

        def seek_relative_ms(self, delta):
            self.calls.append(("seek", int(delta)))

    frame = _Frame()

    PlayerFrame.on_char_hook(frame, _DummyKeyEvent(wx.WXK_UP, ctrl=True))
    PlayerFrame.on_char_hook(frame, _DummyKeyEvent(wx.WXK_DOWN, ctrl=True))
    PlayerFrame.on_char_hook(frame, _DummyKeyEvent(wx.WXK_LEFT, ctrl=True))
    PlayerFrame.on_char_hook(frame, _DummyKeyEvent(wx.WXK_RIGHT, ctrl=True))

    assert frame.calls == [
        ("volume", 7),
        ("volume", -7),
        ("seek", -11000),
        ("seek", 15000),
    ]


def test_on_char_hook_enter_commits_chapter_when_choice_has_focus():
    class _Frame:
        def __init__(self):
            self.commits = 0

        def _is_focus_in_chapter_choice(self):
            return True

        def _commit_chapter_selection(self):
            self.commits += 1

    frame = _Frame()
    PlayerFrame.on_char_hook(frame, _DummyKeyEvent(wx.WXK_RETURN))
    PlayerFrame.on_char_hook(frame, _DummyKeyEvent(wx.WXK_NUMPAD_ENTER))
    assert frame.commits == 2


def test_on_char_hook_ctrl_up_down_fallback_runs_when_hotkeys_returns_false():
    class _Frame:
        def __init__(self):
            self.calls = []
            self.volume_step = 4
            self.seek_back_ms = 10000
            self.seek_forward_ms = 10000
            self._media_hotkeys = _HotkeysAlwaysFalse()

        def _is_focus_in_chapter_choice(self):
            return False

        def has_media_loaded(self):
            return True

        def adjust_volume(self, delta):
            self.calls.append(("volume", int(delta)))

        def seek_relative_ms(self, delta):
            self.calls.append(("seek", int(delta)))

    frame = _Frame()
    PlayerFrame.on_char_hook(frame, _DummyKeyEvent(wx.WXK_UP, ctrl=True))
    PlayerFrame.on_char_hook(frame, _DummyKeyEvent(wx.WXK_DOWN, ctrl=True))

    assert frame.calls == [("volume", 4), ("volume", -4)]
