import subprocess
import sys
import importlib.metadata
import shutil
import platform
import os


def _run_quiet(cmd, timeout=900):
    """Run command quietly; swallow errors."""
    creationflags = 0
    if platform.system().lower() == "windows" and hasattr(subprocess, "CREATE_NO_WINDOW"):
        creationflags = subprocess.CREATE_NO_WINDOW
    try:
        subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            creationflags=creationflags,
            check=False,
        )
    except Exception:
        pass


def _maybe_add_windows_path():
    """Add common VLC/ffmpeg install locations to PATH for this process."""
    if platform.system().lower() != "windows":
        return
    candidates = [
        r"C:\Program Files\VideoLAN\VLC",
        r"C:\Program Files (x86)\VideoLAN\VLC",
        r"C:\Program Files\ffmpeg\bin",
        r"C:\Program Files (x86)\ffmpeg\bin",
    ]
    current = os.environ.get("PATH", "")
    extras = [p for p in candidates if os.path.isdir(p) and p not in current]
    if extras:
        os.environ["PATH"] = os.pathsep.join(extras + [current])


def ensure_media_tools():
    """
    Best-effort silent install of VLC and ffmpeg system-wide if missing.
    Tries platform package managers (winget/brew/apt/pacman/dnf/zypper).
    """
    def has(cmd):
        return shutil.which(cmd) is not None or shutil.which(f"{cmd}.exe") is not None

    vlc_present = has("vlc")
    ff_present = has("ffmpeg")

    # Try to fix PATH first (helps when installed but not on PATH)
    _maybe_add_windows_path()
    if has("vlc"):
        vlc_present = True
    if has("ffmpeg"):
        ff_present = True

    if vlc_present and ff_present:
        return

    sys_name = platform.system().lower()

    if sys_name == "windows":
        # winget IDs: VideoLAN.VLC and FFmpeg.FFmpeg (community repo)
        if not vlc_present:
            _run_quiet([
                "winget", "install", "-e", "--id", "VideoLAN.VLC",
                "--silent", "--accept-package-agreements", "--accept-source-agreements"
            ])
        if not ff_present:
            _run_quiet([
                "winget", "install", "-e", "--id", "FFmpeg.FFmpeg",
                "--silent", "--accept-package-agreements", "--accept-source-agreements"
            ])
        _maybe_add_windows_path()
        return

    if sys_name == "darwin":
        brew = shutil.which("brew")
        if brew:
            if not vlc_present:
                _run_quiet([brew, "install", "--cask", "vlc"])
            if not ff_present:
                _run_quiet([brew, "install", "ffmpeg"])
        return

    # Linux family
    def install_with(cmds):
        for c in cmds:
            _run_quiet(c)

    if shutil.which("apt-get") or shutil.which("apt"):
        base = "apt-get" if shutil.which("apt-get") else "apt"
        cmds = []
        if not vlc_present or not ff_present:
            cmds.append([base, "update", "-y"])
        pkgs = []
        if not vlc_present:
            pkgs.append("vlc")
        if not ff_present:
            pkgs.append("ffmpeg")
        if pkgs:
            cmds.append([base, "install", "-y"] + pkgs)
        install_with(cmds)
        return

    if shutil.which("pacman"):
        pkgs = []
        if not vlc_present:
            pkgs.append("vlc")
        if not ff_present:
            pkgs.append("ffmpeg")
        if pkgs:
            install_with([["pacman", "-Syu", "--noconfirm"] + pkgs])
        return

    if shutil.which("dnf"):
        pkgs = []
        if not vlc_present:
            pkgs.append("vlc")
        if not ff_present:
            pkgs.append("ffmpeg")
        if pkgs:
            install_with([["dnf", "install", "-y"] + pkgs])
        return

    if shutil.which("zypper"):
        pkgs = []
        if not vlc_present:
            pkgs.append("vlc")
        if not ff_present:
            pkgs.append("ffmpeg")
        if pkgs:
            install_with([["zypper", "--non-interactive", "install"] + pkgs])
        return

def check_and_install_dependencies():
    """
    Checks for required packages and installs/updates them silently if missing.
    """
    required = {'yt-dlp', 'wxpython', 'feedparser', 'requests', 'beautifulsoup4', 'python-dateutil', 'mutagen', 'python-vlc'}
    # pkg_resources is deprecated; use importlib.metadata instead
    installed = set()
    for dist in importlib.metadata.distributions():
        name_val = dist.metadata.get("Name") or dist.name
        if name_val:
            installed.add(name_val.lower())
    missing = required - installed

    if missing:
        # print(f"Missing dependencies: {missing}. Installing...")
        try:
            subprocess.check_call(
                [sys.executable, '-m', 'pip', 'install', *missing],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
        except Exception:
            pass # Fail silently as requested

    # Always try to update yt-dlp specifically
    try:
        subprocess.check_call(
            [sys.executable, '-m', 'pip', 'install', '--upgrade', 'yt-dlp'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
    except Exception:
        pass

    # Media backends (system packages)
    try:
        ensure_media_tools()
    except Exception:
        pass
