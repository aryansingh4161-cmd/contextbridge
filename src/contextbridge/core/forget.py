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

TargetType = Literal["entry", "session", "project", "attachment"]


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
    if target_type == "attachment":
        if target_id is None:
            raise CBError("invalid_input", "target_id (the attachment id) is required")
        from .attachments import remove
        return remove(conn, paths, project_id, target_id) | {
            "note": "the file copy was deleted from the memory folder (your original file is untouched)"}
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
            _drop_attachment_index(conn, project_id)
            conn.execute("DELETE FROM staging WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM telemetry WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))  # everything else cascades
        _remove_tree(paths.attachments / name["slug"])
        return {"deleted": {"project": name["slug"]}, "recovery_archive": archive["path"],
                "undo": f"ctxb import --full {archive['path']}",
                "note": "a recovery archive was kept in backups/deleted-projects; `ctxb purge` removes all traces"}
    raise CBError("invalid_input", f"target_type '{target_type}' is invalid", "entry, session, project or attachment")


def _drop_attachment_index(conn: sqlite3.Connection, project_id: int) -> None:
    for (aid,) in conn.execute("SELECT id FROM attachments WHERE project_id = ?", (project_id,)).fetchall():
        conn.execute("DELETE FROM attachments_fts WHERE rowid = ?", (aid,))


def _remove_tree(d: Path) -> bool:
    if d.is_dir() and not d.is_symlink():
        shutil.rmtree(d)
        return True
    return False


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
            _drop_attachment_index(conn, row["id"])
            conn.execute("DELETE FROM staging WHERE project_id = ?", (row["id"],))
            conn.execute("DELETE FROM telemetry WHERE project_id = ?", (row["id"],))
            conn.execute("DELETE FROM projects WHERE id = ?", (row["id"],))
        out["db_rows"] = True
    conn.execute("VACUUM")
    out["exports_removed"] = _remove_tree(paths.exports / slug)
    out["attachments_removed"] = _remove_tree(paths.attachments / slug)
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
        has_att = bc.execute("SELECT 1 FROM sqlite_master WHERE name = 'attachments'").fetchone()
        if has_att:  # older backups predate the attachments table
            for (aid,) in bc.execute("SELECT id FROM attachments WHERE project_id = ?", (pid,)).fetchall():
                bc.execute("DELETE FROM attachments_fts WHERE rowid = ?", (aid,))
        for t in ("raw_archive", "staging", "telemetry"):
            bc.execute(f"DELETE FROM {t} WHERE project_id = ?", (pid,))
        bc.execute("DELETE FROM projects WHERE id = ?", (pid,))
        bc.commit()
        bc.execute("VACUUM")
        return 1
    finally:
        bc.close()


def _scrub_payload(raw: bytes, codec: str, entry_id: int) -> tuple[bytes, str] | None:
    """Remove one entry (and conflicts naming it) from a knowledge snapshot payload; None if absent."""
    import hashlib
    import json

    from compression import zstd

    from .snapshots import _canon

    payload = json.loads(zstd.decompress(raw) if codec == "zstd" else raw)
    entries = [e for e in payload.get("entries", []) if e.get("id") != entry_id]
    conflicts = [c for c in payload.get("conflicts", []) if entry_id not in (c.get("entry_a"), c.get("entry_b"))]
    if len(entries) == len(payload.get("entries", [])) and len(conflicts) == len(payload.get("conflicts", [])):
        return None
    payload["entries"], payload["conflicts"] = entries, conflicts
    data = _canon(payload)
    return zstd.compress(data), hashlib.sha256(data).hexdigest()


def _scrub_db(conn: sqlite3.Connection, project_id: int, entry_id: int) -> int:
    """Delete the entry row (history/conflicts cascade) and rewrite every knowledge snapshot containing it."""
    conn.execute("DELETE FROM entries WHERE id = ? AND project_id = ?", (entry_id, project_id))
    rewritten = 0
    for sid, codec, payload in conn.execute(
            "SELECT id, codec, payload FROM snapshots WHERE project_id = ? AND component = 'knowledge'",
            (project_id,)).fetchall():
        new = _scrub_payload(bytes(payload), codec, entry_id)
        if new is not None:
            conn.execute("UPDATE snapshots SET payload = ?, codec = 'zstd', content_hash = ? WHERE id = ?",
                         (new[0], new[1], sid))
            rewritten += 1
    return rewritten


def _scrub_archive(z: Path, slug: str, entry_id: int) -> bool:
    """Rewrite a full-export ZIP so the project's dump no longer contains the entry; True if changed."""
    import base64
    import hashlib
    import json
    import zipfile

    member = f"projects/{slug}.json"
    with zipfile.ZipFile(z) as zf:
        if member not in zf.namelist():
            return False
        contents = {n: zf.read(n) for n in zf.namelist()}
    dump = json.loads(contents[member])
    t = dump["tables"]
    before = json.dumps(t, sort_keys=True)
    t["entries"] = [e for e in t.get("entries", []) if e.get("id") != entry_id]
    t["entry_history"] = [h for h in t.get("entry_history", []) if h.get("entry_id") != entry_id]
    t["conflicts"] = [c for c in t.get("conflicts", []) if entry_id not in (c.get("entry_a"), c.get("entry_b"))]
    for snap in t.get("snapshots", []):
        if snap.get("component") != "knowledge" or not isinstance(snap.get("payload"), dict):
            continue
        new = _scrub_payload(base64.b64decode(snap["payload"]["$b64"]), snap.get("codec", "zstd"), entry_id)
        if new is not None:
            snap["payload"] = {"$b64": base64.b64encode(new[0]).decode("ascii")}
            snap["codec"], snap["content_hash"] = "zstd", new[1]
    if json.dumps(t, sort_keys=True) == before:
        return False
    data = json.dumps(dump, ensure_ascii=False, sort_keys=True).encode("utf-8")
    manifest = json.loads(contents["manifest.json"])
    for item in manifest.get("projects", []):
        if item.get("file") == member:
            item["sha256"] = hashlib.sha256(data).hexdigest()
    contents[member], contents["manifest.json"] = data, json.dumps(manifest, ensure_ascii=False, indent=2).encode()
    tmp = z.with_name(z.name + ".partial")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as out:
        for n, b in contents.items():
            out.writestr(n, b)
    tmp.replace(z)
    return True


def purge_entry(conn: sqlite3.Connection, paths: DataPaths, project_id: int, entry_id: int) -> dict[str, Any]:
    """Permanently remove one entry from the live DB (row, history, conflicts, every knowledge snapshot),
    from every DB backup (matched by project SLUG, including the snapshots inside them), and from
    every recovery archive of the project. Irreversible."""
    _secure(conn)
    slug = conn.execute("SELECT slug FROM projects WHERE id = ?", (project_id,)).fetchone()["slug"]
    with write_tx(conn):
        rewritten = _scrub_db(conn, project_id, entry_id)
    conn.execute("VACUUM")
    cleaned = 0
    for b in sorted(paths.backups.rglob("*.db")):
        bc = sqlite3.connect(b)
        try:
            bc.execute("PRAGMA foreign_keys = ON")
            bc.execute("PRAGMA secure_delete = ON")
            r = bc.execute("SELECT id FROM projects WHERE slug = ?", (slug,)).fetchone()
            if r is None:
                continue
            had = bc.execute("SELECT count(*) FROM entries WHERE id = ? AND project_id = ?", (entry_id, r[0])).fetchone()[0]
            n = _scrub_db(bc, r[0], entry_id)
            bc.commit()
            if had or n:
                bc.execute("VACUUM")
                cleaned += 1
        finally:
            bc.close()
    archives = 0
    deleted = paths.backups / "deleted-projects"
    for z in sorted(deleted.glob("*.zip")) if deleted.is_dir() else []:
        archives += _scrub_archive(z, slug, entry_id)
    return {"purged_entry": entry_id, "snapshots_rewritten": rewritten, "backups_cleaned": cleaned,
            "recovery_archives_rewritten": archives,
            "note": "handoff/export files already written to exports/ are not changed; delete them if needed"}
