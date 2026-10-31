"""Structured logging to stderr and a rotating file. Never to stdout.

In stdio mode stdout carries the MCP protocol (spec §14), so nothing here may write to it.
Log lines are key=value; callers must log ids/counts/timings, never memory content.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys

from .paths import DataPaths, ensure_private_file

_FORMAT = "%(asctime)s level=%(levelname)s logger=%(name)s pid=%(process)d %(message)s"
_configured = False


def setup_logging(paths: DataPaths | None, level: str = "INFO", *, stderr: bool = True) -> None:
    global _configured
    root = logging.getLogger("contextbridge")
    if _configured:
        root.setLevel(level)
        return
    root.setLevel(level)
    root.propagate = False
    fmt = logging.Formatter(_FORMAT)
    if stderr:
        h = logging.StreamHandler(sys.stderr)
        h.setFormatter(fmt)
        root.addHandler(h)
    if paths is not None and paths.logs.exists():
        log_file = paths.logs / "contextbridge.log"
        fh = logging.handlers.RotatingFileHandler(log_file, maxBytes=2_000_000, backupCount=5)
        fh.setFormatter(fmt)
        root.addHandler(fh)
        ensure_private_file(log_file)
    _configured = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"contextbridge.{name}")
