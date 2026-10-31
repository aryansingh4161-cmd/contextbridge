"""Attachments: screenshots, PDFs and documents stored with a project (post-MVP feature, 2026-10-31).

- The file is COPIED into `<data dir>/attachments/<slug>/<sha256>.<ext>` (dir 700, file 600), so the
  memory keeps it even if the original moves or is deleted. Identical files are stored once per project.
- Only common document/image types; size-limited; the path rules of imports apply when the AI picks the
  path (allowed folders, no hidden folders), the CLI accepts any non-hidden path the user types.
- Text-like files (txt, md, csv, json, log, code) are redacted and indexed for search; binary files
  (images, PDFs, Office files) are searchable by name and note. File contents are never logged.
- An AI cannot hand over an image it was SHOWN in a chat; attachments come from files on disk.
"""

from __future__ import annotations

import hashlib
import mimetypes
import shutil
import sqlite3
from pathlib import Path
from typing import Any

from .config import Config
from .db.connection import now_iso, write_tx
from .errors import CBError
from .importer.safety import check_path
from .paths import DataPaths, ensure_private_dir, ensure_private_file
from .security.redact import redact

IMAGE_EXT = {"png", "jpg", "jpeg", "gif", "webp", "heic"}
TEXT_EXT = {"txt", "md", "markdown", "csv", "json", "log", "yaml", "yml", "toml", "py", "js", "ts", "html", "css",
            "sql", "sh"}
DOC_EXT = {"pdf", "docx", "xlsx", "pptx", "rtf", "key", "pages", "numbers"}
ALLOWED_EXT = IMAGE_EXT | TEXT_EXT | DOC_EXT
TEXT_INDEX_MAX = 2_000_000
PROVENANCES = ("user_stated", "ai_suggested", "user_confirmed", "verified", "imported")


def project_dir(paths: DataPaths, slug: str) -> Path:
    return paths.attachments / slug


def stored_path(paths: DataPaths, slug: str, row: dict[str, Any]) -> Path:
    return project_dir(paths, slug) / f"{row['sha256']}.{row['ext']}"


def _check_file(paths: DataPaths, config: Config, path: str, restrict: bool) -> Path:
    max_bytes = int(config.get("attachments.max_bytes", 50_000_000))
    roots = None
    if restrict:
        from .importer.staging import allowed_roots
        roots = allowed_roots(config, paths)
    # the import path rules: absolute, exists, regular file, not hidden, allowed folders when the AI chose it
    p = check_path(path, Path.home(), paths.exports, max_bytes, roots, allowed_ext={f".{e}" for e in ALLOWED_EXT})
    if p.is_dir():
        raise CBError("invalid_input", f"{p} is a folder; attach files one by one")
    return p


def add(conn: sqlite3.Connection, paths: DataPaths, config: Config, project_id: int, path: str, *,
        note: str | None = None, entry_id: int | None = None, provenance: str = "user_stated",
        restrict: bool = True) -> dict[str, Any]:
    if provenance not in PROVENANCES:
        raise CBError("invalid_input", f"provenance '{provenance}' is invalid")
    src = _check_file(paths, config, path, restrict)
    ext = src.suffix.lower().lstrip(".")
    data = src.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    slug = conn.execute("SELECT slug FROM projects WHERE id = ?", (project_id,)).fetchone()["slug"]
    if entry_id is not None and conn.execute("SELECT 1 FROM entries WHERE id = ? AND project_id = ?",
                                             (entry_id, project_id)).fetchone() is None:
        raise CBError("not_found", f"entry {entry_id} not found in this project")
    clean_note = redact(note.strip())[0][:2000] if note and note.strip() else None
    existing = conn.execute("SELECT * FROM attachments WHERE project_id = ? AND sha256 = ?",
                            (project_id, digest)).fetchone()
    if existing is not None:
        return {"attachment_id": existing["id"], "stored": False, "duplicate": True, "filename": existing["filename"],
                "note": "this exact file is already attached to the project"}
    d = project_dir(paths, slug)
    ensure_private_dir(paths.attachments)
    ensure_private_dir(d)
    dest = d / f"{digest}.{ext}"
    tmp = dest.with_name(dest.name + ".partial")
    shutil.copyfile(src, tmp)
    ensure_private_file(tmp)
    tmp.replace(dest)
    text = ""
    redactions = 0
    if ext in TEXT_EXT and len(data) <= TEXT_INDEX_MAX:
        try:
            raw = data.decode("utf-8")
            text, found = redact(raw)
            redactions = sum(f.count for f in found)
        except UnicodeDecodeError:
            text = ""
    mime = mimetypes.guess_type(src.name)[0] or "application/octet-stream"
    filename = redact(src.name)[0][:300]
    try:
        with write_tx(conn):
            cur = conn.execute(
                "INSERT INTO attachments(project_id, entry_id, filename, ext, mime, bytes, sha256, note, source_path,"
                " provenance, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (project_id, entry_id, filename, ext, mime, len(data), digest, clean_note, str(src), provenance,
                 now_iso()))
            aid = int(cur.lastrowid or 0)
            conn.execute("INSERT INTO attachments_fts(rowid, filename, note, text) VALUES (?,?,?,?)",
                         (aid, filename, clean_note or "", text))
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
    out: dict[str, Any] = {"attachment_id": aid, "stored": True, "filename": filename, "bytes": len(data),
                           "mime": mime, "searchable": "full text" if text else "name and note"}
    if redactions:
        out["text_redactions"] = redactions
        out["warning"] = ("the file contains what looks like secrets; they were redacted in the search index, but "
                          "the stored copy is the original file")
    return out


def list_for(conn: sqlite3.Connection, paths: DataPaths, project_id: int) -> list[dict[str, Any]]:
    slug = conn.execute("SELECT slug FROM projects WHERE id = ?", (project_id,)).fetchone()["slug"]
    rows = [dict(r) for r in conn.execute("SELECT * FROM attachments WHERE project_id = ? ORDER BY id", (project_id,))]
    for r in rows:
        r["path"] = str(stored_path(paths, slug, r))
        r["present"] = Path(r["path"]).exists()
        r["tag"] = f"[A-{r['id']:04d} · {r['provenance']} · {str(r['created_at'])[:10]}]"
    return rows


def remove(conn: sqlite3.Connection, paths: DataPaths, project_id: int, attachment_id: int) -> dict[str, Any]:
    conn.execute("PRAGMA secure_delete = ON")
    slug = conn.execute("SELECT slug FROM projects WHERE id = ?", (project_id,)).fetchone()["slug"]
    with write_tx(conn):
        row = conn.execute("SELECT * FROM attachments WHERE id = ? AND project_id = ?",
                           (attachment_id, project_id)).fetchone()
        if row is None:
            raise CBError("not_found", f"attachment {attachment_id} not found in this project")
        conn.execute("DELETE FROM attachments_fts WHERE rowid = ?", (attachment_id,))
        conn.execute("DELETE FROM attachments WHERE id = ?", (attachment_id,))
    stored_path(paths, slug, dict(row)).unlink(missing_ok=True)
    return {"deleted": {"attachment": attachment_id, "filename": row["filename"]}}


def search(conn: sqlite3.Connection, project_id: int, query: str, limit: int = 10) -> list[dict[str, Any]]:
    from .knowledge import _fts_query

    rows = conn.execute(
        """SELECT a.id, a.filename, a.note, snippet(attachments_fts, -1, '[', ']', '…', 12) AS snippet
           FROM attachments_fts JOIN attachments a ON a.id = attachments_fts.rowid
           WHERE attachments_fts MATCH ? AND a.project_id = ? ORDER BY bm25(attachments_fts) LIMIT ?""",
        (_fts_query(query, "or"), project_id, limit)).fetchall()
    return [dict(r) | {"source": "attachment"} for r in rows]
