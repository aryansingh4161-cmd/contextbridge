"""Whole-database backups (spec §5.4, §9; acceptance Test 7).

There's no daemon: scheduled backups are opportunistic. Whenever a server/CLI starts or a
checkpoint is written, a backup is taken if the newest one is older than
`database.backup_interval_hours`. Restore uses SQLite's online backup API into the live DB,
which is safe with WAL and with other processes holding the file open (they see the new data
on their next transaction).
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

from .db.migrate import backup_db, prune_backups
from .errors import CBError
from .logging_setup import get_logger
from .paths import DataPaths

log = get_logger("backup")


def list_backups(paths: DataPaths) -> list[dict[str, Any]]:
    out = []
    for p in sorted(paths.backups.glob("*.db"), key=lambda f: f.stat().st_mtime, reverse=True):
        st = p.stat()
        out.append({"file": p.name, "bytes": st.st_size,
                    "modified": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(st.st_mtime))})
    return out


def newest_backup_age_hours(paths: DataPaths) -> float | None:
    files = list(paths.backups.glob("*.db"))
    if not files:
        return None
    return (time.time() - max(f.stat().st_mtime for f in files)) / 3600


def maybe_scheduled_backup(conn: sqlite3.Connection, paths: DataPaths, interval_hours: float,
                           keep: int) -> Path | None:
    if interval_hours <= 0:
        return None
    if not conn.execute("SELECT count(*) FROM projects").fetchone()[0]:
        return None  # nothing worth backing up yet
    age = newest_backup_age_hours(paths)
    if age is not None and age < interval_hours:
        return None
    dest = backup_db(conn, paths, "scheduled")
    prune_backups(paths, keep)
    return dest


def restore_backup(conn: sqlite3.Connection, paths: DataPaths, filename: str, keep: int) -> dict[str, Any]:
    src = (paths.backups / filename).resolve()
    if src.parent != paths.backups.resolve() or not src.is_file():
        raise CBError("not_found", f"backup '{filename}' not found in {paths.backups}",
                      "list them with `ctxb backup --list`")
    probe = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    try:
        if probe.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise CBError("internal", f"backup '{filename}' fails its integrity check; choose another")
        probe.execute("SELECT count(*) FROM projects").fetchone()
    except sqlite3.DatabaseError as exc:
        raise CBError("internal", f"backup '{filename}' is not a usable ContextBridge DB: {exc}") from exc
    finally:
        probe.close()
    safety = backup_db(conn, paths, "pre-restore")
    srcconn = sqlite3.connect(src)
    try:
        srcconn.backup(conn)
    finally:
        srcconn.close()
    prune_backups(paths, max(keep, 2))
    log.info("event=db_restored from=%s safety=%s", filename, safety.name)
    return {"restored_from": filename, "safety_backup": safety.name}
