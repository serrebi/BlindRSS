import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.range_cache_proxy import _parse_range_header


def test_parse_range_open_ended_keeps_end_none_when_length_known():
    # Open-ended ranges should keep end=None so the caller can clamp
    # to a small inline window for responsive startup.
    assert _parse_range_header("bytes=0-", 1000) == (0, None)


def test_parse_range_open_ended_keeps_end_none_when_length_unknown():
    assert _parse_range_header("bytes=512-", None) == (512, None)


def test_parse_range_clamps_end_to_length():
    # Explicit end should still be clamped to known length.
    assert _parse_range_header("bytes=100-200", 150) == (100, 149)
