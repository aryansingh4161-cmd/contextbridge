"""Bootstrap shared by the MCP server and the CLI: paths → layout → config → logging → DB → migrate."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass

from .config import Config, load_config, write_default_config
from .db.connection import connect
from .db.migrate import migrate
from .logging_setup import get_logger, setup_logging
from .paths import DataPaths, ensure_layout, ensure_private_file, resolve_paths

log = get_logger("app")


@dataclass
class App:
    paths: DataPaths
    config: Config
    conn: sqlite3.Connection  # owned by the opening thread (CLI, tests)

    @contextmanager
    def db(self) -> Generator[sqlite3.Connection]:
        """A fresh connection for one unit of work. The MCP SDK runs sync tools in worker
        threads, and sqlite3 connections must not cross threads; opening per call is ~1 ms."""
        conn = connect(self.paths, self.config.busy_timeout_ms)
        try:
            yield conn
        finally:
            conn.close()

    def scheduled_backup(self) -> None:
        from .backup import maybe_scheduled_backup

        try:
            maybe_scheduled_backup(self.conn, self.paths, float(self.config.get("database.backup_interval_hours", 24)),
                                   self.config.backups_keep)
        except (OSError, sqlite3.Error) as exc:  # a failed backup must never block work; doctor reports it
            log.warning("event=scheduled_backup_failed error=%s", type(exc).__name__)

    def close(self) -> None:
        self.conn.close()


def open_app(root: str | os.PathLike[str] | None = None, *, log_to_stderr: bool = True,
             maintenance: bool = True) -> App:
    """`maintenance=False` (status line, hooks: latency-critical) skips the opportunistic DB backup."""
    paths = resolve_paths(root)
    ensure_layout(paths)
    write_default_config(paths)
    ensure_private_file(paths.config)
    config = load_config(paths)
    setup_logging(paths, config.log_level, stderr=log_to_stderr)
    conn = connect(paths, config.busy_timeout_ms)
    applied = migrate(conn, paths, config.backups_keep)
    if applied:
        log.info("event=schema_ready applied=%s", applied)
    app = App(paths, config, conn)
    if maintenance:
        app.scheduled_backup()
    return app
