"""`ctxb doctor`: health checks (spec §14). M1 covers DB, migrations, FTS, permissions,
backups and client-config detection; later milestones add stale/oversized/conflict checks."""

from __future__ import annotations

import json
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from ..core.app import open_app
from ..core.db.migrate import available_migrations, pending, verify_checksums
from ..core.paths import DIR_MODE, FILE_MODE

Status = Literal["ok", "warn", "fail", "info"]
SERVER_NAME = "contextbridge"
DESKTOP_CONFIG = Path.home() / "Library/Application Support/Claude/claude_desktop_config.json"
CLAUDE_CODE_USER_CONFIG = Path.home() / ".claude.json"


@dataclass
class Check:
    name: str
    status: Status
    detail: str


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)

    def add(self, name: str, status: Status, detail: str) -> None:
        self.checks.append(Check(name, status, detail))

    @property
    def overall(self) -> Literal["ok", "warn", "fail"]:
        statuses = {c.status for c in self.checks}
        return "fail" if "fail" in statuses else "warn" if "warn" in statuses else "ok"

    def to_dict(self) -> dict[str, Any]:
        return {"overall": self.overall, "checks": [c.__dict__ for c in self.checks]}


def _mode(p: Path) -> int:
    return stat.S_IMODE(p.stat().st_mode)


def _has_server(config_path: Path) -> bool | None:
    """True/False if the JSON config lists our server under mcpServers; None if unreadable/absent.
    Only key names are read; values (which may hold secrets) are never printed."""
    if not config_path.exists():
        return None
    try:
        data = json.loads(config_path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return isinstance(data, dict) and SERVER_NAME in (data.get("mcpServers") or {})


def run_doctor(home: str | None) -> Report:
    r = Report()
    app = open_app(home)
    try:
        conn = app.conn
        r.add("data dir", "ok" if _mode(app.paths.root) == DIR_MODE else "fail",
              f"{app.paths.root} (mode {oct(_mode(app.paths.root))})")
        icloud_roots = [Path.home() / d for d in ("Desktop", "Documents", "Library/Mobile Documents")]
        in_icloud = any(app.paths.root.is_relative_to(r) for r in icloud_roots)
        r.add("sync safety", "warn" if in_icloud else "ok",
              "data dir may be iCloud-synced; a live SQLite DB can be corrupted by sync. Move it outside "
              "Desktop/Documents/iCloud Drive" if in_icloud else "data dir is outside iCloud-synced folders")
        bad = [p.name for p in [app.paths.db, app.paths.config] if p.exists() and _mode(p) != FILE_MODE]
        r.add("file permissions", "fail" if bad else "ok", f"not 600: {bad}" if bad else "db and config are 600")

        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        r.add("db integrity", "ok" if integrity == "ok" else "fail", integrity)
        jm = conn.execute("PRAGMA journal_mode").fetchone()[0]
        r.add("journal mode", "ok" if jm == "wal" else "fail", jm)

        latest = available_migrations()[-1].version
        pend = pending(conn)
        r.add("migrations", "ok" if not pend else "warn",
              f"schema v{latest}" if not pend else f"{len(pend)} pending")
        for problem in verify_checksums(conn):
            r.add("migration checksum", "fail", problem)

        try:
            conn.execute("INSERT INTO entries_fts(entries_fts) VALUES ('integrity-check')")
            r.add("fts index", "ok", "entries_fts consistent")
        except Exception as exc:  # sqlite3.DatabaseError on corruption
            r.add("fts index", "fail", f"entries_fts: {exc}; run `ctxb doctor --rebuild-fts` (M2)")

        n_proj = conn.execute("SELECT count(*) FROM projects").fetchone()[0]
        n_conf = conn.execute("SELECT count(*) FROM conflicts WHERE status='open'").fetchone()[0]
        r.add("projects", "info", f"{n_proj} project(s)")
        r.add("open conflicts", "warn" if n_conf else "ok", str(n_conf))

        backups = sorted(app.paths.backups.glob("*.db"))
        r.add("backups", "info", f"{len(backups)} file(s) in backups/" if backups else
              "none yet (created before each migration and daily when in use)")

        for label, path in [("Claude Desktop", DESKTOP_CONFIG), ("Claude Code (user scope)", CLAUDE_CODE_USER_CONFIG)]:
            found = _has_server(path)
            if found is None:
                r.add(f"client: {label}", "info", "config not found")
            else:
                r.add(f"client: {label}", "ok" if found else "info",
                      "contextbridge configured" if found else "contextbridge not configured (ctxb install-claude-"
                      + ("desktop)" if label == "Claude Desktop" else "code)"))
        settings = Path.home() / ".claude" / "settings.json"
        try:
            hook = "hook session-start" in settings.read_text(encoding="utf-8")
        except OSError:
            hook = False
        r.add("client: Claude Code SessionStart hook", "ok" if hook else "info",
              "installed (loads linked projects' context)" if hook else "not installed (optional)")
        linked = app.conn.execute("SELECT count(*) FROM projects WHERE linked_dir IS NOT NULL").fetchone()[0]
        r.add("linked folders", "info", f"{linked} project(s) linked to a folder (ctxb link)")
    finally:
        app.close()
    return r
