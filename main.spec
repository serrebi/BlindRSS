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
    'pkg_resources.py2_warn',
]

try:
    import webrtcvad  # noqa: F401
    hiddenimports.append('webrtcvad')
except Exception:
    pass

for pkg in packages_to_collect:
    d, b, h = collect_all(pkg)
    datas.extend(d)
    binaries.extend(b)
    hiddenimports.extend(h)

# Include update helper script in the app directory.
helper_path = os.path.join(os.getcwd(), 'update_helper.bat')
if os.path.isfile(helper_path):
    datas.append((helper_path, '.'))

# Add VLC plugins
datas.append((os.path.join(vlc_path, 'plugins'), 'plugins'))

# Add VLC assets (locales, Lua scripts, HRTF data)
for asset_dir in ('lua', 'locale', 'hrtfs'):
    asset_path = os.path.join(vlc_path, asset_dir)
    if os.path.isdir(asset_path):
        datas.append((asset_path, asset_dir))

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[os.path.join(os.getcwd(), 'hooks')],
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
    console=False, # Use debug mode to show a console when needed
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
