import unittest
import math
from core.audio_silence import (
    _rms,
    _dbfs,
    merge_ranges,
    merge_ranges_with_gap,
    StreamingSilenceDetector
)

class TestAudioSilence(unittest.TestCase):

    def test_rms(self):
        # Test with 16-bit mono
        chunk = b'\x00\x00\x00\x00' # 2 samples of 0
        self.assertEqual(0.0, _rms(chunk, 2, 1))

        chunk = b'\xff\x7f\xff\x7f' # 2 samples of 32767
        self.assertAlmostEqual(32767.0, _rms(chunk, 2, 1))

    def test_dbfs(self):
        self.assertAlmostEqual(-120.0, _dbfs(0))
        self.assertAlmostEqual(0.0, _dbfs(32768.0))

    def test_merge_ranges(self):
        ranges = [(0, 10), (10, 20), (30, 40)]
        self.assertEqual([(0, 20), (30, 40)], merge_ranges(ranges))

        ranges_with_overlap = [(0, 15), (10, 20), (30, 40)]
        self.assertEqual([(0, 20), (30, 40)], merge_ranges(ranges_with_overlap))

    def test_merge_ranges_with_gap(self):
        ranges = [(0, 10), (15, 25), (30, 40)]
        self.assertEqual([(0, 40)], merge_ranges_with_gap(ranges, 5))
        self.assertEqual([(0, 10), (15, 25), (30, 40)], merge_ranges_with_gap(ranges, 4))


    def test_streaming_silence_detector(self):
        detector = StreamingSilenceDetector(
            sample_rate=16000,
            sample_width=2,
            channels=1,
            window_ms=10,
            min_silence_ms=20, # 2 windows
            threshold_db=-40.0
        )

        # 10ms of silence, 10ms of sound, 20ms of silence
        silent_chunk = b'\x00\x00' * 160 # 10ms at 16kHz, 16-bit mono
        loud_chunk = b'\xff\x7f' * 160   # 10ms

        detector.feed(silent_chunk)
        detector.feed(loud_chunk)
        detector.feed(silent_chunk)
        detector.feed(silent_chunk)

        ranges = detector.finalize()
        self.assertEqual([(20, 40)], ranges)


if __name__ == '__main__':
    unittest.main()
