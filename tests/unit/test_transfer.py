"""M5: full export / import round trips (spec §8.4 "test the round trip", §11 "another Mac")."""

from __future__ import annotations

import json
import re
import stat
import zipfile
from pathlib import Path

import pytest
from compression import zstd

from contextbridge.core import knowledge as kn
from contextbridge.core import projects as proj
from contextbridge.core import state as st
from contextbridge.core.app import App, open_app
from contextbridge.core.backup import list_backups
from contextbridge.core.db.connection import now_iso, write_tx
from contextbridge.core.errors import CBError
from contextbridge.core.handoff import prepare_handoff
from contextbridge.core.knowledge import EntryIn
from contextbridge.core.snapshots import build_payload, diff, list_snapshots, load_snapshot, restore
from contextbridge.core.transfer import export_full, import_full

from fixtures.five_sessions import five_sessions


def _add_archive(app: App, pid: int, text: str) -> None:
    import hashlib
    with write_tx(app.conn):
        cur = app.conn.execute(
            "INSERT INTO raw_archive(project_id, source_type, title, sha256, codec, content, created_at)"
            " VALUES (?,?,?,?,'zstd',?,?)", (pid, "transcript", "chat 1", hashlib.sha256(text.encode()).hexdigest(),
                                            zstd.compress(text.encode()), now_iso()))
        app.conn.execute("INSERT INTO archive_fts(rowid, title, text) VALUES (?,?,?)", (cur.lastrowid, "chat 1", text))


def _comparable(app: App, slug: str) -> dict[str, object]:
    pid = proj.get_project_id(app.conn, slug)
    k = build_payload(app.conn, pid, "knowledge")
    s = build_payload(app.conn, pid, "state")
    hist = app.conn.execute("SELECT count(*) FROM entry_history h JOIN entries e ON e.id=h.entry_id"
                            " WHERE e.project_id=?", (pid,)).fetchone()[0]
    return {"knowledge": k, "state": s, "history": hist,
            "checkpoints": [dict(r) for r in app.conn.execute(
                "SELECT summary, where_stopped, next_action, kind, created_at FROM checkpoints WHERE project_id=?"
                " ORDER BY id", (pid,))],
            "snapshots": [(x["component"], x["version"]) for x in list_snapshots(app.conn, pid)]}


@pytest.fixture
def other_home(tmp_path: Path) -> Path:
    return tmp_path / "newmac"


def test_full_round_trip_into_empty_db(app: App, other_home: Path) -> None:
    pid, ids = five_sessions(app)
    _add_archive(app, pid, "we discussed the zebra-unique-word OCR pipeline")
    proj.create_project(app.conn, name="Second", objective="o2", slug="second")
    before = {s: _comparable(app, s) for s in ("sih", "second")}
    out = export_full(app.conn, app.paths)
    zpath = Path(out["path"])
    assert stat.S_IMODE(zpath.stat().st_mode) == 0o600 and out["projects"] == ["second", "sih"]

    new = open_app(other_home, log_to_stderr=False)
    try:
        res = import_full(new.conn, new.paths, zpath)
        assert all(r["ids_preserved"] for r in res["imported"])
        for s in ("sih", "second"):
            assert _comparable(new, s) == before[s], f"{s} differs after round trip"
        npid = proj.get_project_id(new.conn, "sih")
        # FTS rebuilt for knowledge and archive
        assert kn.search(new.conn, npid, "FastAPI")["hits"]
        assert kn.search(new.conn, npid, "zebra", include_archive=True)["hits"]
        # snapshots load (hash-verified) and restore still works
        snaps = list_snapshots(new.conn, npid)
        load_snapshot(new.conn, npid, snaps[0]["id"])
        state_snap = next(x["id"] for x in snaps if x["component"] == "state")
        restore(new.conn, npid, state_snap)
        # a handoff from the migrated DB carries the same tags
        h = Path(prepare_handoff(new.conn, new.paths, new.config, npid)["primary_file"]).read_text(encoding="utf-8")
        assert f"[D-{ids['fastapi']:04d} · user_stated" in h
        assert new.conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert any(b.name.startswith("pre-import") for b in new.paths.backups.iterdir())
    finally:
        new.close()


def test_import_with_colliding_ids_remaps_every_reference(app: App, other_home: Path) -> None:
    pid, ids = five_sessions(app)
    out = export_full(app.conn, app.paths, ["sih"])
    new = open_app(other_home, log_to_stderr=False)
    try:  # the new machine already has its own project occupying low ids
        proj.create_project(new.conn, name="Local", objective="o", slug="local")
        lp = proj.get_project_id(new.conn, "local")
        kn.remember(new.conn, lp, [EntryIn(kind="fact", topic_key=f"x.k{i}", title=f"local fact {i}",
                                           provenance="user_stated") for i in range(12)])
        st.update_state(new.conn, lp, {"stack": st.SectionIn("Go")},
                        [st.TaskIn(title="a", status="todo"), st.TaskIn(title="b", status="todo")])
        res = import_full(new.conn, new.paths, out["path"])["imported"][0]
        assert not res["ids_preserved"] and "entries" in res["id_map"]
        npid = proj.get_project_id(new.conn, "sih")
        m = {int(k): v for k, v in res["id_map"]["entries"].items()}
        fastapi = new.conn.execute("SELECT * FROM entries WHERE id = ?", (m[ids["fastapi"]],)).fetchone()
        assert fastapi["title"] == "Use FastAPI for the backend" and fastapi["project_id"] == npid
        assert fastapi["supersedes_id"] == m[ids["flask"]]
        stack = new.conn.execute("SELECT refs FROM live_state WHERE project_id = ? AND section = 'stack'",
                                 (npid,)).fetchone()
        assert json.loads(stack["refs"]) == [m[ids["fastapi"]]]
        # the newest snapshot of each component equals the current data: payloads were rewritten consistently
        for comp in ("knowledge", "state"):
            snap = next(x["id"] for x in list_snapshots(new.conn, npid, comp))
            d = diff(new.conn, npid, snap, None)
            assert d.get("entries", d.get("tasks"))["changed"] == [] and not d.get("sections"), (comp, d)
        assert new.conn.execute("PRAGMA foreign_key_check").fetchall() == []
        # the local project is untouched
        assert new.conn.execute("SELECT count(*) FROM entries WHERE project_id = ?", (lp,)).fetchone()[0] == 12
    finally:
        new.close()


def test_existing_slug_refused_then_imported_under_new_slug(app: App) -> None:
    five_sessions(app)
    out = export_full(app.conn, app.paths, ["sih"])
    n_before = app.conn.execute("SELECT count(*) FROM entries").fetchone()[0]
    with pytest.raises(CBError) as e:
        import_full(app.conn, app.paths, out["path"])
    assert e.value.code == "conflict"
    assert app.conn.execute("SELECT count(*) FROM entries").fetchone()[0] == n_before
    res = import_full(app.conn, app.paths, out["path"], as_slug="sih-copy")["imported"][0]
    assert res["slug"] == "sih-copy" and not res["ids_preserved"]
    assert app.conn.execute("SELECT count(*) FROM entries").fetchone()[0] == 2 * n_before


def test_corrupt_archive_is_refused_without_changes(app: App, other_home: Path, tmp_path: Path) -> None:
    five_sessions(app)
    good = Path(export_full(app.conn, app.paths)["path"])
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(good) as zin, zipfile.ZipFile(bad, "w") as zout:
        for item in zin.infolist():
            data = zin.read(item)
            if item.filename == "projects/sih.json":
                data = data.replace(b"FastAPI", b"Fastapi")
            zout.writestr(item, data)
    new = open_app(other_home, log_to_stderr=False)
    try:
        with pytest.raises(CBError) as e:
            import_full(new.conn, new.paths, bad)
        assert "sha256" in e.value.message
        assert new.conn.execute("SELECT count(*) FROM projects").fetchone()[0] == 0
        with pytest.raises(CBError):
            import_full(new.conn, new.paths, tmp_path / "missing.zip")
        not_zip = tmp_path / "x.zip"
        not_zip.write_text("hello")
        with pytest.raises(CBError):
            import_full(new.conn, new.paths, not_zip)
    finally:
        new.close()


def test_package_sidecar_rebuilds_project_with_ids_and_provenance(app: App, other_home: Path) -> None:
    pid, ids = five_sessions(app)
    kn.remember(app.conn, pid, [EntryIn(kind="risk", topic_key="ocr.accuracy", title="OCR may misread seals",
                                        provenance="ai_suggested", importance="important")])
    pkg = prepare_handoff(app.conn, app.paths, app.config, pid, mode="standard")
    original = Path(pkg["primary_file"]).read_text(encoding="utf-8")
    new = open_app(other_home, log_to_stderr=False)
    try:
        res = import_full(new.conn, new.paths, pkg["directory"])["imported"][0]
        assert res["ids_preserved"] and res["slug"] == "sih"
        npid = proj.get_project_id(new.conn, "sih")
        prov = {r["title"]: r["provenance"] for r in new.conn.execute("SELECT title, provenance FROM entries")}
        assert prov["OCR may misread seals"] == "ai_suggested", "provenance kept, not flattened to 'imported'"
        rebuilt = Path(prepare_handoff(new.conn, new.paths, new.config, npid)["primary_file"]).read_text(encoding="utf-8")
        tags = lambda t: set(re.findall(r"\[[A-Z]+-\d{4} · [a-z_]+ · [\d-]+(?: · [a-z]+)?\]", t))  # noqa: E731
        assert tags(rebuilt) == tags(original)
    finally:
        new.close()


def test_dry_run_and_selection(app: App, other_home: Path) -> None:
    five_sessions(app)
    proj.create_project(app.conn, name="Second", objective="o2", slug="second")
    out = export_full(app.conn, app.paths)
    with pytest.raises(CBError):
        export_full(app.conn, app.paths, ["nope"])
    with pytest.raises(CBError):
        export_full(app.conn, app.paths, None, out["path"])  # never overwrites
    new = open_app(other_home, log_to_stderr=False)
    try:
        plan = import_full(new.conn, new.paths, out["path"], dry_run=True)
        assert {p["slug"] for p in plan["would_import"]} == {"sih", "second"}
        assert new.conn.execute("SELECT count(*) FROM projects").fetchone()[0] == 0
        assert not list_backups(new.paths)
        import_full(new.conn, new.paths, out["path"], only=["second"])
        assert [r["slug"] for r in new.conn.execute("SELECT slug FROM projects")] == ["second"]
    finally:
        new.close()


def test_import_never_trusts_linked_dir(app: App, other_home: Path, tmp_path: Path) -> None:
    from contextbridge.core.repo import link
    pid, _ = five_sessions(app)
    link(app.conn, pid, str(tmp_path))
    out = export_full(app.conn, app.paths)
    new = open_app(other_home, log_to_stderr=False)
    try:
        import_full(new.conn, new.paths, out["path"])
        assert new.conn.execute("SELECT linked_dir FROM projects WHERE slug='sih'").fetchone()[0] is None
    finally:
        new.close()


def test_git_never_runs_repo_configured_programs(tmp_path: Path) -> None:
    """A hostile repo config (fsmonitor, clean/smudge/process filters, textconv) must never execute."""
    import subprocess
    from contextbridge.core.repo import repo_summary
    repo, marker = tmp_path / "evil", tmp_path / "PWNED"
    repo.mkdir()
    g = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)  # noqa: E731
    g("init", "-q")
    hook = tmp_path / "hook.sh"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\ncat\n")
    hook.chmod(0o755)
    (repo / ".gitattributes").write_text("*.txt filter=evil diff=evil\n*.dat filter=proc\n")
    (repo / "a.txt").write_text("x")
    (repo / "b.dat").write_text("y")
    g("add", ".")
    g("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "c")
    for k, v in [("core.fsmonitor", str(hook)), ("filter.evil.clean", str(hook)), ("filter.evil.smudge", str(hook)),
                 ("filter.proc.process", str(hook)), ("diff.evil.textconv", str(hook))]:
        g("config", k, v)
    (repo / "a.txt").write_text("changed")
    (repo / "b.dat").write_text("changed too")
    info = repo_summary(str(repo))
    assert info is not None and "a.txt" in info["changes"], info
    assert not marker.exists(), "a repo-configured program was executed"


@pytest.mark.parametrize("setup", ["partial_clone", "odd_name"])
def test_git_fails_closed_on_risky_repo_config(tmp_path: Path, setup: str) -> None:
    import subprocess
    from contextbridge.core.repo import repo_summary
    repo, marker = tmp_path / "r", tmp_path / "PWNED"
    repo.mkdir()
    g = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)  # noqa: E731
    g("init", "-q")
    hook = tmp_path / "hook.sh"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\ncat\n")
    hook.chmod(0o755)
    (repo / "a.txt").write_text("x")
    if setup == "partial_clone":
        g("config", "extensions.partialClone", "origin")
        g("config", "remote.origin.promisor", "true")
        g("config", "remote.origin.url", f"ext::{hook}")
    else:
        g("config", "filter.a=b c.clean", str(hook))
        (repo / ".gitattributes").write_text("*.txt filter=a=b\n")
    info = repo_summary(str(repo))
    assert info is not None and "skipped for safety" in info.get("warning", "")
    assert not marker.exists()


def test_old_git_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from contextbridge.core import repo as repo_mod
    monkeypatch.setattr(repo_mod, "git_version", lambda: (2, 30))
    info = repo_mod.repo_summary(str(tmp_path))
    assert info is not None and "older than 2.31" in info["error"]
