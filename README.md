# BlindRSS

A vibe coded accessible, modern RSS and Podcast player.

## Features
*   **Screen Reader Friendly:** Uses standard controls.
*   **Fast:** Instant startup and parallel feed refreshing.
*   **Podcast Player:** Supports chapters. Auto-downloads if streaming fails.
*   **Tray Icon:** Minimizes to system tray. Right-click for media controls.
*   **Smart Dates:** Fixes incorrect dates by reading titles 

## Keyboard Shortcuts

### Main Window
*   **F6**: Cycle focus (Feeds -> Articles -> Content).
*   **Ctrl + N**: Add Feed.
*   **Delete**: Remove Feed/Category.
*   **Ctrl + P**: Open Player.
*   **F5** or **Ctrl + R**: Refresh all feeds.
*   **Alt + F4**: Minimize to tray.

### Player
*   **Space**: Play / Pause.
*   **Ctrl + Left/Right**: Seek 10s.
*   **Ctrl + Up/Down**: Volume.
*   **Enter**: Play selected chapter.
*   **Escape**: Hide player.

## How to Use
1.  Run `BlindRSS.exe`.
2.  **Add Feed**: Press `Ctrl+N`. Paste URL.
3.  **Read**: Arrow keys to select feed, then Tab or F6 to article list. Enter to open.
4.  **Settings**: Go to **Edit > Settings** to switch providers (Local, TheOldReader, etc).
5.  **Minimize**: Closing or minimizing sends it to the notification area (System Tray). Press Enter on the tray icon to restore.

## Files
*   `BlindRSS.exe`: The application.
*   `rss.db`: Database containing your feeds and articles.
*   `config.json`: Configuration file.
*   Keep these files together. They are created when you run the program for the first time.
## Support:
I do not really provide support, but if you notice a bug or you want to request a feature, feel free to open a new issue, and I can try to help you out.