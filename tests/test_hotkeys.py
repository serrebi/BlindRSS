import unittest
from unittest.mock import Mock, patch
import time

# Since we can't import wx in a headless environment, we'll mock it
class MockWx:
    class KeyEvent:
        def __init__(self, key_code, control_down=False):
            self._key_code = key_code
            self._control_down = control_down

        def GetKeyCode(self):
            return self._key_code

        def ControlDown(self):
            return self._control_down

        def IsAutoRepeat(self):
            return False

    WXK_LEFT = 1
    WXK_RIGHT = 2
    WXK_CONTROL = 3

    class Timer:
        def __init__(self, owner):
            self._owner = owner
            self._running = False

        def IsRunning(self):
            return self._running

        def Start(self, interval):
            self._running = True

        def Stop(self):
            self._running = False

    class Window:
        def Bind(self, event, handler, source):
            pass

    class TimerEvent:
        pass

    EVT_TIMER = 1

with patch.dict('sys.modules', {'wx': MockWx()}):
    from gui.hotkeys import HoldRepeatHotkeys

class TestHoldRepeatHotkeys(unittest.TestCase):

    def setUp(self):
        self.mock_owner = MockWx.Window()
        self.hotkeys = HoldRepeatHotkeys(
            self.mock_owner,
            hold_delay_s=0.1,
            repeat_interval_s=0.05
        )
        self.action = Mock()

    def test_single_tap(self):
        event = MockWx.KeyEvent(MockWx.WXK_LEFT, control_down=True)
        self.hotkeys.handle_ctrl_key(event, {MockWx.WXK_LEFT: self.action})
        self.action.assert_called_once()

    def test_hold_and_repeat(self):
        event = MockWx.KeyEvent(MockWx.WXK_LEFT, control_down=True)

        # Initial press
        self.hotkeys.handle_ctrl_key(event, {MockWx.WXK_LEFT: self.action})
        self.action.assert_called_once()

        # Simulate time passing for the hold delay
        time.sleep(0.11)
        with patch.object(self.hotkeys, '_combo_is_down', return_value=True):
            self.hotkeys._on_timer(None)

        # At least one more call for the repeat
        self.assertGreater(self.action.call_count, 1)

    def test_release_stops_repeat(self):
        event = MockWx.KeyEvent(MockWx.WXK_LEFT, control_down=True)
        self.hotkeys.handle_ctrl_key(event, {MockWx.WXK_LEFT: self.action})
        self.action.assert_called_once()

        # After release, no more calls
        with patch.object(self.hotkeys, '_combo_is_down', return_value=False):
            # Simulate multiple timer events after release
            for _ in range(10):
                self.hotkeys._on_timer(None)

        self.action.assert_called_once()


if __name__ == '__main__':
    unittest.main()
