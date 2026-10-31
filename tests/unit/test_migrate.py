from __future__ import annotations

import sqlite3

import pytest

from contextbridge.core.app import App
from contextbridge.core.db import migrate as mig
from contextbridge.core.db.migrate import Migration


EXPECTED_TABLES = {
    "schema_migrations", "projects", "global_memory", "sessions", "entries", "entry_history",
    "conflicts", "live_state", "live_state_history", "tasks", "task_history", "checkpoints",
    "snapshots", "raw_archive", "staging", "file_refs", "telemetry", "handoff_runs",
    "entries_fts", "archive_fts",
}


def tables(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_fresh_schema(app: App) -> None:
    assert EXPECTED_TABLES <= tables(app.conn)
    assert mig.pending(app.conn) == []
    assert mig.verify_checksums(app.conn) == []


def test_migrate_is_idempotent(app: App) -> None:
    assert mig.migrate(app.conn, app.paths) == []


def test_split_sql_keeps_trigger_bodies_whole() -> None:
    sql = "-- c\nCREATE TABLE a(x);\nCREATE TRIGGER t AFTER INSERT ON a BEGIN\n  SELECT 1;\n  SELECT 2;\nEND;\n"
    stmts = mig._split_sql(sql)
    assert len(stmts) == 2 and stmts[1].endswith("END;")


def test_fts_triggers_index_entries(app: App) -> None:
    c = app.conn
    c.execute("INSERT INTO projects(slug,name,created_at,updated_at) VALUES ('p1','P','t','t')")
    c.execute(
        "INSERT INTO entries(project_id,kind,topic_key,title,body,status,provenance,confidence,importance,"
        "norm_hash,created_at,updated_at) VALUES (1,'fact','db.engine','Uses SQLite','WAL mode',"
        "'active','user_stated','confirmed','important','h','t','t')"
    )
    hits = c.execute("SELECT rowid FROM entries_fts WHERE entries_fts MATCH 'sqlite'").fetchall()
    assert [h[0] for h in hits] == [1]


def test_schema_enforces_provenance_rule(app: App) -> None:
    c = app.conn
    c.execute("INSERT INTO projects(slug,name,created_at,updated_at) VALUES ('p1','P','t','t')")
    with pytest.raises(sqlite3.IntegrityError):
        c.execute(
            "INSERT INTO entries(project_id,kind,topic_key,title,rationale,status,provenance,confidence,"
            "importance,norm_hash,created_at,updated_at) VALUES (1,'decision','x','t','r','active',"
            "'ai_suggested','assumed','important','h','t','t')"
        )


def test_decision_requires_rationale(app: App) -> None:
    c = app.conn
    c.execute("INSERT INTO projects(slug,name,created_at,updated_at) VALUES ('p1','P','t','t')")
    with pytest.raises(sqlite3.IntegrityError):
        c.execute(
            "INSERT INTO entries(project_id,kind,topic_key,title,status,provenance,confidence,"
            "importance,norm_hash,created_at,updated_at) VALUES (1,'decision','x','t','active',"
            "'user_stated','confirmed','important','h','t','t')"
        )


def _with_extra(monkeypatch: pytest.MonkeyPatch, extra: Migration) -> None:
    base = mig.available_migrations()
    monkeypatch.setattr(mig, "available_migrations", lambda: [*base, extra])


def test_new_migration_backs_up_first(app: App, monkeypatch: pytest.MonkeyPatch) -> None:
    app.conn.execute("INSERT INTO projects(slug,name,created_at,updated_at) VALUES ('keep','Keep','t','t')")
    n = len(mig.available_migrations()) + 1  # the next free version, whatever exists today
    _with_extra(monkeypatch, Migration(n, f"{n:04d}_test.sql", "CREATE TABLE extra(x);"))
    assert mig.migrate(app.conn, app.paths) == [n]
    backups = list(app.paths.backups.glob(f"pre-migrate-{n:04d}-*.db"))
    assert len(backups) == 1
    b = sqlite3.connect(backups[0])
    assert b.execute("SELECT slug FROM projects").fetchall() == [("keep",)]
    assert "extra" not in {r[0] for r in b.execute("SELECT name FROM sqlite_master")}
    b.close()


def test_failed_migration_rolls_back(app: App, monkeypatch: pytest.MonkeyPatch) -> None:
    n = len(mig.available_migrations()) + 1
    _with_extra(monkeypatch, Migration(n, f"{n:04d}_bad.sql", "CREATE TABLE half(x);\nNOT VALID SQL;"))
    with pytest.raises(sqlite3.OperationalError):
        mig.migrate(app.conn, app.paths)
    assert "half" not in tables(app.conn)
    assert n not in mig.applied_versions(app.conn)
    assert list(app.paths.backups.glob(f"pre-migrate-{n:04d}-*.db"))  # backup still there to restore from


def test_checksum_drift_detected(app: App) -> None:
    app.conn.execute("UPDATE schema_migrations SET checksum='tampered' WHERE version=1")
    assert mig.verify_checksums(app.conn) == ["migration 0001_initial.sql changed after it was applied"]


def test_prune_backups_keeps_newest(app: App) -> None:
    import os
    import time

    for i in range(5):
        p = app.paths.backups / f"b{i}.db"
        p.write_bytes(b"x")
        os.utime(p, (time.time() + i, time.time() + i))
    removed = mig.prune_backups(app.paths, keep=2)
    assert sorted(p.name for p in removed) == ["b0.db", "b1.db", "b2.db"]
