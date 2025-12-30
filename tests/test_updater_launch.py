import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch


class UpdaterLaunchTests(unittest.TestCase):
    def test_launch_update_helper_uses_helper_directory_as_cwd(self) -> None:
        from core import updater

        helper_dir = tempfile.mkdtemp(prefix="blindrss_helper_test_")
        helper_path = os.path.join(helper_dir, "update_helper.bat")
        try:
            with open(helper_path, "w", encoding="utf-8") as f:
                f.write("@echo off\n")

            with patch("core.updater.subprocess.Popen", return_value=MagicMock()) as popen:
                ok, msg = updater._launch_update_helper(helper_path, 1234, r"C:\Install", r"C:\Stage")
                self.assertTrue(ok, msg)
                _args, kwargs = popen.call_args
                self.assertEqual(kwargs.get("cwd"), helper_dir)
        finally:
            try:
                import shutil

                shutil.rmtree(helper_dir, ignore_errors=True)
            except Exception:
                pass


if __name__ == "__main__":
    unittest.main()

