# Fix for Volume Jump on First Adjustment

## Problem Description

When adjusting the volume for the first time after starting playback, the volume would jump down unexpectedly instead of smoothly adjusting from the current level.

## Root Cause

The issue was caused by a mismatch between the volume slider's value and VLC's actual internal volume:

1. **Slider Initialization**: The volume slider was initialized from the saved config value (e.g., 100%)
2. **VLC Initialization**: VLC was told to set volume to the config value via `audio_set_volume()`
3. **Timing Issue**: VLC's volume change happens asynchronously and might not complete immediately
4. **First Adjustment**: When the user first moved the slider, the slider was at the config value (e.g., 100), but VLC's actual volume might have been different (e.g., still at its default)
5. **Jump**: The slider would then force VLC to the slider's value, causing a perceived "jump"

### Example Scenario:
```
Config says: volume = 70%
Slider shows: 70% (from config)
VLC actually at: 100% (default, because audio_set_volume() didn't complete yet)
User moves slider to 75%: VLC jumps from 100% → 75% (sounds like it went DOWN)
```

## Solution

Added a volume synchronization step that reads VLC's actual volume after playback starts and updates the slider to match:

1. After calling `player.play()` and `audio_set_volume()`, schedule a sync after 500ms
2. The `_sync_volume_from_vlc()` method reads VLC's actual volume via `audio_get_volume()`
3. Updates the internal volume state and slider position to match VLC
4. Now when the user adjusts, the slider starts from VLC's actual volume

### Flow:
```
1. Play media
2. Set VLC volume to config value (e.g., 70%)
3. Wait 500ms for VLC to settle
4. Read VLC's actual volume → confirms it's at 70%
5. Sync slider to 70% (if it wasn't already)
6. User adjusts slider from 70% → 75% smoothly ✓
```

## Files Modified

**`gui/player.py`**:
- Added `_sync_volume_from_vlc()` method (line 3433)
- Call sync after playback starts in `_play_url_in_vlc()` (line 1199)
- Call sync after resume in `on_timer()` (line 4028)

## Technical Details

### The `_sync_volume_from_vlc()` Method

```python
def _sync_volume_from_vlc(self) -> None:
    """Sync the volume slider with VLC's actual volume to prevent jumps on first adjustment."""
    if self.is_casting:
        return
    try:
        vlc_volume = self.player.audio_get_volume()
        if vlc_volume >= 0:  # -1 means error
            # Update our internal state and UI without persisting (already persisted)
            self.volume = int(vlc_volume)
            self._update_volume_ui(int(vlc_volume))
    except Exception:
        pass
```

**Key Features:**
- Only runs for local VLC playback (not casting)
- Uses VLC's `audio_get_volume()` to read actual volume
- Returns -1 on error (which we skip)
- Updates internal state (`self.volume`) and UI slider
- Does NOT persist to config (already saved)
- Gracefully handles exceptions

### Timing

The sync is delayed by 500ms after playback starts using `wx.CallLater(500, ...)`. This gives VLC time to:
- Actually start playing
- Process the initial `audio_set_volume()` call
- Settle to a stable volume level

### Where It's Called

1. **Initial Playback** (`_play_url_in_vlc`):
   ```python
   self.player.play()
   self.player.audio_set_volume(int(getattr(self, 'volume', 100)))
   wx.CallLater(500, self._sync_volume_from_vlc)  # Sync after playback starts
   ```

2. **Resume After Pause** (`on_timer`):
   ```python
   self.player.play()
   self.player.audio_set_volume(int(getattr(self, "volume", 100)))
   wx.CallLater(500, self._sync_volume_from_vlc)  # Sync after resume
   ```

## Testing

To test this fix:

1. Set volume to a specific value (e.g., 70%) and restart the app
2. Play any media
3. Wait for playback to start (1-2 seconds)
4. Immediately adjust the volume slider
5. ✓ Volume should smoothly adjust from current level, no jump

### Test Cases:

1. **Saved volume = 70%**:
   - Start playback
   - Volume should be at 70%
   - Adjusting to 75% should smoothly increase ✓

2. **Saved volume = 50%**:
   - Start playback
   - Volume should be at 50%
   - Adjusting to 60% should smoothly increase ✓

3. **VLC fails to set volume** (edge case):
   - If VLC doesn't apply the volume, sync will read VLC's actual value
   - Slider will match VLC's reality
   - User still has smooth control ✓

## Benefits

1. **Smooth Volume Control**: No unexpected jumps on first adjustment
2. **Accurate Slider**: Slider always reflects VLC's actual volume
3. **Better UX**: Volume behaves as users expect
4. **Robust**: Handles edge cases where VLC volume doesn't match config

## Edge Cases Handled

1. **VLC Volume Command Fails**: Sync reads actual volume, slider adjusts to reality
2. **Casting Mode**: Sync is disabled (not needed for cast devices)
3. **VLC Returns Error**: Sync checks for -1 return value and skips update
4. **Exceptions**: All operations wrapped in try/except for safety

## Future Enhancements (Not Implemented)

Possible improvements:
- Periodic volume sync (every N seconds) to handle external volume changes
- Visual feedback during sync (though 500ms is fast enough)
- Configurable sync delay for slower systems
