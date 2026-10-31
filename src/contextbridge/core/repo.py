"""Optional repo summary for a directory the user explicitly linked (spec §8.6).

Only `ctxb link <project> <dir>` sets `projects.linked_dir`; nothing here ever scans the disk.
Read-only git plumbing, local only (no fetch), each with a short timeout. Paths of uncommitted
files are reported; file contents are never read or exported.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

from .db.connection import now_iso, write_tx
from .errors import CBError

GIT_TIMEOUT_S = 5
MAX_CHANGES = 200


def _git(path: Path, *args: str) -> str:
    # Repo config is untrusted (a cloned repo can set core.fsmonitor or diff drivers that run
    # programs): disable everything that can execute, and never page.
    safe = ["-c", "core.fsmonitor=false", "-c", "core.pager=cat", "-c", "core.hooksPath=/dev/null",
            "-c", "diff.external=", "--no-pager"]
    out = subprocess.run(["git", *safe, "-C", str(path), *args], capture_output=True, text=True, timeout=GIT_TIMEOUT_S, stdin=subprocess.DEVNULL,
                         env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"})
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip().splitlines()[0] if out.stderr.strip() else "git failed")
    return out.stdout


def repo_summary(linked_dir: str | None) -> dict[str, Any] | None:
    if not linked_dir:
        return None
    path = Path(linked_dir)
    if not path.is_dir():
        return {"path": linked_dir, "error": "directory not found on this machine"}
    if shutil.which("git") is None:
        return {"path": linked_dir, "error": "git is not installed; no repo details"}
    try:
        _git(path, "rev-parse", "--is-inside-work-tree")
    except (RuntimeError, OSError, subprocess.TimeoutExpired):
        return {"path": linked_dir, "error": "not a git repository; no repo details"}
    info: dict[str, Any] = {"path": linked_dir}
    try:
        info["branch"] = _git(path, "rev-parse", "--abbrev-ref", "HEAD").strip()
    except (RuntimeError, subprocess.TimeoutExpired):
        info["branch"] = "(none)"
    try:
        info["last_commit"] = _git(path, "log", "-1", "--format=%h %s (%cI)").strip() or "(no commits)"
    except (RuntimeError, subprocess.TimeoutExpired):
        info["last_commit"] = "(no commits)"
    try:
        lines = _git(path, "status", "--porcelain=v1", "--untracked-files=normal").splitlines()
        info["changes"] = [ln[3:] for ln in lines[:MAX_CHANGES]]
        info["changes_total"] = len(lines)
    except (RuntimeError, subprocess.TimeoutExpired):
        info["changes"] = []
    try:
        stat = _git(path, "diff", "--no-ext-diff", "--no-textconv", "--shortstat").strip()
        info["diffstat"] = stat
    except (RuntimeError, subprocess.TimeoutExpired):
        pass
    return info


def link(conn: sqlite3.Connection, project_id: int, directory: str | None) -> str | None:
    """Set (or clear, with None) the project's linked directory. Stores the resolved absolute path."""
    value = None
    if directory is not None:
        p = Path(directory).expanduser().resolve()
        if not p.is_dir():
            raise CBError("invalid_input", f"'{directory}' is not a directory")
        value = str(p)
    with write_tx(conn):
        conn.execute("UPDATE projects SET linked_dir = ?, updated_at = ? WHERE id = ?", (value, now_iso(), project_id))
    return value
