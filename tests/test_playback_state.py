import os
import tempfile

import core.db
from core import playback_state


def test_playback_state_roundtrip_and_updates():
    with tempfile.TemporaryDirectory() as tmp:
        orig_db_file = core.db.DB_FILE
        core.db.DB_FILE = os.path.join(tmp, "rss.db")
        try:
            core.db.init_db()

            pid = "https://example.com/episode/1"
            assert playback_state.get_playback_state(pid) is None

            playback_state.upsert_playback_state(
                pid,
                12345,
                duration_ms=60000,
                title="Episode 1",
                completed=False,
            )
            st = playback_state.get_playback_state(pid)
            assert st is not None
            assert st.id == pid
            assert st.position_ms == 12345
            assert st.duration_ms == 60000
            assert st.completed is False
            assert st.seek_supported is None
            assert st.title == "Episode 1"

            # duration/title/seek_supported should not be overwritten by None.
            playback_state.upsert_playback_state(pid, 23456, duration_ms=None, title=None, completed=False, seek_supported=None)
            st2 = playback_state.get_playback_state(pid)
            assert st2 is not None
            assert st2.position_ms == 23456
            assert st2.duration_ms == 60000
            assert st2.title == "Episode 1"
            assert st2.seek_supported is None

            playback_state.set_seek_supported(pid, False)
            st3 = playback_state.get_playback_state(pid)
            assert st3 is not None
            assert st3.seek_supported is False

            playback_state.upsert_playback_state(pid, 0, duration_ms=60000, completed=True)
            st4 = playback_state.get_playback_state(pid)
            assert st4 is not None
            assert st4.completed is True
            assert st4.position_ms == 0
        finally:
            core.db.DB_FILE = orig_db_file

