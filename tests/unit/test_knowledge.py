from __future__ import annotations

import sqlite3

import pytest

from contextbridge.core import knowledge as kn
from contextbridge.core import projects as proj
from contextbridge.core.app import App
from contextbridge.core.errors import CBError
from contextbridge.core.knowledge import EntryIn
from contextbridge.core.status import checkpoint_status


@pytest.fixture
def pid(app: App) -> int:
    proj.create_project(app.conn, name="SIH", objective="Verify documents", slug="sih")
    return proj.get_project_id(app.conn, "sih")


def decision(title: str, topic: str = "backend.framework", **kw: object) -> EntryIn:
    args: dict[str, object] = {"kind": "decision", "topic_key": topic, "title": title,
                               "rationale": "because", "provenance": "user_stated"}
    args.update(kw)
    return EntryIn(**args)  # type: ignore[arg-type]


def row(conn: sqlite3.Connection, eid: int) -> sqlite3.Row:
    r: sqlite3.Row = conn.execute("SELECT * FROM entries WHERE id = ?", (eid,)).fetchone()
    return r


def test_save_writes_history_and_bumps_version(app: App, pid: int) -> None:
    res = kn.remember(app.conn, pid, [decision("Use Flask")])
    r = res.results[0]
    assert r.status == "saved" and r.id and res.knowledge_version == 1
    h = kn.entry_history(app.conn, pid, r.id)
    assert [x["change_type"] for x in h] == ["create"] and h[0]["snapshot"]["title"] == "Use Flask"


def test_topic_key_normalized_and_returned(app: App, pid: int) -> None:
    r = kn.remember(app.conn, pid, [decision("Use Flask", topic="Backend / Framework")]).results[0]
    assert r.topic_key == "backend_framework"


def test_near_duplicate_merges(app: App, pid: int) -> None:
    first = kn.remember(app.conn, pid, [decision("Use FastAPI for the backend", body="async, typed")]).results[0]
    again = kn.remember(app.conn, pid, [decision("Use FastAPI for the backend.", body="async, typed",
                                                 importance="critical")]).results[0]
    assert again.status == "merged" and again.id == first.id
    assert app.conn.execute("SELECT count(*) FROM entries").fetchone()[0] == 1
    e = row(app.conn, first.id or 0)
    assert e["version"] == 2 and e["importance"] == "critical"
    assert [h["change_type"] for h in kn.entry_history(app.conn, pid, first.id or 0)] == ["create", "merge"]


def test_conflict_on_singular_kind_is_recorded_not_chosen(app: App, pid: int) -> None:
    """Acceptance Test 5."""
    a = kn.remember(app.conn, pid, [decision("Use Flask")]).results[0]
    b = kn.remember(app.conn, pid, [decision("Use FastAPI")]).results[0]
    assert b.status == "conflict" and b.conflicts_with == a.id and b.conflict_id
    assert row(app.conn, a.id or 0)["status"] == "active"      # nothing silently chosen
    assert row(app.conn, b.id or 0)["status"] == "unverified"
    assert len(kn.list_conflicts(app.conn, pid)) == 1


def test_non_singular_kinds_coexist(app: App, pid: int) -> None:
    items = [EntryIn(kind="bug", topic_key="auth.login", title="Token not refreshed", provenance="user_stated"),
             EntryIn(kind="bug", topic_key="auth.login", title="CSRF header missing on POST", provenance="user_stated")]
    res = kn.remember(app.conn, pid, items)
    assert [r.status for r in res.results] == ["saved", "saved"]


def test_explicit_supersession(app: App, pid: int) -> None:
    a = kn.remember(app.conn, pid, [decision("Use Flask")]).results[0]
    b = kn.remember(app.conn, pid, [decision("Migrate to FastAPI", rationale="need async",
                                             supersedes_id=a.id)]).results[0]
    assert b.status == "saved"
    old, new = row(app.conn, a.id or 0), row(app.conn, b.id or 0)
    assert old["status"] == "superseded" and old["superseded_by_id"] == b.id
    assert new["status"] == "active" and new["supersedes_id"] == a.id
    assert kn.list_conflicts(app.conn, pid) == []


def test_supersession_wins_over_merge(app: App, pid: int) -> None:
    a = kn.remember(app.conn, pid, [decision("Use port 8000 for the API")]).results[0]
    b = kn.remember(app.conn, pid, [decision("Use port 8080 for the API", supersedes_id=a.id)]).results[0]
    assert b.status == "saved" and b.id != a.id


def test_cannot_supersede_twice(app: App, pid: int) -> None:
    a = kn.remember(app.conn, pid, [decision("Use Flask")]).results[0]
    kn.remember(app.conn, pid, [decision("Use FastAPI", supersedes_id=a.id)])
    r = kn.remember(app.conn, pid, [decision("Use Django", supersedes_id=a.id)]).results[0]
    assert r.status == "rejected" and "already superseded" in (r.reason or "")


def test_provenance_gate_and_confirmation(app: App, pid: int) -> None:
    """Acceptance Test 15: ai_suggested never appears as a confirmed active decision until confirmed."""
    r = kn.remember(app.conn, pid, [decision("Use Redis cache", topic="cache.engine",
                                             provenance="ai_suggested")]).results[0]
    assert r.status == "rejected" and "provenance_violation" in (r.reason or "")
    r = kn.remember(app.conn, pid, [decision("Use Redis cache", topic="cache.engine", status="unverified",
                                             provenance="ai_suggested")]).results[0]
    e = row(app.conn, r.id or 0)
    assert (e["status"], e["provenance"], e["confidence"]) == ("unverified", "ai_suggested", "assumed")
    c = kn.remember(app.conn, pid, [], confirm=[r.id or 0]).confirmations[0]
    assert c.status == "confirmed"
    e = row(app.conn, r.id or 0)
    assert (e["status"], e["provenance"], e["confidence"]) == ("active", "user_confirmed", "confirmed")
    assert kn.remember(app.conn, pid, [], confirm=[r.id or 0]).confirmations[0].status == "already_confirmed"


def test_confirm_into_conflict(app: App, pid: int) -> None:
    kn.remember(app.conn, pid, [decision("Use Postgres", topic="db.engine")])
    r = kn.remember(app.conn, pid, [decision("Use SQLite", topic="db.engine", status="unverified",
                                             provenance="ai_suggested")]).results[0]
    c = kn.remember(app.conn, pid, [], confirm=[r.id or 0]).confirmations[0]
    assert c.status == "conflict" and c.conflict_id
    assert row(app.conn, r.id or 0)["status"] == "unverified"


@pytest.mark.parametrize(("item", "reason"), [
    (EntryIn(kind="decision", topic_key="x", title="t", provenance="user_stated"), "rationale is required"),
    (EntryIn(kind="nope", topic_key="x", title="t", provenance="user_stated"), "kind 'nope'"),
    (EntryIn(kind="fact", topic_key="!!!", title="t", provenance="user_stated"), "topic_key"),
    (EntryIn(kind="fact", topic_key="x", title="  ", provenance="user_stated"), "title is required"),
    (EntryIn(kind="fact", topic_key="x", title="t", provenance="me"), "provenance"),
    (EntryIn(kind="fact", topic_key="x", title="t", provenance="user_stated", status="superseded"), "status"),
])
def test_validation_rejects_per_item(app: App, pid: int, item: EntryIn, reason: str) -> None:
    good = EntryIn(kind="fact", topic_key="ok", title="fine", provenance="user_stated")
    res = kn.remember(app.conn, pid, [item, good])
    assert res.results[0].status == "rejected" and reason in (res.results[0].reason or "")
    assert res.results[1].status == "saved"  # one bad item doesn't sink the batch


def test_secrets_redacted_on_write(app: App, pid: int) -> None:
    key = "sk-ant-" + "api03-" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4"
    res = kn.remember(app.conn, pid, [EntryIn(kind="reference", topic_key="env.api", title="API key setup",
                                              body=f"export ANTHROPIC_API_KEY={key}", provenance="user_stated")])
    body = row(app.conn, res.results[0].id or 0)["body"]
    assert key not in body and "[REDACTED:" in body
    assert res.redactions and res.redactions[0]["field"] == "body" and "value" not in res.redactions[0]
    # nothing leaks through FTS or history either
    assert app.conn.execute("SELECT count(*) FROM entries_fts WHERE entries_fts MATCH '\"A1b2C3d4E5f6G7h8I9j0K1l2M3n4\"'").fetchone()[0] == 0
    assert key not in str(kn.entry_history(app.conn, pid, res.results[0].id or 0))


@pytest.mark.parametrize("resolution", ["a_supersedes_b", "b_supersedes_a", "both_valid", "merge"])
def test_resolve_conflict(app: App, pid: int, resolution: kn.Resolution) -> None:
    a = kn.remember(app.conn, pid, [decision("Use Flask")]).results[0]
    b = kn.remember(app.conn, pid, [decision("Use FastAPI")]).results[0]
    merged = decision("Use FastAPI; keep Flask admin app", rationale="both needed") if resolution == "merge" else None
    out = kn.resolve_conflict(app.conn, pid, b.conflict_id or 0, resolution, "user decided", merged)
    sa, sb = row(app.conn, a.id or 0)["status"], row(app.conn, b.id or 0)["status"]
    expected = {"a_supersedes_b": ("active", "superseded"), "b_supersedes_a": ("superseded", "active"),
                "both_valid": ("active", "active"), "merge": ("superseded", "superseded")}[resolution]
    assert (sa, sb) == expected
    if resolution == "merge":
        assert row(app.conn, out["active_ids"][0])["status"] == "active"
    assert kn.list_conflicts(app.conn, pid) == []
    with pytest.raises(CBError):
        kn.resolve_conflict(app.conn, pid, b.conflict_id or 0, resolution, "again")


def test_resolve_requires_note(app: App, pid: int) -> None:
    kn.remember(app.conn, pid, [decision("Use Flask")])
    b = kn.remember(app.conn, pid, [decision("Use FastAPI")]).results[0]
    with pytest.raises(CBError, match="note is required"):
        kn.resolve_conflict(app.conn, pid, b.conflict_id or 0, "a_supersedes_b", "  ")


def test_search(app: App, pid: int) -> None:
    a = kn.remember(app.conn, pid, [decision("Use Flask", body="simple WSGI")]).results[0]
    kn.remember(app.conn, pid, [decision("Migrate to FastAPI", body="async ASGI", supersedes_id=a.id),
                                EntryIn(kind="lesson", topic_key="db.locks", title="WAL avoids reader locks",
                                        provenance="verified")])
    hits = kn.search(app.conn, pid, "fastapi")["hits"]
    assert [h["title"] for h in hits] == ["Migrate to FastAPI"]
    assert kn.search(app.conn, pid, "flask")["hits"] == []                       # superseded hidden
    assert len(kn.search(app.conn, pid, "flask", include_superseded=True)["hits"]) == 1
    assert kn.search(app.conn, pid, "wal", kinds=["decision"])["hits"] == []
    out = kn.search(app.conn, pid, "fastapi locks")                               # no AND match → OR
    assert out["match_mode"] == "or" and len(out["hits"]) == 2
    assert kn.search(app.conn, pid, 'weird "quote AND OR NEAR(')["hits"] == []   # FTS syntax can't break it


def test_isolation_between_projects(app: App, pid: int) -> None:
    """Acceptance Test 6."""
    proj.create_project(app.conn, name="Portfolio", objective="o", slug="portfolio")
    other = proj.get_project_id(app.conn, "portfolio")
    a = kn.remember(app.conn, pid, [decision("Use FastAPI", body="secret sauce")]).results[0]
    kn.remember(app.conn, other, [decision("Use Next.js", topic="frontend.framework")])
    assert kn.search(app.conn, other, "fastapi sauce")["hits"] == []
    assert kn.remember(app.conn, other, [decision("Use Flask", supersedes_id=a.id)]).results[0].status == "rejected"
    assert kn.remember(app.conn, other, [], confirm=[a.id or 0]).confirmations[0].status == "rejected"
    with pytest.raises(CBError):
        kn.get_entry(app.conn, other, a.id or 0)
    # same topic in another project is not a conflict
    assert kn.remember(app.conn, other, [decision("Use Django")]).results[0].status == "saved"
    b = kn.remember(app.conn, pid, [decision("Use Flask")]).results[0]
    with pytest.raises(CBError, match="not found"):
        kn.resolve_conflict(app.conn, other, b.conflict_id or 0, "both_valid", "x")


def test_checkpoint_status_reports_loss_window(app: App, pid: int) -> None:
    s = checkpoint_status(app.conn, pid)
    assert s["writes_since_checkpoint"] == 0 and s["recommendation"] == "none"
    kn.remember(app.conn, pid, [decision("Use Flask")])
    s = checkpoint_status(app.conn, pid)
    assert s["writes_since_checkpoint"] == 1 and s["recommendation"] == "call checkpoint"
    assert s["unsaved_hint"] == "no checkpoint yet" and s["telemetry"] == "unavailable"


def test_batch_limits(app: App, pid: int) -> None:
    with pytest.raises(CBError):
        kn.remember(app.conn, pid, [])
    with pytest.raises(CBError, match="at most 50"):
        kn.remember(app.conn, pid, [EntryIn(kind="fact", topic_key=f"k{i}", title="t",
                                            provenance="user_stated") for i in range(51)])


def test_snippet_uses_matching_column(app: App, pid: int) -> None:
    kn.remember(app.conn, pid, [decision("Use FastAPI for the backend")])  # no body
    hit = kn.search(app.conn, pid, "fastapi")["hits"][0]
    assert "[FastAPI]" in hit["snippet"]


def test_conflict_reason_names_both_sides(app: App, pid: int) -> None:
    a = kn.remember(app.conn, pid, [decision("Use Flask")]).results[0]
    b = kn.remember(app.conn, pid, [decision("Use FastAPI")]).results[0]
    assert f"entry_a = {a.id} (existing), entry_b = {b.id} (this one)" in (b.reason or "")
