from PyInstaller.utils.hooks import copy_metadata

hiddenimports = ["_webrtcvad"]

datas = []
for dist_name in ("webrtcvad", "webrtcvad-wheels", "webrtcvad_wheels"):
    try:
        datas += copy_metadata(dist_name)
        break
    except Exception:
        continue

