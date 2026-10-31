"""Live State: current-state sections + tasks (spec §5.3).

Hard rule (P2, acceptance Test 14): nothing in this module writes to `entries`. Every change
keeps the previous version in live_state_history / task_history and bumps state_version.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from .db.connection import now_iso, write_tx
from .errors import CBError
from .security.redact import redact

SECTIONS = ("architecture", "stack", "implemented", "in_progress", "broken", "needs_testing", "postponed",
            "blockers", "dependencies", "outstanding_decisions", "environment", "repo")
TASK_STATUSES = ("todo", "in_progress", "blocked", "done", "postponed")
SECTION_MAX, TASK_TEXT_MAX, TASKS_MAX = 20_000, 2_000, 100


@dataclass
class SectionIn:
    content: str
    refs: list[int] | None = None


@dataclass
class TaskIn:
    id: int | None = None
    title: str | None = None
    status: str | None = None
    priority: int | None = None
    depends_on: list[int] | None = None
    expected_outcome: str | None = None
    notes: str | None = None


@dataclass
class StateResult:
    updated_sections: list[str] = field(default_factory=list)
    unchanged_sections: list[str] = field(default_factory=list)
    task_results: list[dict[str, Any]] = field(default_factory=list)
    redactions: list[dict[str, Any]] = field(default_factory=list)
    state_version: int = 0

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"updated_sections": self.updated_sections, "state_version": self.state_version}
        if self.unchanged_sections:
            out["unchanged_sections"] = self.unchanged_sections
        if self.task_results:
            out["task_results"] = self.task_results
        if self.redactions:
            out["redactions"] = self.redactions
        return out


def _redacted(value: str, where: str, report: list[dict[str, Any]]) -> str:
    new, found = redact(value)
    report.extend({"where": where, "type": f.type, "count": f.count} for f in found)
    return new


def _check_refs(conn: sqlite3.Connection, project_id: int, refs: list[int]) -> None:
    for r in refs:
        if conn.execute("SELECT 1 FROM entries WHERE id = ? AND project_id = ?", (r, project_id)).fetchone() is None:
            raise CBError("invalid_input", f"ref {r} is not an entry in this project")


def _upsert_section(conn: sqlite3.Connection, project_id: int, name: str, sec: SectionIn,
                    session_id: int | None, res: StateResult) -> None:
    if name not in SECTIONS:
        raise CBError("invalid_input", f"unknown section '{name}'", f"use one of: {', '.join(SECTIONS)}")
    content = sec.content.strip()
    if len(content) > SECTION_MAX:
        raise CBError("too_large", f"section '{name}' is {len(content)} chars; max is {SECTION_MAX}")
    content = _redacted(content, f"section:{name}", res.redactions)
    refs = sorted(set(sec.refs or []))
    _check_refs(conn, project_id, refs)
    refs_json = json.dumps(refs) if refs else None
    ts = now_iso()
    row = conn.execute("SELECT * FROM live_state WHERE project_id = ? AND section = ?", (project_id, name)).fetchone()
    if row is None:
        cur = conn.execute("INSERT INTO live_state(project_id, section, content, refs, version, updated_at)"
                           " VALUES (?,?,?,?,1,?)", (project_id, name, content, refs_json, ts))
        sid, version = int(cur.lastrowid or 0), 1
    else:
        if row["content"] == content and row["refs"] == refs_json:
            res.unchanged_sections.append(name)
            return
        sid, version = int(row["id"]), int(row["version"]) + 1
        conn.execute("UPDATE live_state SET content = ?, refs = ?, version = ?, updated_at = ? WHERE id = ?",
                     (content, refs_json, version, ts, sid))
    # history holds every version, including the newest, so any two can be diffed
    conn.execute("INSERT INTO live_state_history(live_state_id, version, content, refs, changed_at, session_id)"
                 " VALUES (?,?,?,?,?,?)", (sid, version, content, refs_json, ts, session_id))
    res.updated_sections.append(name)


def _task_snapshot(conn: sqlite3.Connection, task_id: int, session_id: int | None) -> None:
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    conn.execute("INSERT INTO task_history(task_id, snapshot_json, changed_at, session_id) VALUES (?,?,?,?)",
                 (task_id, json.dumps(dict(row), ensure_ascii=False), now_iso(), session_id))


def _upsert_task(conn: sqlite3.Connection, project_id: int, t: TaskIn, index: int,
                 session_id: int | None, res: StateResult) -> None:
    def fail(reason: str) -> None:
        res.task_results.append({"index": index, "status": "rejected", "reason": reason})

    if t.status is not None and t.status not in TASK_STATUSES:
        return fail(f"status '{t.status}' is invalid; use one of: {', '.join(TASK_STATUSES)}")
    for name in ("title", "expected_outcome", "notes"):
        v = getattr(t, name)
        if v is not None:
            if len(v) > TASK_TEXT_MAX:
                return fail(f"{name} is longer than {TASK_TEXT_MAX} chars")
            setattr(t, name, _redacted(v.strip(), f"task[{index}].{name}", res.redactions))
    for dep in t.depends_on or []:
        if conn.execute("SELECT 1 FROM tasks WHERE id = ? AND project_id = ?", (dep, project_id)).fetchone() is None:
            return fail(f"depends_on task {dep} is not in this project")
        if dep == t.id:
            return fail("a task cannot depend on itself")
    deps = json.dumps(sorted(set(t.depends_on))) if t.depends_on is not None else None
    ts = now_iso()
    if t.id is None:
        if not t.title:
            return fail("title is required for a new task")
        cur = conn.execute(
            "INSERT INTO tasks(project_id, title, status, priority, depends_on, expected_outcome, notes,"
            " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (project_id, t.title, t.status or "todo", t.priority, deps, t.expected_outcome, t.notes, ts, ts))
        tid = int(cur.lastrowid or 0)
        _task_snapshot(conn, tid, session_id)
        res.task_results.append({"index": index, "status": "created", "id": tid})
        return None
    row = conn.execute("SELECT * FROM tasks WHERE id = ? AND project_id = ?", (t.id, project_id)).fetchone()
    if row is None:
        return fail(f"task {t.id} not found in this project")
    fields = {"title": t.title, "status": t.status, "priority": t.priority, "depends_on": deps,
              "expected_outcome": t.expected_outcome, "notes": t.notes}
    changes = {k: v for k, v in fields.items() if v is not None and v != row[k]}
    if not changes:
        res.task_results.append({"index": index, "status": "unchanged", "id": t.id})
        return None
    sets = ", ".join(f"{k} = ?" for k in changes)
    conn.execute(f"UPDATE tasks SET {sets}, updated_at = ? WHERE id = ?", (*changes.values(), ts, t.id))
    _task_snapshot(conn, t.id, session_id)
    res.task_results.append({"index": index, "status": "updated", "id": t.id, "changed": sorted(changes)})
    return None


def update_state_tx(conn: sqlite3.Connection, project_id: int, sections: dict[str, SectionIn] | None,
                    tasks: list[TaskIn] | None, session_id: int | None = None) -> StateResult:
    """Caller holds the write transaction. Section errors abort (all-or-nothing on sections);
    task errors are reported per task."""
    sections, tasks = sections or {}, tasks or []
    if len(tasks) > TASKS_MAX:
        raise CBError("too_large", f"at most {TASKS_MAX} tasks per call")
    res = StateResult()
    for name, sec in sections.items():
        _upsert_section(conn, project_id, name, sec, session_id, res)
    for i, t in enumerate(tasks):
        _upsert_task(conn, project_id, t, i, session_id, res)
    changed = bool(res.updated_sections) or any(r["status"] in ("created", "updated") for r in res.task_results)
    if changed:
        conn.execute("UPDATE projects SET state_version = state_version + 1, updated_at = ? WHERE id = ?",
                     (now_iso(), project_id))
    res.state_version = int(conn.execute("SELECT state_version FROM projects WHERE id = ?",
                                         (project_id,)).fetchone()[0])
    return res


def update_state(conn: sqlite3.Connection, project_id: int, sections: dict[str, SectionIn] | None = None,
                 tasks: list[TaskIn] | None = None, session_id: int | None = None) -> StateResult:
    if not sections and not tasks:
        raise CBError("invalid_input", "nothing to do: pass sections and/or tasks")
    with write_tx(conn):
        return update_state_tx(conn, project_id, sections, tasks, session_id)


def get_state(conn: sqlite3.Connection, project_id: int) -> dict[str, Any]:
    secs = {r["section"]: {"content": r["content"], "refs": json.loads(r["refs"]) if r["refs"] else [],
                           "version": r["version"], "updated_at": r["updated_at"]}
            for r in conn.execute("SELECT * FROM live_state WHERE project_id = ? AND content != ''"
                                  " ORDER BY section", (project_id,))}
    tasks = [dict(r) | {"depends_on": json.loads(r["depends_on"]) if r["depends_on"] else []}
             for r in conn.execute("SELECT * FROM tasks WHERE project_id = ? ORDER BY "
                                   "CASE status WHEN 'in_progress' THEN 0 WHEN 'blocked' THEN 1 WHEN 'todo' THEN 2 "
                                   "WHEN 'postponed' THEN 3 ELSE 4 END, coalesce(priority, 99), id", (project_id,))]
    version = conn.execute("SELECT state_version FROM projects WHERE id = ?", (project_id,)).fetchone()[0]
    return {"sections": secs, "tasks": tasks, "state_version": version}
