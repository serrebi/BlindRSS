from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from core.db import get_connection


@dataclass(frozen=True)
class PlaybackState:
    id: str
    position_ms: int
    duration_ms: Optional[int]
    updated_at: int
    completed: bool
    seek_supported: Optional[bool]
    title: Optional[str]


def get_playback_state(playback_id: str) -> Optional[PlaybackState]:
    if not playback_id:
        return None

    conn = get_connection()
    try:
        c = conn.cursor()
        c.execute(
            "SELECT id, position_ms, duration_ms, updated_at, completed, seek_supported, title "
            "FROM playback_state WHERE id = ?",
            (playback_id,),
        )
        row = c.fetchone()
        if not row:
            return None

        duration_ms = row[2]
        seek_supported = row[5]
        return PlaybackState(
            id=str(row[0]),
            position_ms=int(row[1] or 0),
            duration_ms=(int(duration_ms) if duration_ms is not None else None),
            updated_at=int(row[3] or 0),
            completed=bool(row[4] or 0),
            seek_supported=(None if seek_supported is None else bool(int(seek_supported))),
            title=(str(row[6]) if row[6] is not None else None),
        )
    finally:
        conn.close()


def upsert_playback_state(
    playback_id: str,
    position_ms: int,
    *,
    duration_ms: Optional[int] = None,
    title: Optional[str] = None,
    completed: bool = False,
    seek_supported: Optional[bool] = None,
    updated_at: Optional[int] = None,
) -> None:
    if not playback_id:
        return

    try:
        pos = max(0, int(position_ms))
    except Exception:
        pos = 0

    dur = None
    if duration_ms is not None:
        try:
            dur = int(duration_ms)
        except Exception:
            dur = None
        if dur is not None and dur <= 0:
            dur = None

    ts = int(updated_at if updated_at is not None else time.time())
    completed_i = 1 if bool(completed) else 0
    seek_i = None if seek_supported is None else (1 if bool(seek_supported) else 0)

    conn = get_connection()
    try:
        c = conn.cursor()
        c.execute(
            """
            INSERT INTO playback_state (id, position_ms, duration_ms, updated_at, completed, seek_supported, title)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                position_ms = excluded.position_ms,
                duration_ms = CASE
                    WHEN excluded.duration_ms IS NOT NULL THEN excluded.duration_ms
                    ELSE playback_state.duration_ms
                END,
                updated_at = excluded.updated_at,
                completed = excluded.completed,
                seek_supported = CASE
                    WHEN excluded.seek_supported IS NOT NULL THEN excluded.seek_supported
                    ELSE playback_state.seek_supported
                END,
                title = CASE
                    WHEN excluded.title IS NOT NULL THEN excluded.title
                    ELSE playback_state.title
                END
            """,
            (playback_id, pos, dur, ts, completed_i, seek_i, title),
        )
        conn.commit()
    finally:
        conn.close()


def delete_playback_state(playback_id: str) -> None:
    if not playback_id:
        return

    conn = get_connection()
    try:
        c = conn.cursor()
        c.execute("DELETE FROM playback_state WHERE id = ?", (playback_id,))
        conn.commit()
    finally:
        conn.close()


def set_seek_supported(playback_id: str, seek_supported: bool) -> None:
    if not playback_id:
        return

    conn = get_connection()
    try:
        c = conn.cursor()
        c.execute(
            "UPDATE playback_state SET seek_supported = ?, updated_at = ? WHERE id = ?",
            (1 if bool(seek_supported) else 0, int(time.time()), playback_id),
        )
        conn.commit()
    finally:
        conn.close()
