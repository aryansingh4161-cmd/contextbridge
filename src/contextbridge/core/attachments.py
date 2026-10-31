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
import re
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
# no ".key": it is Keynote's extension but also the usual name for private keys
DOC_EXT = {"pdf", "docx", "xlsx", "pptx", "rtf", "pages", "numbers"}
ALLOWED_EXT = IMAGE_EXT | TEXT_EXT | DOC_EXT
TEXT_INDEX_MAX = 2_000_000
PROVENANCES = ("user_stated", "ai_suggested", "user_confirmed", "verified", "imported")


SHA_RE = re.compile(r"^[0-9a-f]{64}$")
PEM_PRIVATE = re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----")


def project_dir(paths: DataPaths, slug: str) -> Path:
    return paths.attachments / slug


def valid_row(row: dict[str, Any]) -> bool:
    return bool(SHA_RE.match(str(row.get("sha256", "")))) and str(row.get("ext", "")) in ALLOWED_EXT


def stored_path(paths: DataPaths, slug: str, row: dict[str, Any]) -> Path:
    """Where an attachment's copy lives. Rows can come from imported archives, so the name parts are
    validated and the result must stay inside the project's attachment folder."""
    if not valid_row(row):
        raise CBError("invalid_input", "attachment record has an invalid file name; refusing to touch the disk")
    base = project_dir(paths, slug).resolve()
    p = (base / f"{row['sha256']}.{row['ext']}").resolve()
    if not p.is_relative_to(base):
        raise CBError("invalid_input", "attachment path escapes the attachments folder")
    return p


ZIP_EXT = {"docx", "xlsx", "pptx", "pages", "numbers"}
INSPECT_MAX = 50_000_000      # inflated bytes we are willing to scan in one container
_PDF_STREAM = re.compile(rb"stream\r?\n(.*?)\r?\nendstream", re.S)


def _scan_text(text: str, *, entropy: bool) -> bool:
    return bool(redact(text, entropy=entropy)[1])


def _inner_texts(data: bytes, ext: str) -> list[bytes] | None:
    """Text hidden inside containers: ZIP members (Office/iWork) and Flate streams (PDF).
    Returns None when a container can't be inspected (corrupt, too large)."""
    import io
    import zipfile
    import zlib

    if ext in ZIP_EXT or data[:4] == b"PK\x03\x04":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                infos = z.infolist()
                if sum(i.file_size for i in infos) > INSPECT_MAX or len(infos) > 5000:
                    return None
                return [z.read(i) for i in infos if not i.is_dir()]
        except (zipfile.BadZipFile, OSError, RuntimeError, NotImplementedError):
            return None
    if ext == "pdf" or data[:5] == b"%PDF-":
        out, total = [], 0
        for m in _PDF_STREAM.finditer(data):
            raw = m.group(1)
            try:
                inflated = zlib.decompressobj().decompress(raw, INSPECT_MAX - total)
            except zlib.error:
                inflated = raw  # not Flate-compressed: scan as is
            total += len(inflated)
            out.append(inflated)
            if total >= INSPECT_MAX:
                return None
        return out
    return []


def has_secrets(data: bytes, ext: str, *, fail_closed: bool = False) -> bool:
    """Scan by CONTENT, not extension: private keys anywhere; provider/assignment patterns in every file
    (decoded tolerantly, so one bad byte can't skip the scan); the entropy heuristic only for text types
    (binary bytes look random); text inside ZIP containers and PDF streams. A container that can't be
    inspected counts as risky when `fail_closed` (AI-chosen paths, upload packages)."""
    if PEM_PRIVATE.search(data):
        return True
    text_like = ext in TEXT_EXT or ext == "rtf"
    if _scan_text(data.decode("utf-8", errors="replace"), entropy=text_like):
        return True
    inner = _inner_texts(data, ext)
    if inner is None:
        return fail_closed
    for chunk in inner:
        if PEM_PRIVATE.search(chunk) or _scan_text(chunk.decode("utf-8", errors="replace"), entropy=False):
            return True
    return False


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
    if PEM_PRIVATE.search(data):
        raise CBError("invalid_input", f"{src.name} contains a private key; it can't be attached",
                      "keys don't belong in project memory")
    if restrict and has_secrets(data, ext, fail_closed=True):
        raise CBError("invalid_input", f"{src.name} appears to contain secrets (API keys, tokens, passwords); "
                      "the AI can't attach it", "remove the secrets, or attach it yourself with `ctxb attach` "
                      "(the stored copy would keep them)")
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
