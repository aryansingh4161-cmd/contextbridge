"""`checkpoint_status` block attached to every project-scoped tool response (spec §10.6).

It reports the loss window honestly (BLUEPRINT §0): how long since the last durable checkpoint
and how many writes happened since. It never claims work is "safe".
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

CHECKPOINT_AFTER_MIN = 25
CHECKPOINT_AFTER_WRITES = 10


def checkpoint_status(conn: sqlite3.Connection, project_id: int) -> dict[str, Any]:
    last = conn.execute("SELECT max(created_at) FROM checkpoints WHERE project_id = ?",
                        (project_id,)).fetchone()[0]
    since = last or "0000"
    writes = conn.execute(
        """SELECT (SELECT count(*) FROM entry_history h JOIN entries e ON e.id = h.entry_id
                    WHERE e.project_id = :p AND h.changed_at > :t)
                + (SELECT count(*) FROM live_state_history h JOIN live_state s ON s.id = h.live_state_id
                    WHERE s.project_id = :p AND h.changed_at > :t)
                + (SELECT count(*) FROM task_history h JOIN tasks k ON k.id = h.task_id
                    WHERE k.project_id = :p AND h.changed_at > :t)""",
        {"p": project_id, "t": since}).fetchone()[0]
    minutes = None
    if last:
        minutes = max(0, int((datetime.now().astimezone() - datetime.fromisoformat(last)).total_seconds() // 60))
    if last is None:
        rec = "call checkpoint" if writes else "none"
    elif minutes is not None and minutes >= CHECKPOINT_AFTER_MIN or writes >= CHECKPOINT_AFTER_WRITES:
        rec = "call checkpoint"
    else:
        rec = "none"
    return {
        "last_checkpoint_at": last,
        "minutes_since_checkpoint": minutes,
        "writes_since_checkpoint": writes,
        "unsaved_hint": ("no checkpoint yet" if last is None else f"unsaved since {last}") if writes else None,
        "recommendation": rec,
        "telemetry": "unavailable",  # filled by the budget monitor (M8) where a client provides it
    }
