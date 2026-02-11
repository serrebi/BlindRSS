from core.config import DEFAULT_CONFIG


def test_default_preferred_soundcard_is_system_default():
    assert DEFAULT_CONFIG.get("preferred_soundcard", None) == ""

