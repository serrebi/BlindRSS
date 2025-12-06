# BlindRSS Architecture & Dev Guide

## System Overview
**Stack:** Python 3.13, wxPython (GUI), SQLite (Storage), Feedparser/Requests.
**Entry:** `main.py` -> `core.factory` -> `gui.mainframe`.
**Build:** PyInstaller (`main.spec` -> `dist/BlindRSS.exe`).

## File Structure & Responsibilities
*   **`main.py`**: Bootstrap. Initializes `ConfigManager`, `RSSProvider`, `MainFrame`. **Async Startup:** Uses `threading` to load GUI while feeds fetch.
*   **`core/`**
    *   `db.py`: `sqlite3` wrapper. `init_db()` (includes schemas for `feeds`, `articles`, `chapters`, `categories`), `get_connection()`.
    *   `utils.py`: **CRITICAL**. 
        *   `HEADERS`: Browser-like User-Agent to prevent blocking.
        *   `normalize_date(raw, title, content, url)`: Robust parsing. **Priority:** Title > URL > Feed Date > Content.
        *   `get_chapters_batch(ids)`: Optimized batch fetch for lists.
    *   `factory.py`: Instantiates providers. Calls `init_db` globally.
    *   `config.py`: JSON config manager. Paths relative to EXE if frozen.
*   **`gui/`**
    *   `mainframe.py`: Main window. 
        *   **Threads:** `_refresh_feeds_worker` (startup), `_manual_refresh_thread` (F5).
        *   **Tray:** Minimizes to tray via `EVT_ICONIZE`.
    *   `player.py`: `wx.media` implementation. 
        *   **Async Load:** Opens window immediately, loads chapters in background.
        *   **Fallback:** Downloads to temp file if streaming fails.
        *   **Safety:** Forces playback if state lags.
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

### 3. Media Playback
*   **Instant Open:** Player window shows immediately.
*   **Chapters:** Fetched via `utils.fetch_and_store_chapters` (background) or `get_chapters_batch`.
*   **Resiliency:** If `Load()` fails or times out, downloads media to temp file and plays locally.

## Operational Mandates
1.  **User-Agent:** ALWAYS use `core.utils.safe_requests_get` or `core.utils.HEADERS`.
2.  **Dates:** Use `core.utils.normalize_date`. Trust Title/URL dates over Feed metadata if discrepancies exist.
3.  **Performance:** Use `get_chapters_batch` for lists. Never loop DB queries in UI threads.
4.  **Naming:** App is **BlindRSS**.