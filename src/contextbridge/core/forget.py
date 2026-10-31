"""User-controlled deletion (spec §7.1 #12, §9): `forget` (entry / session / project) and `purge`.

- Always a recovery point first: entry/session → a knowledge snapshot; project → a full export
  archive in backups/ (restorable with `ctxb import --full`).
- `secure_delete` is on for these transactions, so freed pages are overwritten, and FTS rows of
  archived text are removed explicitly (they have no trigger).
- A whole project needs the exact project name typed (`confirm_project_name`).
- `purge` is the permanent version: also removes the project's exports, its pre-delete archives,
  and its rows from every DB backup file, then VACUUMs. Nothing of it remains on disk.
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path
from typing import Any, Literal

from .db.connection import write_tx
from .errors import CBError
from .paths import DataPaths, ensure_private_dir
from .snapshots import create_snapshot_tx

TargetType = Literal["entry", "session", "project"]


def _secure(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA secure_delete = ON")


def _drop_archives(conn: sqlite3.Connection, where: str, params: tuple[Any, ...]) -> int:
    ids = [r[0] for r in conn.execute(f"SELECT id FROM raw_archive WHERE {where}", params)]
    for i in ids:
        conn.execute("DELETE FROM archive_fts WHERE rowid = ?", (i,))
    conn.execute(f"DELETE FROM raw_archive WHERE {where}", params)
    return len(ids)


def forget(conn: sqlite3.Connection, paths: DataPaths, project_id: int, target_type: str, target_id: int | None,
           *, confirm: bool, confirm_project_name: str | None = None) -> dict[str, Any]:
    if not confirm:
        raise CBError("confirm_required", "deletion needs confirm=true", "ask the user first; say exactly what goes")
    _secure(conn)
    if target_type == "entry":
        if target_id is None:
            raise CBError("invalid_input", "target_id (the entry id) is required")
        with write_tx(conn):
            row = conn.execute("SELECT id, title FROM entries WHERE id = ? AND project_id = ?",
                               (target_id, project_id)).fetchone()
            if row is None:
                raise CBError("not_found", f"entry {target_id} not found in this project")
            snap, _ = create_snapshot_tx(conn, project_id, "knowledge", label=f"before forgetting entry {target_id}")
            conn.execute("DELETE FROM entries WHERE id = ?", (target_id,))
            conn.execute("UPDATE projects SET knowledge_version = knowledge_version + 1 WHERE id = ?", (project_id,))
        return {"deleted": {"entry": target_id}, "snapshot_id": snap,
                "undo": f"ctxb restore <project> {snap} --yes  (restores the knowledge as it was)"}
    if target_type == "session":
        if target_id is None:
            raise CBError("invalid_input", "target_id (the session id) is required")
        with write_tx(conn):
            s = conn.execute("SELECT id FROM sessions WHERE id = ? AND project_id = ?",
                             (target_id, project_id)).fetchone()
            if s is None:
                raise CBError("not_found", f"session {target_id} not found in this project")
            snap, _ = create_snapshot_tx(conn, project_id, "knowledge", label=f"before forgetting session {target_id}")
            archives = _drop_archives(conn, "project_id = ? AND session_id = ?", (project_id, target_id))
            n_cp = conn.execute("SELECT count(*) FROM checkpoints WHERE session_id = ?", (target_id,)).fetchone()[0]
            conn.execute("DELETE FROM sessions WHERE id = ?", (target_id,))  # checkpoints cascade; entries keep, unlinked
        return {"deleted": {"session": target_id, "checkpoints": n_cp, "archived_transcripts": archives},
                "kept": "knowledge entries saved during the session (now unlinked from it)", "snapshot_id": snap}
    if target_type == "project":
        name = conn.execute("SELECT name, slug FROM projects WHERE id = ?", (project_id,)).fetchone()
        if confirm_project_name is None or confirm_project_name.strip() != name["name"]:
            raise CBError("confirm_required", "deleting a whole project needs its exact name in confirm_project_name",
                          f"the user must type: {name['name']}")
        from .transfer import export_full

        ensure_private_dir(paths.backups)
        ensure_private_dir(paths.backups / "deleted-projects")
        archive = export_full(conn, paths, [name["slug"]], paths.backups / "deleted-projects")
        with write_tx(conn):
            _drop_archives(conn, "project_id = ?", (project_id,))
            conn.execute("DELETE FROM staging WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM telemetry WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))  # everything else cascades
        return {"deleted": {"project": name["slug"]}, "recovery_archive": archive["path"],
                "undo": f"ctxb import --full {archive['path']}",
                "note": "a recovery archive was kept in backups/deleted-projects; `ctxb purge` removes all traces"}
    raise CBError("invalid_input", f"target_type '{target_type}' is invalid", "entry, session or project")


def purge(conn: sqlite3.Connection, paths: DataPaths, slug: str) -> dict[str, Any]:
    """Permanently remove every trace of a project: DB rows (if still there), exports, recovery
    archives, and its rows inside every DB backup file. Irreversible by design."""
    import zipfile

    from .slug import SLUG_RE

    if not SLUG_RE.match(slug):
        raise CBError("invalid_input", f"'{slug}' is not a valid slug")
    _secure(conn)
    out: dict[str, Any] = {"project": slug, "db_rows": False, "exports_removed": False, "recovery_archives": 0,
                           "backups_cleaned": 0}
    row = conn.execute("SELECT id FROM projects WHERE slug = ?", (slug,)).fetchone()
    if row is not None:
        with write_tx(conn):
            _drop_archives(conn, "project_id = ?", (row["id"],))
            conn.execute("DELETE FROM staging WHERE project_id = ?", (row["id"],))
            conn.execute("DELETE FROM telemetry WHERE project_id = ?", (row["id"],))
            conn.execute("DELETE FROM projects WHERE id = ?", (row["id"],))
        out["db_rows"] = True
    conn.execute("VACUUM")
    exp = paths.exports / slug
    if exp.is_dir() and not exp.is_symlink():
        shutil.rmtree(exp)
        out["exports_removed"] = True
    deleted = paths.backups / "deleted-projects"
    if deleted.is_dir():
        for z in deleted.glob("*.zip"):
            try:
                with zipfile.ZipFile(z) as zf:
                    hit = f"projects/{slug}.json" in zf.namelist()
            except zipfile.BadZipFile:
                continue
            if hit:
                z.unlink()
                out["recovery_archives"] += 1
    for b in sorted(paths.backups.glob("*.db")):
        out["backups_cleaned"] += _purge_backup(b, slug)
    return out


def _purge_backup(path: Path, slug: str) -> int:
    bc = sqlite3.connect(path)
    try:
        bc.execute("PRAGMA foreign_keys = ON")
        bc.execute("PRAGMA secure_delete = ON")
        r = bc.execute("SELECT id FROM projects WHERE slug = ?", (slug,)).fetchone()
        if r is None:
            return 0
        pid = r[0]
        ids = [x[0] for x in bc.execute("SELECT id FROM raw_archive WHERE project_id = ?", (pid,))]
        for i in ids:
            bc.execute("DELETE FROM archive_fts WHERE rowid = ?", (i,))
        for t in ("raw_archive", "staging", "telemetry"):
            bc.execute(f"DELETE FROM {t} WHERE project_id = ?", (pid,))
        bc.execute("DELETE FROM projects WHERE id = ?", (pid,))
        bc.commit()
        bc.execute("VACUUM")
        return 1
    finally:
        bc.close()


def purge_entry(conn: sqlite3.Connection, paths: DataPaths, project_id: int, entry_id: int) -> dict[str, Any]:
    """Permanently remove one entry: the row, its history, its conflicts, every knowledge snapshot that
    contains it (rewritten, hash recomputed), and its rows in every DB backup. Irreversible."""
    import hashlib
    import json

    from compression import zstd

    from .snapshots import _canon

    _secure(conn)
    rewritten = 0
    with write_tx(conn):
        conn.execute("DELETE FROM entries WHERE id = ? AND project_id = ?", (entry_id, project_id))
        for s in conn.execute("SELECT id, codec, payload FROM snapshots WHERE project_id = ? AND component = 'knowledge'",
                              (project_id,)).fetchall():
            raw = zstd.decompress(s["payload"]) if s["codec"] == "zstd" else bytes(s["payload"])
            payload = json.loads(raw)
            entries = [e for e in payload.get("entries", []) if e.get("id") != entry_id]
            conflicts = [c for c in payload.get("conflicts", []) if entry_id not in (c.get("entry_a"), c.get("entry_b"))]
            if len(entries) == len(payload.get("entries", [])) and len(conflicts) == len(payload.get("conflicts", [])):
                continue
            payload["entries"], payload["conflicts"] = entries, conflicts
            data = _canon(payload)
            conn.execute("UPDATE snapshots SET payload = ?, codec = 'zstd', content_hash = ? WHERE id = ?",
                         (zstd.compress(data), hashlib.sha256(data).hexdigest(), s["id"]))
            rewritten += 1
    conn.execute("VACUUM")
    cleaned = 0
    for b in sorted(paths.backups.glob("*.db")):
        bc = sqlite3.connect(b)
        try:
            bc.execute("PRAGMA foreign_keys = ON")
            bc.execute("PRAGMA secure_delete = ON")
            n = bc.execute("DELETE FROM entries WHERE id = ? AND project_id = ?", (entry_id, project_id)).rowcount
            bc.commit()
            if n:
                bc.execute("VACUUM")
                cleaned += 1
        finally:
            bc.close()
    return {"purged_entry": entry_id, "snapshots_rewritten": rewritten, "backups_cleaned": cleaned,
            "note": "handoff/export files already written outside the database are not changed"}
