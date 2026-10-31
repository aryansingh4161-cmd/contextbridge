"""M7: client installers and the SessionStart hook. All against a fake HOME and a fake `claude`."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from contextbridge.adapters.claude_code.hooks import session_start
from contextbridge.adapters.install import (claude_settings_path, desktop_config_path, install_code,
                                            install_desktop)
from contextbridge.core import projects as proj
from contextbridge.core.app import App
from contextbridge.core.checkpoints import CheckpointIn, checkpoint
from contextbridge.core.errors import CBError
from contextbridge.core.paths import resolve_paths
from contextbridge.core.repo import link

FAKE_CLAUDE = """#!/bin/sh
echo "$@" >> "$FAKE_CLAUDE_LOG"
state="$FAKE_CLAUDE_LOG.state"
case "$1 $2" in
  "mcp get") [ -f "$state" ] && exit 0 || exit 1 ;;
  "mcp add") touch "$state"; exit 0 ;;
  "mcp remove") rm -f "$state"; exit 0 ;;
esac
exit 0
"""


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = tmp_path / "Users" / "me"
    h.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("CONTEXTBRIDGE_HOME", str(h / ".contextbridge"))
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "claude").write_text(FAKE_CLAUDE)
    (bindir / "claude").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(tmp_path / "claude.log"))
    return h


CMD = Path("/opt/cb/bin/contextbridge-mcp")


def test_desktop_merge_backup_idempotent_uninstall(fake_home: Path) -> None:
    cfg = desktop_config_path()
    cfg.parent.mkdir(parents=True)
    original = {"coworkUserFilesPath": "/x", "preferences": {"a": 1},
                "mcpServers": {"other": {"command": "/bin/other", "args": ["-v"]}}}
    cfg.write_text(json.dumps(original))
    cfg.chmod(0o644)
    paths = resolve_paths(fake_home / ".contextbridge")
    dry = install_desktop(paths, dry_run=True, command=CMD)
    assert dry["dry_run"] and json.loads(cfg.read_text()) == original
    out = install_desktop(paths, command=CMD)
    data = json.loads(cfg.read_text())
    assert data["mcpServers"]["contextbridge"] == {"command": str(CMD), "args": []}
    assert data["mcpServers"]["other"] == original["mcpServers"]["other"]
    assert data["preferences"] == {"a": 1} and data["coworkUserFilesPath"] == "/x"
    assert json.loads(Path(out["backup"]).read_text()) == original
    assert stat.S_IMODE(Path(out["backup"]).stat().st_mode) == 0o600
    assert stat.S_IMODE(cfg.stat().st_mode) == 0o644, "keeps the original file's permissions"
    assert install_desktop(paths, command=CMD)["changed"] is False
    install_desktop(paths, uninstall=True)
    assert json.loads(cfg.read_text()) == original


def test_desktop_custom_home_env_and_new_file(fake_home: Path, tmp_path: Path) -> None:
    paths = resolve_paths(tmp_path / "elsewhere")
    install_desktop(paths, command=CMD)
    entry = json.loads(desktop_config_path().read_text())["mcpServers"]["contextbridge"]
    assert entry["env"] == {"CONTEXTBRIDGE_HOME": str(tmp_path / "elsewhere")}


def test_desktop_refuses_invalid_json(fake_home: Path) -> None:
    cfg = desktop_config_path()
    cfg.parent.mkdir(parents=True)
    cfg.write_text("{not json")
    with pytest.raises(CBError):
        install_desktop(resolve_paths(fake_home / ".contextbridge"), command=CMD)
    assert cfg.read_text() == "{not json"
    cfg.write_text(json.dumps({"mcpServers": []}))
    with pytest.raises(CBError):
        install_desktop(resolve_paths(fake_home / ".contextbridge"), command=CMD)


def test_code_install_uses_verified_command_and_merges_hook(fake_home: Path, tmp_path: Path) -> None:
    sp = claude_settings_path()
    sp.parent.mkdir(parents=True)
    other = {"hooks": [{"type": "command", "command": "echo hi"}]}
    sp.write_text(json.dumps({"model": "opus", "hooks": {"SessionStart": [other],
                                                          "PreToolUse": [{"matcher": "Bash", "hooks": []}]}}))
    paths = resolve_paths(fake_home / ".contextbridge")
    ctxb = tmp_path / "it's here" / "ctxb"
    out = install_code(paths, command=CMD, ctxb=ctxb)
    log = (tmp_path / "claude.log").read_text().splitlines()
    assert f"mcp add --transport stdio --scope user contextbridge -- {CMD}" in log
    assert out["mcp"].startswith("added") and out["hook"]["status"] == "added"
    s = json.loads(sp.read_text())
    assert s["model"] == "opus" and s["hooks"]["PreToolUse"] == [{"matcher": "Bash", "hooks": []}]
    assert s["hooks"]["SessionStart"][0] == other
    ours = s["hooks"]["SessionStart"][1]["hooks"][0]
    assert ours["type"] == "command" and ours["timeout"] == 10
    import shlex
    assert ours["command"] == f"{shlex.quote(str(ctxb))} hook session-start", "shell-quoted (path has an apostrophe)"
    assert subprocess.run(["sh", "-c", f"printf %s {shlex.quote(str(ctxb))}"], capture_output=True,
                          text=True).stdout == str(ctxb)
    again = install_code(paths, command=CMD, ctxb=ctxb)
    assert again["mcp"].startswith("already") and again["hook"]["status"] == "already configured"
    install_code(paths, uninstall=True)
    s = json.loads(sp.read_text())
    assert s["hooks"]["SessionStart"] == [other]
    assert "mcp remove contextbridge --scope user" in (tmp_path / "claude.log").read_text()


def _linked_project(app: App, d: Path) -> int:
    proj.create_project(app.conn, name="Folio", objective="photo portfolio", slug="folio")
    pid = proj.get_project_id(app.conn, "folio")
    link(app.conn, pid, str(d))
    checkpoint(app.conn, pid, CheckpointIn(summary="s", where_stopped="Lightbox.astro swipe broken",
                                           next_action="fix swipe with pointer events"))
    return pid


def test_session_start_hook_loads_linked_project(app: App, tmp_path: Path) -> None:
    repo = tmp_path / "folio"
    (repo / "src" / "components").mkdir(parents=True)
    _linked_project(app, repo)
    out = session_start(json.dumps({"cwd": str(repo / "src" / "components"), "source": "startup"}), str(app.paths.root))
    assert 'project="folio"' in out and "fix swipe with pointer events" in out and len(out) <= 9500
    assert out.rstrip().endswith("<<<END_CONTEXTBRIDGE_MEMORY>>>")
    assert session_start(json.dumps({"cwd": str(tmp_path), "source": "startup"}), str(app.paths.root)) == ""
    assert session_start("not json", str(app.paths.root)) == ""


def test_hook_cli_never_fails_and_quoted_command_runs(app: App, tmp_path: Path) -> None:
    repo = tmp_path / "folio"
    repo.mkdir()
    _linked_project(app, repo)
    env = {**os.environ, "CONTEXTBRIDGE_HOME": str(app.paths.root)}
    ctxb = Path(sys.executable).parent / "ctxb"
    import shlex
    cmd = f"{shlex.quote(str(ctxb))} hook session-start"  # exactly how the installer writes it
    r = subprocess.run(["sh", "-c", cmd], input=json.dumps({"cwd": str(repo), "source": "startup"}),
                       capture_output=True, text=True, env=env, timeout=30)
    assert r.returncode == 0 and "fix swipe with pointer events" in r.stdout
    broken = {**env, "CONTEXTBRIDGE_HOME": "/dev/null/nope"}
    r = subprocess.run(["sh", "-c", cmd], input=json.dumps({"cwd": str(repo)}), capture_output=True, text=True,
                       env=broken, timeout=30)
    assert r.returncode == 0 and r.stdout == "", "a broken data dir must not break the Claude session"
