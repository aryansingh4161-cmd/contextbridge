"""Optional repo summary for a directory the user explicitly linked (spec §8.6).

Only `ctxb link <project> <dir>` sets `projects.linked_dir`; nothing here ever scans the disk.
Read-only git plumbing, local only (no fetch), each with a short timeout. Paths of uncommitted
files are reported; file contents are never read or exported.
"""

from __future__ import annotations

import os
import re
import shutil
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

from .db.connection import now_iso, write_tx
from .errors import CBError

GIT_TIMEOUT_S = 5
MAX_CHANGES = 200


_ENV = {"GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0", "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_ATTR_NOSYSTEM": "1", "GIT_NO_LAZY_FETCH": "1",
        "GIT_PROTOCOL_FROM_USER": "0"}
# Settings that could make a read-only command execute something or touch the network. Repo config
# is untrusted, so these are forced on every call (passed via GIT_CONFIG_COUNT env vars, where key and
# value can't be confused with each other, never via `-c key=value` strings).
_FIXED: list[tuple[str, str]] = [
    ("core.fsmonitor", "false"), ("core.hooksPath", "/dev/null"), ("core.pager", "cat"), ("diff.external", ""),
    ("log.showSignature", "false"), ("gpg.program", "false"), ("core.sshCommand", "false"),
    ("status.submoduleSummary", "false"), ("protocol.allow", "never"), ("credential.helper", ""),
    ("core.askPass", "false"), ("fetch.recurseSubmodules", "false")]
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


class UnsafeRepo(Exception):
    """The repo's config is shaped in a way we refuse to run working-tree commands against."""


def _run(path: Path, args: list[str], config: list[tuple[str, str]] | None = None) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, **_ENV}
    pairs = config or []
    env["GIT_CONFIG_COUNT"] = str(len(pairs))
    for i, (k, v) in enumerate(pairs):
        env[f"GIT_CONFIG_KEY_{i}"], env[f"GIT_CONFIG_VALUE_{i}"] = k, v
    return subprocess.run(["git", "--no-pager", *args], cwd=path, capture_output=True, text=True,
                          timeout=GIT_TIMEOUT_S, stdin=subprocess.DEVNULL, env=env)


def _config_names(path: Path, pattern: str) -> list[str]:
    out = _run(path, ["config", "-z", "--name-only", "--get-regexp", pattern], _FIXED)
    return [n for n in out.stdout.split("\0") if n]


def _overrides(path: Path) -> list[tuple[str, str]]:
    """Neutralize every filter / diff / merge driver the repo defines (reading config runs nothing).
    Fails closed on odd driver names and on partial clones (which can lazily fetch = run programs)."""
    if _config_names(path, r"^extensions\.partialclone$") or _config_names(path, r"^remote\..*\.promisor$"):
        raise UnsafeRepo("partial clone (may fetch on read)")
    pairs: list[tuple[str, str]] = []
    for name in sorted(set(_config_names(path, r"^(filter|diff|merge)\..*\."))):
        head, _, key = name.rpartition(".")
        kind, _, sub = head.partition(".")
        if not _SAFE_NAME.match(sub):
            raise UnsafeRepo("unusual filter/driver name in repo config")
        if kind == "filter":
            pairs += [(f"filter.{sub}.clean", "cat"), (f"filter.{sub}.smudge", "cat"),
                      (f"filter.{sub}.process", ""), (f"filter.{sub}.required", "false")]
        elif kind == "diff":
            pairs += [(f"diff.{sub}.textconv", ""), (f"diff.{sub}.command", "")]
        elif kind == "merge":
            pairs.append((f"merge.{sub}.driver", "false"))
    return pairs


def _git(path: Path, *args: str, worktree: bool = False) -> str:
    """Run a read-only git command. `worktree=True` for commands that read file contents (status/diff),
    which get every repo-defined driver neutralized first."""
    cfg = [*_FIXED, *(_overrides(path) if worktree else [])]
    out = _run(path, list(args), cfg)
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
        lines = _git(path, "status", "--porcelain=v1", "--untracked-files=normal", "--ignore-submodules=all",
                     worktree=True).splitlines()
        info["changes"] = [ln[3:] for ln in lines[:MAX_CHANGES]]
        info["changes_total"] = len(lines)
    except UnsafeRepo as exc:
        info["changes"] = []
        info["warning"] = f"working-tree status skipped for safety: {exc}"
        return info
    except (RuntimeError, subprocess.TimeoutExpired):
        info["changes"] = []
    try:
        stat = _git(path, "diff", "--no-ext-diff", "--no-textconv", "--ignore-submodules=all", "--shortstat",
                    worktree=True).strip()
        info["diffstat"] = stat
    except (RuntimeError, UnsafeRepo, subprocess.TimeoutExpired):
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
