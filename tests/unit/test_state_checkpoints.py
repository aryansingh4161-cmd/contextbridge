from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

import pytest

from contextbridge.core import consistency
from contextbridge.core import knowledge as kn
from contextbridge.core import projects as proj
from contextbridge.core import snapshots as snap
from contextbridge.core import state as st
from contextbridge.core.app import App
from contextbridge.core.backup import maybe_scheduled_backup, restore_backup
from contextbridge.core.checkpoints import CheckpointIn, checkpoint, latest_checkpoint
from contextbridge.core.errors import CBError
from contextbridge.core.knowledge import EntryIn
from contextbridge.core.state import SectionIn, TaskIn
from contextbridge.core.status import checkpoint_status


@pytest.fixture
def pid(app: App) -> int:
    proj.create_project(app.conn, name="SIH", objective="Verify documents", slug="sih")
    return proj.get_project_id(app.conn, "sih")


def d(title: str, **kw: object) -> EntryIn:
    a: dict[str, object] = {"kind": "decision", "topic_key": "backend.framework", "title": title,
                            "rationale": "r", "provenance": "user_stated"}
    a.update(kw)
    return EntryIn(**a)  # type: ignore[arg-type]


def entries_dump(conn: sqlite3.Connection) -> list[tuple[object, ...]]:
    return [tuple(r) for r in conn.execute("SELECT * FROM entries ORDER BY id")]


def cp(**kw: object) -> CheckpointIn:
    a: dict[str, object] = {"summary": "did things", "where_stopped": "auth.py line 40", "next_action": "write tests"}
    a.update(kw)
    return CheckpointIn(**a)  # type: ignore[arg-type]


# ---------------------------------------------------------------- Live State

def test_state_update_never_touches_knowledge(app: App, pid: int) -> None:
    """Acceptance Test 14 (part 1)."""
    kn.remember(app.conn, pid, [d("Use FastAPI")])
    before = entries_dump(app.conn)
    kv = app.conn.execute("SELECT knowledge_version FROM projects").fetchone()[0]
    st.update_state(app.conn, pid, {"stack": SectionIn("FastAPI + Postgres"), "broken": SectionIn("login")},
                    [TaskIn(title="Add tests")])
    assert entries_dump(app.conn) == before
    assert app.conn.execute("SELECT knowledge_version FROM projects").fetchone()[0] == kv


def test_section_versions_and_history(app: App, pid: int) -> None:
    r1 = st.update_state(app.conn, pid, {"stack": SectionIn("Flask")})
    r2 = st.update_state(app.conn, pid, {"stack": SectionIn("FastAPI")})
    r3 = st.update_state(app.conn, pid, {"stack": SectionIn("FastAPI")})
    assert (r1.state_version, r2.state_version, r3.state_version) == (1, 2, 2)
    assert r3.unchanged_sections == ["stack"]
    hist = [r[0] for r in app.conn.execute("SELECT content FROM live_state_history ORDER BY id")]
    assert hist == ["Flask", "FastAPI"]
    assert st.get_state(app.conn, pid)["sections"]["stack"]["version"] == 2


def test_unknown_section_and_bad_refs(app: App, pid: int) -> None:
    with pytest.raises(CBError, match="unknown section"):
        st.update_state(app.conn, pid, {"vibes": SectionIn("x")})
    with pytest.raises(CBError, match="not an entry in this project"):
        st.update_state(app.conn, pid, {"stack": SectionIn("x", refs=[999])})


def test_tasks_create_update_and_validate(app: App, pid: int) -> None:
    r = st.update_state(app.conn, pid, tasks=[TaskIn(title="A"), TaskIn(title="B", status="blocked")])
    a, b = (t["id"] for t in r.task_results)
    r = st.update_state(app.conn, pid, tasks=[
        TaskIn(id=a, status="done"), TaskIn(id=b, depends_on=[a]), TaskIn(id=b, depends_on=[b]),
        TaskIn(title="C", status="maybe"), TaskIn(id=999, status="done"), TaskIn(status="todo")])
    statuses = [t["status"] for t in r.task_results]
    assert statuses == ["updated", "updated", "rejected", "rejected", "rejected", "rejected"]
    tasks = {t["id"]: t for t in st.get_state(app.conn, pid)["tasks"]}
    assert tasks[a]["status"] == "done" and tasks[b]["depends_on"] == [a]
    assert app.conn.execute("SELECT count(*) FROM task_history").fetchone()[0] == 4


def test_state_secrets_redacted(app: App, pid: int) -> None:
    st.update_state(app.conn, pid, {"environment": SectionIn("export DB_PASSWORD=hunter2hunter2")})
    assert "hunter2" not in st.get_state(app.conn, pid)["sections"]["environment"]["content"]


def test_consistency_validator(app: App, pid: int) -> None:
    """Acceptance Test 14 (part 3): a deliberately inconsistent state is flagged."""
    a = kn.remember(app.conn, pid, [d("Use Flask")]).results[0]
    st.update_state(app.conn, pid, {"stack": SectionIn("Python 3.14, Flask, Postgres", refs=[a.id or 0])})
    assert consistency.check(app.conn, pid) == []
    b = kn.remember(app.conn, pid, [d("Migrate to FastAPI", supersedes_id=a.id)]).results[0]
    codes = {w["code"] for w in consistency.check(app.conn, pid)}
    assert codes == {"stale_ref", "inconsistent_state"}
    st.update_state(app.conn, pid, {"stack": SectionIn("Python 3.14, FastAPI, Postgres", refs=[b.id or 0])})
    assert consistency.check(app.conn, pid) == []


# ---------------------------------------------------------------- checkpoints & sessions

def test_checkpoint_saves_everything_atomically(app: App, pid: int) -> None:
    out = checkpoint(app.conn, pid, cp(session_label="s1", client="claude-code", account_label="work",
                                       items=[d("Use FastAPI")], sections={"stack": SectionIn("FastAPI")},
                                       tasks=[TaskIn(title="Write tests")]))
    assert out["item_results"]["results"][0]["status"] == "saved"
    assert out["state_results"]["updated_sections"] == ["stack"]
    last = latest_checkpoint(app.conn, pid)
    assert last and last["where_stopped"] == "auth.py line 40" and last["account_label"] == "work"
    sess = app.conn.execute("SELECT capture, next_action FROM sessions").fetchone()
    assert tuple(sess) == ("checkpoints", "write tests")
    assert out["snapshots"]["knowledge"]["new"] and out["snapshots"]["state"]["new"]


def test_checkpoint_rolls_back_on_failure(app: App, pid: int) -> None:
    with pytest.raises(CBError):
        checkpoint(app.conn, pid, cp(items=[d("Use FastAPI")], sections={"nope": SectionIn("x")}))
    for table in ("entries", "checkpoints", "sessions", "snapshots"):
        assert app.conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0, table


def test_checkpoint_requires_where_and_next(app: App, pid: int) -> None:
    with pytest.raises(CBError, match="where_stopped is required"):
        checkpoint(app.conn, pid, cp(where_stopped="  "))


def test_checkpoint_status_resets_after_checkpoint(app: App, pid: int) -> None:
    kn.remember(app.conn, pid, [d("Use FastAPI")])
    st.update_state(app.conn, pid, {"stack": SectionIn("FastAPI")})
    assert checkpoint_status(app.conn, pid)["writes_since_checkpoint"] == 2
    checkpoint(app.conn, pid, cp())
    s = checkpoint_status(app.conn, pid)
    assert s["writes_since_checkpoint"] == 0 and s["minutes_since_checkpoint"] == 0 and s["recommendation"] == "none"


def test_sessions_without_checkpoint_stay_uncaptured(app: App, pid: int) -> None:
    """Basis for acceptance Test 16: a session with no checkpoint is a gap, not assumed captured."""
    kn.remember(app.conn, pid, [d("Use FastAPI")], session_label="s-quiet")
    checkpoint(app.conn, pid, cp(session_label="s-saved"))
    rows = dict(app.conn.execute("SELECT label, capture FROM sessions").fetchall())
    assert rows == {"s-quiet": "none", "s-saved": "checkpoints"}


# ---------------------------------------------------------------- snapshots

def test_snapshot_dedupe_list_and_diff(app: App, pid: int) -> None:
    a = kn.remember(app.conn, pid, [d("Use Flask")]).results[0]
    s1, new1 = snap.create_snapshot(app.conn, pid, "knowledge")
    s1b, new1b = snap.create_snapshot(app.conn, pid, "knowledge")
    assert new1 and not new1b and s1 == s1b
    kn.remember(app.conn, pid, [d("Migrate to FastAPI", supersedes_id=a.id)])
    s2, _ = snap.create_snapshot(app.conn, pid, "knowledge")
    out = snap.diff(app.conn, pid, s1, s2)
    assert out["entries"]["added"] == [2]
    changed = out["entries"]["changed"][0]
    assert changed["id"] == a.id and changed["fields"]["status"] == ["active", "superseded"]
    assert [r["id"] for r in snap.list_snapshots(app.conn, pid)] == [s2, s1]


def test_restore_knowledge_is_non_destructive_and_reversible(app: App, pid: int) -> None:
    a = kn.remember(app.conn, pid, [d("Use Flask")]).results[0]
    s1, _ = snap.create_snapshot(app.conn, pid, "knowledge")
    kn.remember(app.conn, pid, [d("Migrate to FastAPI", supersedes_id=a.id),
                                EntryIn(kind="lesson", topic_key="x", title="later lesson", provenance="verified")])
    out = snap.restore(app.conn, pid, s1)
    statuses = dict(app.conn.execute("SELECT title, status FROM entries").fetchall())
    assert statuses == {"Use Flask": "active", "Migrate to FastAPI": "outdated", "later lesson": "outdated"}
    assert kn.search(app.conn, pid, "flask")["hits"]           # FTS follows the restored rows
    snap.restore(app.conn, pid, out["pre_restore_snapshot"])   # undo
    statuses = dict(app.conn.execute("SELECT title, status FROM entries").fetchall())
    assert statuses == {"Use Flask": "superseded", "Migrate to FastAPI": "active", "later lesson": "active"}


def test_restore_state(app: App, pid: int) -> None:
    st.update_state(app.conn, pid, {"stack": SectionIn("Flask")}, [TaskIn(title="old task")])
    s1, _ = snap.create_snapshot(app.conn, pid, "state")
    st.update_state(app.conn, pid, {"stack": SectionIn("FastAPI"), "broken": SectionIn("login")},
                    [TaskIn(title="new task")])
    snap.restore(app.conn, pid, s1)
    state = st.get_state(app.conn, pid)
    assert {k: v["content"] for k, v in state["sections"].items()} == {"stack": "Flask"}
    assert [t["title"] for t in state["tasks"]] == ["old task"]


def test_corrupt_latest_snapshot_falls_back_to_earlier(app: App, pid: int) -> None:
    """Acceptance Test 7 (snapshots)."""
    kn.remember(app.conn, pid, [d("Use Flask")])
    s1, _ = snap.create_snapshot(app.conn, pid, "knowledge")
    kn.remember(app.conn, pid, [EntryIn(kind="fact", topic_key="y", title="later", provenance="verified")])
    s2, _ = snap.create_snapshot(app.conn, pid, "knowledge")
    app.conn.execute("UPDATE snapshots SET payload = x'00ff00' WHERE id = ?", (s2,))
    with pytest.raises(CBError, match="corrupt"):
        snap.restore(app.conn, pid, s2)
    snap.restore(app.conn, pid, s1)
    assert dict(app.conn.execute("SELECT title, status FROM entries").fetchall())["later"] == "outdated"


def test_snapshot_isolation(app: App, pid: int) -> None:
    s1, _ = snap.create_snapshot(app.conn, pid, "knowledge")
    proj.create_project(app.conn, name="Other", objective="o", slug="other")
    other = proj.get_project_id(app.conn, "other")
    with pytest.raises(CBError, match="not found"):
        snap.load_snapshot(app.conn, other, s1)


def test_prune_keeps_recent_all_and_old_one_per_day(app: App, pid: int) -> None:
    now = datetime.now().astimezone()
    stamps = [now - timedelta(days=40, hours=h) for h in (1, 2, 3)] + \
             [now - timedelta(days=35)] + [now - timedelta(hours=h) for h in (1, 2)]
    for i, ts in enumerate(sorted(stamps)):
        app.conn.execute("INSERT INTO snapshots(project_id, component, version, content_hash, payload, created_at)"
                         " VALUES (?, 'knowledge', ?, ?, x'00', ?)", (pid, i, f"h{i}", ts.isoformat()))
    removed = snap.prune_snapshots(app.conn, pid, keep_all_days=30)
    assert removed == 2  # three on day -40 → one; day -35 kept; both recent kept
    assert app.conn.execute("SELECT count(*) FROM snapshots").fetchone()[0] == 4


# ---------------------------------------------------------------- whole-DB backups

def test_scheduled_backup_and_restore(app: App, pid: int) -> None:
    """Acceptance Test 7 (DB backup restores)."""
    kn.remember(app.conn, pid, [d("Use Flask")])
    first = maybe_scheduled_backup(app.conn, app.paths, interval_hours=24, keep=10)
    assert first is not None
    assert maybe_scheduled_backup(app.conn, app.paths, interval_hours=24, keep=10) is None  # not due yet
    app.conn.execute("DELETE FROM entries")  # disaster
    out = restore_backup(app.conn, app.paths, first.name, keep=10)
    assert [r[0] for r in app.conn.execute("SELECT title FROM entries")] == ["Use Flask"]
    assert (app.paths.backups / out["safety_backup"]).exists()
    with pytest.raises(CBError, match="not found"):
        restore_backup(app.conn, app.paths, "../contextbridge.db", keep=10)
