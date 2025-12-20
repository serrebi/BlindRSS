You are a python expert skilled in yt-dlp, ffmpeg,  and rss.
# BlindRSS Architecture & Dev Guide

## System Overview
**Stack:** Python 3.13, wxPython (GUI), SQLite (Storage), Feedparser/Requests.
**Entry:** `main.py` -> `core.factory` -> `gui.mainframe`.
**Build:** PyInstaller (`main.spec` -> `dist/BlindRSS.exe`).
**Build Notes (2025-12-19):**
*   **Meticulous Submodule Analysis:** `main.spec` now performs an exhaustive collection of all transient and direct dependencies using `collect_all`.
*   **Expanded Collection:** `packages_to_collect` expanded to include `pyatv`, `pychromecast`, `async_upnp_client`, `trafilatura`, `yt_dlp`, `aiohttp`, `zeroconf`, `pydantic`, `lxml`, `readability`, `sgmllib`, `six`, `soupsieve`, `xmltodict`, `defusedxml`, `didl_lite`, `ifaddr`, `langcodes`, and `language_data`.
*   **Metadata & TLS:** Preserves metadata for `metadata_packages` (discovery support) and explicitly bundles `certifi` CA bundles for secure requests.
*   **Specialized Hooks:** Utilizes `yt-dlp`'s internal PyInstaller hook system to maintain extractor functionality.
*   **Portable Executable:** Optimized for Windows with `build.bat` handling venv setup and artifact staging (`config.json`, `README.md`).
*   **Rebuild:** Run `.\build.bat` to generate `BlindRSS.exe` in root and `dist/`.

## File Structure & Responsibilities
*   **`main.py`**: Bootstrap. Initializes `ConfigManager`, `RSSProvider`, `MainFrame`. **Async Startup:** Uses `threading` to load GUI while feeds fetch.
*   **`core/`**
    *   `db.py`: `sqlite3` wrapper. `init_db()` (includes schemas for `feeds`, `articles`, `chapters`, `categories`), `get_connection()`.
    *   `utils.py`: **CRITICAL**.
        *   `HEADERS`: Browser-like User-Agent to prevent blocking.
        *   `normalize_date(raw, title, content, url)`: Robust parsing. **Priority:** Title > URL > Feed Date > Content.
        *   `get_chapters_batch(ids)`: Optimized batch fetch for lists.
    *   `range_cache_proxy.py`: **VLC Streaming Proxy (Local)**.
        *   **Purpose:** Terminates VLC's HTTP requests locally. Caches media to disk for instant seeking.
        *   **Thread Safety:** Instantiates isolated `requests.Session` per operation (probe/fetch/stream) to prevent SSL/socket race conditions.
        *   **Optimization:** Resolves redirects *once* at startup (`probe`) to kill latency. Aborts background downloads if user seeks away (>2MB jump). Saves partial chunks on interruption.
    *   `stream_proxy.py`: **Casting Proxy (Network)**.
        *   **Purpose:** Serves media to external devices (Chromecast/DLNA). Binds `0.0.0.0`.
        *   **Features:** Header forwarding (for auth/anti-hotlink), MPEG-TS to HLS remuxing via `ffmpeg` (for Chromecast compatibility), local file serving.
    *   `article_extractor.py`: **Full-Text Fetcher**.
        *   **Engine:** `trafilatura` (primary) + `BeautifulSoup` (fallback).
        *   **Logic:** Follows pagination (`next` links), merges multi-page text, strips ZDNET-specific boilerplate.
    *   `casting.py`: **Unified Casting**.
        *   **Protocols:** Chromecast (`pychromecast`), DLNA/UPnP (`async_upnp_client`), AirPlay (`pyatv`).
        *   **Manager:** Discovers devices on background thread, unifies playback/control interfaces.
    *   `dependency_check.py`: **Auto-Setup**.
        *   **Checks:** `yt-dlp` (auto-download binary), `ffmpeg`, `vlc`.
        *   **Installers:**
            *   **Windows:** `winget`.
            *   **Linux:** `apt`, `dnf`, `yum`, `pacman`, `zypper` (handles `sudo`).
            *   **macOS:** `brew`.
    *   `factory.py`: Instantiates providers. Calls `init_db` globally.
    *   `config.py`: JSON config manager. Paths relative to EXE if frozen.
*   **`gui/`**
    *   `mainframe.py`: Main window.
        *   **Threads:** `_refresh_feeds_worker` (startup), `_manual_refresh_thread` (F5).
        *   **Tray:** Minimizes to tray via `EVT_ICONIZE`.
    *   `player.py`: `wx.media` implementation.
        *   **Proxy Integration:** Routes URLs through `127.0.0.1` proxy for aggressive caching.
        *   **Seeking:** Custom debounce logic (`_apply_seek_time_ms`) prevents UI stutter.
        *   **Async Load:** Opens window immediately, loads chapters in background.
    *   `hotkeys.py`: **Global Input**.
        *   **Filter:** `wx.EventFilter` to catch media keys (Play/Pause, Vol, Seek) app-wide, even when focus is in dialogs.
    *   **`tray.py`**: System tray icon (`BlindRSSTrayIcon`) with Context Menu (Restore, Refresh, Media Controls, Volume).
    *   `dialogs.py`: Add Feed, Settings, Podcast Search logic.
*   **`providers/`**
    *   `base.py`: Abstract `RSSProvider`.
    *   `local.py`: **Optimized**.
        *   `refresh`: Uses `ThreadPoolExecutor` (20 workers) for parallel fetch. Uses `If-None-Match`/`If-Modified-Since` conditional GET.
        *   `get_articles`: Uses batch chapter fetching.
    *   `theoldreader.py`: Uses `?s=...` param for Stream IDs (fixes URL encoding bugs). Robust login/logging.
    *   `miniflux.py`, `inoreader.py`, `bazqux.py`: Implement standard interface + batch chapter fetching.

## Data Model (`rss.db`)
*   **`feeds`**: `id` (UUID/String), `url`, `title`, `category`, `icon_url`, `etag`, `last_modified`.
*   **`articles`**: `id`, `feed_id`, `title`, `url`, `content`, `date` (fmt: YYYY-MM-DD HH:MM:SS), `is_read`, `media_url`, `media_type`.
    *   Indices on `feed_id`, `is_read`, `date`.
*   **`chapters`**: `id`, `article_id`, `start` (float seconds), `title`, `href`.
*   **`categories`**: `id`, `title`.

## Key Workflows

### 1. Feed Refresh
*   **Parallel:** `LocalProvider` spawns threads. Each has own DB connection.
*   **Conditional:** Checks HTTP 304 Not Modified to skip parsing.
*   **Parsing:** `feedparser` + `BeautifulSoup` (chapters).
*   **Date Logic:** strict normalization. If parsed date != stored date, **Force Update**.

### 2. UI & Threading
*   **Startup:** `MainFrame.__init__` -> `refresh_feeds` (Thread) -> `_update_tree` (MainThread via `wx.CallAfter`).
*   **Navigation:** `on_tree_select` -> Background fetch articles -> Populate List.
*   **Tray:** Main window hides on minimize. Tray icon remains. Context menu allows control without window.

### 3. Media Playback & Caching
*   **Instant Open:** Player window shows immediately.
*   **Proxy Cache (Local):**
    *   URLs flow through `RangeCacheProxy`.
    *   **Partial Chunks:** Interrupted downloads are saved (not deleted) to allow instant rewind.
    *   **Redirects:** Resolved once. Player connects to final URL.
*   **Casting (Network):**
    *   Uses `StreamProxy` to serve media to devices.
    *   **Transcoding:** Remuxes MPEG-TS -> HLS for Chromecast if needed.
    *   **Headers:** Forwards headers via proxy URL params to bypass hotlink protection on devices.

### 4. Article Extraction
*   **Trigger:** User selects article with no content or requests "Full Text".
*   **Process:** `article_extractor.extract_full_article`.
*   **Logic:** Fetches URL -> Detects Pagination -> Merges text -> Cleans boilerplate.
*   **Fallback:** If fetch fails, falls back to RSS description/content.

## Operational Mandates
1.  **User-Agent:** ALWAYS use `core.utils.safe_requests_get` or `core.utils.HEADERS`.
2.  **Dates:** Use `core.utils.normalize_date`. Trust Title/URL dates over Feed metadata if discrepancies exist.
3.  **Performance:** Use `get_chapters_batch` for lists. Never loop DB queries in UI threads.
4.  **Network Safety:** In `RangeCacheProxy`, **NEVER** share `requests.Session` objects across threads. Instantiate fresh per-request.
5.  **Naming:** App is **BlindRSS**.
Tests scripts are in the /tests directory. Add new ones to it if you need to test something.