# BlindRSS Build and Release (Windows)

This is the only approved workflow for packaging and publishing BlindRSS.

## Commands

- Iterative local build: `.\build.bat build`
- Official release: `.\build.bat release`
- No-change preview: `.\build.bat dry-run`

## Mandatory Release Rule

Always publish with `.\build.bat release`.

Do not publish manually from GitHub UI/CLI without running this script first. The script is required because it:

- Creates `BlindRSS-update.json` for auto-updates.
- Computes the release ZIP SHA-256 hash.
- Signs `BlindRSS.exe` when `signtool.exe` is available.
- Bumps `core/version.py`, tags Git, pushes, and creates the GitHub release.

## Prerequisites

- Windows with Python 3.12+ (`python` or `py` on PATH).
- VLC 64-bit installed (expected at `C:\Program Files\VideoLAN\VLC`).
- GitHub CLI (`gh`) authenticated for `release` mode.
- Windows SDK `signtool.exe` for signed builds/releases.
- Network access (the script installs deps and can download `yt-dlp.exe` and `deno.exe`).

## What Each Mode Does

### `build`

- Sets up/uses `.venv`.
- Installs dependencies.
- Runs PyInstaller using `main.spec`.
- Preserves `dist\BlindRSS` user data (`rss.db`, `rss.db-wal`, `rss.db-shm`, `podcasts\`) between iterative builds.
- Signs when possible (or skip with `SKIP_SIGN=1`).
- Produces:
  - `dist\BlindRSS\`
  - `dist\BlindRSS-vX.Y.Z.zip`
  - `BlindRSS.exe` in repo root
  - `BlindRSS.zip` in repo root

### `release`

- Computes next version and bumps `core/version.py`.
- Performs a clean build (wipes `build\` and `dist\`).
- Signs executable.
- Produces:
  - `dist\BlindRSS-vX.Y.Z.zip`
  - `dist\BlindRSS-update.json`
  - `dist\release-notes-vX.Y.Z.md`
- Commits version bump, tags, pushes, and creates GitHub release assets (ZIP + manifest).

### `dry-run`

- Shows next version and planned release steps.
- Does not modify files or Git state.

## Optional Environment Variables

- `SIGNTOOL_PATH`: override default signtool path.
- `SIGN_CERT_THUMBPRINT`: force manifest signing thumbprint value.
- `SKIP_SIGN=1`: skip signing in `build` mode only.

## Typical Usage

```powershell
.\build.bat build
```

See `README.md` for end-user usage and feature overview.
