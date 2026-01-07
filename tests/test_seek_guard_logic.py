import unittest
from unittest.mock import patch

# Since we can't import wx in a headless environment, we'll mock it
class MockWx:
    class Window:
        pass
    class KeyEvent:
        pass
    class TimerEvent:
        pass
    class Dialog:
        pass
    class Frame:
        pass

with patch.dict('sys.modules', {'wx': MockWx()}):
    from gui.player import _should_reapply_seek


class SeekGuardLogicTests(unittest.TestCase):
    def test_reapply_when_far_and_budget(self):
        self.assertTrue(_should_reapply_seek(10_000, 4_000, 2000, 2))

    def test_no_reapply_when_close(self):
        self.assertFalse(_should_reapply_seek(10_000, 9_100, 2000, 2))

    def test_no_budget_no_reapply(self):
        self.assertFalse(_should_reapply_seek(10_000, 1000, 2000, 0))

    def test_negative_current_triggers_reapply(self):
        self.assertTrue(_should_reapply_seek(5000, -1, 2000, 1))


if __name__ == "__main__":
    unittest.main()
