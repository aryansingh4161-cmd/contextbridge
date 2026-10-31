"""Projects repository. Every function takes an explicit connection; there is no hidden
"current project" anywhere in the core (spec §4)."""

from __future__ import annotations

import difflib
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .db.connection import now_iso, write_tx
from .errors import CBError
from .slug import is_valid_slug, make_slug

NAME_MAX = 120
FIELD_MAX = 4000


@dataclass(frozen=True)
class ProjectSummary:
    slug: str
    name: str
    objective: str | None
    updated_at: str
    last_checkpoint_at: str | None
    last_checkpoint_age_min: int | None
    open_conflicts: int
    archived: bool

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def _age_minutes(ts: str | None) -> int | None:
    if not ts:
        return None
    delta = datetime.now().astimezone() - datetime.fromisoformat(ts)
    return max(0, int(delta.total_seconds() // 60))


def list_projects(conn: sqlite3.Connection, include_archived: bool = False) -> list[ProjectSummary]:
    sql = """
      SELECT p.slug, p.name, p.objective, p.updated_at, p.archived_at,
             (SELECT max(c.created_at) FROM checkpoints c WHERE c.project_id = p.id) AS last_ckpt,
             (SELECT count(*) FROM conflicts k WHERE k.project_id = p.id AND k.status = 'open') AS open_conflicts
      FROM projects p
    """
    if not include_archived:
        sql += " WHERE p.archived_at IS NULL"
    sql += " ORDER BY p.updated_at DESC"
    return [
        ProjectSummary(
            slug=r["slug"],
            name=r["name"],
            objective=r["objective"],
            updated_at=r["updated_at"],
            last_checkpoint_at=r["last_ckpt"],
            last_checkpoint_age_min=_age_minutes(r["last_ckpt"]),
            open_conflicts=r["open_conflicts"],
            archived=r["archived_at"] is not None,
        )
        for r in conn.execute(sql)
    ]


def all_slugs(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT slug FROM projects")}


def get_project_id(conn: sqlite3.Connection, slug: str) -> int:
    """Resolve a slug, or raise not_found listing close matches (spec §14 error style)."""
    row = conn.execute("SELECT id FROM projects WHERE slug = ?", (slug,)).fetchone()
    if row:
        return int(row["id"])
    known = sorted(all_slugs(conn))
    close = difflib.get_close_matches(slug, known, n=3, cutoff=0.4)
    hint = f"did you mean: {', '.join(close)}" if close else (
        f"known projects: {', '.join(known)}" if known else "no projects yet; call create_project"
    )
    raise CBError("not_found", f"project '{slug}' not found", hint)


def _clean(value: str | None, field: str, limit: int = FIELD_MAX) -> str | None:
    if value is None:
        return None
    v = value.strip()
    if len(v) > limit:
        raise CBError("invalid_input", f"{field} is {len(v)} chars; max is {limit}")
    return v or None


def create_project(
    conn: sqlite3.Connection,
    *,
    name: str,
    objective: str,
    problem: str | None = None,
    target_users: str | None = None,
    scope: str | None = None,
    success_criteria: str | None = None,
    slug: str | None = None,
) -> tuple[str, bool]:
    """Create a project. Returns (slug, created). Idempotent on an exact name match:
    if a project with the same name exists, returns its slug with created=False instead of
    making a near-duplicate (models often retry calls)."""
    clean_name = _clean(name, "name", NAME_MAX)
    clean_obj = _clean(objective, "objective")
    if not clean_name:
        raise CBError("invalid_input", "name is required")
    if not clean_obj:
        raise CBError("invalid_input", "objective is required", "one sentence on what the project is for")

    with write_tx(conn):
        same = conn.execute(
            "SELECT slug FROM projects WHERE lower(name) = lower(?)", (clean_name,)
        ).fetchone()
        if same:
            return str(same["slug"]), False
        existing = all_slugs(conn)
        final: str
        if slug is not None:
            final = slug.strip().lower()
            if not is_valid_slug(final):
                raise CBError("invalid_input", f"slug '{final}' is invalid",
                              "use 2–63 chars: a-z, 0-9 and '-', starting with a letter or digit")
            if final in existing:
                raise CBError("conflict", f"slug '{final}' is already used")
        else:
            try:
                final = make_slug(clean_name, existing)
            except ValueError as exc:
                raise CBError("invalid_input", f"cannot derive a slug from name: {exc}",
                              "pass slug explicitly") from exc
        ts = now_iso()
        conn.execute(
            """INSERT INTO projects(slug, name, objective, problem, target_users, scope,
                                    success_criteria, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (final, clean_name, clean_obj, _clean(problem, "problem"), _clean(target_users, "target_users"),
             _clean(scope, "scope"), _clean(success_criteria, "success_criteria"), ts, ts),
        )
    return final, True
