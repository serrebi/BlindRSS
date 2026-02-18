import os
import sys

# Ensure repo root on path
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from core import windows_integration as winint


def test_desktop_dir_uses_windows_shell_path(monkeypatch):
    monkeypatch.setattr(winint, "is_windows", lambda: True)
    monkeypatch.setattr(
        winint,
        "_run_powershell",
        lambda _script, timeout_s=10: (True, r"C:\Users\admin\OneDrive\Desktop"),
    )
    path = winint._desktop_dir()
    assert path == r"C:\Users\admin\OneDrive\Desktop"


def test_desktop_dir_falls_back_to_onedrive_env_when_shell_fails(monkeypatch):
    monkeypatch.setattr(winint, "is_windows", lambda: True)
    monkeypatch.setattr(winint, "_run_powershell", lambda _script, timeout_s=10: (False, "nope"))
    monkeypatch.setenv("OneDrive", r"C:\Users\admin\OneDrive")
    monkeypatch.delenv("OneDriveConsumer", raising=False)
    monkeypatch.setattr(
        winint.os.path,
        "isdir",
        lambda p: str(p).replace("/", "\\").lower() == r"c:\users\admin\onedrive\desktop",
    )
    path = winint._desktop_dir()
    assert path.replace("/", "\\").lower() == r"c:\users\admin\onedrive\desktop"

