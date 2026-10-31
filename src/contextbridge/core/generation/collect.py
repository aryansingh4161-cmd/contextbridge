"""Gather everything a handoff may draw on, in one read (spec P2: the handoff uses ALL knowledge).

Read-only. Runs inside one implicit read transaction so the package reflects a single
consistent version of the database even while another process writes.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from ..checkpoints import latest_checkpoint
from ..consistency import check as consistency_check
from ..knowledge import list_conflicts
from ..state import get_state

KIND_PREFIX = {
    "decision": "D", "requirement": "R", "rejected_approach": "X", "constraint": "C", "preference": "P",
    "fact": "F", "explanation": "E", "bug": "B", "fix": "FX", "lesson": "L", "milestone": "M",
    "risk": "K", "open_question": "Q", "reference": "REF", "convention": "CV",
}


def tag(e: dict[str, Any]) -> str:
    """Traceable tag, e.g. `[D-0123 · user_confirmed · 2026-10-10]` (spec §8.2)."""
    bits = [f"{KIND_PREFIX.get(e['kind'], 'E')}-{e['id']:04d}", e["provenance"], str(e["created_at"])[:10]]
    if e["status"] not in ("active",):
        bits.append(e["status"])
    return "[" + " · ".join(bits) + "]"


@dataclass
class ProjectData:
    project: dict[str, Any]
    entries: list[dict[str, Any]]
    by_id: dict[int, dict[str, Any]]
    conflicts: list[dict[str, Any]]
    state: dict[str, Any]
    sessions: list[dict[str, Any]]
    latest_checkpoint: dict[str, Any] | None
    consistency: list[dict[str, Any]]
    file_refs: list[dict[str, Any]]
    last_handoff_at: str | None
    archive_count: int
    writes_since_checkpoint: int = 0
    repo: dict[str, Any] | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def latest_session_id(self) -> int | None:
        cp = self.latest_checkpoint
        return int(cp["session_id"]) if cp else (int(self.sessions[-1]["id"]) if self.sessions else None)


def collect(conn: sqlite3.Connection, project_id: int) -> ProjectData:
    from ..status import checkpoint_status

    conn.execute("BEGIN")  # one snapshot of the DB for the whole read (WAL readers never block writers)
    try:
        project = dict(conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone())
        entries = []
        for r in conn.execute("SELECT * FROM entries WHERE project_id = ? ORDER BY id", (project_id,)):
            e = dict(r)
            e["alternatives"] = json.loads(e["alternatives"]) if e["alternatives"] else []
            e["tag"] = tag(e)
            entries.append(e)
        sessions = [dict(r) | {"entries": 0} for r in conn.execute(
            "SELECT id, label, client, account_label, started_at, ended_at, capture, summary FROM sessions"
            " WHERE project_id = ? ORDER BY started_at, id", (project_id,))]
        counts = dict(conn.execute("SELECT session_id, count(*) FROM entries WHERE project_id = ?"
                                   " GROUP BY session_id", (project_id,)).fetchall())
        for s in sessions:
            s["entries"] = counts.get(s["id"], 0)
        data = ProjectData(
            project=project,
            entries=entries,
            by_id={e["id"]: e for e in entries},
            conflicts=list_conflicts(conn, project_id),
            state=get_state(conn, project_id),
            sessions=sessions,
            latest_checkpoint=latest_checkpoint(conn, project_id),
            consistency=[w for w in consistency_check(conn, project_id) if w["code"] != "open_conflicts"],
            file_refs=[dict(r) for r in conn.execute("SELECT * FROM file_refs WHERE project_id = ? ORDER BY path",
                                                     (project_id,))],
            last_handoff_at=conn.execute("SELECT max(created_at) FROM handoff_runs WHERE project_id = ?",
                                         (project_id,)).fetchone()[0],
            archive_count=conn.execute("SELECT count(*) FROM raw_archive WHERE project_id = ?",
                                       (project_id,)).fetchone()[0],
            writes_since_checkpoint=int(checkpoint_status(conn, project_id)["writes_since_checkpoint"]),
        )
        data.extra["entries_without_session"] = counts.get(None, 0)
    finally:
        conn.execute("COMMIT")
    return data
