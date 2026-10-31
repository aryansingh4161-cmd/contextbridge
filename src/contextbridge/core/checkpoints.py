"""`checkpoint`: the cheap, atomic, periodic save (spec §7.1 #6, P5).

One transaction: session (get/create) → optional items (knowledge pipeline) → optional state →
checkpoint row → session summary/where_stopped/next_action → snapshots of knowledge and state
(deduplicated, so unchanged components cost nothing). Either everything lands or nothing does.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from .db.connection import now_iso, write_tx
from .errors import CBError
from .knowledge import EntryIn, remember_tx
from .security.redact import redact
from .sessions import get_or_create_session
from .snapshots import create_snapshot_tx
from .state import SectionIn, TaskIn, update_state_tx

CheckpointKind = Literal["manual", "auto", "emergency", "handoff"]
TEXT_MAX = 4000


@dataclass
class CheckpointIn:
    summary: str
    where_stopped: str
    next_action: str
    session_label: str | None = None
    client: str | None = None
    account_label: str | None = None
    items: list[EntryIn] | None = None
    sections: dict[str, SectionIn] | None = None
    tasks: list[TaskIn] | None = None
    kind: CheckpointKind = "manual"


def _clean(name: str, value: str, redactions: list[dict[str, Any]]) -> str:
    v = (value or "").strip()
    if not v:
        raise CBError("invalid_input", f"{name} is required",
                      "one or two sentences; for where_stopped be concrete (file, step, last result)")
    if len(v) > TEXT_MAX:
        raise CBError("too_large", f"{name} is {len(v)} chars; max is {TEXT_MAX}")
    new, found = redact(v)
    redactions.extend({"where": name, "type": f.type, "count": f.count} for f in found)
    return new


def default_label(client: str | None) -> str:
    return f"{datetime.now().astimezone().date().isoformat()} {client or 'session'}"


def checkpoint(conn: sqlite3.Connection, project_id: int, cp: CheckpointIn, *,
               near_duplicate_threshold: float = 0.85) -> dict[str, Any]:
    redactions: list[dict[str, Any]] = []
    summary = _clean("summary", cp.summary, redactions)
    where = _clean("where_stopped", cp.where_stopped, redactions)
    nxt = _clean("next_action", cp.next_action, redactions)
    out: dict[str, Any] = {}
    with write_tx(conn):
        sid = get_or_create_session(conn, project_id, cp.session_label or default_label(cp.client), cp.client)
        assert sid is not None
        if cp.items:
            out["item_results"] = remember_tx(conn, project_id, cp.items, session_id=sid,
                                              near_duplicate_threshold=near_duplicate_threshold).to_dict()
        if cp.sections or cp.tasks:
            out["state_results"] = update_state_tx(conn, project_id, cp.sections, cp.tasks, sid).to_dict()
        ts = now_iso()
        cur = conn.execute(
            "INSERT INTO checkpoints(session_id, project_id, summary, where_stopped, next_action, kind, created_at)"
            " VALUES (?,?,?,?,?,?,?)", (sid, project_id, summary, where, nxt, cp.kind, ts))
        conn.execute(
            """UPDATE sessions SET summary = ?, where_stopped = ?, next_action = ?,
                 account_label = coalesce(?, account_label), client = coalesce(?, client),
                 capture = CASE capture WHEN 'none' THEN 'checkpoints' WHEN 'transcript' THEN 'both' ELSE capture END
               WHERE id = ?""", (summary, where, nxt, cp.account_label, cp.client, sid))
        k_snap, k_new = create_snapshot_tx(conn, project_id, "knowledge", label=f"checkpoint {cur.lastrowid}")
        s_snap, s_new = create_snapshot_tx(conn, project_id, "state", label=f"checkpoint {cur.lastrowid}")
    out |= {"checkpoint_id": cur.lastrowid, "session_id": sid, "created_at": ts,
            "snapshots": {"knowledge": {"id": k_snap, "new": k_new}, "state": {"id": s_snap, "new": s_new}}}
    if redactions:
        out["redactions"] = redactions
    return out


def end_session(conn: sqlite3.Connection, project_id: int, session_id: int) -> None:
    with write_tx(conn):
        conn.execute("UPDATE sessions SET ended_at = ? WHERE id = ? AND project_id = ? AND ended_at IS NULL",
                     (now_iso(), session_id, project_id))


def latest_checkpoint(conn: sqlite3.Connection, project_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        """SELECT c.*, s.label AS session_label, s.client, s.account_label FROM checkpoints c
           JOIN sessions s ON s.id = c.session_id WHERE c.project_id = ? ORDER BY c.id DESC LIMIT 1""",
        (project_id,)).fetchone()
    return dict(row) if row else None
