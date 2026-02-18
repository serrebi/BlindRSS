import base64
import logging
import os
import shutil
import subprocess
import sys
import tempfile

try:
    import winreg
except Exception:  # pragma: no cover - non-Windows envs
    winreg = None


log = logging.getLogger(__name__)

APP_NAME = "BlindRSS"
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def is_windows() -> bool:
    return bool(sys.platform.startswith("win"))


def _quote_cmd_arg(value: str) -> str:
    return subprocess.list2cmdline([str(value or "")]).strip()


def _ps_literal(value: str) -> str:
    return "'" + str(value or "").replace("'", "''") + "'"


def get_launch_parts() -> tuple[str, str, str, str]:
    """Return launch tuple: (target_path, arguments, working_dir, icon_path)."""
    if getattr(sys, "frozen", False):
        exe_path = os.path.abspath(sys.executable)
        return exe_path, "", os.path.dirname(exe_path), exe_path

    python_exe = os.path.abspath(sys.executable or "python")
    pythonw_exe = python_exe
    low = python_exe.lower()
    if low.endswith("python.exe"):
        candidate = python_exe[:-10] + "pythonw.exe"
        if os.path.exists(candidate):
            pythonw_exe = candidate

    script_path = ""
    try:
        if sys.argv and sys.argv[0]:
            script_path = os.path.abspath(sys.argv[0])
    except Exception:
        script_path = ""
    if not script_path:
        script_path = os.path.abspath("main.py")

    args = _quote_cmd_arg(script_path)
    return pythonw_exe, args, os.path.dirname(script_path), python_exe


def build_startup_command() -> str:
    target, args, _working_dir, _icon = get_launch_parts()
    cmd = _quote_cmd_arg(target)
    if args:
        cmd = f"{cmd} {args}"
    return cmd


def set_startup_enabled(enabled: bool, app_name: str = APP_NAME) -> tuple[bool, str]:
    if not is_windows() or winreg is None:
        return False, "Startup registration is only available on Windows."

    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            if bool(enabled):
                winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, build_startup_command())
                return True, "BlindRSS will now start when you sign in to Windows."
            try:
                winreg.DeleteValue(key, app_name)
            except FileNotFoundError:
                pass
            return True, "BlindRSS startup on sign-in has been disabled."
    except Exception as e:
        log.exception("Failed to update Windows startup setting")
        return False, f"Could not update Windows startup setting: {e}"


def _run_powershell(script: str, timeout_s: int = 30) -> tuple[bool, str]:
    if not is_windows():
        return False, "PowerShell integration is only available on Windows."
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    cmd = [
        "powershell",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-EncodedCommand",
        encoded,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=max(5, int(timeout_s)))
    except Exception as e:
        return False, str(e)

    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if proc.returncode == 0:
        return True, out
    return False, err or out or f"PowerShell exited with code {proc.returncode}."


def _create_shortcut(shortcut_path: str, target_path: str, arguments: str, working_dir: str, icon_path: str) -> tuple[bool, str]:
    os.makedirs(os.path.dirname(shortcut_path), exist_ok=True)
    script = "\n".join(
        [
            "$ErrorActionPreference = 'Stop'",
            "$ws = New-Object -ComObject WScript.Shell",
            f"$shortcut = $ws.CreateShortcut({_ps_literal(shortcut_path)})",
            f"$shortcut.TargetPath = {_ps_literal(target_path)}",
            f"$shortcut.Arguments = {_ps_literal(arguments)}",
            f"$shortcut.WorkingDirectory = {_ps_literal(working_dir)}",
            f"$shortcut.IconLocation = {_ps_literal(icon_path)}",
            "$shortcut.Description = 'BlindRSS'",
            "$shortcut.Save()",
        ]
    )
    ok, msg = _run_powershell(script, timeout_s=20)
    if not ok:
        return False, msg
    if not os.path.exists(shortcut_path):
        return False, "Shortcut was not created."
    return True, "OK"


def _taskbar_dir() -> str:
    return os.path.join(
        os.path.expandvars("%APPDATA%"),
        "Microsoft",
        "Internet Explorer",
        "Quick Launch",
        "User Pinned",
        "TaskBar",
    )


def _pin_shortcut_to_taskbar(shortcut_path: str) -> tuple[bool, str]:
    # This is best-effort; modern Windows can hide taskbar pin verbs.
    script = "\n".join(
        [
            "$ErrorActionPreference = 'Stop'",
            f"$path = {_ps_literal(shortcut_path)}",
            "if (-not (Test-Path -LiteralPath $path)) { throw 'Shortcut not found.' }",
            "$shell = New-Object -ComObject Shell.Application",
            "$folder = Split-Path -Path $path",
            "$file = Split-Path -Path $path -Leaf",
            "$item = $shell.NameSpace($folder).ParseName($file)",
            "if (-not $item) { throw 'Unable to load shortcut item.' }",
            "$verbs = @($item.Verbs())",
            "$normalized = $verbs | ForEach-Object { [PSCustomObject]@{ Raw=$_; Name=($_.Name -replace '&','').Trim().ToLowerInvariant() } }",
            "$already = $normalized | Where-Object { $_.Name -like '*unpin from taskbar*' } | Select-Object -First 1",
            "if ($already) { Write-Output 'already-pinned'; exit 0 }",
            "$pin = $normalized | Where-Object { $_.Name -like '*pin to taskbar*' -or $_.Name -like '*taskbarpin*' } | Select-Object -First 1",
            "if ($pin) { $pin.Raw.DoIt(); Write-Output 'pinned'; exit 0 }",
            "exit 1",
        ]
    )
    return _run_powershell(script, timeout_s=20)


def create_shortcuts(
    *,
    desktop: bool = False,
    start_menu: bool = False,
    taskbar: bool = False,
    app_name: str = APP_NAME,
) -> dict[str, tuple[bool, str]]:
    results: dict[str, tuple[bool, str]] = {}
    if not is_windows():
        msg = "Shortcuts are only supported on Windows."
        if desktop:
            results["desktop"] = (False, msg)
        if start_menu:
            results["start_menu"] = (False, msg)
        if taskbar:
            results["taskbar"] = (False, msg)
        return results

    target, args, working_dir, icon_path = get_launch_parts()
    lnk_name = f"{app_name}.lnk"

    if desktop:
        desktop_dir = os.path.join(os.path.expanduser("~"), "Desktop")
        desktop_lnk = os.path.join(desktop_dir, lnk_name)
        results["desktop"] = _create_shortcut(desktop_lnk, target, args, working_dir, icon_path)

    if start_menu:
        start_dir = os.path.join(os.path.expandvars("%APPDATA%"), "Microsoft", "Windows", "Start Menu", "Programs")
        start_lnk = os.path.join(start_dir, lnk_name)
        results["start_menu"] = _create_shortcut(start_lnk, target, args, working_dir, icon_path)

    if taskbar:
        temp_dir = tempfile.mkdtemp(prefix="blindrss_shortcut_")
        try:
            temp_lnk = os.path.join(temp_dir, lnk_name)
            made_temp, made_msg = _create_shortcut(temp_lnk, target, args, working_dir, icon_path)
            if made_temp:
                pinned, pin_msg = _pin_shortcut_to_taskbar(temp_lnk)
                if pinned:
                    results["taskbar"] = (True, "Pinned to taskbar.")
                else:
                    taskbar_lnk = os.path.join(_taskbar_dir(), lnk_name)
                    made_tb, msg_tb = _create_shortcut(taskbar_lnk, target, args, working_dir, icon_path)
                    if made_tb:
                        results["taskbar"] = (
                            True,
                            "Created taskbar shortcut file (pin verb unavailable on this Windows build).",
                        )
                    else:
                        results["taskbar"] = (False, pin_msg or msg_tb)
            else:
                results["taskbar"] = (False, made_msg)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    return results

