"""M8: budget monitor — acceptance tests 17–22 (and 19, 20, 23 at the core level)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from contextbridge.adapters.claude_code import hooks
from contextbridge.core import projects as proj
from contextbridge.core.app import App
from contextbridge.core.budget import assess, budget_status, latest, parse_statusline, record
from contextbridge.core.checkpoints import CheckpointIn, checkpoint
from contextbridge.core.emergency import emergency
from contextbridge.core.importer.staging import commit, stage
from contextbridge.core.repo import link
from contextbridge.core.status import checkpoint_status

SID = "sess-abc123"


def sl_json(ctx: float | None = None, five: float | None = None, seven: float | None = None,
            cwd: str = "/nowhere", window: int = 200_000, sid: str = SID) -> str:
    obj: dict[str, object] = {"session_id": sid, "workspace": {"current_dir": cwd},
                              "context_window": {"used_percentage": ctx, "context_window_size": window}}
    rl: dict[str, object] = {}
    future = int(time.time()) + 3600
    if five is not None:
        rl["five_hour"] = {"used_percentage": five, "resets_at": future}
    if seven is not None:
        rl["seven_day"] = {"used_percentage": seven, "resets_at": future}
    if rl:
        obj["rate_limits"] = rl
    return json.dumps(obj)


@pytest.fixture
def linked(app: App, tmp_path: Path) -> tuple[int, Path]:
    repo = tmp_path / "folio"
    repo.mkdir()
    proj.create_project(app.conn, name="Folio", objective="o", slug="folio")
    pid = proj.get_project_id(app.conn, "folio")
    link(app.conn, pid, str(repo))
    return pid, repo


def _age(app: App, seconds: int) -> None:
    ts = (datetime.now().astimezone() - timedelta(seconds=seconds)).isoformat(timespec="microseconds")
    app.conn.execute("UPDATE telemetry SET ts = ?", (ts,))


# ---------------------------------------------------------------- Test 17: telemetry

def test_statusline_records_and_renders(app: App, linked: tuple[int, Path]) -> None:
    pid, repo = linked
    home = str(app.paths.root)
    line = hooks.statusline(sl_json(72.4, 41, 63, cwd=str(repo)), home)
    assert line.startswith("ctx 72% · 5h 41% · 7d 63% · ckpt never · handoff ✓ · folio"), line
    r = latest(app.conn, session_key=SID)
    assert r and r["project_id"] == pid and r["context_used_pct"] == 72.4 and r["context_used_tokens"] == 144800
    # absent fields are "–", never 0; unlinked folders say so
    line = hooks.statusline(json.dumps({"session_id": "s2", "context_window": {"used_percentage": None}}), home)
    assert line == "ctx – · 5h – · 7d – · no linked project"
    assert latest(app.conn, session_key="s2")["context_used_pct"] is None  # type: ignore[index]
    assert hooks.statusline("garbage", home).startswith("ctx –")


def test_statusline_throttles_writes(app: App) -> None:
    for _ in range(5):
        record(app.conn, parse_statusline(json.loads(sl_json(50))))
    assert app.conn.execute("SELECT count(*) FROM telemetry").fetchone()[0] == 1
    record(app.conn, parse_statusline(json.loads(sl_json(52))))  # moved ≥ 1 point
    assert app.conn.execute("SELECT count(*) FROM telemetry").fetchone()[0] == 2


def test_unavailable_and_stale_are_honest(app: App, linked: tuple[int, Path]) -> None:
    pid, _ = linked
    s = budget_status(app.conn, pid, None)
    assert s["telemetry"] == "unavailable" and s["context"] == "unavailable" and s["five_hour"] == "unavailable"
    assert "Claude Desktop" in s["note"] and checkpoint_status(app.conn, pid)["telemetry"] == "unavailable"
    r = parse_statusline(json.loads(sl_json(40)))
    r.project_id = pid
    record(app.conn, r)
    s = budget_status(app.conn, pid, None)
    assert s["context"]["used_pct"] == 40 and s["five_hour"] == "unavailable", "5h never inferred from context"
    _age(app, 600)
    s = budget_status(app.conn, pid, None)
    assert s["stale"] is True and "stale" in s["note"]
    assert checkpoint_status(app.conn, pid)["telemetry"] == "unavailable", "stale readings don't drive nudges"


def test_expired_rate_limit_window_is_ignored(app: App) -> None:
    obj = json.loads(sl_json(10, five=99))
    obj["rate_limits"]["five_hour"]["resets_at"] = int(time.time()) - 10
    record(app.conn, parse_statusline(obj))
    a = assess(latest(app.conn, session_key=SID))
    assert a["level"] == 0 and a["budgets"]["five_hour"] is None


# ---------------------------------------------------------------- Test 18 + 21: thresholds and loop guard

def _stop(app: App, repo: Path, active: bool = False) -> str:
    return hooks.stop(json.dumps({"session_id": SID, "cwd": str(repo), "stop_hook_active": active}),
                      str(app.paths.root))


def _reading(app: App, pid: int, **kw: float) -> None:
    r = parse_statusline(json.loads(sl_json(**kw)))  # type: ignore[arg-type]
    r.project_id = pid
    app.conn.execute("DELETE FROM telemetry")
    record(app.conn, r)


def test_each_threshold_fires_exactly_once(app: App, linked: tuple[int, Path]) -> None:
    pid, repo = linked
    fired = []
    for pct in (50, 70, 75, 80, 81, 85, 86, 90, 92, 95, 99):
        _reading(app, pid, ctx=10, five=pct)
        for _ in range(3):  # repeated Stop events at the same level
            out = _stop(app, repo)
            if out:
                msg = json.loads(out)["hookSpecificOutput"]
                assert msg["hookEventName"] == "Stop"
                fired.append((pct, msg["additionalContext"]))
    assert [p for p, _ in fired] == [80, 85, 90, 95], fired
    assert "5-hour usage limit is at 80%" in fired[0][1] and "remember" in fired[0][1]
    assert "checkpoint" in fired[1][1] and "prepare_handoff" in fired[2][1] and "emergency" in fired[3][1]
    assert all('project "folio"' in m for _, m in fired)


def test_stop_hook_active_is_always_honoured(app: App, linked: tuple[int, Path]) -> None:
    pid, repo = linked
    _reading(app, pid, ctx=86)
    for _ in range(10):
        assert _stop(app, repo, active=True) == ""
    assert _stop(app, repo) != "", "the level is still pending until a normal Stop"
    assert _stop(app, repo) == ""


def test_checkpoint_after_crossing_satisfies_level(app: App, linked: tuple[int, Path]) -> None:
    pid, repo = linked
    _reading(app, pid, ctx=86)                     # the status line sees 85+ first …
    checkpoint(app.conn, pid, CheckpointIn(summary="s", where_stopped="w", next_action="n"))  # … model saves
    assert _stop(app, repo) == "", "already checkpointed after crossing: no request"
    _reading(app, pid, ctx=91)                     # 90 needs a HANDOFF, a checkpoint isn't enough
    out = _stop(app, repo)
    assert out and "prepare_handoff" in json.loads(out)["hookSpecificOutput"]["additionalContext"]
    assert _stop(app, repo) == ""


def test_unrelated_folder_writes_no_state(app: App, linked: tuple[int, Path]) -> None:
    pid, _ = linked
    _reading(app, pid, ctx=90)
    assert hooks.stop(json.dumps({"session_id": SID, "cwd": "/elsewhere"}), str(app.paths.root)) == ""
    assert not (app.paths.root / "telemetry" / "state" / f"{SID}.json").exists()


def test_unsafe_session_ids_and_stale_readings_do_nothing(app: App, linked: tuple[int, Path]) -> None:
    pid, repo = linked
    r = parse_statusline(json.loads(sl_json(ctx=90, sid="../../etc/x")))
    r.project_id = pid
    record(app.conn, r)
    assert hooks.stop(json.dumps({"session_id": "../../etc/x", "cwd": str(repo)}), str(app.paths.root)) == ""
    assert not list((app.paths.root / "telemetry").rglob("*x.json"))
    _reading(app, pid, ctx=90)
    _age(app, 3600)
    assert _stop(app, repo) == "", "stale readings never trigger requests"


def test_feasibility_acts_before_percentages(app: App, linked: tuple[int, Path]) -> None:
    pid, _ = linked
    r = parse_statusline(json.loads(sl_json(ctx=60, window=40_000)))  # 16k left < 4.5k+4k reserve… → act
    a = assess({"ts": r.ts, "context_used_pct": 85.0, "context_window_tokens": 40_000})
    assert a["feasible"] is False and a["recommended_mode"] == "quick"     # Test 19: compact handoff
    a = assess({"ts": r.ts, "context_used_pct": 65.0, "context_window_tokens": 20_000})  # 7k left < 5k + 3k
    assert a["level"] >= 85 and "feasibility" in str(a["budget"])
    a = assess({"ts": r.ts, "context_used_pct": 30.0, "context_window_tokens": 1_000_000})
    assert a["level"] == 0 and a["feasible"] is True
    assert a["est_handoff_cost"]["required"] <= 23_000, "reserve is capped: a 1M window doesn't mean a 100k handoff"
    a = assess({"ts": r.ts, "context_used_pct": 98.5, "context_window_tokens": 1_000_000})  # 15k left < 23k + 3k
    assert a["feasible"] is False and a["recommended_mode"] == "quick"


def test_checkpoint_status_uses_fresh_telemetry(app: App, linked: tuple[int, Path]) -> None:
    pid, _ = linked
    _reading(app, pid, ctx=91)
    cs = checkpoint_status(app.conn, pid)
    assert cs["telemetry"] == "available" and cs["recommendation"] == "prepare handoff" and cs["budget_level"] == 90


# ---------------------------------------------------------------- Test 22: PreCompact

def _transcript(tmp_path: Path, sid: str = "sess-abc123") -> Path:
    t = tmp_path / f"{sid}.jsonl"
    t.write_text("\n".join(json.dumps(r) for r in [
        {"type": "user", "sessionId": sid, "message": {"role": "user", "content": "decided on pointer events"}},
        {"type": "assistant", "sessionId": sid, "message": {"role": "assistant",
                                                             "content": [{"type": "text", "text": "ok, doing it"}]}}]))
    return t


def test_pre_compact_archives_transcript_for_the_linked_project(app: App, linked: tuple[int, Path],
                                                                tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pid, repo = linked
    claude_dir = tmp_path / "home" / ".claude" / "projects" / "x"
    claude_dir.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    t = _transcript(claude_dir)
    out = hooks.pre_compact(json.dumps({"session_id": SID, "transcript_path": str(t), "cwd": str(repo),
                                        "trigger": "auto"}), str(app.paths.root))
    assert out == "", "never blocks compaction"
    row = app.conn.execute("SELECT a.project_id, s.external_key, s.capture FROM raw_archive a "
                           "JOIN sessions s ON s.id = a.session_id").fetchone()
    assert tuple(row) == (pid, SID, "transcript")
    hooks.pre_compact(json.dumps({"session_id": SID, "transcript_path": str(t), "cwd": str(repo)}), str(app.paths.root))
    assert app.conn.execute("SELECT count(*) FROM raw_archive").fetchone()[0] == 1, "no duplicate on 2nd compaction"
    assert hooks.pre_compact(json.dumps({"transcript_path": str(t), "cwd": "/elsewhere"}), str(app.paths.root)) == ""


# ---------------------------------------------------------------- Tests 20 + 23: emergency

def test_emergency_handoff_is_labelled_and_archives(app: App, linked: tuple[int, Path], tmp_path: Path) -> None:
    pid, _ = linked
    checkpoint(app.conn, pid, CheckpointIn(summary="s", where_stopped="Lightbox.astro line 40",
                                           next_action="fix swipe"))
    t = _transcript(tmp_path)
    out = emergency(app.conn, app.paths, app.config, pid, transcript=str(t), reason="api error: rate_limit",
                    restrict=False)
    text = Path(out["handoff"]).read_text(encoding="utf-8")
    assert "EMERGENCY HANDOFF" in text and "WITHOUT an AI summary" in text and "rate_limit" in text
    assert "archived as archive #" in text and "fix swipe" in text and "emergency: true" in text
    manifest = json.loads((Path(out["directory"]) / "00_manifest.json").read_text())
    assert manifest["emergency"]["reason"] == "api error: rate_limit"
    # Test 23: a new conversation can find the archived transcript and the handoff
    from contextbridge.core import knowledge as kn
    assert kn.search(app.conn, pid, "pointer events", include_archive=True)["hits"]
    from contextbridge.core.context import get_context
    assert "fix swipe" in get_context(app.conn, app.paths, app.config, pid)["content"]


def test_emergency_only_if_unsaved_skips_when_nothing_changed(app: App, linked: tuple[int, Path]) -> None:
    pid, _ = linked
    checkpoint(app.conn, pid, CheckpointIn(summary="s", where_stopped="w", next_action="n"))
    out = emergency(app.conn, app.paths, app.config, pid, only_if_unsaved=True)
    assert out["handoff"] is None and "nothing unsaved" in out["skipped_handoff"]


def test_session_end_hook_returns_fast_and_spawns_detached(app: App, linked: tuple[int, Path], tmp_path: Path) -> None:
    pid, repo = linked
    from contextbridge.core import knowledge as kn
    from contextbridge.core.knowledge import EntryIn
    kn.remember(app.conn, pid, [EntryIn(kind="fact", topic_key="a.b", title="unsaved fact", provenance="user_stated")])
    t = _transcript(tmp_path)
    ctxb = Path(sys.executable).parent / "ctxb"
    env = {**os.environ, "CONTEXTBRIDGE_HOME": str(app.paths.root)}
    started = time.perf_counter()
    r = subprocess.run([str(ctxb), "hook", "session-end"], input=json.dumps(
        {"session_id": SID, "cwd": str(repo), "transcript_path": str(t), "reason": "prompt_input_exit"}),
        capture_output=True, text=True, env=env, timeout=30)
    elapsed = time.perf_counter() - started
    assert r.returncode == 0 and r.stdout == ""
    assert elapsed < 1.5, f"must return within SessionEnd's 1.5 s default budget (took {elapsed:.2f}s)"
    deadline = time.time() + 20
    while time.time() < deadline:  # the detached child finishes on its own
        if app.conn.execute("SELECT count(*) FROM handoff_runs WHERE mode LIKE 'emergency-%'").fetchone()[0]:
            break
        time.sleep(0.2)
    assert app.conn.execute("SELECT count(*) FROM handoff_runs WHERE mode LIKE 'emergency-%'").fetchone()[0] == 1
    assert app.conn.execute("SELECT count(*) FROM raw_archive").fetchone()[0] == 1


def test_hooks_cli_never_fail(app: App) -> None:
    ctxb = Path(sys.executable).parent / "ctxb"
    env = {**os.environ, "CONTEXTBRIDGE_HOME": "/dev/null/nope"}
    for args in (["hook", "stop"], ["hook", "pre-compact"], ["hook", "session-end"], ["hook", "stop-failure"],
                 ["statusline"]):
        r = subprocess.run([str(ctxb), *args], input="{}", capture_output=True, text=True, env=env, timeout=30)
        assert r.returncode == 0, (args, r.stderr)


def test_statusline_is_fast(app: App, linked: tuple[int, Path]) -> None:
    _, repo = linked
    ctxb = Path(sys.executable).parent / "ctxb"
    env = {**os.environ, "CONTEXTBRIDGE_HOME": str(app.paths.root)}
    times = []
    for i in range(5):
        t0 = time.perf_counter()
        r = subprocess.run([str(ctxb), "statusline"], input=sl_json(40 + i, cwd=str(repo)), capture_output=True,
                           text=True, env=env, timeout=30)
        times.append((time.perf_counter() - t0) * 1000)
        assert r.returncode == 0 and "folio" in r.stdout
    print(f"\nstatusline: best {min(times):.0f} ms of {[round(t) for t in times]}")
    assert min(times) < 1000
