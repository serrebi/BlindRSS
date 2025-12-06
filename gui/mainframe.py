import wx
# import wx.html2 # Removed as per request
import webbrowser
import threading
import time
from bs4 import BeautifulSoup
from dateutil import parser as date_parser
from .dialogs import AddFeedDialog, SettingsDialog
from .player import PlayerFrame
from .tray import BlindRSSTrayIcon
from providers.base import RSSProvider

class MainFrame(wx.Frame):
    def __init__(self, provider: RSSProvider, config_manager):
        super().__init__(None, title="BlindRSS", size=(1000, 700))
        self.provider = provider
        self.config_manager = config_manager
        
        # Create independent player window
        self.player_window = PlayerFrame(self)
        
        self.init_ui()
        self.init_menus()
        self.init_shortcuts()
        
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

    def _strip_html(self, html_content):
        if not html_content:
            return ""
        try:
            soup = BeautifulSoup(html_content, 'html.parser')
            # Get text with basic formatting preservation
            text = soup.get_text(separator='\n\n')
            return text.strip()
        except:
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
        search_podcast_item = file_menu.Append(wx.ID_ANY, "Search &Podcast...", "Search and add a podcast feed")
        file_menu.AppendSeparator()
        exit_item = file_menu.Append(wx.ID_EXIT, "E&xit", "Exit application")
        
        view_menu = wx.Menu()
        player_item = view_menu.Append(wx.ID_ANY, "Show &Player\tCtrl+P", "Show the media player window")
        
        edit_menu = wx.Menu()
        settings_item = edit_menu.Append(wx.ID_PREFERENCES, "&Settings...", "Configure application")
        
        menubar.Append(file_menu, "&File")
        menubar.Append(edit_menu, "&Edit")
        menubar.Append(view_menu, "&View")
        self.SetMenuBar(menubar)
        
        self.Bind(wx.EVT_MENU, self.on_add_feed, add_feed_item)
        self.Bind(wx.EVT_MENU, self.on_remove_feed, remove_feed_item)
        self.Bind(wx.EVT_MENU, self.on_refresh_feeds, refresh_item)
        self.Bind(wx.EVT_MENU, self.on_add_category, add_cat_item)
        self.Bind(wx.EVT_MENU, self.on_remove_category, remove_cat_item)
        self.Bind(wx.EVT_MENU, self.on_import_opml, import_opml_item)
        self.Bind(wx.EVT_MENU, self.on_export_opml, export_opml_item)
        self.Bind(wx.EVT_MENU, self.on_search_podcast, search_podcast_item)
        self.Bind(wx.EVT_MENU, self.on_show_player, player_item)
        self.Bind(wx.EVT_MENU, self.on_settings, settings_item)
        self.Bind(wx.EVT_MENU, self.on_exit, exit_item)

    def init_shortcuts(self):
        # Add accelerator for Ctrl+R (F5 is handled by menu item text usually, but being explicit helps)
        entries = [
            wx.AcceleratorEntry(wx.ACCEL_CTRL, ord('R'), wx.ID_REFRESH),
            wx.AcceleratorEntry(wx.ACCEL_NORMAL, wx.WXK_F5, wx.ID_REFRESH)
        ]
        accel = wx.AcceleratorTable(entries)
        self.SetAcceleratorTable(accel)

    def on_refresh_feeds(self, event):
        # Visual feedback usually good, but console for now or title?
        # self.SetTitle("RSS Reader - Refreshing...") 
        threading.Thread(target=self._manual_refresh_thread, daemon=True).start()

    def _manual_refresh_thread(self):
        try:
            self.provider.refresh()
            wx.CallAfter(self.refresh_feeds)
            # wx.CallAfter(self.SetTitle, "RSS Reader")
        except Exception as e:
            print(f"Manual refresh error: {e}")
            # wx.CallAfter(self.SetTitle, "RSS Reader")

    def on_close(self, event):
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
        if event.IsIconized():
            self.Hide()
        else:
            self.Show()
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
        
        # Bindings for list menu items need to use the current idx or selected article
        # on_article_activate (event) needs an event object, but I can re-create one or just call its core logic
        # For simplicity, pass idx to lambda
        self.Bind(wx.EVT_MENU, lambda e: self.on_article_activate(event=wx.ListEvent(wx.EVT_LIST_ITEM_ACTIVATED.type, self.list_ctrl.GetId(), idx=idx)), open_item)
        self.Bind(wx.EVT_MENU, lambda e: self.on_copy_link(idx), copy_item)
        
        self.list_ctrl.PopupMenu(menu, menu_pos)
        menu.Destroy()

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
                if self.provider.refresh():
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

    def _update_tree(self, feeds, all_cats):
        # Save selection to restore it later
        selected_item = self.tree.GetSelection()
        selected_data = None
        if selected_item.IsOk():
            selected_data = self.tree.GetItemData(selected_item)

        self.tree.Freeze() # Stop updates while rebuilding
        self.tree.DeleteChildren(self.all_feeds_node)
        self.tree.DeleteChildren(self.root)
        
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
                
                # Check if this feed was selected
                if selected_data and selected_data["type"] == "feed" and selected_data["id"] == feed.id:
                    item_to_select = node

        self.tree.ExpandAll()
        
        # Restore selection
        if selected_data and selected_data["type"] == "all":
             self.tree.SelectItem(self.all_feeds_node)
        elif item_to_select and item_to_select.IsOk():
             self.tree.SelectItem(item_to_select)
             
        self.tree.Thaw() # Resume updates

    def on_tree_select(self, event):
        item = event.GetItem()
        if not item.IsOk():
            return
            
        data = self.tree.GetItemData(item)
        if not data:
            return
            
        feed_id = None
        if data["type"] == "all":
            feed_id = "all"
        elif data["type"] == "feed":
            feed_id = data["id"]
        elif data["type"] == "category":
            # Use a special prefix to indicate category fetch
            feed_id = f"category:{data['id']}"
        
        if feed_id:
            # Clear immediately to show feedback, or show "Loading..."
            self.list_ctrl.DeleteAllItems()
            self.list_ctrl.InsertItem(0, "Loading...")
            self.content_ctrl.Clear()
            
            # Use a request ID to handle race conditions (if user clicks fast)
            self.current_request_id = time.time()
            threading.Thread(
                target=self._load_articles_thread,
                args=(feed_id, self.current_request_id),
                daemon=True
            ).start()

    def _load_articles_thread(self, feed_id, request_id):
        try:
            articles = self.provider.get_articles(feed_id)
            wx.CallAfter(self._populate_articles, articles, request_id)
        except Exception as e:
            print(f"Error loading articles: {e}")
            wx.CallAfter(self._populate_articles, [], request_id)

    def _populate_articles(self, articles, request_id):
        # If a newer request was started, ignore this result
        if not hasattr(self, 'current_request_id') or request_id != self.current_request_id:
            return

        # Client-side sort to ensure chronological order
        def parse_date_safe(d):
            if not d: return 0
            try:
                # Parse to timestamp for easy comparison
                return date_parser.parse(d).timestamp()
            except:
                return 0 # Treat invalid dates as oldest
        
        # Sort descending (newest first)
        articles.sort(key=lambda a: parse_date_safe(a.date), reverse=True)
        
        self.current_articles = articles
        self.list_ctrl.DeleteAllItems()
        
        if not articles:
            self.list_ctrl.InsertItem(0, "No articles found.")
            return
            
        self.list_ctrl.Freeze()
        for i, article in enumerate(self.current_articles):
            idx = self.list_ctrl.InsertItem(i, article.title)
            # Format date to be more readable if possible, otherwise keep raw
            self.list_ctrl.SetItem(idx, 1, article.date[:16] if article.date else "")
            self.list_ctrl.SetItem(idx, 2, article.author or "")
        self.list_ctrl.Thaw()
        
        # Optional: Restore focus to list if needed, but user might be navigating tree
        # self.list_ctrl.SetFocus() 

    # def load_articles(self, feed_id):  <-- Replaced by the thread logic above
    
    def on_article_select(self, event):
        idx = event.GetIndex()
        if 0 <= idx < len(self.current_articles):
            article = self.current_articles[idx]
            self.selected_article_id = article.id # Track selection
            
            # Prepare content
            header = f"Title: {article.title}\n"
            header += f"Date: {article.date}\n"
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
        if not self.player_window.IsShown():
            self.player_window.Show()
        self.player_window.Raise()

    def on_article_activate(self, event):
        # Double click or Enter
        idx = event.GetIndex()
        if 0 <= idx < len(self.current_articles):
            article = self.current_articles[idx]
            
            if self._should_play_in_player(article):
                is_youtube = (article.media_type or "").lower() == "video/youtube"
                # Use cached chapters if available
                chapters = getattr(article, "chapters", None)
                
                # Open player IMMEDIATELY with what we have (avoid blocking)
                self.player_window.load_media(article.media_url, is_youtube, chapters)
                if not self.player_window.IsShown():
                    self.player_window.Show()
                self.player_window.Raise()
                
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
        print("DEBUG: on_import_opml triggered")
        dlg = wx.FileDialog(self, "Import OPML", wildcard="OPML files (*.opml)|*.opml", style=wx.FD_OPEN)
        if dlg.ShowModal() == wx.ID_OK:
            path = dlg.GetPath()
            print(f"DEBUG: Selected path: {path}")
            # wx.BusyInfo("Importing feeds... This may take a while.") # potentially problematic
            print("DEBUG: Starting import thread...")
            threading.Thread(target=self._import_opml_thread, args=(path, target_category), daemon=True).start()
        dlg.Destroy()

    def _import_opml_thread(self, path, target_category):
        print(f"DEBUG: Thread started for {path}")
        try:
            success = self.provider.import_opml(path, target_category)
            print(f"DEBUG: import_opml returned {success}")
            wx.CallAfter(self._post_import_opml, success)
        except Exception as e:
            print(f"DEBUG: Exception in thread: {e}")
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
            if not cats:
                cats = ["Uncategorized"]
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
