"""SQLite connection with the settings required for multi-process use (spec §5.4, §4)."""

from __future__ import annotations

import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime

from ..paths import DataPaths, ensure_private_file


def now_iso() -> str:
    """Local time with UTC offset, microsecond precision (e.g. 2026-10-31T14:05:00.123456+05:30).

    Microseconds matter: "writes since the last checkpoint" compares these strings, and with
    second precision a write in the same second as a checkpoint was not counted as unsaved.
    Always the same fixed-width format, so lexicographic order = time order (within one UTC offset)."""
    return datetime.now().astimezone().isoformat(timespec="microseconds")


def connect(paths: DataPaths, busy_timeout_ms: int = 5000) -> sqlite3.Connection:
    """Open the DB in WAL mode. `isolation_level=None` = we manage transactions explicitly."""
    conn = sqlite3.connect(paths.db, timeout=busy_timeout_ms / 1000, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    for suffix in ("", "-wal", "-shm"):
        ensure_private_file(paths.db.with_name(paths.db.name + suffix))
    return conn


@contextmanager
def write_tx(conn: sqlite3.Connection) -> Generator[sqlite3.Connection]:
    """Short write transaction. BEGIN IMMEDIATE takes the write lock up front, so two
    processes never both read-then-upgrade and deadlock; the busy timeout makes the second wait."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")
