"""Versioned, compressed snapshots of the three components (spec P2, §5.4): knowledge, state, handoff.

- Payloads are canonical JSON, zstd-compressed (stdlib `compression.zstd`, Python 3.14).
- An unchanged component is not snapshotted twice (content_hash dedupe).
- Restore always snapshots the current version first, so every restore is reversible.
- Knowledge restore is non-destructive: entries created after the snapshot become 'outdated'.
- Retention: keep everything for `keep_all_days`, then one per component per day; the newest
  snapshot of each component is never pruned.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import sqlite3
from datetime import datetime, timedelta
from typing import Any, Literal

from compression import zstd

from .db.connection import now_iso, write_tx
from .errors import CBError

Component = Literal["knowledge", "state", "handoff"]
COMPONENTS: tuple[Component, ...] = ("knowledge", "state", "handoff")
FORMAT = 1
VOLATILE = frozenset({"updated_at", "version", "last_verified_at"})


def _canon(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _rows(conn: sqlite3.Connection, sql: str, pid: int) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(sql, (pid,))]


def build_payload(conn: sqlite3.Connection, project_id: int, component: Component,
                  handoff: dict[str, Any] | None = None) -> dict[str, Any]:
    p = conn.execute("SELECT slug, knowledge_version, state_version FROM projects WHERE id = ?",
                     (project_id,)).fetchone()
    base: dict[str, Any] = {"format": FORMAT, "component": component, "project": p["slug"]}
    if component == "knowledge":
        return base | {"knowledge_version": p["knowledge_version"],
                       "entries": _rows(conn, "SELECT * FROM entries WHERE project_id = ? ORDER BY id", project_id),
                       "conflicts": _rows(conn, "SELECT * FROM conflicts WHERE project_id = ? ORDER BY id", project_id)}
    if component == "state":
        return base | {"state_version": p["state_version"],
                       "sections": _rows(conn, "SELECT section, content, refs FROM live_state"
                                               " WHERE project_id = ? AND content != '' ORDER BY section", project_id),
                       "tasks": _rows(conn, "SELECT * FROM tasks WHERE project_id = ? ORDER BY id", project_id)}
    if handoff is None:
        raise CBError("invalid_input", "a handoff snapshot needs the handoff payload")
    return base | {"handoff": handoff}


def create_snapshot_tx(conn: sqlite3.Connection, project_id: int, component: Component, *,
                       label: str | None = None, reason: str | None = None,
                       handoff: dict[str, Any] | None = None) -> tuple[int, bool]:
    """Caller holds the transaction. Returns (snapshot_id, created)."""
    payload = build_payload(conn, project_id, component, handoff)
    raw = _canon(payload)
    digest = hashlib.sha256(raw).hexdigest()
    last = conn.execute("SELECT id, content_hash FROM snapshots WHERE project_id = ? AND component = ?"
                        " ORDER BY id DESC LIMIT 1", (project_id, component)).fetchone()
    if last is not None and last["content_hash"] == digest:
        return int(last["id"]), False
    version = payload.get(f"{component}_version", 0) if component != "handoff" else (
        conn.execute("SELECT count(*) FROM snapshots WHERE project_id = ? AND component = 'handoff'",
                     (project_id,)).fetchone()[0] + 1)
    cur = conn.execute(
        "INSERT INTO snapshots(project_id, component, version, label, reason, content_hash, codec, payload,"
        " created_at) VALUES (?,?,?,?,?,?,'zstd',?,?)",
        (project_id, component, version, label, reason, digest, zstd.compress(raw), now_iso()))
    return int(cur.lastrowid or 0), True


def create_snapshot(conn: sqlite3.Connection, project_id: int, component: Component, **kw: Any) -> tuple[int, bool]:
    with write_tx(conn):
        return create_snapshot_tx(conn, project_id, component, **kw)


def list_snapshots(conn: sqlite3.Connection, project_id: int,
                   component: Component | None = None) -> list[dict[str, Any]]:
    sql = ("SELECT id, component, version, label, reason, content_hash, length(payload) AS bytes, created_at"
           " FROM snapshots WHERE project_id = ?")
    args: list[Any] = [project_id]
    if component:
        sql += " AND component = ?"
        args.append(component)
    return [dict(r) for r in conn.execute(sql + " ORDER BY id DESC", args)]


def load_snapshot(conn: sqlite3.Connection, project_id: int, snapshot_id: int) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM snapshots WHERE id = ? AND project_id = ?",
                       (snapshot_id, project_id)).fetchone()
    if row is None:
        raise CBError("not_found", f"snapshot {snapshot_id} not found in this project",
                      "list them with `ctxb snapshots <project>`")
    try:
        raw = zstd.decompress(row["payload"]) if row["codec"] == "zstd" else bytes(row["payload"])
        if hashlib.sha256(raw).hexdigest() != row["content_hash"]:
            raise ValueError("hash mismatch")
        data: dict[str, Any] = json.loads(raw)
    except (zstd.ZstdError, ValueError) as exc:
        raise CBError("internal", f"snapshot {snapshot_id} is corrupt ({exc})",
                      "restore from an earlier snapshot or a DB backup") from exc
    return data


# ---------------------------------------------------------------- diff

def _strip(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if k not in VOLATILE}


def _diff_rows(a: list[dict[str, Any]], b: list[dict[str, Any]], key: str) -> dict[str, Any]:
    ma, mb = {r[key]: _strip(r) for r in a}, {r[key]: _strip(r) for r in b}
    changed = []
    for k in sorted(set(ma) & set(mb), key=str):
        fields = {f: [ma[k].get(f), mb[k].get(f)] for f in sorted(set(ma[k]) | set(mb[k])) if ma[k].get(f) != mb[k].get(f)}
        if fields:
            changed.append({key: k, "fields": fields})
    return {"added": sorted(set(mb) - set(ma), key=str), "removed": sorted(set(ma) - set(mb), key=str),
            "changed": changed}


def diff_payloads(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    if a["component"] != b["component"]:
        raise CBError("invalid_input", f"cannot diff {a['component']} against {b['component']}")
    comp = a["component"]
    if comp == "knowledge":
        return {"component": comp, "entries": _diff_rows(a["entries"], b["entries"], "id"),
                "conflicts": _diff_rows(a["conflicts"], b["conflicts"], "id")}
    if comp == "state":
        sa = {s["section"]: s["content"] for s in a["sections"]}
        sb = {s["section"]: s["content"] for s in b["sections"]}
        sections = {}
        for name in sorted(set(sa) | set(sb)):
            if sa.get(name) != sb.get(name):
                sections[name] = list(difflib.unified_diff(
                    (sa.get(name) or "").splitlines(), (sb.get(name) or "").splitlines(),
                    "before", "after", lineterm="", n=1))
        return {"component": comp, "sections": sections, "tasks": _diff_rows(a["tasks"], b["tasks"], "id")}
    return {"component": comp, "changed": a.get("handoff") != b.get("handoff")}


def diff(conn: sqlite3.Connection, project_id: int, a_id: int, b_id: int | None) -> dict[str, Any]:
    """Diff snapshot a against snapshot b, or against the current state if b_id is None."""
    a = load_snapshot(conn, project_id, a_id)
    b = load_snapshot(conn, project_id, b_id) if b_id is not None else build_payload(conn, project_id, a["component"])
    return diff_payloads(a, b)


# ---------------------------------------------------------------- restore

ENTRY_COLS = ("session_id", "kind", "topic_key", "title", "body", "rationale", "alternatives", "status", "provenance",
              "confidence", "importance", "supersedes_id", "superseded_by_id", "source_ref", "norm_hash",
              "created_at", "updated_at", "last_verified_at")


def _restore_knowledge(conn: sqlite3.Connection, pid: int, snap: dict[str, Any], snap_id: int) -> dict[str, int]:
    from .knowledge import _history  # local import: knowledge imports nothing from here

    ts = now_iso()
    in_snap = {e["id"]: e for e in snap["entries"]}
    current = {r["id"]: r for r in conn.execute("SELECT * FROM entries WHERE project_id = ?", (pid,))}
    counts = {"restored": 0, "reinserted": 0, "outdated": 0}
    for eid, e in in_snap.items():
        if eid in current:
            if _strip(dict(current[eid])) == _strip(e):
                continue
            sets = ", ".join(f"{c} = ?" for c in ENTRY_COLS)
            conn.execute(f"UPDATE entries SET {sets}, version = version + 1 WHERE id = ?",
                         (*(e[c] for c in ENTRY_COLS), eid))
            counts["restored"] += 1
        else:
            if conn.execute("SELECT 1 FROM entries WHERE id = ?", (eid,)).fetchone():
                raise CBError("internal", f"entry id {eid} is now used by another project; cannot restore")
            cols = ("id", "project_id", *ENTRY_COLS, "version")
            conn.execute(f"INSERT INTO entries({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                         (eid, pid, *(e[c] for c in ENTRY_COLS), e["version"]))
            counts["reinserted"] += 1
        _history(conn, eid, "restore", None)
    for eid, row in current.items():
        if eid not in in_snap and row["status"] != "outdated":
            conn.execute("UPDATE entries SET status = 'outdated', version = version + 1, updated_at = ? WHERE id = ?",
                         (ts, eid))
            _history(conn, eid, "restore", None)
            counts["outdated"] += 1
    snap_conflicts = {c["id"]: c for c in snap["conflicts"]}
    for c in conn.execute("SELECT id FROM conflicts WHERE project_id = ?", (pid,)).fetchall():
        s = snap_conflicts.get(c["id"])
        if s is None:
            conn.execute("UPDATE conflicts SET status = 'resolved', note = ?, resolved_at = ? WHERE id = ?",
                         (f"closed by restore of snapshot {snap_id}", ts, c["id"]))
        else:
            conn.execute("UPDATE conflicts SET status = ?, resolution = ?, note = ?, resolved_at = ? WHERE id = ?",
                         (s["status"], s["resolution"], s["note"], s["resolved_at"], c["id"]))
    conn.execute("UPDATE projects SET knowledge_version = knowledge_version + 1, updated_at = ? WHERE id = ?", (ts, pid))
    return counts


def _restore_state(conn: sqlite3.Connection, pid: int, snap: dict[str, Any]) -> dict[str, int]:
    from .state import SectionIn, StateResult, _upsert_section

    res = StateResult()
    snap_secs = {s["section"]: s for s in snap["sections"]}
    present = [r["section"] for r in conn.execute("SELECT section FROM live_state WHERE project_id = ? AND content != ''",
                                                  (pid,))]
    for name, s in snap_secs.items():
        _upsert_section(conn, pid, name, SectionIn(s["content"], json.loads(s["refs"]) if s["refs"] else None), None, res)
    for name in present:
        if name not in snap_secs:
            _upsert_section(conn, pid, name, SectionIn(""), None, res)
    snap_tasks = {t["id"]: t for t in snap["tasks"]}
    cols = ("title", "status", "priority", "depends_on", "expected_outcome", "notes", "created_at", "updated_at")
    deleted = 0
    for (tid,) in conn.execute("SELECT id FROM tasks WHERE project_id = ?", (pid,)).fetchall():
        if tid not in snap_tasks:
            conn.execute("DELETE FROM tasks WHERE id = ?", (tid,))
            deleted += 1
    for tid, t in snap_tasks.items():
        conn.execute(f"INSERT INTO tasks(id, project_id, {', '.join(cols)}) VALUES (?,?,{', '.join('?' * len(cols))})"
                     f" ON CONFLICT(id) DO UPDATE SET {', '.join(f'{c} = excluded.{c}' for c in cols)}",
                     (tid, pid, *(t[c] for c in cols)))
    conn.execute("UPDATE projects SET state_version = state_version + 1, updated_at = ? WHERE id = ?", (now_iso(), pid))
    return {"sections_changed": len(res.updated_sections), "tasks_restored": len(snap_tasks), "tasks_removed": deleted}


def restore(conn: sqlite3.Connection, project_id: int, snapshot_id: int) -> dict[str, Any]:
    snap = load_snapshot(conn, project_id, snapshot_id)
    comp: Component = snap["component"]
    if comp == "handoff":
        raise CBError("invalid_input", "handoff snapshots are archives of generated files; nothing to restore",
                      "regenerate with prepare_handoff")
    with write_tx(conn):
        # Re-inserted rows may reference each other (supersedes_id); check FKs at COMMIT.
        conn.execute("PRAGMA defer_foreign_keys = ON")
        pre_id, _ = create_snapshot_tx(conn, project_id, comp, label="pre-restore",
                                       reason=f"automatic, before restoring snapshot {snapshot_id}")
        counts = (_restore_knowledge(conn, project_id, snap, snapshot_id) if comp == "knowledge"
                  else _restore_state(conn, project_id, snap))
    return {"restored": snapshot_id, "component": comp, "pre_restore_snapshot": pre_id, "counts": counts,
            "undo": f"ctxb restore <project> {pre_id}"}


# ---------------------------------------------------------------- retention

def prune_snapshots(conn: sqlite3.Connection, project_id: int, keep_all_days: int = 30) -> int:
    """Keep all snapshots newer than keep_all_days; older ones: newest per component per day.
    The newest snapshot of each component is always kept. Returns number deleted."""
    cutoff = datetime.now().astimezone() - timedelta(days=keep_all_days)
    rows = conn.execute("SELECT id, component, created_at FROM snapshots WHERE project_id = ? ORDER BY id DESC",
                        (project_id,)).fetchall()
    newest: set[str] = set()
    seen_day: set[tuple[str, str]] = set()
    doomed: list[int] = []
    for r in rows:
        comp, ts = r["component"], datetime.fromisoformat(r["created_at"])
        if comp not in newest:
            newest.add(comp)
            seen_day.add((comp, ts.date().isoformat()))
            continue
        if ts >= cutoff:
            seen_day.add((comp, ts.date().isoformat()))
            continue
        day = (comp, ts.date().isoformat())
        if day in seen_day:
            doomed.append(int(r["id"]))
        else:
            seen_day.add(day)
    with write_tx(conn):
        conn.executemany("DELETE FROM snapshots WHERE id = ?", [(i,) for i in doomed])
    return len(doomed)
