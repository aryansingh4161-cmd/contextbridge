"""Full export / import for migration to another Mac or data dir (spec §8.4, §11, M5).

`export_full` writes ONE local archive (ZIP is allowed here: local backup/migration only, never a
normal handoff) containing every row of every selected project — knowledge, history, conflicts,
state + history, tasks + history, sessions, checkpoints, snapshots, raw archive, file refs,
handoff runs — plus global memory, with per-member sha256 in `manifest.json`.

`import_full` rebuilds those projects in the current DB:
- entry/task/session ids are PRESERVED when they are free (the normal case: a new, empty DB),
  so tags like `[D-0012 …]` in old handoff files still match;
- when ids collide, every id is remapped and every structured reference is rewritten
  (FK columns, Live State `refs`, task `depends_on`, history snapshots, snapshot payloads,
  whose hashes are recomputed); the id map is returned so old tags can be translated;
- a project whose slug already exists is refused unless `as_slug` is given (never merged);
- a pre-import backup of the whole DB is taken first; each project imports in one transaction;
- text fields are re-run through redaction (idempotent on already-redacted data).

`package_to_dump` turns a handoff package's `source_references.json` into the same structure,
so a package alone can rebuild a project (entries with ids and provenance, state, tasks,
sessions, checkpoints; no history or snapshots, which packages don't carry).
"""

from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import zipfile
from datetime import datetime
from importlib.metadata import version as pkg_version
from pathlib import Path
from typing import Any

from compression import zstd

from .db.connection import now_iso, write_tx
from .db.migrate import applied_versions, backup_db
from .errors import CBError
from .paths import DataPaths, ensure_private_dir, ensure_private_file
from .security.redact import redact

FORMAT = 1
KIND = "contextbridge_full_export"
MAX_ARCHIVE_BYTES = 2_000_000_000

# Insert order respects foreign keys. Each table: {fk column: referenced table}.
TABLES: dict[str, dict[str, str]] = {
    "projects": {},
    "sessions": {"project_id": "projects"},
    "entries": {"project_id": "projects", "session_id": "sessions", "supersedes_id": "entries",
                "superseded_by_id": "entries"},
    "entry_history": {"entry_id": "entries", "session_id": "sessions"},
    "conflicts": {"project_id": "projects", "entry_a": "entries", "entry_b": "entries"},
    "live_state": {"project_id": "projects"},
    "live_state_history": {"live_state_id": "live_state", "session_id": "sessions"},
    "tasks": {"project_id": "projects"},
    "task_history": {"task_id": "tasks", "session_id": "sessions"},
    "checkpoints": {"session_id": "sessions", "project_id": "projects"},
    "snapshots": {"project_id": "projects"},
    "raw_archive": {"project_id": "projects", "session_id": "sessions"},
    "file_refs": {"project_id": "projects"},  # display-only paths; never executed or read
    "attachments": {"project_id": "projects", "session_id": "sessions", "entry_id": "entries"},
    "handoff_runs": {"project_id": "projects"},
}
# How each child table is selected for one project.
_SELECT: dict[str, str] = {
    "projects": "SELECT * FROM projects WHERE id = :p",
    "entry_history": "SELECT h.* FROM entry_history h JOIN entries e ON e.id = h.entry_id WHERE e.project_id = :p",
    "live_state_history": "SELECT h.* FROM live_state_history h JOIN live_state s ON s.id = h.live_state_id"
                          " WHERE s.project_id = :p",
    "task_history": "SELECT h.* FROM task_history h JOIN tasks t ON t.id = h.task_id WHERE t.project_id = :p",
}
REDACT_COLUMNS: dict[str, tuple[str, ...]] = {
    "projects": ("objective", "problem", "target_users", "scope", "success_criteria"),
    "entries": ("title", "body", "rationale", "alternatives", "source_ref"),
    "live_state": ("content",), "live_state_history": ("content",),
    "tasks": ("title", "expected_outcome", "notes"),
    "sessions": ("summary", "where_stopped", "next_action"),
    "checkpoints": ("summary", "where_stopped", "next_action"),
}
NULLABLE_FKS = {("entries", "session_id"), ("entries", "supersedes_id"), ("entries", "superseded_by_id"),
                ("entry_history", "session_id"), ("live_state_history", "session_id"),
                ("task_history", "session_id"), ("raw_archive", "session_id"), ("attachments", "session_id"),
                ("attachments", "entry_id")}
ENTRY_REFS = ("id", "project_id", "session_id", "supersedes_id", "superseded_by_id")


# ---------------------------------------------------------------- encoding

def _enc(v: Any) -> Any:
    return {"$b64": base64.b64encode(v).decode("ascii")} if isinstance(v, bytes) else v


def _dec(v: Any) -> Any:
    return base64.b64decode(v["$b64"]) if isinstance(v, dict) and "$b64" in v else v


def _schema_version(conn: sqlite3.Connection) -> int:
    return max(applied_versions(conn), default=0)


# ---------------------------------------------------------------- export

def dump_project(conn: sqlite3.Connection, project_id: int) -> dict[str, Any]:
    tables: dict[str, list[dict[str, Any]]] = {}
    for t in TABLES:
        sql = _SELECT.get(t, f"SELECT * FROM {t} WHERE project_id = :p")
        tables[t] = [{k: _enc(v) for k, v in dict(r).items()} for r in conn.execute(sql + " ORDER BY 1", {"p": project_id})]
    return {"format": FORMAT, "kind": "contextbridge_project_dump", "schema_version": _schema_version(conn),
            "tables": tables}


def export_full(conn: sqlite3.Connection, paths: DataPaths, slugs: list[str] | None = None,
                out: str | Path | None = None) -> dict[str, Any]:
    conn.execute("BEGIN")  # one consistent read across all projects
    try:
        rows = conn.execute("SELECT id, slug, name FROM projects ORDER BY slug").fetchall()
        if slugs:
            known = {r["slug"] for r in rows}
            missing = [s for s in slugs if s not in known]
            if missing:
                raise CBError("not_found", f"unknown project(s): {', '.join(missing)}",
                              f"known: {', '.join(sorted(known)) or 'none'}")
            rows = [r for r in rows if r["slug"] in slugs]
        members: dict[str, bytes] = {}
        listing = []
        for r in rows:
            data = json.dumps(dump_project(conn, r["id"]), ensure_ascii=False, sort_keys=True).encode("utf-8")
            name = f"projects/{r['slug']}.json"
            members[name] = data
            counts = {t: n for t, n in ((t, len(v)) for t, v in json.loads(data)["tables"].items()) if n}
            files = []
            for a in conn.execute("SELECT sha256, ext FROM attachments WHERE project_id = ?", (r["id"],)):
                fp = paths.attachments / r["slug"] / f"{a['sha256']}.{a['ext']}"
                if fp.exists():
                    member = f"attachments/{r['slug']}/{a['sha256']}.{a['ext']}"
                    members[member] = fp.read_bytes()
                    files.append(member)
            listing.append({"slug": r["slug"], "name": r["name"], "file": name, "attachment_files": files,
                            "sha256": hashlib.sha256(data).hexdigest(), "counts": counts})
        gm = [dict(r) for r in conn.execute("SELECT * FROM global_memory ORDER BY id")]
        members["global_memory.json"] = json.dumps(gm, ensure_ascii=False, sort_keys=True).encode("utf-8")
        schema = _schema_version(conn)
    finally:
        conn.execute("COMMIT")
    manifest = {"format": FORMAT, "kind": KIND, "schema_version": schema, "exported_at": now_iso(),
                "app_version": pkg_version("contextbridge"), "projects": listing,
                "global_memory": {"file": "global_memory.json",
                                  "sha256": hashlib.sha256(members["global_memory.json"]).hexdigest(),
                                  "count": len(gm)},
                "note": "Local migration archive. Contains private project memory: keep it private."}
    if out is None:
        ensure_private_dir(paths.exports)
        stamp = datetime.now().astimezone().strftime("%Y-%m-%d_%H%M%S")
        target = paths.exports / f"contextbridge_full_{stamp}.zip"
    else:
        target = Path(out).expanduser()
        if target.is_dir():
            target = target / f"contextbridge_full_{datetime.now().astimezone().strftime('%Y-%m-%d_%H%M%S')}.zip"
    if target.exists():
        raise CBError("conflict", f"{target} already exists", "choose another path; exports never overwrite")
    tmp = target.with_name(target.name + ".partial")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        for name, data in members.items():
            z.writestr(name, data)
    tmp.replace(target)
    ensure_private_file(target)
    return {"path": str(target), "bytes": target.stat().st_size, "projects": [p["slug"] for p in listing],
            "counts": {p["slug"]: p["counts"] for p in listing}}


# ---------------------------------------------------------------- reading sources

def read_archive(path: str | Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Return (manifest, {slug: dump}, global_memory) after verifying every member's sha256."""
    p = Path(path).expanduser()
    if not p.is_file():
        raise CBError("not_found", f"{p} is not a file")
    if p.stat().st_size > MAX_ARCHIVE_BYTES:
        raise CBError("too_large", f"{p} is larger than {MAX_ARCHIVE_BYTES} bytes")
    try:
        z = zipfile.ZipFile(p)
    except zipfile.BadZipFile as exc:
        raise CBError("invalid_input", f"{p.name} is not a ContextBridge export (not a ZIP)") from exc
    with z:
        try:
            manifest = json.loads(z.read("manifest.json"))
        except KeyError as exc:
            raise CBError("invalid_input", f"{p.name} has no manifest.json") from exc
        if manifest.get("kind") != KIND or manifest.get("format") != FORMAT:
            raise CBError("invalid_input", "not a ContextBridge full export (format 1)")
        dumps = {}
        for item in manifest["projects"]:
            data = z.read(item["file"])
            if hashlib.sha256(data).hexdigest() != item["sha256"]:
                raise CBError("invalid_input", f"{item['file']} is corrupt (sha256 mismatch); nothing imported")
            dumps[item["slug"]] = json.loads(data)
            blobs: dict[str, bytes] = {}
            for member in item.get("attachment_files", []):
                b = z.read(member)
                sha, _, _ = Path(member).name.partition(".")
                if hashlib.sha256(b).hexdigest() != sha:
                    raise CBError("invalid_input", f"{member} is corrupt (sha256 mismatch); nothing imported")
                blobs[sha] = b
            dumps[item["slug"]]["_files"] = blobs
        gm_data = z.read(manifest["global_memory"]["file"])
        if hashlib.sha256(gm_data).hexdigest() != manifest["global_memory"]["sha256"]:
            raise CBError("invalid_input", "global_memory.json is corrupt (sha256 mismatch); nothing imported")
    return manifest, dumps, json.loads(gm_data)


def package_to_dump(package_dir: str | Path) -> dict[str, Any]:
    """A handoff package's sidecar → a project dump (no history/snapshots: packages don't carry them)."""
    d = Path(package_dir).expanduser()
    side_path = d / "source_references.json" if d.is_dir() else d
    try:
        side = json.loads(side_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CBError("invalid_input", f"cannot read {side_path}: {type(exc).__name__}") from exc
    if side.get("kind") != "source_references" or side.get("contextbridge_format") != 1:
        raise CBError("invalid_input", f"{side_path.name} is not a ContextBridge sidecar (format 1)")
    project = dict(side["project"])
    pid = project.get("id") or next((e["project_id"] for e in side["entries"]), 1)
    project["id"] = pid
    entries = []
    for e in side["entries"]:
        e = dict(e)
        if isinstance(e.get("alternatives"), list):
            e["alternatives"] = json.dumps(e["alternatives"]) if e["alternatives"] else None
        entries.append(e)
    live = [dict(r) | {"id": i, "project_id": pid} for i, r in enumerate(side.get("live_state", []), 1)]
    return {"format": FORMAT, "kind": "contextbridge_project_dump", "schema_version": 1, "tables": {
        "projects": [project], "sessions": side.get("sessions", []), "entries": entries,
        "conflicts": side.get("conflicts", []), "live_state": live, "tasks": side.get("tasks", []),
        "checkpoints": side.get("checkpoints", [])}}


# ---------------------------------------------------------------- import

def _columns(conn: sqlite3.Connection, table: str) -> dict[str, bool]:
    """column -> NOT NULL without default (required)."""
    return {r["name"]: bool(r["notnull"]) and r["dflt_value"] is None and not r["pk"]
            for r in conn.execute(f"PRAGMA table_info({table})")}


def _plan_ids(conn: sqlite3.Connection, tables: dict[str, list[dict[str, Any]]]) -> dict[str, dict[int, int]]:
    """Old id -> new id per table: identity if none of the ids is taken, else fresh ids after max(id)."""
    maps: dict[str, dict[int, int]] = {}
    for t in TABLES:
        ids = [int(r["id"]) for r in tables.get(t, []) if r.get("id") is not None]
        if not ids:
            maps[t] = {}
            continue
        taken = False
        for i in range(0, len(ids), 900):  # stay under SQLite's bound-parameter limit
            chunk = ids[i:i + 900]
            if conn.execute(f"SELECT 1 FROM {t} WHERE id IN ({','.join('?' * len(chunk))}) LIMIT 1", chunk).fetchone():
                taken = True
                break
        if not taken:
            maps[t] = {i: i for i in ids}
        else:
            start = int(conn.execute(f"SELECT coalesce(max(id), 0) FROM {t}").fetchone()[0]) + 1
            maps[t] = {old: start + n for n, old in enumerate(sorted(ids))}
    return maps


def _remap_list(text: Any, m: dict[int, int]) -> Any:
    if not text:
        return text
    try:
        ids = json.loads(text) if isinstance(text, str) else text
    except ValueError:
        return text
    return json.dumps([m[i] for i in ids if i in m]) if isinstance(ids, list) else text


def _remap_entry_row(row: dict[str, Any], maps: dict[str, dict[int, int]]) -> dict[str, Any]:
    out = dict(row)
    for col, table in (("id", "entries"), ("project_id", "projects"), ("session_id", "sessions"),
                       ("supersedes_id", "entries"), ("superseded_by_id", "entries")):
        if out.get(col) is not None:
            out[col] = maps[table].get(int(out[col]))
    return out


def _remap_task_row(row: dict[str, Any], maps: dict[str, dict[int, int]]) -> dict[str, Any]:
    out = dict(row)
    if out.get("id") is not None:
        out["id"] = maps["tasks"].get(int(out["id"]))
    if out.get("project_id") is not None:
        out["project_id"] = maps["projects"].get(int(out["project_id"]))
    if "depends_on" in out:
        out["depends_on"] = _remap_list(out["depends_on"], maps["tasks"])
    return out


def _remap_snapshot(row: dict[str, Any], maps: dict[str, dict[int, int]], slug: str) -> dict[str, Any]:
    from .snapshots import _canon

    raw = row["payload"]
    codec = row.get("codec", "zstd")
    if codec == "zstd":
        payload = json.loads(zstd.decompress(raw))
    elif codec == "none":
        payload = json.loads(raw)
    else:
        import gzip
        payload = json.loads(gzip.decompress(raw))
    payload["project"] = slug
    if "entries" in payload:
        payload["entries"] = [_remap_entry_row(e, maps) for e in payload["entries"]]
    if "conflicts" in payload:
        payload["conflicts"] = [dict(k) | {
            "id": maps["conflicts"].get(int(k["id"]), k["id"]), "project_id": maps["projects"].get(k["project_id"]),
            "entry_a": maps["entries"].get(int(k["entry_a"])), "entry_b": maps["entries"].get(int(k["entry_b"]))}
            for k in payload["conflicts"]]
    if "sections" in payload:
        payload["sections"] = [dict(s) | {"refs": _remap_list(s.get("refs"), maps["entries"])}
                               for s in payload["sections"]]
    if "tasks" in payload:
        payload["tasks"] = [_remap_task_row(t, maps) for t in payload["tasks"]]
    data = _canon(payload)
    return dict(row) | {"payload": zstd.compress(data), "codec": "zstd", "content_hash": hashlib.sha256(data).hexdigest()}


def _fix_row(table: str, row: dict[str, Any], maps: dict[str, dict[int, int]], slug: str,
             redactions: list[int]) -> dict[str, Any]:
    out = dict(row)
    if out.get("id") is not None and table in maps:
        out["id"] = maps[table][int(out["id"])]
    for col, ref in TABLES[table].items():
        if out.get(col) is None:
            continue
        new = maps[ref].get(int(out[col]))
        if new is None and (table, col) not in NULLABLE_FKS:
            raise CBError("invalid_input", f"{table}.{col} references missing {ref} {out[col]}; nothing imported")
        out[col] = new
    if table == "projects":
        out["slug"] = slug
        # Never trust a directory path from an archive: handoffs run git there. The user re-links.
        out["linked_dir"] = None
    if table in ("live_state", "live_state_history"):
        out["refs"] = _remap_list(out.get("refs"), maps["entries"])
    if table == "tasks":
        out["depends_on"] = _remap_list(out.get("depends_on"), maps["tasks"])
    if table == "entry_history" and out.get("snapshot_json"):
        out["snapshot_json"] = json.dumps(_remap_entry_row(json.loads(out["snapshot_json"]), maps), ensure_ascii=False)
    if table == "task_history" and out.get("snapshot_json"):
        out["snapshot_json"] = json.dumps(_remap_task_row(json.loads(out["snapshot_json"]), maps), ensure_ascii=False)
    if table == "snapshots":
        out = _remap_snapshot(out, maps, slug)
    if table == "attachments":
        from .attachments import valid_row
        if not valid_row(out):
            raise CBError("invalid_input", "an attachment record has an invalid sha256/extension; nothing imported")
    for col in REDACT_COLUMNS.get(table, ()):
        if isinstance(out.get(col), str):
            cleaned, found = redact(out[col])
            if found:
                redactions[0] += sum(f.count for f in found)
                out[col] = cleaned
    return out


def _archive_text(row: dict[str, Any]) -> str:
    raw = row["content"]
    if row.get("codec") == "zstd":
        raw = zstd.decompress(raw)
    elif row.get("codec") == "gzip":
        import gzip
        raw = gzip.decompress(raw)
    return bytes(raw).decode("utf-8", errors="replace")


def import_dump(conn: sqlite3.Connection, dump: dict[str, Any], *, as_slug: str | None = None,
                schema_version: int | None = None, paths: DataPaths | None = None) -> dict[str, Any]:
    """Import one project dump in ONE transaction. Caller has taken the pre-import backup."""
    from .slug import SLUG_RE

    if dump.get("kind") != "contextbridge_project_dump":
        raise CBError("invalid_input", "not a ContextBridge project dump")
    sv = schema_version if schema_version is not None else int(dump.get("schema_version", 1))
    if sv > _schema_version(conn):
        raise CBError("invalid_input", f"export uses schema v{sv}, this install has v{_schema_version(conn)}",
                      "upgrade ContextBridge on this machine, then import again")
    tables = {t: [{k: _dec(v) for k, v in r.items()} for r in rows] for t, rows in dump["tables"].items()}
    if len(tables.get("projects", [])) != 1:
        raise CBError("invalid_input", "a project dump must contain exactly one project")
    slug = as_slug or tables["projects"][0]["slug"]
    if not SLUG_RE.match(slug):
        raise CBError("invalid_input", f"'{slug}' is not a valid slug")
    if conn.execute("SELECT 1 FROM projects WHERE slug = ?", (slug,)).fetchone():
        raise CBError("conflict", f"project '{slug}' already exists here; nothing imported",
                      "import under another slug with --as NEW-SLUG (projects are never merged silently)")
    counts: dict[str, int] = {}
    redactions = [0]
    with write_tx(conn):
        conn.execute("PRAGMA defer_foreign_keys = ON")
        maps = _plan_ids(conn, tables)
        for t in TABLES:
            rows = tables.get(t, [])
            if not rows:
                continue
            cols = _columns(conn, t)
            for r in rows:
                fixed = _fix_row(t, r, maps, slug, redactions)
                use = {k: v for k, v in fixed.items() if k in cols}
                missing = [c for c, req in cols.items() if req and use.get(c) is None]
                if missing:
                    raise CBError("invalid_input", f"{t} row is missing {', '.join(missing)}; nothing imported")
                conn.execute(f"INSERT INTO {t}({', '.join(use)}) VALUES ({', '.join('?' * len(use))})",
                             list(use.values()))
                if t == "raw_archive":
                    conn.execute("INSERT INTO archive_fts(rowid, title, text) VALUES (?,?,?)",
                                 (use["id"], use.get("title") or "", _archive_text(use)))
                if t == "attachments":
                    _restore_attachment(paths, slug, use, dump.get("_files", {}), conn)
            counts[t] = len(rows)
        bad = conn.execute("PRAGMA foreign_key_check").fetchall()
        if bad:
            raise CBError("internal", f"{len(bad)} broken reference(s) after import; rolled back")
    remapped = {t: {o: n for o, n in m.items() if o != n} for t, m in maps.items()}
    return {"slug": slug, "counts": counts, "ids_preserved": not any(remapped.values()),
            "id_map": {t: m for t, m in remapped.items() if m}, "redactions": redactions[0]}


def _restore_attachment(paths: DataPaths | None, slug: str, row: dict[str, Any], blobs: dict[str, bytes],
                        conn: sqlite3.Connection) -> None:
    """Write an imported attachment's file into the memory folder and re-index its text."""
    from .attachments import TEXT_EXT, TEXT_INDEX_MAX

    data = blobs.get(row["sha256"])
    text = ""
    if paths is not None and data is not None:
        d = paths.attachments / slug
        ensure_private_dir(paths.attachments)
        ensure_private_dir(d)
        dest = d / f"{row['sha256']}.{row['ext']}"
        dest.write_bytes(data)
        ensure_private_file(dest)
        if row["ext"] in TEXT_EXT and len(data) <= TEXT_INDEX_MAX:
            try:
                text = redact(data.decode("utf-8"))[0]
            except UnicodeDecodeError:
                text = ""
    conn.execute("INSERT INTO attachments_fts(rowid, filename, note, text) VALUES (?,?,?,?)",
                 (row["id"], row.get("filename") or "", row.get("note") or "", text))


def import_full(conn: sqlite3.Connection, paths: DataPaths, source: str | Path, *,
                only: list[str] | None = None, as_slug: str | None = None,
                dry_run: bool = False) -> dict[str, Any]:
    src = Path(source).expanduser()
    if src.is_dir() or src.name == "source_references.json":
        dumps = {"(package)": package_to_dump(src)}
        manifest: dict[str, Any] = {"schema_version": 1}
        gm: list[dict[str, Any]] = []
    else:
        manifest, dumps, gm = read_archive(src)
    if only:
        unknown = [s for s in only if s not in dumps]
        if unknown:
            raise CBError("not_found", f"not in this export: {', '.join(unknown)}", f"contains: {', '.join(dumps)}")
        dumps = {s: d for s, d in dumps.items() if s in only}
    if as_slug and len(dumps) != 1:
        raise CBError("invalid_input", "--as needs exactly one project (use --project to pick one)")
    plan = [{"slug": as_slug or d["tables"]["projects"][0]["slug"],
             "rows": sum(len(v) for v in d["tables"].values())} for d in dumps.values()]
    if dry_run:
        return {"dry_run": True, "would_import": plan, "global_memory": len(gm)}
    backup = backup_db(conn, paths, "pre-import")
    results = []
    for d in dumps.values():
        results.append(import_dump(conn, d, as_slug=as_slug, schema_version=int(manifest.get("schema_version", 1)),
                                   paths=paths))
    gm_added = _import_global(conn, gm)
    return {"imported": results, "global_memory_added": gm_added, "backup": str(backup)}


def _import_global(conn: sqlite3.Connection, gm: list[dict[str, Any]]) -> int:
    added = 0
    with write_tx(conn):
        for g in gm:
            if conn.execute("SELECT 1 FROM global_memory WHERE kind = ? AND topic_key = ? AND title = ?",
                            (g["kind"], g["topic_key"], g["title"])).fetchone():
                continue
            conn.execute("INSERT INTO global_memory(kind, topic_key, title, body, provenance, created_at, updated_at)"
                         " VALUES (?,?,?,?,?,?,?)", (g["kind"], g["topic_key"], redact(g["title"])[0],
                                                     redact(g.get("body") or "")[0], g["provenance"],
                                                     g["created_at"], g["updated_at"]))
            added += 1
    return added

