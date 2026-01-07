import wx
import wx.adv
import sys
# import wx.html2 # Removed as per request
import webbrowser
import threading
import time
import os
import re
import logging
from urllib.parse import urlsplit
from bs4 import BeautifulSoup
# from dateutil import parser as date_parser  # Removed unused import
from .dialogs import AddFeedDialog, SettingsDialog, FeedPropertiesDialog, AboutDialog
from .player import PlayerFrame
from .tray import BlindRSSTrayIcon
from .hotkeys import HoldRepeatHotkeys
from providers.base import RSSProvider
from core.config import APP_DIR
from core import utils
from core import article_extractor
from core import updater
from core.version import APP_VERSION
from core import dependency_check
import core.discovery

log = logging.getLogger(__name__)


class MainFrame(wx.Frame):
    def __init__(self, provider: RSSProvider, config_manager):
        style = wx.DEFAULT_FRAME_STYLE
        if config_manager.get("start_maximized", False):
            style |= wx.MAXIMIZE
        super().__init__(None, title="BlindRSS", size=(1000, 700), style=style)
        self.provider = provider
        self.config_manager = config_manager
        self._refresh_guard = threading.Lock()
        self.feed_map = {}
        self.feed_nodes = {}
        self._article_refresh_pending = False
        # View/article cache so switching between nodes doesn't re-index history every time.
        # Keys are feed_id values like: "all", "<feed_id>", "category:<id>".
        self.view_cache = {}
        self._view_cache_lock = threading.Lock()
        self.max_cached_views = int(self.config_manager.get("max_cached_views", 15))

        self.current_feed_id = None
        self._loading_more_placeholder = False
        # Article paging
        self.article_page_size = 400
        self._load_more_inflight = False
        self._load_more_label = "Load more items (Enter)"
        self._loading_label = "Loading more..."
        
        # Create independent player window
        self.player_window = PlayerFrame(self, config_manager)

        # Custom hold-to-repeat for media keys (prevents multi-seek on quick tap)
        self._media_hotkeys = HoldRepeatHotkeys(self, hold_delay_s=2.0, repeat_interval_s=0.12, poll_interval_ms=15)
        
        self._updating_list = False # Flag to ignore selection events during background updates
        self._updating_tree = False # Flag to ignore tree selection events during rebuilds
        self.selected_article_id = None
        self._update_check_inflight = False
        self._update_install_inflight = False

        # Batch refresh progress updates to avoid flooding the UI thread when many feeds refresh in parallel.
        self._refresh_progress_lock = threading.Lock()
        self._refresh_progress_pending = {}
        self._refresh_progress_flush_scheduled = False

        self.init_ui()
        self.init_menus()
        self.init_shortcuts()
        self.Bind(wx.EVT_CHAR_HOOK, self.on_char_hook)
        
        self.tray_icon = BlindRSSTrayIcon(self)
        
        self.Bind(wx.EVT_CLOSE, self.on_close)
        self.Bind(wx.EVT_ICONIZE, self.on_iconize)
        
        # Start background refresh loop (daemon so it can't keep the app alive)
        self.stop_event = threading.Event()
        self.refresh_thread = threading.Thread(target=self.refresh_loop, daemon=True)
        self.refresh_thread.start()
        
        # Initial load
        self.refresh_feeds()
        wx.CallAfter(self._focus_default_control)
        wx.CallLater(15000, self._maybe_auto_check_updates)
        wx.CallAfter(self._check_media_dependencies)

    def _check_media_dependencies(self):
        try:
            missing_vlc, missing_ffmpeg = dependency_check.check_media_tools_status()
            if missing_vlc or missing_ffmpeg:
                msg = "Missing recommended media tools:\n"
                if missing_vlc: msg += "- VLC Media Player (required for playback)\n"
                if missing_ffmpeg: msg += "- FFmpeg (required for some podcasts)\n"
                msg += "\nWould you like to install them automatically (via winget) and add them to PATH?"
                
                if wx.MessageBox(msg, "Install Dependencies", wx.YES_NO | wx.ICON_QUESTION) == wx.YES:
                    self.SetStatusText("Installing dependencies...")
                    # Run in thread to avoid freezing
                    threading.Thread(target=self._install_dependencies_thread, args=(missing_vlc, missing_ffmpeg), daemon=True).start()
        except Exception as e:
            log.error(f"Dependency check failed: {e}")

    def _install_dependencies_thread(self, vlc, ffmpeg):
        try:
            dependency_check.install_media_tools(vlc=vlc, ffmpeg=ffmpeg)
            wx.CallAfter(wx.MessageBox, "Dependencies installed. Please restart the application.", "Success", wx.ICON_INFORMATION)
        except Exception as e:
            wx.CallAfter(wx.MessageBox, f"Installation failed: {e}", "Error", wx.ICON_ERROR)

    def on_about(self, event):
        dlg = AboutDialog(self, APP_VERSION)
        dlg.ShowModal()
        dlg.Destroy()

    def init_ui(self):
        # Main Splitter: Tree vs Content Area
        splitter = wx.SplitterWindow(self)
        
        # Left: Tree (Feeds)
        self.tree = wx.TreeCtrl(splitter, style=wx.TR_DEFAULT_STYLE | wx.TR_HIDE_ROOT | wx.TR_HAS_BUTTONS)
        self.tree.SetName("Feeds Tree")
        self.root = self.tree.AddRoot("Root")
        self.all_feeds_node = self.tree.AppendItem(self.root, "All Feeds")
        
        # Right: Splitter (List + Content)
        right_splitter = wx.SplitterWindow(splitter)
        
        # Top Right: List (Articles)
        self.list_ctrl = wx.ListCtrl(right_splitter, style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
        self.list_ctrl.SetName("Articles List")
        self.list_ctrl.InsertColumn(0, "Title", width=400)
        self.list_ctrl.InsertColumn(1, "Date", width=150)
        self.list_ctrl.InsertColumn(2, "Author", width=150)
        self.list_ctrl.InsertColumn(3, "Status", width=80)
        
        # Bottom Right: Content (No embedded player anymore)
        self.content_ctrl = wx.TextCtrl(right_splitter, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2)
        self.content_ctrl.SetName("Article Content")
        
        right_splitter.SplitHorizontally(self.list_ctrl, self.content_ctrl, 300)
        splitter.SplitVertically(self.tree, right_splitter, 250)
        
        self.Bind(wx.EVT_TREE_SEL_CHANGED, self.on_tree_select, self.tree)
        self.Bind(wx.EVT_CONTEXT_MENU, self.on_tree_context_menu, self.tree)
        
        self.Bind(wx.EVT_LIST_ITEM_SELECTED, self.on_article_select, self.list_ctrl)
        self.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self.on_article_activate, self.list_ctrl)
        self.Bind(wx.EVT_CONTEXT_MENU, self.on_list_context_menu, self.list_ctrl)

        # When tabbing into the content field, load full article text.
        self.content_ctrl.Bind(wx.EVT_SET_FOCUS, self.on_content_focus)

        # Full-text extraction cache (url -> rendered text)
        self._fulltext_cache = {}
        self._fulltext_token = 0
        self._fulltext_loading_url = None
        # Debounce full-text extraction when moving through the list quickly.
        self._fulltext_debounce = None
        self._fulltext_debounce_ms = 350

        # Single-worker background thread for full-text extraction (keeps CPU usage predictable).
        self._fulltext_worker_lock = threading.Lock()
        self._fulltext_worker_event = threading.Event()
        self._fulltext_worker_request = None
        self._fulltext_worker_stop = False
        self._fulltext_worker_thread = threading.Thread(target=self._fulltext_worker_loop, daemon=True)
        self._fulltext_worker_thread.start()

        # Debounce chapter loading too (selection changes can be rapid).
        self._chapters_debounce = None
        self._chapters_debounce_ms = 500

        # Store article objects for the list
        self.current_articles = []

    def _focus_default_control(self):
        """Ensure keyboard focus lands on the tree after the frame is visible."""
        try:
            self.tree.SetFocus()
        except Exception:
            pass

    def _ensure_view_state(self, view_id: str):
        """Return a mutable cache dict for a view, creating it if needed.

        View ids are strings like:
        - "all"
        - "<feed_id>"
        - "category:<name>"
        """
        if not view_id:
            view_id = "all"

        with getattr(self, "_view_cache_lock", threading.Lock()):
            st = self.view_cache.get(view_id)
            if st is None:
                st = {
                    "articles": [],
                    "id_set": set(),
                    "total": None,
                    "page_size": self.article_page_size,
                    "paged_offset": 0,
                    "fully_loaded": False,
                    "last_access": time.time(),
                }
                self.view_cache[view_id] = st
            else:
                st["last_access"] = time.time()

            # LRU prune
            try:
                max_views = int(getattr(self, "max_cached_views", 15))
            except Exception:
                max_views = 15

            if max_views > 0 and len(self.view_cache) > max_views:
                # Evict least recently used views, but never evict the current view.
                current = getattr(self, "current_feed_id", None)
                items = []
                for k, v in list(self.view_cache.items()):
                    if k == current:
                        continue
                    ts = 0.0
                    try:
                        ts = float(v.get("last_access", 0.0))
                    except Exception:
                        ts = 0.0
                    items.append((ts, k))
                items.sort()
                while len(self.view_cache) > max_views and items:
                    _, victim = items.pop(0)
                    self.view_cache.pop(victim, None)

            return st

    def _select_view(self, feed_id: str):
        """Switch the UI to a view, using cached articles when available."""
        if not feed_id:
            return

        self.current_feed_id = feed_id
        self.content_ctrl.Clear()
        self.selected_article_id = None

        # If we have cached articles for this view, render them immediately.
        with getattr(self, "_view_cache_lock", threading.Lock()):
            st = self.view_cache.get(feed_id)
        if st and isinstance(st.get("articles"), list) and st.get("articles"):
            self.current_articles = list(st.get("articles") or [])
            self.list_ctrl.DeleteAllItems()
            self._remove_loading_more_placeholder()

            self.list_ctrl.Freeze()
            for i, article in enumerate(self.current_articles):
                idx = self.list_ctrl.InsertItem(i, self._get_display_title(article))
                self.list_ctrl.SetItem(idx, 1, utils.humanize_article_date(article.date))
                self.list_ctrl.SetItem(idx, 2, article.author or "")
            self.list_ctrl.Thaw()

            if not bool(st.get("fully_loaded", False)):
                self._add_loading_more_placeholder()
            else:
                self._remove_loading_more_placeholder()

            # Start a cheap top-up (latest page) in the background.
            self.current_request_id = time.time()
            threading.Thread(
                target=self._load_articles_thread,
                args=(feed_id, self.current_request_id, False),
                daemon=True,
            ).start()
            return

        # If we have cached empty state, show it immediately and still top-up.
        if st and isinstance(st.get("articles"), list) and not st.get("articles") and st.get("fully_loaded"):
            self.current_articles = []
            self.list_ctrl.DeleteAllItems()
            self._remove_loading_more_placeholder()
            self.list_ctrl.InsertItem(0, "No articles found.")
            self.current_request_id = time.time()
            threading.Thread(
                target=self._load_articles_thread,
                args=(feed_id, self.current_request_id, False),
                daemon=True,
            ).start()
            return

        # No cache yet: do fast-first + background history.
        self._begin_articles_load(feed_id, full_load=True, clear_list=True)

    def _resume_history_thread(self, feed_id: str, request_id):
        """Continue paging older entries from the last cached offset for this view."""
        page_size = self.article_page_size
        try:
            st = self._ensure_view_state(feed_id)
            try:
                offset = int(st.get("paged_offset", 0))
            except Exception:
                offset = 0

            # Fallback: if offset wasn't tracked, infer from cached articles length.
            if offset <= 0:
                try:
                    offset = int(len(st.get("articles") or []))
                except Exception:
                    offset = 0

            total = st.get("total")

            while True:
                if not hasattr(self, "current_request_id") or request_id != self.current_request_id:
                    break
                if feed_id != getattr(self, "current_feed_id", None):
                    break
                if st.get("fully_loaded", False):
                    break
                if total is not None:
                    try:
                        if int(offset) >= int(total):
                            break
                    except Exception:
                        pass

                page, page_total = self.provider.get_articles_page(feed_id, offset=offset, limit=page_size)
                if total is None and page_total is not None:
                    total = page_total
                if page is None:
                    page = []
                if not page:
                    break

                # Sort newest-first defensively.
                page.sort(key=lambda a: (a.timestamp, a.id), reverse=True)

                wx.CallAfter(self._append_articles, page, request_id, total, page_size)

                offset += len(page)
                try:
                    st["paged_offset"] = int(offset)
                except Exception:
                    st["paged_offset"] = offset
                if total is None and len(page) < page_size:
                    break

            wx.CallAfter(self._finish_loading_more, request_id)
        except Exception as e:
            print(f"Error resuming history: {e}")

    def _strip_html(self, html_content):
        if not html_content:
            return ""
        try:
            soup = BeautifulSoup(html_content, 'html.parser')
            # Get text with basic formatting preservation
            text = soup.get_text(separator='\n\n')
            return text.strip()
        except Exception:
            return html_content

    def init_menus(self):
        menubar = wx.MenuBar()
        
        file_menu = wx.Menu()
        add_feed_item = file_menu.Append(wx.ID_ANY, "&Add Feed\tCtrl+N", "Add a new RSS feed")
        remove_feed_item = file_menu.Append(wx.ID_ANY, "&Remove Feed", "Remove selected feed")
        refresh_item = file_menu.Append(wx.ID_REFRESH, "&Refresh Feeds\tF5", "Refresh all feeds")
        file_menu.AppendSeparator()
        add_cat_item = file_menu.Append(wx.ID_ANY, "Add &Category", "Add a new category")
        remove_cat_item = file_menu.Append(wx.ID_ANY, "Remove C&ategory", "Remove selected category")
        file_menu.AppendSeparator()
        import_opml_item = file_menu.Append(wx.ID_ANY, "&Import OPML...", "Import feeds from OPML")
        export_opml_item = file_menu.Append(wx.ID_ANY, "E&xport OPML...", "Export feeds to OPML")
        file_menu.AppendSeparator()
        exit_item = file_menu.Append(wx.ID_EXIT, "E&xit", "Exit application")
        
        view_menu = wx.Menu()
        # Ctrl+P is handled globally (see main.py GlobalMediaKeyFilter). Do not make it a menu accelerator.
        player_item = view_menu.Append(wx.ID_ANY, "Show/Hide &Player (Ctrl+P)", "Show or hide the media player window")

        # Player menu (media controls)
        player_menu = wx.Menu()
        player_toggle_item = player_menu.Append(wx.ID_ANY, "Show/Hide Player (Ctrl+P)", "Show or hide the media player window")
        player_menu.AppendSeparator()
        player_play_pause_item = player_menu.Append(wx.ID_ANY, "Play/Pause", "Toggle play/pause")
        player_stop_item = player_menu.Append(wx.ID_ANY, "Stop", "Stop playback")
        player_menu.AppendSeparator()
        # NOTE: Do not use '\tCtrl+...' menu accelerators here.
        # We implement Ctrl+Arrow globally via an event filter + hold-to-repeat gate.
        # Leaving these as accelerators causes double-seeks (EVT_MENU + key handlers).
        player_rewind_item = player_menu.Append(wx.ID_ANY, "Rewind (Ctrl+Left)", "Rewind")
        player_forward_item = player_menu.Append(wx.ID_ANY, "Fast Forward (Ctrl+Right)", "Fast forward")
        player_menu.AppendSeparator()
        player_vol_up_item = player_menu.Append(wx.ID_ANY, "Volume Up (Ctrl+Up)", "Increase volume")
        player_vol_down_item = player_menu.Append(wx.ID_ANY, "Volume Down (Ctrl+Down)", "Decrease volume")
        
        tools_menu = wx.Menu()
        find_feed_item = tools_menu.Append(wx.ID_ANY, "Find a &Podcast or RSS Feed...", "Find and add a podcast or RSS feed")
        tools_menu.AppendSeparator()
        settings_item = tools_menu.Append(wx.ID_PREFERENCES, "&Settings...", "Configure application")
        
        help_menu = wx.Menu()
        check_updates_item = help_menu.Append(wx.ID_ANY, "Check for &Updates...", "Check for new versions")
        about_item = help_menu.Append(wx.ID_ABOUT, "&About", "About BlindRSS")

        menubar.Append(file_menu, "&File")
        menubar.Append(view_menu, "&View")
        menubar.Append(player_menu, "&Player")
        menubar.Append(tools_menu, "&Tools")
        menubar.Append(help_menu, "&Help")
        self.SetMenuBar(menubar)
        
        self.Bind(wx.EVT_MENU, self.on_add_feed, add_feed_item)
        self.Bind(wx.EVT_MENU, self.on_remove_feed, remove_feed_item)
        self.Bind(wx.EVT_MENU, self.on_refresh_feeds, refresh_item)
        self.Bind(wx.EVT_MENU, self.on_add_category, add_cat_item)
        self.Bind(wx.EVT_MENU, self.on_remove_category, remove_cat_item)
        self.Bind(wx.EVT_MENU, self.on_import_opml, import_opml_item)
        self.Bind(wx.EVT_MENU, self.on_export_opml, export_opml_item)
        self.Bind(wx.EVT_MENU, self.on_show_player, player_item)
        self.Bind(wx.EVT_MENU, self.on_show_player, player_toggle_item)
        self.Bind(wx.EVT_MENU, self.on_player_play_pause, player_play_pause_item)
        self.Bind(wx.EVT_MENU, self.on_player_stop, player_stop_item)
        self.Bind(wx.EVT_MENU, self.on_player_rewind, player_rewind_item)
        self.Bind(wx.EVT_MENU, self.on_player_forward, player_forward_item)
        self.Bind(wx.EVT_MENU, self.on_player_volume_up, player_vol_up_item)
        self.Bind(wx.EVT_MENU, self.on_player_volume_down, player_vol_down_item)
        self.Bind(wx.EVT_MENU, self.on_settings, settings_item)
        self.Bind(wx.EVT_MENU, self.on_check_updates, check_updates_item)
        self.Bind(wx.EVT_MENU, self.on_exit, exit_item)
        self.Bind(wx.EVT_MENU, self.on_find_feed, find_feed_item)
        self.Bind(wx.EVT_MENU, self.on_about, about_item)

    def init_shortcuts(self):
        # Add accelerator for Ctrl+R (F5 is handled by menu item text usually, but being explicit helps)
        self._toggle_favorite_id = int(wx.NewIdRef())
        entries = [
            wx.AcceleratorEntry(wx.ACCEL_CTRL, ord('R'), wx.ID_REFRESH),
            wx.AcceleratorEntry(wx.ACCEL_NORMAL, wx.WXK_F5, wx.ID_REFRESH),
            wx.AcceleratorEntry(wx.ACCEL_CTRL, ord('D'), self._toggle_favorite_id),
        ]
        accel = wx.AcceleratorTable(entries)
        self.SetAcceleratorTable(accel)
        self.Bind(wx.EVT_MENU, self.on_toggle_favorite, id=self._toggle_favorite_id)


    def on_char_hook(self, event: wx.KeyEvent) -> None:
        """Global media shortcuts while the main window is focused."""
        try:
            key = event.GetKeyCode()
        except Exception:
            key = None

        if key == wx.WXK_DELETE:
            focus = None
            try:
                focus = wx.Window.FindFocus()
            except Exception:
                focus = None
            if focus == getattr(self, "list_ctrl", None):
                self.on_delete_article()
                return

        if key == ord('M') or key == ord('m'):
            focus = wx.Window.FindFocus()
            if focus == getattr(self, "list_ctrl", None):
                idx = self.list_ctrl.GetFirstSelected()
                if idx != wx.NOT_FOUND and 0 <= idx < len(self.current_articles):
                    article = self.current_articles[idx]
                    if article.is_read:
                        self.mark_article_unread(idx)
                    else:
                        self.mark_article_read(idx)
                return

        if event.ControlDown() and not event.ShiftDown() and not event.AltDown() and not event.MetaDown():
            pw = getattr(self, "player_window", None)
            playing = False
            try:
                playing = bool(getattr(pw, "is_audio_playing", lambda: False)()) if pw else False
            except Exception:
                playing = False
            if pw and playing:
                actions = {
                    wx.WXK_UP: lambda: pw.adjust_volume(int(getattr(pw, "volume_step", 5))),
                    wx.WXK_DOWN: lambda: pw.adjust_volume(-int(getattr(pw, "volume_step", 5))),
                    wx.WXK_LEFT: lambda: pw.seek_relative_ms(-int(getattr(pw, "seek_back_ms", 10000))),
                    wx.WXK_RIGHT: lambda: pw.seek_relative_ms(int(getattr(pw, "seek_forward_ms", 10000))),
                }
                try:
                    if getattr(self, "_media_hotkeys", None) and self._media_hotkeys.handle_ctrl_key(event, actions):
                        return
                except Exception:
                    # Fall back to default behavior below
                    pass
        event.Skip()

    # -----------------------------------------------------------------
    # Player menu handlers
    # -----------------------------------------------------------------

    def on_player_play_pause(self, event):
        pw = getattr(self, "player_window", None)
        if pw and getattr(pw, "has_media_loaded", lambda: False)():
            try:
                pw.toggle_play_pause()
            except Exception:
                pass

    def on_player_stop(self, event):
        pw = getattr(self, "player_window", None)
        if pw:
            try:
                pw.stop()
            except Exception:
                pass

    def on_player_rewind(self, event):
        pw = getattr(self, "player_window", None)
        if pw:
            try:
                pw.seek_relative_ms(-int(getattr(pw, "seek_back_ms", 10000)))
            except Exception:
                pass

    def on_player_forward(self, event):
        pw = getattr(self, "player_window", None)
        if pw:
            try:
                pw.seek_relative_ms(int(getattr(pw, "seek_forward_ms", 10000)))
            except Exception:
                pass

    def on_player_volume_up(self, event):
        pw = getattr(self, "player_window", None)
        if pw:
            try:
                pw.adjust_volume(int(getattr(pw, "volume_step", 5)))
            except Exception:
                pass

    def on_player_volume_down(self, event):
        pw = getattr(self, "player_window", None)
        if pw:
            try:
                pw.adjust_volume(-int(getattr(pw, "volume_step", 5)))
            except Exception:
                pass

    def on_refresh_feeds(self, event=None):
        # Visual feedback usually good, but console for now or title?
        # self.SetTitle("RSS Reader - Refreshing...") 
        threading.Thread(target=self._manual_refresh_thread, daemon=True).start()

    def on_refresh_single_feed(self, event):
        item = self.tree.GetSelection()
        feed_id = self._get_feed_id_from_tree_item(item)
        if not feed_id:
            return
        threading.Thread(target=self._refresh_single_feed_thread, args=(feed_id,), daemon=True).start()

    def _play_sound(self, key):
        if not self.config_manager.get("sounds_enabled", True):
            return
        path = self.config_manager.get(key)
        if not path:
            return
        
        # Resolve relative path
        if not os.path.isabs(path):
            # 1. Check user/custom path (APP_DIR/path)
            custom_path = os.path.join(APP_DIR, path)
            if os.path.exists(custom_path):
                path = custom_path
            # 2. Check PyInstaller bundle path (MEIPASS/path)
            elif getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
                 bundled_path = os.path.join(sys._MEIPASS, path)
                 if os.path.exists(bundled_path):
                     path = bundled_path
                 else:
                     path = custom_path
            else:
                path = custom_path
            
        if os.path.exists(path):
            try:
                snd = wx.adv.Sound(path)
                if snd.IsOk():
                    snd.Play(wx.adv.SOUND_ASYNC)
            except Exception:
                log.exception(f"Failed to play sound: {path}")

    def _refresh_single_feed_thread(self, feed_id):
        try:
            # Re-use the existing progress callback mechanism
            self.provider.refresh_feed(feed_id, progress_cb=self._on_feed_refresh_progress)
            wx.CallAfter(self._flush_feed_refresh_progress) # Ensure it flushes immediately
            # We don't need to call refresh_feeds() (full tree rebuild) if we just updated one feed.
            # The progress callback updates the tree item label.
            self._play_sound("sound_refresh_complete")
        except Exception as e:
            print(f"Single feed refresh error: {e}")
            self._play_sound("sound_refresh_error")

    def _run_refresh(self, block: bool, force: bool = False) -> bool:
        """Run provider.refresh with optional blocking guard to avoid overlap."""
        acquired = False
        try:
            acquired = self._refresh_guard.acquire(blocking=block)
        except Exception:
            acquired = False
        if not acquired:
            return False
        try:
            if self.provider.refresh(self._on_feed_refresh_progress, force=force):
                wx.CallAfter(self.refresh_feeds)
            self._play_sound("sound_refresh_complete")
            return True
        except Exception as e:
            print(f"Refresh error: {e}")
            self._play_sound("sound_refresh_error")
            return False
        finally:
            try:
                self._refresh_guard.release()
            except Exception:
                pass

    def _manual_refresh_thread(self):
        # Manual refresh should wait for any in-flight refresh to finish.
        ran = self._run_refresh(block=True, force=True)
        if not ran:
            print("Manual refresh skipped: another refresh is running.")

    def on_close(self, event):
        # If user prefers closing to tray and this is a real close event, just hide
        if event and self.config_manager.get("close_to_tray", False):
            event.Veto()
            self.Hide()
            return

        # Close player window cleanly
        if self.player_window:
            try:
                if hasattr(self.player_window, "shutdown"):
                    self.player_window.shutdown()
            except Exception:
                log.exception("Error during player window shutdown")
            self.player_window.Destroy()
        try:
            if getattr(self, "_media_hotkeys", None):
                self._media_hotkeys.stop()
        except Exception:
            pass
        if self.tray_icon:
            self.tray_icon.Destroy()
        try:
            self._fulltext_worker_stop = True
            self._fulltext_worker_event.set()
        except Exception:
            pass

        self.stop_event.set()
        if self.refresh_thread.is_alive():
            self.refresh_thread.join(timeout=1)
        self.Destroy()

    def on_iconize(self, event):
        if event.IsIconized() and self.config_manager.get("minimize_to_tray", True):
            self.Hide()
            return
        event.Skip()

    def on_tree_context_menu(self, event):
        # Determine position for the menu
        pos = event.GetPosition() # Mouse position if mouse event, (-1,-1) if keyboard event
        item = self.tree.GetSelection() # Get currently selected item (important for keyboard trigger)
        
        menu_pos = wx.DefaultPosition # Default to mouse if available
        
        if pos == wx.DefaultPosition: # Keyboard event
            if item.IsOk():
                rect = self.tree.GetBoundingRect(item)
                menu_pos = rect.GetPosition() # Use item's top-left corner relative to tree control
            else:
                # Fallback: display menu at center of the tree control if no item selected
                size = self.tree.GetSize()
                menu_pos = wx.Point(size.width // 2, size.height // 2)
        else: # Mouse event, pos is relative to the tree control itself
            menu_pos = pos

        if not item.IsOk() and pos == wx.DefaultPosition:
            # If keyboard trigger and no item selected, don't show menu.
            # Or show a generic one if that makes sense. For now, skip.
            return
            
        data = self.tree.GetItemData(item) # Data of the selected item
        if not data:
            # If no data, it means no valid item is selected, so no menu
            return
            
        menu = wx.Menu()
        
        if data["type"] == "category":
            cat_title = data["id"]
            if cat_title != "Uncategorized":
                rename_item = menu.Append(wx.ID_ANY, "Rename Category")
                self.Bind(wx.EVT_MENU, lambda e: self.on_rename_category(cat_title), rename_item)

                remove_item = menu.Append(wx.ID_ANY, "Remove Category")
                self.Bind(wx.EVT_MENU, self.on_remove_category, remove_item)

                delete_with_feeds_item = menu.Append(wx.ID_ANY, "Delete Category and Feeds")
                self.Bind(wx.EVT_MENU, self.on_delete_category_with_feeds, delete_with_feeds_item)
            
            import_item = menu.Append(wx.ID_ANY, "Import OPML Here...")
            self.Bind(wx.EVT_MENU, lambda e: self.on_import_opml(e, target_category=cat_title), import_item)
            
        elif data["type"] == "feed":
            refresh_feed_item = menu.Append(wx.ID_ANY, "Refresh Feed")
            self.Bind(wx.EVT_MENU, self.on_refresh_single_feed, refresh_feed_item)

            edit_item = menu.Append(wx.ID_ANY, "Edit Feed...")
            self.Bind(wx.EVT_MENU, self.on_edit_feed, edit_item)

            copy_url_item = menu.Append(wx.ID_ANY, "Copy Feed URL")
            self.Bind(wx.EVT_MENU, self.on_copy_feed_url, copy_url_item)

            remove_item = menu.Append(wx.ID_ANY, "Remove Feed")
            self.Bind(wx.EVT_MENU, self.on_remove_feed, remove_item)
            
        if menu.GetMenuItemCount() > 0:
            self.tree.PopupMenu(menu, menu_pos)
        menu.Destroy()

    def on_list_context_menu(self, event):
        pos = event.GetPosition()
        idx = self.list_ctrl.GetFocusedItem() # Get currently focused item
        
        menu_pos = wx.DefaultPosition
        
        if pos == wx.DefaultPosition: # Keyboard event
            if idx != wx.NOT_FOUND:
                rect = self.list_ctrl.GetItemRect(idx)
                menu_pos = rect.GetPosition() # Use item's top-left corner relative to list control
            else:
                size = self.list_ctrl.GetSize()
                menu_pos = wx.Point(size.width // 2, size.height // 2)
        else: # Mouse event
            menu_pos = pos

        if idx == wx.NOT_FOUND and pos == wx.DefaultPosition:
            # If keyboard trigger and no item focused, don't show menu
            return

        menu = wx.Menu()
        open_item = menu.Append(wx.ID_ANY, "Open Article")
        menu.AppendSeparator()
        mark_read_item = menu.Append(wx.ID_ANY, "Mark as Read\tM")
        mark_unread_item = menu.Append(wx.ID_ANY, "Mark as Unread")
        menu.AppendSeparator()
        copy_item = menu.Append(wx.ID_ANY, "Copy Link")
        delete_item = None
        download_item = None
        if idx != wx.NOT_FOUND and 0 <= idx < len(self.current_articles):
            if not self._is_load_more_row(idx):
                delete_item = menu.Append(wx.ID_ANY, "Delete Article\tDelete")
            article_for_menu = self.current_articles[idx]
            if article_for_menu.media_url:
                download_item = menu.Append(wx.ID_ANY, "Download")
                self.Bind(wx.EVT_MENU, lambda e, a=article_for_menu: self.on_download_article(a), download_item)
            try:
                if getattr(self.provider, "supports_favorites", lambda: False)() and hasattr(self, "_toggle_favorite_id"):
                    label = "Remove from Favorites" if getattr(article_for_menu, "is_favorite", False) else "Add to Favorites"
                    menu.Append(self._toggle_favorite_id, f"{label}\tCtrl+D")
            except Exception:
                pass
        
        # Bindings for list menu items need to use the current idx or selected article
        # on_article_activate (event) needs an event object, but I can re-create one or just call its core logic
        # For simplicity, pass idx to lambda
        self.Bind(wx.EVT_MENU, lambda e: self.on_article_activate(event=wx.ListEvent(wx.EVT_LIST_ITEM_ACTIVATED.type, self.list_ctrl.GetId(), idx=idx)), open_item)
        self.Bind(wx.EVT_MENU, lambda e: self.mark_article_read(idx), mark_read_item)
        self.Bind(wx.EVT_MENU, lambda e: self.mark_article_unread(idx), mark_unread_item)
        self.Bind(wx.EVT_MENU, lambda e: self.on_copy_link(idx), copy_item)
        if delete_item:
            self.Bind(wx.EVT_MENU, lambda e: self.on_delete_article(), delete_item)

        self.list_ctrl.PopupMenu(menu, menu_pos)
        menu.Destroy()

    def on_copy_feed_url(self, event):
        item = self.tree.GetSelection()
        if item.IsOk():
            data = self.tree.GetItemData(item)
            if data and data["type"] == "feed":
                feed_id = data["id"]
                feed = self.feed_map.get(feed_id)
                if feed and feed.url:
                    if wx.TheClipboard.Open():
                        wx.TheClipboard.SetData(wx.TextDataObject(feed.url))
                        wx.TheClipboard.Close()

    def on_copy_link(self, idx):
        if 0 <= idx < len(self.current_articles):
            article = self.current_articles[idx]
            if wx.TheClipboard.Open():
                wx.TheClipboard.SetData(wx.TextDataObject(article.url))
                wx.TheClipboard.Close()

    def _supports_favorites(self) -> bool:
        try:
            return bool(getattr(self.provider, "supports_favorites", lambda: False)())
        except Exception:
            log.exception("Error checking provider support for favorites")
            return False

    def _get_selected_article_index(self) -> int:
        idx = wx.NOT_FOUND
        try:
            idx = self.list_ctrl.GetFirstSelected()
        except Exception:
            idx = wx.NOT_FOUND
        if idx == wx.NOT_FOUND:
            try:
                idx = self.list_ctrl.GetFocusedItem()
            except Exception:
                idx = wx.NOT_FOUND
        return idx

    def _is_favorites_view(self, view_id: str) -> bool:
        view_id = view_id or ""
        return view_id.startswith("favorites:") or view_id.startswith("fav:")

    def _get_display_title(self, article) -> str:
        """Return title to display in list. For aggregate views, append feed title."""
        title = article.title or ""
        fid = getattr(self, "current_feed_id", None)
        
        # Check if current view is an aggregate (All, Unread, Favorites)
        is_aggregate = False
        if not fid or fid == "all" or fid.startswith("unread:") or fid.startswith("read:") or fid.startswith("favorites:") or fid.startswith("fav:"):
            is_aggregate = True
            
        if is_aggregate and article.feed_id:
            feed = self.feed_map.get(article.feed_id)
            if feed and feed.title:
                return f"{title} - {feed.title}"
        
        return title

    def _sync_favorite_flag_in_cached_views(self, article_id: str, is_favorite: bool) -> None:
        try:
            with getattr(self, "_view_cache_lock", threading.Lock()):
                for st in (self.view_cache or {}).values():
                    for a in (st.get("articles") or []):
                        if getattr(a, "id", None) == article_id:
                            a.is_favorite = bool(is_favorite)
        except Exception:
            log.exception("Error syncing favorite flag in cached views")

    def _update_cached_favorites_view(self, article, is_favorite: bool) -> None:
        try:
            fav_view_id = "favorites:all"
            with getattr(self, "_view_cache_lock", threading.Lock()):
                fav_st = (self.view_cache or {}).get(fav_view_id)
                if fav_st is None:
                    return

                fav_articles = list(fav_st.get("articles") or [])
                fav_id_set = set(fav_st.get("id_set") or set())

                if bool(is_favorite):
                    if article.id not in fav_id_set:
                        fav_articles.append(article)
                        fav_id_set.add(article.id)
                        fav_articles.sort(key=lambda a: (a.timestamp, a.id), reverse=True)
                else:
                    if article.id in fav_id_set:
                        fav_id_set.discard(article.id)
                        fav_articles = [a for a in fav_articles if getattr(a, "id", None) != article.id]

                fav_st["articles"] = fav_articles
                fav_st["id_set"] = fav_id_set
                fav_st["last_access"] = time.time()
        except Exception:
            log.exception("Error updating cached favorites view")

    def _decrement_view_total_if_present(self, view_id: str) -> None:
        try:
            st = self._ensure_view_state(view_id)
            total = st.get("total")
            if total is None:
                return
            st["total"] = max(0, int(total) - 1)
        except Exception:
            log.exception("Error decrementing view total for view_id '%s'", view_id)

    def _remove_article_from_current_list(self, idx: int) -> None:
        froze = False
        try:
            self.list_ctrl.Freeze()
            froze = True
        except Exception:
            log.exception("Error freezing list_ctrl")

        try:
            try:
                self.current_articles.pop(idx)
            except Exception:
                log.exception("Error popping article from current_articles at index %s", idx)
            try:
                self.list_ctrl.DeleteItem(idx)
            except Exception:
                log.exception("Error deleting item from list_ctrl at index %s", idx)
        finally:
            if froze:
                try:
                    self.list_ctrl.Thaw()
                except Exception:
                    log.exception("Error thawing list_ctrl")

    def _remove_article_from_cached_views(self, article_id: str) -> None:
        try:
            with getattr(self, "_view_cache_lock", threading.Lock()):
                for st in (self.view_cache or {}).values():
                    articles = list(st.get("articles") or [])
                    if not articles:
                        continue
                    new_articles = [a for a in articles if getattr(a, "id", None) != article_id]
                    if len(new_articles) == len(articles):
                        continue
                    st["articles"] = new_articles
                    st["id_set"] = {a.id for a in new_articles}
                    if st.get("total") is not None:
                        try:
                            st["total"] = max(0, int(st.get("total") or 0) - 1)
                        except Exception:
                            st["total"] = max(0, len(new_articles))
        except Exception:
            log.exception("Error removing article from cached views")

    def on_delete_article(self, event=None):
        idx = self._get_selected_article_index()
        if idx == wx.NOT_FOUND:
            return
        if self._is_load_more_row(idx):
            return
        if idx < 0 or idx >= len(self.current_articles):
            return

        article = self.current_articles[idx]
        try:
            ok = wx.MessageBox(
                "Delete this article? This cannot be undone.",
                "Confirm Delete",
                wx.YES_NO | wx.ICON_WARNING,
            )
        except Exception:
            ok = wx.NO
        if ok != wx.YES:
            return

        if not bool(getattr(self.provider, "supports_article_delete", lambda: False)()):
            wx.MessageBox(
                "This provider does not support deleting articles.",
                "Not Supported",
                wx.ICON_INFORMATION,
            )
            return

        cache_key, _url, _aid = self._fulltext_cache_key_for_article(article, idx)
        threading.Thread(
            target=self._delete_article_thread,
            args=(article.id, cache_key),
            daemon=True,
        ).start()

    def _delete_article_thread(self, article_id: str, cache_key: str) -> None:
        ok = False
        err = ""
        try:
            ok = bool(self.provider.delete_article(article_id))
        except Exception as e:
            err = str(e) or "Unknown error"
        wx.CallAfter(self._post_delete_article, article_id, cache_key, ok, err)

    def _post_delete_article(self, article_id: str, cache_key: str, ok: bool, err: str) -> None:
        if not ok:
            msg = "Could not delete article."
            if err:
                msg += f"\n\n{err}"
            wx.MessageBox(msg, "Error", wx.ICON_ERROR)
            return

        try:
            self._fulltext_cache.pop(cache_key, None)
        except Exception:
            pass

        idx = None
        for i, a in enumerate(self.current_articles):
            if getattr(a, "id", None) == article_id:
                idx = i
                break

        if idx is not None:
            self._remove_article_from_current_list(idx)

        self._remove_article_from_cached_views(article_id)

        if not self.current_articles:
            self._show_empty_articles_state()
            return

        # Select the next closest item to keep navigation smooth.
        next_idx = 0
        if idx is not None:
            next_idx = min(idx, len(self.current_articles) - 1)
        try:
            self.list_ctrl.Select(next_idx)
            self.list_ctrl.Focus(next_idx)
        except Exception:
            pass

    def _show_empty_articles_state(self) -> None:
        try:
            self._remove_loading_more_placeholder()
            self.list_ctrl.DeleteAllItems()
            self.list_ctrl.InsertItem(0, "No articles found.")
            self.content_ctrl.Clear()
            self.selected_article_id = None
        except Exception:
            log.exception("Error showing empty articles state")

    def _update_current_view_cache(self, view_id: str) -> None:
        try:
            st = self._ensure_view_state(view_id)
            st["articles"] = self.current_articles
            st["id_set"] = {a.id for a in (self.current_articles or [])}
            st["last_access"] = time.time()
        except Exception:
            log.exception("Error updating current view cache for view_id '%s'", view_id)

    def on_toggle_favorite(self, event=None):
        if not self._supports_favorites():
            return

        idx = self._get_selected_article_index()
        if idx == wx.NOT_FOUND:
            return
        if self._is_load_more_row(idx):
            return
        if idx < 0 or idx >= len(self.current_articles):
            return

        article = self.current_articles[idx]
        try:
            new_state = self.provider.toggle_favorite(article.id)
        except Exception:
            return
        if new_state is None:
            return

        article.is_favorite = bool(new_state)

        self._sync_favorite_flag_in_cached_views(article.id, bool(new_state))
        self._update_cached_favorites_view(article, bool(new_state))

        # If we're in the Favorites view and the item was removed from favorites, drop it from the list.
        fid = getattr(self, "current_feed_id", "") or ""
        if self._is_favorites_view(fid) and not bool(new_state):
            self._remove_article_from_current_list(idx)

            # If the list is now empty, show an empty-state row.
            if not self.current_articles:
                self._show_empty_articles_state()

            # Keep cache for the current view consistent.
            self._update_current_view_cache(fid)
            self._decrement_view_total_if_present(fid)

    def on_rename_category(self, old_title):
        dlg = wx.TextEntryDialog(self, f"Rename category '{old_title}' to:", "Rename Category", value=old_title)
        if dlg.ShowModal() == wx.ID_OK:
            new_title = dlg.GetValue()
            if new_title and new_title != old_title:
                if self.provider.rename_category(old_title, new_title):
                    self.refresh_feeds()
                else:
                    wx.MessageBox("Could not rename category.", "Error", wx.ICON_ERROR)
        dlg.Destroy()

    def on_add_category(self, event):
        dlg = wx.TextEntryDialog(self, "Enter category name:", "Add Category")
        if dlg.ShowModal() == wx.ID_OK:
            name = dlg.GetValue()
            if name:
                if self.provider.add_category(name):
                    self.refresh_feeds()
                else:
                    wx.MessageBox("Could not add category.", "Error", wx.ICON_ERROR)
        dlg.Destroy()

    def on_remove_category(self, event):
        item = self.tree.GetSelection()
        if item.IsOk():
            data = self.tree.GetItemData(item)
            if data and data["type"] == "category":
                if wx.MessageBox(f"Remove category '{self.tree.GetItemText(item)}'? Feeds will be moved to Uncategorized.", "Confirm", wx.YES_NO) == wx.YES:
                    self._selection_hint = {"type": "all", "id": "all"}
                    if self.provider.delete_category(data["id"]):
                        self.refresh_feeds()
                    else:
                        wx.MessageBox("Could not remove category.", "Error", wx.ICON_ERROR)
            else:
                 wx.MessageBox("Please select a category to remove.", "Info")

    def on_delete_category_with_feeds(self, event):
        item = self.tree.GetSelection()
        if not item or not item.IsOk():
            return
        data = self.tree.GetItemData(item)
        if not data or data.get("type") != "category":
            wx.MessageBox("Please select a category to remove.", "Info")
            return

        cat_title = data.get("id")
        if not cat_title or str(cat_title).lower() == "uncategorized":
            wx.MessageBox("The Uncategorized folder cannot be removed.", "Info")
            return

        feed_ids = []
        try:
            for fid, feed in (self.feed_map or {}).items():
                if (feed.category or "Uncategorized") == cat_title:
                    feed_ids.append(fid)
        except Exception:
            feed_ids = []

        count = len(feed_ids)
        prompt = (
            f"Delete category '{cat_title}' and its {count} feed(s)?\n\n"
            "This will remove the feeds and their articles."
        )
        if wx.MessageBox(prompt, "Confirm", wx.YES_NO | wx.ICON_WARNING) != wx.YES:
            return

        self._selection_hint = {"type": "all", "id": "all"}
        threading.Thread(
            target=self._delete_category_with_feeds_thread,
            args=(cat_title, feed_ids),
            daemon=True,
        ).start()

    def _delete_category_with_feeds_thread(self, cat_title: str, feed_ids: list[str]):
        failed = []
        try:
            for fid in list(feed_ids or []):
                try:
                    if not self.provider.remove_feed(fid):
                        failed.append(fid)
                except Exception:
                    failed.append(fid)
            try:
                self.provider.delete_category(cat_title)
            except Exception:
                pass
        finally:
            wx.CallAfter(self._post_delete_category_with_feeds, cat_title, failed)

    def _post_delete_category_with_feeds(self, cat_title: str, failed: list[str]):
        self.refresh_feeds()
        if failed:
            wx.MessageBox(
                f"Deleted category '{cat_title}', but {len(failed)} feed(s) could not be removed.",
                "Warning",
                wx.ICON_WARNING,
            )

    def refresh_loop(self):
        while not self.stop_event.is_set():
            interval = int(self.config_manager.get("refresh_interval", 300))
            try:
                self._run_refresh(block=False)
            except Exception as e:
                print(f"Refresh error: {e}")
            # Sleep in one shot but wake early if closing
            self.stop_event.wait(interval)

    def refresh_feeds(self):
        # Offload data fetching to background thread to prevent blocking UI
        threading.Thread(target=self._refresh_feeds_worker, daemon=True).start()

    def _refresh_feeds_worker(self):
        try:
            feeds = self.provider.get_feeds()
            all_cats = self.provider.get_categories()
            wx.CallAfter(self._update_tree, feeds, all_cats)
        except Exception as e:
            wx.MessageBox(f"Error fetching feeds: {e}", "Error", wx.ICON_ERROR)

    def _on_feed_refresh_progress(self, state):
        # Called from worker threads inside provider.refresh; batch and marshal to UI thread.
        if not isinstance(state, dict):
            return
        feed_id = state.get("id")
        if not feed_id:
            return

        with self._refresh_progress_lock:
            self._refresh_progress_pending[str(feed_id)] = state
            if self._refresh_progress_flush_scheduled:
                return
            self._refresh_progress_flush_scheduled = True

        try:
            wx.CallAfter(self._flush_feed_refresh_progress)
        except Exception:
            # Likely during shutdown. We failed to schedule a flush.
            with self._refresh_progress_lock:
                self._refresh_progress_pending.clear()
                self._refresh_progress_flush_scheduled = False
            log.debug("Failed to schedule feed refresh progress flush, likely during shutdown.", exc_info=True)

    def _flush_feed_refresh_progress(self):
        with self._refresh_progress_lock:
            pending = list(self._refresh_progress_pending.values())
            self._refresh_progress_pending.clear()
            self._refresh_progress_flush_scheduled = False

        for st in pending:
            try:
                self._apply_feed_refresh_progress(st)
            except Exception:
                log.debug("Failed to apply feed refresh progress update", exc_info=True)

    def _apply_feed_refresh_progress(self, state):
        if not state:
            return
        feed_id = state.get("id")
        if not feed_id:
            return

        title = state.get("title", "")
        unread = state.get("unread_count", 0)
        category = state.get("category", "Uncategorized")

        # Update cached feed objects
        feed_obj = self.feed_map.get(feed_id)
        if feed_obj:
            feed_obj.title = title or feed_obj.title
            feed_obj.unread_count = unread
            feed_obj.category = category

        # Update tree label if present
        node = self.feed_nodes.get(feed_id)
        if node and node.IsOk():
            label = f"{title} ({unread})" if unread > 0 else title
            self.tree.SetItemText(node, label)

        # If the selected view is impacted, schedule article reload
        sel = self.tree.GetSelection()
        if sel and sel.IsOk():
            data = self.tree.GetItemData(sel)
            if data:
                typ = data.get("type")
                if typ == "all":
                    self._schedule_article_reload()
                elif typ == "feed" and data.get("id") == feed_id:
                    self._schedule_article_reload()
                elif typ == "category" and data.get("id") == category:
                    self._schedule_article_reload()

    def _schedule_article_reload(self):
        if self._article_refresh_pending:
            return
        self._article_refresh_pending = True
        wx.CallLater(120, self._run_pending_article_reload)

    def _run_pending_article_reload(self):
        self._article_refresh_pending = False
        self._reload_selected_articles()

    def _update_tree(self, feeds, all_cats):
        # Save selection to restore it later
        selected_item = self.tree.GetSelection()
        selected_data = None
        if selected_item.IsOk():
            selected_data = self.tree.GetItemData(selected_item)

        # Use selection hint if present (e.g. after deletion)
        hint = getattr(self, "_selection_hint", None)
        if hint:
            selected_data = hint
            self._selection_hint = None

        frozen = False
        self._updating_tree = True
        try:
            self.tree.Freeze() # Stop updates while rebuilding
            frozen = True
            self.tree.DeleteChildren(self.all_feeds_node)
            self.tree.DeleteChildren(self.root)

            # Map feed id -> Feed and Tree items for quick lookup (downloads, labeling)
            self.feed_map = {f.id: f for f in feeds}
            self.feed_nodes = {}
            
            # Special Views
            self.all_feeds_node = self.tree.AppendItem(self.root, "All Feeds")
            self.tree.SetItemData(self.all_feeds_node, {"type": "all", "id": "all"})

            self.unread_node = self.tree.AppendItem(self.root, "Unread Articles")
            self.tree.SetItemData(self.unread_node, {"type": "all", "id": "unread:all"})
            
            self.read_node = self.tree.AppendItem(self.root, "Read Articles")
            self.tree.SetItemData(self.read_node, {"type": "all", "id": "read:all"})

            self.favorites_node = None
            try:
                if getattr(self.provider, "supports_favorites", lambda: False)():
                    self.favorites_node = self.tree.AppendItem(self.root, "Favorites")
                    self.tree.SetItemData(self.favorites_node, {"type": "all", "id": "favorites:all"})
            except Exception:
                self.favorites_node = None
            
            # Group by category
            categories = {c: [] for c in all_cats} # Initialize with all known categories
            
            for feed in feeds:
                cat = feed.category or "Uncategorized"
                if cat not in categories:
                    categories[cat] = []
                categories[cat].append(feed)
                
            # Sort categories alphabetically
            sorted_cats = sorted(categories.keys())
            
            item_to_select = None

            for cat in sorted_cats:
                cat_feeds = categories[cat]
                # Sort feeds alphabetically by title
                cat_feeds.sort(key=lambda f: (f.title or "").lower())
                
                cat_node = self.tree.AppendItem(self.root, cat)
                cat_data = {"type": "category", "id": cat}
                self.tree.SetItemData(cat_node, cat_data)
                
                # Check if this category was selected
                if selected_data and selected_data["type"] == "category" and selected_data["id"] == cat:
                    item_to_select = cat_node

                for feed in cat_feeds:
                    title = f"{feed.title} ({feed.unread_count})" if feed.unread_count > 0 else feed.title
                    node = self.tree.AppendItem(cat_node, title)
                    feed_data = {"type": "feed", "id": feed.id}
                    self.tree.SetItemData(node, feed_data)
                    self.feed_nodes[feed.id] = node
                    
                    # Check if this feed was selected
                    if selected_data and selected_data["type"] == "feed" and selected_data["id"] == feed.id:
                        item_to_select = node

            self.tree.ExpandAll()

            # Restore selection (default to All Feeds on first load so the list populates)
            selection_target = None
            if selected_data and selected_data["type"] == "all":
                if selected_data.get("id") == "unread:all":
                    selection_target = self.unread_node
                elif selected_data.get("id") == "read:all":
                    selection_target = self.read_node
                elif selected_data.get("id") == "favorites:all" and self.favorites_node and self.favorites_node.IsOk():
                    selection_target = self.favorites_node
                else:
                    selection_target = self.all_feeds_node
            elif item_to_select and item_to_select.IsOk():
                selection_target = item_to_select
            else:
                selection_target = self.all_feeds_node

            if selection_target and selection_target.IsOk():
                # Ignore transient EVT_TREE_SEL_CHANGED during rebuild; we refresh explicitly below.
                self.tree.SelectItem(selection_target)
        finally:
            if frozen:
                try:
                    self.tree.Thaw() # Resume updates
                except Exception:
                    pass
            self._updating_tree = False

        # Ensure article list refreshes after auto/remote refresh.
        # Re-selecting items on a rebuilt tree does not always emit EVT_TREE_SEL_CHANGED,
        # so explicitly trigger a load for the currently selected node.
        self._reload_selected_articles()

    def _get_feed_id_from_tree_item(self, item):
        if not item or not item.IsOk():
            return None
        data = self.tree.GetItemData(item)
        if not data:
            return None
        typ = data.get("type")
        if typ == "all":
            return data.get("id")
        if typ == "feed":
            return data.get("id")
        if typ == "category":
            return f"category:{data.get('id')}"
        return None

    def _begin_articles_load(self, feed_id: str, full_load: bool = True, clear_list: bool = True):
        # Track current view so auto-refresh can do a cheap "top-up" without reloading history.
        self.current_feed_id = feed_id

        if clear_list:
            self._remove_loading_more_placeholder()
            self.list_ctrl.DeleteAllItems()
            self.list_ctrl.InsertItem(0, "Loading...")
            self.content_ctrl.Clear()

        # Use a request ID to handle race conditions (if user clicks fast / auto-refresh overlaps).
        self.current_request_id = time.time()
        threading.Thread(
            target=self._load_articles_thread,
            args=(feed_id, self.current_request_id, full_load),
            daemon=True
        ).start()

    def _reload_selected_articles(self):
        """Refresh the currently selected view after a feed refresh/tree rebuild.

        If the view is already loaded, only fetch the newest page and merge it in.
        If the view isn't loaded yet (or selection changed), do a full load.
        """
        item = self.tree.GetSelection()
        feed_id = self._get_feed_id_from_tree_item(item)
        if not feed_id:
            return

        have_articles = bool(getattr(self, "current_articles", None))
        same_view = (feed_id == getattr(self, "current_feed_id", None))

        if have_articles and same_view:
            # Fast: fetch latest page and merge, do not page through history.
            self._begin_articles_load(feed_id, full_load=False, clear_list=False)
        else:
            # First load (or selection changed): fast-first + background history.
            self._begin_articles_load(feed_id, full_load=True, clear_list=True)

    def on_tree_select(self, event):
        if getattr(self, "_updating_tree", False):
            try:
                event.Skip()
            except Exception:
                pass
            return
        item = event.GetItem()
        feed_id = self._get_feed_id_from_tree_item(item)
        if not feed_id:
            return
        
        # If the feed hasn't changed (e.g. during a tree refresh where items are recreated),
        # don't reset the view. The update logic (_reload_selected_articles) handles merging new items.
        if feed_id == getattr(self, "current_feed_id", None):
            return

        self._select_view(feed_id)

    def _load_articles_thread(self, feed_id, request_id, full_load: bool = True):
        page_size = self.article_page_size
        try:
            # Fast-first page
            page, total = self.provider.get_articles_page(feed_id, offset=0, limit=page_size)
            # Ensure stable order (newest first)
            page = page or []
            page.sort(key=lambda a: (a.timestamp, a.id), reverse=True)

            if not full_load:
                wx.CallAfter(self._quick_merge_articles, page, request_id, feed_id)
                return

            wx.CallAfter(self._populate_articles, page, request_id, total, page_size)

        except Exception as e:
            print(f"Error loading articles: {e}")
            if full_load:
                wx.CallAfter(self._populate_articles, [], request_id, 0, page_size)
            # For quick mode, just do nothing on failure.

    def _populate_articles(self, articles, request_id, total=None, page_size: int | None = None):
        # If a newer request was started, ignore this result
        if not hasattr(self, 'current_request_id') or request_id != self.current_request_id:
            return
        if page_size is None:
            page_size = self.article_page_size

        self._remove_loading_more_placeholder()

        self.current_articles = list(articles or [])
        self.list_ctrl.DeleteAllItems()

        fid = getattr(self, 'current_feed_id', None)

        if not self.current_articles:
            self.list_ctrl.InsertItem(0, 'No articles found.')
            # Cache empty state
            if fid:
                st = self._ensure_view_state(fid)
                st['articles'] = []
                st['id_set'] = set()
                st['total'] = total
                st['page_size'] = int(page_size)
                st['paged_offset'] = 0
                st['fully_loaded'] = True
                st['last_access'] = time.time()
            return

        self.list_ctrl.Freeze()
        for i, article in enumerate(self.current_articles):
            idx = self.list_ctrl.InsertItem(i, self._get_display_title(article))
            self.list_ctrl.SetItem(idx, 1, utils.humanize_article_date(article.date))
            self.list_ctrl.SetItem(idx, 2, article.author or '')
            self.list_ctrl.SetItem(idx, 3, "Read" if article.is_read else "Unread")
        self.list_ctrl.Thaw()

        # Add a placeholder row if we know/strongly suspect there is more history coming.
        more = False
        if total is None:
            more = (len(self.current_articles) >= page_size)
        else:
            try:
                more = int(total) > len(self.current_articles)
            except Exception:
                more = False

        if more:
            self._add_loading_more_placeholder()
        else:
            self._remove_loading_more_placeholder()

        # Update cache for this view (fresh first page).
        if fid:
            st = self._ensure_view_state(fid)
            st['articles'] = self.current_articles
            st['id_set'] = {a.id for a in self.current_articles}
            st['total'] = total
            st['page_size'] = int(page_size)
            st['paged_offset'] = len(articles or [])
            # Determine completion based on paging + total/short page.
            fully = False
            if total is not None:
                try:
                    fully = int(st['paged_offset']) >= int(total)
                except Exception:
                    fully = False
            else:
                try:
                    fully = len(articles or []) < int(page_size)
                except Exception:
                    fully = False
            st['fully_loaded'] = bool(fully)
            st['last_access'] = time.time()

    def _append_articles(self, articles, request_id, total=None, page_size: int | None = None):
        if not hasattr(self, 'current_request_id') or request_id != self.current_request_id:
            return
        if not articles:
            return
        if page_size is None:
            page_size = self.article_page_size

        # Deduplicate to avoid duplicates when the underlying feed shifts due to new entries.
        existing_ids = {a.id for a in getattr(self, 'current_articles', [])}
        new_articles = [a for a in articles if a.id not in existing_ids]

        # Even if everything was a duplicate, persist paging progress for resume logic.
        fid = getattr(self, 'current_feed_id', None)
        st = None
        if fid:
            st = self._ensure_view_state(fid)
            try:
                st['paged_offset'] = int(st.get('paged_offset', 0)) + len(articles)
            except Exception:
                st['paged_offset'] = len(articles)
            if total is not None:
                st['total'] = total
            st['page_size'] = int(page_size)
            st['last_access'] = time.time()

        if not new_articles:
            return

        self._remove_loading_more_placeholder()

        # Combine and sort to ensure chronological order even if paging overlapped/shifted
        combined = getattr(self, 'current_articles', []) + new_articles
        combined.sort(key=lambda a: (a.timestamp, a.id), reverse=True)
        self.current_articles = combined

        self.list_ctrl.Freeze()
        self.list_ctrl.DeleteAllItems()
        for i, article in enumerate(self.current_articles):
            idx = self.list_ctrl.InsertItem(i, self._get_display_title(article))
            self.list_ctrl.SetItem(idx, 1, utils.humanize_article_date(article.date))
            self.list_ctrl.SetItem(idx, 2, article.author or '')
            self.list_ctrl.SetItem(idx, 3, "Read" if article.is_read else "Unread")
        self.list_ctrl.Thaw()

        # Update cache for this view
        if fid:
            st = self._ensure_view_state(fid)
            st['articles'] = self.current_articles
            st['id_set'] = {a.id for a in self.current_articles}
            if total is not None:
                st['total'] = total
            st['page_size'] = int(page_size)
            # paged_offset already updated above
            try:
                if st.get('total') is not None and int(st['paged_offset']) >= int(st['total']):
                    st['fully_loaded'] = True
            except Exception:
                pass
            if st.get('total') is None and len(articles) < int(page_size):
                st['fully_loaded'] = True
            st['last_access'] = time.time()

        more = False
        if total is None:
            more = (len(articles) >= page_size)
        else:
            try:
                # Prefer paging progress when available
                if fid and st is not None and st.get('paged_offset') is not None:
                    more = int(st.get('paged_offset', 0)) < int(total)
                else:
                    more = int(total) > len(self.current_articles)
            except Exception:
                more = False

        if more:
            self._add_loading_more_placeholder()
        else:
            self._remove_loading_more_placeholder()

    def _finish_loading_more(self, request_id):
        if not hasattr(self, 'current_request_id') or request_id != self.current_request_id:
            return
        self._remove_loading_more_placeholder()
        fid = getattr(self, 'current_feed_id', None)
        if fid:
            st = self._ensure_view_state(fid)
            st['articles'] = (getattr(self, 'current_articles', []) or [])
            st['id_set'] = {a.id for a in (getattr(self, 'current_articles', []) or [])}
            st['fully_loaded'] = True
            st['last_access'] = time.time()

    def _add_loading_more_placeholder(self, loading: bool = False):
        if getattr(self, "_loading_more_placeholder", False):
            # If it already exists, just update the label if needed
            self._update_loading_placeholder(self._loading_label if loading else self._load_more_label)
            return
        label = self._loading_label if loading else self._load_more_label
        idx = self.list_ctrl.InsertItem(self.list_ctrl.GetItemCount(), label)
        self.list_ctrl.SetItem(idx, 1, "")
        self.list_ctrl.SetItem(idx, 2, "")
        self.list_ctrl.SetItem(idx, 3, "")
        self._loading_more_placeholder = True

    def _remove_loading_more_placeholder(self):
        if not getattr(self, "_loading_more_placeholder", False):
            return
        count = self.list_ctrl.GetItemCount()
        if count > 0:
            self.list_ctrl.DeleteItem(count - 1)
        self._loading_more_placeholder = False

    def _update_loading_placeholder(self, text: str | None = None):
        if not getattr(self, "_loading_more_placeholder", False):
            return
        count = self.list_ctrl.GetItemCount()
        if count <= 0:
            return
        label = text or self._load_more_label
        try:
            self.list_ctrl.SetItem(count - 1, 0, label)
            self.list_ctrl.SetItem(count - 1, 1, "")
            self.list_ctrl.SetItem(count - 1, 2, "")
            self.list_ctrl.SetItem(count - 1, 3, "")
        except Exception:
            pass

    def _is_load_more_row(self, idx: int) -> bool:
        if idx is None or idx < 0:
            return False
        if not getattr(self, "_loading_more_placeholder", False):
            return False
        count = self.list_ctrl.GetItemCount()
        if idx != count - 1:
            return False
        title = self.list_ctrl.GetItemText(idx)
        return title in (self._load_more_label, self._loading_label)

    def _load_more_articles(self):
        if self._load_more_inflight:
            return
        if not getattr(self, "_loading_more_placeholder", False):
            return
        feed_id = getattr(self, "current_feed_id", None)
        if not feed_id:
            return
        st = self._ensure_view_state(feed_id)
        
        # Robust offset calculation:
        # 1. Use current article count as authoritative source if available.
        # 2. Fall back to cached paged_offset.
        # This fixes bugs where cache eviction resets paged_offset to 0, causing Page 0 duplicates.
        current_count = len(getattr(self, "current_articles", []) or [])
        cached_offset = int(st.get("paged_offset", 0))
        offset = current_count if current_count > 0 else cached_offset

        self._load_more_inflight = True
        self._update_loading_placeholder(self._loading_label)
        request_id = getattr(self, "current_request_id", None)
        page_size = self.article_page_size
        threading.Thread(
            target=self._load_more_thread,
            args=(feed_id, request_id, offset, page_size),
            daemon=True,
        ).start()

    def _load_more_thread(self, feed_id, request_id, offset, page_size):
        try:
            page, total = self.provider.get_articles_page(feed_id, offset=offset, limit=page_size)
            page = page or []
            page.sort(key=lambda a: (a.timestamp, a.id), reverse=True)
            wx.CallAfter(self._after_load_more, page, total, request_id, page_size)
        except Exception as e:
            wx.CallAfter(self._load_more_failed, request_id, str(e))

    def _after_load_more(self, page, total, request_id, page_size):
        self._load_more_inflight = False
        if not hasattr(self, "current_request_id") or request_id != self.current_request_id:
            return
        if not page:
            self._finish_loading_more(request_id)
            return
        self._append_articles(page, request_id, total, page_size)

    def _load_more_failed(self, request_id, error_msg: str):
        self._load_more_inflight = False
        if not hasattr(self, "current_request_id") or request_id != self.current_request_id:
            return
        try:
            self._update_loading_placeholder(self._load_more_label)
        except Exception:
            pass

    def _quick_merge_articles(self, latest_page, request_id, feed_id):
        # If a newer request was started, ignore
        if not hasattr(self, 'current_request_id') or request_id != self.current_request_id:
            return
        # Ensure we're still looking at the same view
        if feed_id != getattr(self, "current_feed_id", None):
            return
        if not latest_page:
            return

        page_size = self.article_page_size

        # No prior content: behave like a normal populate
        if not getattr(self, "current_articles", None):
            self._populate_articles(latest_page, request_id, None, page_size)
            return

        existing_ids = {a.id for a in self.current_articles}
        new_entries = [a for a in latest_page if a.id not in existing_ids]
        if not new_entries:
            return

        # Remember selection by article id if possible
        selected_id = getattr(self, "selected_article_id", None)
        
        # Check if the list currently has keyboard focus
        list_had_focus = (wx.Window.FindFocus() == self.list_ctrl)

        self._updating_list = True
        try:
            # Combine, deduplicate, and sort
            combined = new_entries + self.current_articles
            combined.sort(key=lambda a: (a.timestamp, a.id), reverse=True)
            
            # If no change in order or content (unlikely if new_entries was non-empty), skip
            if [a.id for a in combined] == [a.id for a in self.current_articles]:
                return

            self.current_articles = combined

            self.list_ctrl.Freeze()
            self.list_ctrl.DeleteAllItems()
            for i, article in enumerate(self.current_articles):
                idx = self.list_ctrl.InsertItem(i, self._get_display_title(article))
                self.list_ctrl.SetItem(idx, 1, utils.humanize_article_date(article.date))
                self.list_ctrl.SetItem(idx, 2, article.author or "")
                self.list_ctrl.SetItem(idx, 3, "Read" if article.is_read else "Unread")
            
            # Restore selection state without stealing focus
            if selected_id:
                for i, a in enumerate(self.current_articles):
                    if a.id == selected_id:
                        # Set selection silently
                        self.list_ctrl.SetItemState(i, wx.LIST_STATE_SELECTED, wx.LIST_STATE_SELECTED)
                        # Only restore focus state if the list actually had focus, 
                        # otherwise screen readers might jump back to the list.
                        if list_had_focus:
                            self.list_ctrl.SetItemState(i, wx.LIST_STATE_FOCUSED, wx.LIST_STATE_FOCUSED)
                        
                        # Ensure visible only if it was already selected/focused
                        # (prevents random scrolling in background)
                        if list_had_focus:
                            self.list_ctrl.EnsureVisible(i)
                        break
            self.list_ctrl.Thaw()
        finally:
            self._updating_list = False

        # Enforce page-limited view based on how many history pages the user loaded.
        try:
            fid = getattr(self, "current_feed_id", None)
            if fid:
                st = self._ensure_view_state(fid)
                paged = int(st.get("paged_offset", page_size))
                allowed_pages = max(1, (paged + page_size - 1) // page_size)
                allowed = allowed_pages * page_size
                if len(self.current_articles) > allowed:
                    self.current_articles = self.current_articles[:allowed]
                    
                    has_placeholder = getattr(self, "_loading_more_placeholder", False)
                    target_count = len(self.current_articles) + (1 if has_placeholder else 0)
                    
                    while self.list_ctrl.GetItemCount() > target_count:
                        try:
                            # If placeholder exists, delete the item *before* it to preserve the "Load More" button
                            idx_to_delete = self.list_ctrl.GetItemCount() - (2 if has_placeholder else 1)
                            if idx_to_delete >= 0:
                                self.list_ctrl.DeleteItem(idx_to_delete)
                            else:
                                break
                        except Exception:
                            break
        except Exception:
            pass

        # Update cache for this view (do not reset paging offset)
        fid = getattr(self, 'current_feed_id', None)
        if fid:
            st = self._ensure_view_state(fid)
            st['articles'] = self.current_articles
            st['id_set'] = {a.id for a in self.current_articles}
            # Do NOT advance paged_offset here; quick top-ups shouldn't change history offset.
            st['page_size'] = page_size
            st['last_access'] = time.time()

    def on_article_select(self, event):
        if self._updating_list:
            return
            
        idx = event.GetIndex()
        if self._is_load_more_row(idx):
            # Keep focus on placeholder; do not try to load content
            self.selected_article_id = None
            self.content_ctrl.SetValue("")
            return
        if 0 <= idx < len(self.current_articles):
            article = self.current_articles[idx]
            
            # Prevent flashing/resetting if the selection hasn't semantically changed
            # (e.g. during background refresh when list indices shift).
            if getattr(self, "selected_article_id", None) == article.id:
                return

            self.selected_article_id = article.id # Track selection
            # Reset full-text state for new selection
            self._fulltext_loading_url = None
            self._fulltext_token += 1
            
            # Immediate feedback (fast)
            self.content_ctrl.SetValue("Loading...")

            # Debounce heavy operations (HTML parsing, marking read, etc.)
            if getattr(self, "_content_debounce", None):
                self._content_debounce.Stop()
            self._content_debounce = wx.CallLater(150, self._update_content_view, idx)

    def _update_content_view(self, idx):
        if idx < 0 or idx >= len(self.current_articles):
            return
        article = self.current_articles[idx]
        
        # Verify selection hasn't changed
        if getattr(self, "selected_article_id", None) != article.id:
            return

        # Prepare content (Heavy: BeautifulSoup)
        header = f"Title: {article.title}\n"
        header += f"Date: {utils.humanize_article_date(article.date)}\n"
        header += f"Author: {article.author}\n"
        header += f"Link: {article.url}\n"
        header += "-" * 40 + "\n\n"
        
        try:
            content = self._strip_html(article.content)
            full_text = header + content
            self.content_ctrl.SetValue(full_text)
        except Exception:
            pass
        
        # Fetch chapters
        try:
            self._schedule_chapters_load(article)
        except Exception:
            pass


    def on_content_focus(self, event):
        """When the content field receives focus, force an immediate full-text load for the selected article."""
        try:
            event.Skip()
        except Exception:
            pass

        try:
            idx = self.list_ctrl.GetFirstSelected()
        except Exception:
            idx = -1

        if idx is None or idx < 0 or idx >= len(self.current_articles):
            return

        self.mark_article_read(idx)
        try:
            self._schedule_fulltext_load_for_index(idx, force=True)
        except Exception:
            pass

    def _fulltext_cache_key_for_article(self, article, idx: int):
        url = (getattr(article, "url", None) or "").strip()
        article_id = getattr(article, "id", None) or getattr(article, "article_id", None) or str(idx)
        cache_key = url if url else f"article:{article_id}"
        return cache_key, url, str(article_id)

    def _schedule_fulltext_load_for_index(self, idx: int, force: bool = False):
        if idx is None or idx < 0 or idx >= len(self.current_articles):
            return

        article = self.current_articles[idx]
        cache_key, url, _article_id = self._fulltext_cache_key_for_article(article, idx)

        cached = self._fulltext_cache.get(cache_key)
        if cached:
            try:
                self._fulltext_loading_url = None
                # Fix: Don't reset text if it's already displayed (preserves cursor position)
                if self.content_ctrl.GetValue() != cached:
                    self.content_ctrl.SetValue(cached)
                    self.content_ctrl.SetInsertionPoint(0)
            except Exception:
                pass
            return
        if getattr(self, "_fulltext_debounce", None) is not None:
            try:
                self._fulltext_debounce.Stop()
            except Exception:
                pass
            self._fulltext_debounce = None

        delay = 0 if force else int(getattr(self, "_fulltext_debounce_ms", 350))
        token_snapshot = int(getattr(self, "_fulltext_token", 0))

        self._fulltext_debounce = wx.CallLater(delay, self._start_fulltext_load, idx, token_snapshot)

    def _start_fulltext_load(self, idx: int, token_snapshot: int):
        # Only proceed if selection hasn't changed since scheduling.
        if token_snapshot != int(getattr(self, "_fulltext_token", 0)):
            return

        if idx is None or idx < 0 or idx >= len(self.current_articles):
            return

        try:
            sel = self.list_ctrl.GetFirstSelected()
        except Exception:
            sel = idx
        if sel is not None and sel >= 0 and sel != idx:
            # User selection moved; don't start a load for the old index.
            return

        article = self.current_articles[idx]
        cache_key, url, _article_id = self._fulltext_cache_key_for_article(article, idx)

        # If already cached, render immediately.
        cached = self._fulltext_cache.get(cache_key)
        if cached:
            try:
                self._fulltext_loading_url = None
                self.content_ctrl.SetValue(cached)
                self.content_ctrl.SetInsertionPoint(0)
            except Exception:
                pass
            return

        # Avoid duplicate in-flight loads.
        if getattr(self, "_fulltext_loading_url", None) == cache_key:
            return
        self._fulltext_loading_url = cache_key

        fallback_html = getattr(article, "content", "") or ""
        fallback_title = getattr(article, "title", "") or ""
        fallback_author = getattr(article, "author", "") or ""

        req = {
            "idx": idx,
            "cache_key": cache_key,
            "url": url,
            "fallback_html": fallback_html,
            "fallback_title": fallback_title,
            "fallback_author": fallback_author,
            "article_id": _article_id,
            "token": token_snapshot,
        }
        self._fulltext_submit_request(req)

    def _fulltext_submit_request(self, req: dict):
        try:
            with self._fulltext_worker_lock:
                self._fulltext_worker_request = req
            self._fulltext_worker_event.set()
        except Exception:
            pass

    def _provider_fetch_full_content(self, article_id: str, url: str = ""):
        prov = getattr(self, "provider", None)
        if not prov or not hasattr(prov, "fetch_full_content"):
            return None
        try:
            return prov.fetch_full_content(article_id, url)
        except Exception as e:
            print(f"Provider full-content fetch failed for {article_id}: {e}")
            return None

    def _fulltext_worker_loop(self):
        while True:
            try:
                self._fulltext_worker_event.wait()
            except Exception:
                time.sleep(0.05)
                continue

            if getattr(self, "_fulltext_worker_stop", False):
                break

            req = None
            try:
                with self._fulltext_worker_lock:
                    req = self._fulltext_worker_request
                    self._fulltext_worker_request = None
                    self._fulltext_worker_event.clear()
            except Exception:
                req = None
                try:
                    self._fulltext_worker_event.clear()
                except Exception:
                    pass

            if not req:
                continue

            token_snapshot = int(req.get("token", -1))
            cache_key = (req.get("cache_key") or "").strip()
            url = (req.get("url") or "").strip()
            fallback_html = req.get("fallback_html") or ""
            fallback_title = req.get("fallback_title") or ""
            fallback_author = req.get("fallback_author") or ""

            # If selection already changed before we start, skip the expensive work.
            if token_snapshot != int(getattr(self, "_fulltext_token", 0)):
                continue

            err = None
            rendered = None

            # Prefer client-side extraction first (web fetch).
            try:
                rendered = article_extractor.render_full_article(
                    url,
                    fallback_html=fallback_html,
                    fallback_title=fallback_title,
                    fallback_author=fallback_author,
                )
            except Exception as e:
                err = str(e) or "Unknown error"
                rendered = None

            # If client extraction failed, ask provider (e.g., Miniflux fetch-content).
            if not rendered:
                provider_html = None
                try:
                    provider_html = self._provider_fetch_full_content(req.get("article_id"), url)
                except Exception as e:
                    if not err: err = str(e) or "Unknown error"
                if provider_html:
                    try:
                        rendered = article_extractor.render_full_article(
                            "",
                            fallback_html=provider_html,
                            fallback_title=fallback_title,
                            fallback_author=fallback_author,
                        )
                    except Exception as e:
                        if not err: err = str(e) or "Unknown error"

            if not rendered:
                # Fallback: show feed content (cleaned) rather than a blank failure message.
                note_lines = []
                if not url:
                    note_lines.append("No webpage URL for this item. Showing feed content.\n\n")
                else:
                    note_lines.append("Full-text extraction failed. Showing feed content.\n\n")
                if err:
                    note_lines.append(err + "\n\n")

                feed_render = None
                try:
                    feed_render = article_extractor.render_full_article(
                        "",
                        fallback_html=fallback_html,
                        fallback_title=fallback_title,
                        fallback_author=fallback_author,
                    )
                except Exception:
                    feed_render = None

                final_text = "".join(note_lines)
                if feed_render:
                    final_text += feed_render
                else:
                    # last resort: strip HTML to visible text
                    try:
                        final_text += (self._strip_html(fallback_html) or "").strip()
                    except Exception:
                        final_text += "No text available.\n"
                rendered = final_text

            def apply():
                # Only apply if selection still matches.
                if token_snapshot != int(getattr(self, "_fulltext_token", 0)):
                    return
                try:
                    idx_now = self.list_ctrl.GetFirstSelected()
                except Exception:
                    idx_now = -1
                if idx_now is None or idx_now < 0 or idx_now >= len(self.current_articles):
                    return
                article_now = self.current_articles[idx_now]
                cur_key, _cur_url, _aid = self._fulltext_cache_key_for_article(article_now, idx_now)
                if cur_key != cache_key:
                    return

                try:
                    self._fulltext_cache[cache_key] = rendered
                except Exception:
                    pass

                try:
                    self._fulltext_loading_url = None
                    self.content_ctrl.SetValue(rendered)
                    self.content_ctrl.SetInsertionPoint(0)
                except Exception:
                    pass

            try:
                wx.CallAfter(apply)
            except Exception:
                pass


    def _schedule_chapters_load(self, article):
        # Cancel previous debounce timer.
        if getattr(self, "_chapters_debounce", None) is not None:
            try:
                self._chapters_debounce.Stop()
            except Exception:
                pass
            self._chapters_debounce = None

        delay = int(getattr(self, "_chapters_debounce_ms", 500))
        article_id = getattr(article, "id", None)

        self._chapters_debounce = wx.CallLater(delay, self._start_chapters_load, article_id)

    def _start_chapters_load(self, article_id):
        try:
            if hasattr(self, 'selected_article_id') and self.selected_article_id != article_id:
                return
        except Exception:
            pass

        # Find the article object in current list.
        article = None
        try:
            for a in self.current_articles:
                if getattr(a, "id", None) == article_id:
                    article = a
                    break
        except Exception:
            article = None

        if not article:
            return

        try:
            threading.Thread(target=self._load_chapters_thread, args=(article,), daemon=True).start()
        except Exception:
            pass
    def _load_chapters_thread(self, article):
        chapters = getattr(article, "chapters", None)
        if not chapters and hasattr(self.provider, "get_article_chapters"):
            try:
                chapters = self.provider.get_article_chapters(article.id)
            except Exception:
                chapters = None
        
        if chapters:
            wx.CallAfter(self._append_chapters, article.id, chapters)

    def _append_chapters(self, article_id, chapters):
        # Verify selection hasn't changed
        if hasattr(self, 'selected_article_id') and self.selected_article_id == article_id:
            text = "\n\nChapters:\n"
            for ch in chapters:
                start = ch.get("start", 0)
                mins = int(start // 60)
                secs = int(start % 60)
                start_str = f"{mins:02d}:{secs:02d}"
                title = ch.get("title", "")
                href = ch.get("href", "")
                if href:
                    text += f"- {start_str}  {title} ({href})\n"
                else:
                    text += f"- {start_str}  {title}\n"
            self.content_ctrl.AppendText(text)

    def on_show_player(self, event):
        self.toggle_player_visibility()

    def toggle_player_visibility(self, force_show: bool | None = None):
        """Show/hide the player window.

        force_show:
          - True: show
          - False: hide
          - None: toggle
        """
        pw = getattr(self, "player_window", None)
        if not pw:
            return
        try:
            if force_show is None:
                show = not pw.IsShown()
            else:
                show = bool(force_show)
            if show:
                if hasattr(pw, "show_and_focus"):
                    pw.show_and_focus()
                else:
                    pw.Show()
                    pw.Raise()
            else:
                pw.Hide()
                try:
                    self.list_ctrl.SetFocus()
                except Exception:
                    pass
        except Exception:
            pass

    def show_and_focus_main(self, flash: bool = True):
        """Restore window from tray/minimized state and focus the tree."""
        try:
            if self.IsIconized():
                self.Iconize(False)
            if not self.IsShown():
                self.Show()
            self.Raise()
            if flash:
                try:
                    self.RequestUserAttention(wx.NOTIFY_WINDOW_REQUEST)
                except Exception:
                    pass
            wx.CallAfter(self._focus_default_control)
        except Exception:
            pass

    def mark_article_read(self, idx):
        if idx < 0 or idx >= len(self.current_articles):
            return
        article = self.current_articles[idx]
        if not article.is_read:
            threading.Thread(target=self.provider.mark_read, args=(article.id,), daemon=True).start()
            article.is_read = True
            self.list_ctrl.SetItem(idx, 3, "Read")

    def mark_article_unread(self, idx):
        if idx < 0 or idx >= len(self.current_articles):
            return
        article = self.current_articles[idx]
        if article.is_read:
            threading.Thread(target=self.provider.mark_unread, args=(article.id,), daemon=True).start()
            article.is_read = False
            self.list_ctrl.SetItem(idx, 3, "Unread")

    def on_article_activate(self, event):
        # Double click or Enter
        idx = event.GetIndex()
        if self._is_load_more_row(idx):
            self._load_more_articles()
            return
        if 0 <= idx < len(self.current_articles):
            article = self.current_articles[idx]
            self.mark_article_read(idx)

            if self._should_play_in_player(article):
                # Decision logic for which URL to play
                media_url = article.media_url
                media_type = (article.media_type or "").lower()
                use_ytdlp = media_type == "video/youtube"

                is_direct_media = False
                try:
                    if media_url:
                        if media_type.startswith(("audio/", "video/")) or "podcast" in media_type:
                            is_direct_media = True
                        else:
                            media_path = urlsplit(str(media_url)).path.lower()
                            if media_path.endswith((".mp3", ".m4a", ".m4b", ".aac", ".ogg", ".opus", ".wav", ".flac", ".mp4", ".m4v", ".webm", ".mkv", ".mov")):
                                is_direct_media = True
                except Exception:
                    is_direct_media = False

                # If main URL is yt-dlp supported, prefer it only when we don't already
                # have a direct audio/video enclosure (e.g., YouTube thumbnails).
                if article.url and core.discovery.is_ytdlp_supported(article.url):
                    if use_ytdlp or (not media_url) or (not is_direct_media):
                        media_url = article.url
                        use_ytdlp = True
                elif not media_url and article.url:
                    # Fallback
                    media_url = article.url

                if not media_url:
                    return

                # Use cached chapters if available
                chapters = getattr(article, "chapters", None)
                
                # Start playback immediately (avoid blocking)
                self.player_window.load_media(media_url, use_ytdlp, chapters, title=getattr(article, "title", None))

                # Respect the preference for showing/hiding the player on playback
                if bool(self.config_manager.get("show_player_on_play", True)):
                    self.toggle_player_visibility(force_show=True)
                else:
                    # Keep audio playing, but hide the window
                    self.toggle_player_visibility(force_show=False)
                
                # Fetch chapters in background if missing
                if not chapters:
                    chapter_media_url = getattr(article, "media_url", None)
                    chapter_media_type = getattr(article, "media_type", None)

                    threading.Thread(
                        target=self._fetch_chapters_for_player,
                        args=(article.id, chapter_media_url, chapter_media_type),
                        daemon=True,
                    ).start()
            else:
                # Non-podcast/news items open in the user's default browser
                webbrowser.open(article.url)

    def _fetch_chapters_for_player(self, article_id, media_url: str | None = None, media_type: str | None = None):
        chapters = []
        try:
            if hasattr(self.provider, "get_article_chapters"):
                chapters = self.provider.get_article_chapters(article_id) or []
        except Exception as e:
            print(f"Background chapter fetch (provider) failed: {e}")
            chapters = []

        # Fallback: if the provider doesn't resolve chapters itself, try extracting them directly
        # from the playable audio URL (ID3 CHAP frames / Podcasting 2.0 chapters JSON).
        if not chapters and media_url:
            try:
                chapters = utils.fetch_and_store_chapters(article_id, media_url, media_type) or []
            except Exception as e:
                print(f"Background chapter fetch (media) failed: {e}")

        if chapters:
            try:
                wx.CallAfter(self._apply_chapters_for_player, article_id, chapters)
            except Exception:
                pass

    def _apply_chapters_for_player(self, article_id: str, chapters: list[dict]) -> None:
        try:
            for a in getattr(self, "current_articles", []) or []:
                if getattr(a, "id", None) == article_id:
                    try:
                        a.chapters = chapters
                    except Exception:
                        pass
                    break
        except Exception:
            pass

        try:
            self.player_window.update_chapters(chapters)
        except Exception:
            pass

    def _should_play_in_player(self, article):
        """Only treat bona-fide podcast/media items as playable; everything else opens in browser."""
        
        # 1. Check main URL for yt-dlp compatibility first (high priority)
        # This covers YouTube, Twitch, etc. even if they have thumbnail enclosures.
        if article.url and core.discovery.is_ytdlp_supported(article.url):
            # Safe-reject if the main URL is explicitly an image
            url_low = article.url.lower()
            if any(url_low.endswith(ext) for ext in [".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"]):
                return False
            return True

        # 2. Check direct media attachments
        if article.media_url:
            media_type = (article.media_type or "").lower()
            url = article.media_url.lower()
            audio_exts = (".mp3", ".m4a", ".m4b", ".aac", ".ogg", ".opus", ".wav", ".flac")
            
            # Reject common image extensions unless yt-dlp explicitly supports them (unlikely for enclosures)
            if any(url.endswith(ext) for ext in [".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"]):
                if not core.discovery.is_ytdlp_supported(article.media_url):
                    return False

            if media_type.startswith(("audio/", "video/")) or "podcast" in media_type:
                return True
            if media_type == "video/youtube":
                return True
            if url.endswith(audio_exts):
                return True

        return False

    def on_download_article(self, article):
        if not article or not getattr(article, "media_url", None):
            wx.MessageBox("No downloadable media found for this item.", "Download", wx.ICON_INFORMATION)
            return
        if not self.config_manager.get("downloads_enabled", False):
            wx.MessageBox("Downloads are disabled. Enable them in Settings > Downloads.", "Downloads disabled", wx.ICON_INFORMATION)
            return
        threading.Thread(target=self._download_article_thread, args=(article,), daemon=True).start()

    def _download_article_thread(self, article):
        try:
            url = article.media_url
            resp = utils.safe_requests_get(url, stream=True, timeout=30)
            resp.raise_for_status()

            ext = self._guess_extension(url, resp.headers.get("Content-Type"))
            download_root = self.config_manager.get("download_path", os.path.join(APP_DIR, "podcasts"))
            if not download_root:
                download_root = os.path.join(APP_DIR, "podcasts")

            feed_title = self._get_feed_title(article.feed_id) or "Feed"
            feed_folder = self._safe_name(feed_title)
            target_dir = os.path.join(download_root, feed_folder)
            os.makedirs(target_dir, exist_ok=True)

            base_name = self._safe_name(article.title) or "episode"
            target_path = self._unique_path(os.path.join(target_dir, base_name + ext))

            with open(target_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)

            self._apply_download_retention(target_dir)
            wx.CallAfter(lambda: wx.MessageBox(f"Downloaded to:\n{target_path}", "Download complete"))
        except Exception as e:
            wx.CallAfter(lambda: wx.MessageBox(f"Download failed: {e}", "Download error", wx.ICON_ERROR))

    def _guess_extension(self, url, content_type=None):
        path = urlsplit(url).path if url else ""
        ext = os.path.splitext(path)[1]
        if ext and len(ext) <= 5:
            return ext

        mapping = {
            "audio/mpeg": ".mp3",
            "audio/mp3": ".mp3",
            "audio/mp4": ".m4a",
            "audio/aac": ".aac",
            "audio/ogg": ".ogg",
            "audio/opus": ".opus",
            "audio/x-wav": ".wav",
            "audio/wav": ".wav",
            "audio/flac": ".flac"
        }
        if content_type:
            ctype = content_type.split(";")[0].strip().lower()
            if ctype in mapping:
                return mapping[ctype]
            for prefix, mapped in mapping.items():
                if ctype.startswith(prefix):
                    return mapped
        return ".mp3"

    def _safe_name(self, text):
        if not text:
            return "untitled"
        cleaned = re.sub(r'[\\/:*?"<>|]+', "_", text)
        cleaned = cleaned.strip().rstrip(".")
        return cleaned[:120] or "untitled"

    def _unique_path(self, path):
        if not os.path.exists(path):
            return path
        base, ext = os.path.splitext(path)
        counter = 1
        while True:
            candidate = f"{base}-{counter}{ext}"
            if not os.path.exists(candidate):
                return candidate
            counter += 1

    def _apply_download_retention(self, folder):
        label = self.config_manager.get("download_retention", "Unlimited")
        seconds = self._retention_seconds(label)
        if seconds is None:
            return
        cutoff = time.time() - seconds
        try:
            for name in os.listdir(folder):
                path = os.path.join(folder, name)
                if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                    os.remove(path)
        except Exception as e:
            print(f"Retention cleanup failed for {folder}: {e}")

    def _retention_seconds(self, label):
        table = {
            "1 day": 86400,
            "3 days": 3 * 86400,
            "1 week": 7 * 86400,
            "2 weeks": 14 * 86400,
            "3 weeks": 21 * 86400,
            "1 month": 30 * 86400,
            "2 months": 60 * 86400,
            "6 months": 180 * 86400,
            "1 year": 365 * 86400,
            "2 years": 730 * 86400,
            "5 years": 1825 * 86400,
            "Unlimited": None
        }
        return table.get(label, None)

    def _get_feed_title(self, feed_id):
        feed = self.feed_map.get(feed_id) if hasattr(self, "feed_map") else None
        if feed:
            return feed.title
        try:
            feeds = self.provider.get_feeds()
            for f in feeds:
                if f.id == feed_id:
                    return f.title
        except Exception:
            pass
        return None

    def on_add_feed(self, event):
        cats = self.provider.get_categories()
        if not cats: cats = ["Uncategorized"]
        
        dlg = AddFeedDialog(self, cats)
        if dlg.ShowModal() == wx.ID_OK:
            url, cat = dlg.get_data()
            if url:
                self.SetTitle(f"BlindRSS - Adding feed {url}...")
                threading.Thread(target=self._add_feed_thread, args=(url, cat), daemon=True).start()
        dlg.Destroy()
        
    def _add_feed_thread(self, url, cat):
        success = self.provider.add_feed(url, cat)
        wx.CallAfter(self._post_add_feed, success)

    def _post_add_feed(self, success):
        self.SetTitle("BlindRSS")
        self.refresh_feeds() # Refresh regardless of success to be safe/consistent
        if not success:
             wx.MessageBox("Failed to add feed.", "Error", wx.ICON_ERROR)

    def on_remove_feed(self, event):
        item = self.tree.GetSelection()
        if item.IsOk():
            data = self.tree.GetItemData(item)
            if data and data["type"] == "feed":
                if wx.MessageBox("Are you sure you want to remove this feed?", "Confirm", wx.YES_NO) == wx.YES:
                    # Logic to find the "next" best item to focus (alphabetical neighbor)
                    # Try next sibling first, then previous sibling
                    next_item = self.tree.GetNextSibling(item)
                    if not next_item or not next_item.IsOk():
                        next_item = self.tree.GetPrevSibling(item)
                    
                    if next_item and next_item.IsOk():
                        self._selection_hint = self.tree.GetItemData(next_item)
                    else:
                        # Fallback to category if it was the only feed
                        parent = self.tree.GetItemParent(item)
                        if parent.IsOk():
                            self._selection_hint = self.tree.GetItemData(parent)

                    self.provider.remove_feed(data["id"])
                    self.refresh_feeds()

    def on_edit_feed(self, event):
        item = self.tree.GetSelection()
        if not item or not item.IsOk():
            return
        data = self.tree.GetItemData(item)
        if not data or data.get("type") != "feed":
            return
        feed_id = data.get("id")
        feed = self.feed_map.get(feed_id)
        if not feed:
            return

        try:
            if not bool(getattr(self.provider, "supports_feed_edit", lambda: False)()):
                wx.MessageBox("This provider does not support editing feeds.", "Not supported", wx.ICON_INFORMATION)
                return
        except Exception:
            pass

        cats = self.provider.get_categories() if self.provider else []
        if not cats:
            cats = ["Uncategorized"]

        allow_url_edit = False
        try:
            allow_url_edit = bool(getattr(self.provider, "supports_feed_url_update", lambda: False)())
        except Exception:
            allow_url_edit = False

        dlg = FeedPropertiesDialog(self, feed, cats, allow_url_edit=allow_url_edit)
        try:
            if dlg.ShowModal() != wx.ID_OK:
                return
            new_title, new_url, new_cat = dlg.get_data()
        finally:
            dlg.Destroy()

        old_title = str(getattr(feed, "title", "") or "")
        old_url = str(getattr(feed, "url", "") or "")
        old_cat = str(getattr(feed, "category", "") or "Uncategorized")

        if not new_title:
            new_title = old_title
        if not new_url:
            new_url = old_url
        if not new_cat:
            new_cat = old_cat

        url_changed = (new_url or "") != (old_url or "")
        if url_changed and not allow_url_edit:
            wx.MessageBox(
                "This provider does not support changing the feed URL.\n"
                "The title and category will be updated, but the URL will stay the same.",
                "Feed URL not supported",
                wx.ICON_INFORMATION,
            )
            new_url = old_url

        if new_title == old_title and new_url == old_url and new_cat == old_cat:
            return

        threading.Thread(
            target=self._update_feed_thread,
            args=(feed_id, new_title, new_url, new_cat),
            daemon=True,
        ).start()

    def _update_feed_thread(self, feed_id: str, title: str, url: str, category: str):
        ok = False
        err = None
        try:
            updater = getattr(self.provider, "update_feed", None)
            if callable(updater):
                ok = bool(updater(feed_id, title=title, url=url, category=category))
        except Exception as e:
            err = str(e)
            ok = False
        wx.CallAfter(self._post_update_feed, ok, err)

    def _post_update_feed(self, ok: bool, err: str | None):
        if ok:
            self.refresh_feeds()
            return
        msg = "Could not update feed."
        if err:
            msg = f"{msg}\n\n{err}"
        wx.MessageBox(msg, "Error", wx.ICON_ERROR)

    def on_import_opml(self, event, target_category=None):
        dlg = wx.FileDialog(self, "Import OPML", wildcard="OPML files (*.opml)|*.opml", style=wx.FD_OPEN)
        if dlg.ShowModal() == wx.ID_OK:
            path = dlg.GetPath()
            self.SetTitle("BlindRSS - Importing OPML...")
            threading.Thread(target=self._import_opml_thread, args=(path, target_category), daemon=True).start()
        dlg.Destroy()

    def _import_opml_thread(self, path, target_category):
        try:
            success = self.provider.import_opml(path, target_category)
            wx.CallAfter(self._post_import_opml, success)
        except Exception as e:
            import traceback
            traceback.print_exc()

    def _post_import_opml(self, success):
        self.SetTitle("BlindRSS")
        self.refresh_feeds()
        if success:
            wx.MessageBox("Import successful.")
        else:
            wx.MessageBox("Import failed. Please check the latest opml_debug_*.log in the temporary directory.")

    def on_export_opml(self, event):
        dlg = wx.FileDialog(self, "Export OPML", wildcard="OPML files (*.opml)|*.opml", style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT)
        if dlg.ShowModal() == wx.ID_OK:
            path = dlg.GetPath()
            wx.BeginBusyCursor()
            try:
                if self.provider.export_opml(path):
                    wx.MessageBox("Export successful.")
                else:
                    wx.MessageBox("Export failed.")
            finally:
                wx.EndBusyCursor()
        dlg.Destroy()

    def on_settings(self, event):
        old_provider = None
        try:
            old_provider = self.config_manager.get("active_provider", "local")
        except Exception:
            old_provider = "local"

        dlg = SettingsDialog(self, self.config_manager.config)
        if dlg.ShowModal() == wx.ID_OK:
            data = dlg.get_data()

            # Apply settings
            try:
                for k, v in data.items():
                    self.config_manager.set(k, v)
            except Exception:
                pass

            # Apply playback speed immediately if the player exists
            if "playback_speed" in data:
                try:
                    self.player_window.set_playback_speed(data["playback_speed"])
                except Exception:
                    pass

            # If provider credentials/provider selection changed, recreate provider and refresh tree/articles
            try:
                new_provider = self.config_manager.get("active_provider", "local")
            except Exception:
                new_provider = old_provider or "local"

            if new_provider != old_provider or "providers" in data:
                try:
                    from core.factory import get_provider
                    self.provider = get_provider(self.config_manager)
                except Exception as e:
                    try:
                        print(f"Error switching provider: {e}")
                    except Exception:
                        pass
                try:
                    # Clear list/content immediately to avoid stale selection against new provider.
                    self.current_articles = []
                    self.list_ctrl.DeleteAllItems()
                    self.content_ctrl.SetValue("")
                except Exception:
                    pass
                try:
                    self.refresh_feeds()
                except Exception:
                    pass
        dlg.Destroy()

    def on_check_updates(self, event):
        self._start_update_check(manual=True)

    def _maybe_auto_check_updates(self):
        try:
            if not bool(self.config_manager.get("auto_check_updates", True)):
                return
        except Exception:
            return
        wx.CallLater(2500, lambda: self._start_update_check(manual=False))

    def _start_update_check(self, manual: bool):
        if getattr(self, "_update_check_inflight", False):
            return
        self._update_check_inflight = True
        threading.Thread(target=self._update_check_thread, args=(manual,), daemon=True).start()

    def _update_check_thread(self, manual: bool):
        try:
            result = updater.check_for_updates()
        except Exception as e:
            result = updater.UpdateCheckResult("error", f"Update check failed: {e}")
        wx.CallAfter(self._handle_update_check_result, result, manual)

    def _handle_update_check_result(self, result: updater.UpdateCheckResult, manual: bool):
        self._update_check_inflight = False

        if result.status == "error":
            if manual:
                wx.MessageBox(result.message, "Update Check Failed", wx.ICON_ERROR)
            return

        if result.status == "up_to_date":
            if manual:
                wx.MessageBox(result.message, "No Updates", wx.ICON_INFORMATION)
            return

        if result.status != "update_available" or not result.info:
            if manual:
                wx.MessageBox("Unable to determine update status.", "Updates", wx.ICON_ERROR)
            return

        info = result.info
        summary = info.notes_summary or "Release notes are available on GitHub."
        prompt = (
            f"A new version of BlindRSS is available ({info.tag}).\n\n"
            f"{summary}\n\n"
            "Download and install this update now?"
        )
        if wx.MessageBox(prompt, "Update Available", wx.YES_NO | wx.ICON_INFORMATION) == wx.YES:
            self._start_update_install(info)

    def _start_update_install(self, info: updater.UpdateInfo):
        if getattr(self, "_update_install_inflight", False):
            return
        if not updater.is_update_supported():
            wx.MessageBox(
                "Auto-update is only available in the packaged Windows build.\n"
                "Download the latest release from GitHub.",
                "Updates",
                wx.ICON_INFORMATION,
            )
            return
        self._update_install_inflight = True
        wx.BeginBusyCursor()
        threading.Thread(target=self._update_install_thread, args=(info,), daemon=True).start()

    def _update_install_thread(self, info: updater.UpdateInfo):
        debug_mode = False
        try:
            debug_mode = bool(self.config_manager.get("debug_mode", False))
        except Exception:
            pass
        ok, msg = updater.download_and_apply_update(info, debug_mode=debug_mode)
        wx.CallAfter(self._finish_update_install, ok, msg)

    def _finish_update_install(self, ok: bool, msg: str):
        self._update_install_inflight = False
        try:
            wx.EndBusyCursor()
        except Exception:
            pass
        if not ok:
            wx.MessageBox(msg, "Update Failed", wx.ICON_ERROR)
            return
        wx.MessageBox(msg, "Update Ready", wx.ICON_INFORMATION)
        self.real_close()

    def on_exit(self, event):
        self.real_close()

    def on_find_feed(self, event):
        from gui.dialogs import FeedSearchDialog
        dlg = FeedSearchDialog(self)
        url = None
        try:
            if dlg.ShowModal() == wx.ID_OK:
                url = dlg.get_selected_url()
        finally:
            dlg.Destroy()

        if url:
            cats = self.provider.get_categories()
            if not cats: cats = ["Uncategorized"]
            cat_dlg = wx.SingleChoiceDialog(self, "Choose category:", "Add Feed", cats)
            cat = "Uncategorized"
            if cat_dlg.ShowModal() == wx.ID_OK:
                cat = cat_dlg.GetStringSelection()
            cat_dlg.Destroy()

            self.SetTitle(f"BlindRSS - Adding feed {url}...")
            threading.Thread(target=self._add_feed_thread, args=(url, cat), daemon=True).start()

    def real_close(self):
        # Standardize shutdown path
        self.on_close(event=None)
