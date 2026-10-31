from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

CTXB = [sys.executable, "-m", "contextbridge.cli.main"]


def run(home: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "CONTEXTBRIDGE_HOME": str(home)}
    return subprocess.run([*CTXB, *args], capture_output=True, text=True, env=env, timeout=30)


def test_init_projects_doctor(home: Path) -> None:
    r = run(home, "init")
    assert r.returncode == 0 and "data directory ready" in r.stdout
    assert (home / "contextbridge.db").exists()

    r = run(home, "projects")
    assert r.returncode == 0 and "No projects yet" in r.stdout

    r = run(home, "doctor", "--json")
    report = json.loads(r.stdout)
    by_name = {c["name"]: c["status"] for c in report["checks"]}
    for name in ["data dir", "file permissions", "db integrity", "journal mode", "migrations", "fts index"]:
        assert by_name[name] == "ok", (name, report)
    assert report["overall"] in {"ok", "warn"}


def test_cli_does_not_import_mcp_on_fast_path(home: Path) -> None:
    code = "import sys; import contextbridge.cli.main as m; print('mcp' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30)
    assert out.stdout.strip() == "False"


def test_cold_start_time(home: Path) -> None:
    """Measure, don't assert hard: report the number (BLUEPRINT: target < 100 ms for statusline)."""
    samples = []
    for _ in range(3):
        t = time.perf_counter()
        r = run(home, "version")
        samples.append((time.perf_counter() - t) * 1000)
        assert r.returncode == 0
    best = min(samples)
    print(f"\nctxb version cold start: best {best:.0f} ms of {[round(s) for s in samples]}")
    assert best < 1000


def test_snapshot_restore_backup_cli(home: Path) -> None:
    from contextbridge.core.app import open_app
    from contextbridge.core import projects as proj, state as st

    app = open_app(home, log_to_stderr=False)
    proj.create_project(app.conn, name="P", objective="o", slug="pp")
    pid = proj.get_project_id(app.conn, "pp")
    st.update_state(app.conn, pid, {"stack": st.SectionIn("Flask")})
    app.close()
    assert run(home, "checkpoint", "pp", "--summary", "s", "--where", "w", "--next", "n").returncode == 0
    out = json.loads(run(home, "snapshots", "pp", "--json").stdout)
    state_snap = next(s["id"] for s in out if s["component"] == "state")
    app = open_app(home, log_to_stderr=False)
    st.update_state(app.conn, pid, {"stack": st.SectionIn("FastAPI")})
    app.close()
    r = run(home, "diff", "pp", str(state_snap))
    assert "-Flask" in r.stdout and "+FastAPI" in r.stdout
    assert run(home, "restore", "pp", str(state_snap)).returncode == 2          # needs --yes
    r = run(home, "restore", "pp", str(state_snap), "--yes")
    assert r.returncode == 0 and "Undo with" in r.stdout
    r = run(home, "backup")
    assert r.returncode == 0 and "Backup written" in r.stdout
    assert "manual-" in run(home, "backup", "--list").stdout


def test_handoff_context_link_merge_cli(home: Path, tmp_path: Path) -> None:
    from contextbridge.core.app import open_app
    from contextbridge.core import knowledge as kn, projects as proj
    from contextbridge.core.knowledge import EntryIn

    home.mkdir(parents=True)
    (home / "config.toml").write_text("[profiles.tiny]\nmax_file_bytes = 1500\nmax_files = 0\n"
                                      "formats = ['md']\npreferred_ext = 'md'\n")
    app = open_app(home, log_to_stderr=False)
    proj.create_project(app.conn, name="P", objective="o", slug="pp")
    pid = proj.get_project_id(app.conn, "pp")
    kn.remember(app.conn, pid, [EntryIn(kind="fact", topic_key=f"f.k{i}", title=f"fact {i}", body="x " * 200,
                                        provenance="user_stated") for i in range(10)])
    app.close()
    assert run(home, "checkpoint", "pp", "--summary", "s", "--where", "w", "--next", "do the thing").returncode == 0
    r = run(home, "handoff", "pp", "--mode", "quick")
    assert r.returncode == 0 and "Handoff written:" in r.stdout and "Next: do the thing" in r.stdout
    r = run(home, "context", "pp", "--scope", "state")
    assert r.returncode == 0 and "do the thing" in r.stdout
    assert run(home, "link", "pp").returncode == 1                       # needs a dir or --unlink
    r = run(home, "link", "pp", str(tmp_path))
    assert r.returncode == 0 and "Linked pp" in r.stdout
    out = json.loads(run(home, "handoff", "pp", "--profile", "tiny", "--json").stdout)
    r = run(home, "merge", out["directory"])
    assert r.returncode == 0 and "✓" in r.stdout and "MISMATCH" not in r.stdout


def test_export_import_cli_moves_to_another_mac(home: Path, tmp_path: Path) -> None:
    from contextbridge.core.app import open_app
    from contextbridge.core import projects as proj

    app = open_app(home, log_to_stderr=False)
    proj.create_project(app.conn, name="P", objective="o", slug="pp")
    app.close()
    assert run(home, "checkpoint", "pp", "--summary", "s", "--where", "w", "--next", "go on").returncode == 0
    assert run(home, "export").returncode == 1                            # must say --full
    r = run(home, "export", "--full", "-o", str(tmp_path), "--json")
    assert r.returncode == 0
    path = json.loads(r.stdout)["path"]
    other = tmp_path / "other-mac"
    r = run(other, "import", "--full", path, "--dry-run")
    assert r.returncode == 0 and "would import pp" in r.stdout
    r = run(other, "import", "--full", path)
    assert r.returncode == 0 and "✓ pp" in r.stdout and "ids preserved" in r.stdout
    assert "go on" in run(other, "context", "pp", "--scope", "state").stdout
    r = run(other, "import", "--full", path)
    assert r.returncode == 1 and "already exists" in r.stderr and "--as" in r.stderr


def test_merge_rejects_unsafe_manifest_names(home: Path, tmp_path: Path) -> None:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (tmp_path / "secret.txt").write_text("outside")
    (pkg / "00_manifest.json").write_text(json.dumps({"files": [
        {"name": "../secret.txt", "upload": True, "of": 2, "part": 1, "source": "x", "source_sha256": "0"},
        {"name": "b.md", "upload": True, "of": 2, "part": 2, "source": "x"}]}))
    r = run(home, "merge", str(pkg))
    assert r.returncode == 1 and "unsafe file name" in r.stderr
    (pkg / "00_manifest.json").write_text(json.dumps({"files": [
        {"name": "a.md", "upload": True, "of": 1, "part": 1, "source": "../../escape"},
        {"name": "a.md", "upload": True, "of": 2, "part": 1, "source": "../../escape", "source_sha256": "0"}]}))
    (pkg / "a.md").write_text("a")
    r = run(home, "merge", str(pkg))
    assert "HASH MISMATCH" in r.stdout and not (tmp_path.parent / "escape.merged.md").exists()
