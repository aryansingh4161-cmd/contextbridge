"""Migration framework: backup → migrate in a transaction → verify → prune backups (spec §9).

Migrations are `NNNN_name.sql` files in this package. Each applied migration's sha256 is
recorded; if a shipped file later changes, `verify_checksums` reports it (doctor).
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from ..errors import CBError
from ..logging_setup import get_logger
from ..paths import DataPaths, ensure_private_file
from .connection import now_iso

log = get_logger("migrate")
_NAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode("utf-8")).hexdigest()


def available_migrations() -> list[Migration]:
    pkg = resources.files("contextbridge.core.db").joinpath("migrations")
    out: list[Migration] = []
    for item in pkg.iterdir():
        m = _NAME.match(item.name)
        if m:
            out.append(Migration(int(m.group(1)), item.name, item.read_text("utf-8")))
    out.sort(key=lambda mg: mg.version)
    versions = [mg.version for mg in out]
    if versions != list(range(1, len(versions) + 1)):
        raise CBError("internal", f"migration versions are not contiguous: {versions}")
    return out


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL, checksum TEXT NOT NULL)"
    )


def applied_versions(conn: sqlite3.Connection) -> dict[int, str]:
    _ensure_table(conn)
    return {r["version"]: r["checksum"] for r in conn.execute("SELECT version, checksum FROM schema_migrations")}


def pending(conn: sqlite3.Connection) -> list[Migration]:
    done = applied_versions(conn)
    return [m for m in available_migrations() if m.version not in done]


def backup_db(conn: sqlite3.Connection, paths: DataPaths, label: str) -> Path:
    """Consistent online backup (safe while other processes hold the DB open)."""
    stamp = now_iso().replace(":", "").replace("+", "p")
    dest = paths.backups / f"{label}-{stamp}.db"
    n = 1
    while dest.exists():
        dest = paths.backups / f"{label}-{stamp}-{n}.db"
        n += 1
    target = sqlite3.connect(dest)
    try:
        conn.backup(target)
    finally:
        target.close()
    ensure_private_file(dest)
    log.info("event=backup path=%s", dest.name)
    return dest


def prune_backups(paths: DataPaths, keep: int) -> list[Path]:
    files = sorted(paths.backups.glob("*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    removed = files[keep:] if keep > 0 else []
    for f in removed:
        f.unlink()
    return removed


def migrate(conn: sqlite3.Connection, paths: DataPaths, backups_keep: int = 10) -> list[int]:
    """Apply all pending migrations. Returns the versions applied."""
    todo = pending(conn)
    if not todo:
        return []
    has_data = paths.db.exists() and paths.db.stat().st_size > 0 and bool(applied_versions(conn))
    if has_data:
        backup_db(conn, paths, f"pre-migrate-{todo[0].version:04d}")
    applied: list[int] = []
    for m in todo:
        # executescript() would COMMIT implicitly; run statements one by one inside our own tx.
        conn.execute("BEGIN IMMEDIATE")
        try:
            for stmt in _split_sql(m.sql):
                conn.execute(stmt)
            conn.execute(
                "INSERT INTO schema_migrations(version, name, applied_at, checksum) VALUES (?,?,?,?)",
                (m.version, m.name, now_iso(), m.checksum),
            )
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            log.error("event=migration_failed version=%d", m.version)
            raise
        applied.append(m.version)
        log.info("event=migrated version=%d", m.version)
    ok = conn.execute("PRAGMA integrity_check").fetchone()[0]
    if ok != "ok":
        raise CBError("internal", f"integrity check failed after migration: {ok}", "restore from backups/")
    prune_backups(paths, backups_keep)
    return applied


def _split_sql(sql: str) -> list[str]:
    """Split a migration into complete statements using sqlite3.complete_statement,
    which correctly handles semicolons inside CREATE TRIGGER ... END; bodies."""
    stmts: list[str] = []
    buf: list[str] = []
    for line in sql.splitlines(keepends=True):
        if not buf and (not line.strip() or line.lstrip().startswith("--")):
            continue
        buf.append(line)
        candidate = "".join(buf)
        if sqlite3.complete_statement(candidate):
            stmts.append(candidate.strip())
            buf = []
    if "".join(buf).strip():
        raise CBError("internal", "migration file ends with an incomplete statement")
    return stmts


def verify_checksums(conn: sqlite3.Connection) -> list[str]:
    """Return a list of problems (empty = fine)."""
    done = applied_versions(conn)
    problems = []
    for m in available_migrations():
        if m.version in done and done[m.version] != m.checksum:
            problems.append(f"migration {m.name} changed after it was applied")
    return problems
