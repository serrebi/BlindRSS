# Fix for "400 Unread Items After Mark All Read + Refresh" Bug

## Problem Description

After marking all items as read and then pressing F5 to refresh, the application would show exactly 400 unread items in the list. These were not new items, but old items (some dating back weeks) that had been marked as read but were "resurrected" as unread.

## Root Cause

The bug was caused by the interaction between three operations:

1. **Mark All as Read**: Updates database to set `is_read = 1` for all articles
2. **Tree Refresh**: Called automatically after mark-all-read, which triggered **retention cleanup**
3. **Manual Refresh (F5)**: Fetches new content from RSS feeds

### The Bug Sequence:

```
User Action: Mark All as Read
    ↓
Database: UPDATE articles SET is_read = 1
    ↓
UI: Call refresh_feeds() to update tree
    ↓
_refresh_feeds_worker() runs cleanup_old_articles()
    ↓
Database: DELETE FROM articles WHERE date < cutoff AND is_read = 1
    ↓ (Articles just marked as read are DELETED!)
User Action: Press F5
    ↓
_manual_refresh_thread() → _run_refresh() → provider.refresh()
    ↓
RSS feeds return their last 400 entries (standard feed size)
    ↓
Refresh logic checks existing_articles map
    ↓
Articles deleted in cleanup are NOT in existing_articles
    ↓
Database: INSERT INTO articles (..., is_read = 0) for "new" articles
    ↓
BUG: 400 old articles reappear as UNREAD!
```

### Why This Happened:

The `refresh_feeds()` method (which updates the tree UI) was performing retention cleanup. When called after "Mark All as Read", it would immediately delete those read articles if they fell outside the retention window (e.g., > 1 week old). Then when the user manually refreshed (F5), those deleted articles would be re-fetched from the RSS feed and re-inserted as **new unread items**.

## Solution

Moved the retention cleanup logic from `_refresh_feeds_worker()` (tree update) to `_run_refresh()` (network refresh).

### Changes Made:

1. **Created `_perform_retention_cleanup()` helper** (line 794):
   - Centralizes the cleanup logic
   - Makes it reusable and testable

2. **Moved cleanup to `_run_refresh()`** (line 819):
   - Cleanup now runs BEFORE RSS fetch, not after mark-all-read
   - Affects both automatic refresh and manual refresh (F5)
   - Cleanup happens INSIDE the refresh guard (no race conditions)

3. **Removed cleanup from `_refresh_feeds_worker()`** (line 1543):
   - Tree updates no longer trigger cleanup
   - Prevents deletion of articles immediately after marking them as read

### New Flow:

```
User Action: Mark All as Read
    ↓
Database: UPDATE articles SET is_read = 1
    ↓
UI: Call refresh_feeds() to update tree
    ↓
_refresh_feeds_worker() only updates tree (NO CLEANUP!)
    ↓
(Articles remain in database, marked as read)

User Action: Press F5
    ↓
_manual_refresh_thread() → _run_refresh()
    ↓
_perform_retention_cleanup() runs FIRST
    ↓
Database: DELETE FROM articles WHERE date < cutoff AND is_read = 1
    ↓
provider.refresh() fetches RSS feeds
    ↓
For each RSS entry:
    - Check if article exists in database
    - If exists: skip (keep existing is_read status)
    - If new: INSERT with is_read = 0
    ↓
✓ FIX: Only genuinely NEW articles appear as unread!
```

## Benefits

1. **Fixes the bug**: Old read articles stay read after refresh
2. **Maintains cleanup**: Retention policy still enforced during refreshes
3. **No race conditions**: Cleanup runs inside the refresh guard
4. **Correct timing**: Cleanup happens before RSS fetch, not between mark-read and refresh

## Files Modified

- `gui/mainframe.py`:
  - Added `_perform_retention_cleanup()` helper method
  - Moved cleanup call to `_run_refresh()` 
  - Removed cleanup from `_refresh_feeds_worker()`
  - Added detailed docstring explaining the fix

## Testing

Created `tests/test_mark_read_refresh_bug.py` with two tests:

1. **test_mark_all_read_then_refresh_keeps_articles_read()**: 
   - Verifies articles marked as read stay read after refresh
   - Tests the core fix

2. **test_cleanup_then_refresh_doesnt_resurrect_articles()**: 
   - Tests cleanup behavior with articles outside retention window
   - Verifies deleted articles come back as unread (expected behavior)

## Reproduction Steps (Before Fix)

1. Have articles in your feeds (with retention policy enabled, e.g., "1 week")
2. Mark all items as read (File → Mark All Items as Read)
3. Observe: List shows 0 unread items ✓
4. Press F5 to refresh
5. Observe during refresh: Unread count grows to exactly 400
6. BUG: Old articles (dating back weeks) appear as unread

## Verification Steps (After Fix)

1. Have articles in your feeds
2. Mark all items as read
3. Press F5 to refresh
4. ✓ Only genuinely new articles (published after mark-all-read) appear as unread
5. ✓ Old articles stay marked as read

## Additional Notes

- The cleanup still runs on every refresh (automatic and manual)
- Cleanup timing is now correct: BEFORE RSS fetch, not AFTER mark-all-read
- No changes to the cleanup logic itself, only when it runs
- No changes to mark_all_read logic
- No changes to RSS refresh logic
