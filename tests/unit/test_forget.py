"""M9: forget / delete / purge (spec §7.1 #12, §9)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from compression import zstd

from contextbridge.core import knowledge as kn
from contextbridge.core import projects as proj
from contextbridge.core.app import App
from contextbridge.core.checkpoints import CheckpointIn, checkpoint
from contextbridge.core.db.migrate import backup_db
from contextbridge.core.errors import CBError
from contextbridge.core.forget import forget, purge, purge_entry
from contextbridge.core.handoff import prepare_handoff
from contextbridge.core.importer.staging import commit, stage
from contextbridge.core.knowledge import EntryIn
from contextbridge.core.snapshots import restore
from contextbridge.core.transfer import import_full

MARK = "ZEBRA-QUOKKA-7731"  # unique text we can grep for in raw files


def _project(app: App, slug: str = "pp", name: str = "My Project") -> int:
    proj.create_project(app.conn, name=name, objective="o", slug=slug)
    return proj.get_project_id(app.conn, slug)


def _on_disk(app: App) -> bool:
    """Is MARK anywhere: DB file, WAL, backups (raw bytes AND their decompressed snapshots), recovery
    archives (unzipped, with their base64 snapshot payloads decompressed)?"""
    import base64
    import sqlite3
    import zipfile
    app.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    dbs = [app.paths.db, *app.paths.backups.rglob("*.db")]
    for f in [*dbs, Path(str(app.paths.db) + "-wal")]:
        if f.exists() and MARK.encode() in f.read_bytes():
            return True
    for db in dbs:
        c = sqlite3.connect(db)
        try:
            for (payload,) in c.execute("SELECT payload FROM snapshots"):
                if MARK.encode() in zstd.decompress(payload):
                    return True
        finally:
            c.close()
    for z in app.paths.backups.rglob("*.zip"):
        with zipfile.ZipFile(z) as zf:
            for n in zf.namelist():
                data = zf.read(n)
                if MARK.encode() in data:
                    return True
                if n.startswith("projects/"):
                    for snap in json.loads(data)["tables"].get("snapshots", []):
                        if MARK.encode() in zstd.decompress(base64.b64decode(snap["payload"]["$b64"])):
                            return True
    return False


def test_forget_entry_needs_confirm_and_is_undoable(app: App) -> None:
    pid = _project(app)
    eid = kn.remember(app.conn, pid, [EntryIn(kind="fact", topic_key="a.b", title=f"fact {MARK}",
                                              provenance="user_stated")]).results[0].id
    assert eid is not None
    with pytest.raises(CBError) as e:
        forget(app.conn, app.paths, pid, "entry", eid, confirm=False)
    assert e.value.code == "confirm_required"
    out = forget(app.conn, app.paths, pid, "entry", eid, confirm=True)
    assert not kn.search(app.conn, pid, MARK)["hits"]
    restore(app.conn, pid, out["snapshot_id"])
    assert kn.get_entry(app.conn, pid, eid)["title"] == f"fact {MARK}"


def test_forget_session_removes_checkpoints_and_archives_keeps_entries(app: App, tmp_path: Path) -> None:
    pid = _project(app)
    kn.remember(app.conn, pid, [EntryIn(kind="fact", topic_key="a.b", title="kept", provenance="user_stated")],
                session_label="s1")
    checkpoint(app.conn, pid, CheckpointIn(summary="s", where_stopped="w", next_action="n", session_label="s1"))
    sid = app.conn.execute("SELECT id FROM sessions WHERE label = 's1'").fetchone()[0]
    t = tmp_path / "t.jsonl"
    t.write_text(json.dumps({"type": "user", "sessionId": "x", "message": {"role": "user", "content": MARK}}))
    s = stage(app.conn, app.paths, app.config, pid, "transcript_claude_code", path=str(t), restrict=False)
    commit(app.conn, pid, s["staging_id"], select=[])
    app.conn.execute("UPDATE raw_archive SET session_id = ?", (sid,))
    out = forget(app.conn, app.paths, pid, "session", sid, confirm=True)
    assert out["deleted"] == {"session": sid, "checkpoints": 1, "archived_transcripts": 1}
    assert app.conn.execute("SELECT count(*) FROM entries").fetchone()[0] == 1
    assert app.conn.execute("SELECT session_id FROM entries").fetchone()[0] is None
    assert not kn.search(app.conn, pid, MARK, include_archive=True)["hits"], "archive FTS row removed too"


def test_forget_project_needs_exact_name_and_keeps_recovery(app: App) -> None:
    pid = _project(app, name="My Project")
    other = _project(app, "keep", "Keep Me")
    kn.remember(app.conn, pid, [EntryIn(kind="fact", topic_key="a.b", title=MARK, provenance="user_stated")])
    kn.remember(app.conn, other, [EntryIn(kind="fact", topic_key="a.b", title="other", provenance="user_stated")])
    for wrong in (None, "my project", "My Project "[:-2]):
        with pytest.raises(CBError):
            forget(app.conn, app.paths, pid, "project", None, confirm=True, confirm_project_name=wrong)
    assert app.conn.execute("SELECT count(*) FROM projects").fetchone()[0] == 2
    out = forget(app.conn, app.paths, pid, "project", None, confirm=True, confirm_project_name="My Project")
    assert [r[0] for r in app.conn.execute("SELECT slug FROM projects")] == ["keep"]
    assert app.conn.execute("SELECT count(*) FROM entries").fetchone()[0] == 1
    import_full(app.conn, app.paths, out["recovery_archive"])
    assert kn.search(app.conn, proj.get_project_id(app.conn, "pp"), MARK)["hits"], "recoverable from the archive"


def test_purge_project_leaves_no_trace_on_disk(app: App) -> None:
    pid = _project(app)
    kn.remember(app.conn, pid, [EntryIn(kind="fact", topic_key="a.b", title=MARK, body=MARK * 5,
                                        provenance="user_stated")])
    checkpoint(app.conn, pid, CheckpointIn(summary=MARK, where_stopped="w", next_action="n"))
    prepare_handoff(app.conn, app.paths, app.config, pid)
    backup_db(app.conn, app.paths, "manual")
    forget(app.conn, app.paths, pid, "project", None, confirm=True, confirm_project_name="My Project")
    assert list((app.paths.backups / "deleted-projects").glob("*.zip")), "forget keeps a recovery archive"
    out = purge(app.conn, app.paths, "pp")
    assert out["exports_removed"] and out["recovery_archives"] == 1 and out["backups_cleaned"] >= 1
    assert not _on_disk(app), "purged text must not survive in the DB, WAL, snapshots or backups"
    assert not (app.paths.exports / "pp").exists()
    assert app.conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_purge_entry_scrubs_snapshots_and_backups(app: App) -> None:
    pid = _project(app)
    eid = kn.remember(app.conn, pid, [EntryIn(kind="fact", topic_key="a.b", title=f"secret plan {MARK}",
                                              provenance="user_stated"),
                                      EntryIn(kind="fact", topic_key="c.d", title="stays",
                                              provenance="user_stated")]).results[0].id
    assert eid is not None
    checkpoint(app.conn, pid, CheckpointIn(summary="s", where_stopped="w", next_action="n"))  # snapshot has it
    backup_db(app.conn, app.paths, "manual")          # a backup whose SNAPSHOTS also hold it (compressed)
    from contextbridge.core.transfer import export_full
    (app.paths.backups / "deleted-projects").mkdir(parents=True, exist_ok=True)
    export_full(app.conn, app.paths, ["pp"], app.paths.backups / "deleted-projects")  # a recovery archive
    assert _on_disk(app)
    out = purge_entry(app.conn, app.paths, pid, eid)
    assert out["snapshots_rewritten"] >= 1 and out["backups_cleaned"] >= 1
    assert out["recovery_archives_rewritten"] == 1
    assert not _on_disk(app)
    assert app.conn.execute("SELECT title FROM entries").fetchall()[0][0] == "stays"
    from contextbridge.core.snapshots import list_snapshots, load_snapshot
    for s in list_snapshots(app.conn, pid):
        load_snapshot(app.conn, pid, s["id"])  # hashes were recomputed: still verifiable
    z = next((app.paths.backups / "deleted-projects").glob("*.zip"))
    from contextbridge.core.transfer import read_archive
    read_archive(z)  # member hashes in the rewritten archive's manifest still verify


def test_cross_project_ids_are_refused(app: App) -> None:
    a, b = _project(app, "aa", "A"), _project(app, "bb", "B")
    eid = kn.remember(app.conn, b, [EntryIn(kind="fact", topic_key="x", title="b", provenance="user_stated")]).results[0].id
    with pytest.raises(CBError) as e:
        forget(app.conn, app.paths, a, "entry", eid, confirm=True)
    assert e.value.code == "not_found"
    assert app.conn.execute("SELECT count(*) FROM entries").fetchone()[0] == 1
