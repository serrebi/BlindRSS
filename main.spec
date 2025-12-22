# -*- mode: python ; coding: utf-8 -*-

import os
import sys
from PyInstaller.utils.hooks import collect_all

# VLC path - adjust this if VLC is installed elsewhere
vlc_path = r'C:\Program Files\VideoLAN\VLC'
bin_path = os.path.join(os.getcwd(), 'bin')

# Exhaustive collection of dependencies as mentioned in agents.md
packages_to_collect = [
    'pyatv', 'pychromecast', 'async_upnp_client', 'trafilatura', 
    'yt_dlp', 'aiohttp', 'zeroconf', 'pydantic', 'lxml', 
    'readability', 'sgmllib', 'six', 'soupsieve', 'xmltodict', 
    'defusedxml', 'didl_lite', 'ifaddr', 'langcodes', 'language_data',
    'certifi'
]

datas = []
binaries = [
    (os.path.join(vlc_path, 'libvlc.dll'), '.'),
    (os.path.join(vlc_path, 'libvlccore.dll'), '.'),
    (os.path.join(bin_path, 'yt-dlp.exe'), 'bin'),
]
hiddenimports = [
    'vlc',
    'trafilatura',
    'webrtcvad',
    'pkg_resources.py2_warn',
]

for pkg in packages_to_collect:
    d, b, h = collect_all(pkg)
    datas.extend(d)
    binaries.extend(b)
    hiddenimports.extend(h)

# Add VLC plugins
datas.append((os.path.join(vlc_path, 'plugins'), 'plugins'))

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
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
    [],
    exclude_binaries=True,
    name='BlindRSS',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True, # Set to True to see errors if it fails to start
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='BlindRSS',
)
