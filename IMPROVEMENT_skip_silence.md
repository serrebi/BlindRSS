# Skip Silence Improvements - Reducing False Positives

## Problem Description

The "Skip Silence" feature occasionally skips speech, particularly:
- Quiet speech or soft-spoken narrators
- Pauses within natural speech patterns
- Speech in remote streams with network artifacts
- Low-volume audio segments

## Root Cause

The silence detection was using overly aggressive default parameters:

1. **VAD Aggressiveness**: Level 2 (out of 3) was too aggressive
2. **Volume Threshold**: -42 dB was too high (detected quieter speech as silence)
3. **Minimum Silence Duration**: 600ms was too short (brief pauses in speech were skipped)
4. **Padding**: 120ms padding around silence was too small
5. **Merge Gap**: 200ms gap between silent regions was too small

These aggressive settings prioritized removing ALL quiet moments, but ended up catching quiet speech and natural pauses.

## Solution

Adjusted default parameters to be more conservative and speech-preserving:

### Local Playback (Default):
- **`vad_aggressiveness`**: 2 → **1** (less aggressive, preserves more speech)
- **`threshold_db`**: -42.0 → **-50.0** (quieter sounds are now considered "audio", not silence)
- **`min_silence_ms`**: 600 → **800** (longer threshold to avoid catching natural speech pauses)
- **`padding_ms`**: 120 → **300** (more buffer around detected silence)
- **`merge_gap_ms`**: 200 → **200** (reduced to avoid over-merging speech into silence)
- **`resume_backoff_ms`**: 800 → **1000** (more cushion after skipping silence)

### Remote Streams (Extra Conservative):
- **`vad_aggressiveness`**: 1 → **0** (least aggressive, maximum speech preservation)
- **`threshold_db`**: -42.0 → **-52.0** (very lenient for network-affected audio)
- **`min_silence_ms`**: 900 → **1200** (only skip truly long silences)
- **`merge_gap_ms`**: 300 → **200** (keep regions separate to avoid over-merging)

## Impact

**Before:**
```
Speech at -45 dB → Detected as silence → Skipped ❌
Natural pause (400ms) → Detected as silence → Skipped ❌
Network glitch → Detected as silence → Skipped ❌
```

**After:**
```
Speech at -45 dB → Detected as audio → Preserved ✓
Natural pause (400ms) → Below 800ms threshold → Preserved ✓
Network glitch → Brief/quiet → Preserved ✓
True silence (1000ms @ -60 dB) → Detected as silence → Skipped ✓
```

## Advanced Tuning (Manual Config)

Power users can fine-tune silence detection by editing `config.json`:

### Available Parameters:

```json
{
  "skip_silence": true,
  "silence_skip_threshold_db": -50.0,
  "silence_skip_min_ms": 800,
  "silence_skip_padding_ms": 300,
  "silence_skip_merge_gap_ms": 200,
  "silence_skip_resume_backoff_ms": 1000,
  "silence_skip_retrigger_backoff_ms": 1400,
  "silence_vad_aggressiveness": 1,
  "silence_skip_window_ms": 30,
  "silence_vad_frame_ms": 30,
  "silence_scan_sample_rate": 16000,
  "silence_scan_remote_sample_rate": 8000,
  "silence_scan_threads": 2,
  "silence_scan_low_priority": true
}
```

### Key Parameters to Adjust:

1. **`silence_skip_threshold_db`** (Range: -120.0 to 0.0):
   - More negative = more lenient (preserves quieter audio)
   - Less negative = more aggressive (skips quieter audio)
   - **Default**: -50.0 (conservative)
   - **Examples**:
     - `-60.0` - Very lenient, only skips near-total silence
     - `-50.0` - Balanced (recommended)
     - `-40.0` - Aggressive, may skip quiet speech

2. **`silence_skip_min_ms`** (Range: 300 to 5000):
   - Minimum silence duration before skipping
   - **Default**: 800ms (conservative)
   - **Examples**:
     - `1500` - Very conservative, only long silences
     - `800` - Balanced (recommended)
     - `400` - More aggressive, may skip speech pauses

3. **`silence_vad_aggressiveness`** (Range: 0 to 3):
   - WebRTC VAD aggressiveness level
   - **Default**: 1 (conservative)
   - **Values**:
     - `0` - Least aggressive (best for quiet/soft speech)
     - `1` - Low aggressiveness (recommended)
     - `2` - Medium (may skip quiet speech)
     - `3` - Most aggressive (skip more audio, risk missing speech)

4. **`silence_skip_padding_ms`** (Range: 50 to 500):
   - Buffer around detected silence (keeps audio before/after)
   - **Default**: 300ms
   - **Examples**:
     - `400` - Extra safety margin
     - `300` - Balanced (recommended)
     - `200` - Less padding, tighter cuts

5. **`silence_skip_merge_gap_ms`** (Range: 50 to 1000):
   - Maximum gap between silent regions to merge them
   - **Default**: 200ms
   - **Examples**:
     - `300` - Merge more regions (may over-merge)
     - `200` - Balanced (recommended)
     - `100` - Keep regions separate (more precise)

### Tuning Strategies:

**If you still experience speech being skipped:**
```json
{
  "silence_skip_threshold_db": -55.0,  // Even more lenient
  "silence_skip_min_ms": 1200,         // Longer minimum
  "silence_vad_aggressiveness": 0,     // Least aggressive
  "silence_skip_padding_ms": 400,      // More padding
  "silence_skip_merge_gap_ms": 150     // Less merging
}
```

**If not enough silence is being skipped:**
```json
{
  "silence_skip_threshold_db": -45.0,  // More aggressive
  "silence_skip_min_ms": 500,          // Shorter minimum
  "silence_vad_aggressiveness": 2,     // More aggressive
  "silence_skip_padding_ms": 200       // Less padding
}
```

**For podcasts with background music:**
```json
{
  "silence_skip_threshold_db": -55.0,  // Very lenient (music can be quiet)
  "silence_vad_aggressiveness": 0,     // Least aggressive
  "silence_skip_min_ms": 1200          // Only skip long true silences
}
```

## Technical Details

### WebRTC VAD (Voice Activity Detection)

BlindRSS uses Google's WebRTC VAD, which analyzes audio characteristics beyond just volume:
- Frequency patterns typical of human speech
- Spectral energy distribution
- Temporal patterns

The VAD works in conjunction with the volume threshold:
```python
silent = (not is_speech) and (db <= threshold_db)
```

Both conditions must be true for audio to be considered "silence".

### Algorithm Flow:

1. **Decode Audio**: FFmpeg converts media to 16kHz mono PCM
2. **VAD Analysis**: 30ms frames analyzed for speech patterns
3. **Volume Check**: RMS calculated and compared to threshold
4. **Run Detection**: Consecutive silent frames tracked
5. **Minimum Check**: Only runs ≥ min_ms are recorded as silence
6. **Merging**: Close silent regions merged (within merge_gap_ms)
7. **Padding**: Each region expanded by padding_ms on both sides
8. **Playback**: Silence regions are skipped during playback

### Performance:

- Silence scanning runs in a background thread
- Low priority by default (doesn't impact playback)
- Caches results (scan once, use for entire media)
- Remote streams use lower sample rate (8kHz vs 16kHz) for speed

## Files Modified

**`gui/player.py`**:
- Updated default parameters in `_start_silence_scan()` (lines 1658-1663)
- Made remote stream detection more conservative (lines 1668-1677)

## Testing

To verify the improvements:

1. **Enable skip silence** in Settings → General
2. **Test with challenging content**:
   - Audiobooks with soft narrators
   - Podcasts with dynamic volume
   - Interviews with natural pauses
   - Remote streams (e.g., NPR)
3. **Listen for false positives**:
   - Speech should NOT be skipped ✓
   - Natural pauses should NOT be skipped ✓
   - Only true long silences should be skipped ✓

### Test Cases:

1. **Quiet Speech Test**:
   - Play content with soft-spoken narrator
   - ✓ All speech should be preserved

2. **Natural Pause Test**:
   - Play content with conversational pauses
   - ✓ Pauses < 800ms should NOT be skipped
   - ✓ Speech around pauses preserved due to padding

3. **True Silence Test**:
   - Play content with intro/outro silence
   - ✓ Silences (> 800ms) should be skipped

4. **Remote Stream Test**:
   - Play NPR or other remote stream
   - ✓ Even more conservative (preserves more audio)

## Known Limitations

1. **Music Detection**: Background music may prevent silence detection (by design)
2. **Variable Volume**: Audio with highly dynamic range may need tuning
3. **Network Streams**: Network artifacts can confuse detection (we compensate with conservative settings)
4. **Processing Time**: Initial scan takes a few seconds (background operation)

## Future Enhancements (Not Implemented)

Possible improvements:
- UI for adjusting parameters (currently requires manual config edit)
- Per-feed silence settings (some feeds need different thresholds)
- Machine learning-based speech detection (more accurate than VAD)
- Real-time adaptive thresholds based on content analysis
- Profile presets (podcast, audiobook, news, etc.)
