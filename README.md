# BlindRSS
A vibed rss reader for screen reader users.
## 1. What this app does
- A Blind-friendly RSS and podcast reader with keyboard-first controls.
- Lets you subscribe, refresh, read, and play feeds without sighted UI tricks.
- Plays podcasts with chapters support, playback speed, and skip silence support.
- Keeps feeds, articles, and settings locally so you control your data.
- Supports a few different services, like TheOldReader, Miniflux, and more!
## 2. Main features
- Works with screen readers; uses standard wxPython controls.
- Fast startup; refreshes feeds in parallel and uses HTTP cache headers.
- Plays audio via VLC; supports YouTube links and enclosure media.
- Optional skip-silence filter (needs ffmpeg); adjustable playback speed.
- Podcast chapters shown and keyboard-selectable.
- Tray icon for quick restore, refresh, and player controls.
- OPML import/export; feed discovery helps with YouTube channel/user/playlist URLs.
- Categories to group feeds; unread counts shown in the tree.

## 3. What you need before you start
- Windows with a terminal and your screen reader (NVDA/JAWS) running.
- Python 3.13 installed globally (`python --version`).
- Pip available (`pip --version`).
- VLC media player installed system-wide.
- Internet access for feeds and installing dependencies.
- ffmpeg installed if you want the skip-silence option (optional).

## 4. Installing BlindRSS
1. Open a terminal in the folder where you want the app.
2. Clone or unzip the project so you have the `BlindRSS` folder.
3. Change into that folder: `cd BlindRSS`.
4. Install Python packages: `pip install -r requirements.txt`.
5. Install VLC (if not already). Install ffmpeg if you want skip-silence.

## 5. Running the app
- From the project folder run: `python main.py`.
- Windows shortcut: `start_blindrss.bat` does the same after basic checks.
- The main window appears; closing or minimizing sends it to the tray. Use the tray icon menu to restore or control playback.

## 6. Basic usage
- Refresh feeds: press `F5` or `Ctrl+R`.
- Add a feed: `Ctrl+N`, paste URL, choose category, press OK. You can also search podcasts from that dialog.
- Remove a feed or category: select it and press `Delete`.
- Switch focus: `F6` cycles Feeds → Articles → Content.
- Open an article: select in the list and press `Enter`. Non-media items open in your web browser.
- Play podcast media: double-activate the article (Enter/space) or use the player window (`Ctrl+P`). Chapters list accepts Enter to jump.
- Tray controls: right-click tray icon for Restore, Refresh, Play/Pause/Stop, Volume presets.
- OPML import/export: File menu has Import and Export. Import can target a category.
- Settings: Tools → Settings to change refresh interval, playback speed, skip-silence, and active provider (local, Miniflux, TheOldReader, Inoreader, BazQux).

## 7. Configuration
- Config file: `config.json` in the same folder as the app (or next to the executable if packaged). Created on first run.
- Key options:
  - `refresh_interval` (seconds between automatic refreshes).
  - `active_provider` (`local`, `miniflux`, `theoldreader`, `inoreader`, `bazqux`).
  - `skip_silence` (true/false, requires ffmpeg).
  - `playback_speed` (float, e.g., 1.0).
  - Provider credentials (API keys, email/password, tokens).
- Database: `rss.db` holds feeds, articles, chapters. Keep it with the config if you move the app.
- Edit settings through the Settings dialog; only edit the JSON by hand if you must.

## 8. Troubleshooting
- Feeds not updating: check internet, press `F5`, and confirm the feed URL is valid. For hosted services, verify credentials in Settings.
- App will not start: confirm Python 3.13 and `pip install -r requirements.txt` succeeded; make sure VLC is installed.
- Audio fails or is silent: ensure VLC is installed; disable skip-silence or install ffmpeg.
- OPML import errors: look for `opml_debug_*.log` in the app folder.
- Need errors on screen: run from a terminal (`python main.py`) to read console messages.
- Reset settings: close the app and delete `config.json` (you will lose saved settings).

## 9. Build and run (simple, global packages)
1. Install Python 3.13 globally.
2. From the project folder: `pip install -r requirements.txt`.
3. Run: `python main.py`.
4. Optional: build an executable with `pip install pyinstaller` then `pyinstaller main.spec` (outputs `dist/BlindRSS.exe`).
