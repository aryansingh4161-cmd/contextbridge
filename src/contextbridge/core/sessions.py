"""Working sessions (minimal in M2; checkpoints and capture tracking are M3).

A session is identified per project by its label (e.g. "2026-10-31 desktop"), or by an
external key such as a Claude Code session_id. Callers inside a write transaction pass `conn`.
"""

from __future__ import annotations

import sqlite3

from .db.connection import now_iso

LABEL_MAX = 120


def get_or_create_session(conn: sqlite3.Connection, project_id: int, label: str | None,
                          client: str | None = None) -> int | None:
    """Return the open session with this label, creating it if needed. None if no label."""
    if not label or not label.strip():
        return None
    label = label.strip()[:LABEL_MAX]
    row = conn.execute(
        "SELECT id FROM sessions WHERE project_id = ? AND label = ? AND ended_at IS NULL ORDER BY id DESC",
        (project_id, label)).fetchone()
    if row:
        return int(row["id"])
    cur = conn.execute(
        "INSERT INTO sessions(project_id, label, client, started_at) VALUES (?,?,?,?)",
        (project_id, label, client, now_iso()))
    return int(cur.lastrowid or 0)
