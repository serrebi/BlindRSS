# BlindRSS

Screen-reader friendly vibed RSS & Podcast player. Local, fast, and keyboard-first.

## Quick Start

### Windows (Easy)
1. Download `BlindRSS.exe` from releases.
2. Run `BlindRSS.exe`.

### Build it yourself (PyInstaller)
1. Install Python 3.12+ and requirements: `pip install -r requirements.txt`.
2. Ensure **VLC Media Player (64-bit)** is installed at `C:\Program Files\VideoLAN\VLC`.
3. Run the build script: `.\build.bat`.
4. The application will be generated in `dist/BlindRSS/`. Run `dist/BlindRSS/BlindRSS.exe`.

### Python (All OS)
1. Install Python 3.12.
2. Run: `pip install -r requirements.txt`
3. Run: `python main.py`

*Note: On first launch, the app automatically attempts to install system-level dependencies like **VLC** and **FFmpeg** (using Winget on Windows, Brew on macOS, or the system package manager on Linux). It also auto-downloads tools like `yt-dlp` if missing.*
