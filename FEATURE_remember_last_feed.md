# Remember Last Feed Feature

## Overview
Added a new setting to remember the last selected feed/folder when closing and restarting BlindRSS.

## User-Facing Changes

### Settings Dialog
- New checkbox in General tab: **"Remember last selected feed/folder on startup"**
- Default: **Disabled** (maintains current behavior of always starting at "All Articles")
- When enabled, BlindRSS will remember which feed/folder you were viewing and restore it on next startup

### Behavior

**When DISABLED (default):**
- BlindRSS always starts at "All Articles" (current behavior)

**When ENABLED:**
- BlindRSS remembers your last selection (feed, category, or special view)
- On restart, it automatically selects the same feed/folder you were viewing
- Works with:
  - Individual feeds (e.g., "NPR Morning Edition")
  - Categories (e.g., "NPR")
  - Special views (e.g., "Unread Articles", "Read Articles", "Favorites")
  - Unread-filtered views (e.g., "Unread" view of a specific feed)

### Example
1. Navigate to "NPR" → "NPR Morning Edition"
2. Enable "Remember last selected feed/folder on startup" in Settings
3. Close BlindRSS
4. Restart BlindRSS
5. ✓ You're automatically back in "NPR Morning Edition"

## Implementation Details

### Configuration
- Setting key: `remember_last_feed` (boolean)
- Saved feed key: `last_selected_feed` (string)
- Stored in `config.json`

### Feed ID Format
The `last_selected_feed` value is stored as a string representing the feed/view:
- `"all"` - All Articles
- `"unread:all"` - Unread Articles
- `"read:all"` - Read Articles
- `"favorites:all"` - Favorites
- `"category:NPR"` - Category view (e.g., NPR category)
- `"unread:category:NPR"` - Unread view of a category
- `"<feed-id>"` - Specific feed
- `"unread:<feed-id>"` - Unread view of a specific feed

### Files Modified

1. **`gui/dialogs.py`**:
   - Added `remember_last_feed_chk` checkbox to Settings dialog
   - Added setting to `get_data()` return value

2. **`gui/mainframe.py`**:
   - Modified `on_tree_select()` to save the current feed selection when it changes (if setting enabled)
   - Modified `_update_tree()` to restore the saved feed on startup (if setting enabled)
   - Handles all feed types: individual feeds, categories, and special views
   - Preserves unread filter state

### Technical Notes

- The last feed is saved to config on every selection change (if enabled)
- Config writes are relatively cheap (JSON file write)
- On startup, if the saved feed no longer exists (e.g., feed was deleted), falls back to "All Articles"
- The `_unread_filter_enabled` flag is restored when loading unread-filtered views
- Works correctly during tree rebuilds (e.g., after adding/removing feeds)

### Edge Cases Handled

1. **Deleted Feed**: If the saved feed no longer exists, falls back to "All Articles"
2. **Deleted Category**: If the saved category no longer exists, falls back to "All Articles"
3. **Setting Disabled**: If setting is disabled after being enabled, always defaults to "All Articles"
4. **First Launch**: If no saved feed exists, defaults to "All Articles"
5. **Unread Filter**: Correctly restores both the feed AND the unread filter state

## Testing

To test this feature:

1. Enable the setting in Settings → General
2. Navigate to various feeds/categories
3. Close and restart BlindRSS multiple times
4. Verify you return to the same location each time
5. Try with:
   - Individual feeds
   - Categories
   - Special views (Unread, Read, Favorites)
   - Unread-filtered views
6. Disable the setting and verify it always starts at "All Articles"
7. Enable again, delete the saved feed, and verify graceful fallback

## Future Enhancements (Not Implemented)

Possible future improvements:
- Remember scroll position within the article list
- Remember which article was selected
- Remember article detail view state (expanded/collapsed)
- Option to restore multiple tabs (if tabbed interface is added)
