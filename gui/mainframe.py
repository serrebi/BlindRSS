import wx
# import wx.html2 # Removed as per request
import webbrowser
import threading
import time
import os
import re
from urllib.parse import urlsplit
from bs4 import BeautifulSoup
# from dateutil import parser as date_parser  # Removed unused import
from .dialogs import AddFeedDialog, SettingsDialog
from .player import PlayerFrame
from .tray import BlindRSSTrayIcon
from providers.base import RSSProvider
from core.config import APP_DIR
from core import utils


class MainFrame(wx.Frame):
    def __init__(self, provider: RSSProvider, config_manager):
        super().__init__(None, title="BlindRSS", size=(1000, 700))
        self.provider = provider
        self.config_manager = config_manager
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
        
        # Create independent player window
        self.player_window = PlayerFrame(self, config_manager)
        
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
        self.tree.SetFocus()

    def init_ui(self):
        # Main Splitter: Tree vs Content Area
        splitter = wx.SplitterWindow(self)
        
        # Left: Tree (Feeds)
        self.tree = wx.TreeCtrl(splitter, style=wx.TR_DEFAULT_STYLE | wx.TR_HIDE_ROOT | wx.TR_HAS_BUTTONS)
        self.root = self.tree.AddRoot("Root")
        self.all_feeds_node = self.tree.AppendItem(self.root, "All Feeds")
        
        # Right: Splitter (List + Content)
        right_splitter = wx.SplitterWindow(splitter)
        
        # Top Right: List (Articles)
        self.list_ctrl = wx.ListCtrl(right_splitter, style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
        self.list_ctrl.InsertColumn(0, "Title", width=400)
        self.list_ctrl.InsertColumn(1, "Date", width=150)
        self.list_ctrl.InsertColumn(2, "Author", width=150)
        
        # Bottom Right: Content (No embedded player anymore)
        self.content_ctrl = wx.TextCtrl(right_splitter, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2)
        
        right_splitter.SplitHorizontally(self.list_ctrl, self.content_ctrl, 300)
        splitter.SplitVertically(self.tree, right_splitter, 250)
        
        self.Bind(wx.EVT_TREE_SEL_CHANGED, self.on_tree_select, self.tree)
        self.Bind(wx.EVT_CONTEXT_MENU, self.on_tree_context_menu, self.tree)
        
        self.Bind(wx.EVT_LIST_ITEM_SELECTED, self.on_article_select, self.list_ctrl)
        self.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self.on_article_activate, self.list_ctrl)
        self.Bind(wx.EVT_CONTEXT_MENU, self.on_list_context_menu, self.list_ctrl)
        
        # Store article objects for the list
        self.current_articles = []

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
                    "page_size": 200,
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
                idx = self.list_ctrl.InsertItem(i, article.title)
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

            # If the view isn't fully loaded, resume history paging from last offset.
            if not bool(st.get("fully_loaded", False)):
                threading.Thread(
                    target=self._resume_history_thread,
                    args=(feed_id, self.current_request_id),
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
        page_size = 200
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
                page.sort(
                    key=lambda a: (
                        utils.parse_datetime_utc(a.date).timestamp() if utils.parse_datetime_utc(a.date) else 0
                    ),
                    reverse=True,
                )

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
        remove_feed_item = file_menu.Append(wx.ID_ANY, "&Remove Feed\tDelete", "Remove selected feed")
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
        player_item = view_menu.Append(wx.ID_ANY, "Show/Hide &Player\tCtrl+P", "Show or hide the media player window")

        # Player menu (media controls)
        player_menu = wx.Menu()
        player_toggle_item = player_menu.Append(wx.ID_ANY, "Show/Hide Player\tCtrl+P", "Show or hide the media player window")
        player_menu.AppendSeparator()
        player_play_pause_item = player_menu.Append(wx.ID_ANY, "Play/Pause", "Toggle play/pause")
        player_stop_item = player_menu.Append(wx.ID_ANY, "Stop", "Stop playback")
        player_menu.AppendSeparator()
        player_rewind_item = player_menu.Append(wx.ID_ANY, "Rewind\tCtrl+Left", "Rewind")
        player_forward_item = player_menu.Append(wx.ID_ANY, "Fast Forward\tCtrl+Right", "Fast forward")
        player_menu.AppendSeparator()
        player_vol_up_item = player_menu.Append(wx.ID_ANY, "Volume Up\tCtrl+Up", "Increase volume")
        player_vol_down_item = player_menu.Append(wx.ID_ANY, "Volume Down\tCtrl+Down", "Decrease volume")
        
        tools_menu = wx.Menu()
        settings_item = tools_menu.Append(wx.ID_PREFERENCES, "&Settings...", "Configure application")
        tools_menu.AppendSeparator()
        search_podcast_item = tools_menu.Append(wx.ID_ANY, "Search &Podcast...", "Search and add a podcast feed")
        
        menubar.Append(file_menu, "&File")
        menubar.Append(view_menu, "&View")
        menubar.Append(player_menu, "&Player")
        menubar.Append(tools_menu, "&Tools")
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
        self.Bind(wx.EVT_MENU, self.on_exit, exit_item)
        self.Bind(wx.EVT_MENU, self.on_search_podcast, search_podcast_item)

    def init_shortcuts(self):
        # Add accelerator for Ctrl+R (F5 is handled by menu item text usually, but being explicit helps)
        entries = [
            wx.AcceleratorEntry(wx.ACCEL_CTRL, ord('R'), wx.ID_REFRESH),
            wx.AcceleratorEntry(wx.ACCEL_NORMAL, wx.WXK_F5, wx.ID_REFRESH)
        ]
        accel = wx.AcceleratorTable(entries)
        self.SetAcceleratorTable(accel)


    def on_char_hook(self, event: wx.KeyEvent) -> None:
        """Global media shortcuts while the main window is focused."""
        if event.ControlDown():
            pw = getattr(self, "player_window", None)
            if pw and getattr(pw, "has_media_loaded", None) and pw.has_media_loaded():
                key = event.GetKeyCode()
                if key == wx.WXK_UP:
                    pw.adjust_volume(int(getattr(pw, "volume_step", 5)))
                    return
                if key == wx.WXK_DOWN:
                    pw.adjust_volume(-int(getattr(pw, "volume_step", 5)))
                    return
                if key == wx.WXK_LEFT:
                    pw.seek_relative_ms(-int(getattr(pw, "seek_back_ms", 10000)))
                    return
                if key == wx.WXK_RIGHT:
                    pw.seek_relative_ms(int(getattr(pw, "seek_forward_ms", 30000)))
                    return
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
                pw.seek_relative_ms(int(getattr(pw, "seek_forward_ms", 30000)))
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

    def _manual_refresh_thread(self):
        try:
            self.provider.refresh(self._on_feed_refresh_progress)
            wx.CallAfter(self.refresh_feeds)
            # wx.CallAfter(self.SetTitle, "RSS Reader")
        except Exception as e:
            print(f"Manual refresh error: {e}")
            # wx.CallAfter(self.SetTitle, "RSS Reader")

    def on_close(self, event):
        # If user prefers closing to tray and this is a real close event, just hide
        if event and self.config_manager.get("close_to_tray", False):
            event.Veto()
            self.Hide()
            return

        # Close player window cleanly
        if self.player_window:
            self.player_window.Destroy()
        if self.tray_icon:
            self.tray_icon.Destroy()
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
            
            import_item = menu.Append(wx.ID_ANY, "Import OPML Here...")
            self.Bind(wx.EVT_MENU, lambda e: self.on_import_opml(e, target_category=cat_title), import_item)
            
        elif data["type"] == "feed":
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
        copy_item = menu.Append(wx.ID_ANY, "Copy Link")
        download_item = None
        if idx != wx.NOT_FOUND and 0 <= idx < len(self.current_articles):
            article_for_menu = self.current_articles[idx]
            if article_for_menu.media_url:
                download_item = menu.Append(wx.ID_ANY, "Download")
                self.Bind(wx.EVT_MENU, lambda e, a=article_for_menu: self.on_download_article(a), download_item)
        
        # Bindings for list menu items need to use the current idx or selected article
        # on_article_activate (event) needs an event object, but I can re-create one or just call its core logic
        # For simplicity, pass idx to lambda
        self.Bind(wx.EVT_MENU, lambda e: self.on_article_activate(event=wx.ListEvent(wx.EVT_LIST_ITEM_ACTIVATED.type, self.list_ctrl.GetId(), idx=idx)), open_item)
        self.Bind(wx.EVT_MENU, lambda e: self.on_copy_link(idx), copy_item)
        
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
                    if self.provider.delete_category(data["id"]):
                        self.refresh_feeds()
                    else:
                        wx.MessageBox("Could not remove category.", "Error", wx.ICON_ERROR)
            else:
                 wx.MessageBox("Please select a category to remove.", "Info")

    def refresh_loop(self):
        while not self.stop_event.is_set():
            interval = int(self.config_manager.get("refresh_interval", 300))
            try:
                if self.provider.refresh(self._on_feed_refresh_progress):
                   wx.CallAfter(self.refresh_feeds)
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
            print(f"Error fetching feeds: {e}")

    def _on_feed_refresh_progress(self, state):
        # Called from worker threads inside provider.refresh; marshal to UI thread
        wx.CallAfter(self._apply_feed_refresh_progress, state)

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

        self.tree.Freeze() # Stop updates while rebuilding
        self.tree.DeleteChildren(self.all_feeds_node)
        self.tree.DeleteChildren(self.root)

        # Map feed id -> Feed and Tree items for quick lookup (downloads, labeling)
        self.feed_map = {f.id: f for f in feeds}
        self.feed_nodes = {}
        
        self.all_feeds_node = self.tree.AppendItem(self.root, "All Feeds")
        self.tree.SetItemData(self.all_feeds_node, {"type": "all", "id": "all"})
        
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
            selection_target = self.all_feeds_node
        elif item_to_select and item_to_select.IsOk():
            selection_target = item_to_select
        else:
            selection_target = self.all_feeds_node

        if selection_target and selection_target.IsOk():
            self.tree.SelectItem(selection_target)

        self.tree.Thaw() # Resume updates

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
            return "all"
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
        item = event.GetItem()
        feed_id = self._get_feed_id_from_tree_item(item)
        if not feed_id:
            return
        self._select_view(feed_id)

    def _load_articles_thread(self, feed_id, request_id, full_load: bool = True):
        page_size = 200
        try:
            # Fast-first page
            page, total = self.provider.get_articles_page(feed_id, offset=0, limit=page_size)
            # Ensure stable order (newest first) even if provider returns inconsistent ordering
            page = page or []
            page.sort(key=lambda a: (utils.parse_datetime_utc(a.date).timestamp() if utils.parse_datetime_utc(a.date) else 0), reverse=True)

            if not full_load:
                wx.CallAfter(self._quick_merge_articles, page, request_id, feed_id)
                return

            wx.CallAfter(self._populate_articles, page, request_id, total, page_size)

            offset = len(page)
            # Background paging through the remainder
            while True:
                # Cancel if a newer request exists
                if not hasattr(self, 'current_request_id') or request_id != self.current_request_id:
                    break

                if total is not None and offset >= total:
                    break

                next_page, next_total = self.provider.get_articles_page(feed_id, offset=offset, limit=page_size)
                if total is None and next_total is not None:
                    total = next_total

                next_page = next_page or []
                if not next_page:
                    break

                next_page.sort(key=lambda a: (utils.parse_datetime_utc(a.date).timestamp() if utils.parse_datetime_utc(a.date) else 0), reverse=True)

                wx.CallAfter(self._append_articles, next_page, request_id, total, page_size)
                offset += len(next_page)

                # If the provider doesn't return total, stop on short page
                if total is None and len(next_page) < page_size:
                    break

            wx.CallAfter(self._finish_loading_more, request_id)

        except Exception as e:
            print(f"Error loading articles: {e}")
            if full_load:
                wx.CallAfter(self._populate_articles, [], request_id, 0, page_size)
            # For quick mode, just do nothing on failure.

    def _populate_articles(self, articles, request_id, total=None, page_size: int = 200):
        # If a newer request was started, ignore this result
        if not hasattr(self, 'current_request_id') or request_id != self.current_request_id:
            return

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
            idx = self.list_ctrl.InsertItem(i, article.title)
            self.list_ctrl.SetItem(idx, 1, utils.humanize_article_date(article.date))
            self.list_ctrl.SetItem(idx, 2, article.author or '')
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

    def _append_articles(self, articles, request_id, total=None, page_size: int = 200):
        if not hasattr(self, 'current_request_id') or request_id != self.current_request_id:
            return
        if not articles:
            return

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

        start_index = len(getattr(self, 'current_articles', []))
        self.current_articles.extend(new_articles)

        self.list_ctrl.Freeze()
        for i, article in enumerate(new_articles):
            row = start_index + i
            idx = self.list_ctrl.InsertItem(row, article.title)
            self.list_ctrl.SetItem(idx, 1, utils.humanize_article_date(article.date))
            self.list_ctrl.SetItem(idx, 2, article.author or '')
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

    def _add_loading_more_placeholder(self):
        if getattr(self, "_loading_more_placeholder", False):
            return
        idx = self.list_ctrl.InsertItem(self.list_ctrl.GetItemCount(), "Loading more...")
        self.list_ctrl.SetItem(idx, 1, "")
        self.list_ctrl.SetItem(idx, 2, "")
        self._loading_more_placeholder = True

    def _remove_loading_more_placeholder(self):
        if not getattr(self, "_loading_more_placeholder", False):
            return
        count = self.list_ctrl.GetItemCount()
        if count > 0:
            # Only delete if the last row is our placeholder.
            title = self.list_ctrl.GetItemText(count - 1)
            if title == "Loading more...":
                self.list_ctrl.DeleteItem(count - 1)
        self._loading_more_placeholder = False

    def _quick_merge_articles(self, latest_page, request_id, feed_id):
        # If a newer request was started, ignore
        if not hasattr(self, 'current_request_id') or request_id != self.current_request_id:
            return
        # Ensure we're still looking at the same view
        if feed_id != getattr(self, "current_feed_id", None):
            return
        if not latest_page:
            return

        # No prior content: behave like a normal populate
        if not getattr(self, "current_articles", None):
            self._populate_articles(latest_page, request_id, None, 200)
            return

        existing_ids = {a.id for a in self.current_articles}
        new_entries = [a for a in latest_page if a.id not in existing_ids]
        if not new_entries:
            return

        # Remember selection by article id if possible
        selected_id = getattr(self, "selected_article_id", None)

        self.current_articles = new_entries + self.current_articles

        self.list_ctrl.Freeze()
        for i, article in enumerate(new_entries):
            idx = self.list_ctrl.InsertItem(i, article.title)
            self.list_ctrl.SetItem(idx, 1, utils.humanize_article_date(article.date))
            self.list_ctrl.SetItem(idx, 2, article.author or "")
        self.list_ctrl.Thaw()

        # Restore selection if possible
        if selected_id:
            try:
                for i, a in enumerate(self.current_articles):
                    if a.id == selected_id:
                        self.list_ctrl.Select(i)
                        break
            except Exception:
                pass

        # Update cache for this view (do not reset paging offset)
        fid = getattr(self, 'current_feed_id', None)
        if fid:
            st = self._ensure_view_state(fid)
            st['articles'] = self.current_articles
            st['id_set'] = {a.id for a in self.current_articles}
            st['last_access'] = time.time()

    def on_article_select(self, event):
        idx = event.GetIndex()
        if 0 <= idx < len(self.current_articles):
            article = self.current_articles[idx]
            self.selected_article_id = article.id # Track selection
            
            # Prepare content
            header = f"Title: {article.title}\n"
            header += f"Date: {utils.humanize_article_date(article.date)}\n"
            header += f"Author: {article.author}\n"
            header += f"Link: {article.url}\n"
            header += "-" * 40 + "\n\n"
            
            content = self._strip_html(article.content)
            full_text = header + content
            
            self.content_ctrl.SetValue(full_text)
            
            # Mark read in background
            if not article.is_read:
                threading.Thread(target=self.provider.mark_read, args=(article.id,), daemon=True).start()
                article.is_read = True
            
            # Fetch chapters in background to avoid UI lag
            threading.Thread(target=self._load_chapters_thread, args=(article,), daemon=True).start()

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

    def on_article_activate(self, event):
        # Double click or Enter
        idx = event.GetIndex()
        if 0 <= idx < len(self.current_articles):
            article = self.current_articles[idx]
            
            if self._should_play_in_player(article):
                is_youtube = (article.media_type or "").lower() == "video/youtube"
                # Use cached chapters if available
                chapters = getattr(article, "chapters", None)
                
                # Start playback immediately (avoid blocking)
                self.player_window.load_media(article.media_url, is_youtube, chapters, title=getattr(article, "title", None))

                # Respect the preference for showing/hiding the player on playback
                if bool(self.config_manager.get("show_player_on_play", True)):
                    self.toggle_player_visibility(force_show=True)
                else:
                    # Keep audio playing, but hide the window
                    self.toggle_player_visibility(force_show=False)
                
                # Fetch chapters in background if missing
                if not chapters:
                    threading.Thread(target=self._fetch_chapters_for_player, args=(article.id,), daemon=True).start()
            else:
                # Non-podcast/news items open in the user's default browser
                webbrowser.open(article.url)

    def _fetch_chapters_for_player(self, article_id):
        if hasattr(self.provider, "get_article_chapters"):
            try:
                chapters = self.provider.get_article_chapters(article_id)
                if chapters:
                    wx.CallAfter(self.player_window.update_chapters, chapters)
            except Exception as e:
                print(f"Background chapter fetch failed: {e}")

    def _should_play_in_player(self, article):
        """Only treat bona-fide podcast/media items as playable; everything else opens in browser."""
        if not article.media_url:
            return False
        media_type = (article.media_type or "").lower()
        url = article.media_url.lower()
        audio_exts = (".mp3", ".m4a", ".m4b", ".aac", ".ogg", ".opus", ".wav", ".flac")
        
        if media_type.startswith(("audio/", "video/")) or "podcast" in media_type:
            return True
        if media_type == "video/youtube":
            return True
        # Some feeds mislabel audio; fall back to extension sniffing
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
                wx.BusyInfo("Adding feed...")
                threading.Thread(target=self._add_feed_thread, args=(url, cat), daemon=True).start()
        dlg.Destroy()
        
    def _add_feed_thread(self, url, cat):
        success = self.provider.add_feed(url, cat)
        wx.CallAfter(self._post_add_feed, success)

    def _post_add_feed(self, success):
        # Close busy info? wx.BusyInfo is usually a window that needs to be destroyed 
        # or it might be auto-managed if assigned to variable, but here we used it transiently
        # which is actually bad practice as it might disappear or stay stuck.
        # Better to not use BusyInfo without a handle, but keeping existing style for now.
        # Actually, standard wx.BusyInfo hides when the object is destroyed. 
        # Since we didn't keep a reference in on_add_feed, it might have destroyed immediately?
        # Let's ignore fixing BusyInfo for now and focus on refresh.
        
        self.refresh_feeds() # Refresh regardless of success to be safe/consistent
        if not success:
             wx.MessageBox("Failed to add feed.", "Error", wx.ICON_ERROR)

    def on_remove_feed(self, event):
        item = self.tree.GetSelection()
        if item.IsOk():
            data = self.tree.GetItemData(item)
            if data and data["type"] == "feed":
                if wx.MessageBox("Are you sure you want to remove this feed?", "Confirm", wx.YES_NO) == wx.YES:
                    self.provider.remove_feed(data["id"])
                    self.refresh_feeds()

    def on_import_opml(self, event, target_category=None):
        dlg = wx.FileDialog(self, "Import OPML", wildcard="OPML files (*.opml)|*.opml", style=wx.FD_OPEN)
        if dlg.ShowModal() == wx.ID_OK:
            path = dlg.GetPath()
            # wx.BusyInfo("Importing feeds... This may take a while.") # potentially problematic
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
        self.refresh_feeds()
        if success:
            wx.MessageBox("Import successful.")
        else:
            wx.MessageBox("Import failed. Please check the latest opml_debug_*.log in the application directory.")

    def on_export_opml(self, event):
        dlg = wx.FileDialog(self, "Export OPML", wildcard="OPML files (*.opml)|*.opml", style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT)
        if dlg.ShowModal() == wx.ID_OK:
            path = dlg.GetPath()
            if self.provider.export_opml(path):
                wx.MessageBox("Export successful.")
            else:
                wx.MessageBox("Export failed.")
        dlg.Destroy()

    def on_settings(self, event):
        dlg = SettingsDialog(self, self.config_manager.config)
        if dlg.ShowModal() == wx.ID_OK:
            data = dlg.get_data()
            for k, v in data.items():
                self.config_manager.set(k, v)
            if "playback_speed" in data:
                try:
                    self.player_window.set_playback_speed(data["playback_speed"])
                except Exception:
                    pass
        dlg.Destroy()

    def on_exit(self, event):
        self.real_close()

    def on_search_podcast(self, event):
        from gui.dialogs import PodcastSearchDialog
        dlg = PodcastSearchDialog(self)
        url = None
        try:
            if dlg.ShowModal() == wx.ID_OK:
                url = dlg.get_selected_url()
        finally:
            dlg.Destroy()

        if url:
            cats = self.provider.get_categories()
            if not cats: cats = ["Uncategorized"]
            cat_dlg = wx.SingleChoiceDialog(self, "Choose category:", "Add Podcast", cats)
            cat = "Uncategorized"
            if cat_dlg.ShowModal() == wx.ID_OK:
                cat = cat_dlg.GetStringSelection()
            cat_dlg.Destroy()

            wx.BusyInfo("Adding podcast feed...")
            threading.Thread(target=self._add_feed_thread, args=(url, cat), daemon=True).start()

    def real_close(self):
        # Standardize shutdown path
        self.on_close(event=None)
