# -*- mode: python ; coding: utf-8 -*-
import os
import sys
from PyInstaller.utils.hooks import collect_all, collect_data_files, copy_metadata

datas = []
binaries = []
hiddenimports = [
    "webrtcvad",
    "mutagen",
    "requests",
    "feedparser",
    "bs4",
    "dateutil",
    "vlc",
    "pkg_resources",
    "setuptools",
    "packaging",
    "charset_normalizer",
    "idna",
    "urllib3",
    "brotli",
]

# Specifically exclude modules that cause noise or aren't needed on Windows
excludes = [
    "urllib3.contrib.emscripten",
    "js",
    "tkinter",
    "tcl",
    "tk",
    "matplotlib",
    "PIL",
    "PyQt5",
    "PyQt6",
    "PySide2",
    "PySide6",
]

def _collect(package: str):
    try:
        # Check if package is installed before collecting
        import importlib.util
        if importlib.util.find_spec(package) is None:
            return
        pkg_datas, pkg_bins, pkg_hidden = collect_all(package)
        datas.extend(pkg_datas)
        binaries.extend(pkg_bins)
        hiddenimports.extend(pkg_hidden)
    except Exception:
        pass


# Essential packages that need full collection
packages_to_collect = [
    "pyatv",
    "pychromecast",
    "zeroconf",
    "async_upnp_client",
    "trafilatura",
    "yt_dlp",
    "requests",
    "bs4",
    "feedparser",
    "mutagen",
    "lxml",
    "courlan",
    "htmldate",
    "justext",
    "aiohttp",
    "aiosignal",
    "frozenlist",
    "multidict",
    "yarl",
    "async_timeout",
    "idna",
    "certifi",
    "urllib3",
    "charset_normalizer",
    "brotli",
    "html5lib",
    "dateutil",
    "webrtcvad",
    "vlc",
    "packaging",
    "cryptography",
    "soupsieve",
    "xmltodict",
    "defusedxml",
    "didl_lite",
    "ifaddr",
    "langcodes",
    "language_data",
    "pydantic",
    "readability",
    "sgmllib",
    "six",
]

for pkg in packages_to_collect:
    _collect(pkg)

# Add metadata for packages that use pkg_resources or importlib.metadata for discovery
metadata_packages = [
    "pychromecast",
    "yt_dlp",
    "trafilatura",
    "pyatv",
    "webrtcvad",
    "requests",
    "vlc",
    "setuptools",
    "cryptography",
    "pydantic",
    "async_upnp_client",
    "zeroconf",
    "aiohttp"
]

for pkg in metadata_packages:
    try:
        datas += copy_metadata(pkg)
    except Exception:
        pass

# TLS root bundle for requests/trafilatura when frozen
datas += collect_data_files("certifi")

# Use yt-dlp's bundled PyInstaller hooks to keep extractor plugins intact
try:
    import yt_dlp.__pyinstaller as yt_pyi
    hook_dirs = yt_pyi.get_hook_dirs()
except Exception:
    hook_dirs = []

a = Analysis(
    ["main.py"],
    pathex=[os.path.abspath(".")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=hook_dirs,
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="BlindRSS",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
