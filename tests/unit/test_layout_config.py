from __future__ import annotations

import stat
from pathlib import Path

import pytest

from contextbridge.core.app import App
from contextbridge.core.config import load_config
from contextbridge.core.errors import CBError
from contextbridge.core.paths import resolve_paths


def mode(p: Path) -> int:
    return stat.S_IMODE(p.stat().st_mode)


def test_layout_and_permissions(app: App) -> None:
    assert mode(app.paths.root) == 0o700
    for d in app.paths.subdirs():
        assert d.is_dir() and mode(d) == 0o700
    assert mode(app.paths.db) == 0o600
    assert mode(app.paths.config) == 0o600


def test_permissions_are_repaired(home: Path, app: App) -> None:
    app.paths.root.chmod(0o755)
    app.paths.config.chmod(0o644)
    from contextbridge.core.app import open_app

    open_app(home, log_to_stderr=False).close()
    assert mode(app.paths.root) == 0o700
    assert mode(app.paths.config) == 0o600


def test_wal_and_foreign_keys(app: App) -> None:
    assert app.conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert app.conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_config_defaults_and_user_override(home: Path, app: App) -> None:
    cfg = load_config(app.paths)
    assert cfg.get("profiles.claude_chat.max_files") == 20
    assert cfg.get("profiles.claude_project.max_file_bytes") == 30_000_000
    app.paths.config.write_text("[database]\nbusy_timeout_ms = 1234\n", "utf-8")
    cfg = load_config(app.paths)
    assert cfg.busy_timeout_ms == 1234
    assert cfg.get("profiles.claude_chat.max_files") == 20  # untouched defaults survive


def test_invalid_config_gives_clear_error(home: Path, app: App) -> None:
    app.paths.config.write_text("this is = = not toml", "utf-8")
    with pytest.raises(CBError) as e:
        load_config(resolve_paths(home))
    assert e.value.code == "invalid_input" and "config.toml" in e.value.message
