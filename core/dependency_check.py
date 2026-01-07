import subprocess
import sys
import importlib.metadata
import shutil
import platform
import os
import urllib.request
import contextlib
import time
import tempfile

try:
    import winreg
except Exception:
    winreg = None

def _log(msg):
    """Write to a persistent log file in temp dir for user diagnostics."""
    try:
        t = time.strftime("%Y-%m-%d %H:%M:%S")
        temp_dir = os.environ.get("TEMP", os.environ.get("TMP", tempfile.gettempdir()))
        log_path = os.path.join(temp_dir, "blindrss_dep_check.log")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[{t}] {msg}\n")
    except:
        pass

def _get_startup_info():
    """Helper to get a hidden startup info object for Windows."""
    if platform.system().lower() != "windows":
        return None
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0 # SW_HIDE
    return si

def _run_quiet(cmd, timeout=900):
    """Run command quietly; swallow errors."""
    _log(f"Running command: {' '.join(cmd)}")
    creationflags = 0
    if platform.system().lower() == "windows":
        creationflags = 0x08000000 # CREATE_NO_WINDOW
    try:
        subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
            creationflags=creationflags,
            startupinfo=_get_startup_info(),
            check=False,
        )
    except Exception as e:
        _log(f"Command failed: {e}")

def _maybe_add_windows_path():
    """Meticulously find VLC/ffmpeg and add to PATH for this process."""
    if platform.system().lower() != "windows":
        return
    if winreg is None:
        return
    
    _log("Starting meticulous Windows path search...")
    candidates = set()
    to_add_front = []
    
    # 0. Check app directory
    app_exe = sys.executable if getattr(sys, 'frozen', False) else __file__
    app_dir = os.path.dirname(os.path.abspath(app_exe))
    candidates.add(app_dir)
    candidates.add(os.path.join(app_dir, "bin"))
    
    # 0.1 General Python Scripts folders (WinGet/Store/Installer locations)
    # Use environment variables to avoid hardcoded user names
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if local_app_data:
        # Check common Python versions in Programs/Python
        programs_python = os.path.join(local_app_data, "Programs", "Python")
        if os.path.isdir(programs_python):
            try:
                for d in os.listdir(programs_python):
                    scripts = os.path.join(programs_python, d, "Scripts")
                    if os.path.isdir(scripts):
                        to_add_front.append(scripts)
                        candidates.add(scripts)
            except: pass

    # 1. Common hardcoded paths
    candidates.update([
        r"C:\Program Files\VideoLAN\VLC",
        r"C:\Program Files (x86)\VideoLAN\VLC",
        r"C:\Program Files\ffmpeg\bin",
        r"C:\Program Files (x86)\ffmpeg\bin",
        r"C:\ffmpeg\bin",
        r"C:\tools\ffmpeg\bin",
        r"C:\ProgramData\chocolatey\bin",
        r"C:\Program Files\Common Files\VLC",
        r"C:\vlc",
        r"D:\ffmpeg\bin",
        r"D:\vlc",
    ])
    
    # 1.1 Read System and User PATH from Registry directly and EXPAND them
    for hive, subkey in [(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
                         (winreg.HKEY_CURRENT_USER, r"Environment")]:
        try:
            with winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ) as key:
                p, _ = winreg.QueryValueEx(key, "PATH")
                if p:
                    expanded_p = os.path.expandvars(str(p))
                    for part in expanded_p.split(os.pathsep):
                        part = part.strip('"').strip()
                        if part: candidates.add(part)
        except: pass
    
    # 2. Registry - Specific App Keys
    vlc_registry_paths = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\VideoLAN\VLC"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\VideoLAN\VLC"),
    ]
    
    for hive, subkey in vlc_registry_paths:
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ | view) as key:
                    for val_name in ("InstallDir", "InstallLocation"):
                        try:
                            p, _ = winreg.QueryValueEx(key, val_name)
                            if p: 
                                p_exp = os.path.expandvars(str(p))
                                candidates.add(p_exp)
                                candidates.add(os.path.join(p_exp, "bin"))
                        except: pass
            except: pass

    # 3. Registry - App Paths
    for app in ("vlc.exe", "ffmpeg.exe"):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{app}") as key:
                p, _ = winreg.QueryValueEx(key, "")
                if p:
                    p_exp = os.path.expandvars(str(p))
                    candidates.add(os.path.dirname(p_exp))
        except: pass

    # 4. Registry - Uninstall Keys
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for root in (r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
                     r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"):
            try:
                with winreg.OpenKey(hive, root) as key:
                    for i in range(winreg.QueryInfoKey(key)[0]):
                        try:
                            name = winreg.EnumKey(key, i)
                            with winreg.OpenKey(key, name) as item:
                                try:
                                    disp, _ = winreg.QueryValueEx(item, "DisplayName")
                                    disp_l = str(disp).lower()
                                    if "vlc" in disp_l or "ffmpeg" in disp_l:
                                        loc, _ = winreg.QueryValueEx(item, "InstallLocation")
                                        if loc:
                                            loc_exp = os.path.expandvars(str(loc))
                                            candidates.add(loc_exp)
                                            candidates.add(os.path.join(loc_exp, "bin"))
                                except: pass
                        except: pass
            except: pass

    # 5. User-space (Scoop, WinGet)
    user_p = os.environ.get("USERPROFILE", "")
    if user_p:
        candidates.update([
            os.path.join(user_p, r"scoop\shims"),
            os.path.join(user_p, r"scoop\apps\ffmpeg\current\bin"),
            os.path.join(user_p, r"scoop\apps\vlc\current"),
            os.path.join(user_p, r"AppData\Local\Microsoft\WinGet\Packages"),
            os.path.join(user_p, r"AppData\Local\Microsoft\WinGet\Links"),
        ])

    # 6. Scan WinGet Packages root specifically
    if local_app_data:
        winget_root = os.path.join(local_app_data, "Microsoft", "WinGet", "Packages")
        if os.path.isdir(winget_root):
            try:
                for d in os.listdir(winget_root):
                    if "vlc" in d.lower() or "ffmpeg" in d.lower():
                        base = os.path.join(winget_root, d)
                        candidates.add(base)
                        for root, dirs, files in os.walk(base):
                            if "ffmpeg.exe" in files or "vlc.exe" in files or "libvlc.dll" in files:
                                candidates.add(root)
                                break
            except: pass

    # 7. Add to process PATH
    current_path = os.environ.get("PATH", "")
    current_paths_lower = [p.lower().strip('"').strip() for p in current_path.split(os.pathsep) if p.strip()]
    
    to_add = []
    # Add front-prioritized candidates first
    for p in to_add_front:
        if p and os.path.isdir(p):
            p_abs = os.path.abspath(p)
            if p_abs.lower() not in current_paths_lower:
                to_add.append(p_abs)
                current_paths_lower.append(p_abs.lower())

    for p in candidates:
        if p and os.path.isdir(p):
            p_abs = os.path.abspath(p)
            if p_abs.lower() not in current_paths_lower:
                to_add.append(p_abs)
                current_paths_lower.append(p_abs.lower())
    
    if to_add:
        _log(f"Adding to PATH: {';'.join(to_add)}")
        os.environ["PATH"] = os.pathsep.join(to_add + [current_path])
    
    # 8. Explicitly set VLC lib path if found
    for p in candidates:
        if p and os.path.isdir(p):
            dll = os.path.join(p, "libvlc.dll")
            if os.path.isfile(dll):
                _log(f"Found libvlc.dll at {dll}")
                os.environ["PYTHON_VLC_LIB_PATH"] = dll
                if hasattr(os, "add_dll_directory"):
                    try:
                        os.add_dll_directory(p)
                    except: pass
                break

def _should_check_updates(marker_name):
    """Throttles specific checks to once every 24 hours."""
    temp_dir = os.environ.get("TEMP", os.environ.get("TMP", tempfile.gettempdir()))
    marker = os.path.join(temp_dir, f"blindrss_last_{marker_name}.txt")
    try:
        if os.path.isfile(marker):
            mtime = os.path.getmtime(marker)
            if (time.time() - mtime) < 86400:
                _log(f"Throttle active for {marker_name} (last check: {time.ctime(mtime)})")
                return False
        with open(marker, "w") as f:
            f.write(str(time.time()))
    except: pass
    return True

def has(cmd, version_arg="-version"):
    """Robust verification of executable availability."""
    # 1. Check PATH via shutil.which
    exe = shutil.which(cmd) or shutil.which(f"{cmd}.exe")
    if exe:
        if os.path.isfile(exe):
            _log(f"Found {cmd} at {exe} via which.")
            return True

    if platform.system().lower() == "windows":
        try:
            # 2. Use 'where' command as a fallback
            res = subprocess.run(
                ["where", cmd],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                text=True,
                creationflags=0x08000000,
                startupinfo=_get_startup_info(),
                timeout=5
            )
            if res.returncode == 0 and res.stdout:
                first_found = res.stdout.splitlines()[0].strip()
                if os.path.isfile(first_found):
                    _log(f"'where' found {cmd} at {first_found}")
                    return True
        except: pass
    return False

def _winget_has_package(package_id):
    """Check if winget thinks the package is already installed."""
    if platform.system().lower() != "windows":
        return False
    try:
        # Use 'winget list --id <id>' to check for existence
        res = subprocess.run(
            ["winget", "list", "--id", package_id, "--source", "winget"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            creationflags=0x08000000,
            startupinfo=_get_startup_info(),
            timeout=15
        )
        if res.returncode == 0 and package_id.lower() in res.stdout.decode('utf-8', 'ignore').lower():
            _log(f"Winget reports package {package_id} is already installed.")
            return True
    except: 
        pass
    return False

def check_media_tools_status():
    """Returns tuple (vlc_missing, ffmpeg_missing)."""
    _maybe_add_windows_path()
    
    vlc_present = has("vlc", "--version")
    if not vlc_present and os.environ.get("PYTHON_VLC_LIB_PATH"):
        if os.path.isfile(os.environ["PYTHON_VLC_LIB_PATH"]):
            vlc_present = True
    if not vlc_present:
        if _winget_has_package("VideoLAN.VLC"):
            vlc_present = True
            
    ff_present = has("ffmpeg", "-version")
    if not ff_present:
        if _winget_has_package("Gyan.FFmpeg"):
            ff_present = True
            
    return (not vlc_present, not ff_present)

def install_media_tools(vlc=True, ffmpeg=True):
    """Installs missing tools via winget (Windows only)."""
    if platform.system().lower() != "windows":
        return

    common_flags = ["--accept-package-agreements", "--accept-source-agreements", "--no-upgrade", "--disable-interactivity"]
    
    if vlc:
        _log("Installing VLC via winget...")
        _run_quiet(["winget", "install", "-e", "--id", "VideoLAN.VLC"] + common_flags)
    if ffmpeg:
        _log("Installing FFmpeg via winget...")
        _run_quiet(["winget", "install", "-e", "--id", "Gyan.FFmpeg"] + common_flags)
    
    _maybe_add_windows_path()

def ensure_media_tools():
    """Robust detection of media tools (Path setup only)."""
    _maybe_add_windows_path()
    # Automatic installation has been moved to interactive prompt in GUI.
    return

def _ensure_yt_dlp_cli():
    """Throttled update/install of yt-dlp binary. Prioritizes working version."""
    if platform.system().lower() != "windows":
        return

    base_dir = os.path.dirname(os.path.abspath(sys.executable)) if getattr(sys, "frozen", False) else os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    bin_dir = os.path.join(base_dir, "bin")
    os.makedirs(bin_dir, exist_ok=True)
    local_exe = os.path.join(bin_dir, "yt-dlp.exe")

    def works(path):
        try:
            res = subprocess.run(
                [path, "--version"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                creationflags=0x08000000,
                startupinfo=_get_startup_info(),
                timeout=5
            )
            return res.returncode == 0
        except:
            return False

    # Check for working yt-dlp
    exe = None

    # 0. Check bundled (sys._MEIPASS) if frozen
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        bundled_bin = os.path.join(sys._MEIPASS, "bin")
        bundled_exe = os.path.join(bundled_bin, "yt-dlp.exe")
        if os.path.isfile(bundled_exe) and works(bundled_exe):
            _log(f"Using bundled yt-dlp at {bundled_exe}")
            # Prepend to PATH immediately so subprocess calls find it
            current_path = os.environ.get("PATH", "")
            if bundled_bin not in current_path:
                os.environ["PATH"] = os.pathsep.join([bundled_bin, current_path])
            return

    if os.path.isfile(local_exe) and works(local_exe):
        exe = local_exe
        _log(f"Using local yt-dlp at {exe}")
    else:
        system_exe = shutil.which("yt-dlp") or shutil.which("yt-dlp.exe")
        if system_exe and works(system_exe):
            exe = system_exe
            _log(f"Using system yt-dlp at {exe}")

    if exe:
        if _should_check_updates("ytdlp_cli_update"):
            _log("Updating yt-dlp CLI...")
            _run_quiet([exe, "-U"])
        _add_bin_to_user_path(bin_dir)
        return

    # Download if missing
    _log("No working yt-dlp CLI found. Downloading standalone...")
    try:
        url = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"
        with contextlib.closing(urllib.request.urlopen(url, timeout=30)) as r, open(local_exe, "wb") as f:
            shutil.copyfileobj(r, f)
        _log(f"Downloaded yt-dlp to {local_exe}")
    except Exception as e:
        _log(f"Failed to download yt-dlp: {e}")
        return
    
    current = os.environ.get("PATH", "")
    if bin_dir not in current.split(os.pathsep):
        os.environ["PATH"] = os.pathsep.join([bin_dir, current])
    _add_bin_to_user_path(bin_dir)

def _add_bin_to_user_path(bin_dir):
    """Persist bin_dir to user PATH."""
    try:
        if platform.system().lower() != "windows":
            return
        if winreg is None:
            return
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment", 0, winreg.KEY_READ | winreg.KEY_SET_VALUE) as key:
            try: existing, _ = winreg.QueryValueEx(key, "PATH")
            except: existing = ""
            if bin_dir in str(existing).split(os.pathsep): return
            new_path = os.pathsep.join([str(existing), bin_dir]) if existing else bin_dir
            winreg.SetValueEx(key, "PATH", 0, winreg.REG_EXPAND_SZ, new_path)
            _log(f"Added {bin_dir} to user PATH registry.")
    except Exception as e:
        _log(f"Failed to add to user PATH registry: {e}")

def check_and_install_dependencies():
    """Main dependency check entry point."""
    _log("--- Dependency Check Started ---")
    if getattr(sys, "frozen", False):
        _maybe_add_windows_path()
        _ensure_yt_dlp_cli()
        try: ensure_media_tools()
        except: pass
        _log("--- Dependency Check Finished (Frozen) ---")
        return

    required = {
        'yt-dlp', 'wxpython', 'feedparser', 'requests', 'beautifulsoup4', 
        'python-dateutil', 'mutagen', 'python-vlc',
        'pychromecast', 'async-upnp-client', 'pyatv', 'trafilatura',
        'webrtcvad', 'brotli', 'html5lib', 'lxml', 'pytest', 'pyinstaller', 'packaging'
    }
    installed = {d.metadata.get("Name", d.name).lower() for d in importlib.metadata.distributions() if d.metadata.get("Name") or d.name}
    missing = required - installed

    if missing:
        _log(f"Missing pip packages: {missing}. Installing...")
        try:
            creationflags = 0
            if platform.system().lower() == "windows":
                creationflags = 0x08000000
            subprocess.check_call(
                [sys.executable, '-m', 'pip', 'install', '--quiet', '--no-python-version-warning', *missing],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                creationflags=creationflags, startupinfo=_get_startup_info()
            )
            _log("Pip install successful.")
        except Exception as e:
            _log(f"Pip install failed: {e}")

    if _should_check_updates("pip_upgrade"):
        _log("Checking for pip self-upgrade...")
        try:
            creationflags = 0
            if platform.system().lower() == "windows":
                creationflags = 0x08000000
            subprocess.check_call(
                [sys.executable, '-m', 'pip', 'install', '--upgrade', '--quiet', 'pip'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                creationflags=creationflags, startupinfo=_get_startup_info()
            )
        except: pass

    if _should_check_updates("ytdlp_pip_upgrade"):
        _log("Checking for yt-dlp pip upgrade...")
        try:
            creationflags = 0
            if platform.system().lower() == "windows":
                creationflags = 0x08000000
            subprocess.check_call(
                [sys.executable, '-m', 'pip', 'install', '--upgrade', '--quiet', '--no-python-version-warning', 'yt-dlp'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                creationflags=creationflags, startupinfo=_get_startup_info()
            )
        except: pass

    try: ensure_media_tools()
    except: pass
    try: _ensure_yt_dlp_cli()
    except: pass
    _log("--- Dependency Check Finished ---")
