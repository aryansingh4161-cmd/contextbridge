"""Acceptance Test 24 (BLUEPRINT §0): abrupt end at an arbitrary point.

A real child process runs a working session and is SIGKILLed at random points (between writes, in
the middle of a handoff, at the very start). For every run:
  (a) every write acknowledged before the kill is recoverable;
  (b) the emergency handoff (no model, no hooks) states the last durable save and the loss window;
  (c) nothing unsaved is presented as saved: checkpoints are all-or-nothing, an interrupted package is
      never served as the latest handoff, and no file claims to be complete when it isn't.
The metric (share of acknowledged writes recovered) must be 100 %.
"""

from __future__ import annotations

import json
import os
import random
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from contextbridge.core import projects as proj
from contextbridge.core.app import open_app
from contextbridge.core.emergency import emergency
from contextbridge.core.handoff import latest_handoff

CHILD = Path(__file__).with_name("session_child.py")
STEPS = 30


def run_and_kill(home: Path, kill_after_acks: int | None, mid_handoff_delay: float | None,
                 slow_writes: bool = False) -> list[str]:
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[2] / "src")}
    if slow_writes:
        env["CB_TEST_SLOW_WRITES"] = "1"
    p = subprocess.Popen([sys.executable, str(CHILD), str(home), str(STEPS)], stdout=subprocess.PIPE, text=True,
                         env=env)
    acks: list[str] = []
    assert p.stdout is not None
    if kill_after_acks == 0:  # dies before it can write anything
        p.send_signal(signal.SIGKILL)
        p.wait(timeout=30)
        return acks
    for line in p.stdout:
        if line.startswith("START") and mid_handoff_delay is not None:
            time.sleep(mid_handoff_delay)
            p.send_signal(signal.SIGKILL)
            break
        if line.startswith("ACK"):
            acks.append(line.strip())
            if kill_after_acks is not None and len(acks) >= kill_after_acks:
                p.send_signal(signal.SIGKILL)
                break
    p.wait(timeout=30)
    return acks


def check_recovery(home: Path, acks: list[str]) -> tuple[int, int]:
    if not acks and not (home / "contextbridge.db").exists():
        return 0, 0
    app = open_app(home, log_to_stderr=False)
    try:
        c = app.conn
        assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        if not acks or c.execute("SELECT count(*) FROM projects").fetchone()[0] == 0:
            return 0, 0
        pid = proj.get_project_id(c, "kt")
        titles = {r[0] for r in c.execute("SELECT title FROM entries")}
        state = (c.execute("SELECT content FROM live_state WHERE section='in_progress'").fetchone() or [""])[0]
        cps = {r[0] for r in c.execute("SELECT where_stopped FROM checkpoints")}
        runs = c.execute("SELECT count(*) FROM handoff_runs").fetchone()[0]
        recovered = 0
        last_state = None
        for a in acks[1:]:
            _, n, kind, m = a.split()
            if kind == "remember":
                recovered += f"fact {m}" in titles
            elif kind == "state":
                last_state = m
                recovered += 1  # checked below: the newest acknowledged state (or a later one) is present
            elif kind == "checkpoint":
                recovered += f"stopped {m}" in cps and f"lesson {m}" in titles
            else:
                recovered += runs >= 1
        if last_state is not None:
            assert state.startswith("working on M") and state >= f"working on {last_state}"
        # (c) checkpoints are atomic: a lesson exists only together with its checkpoint, and vice versa
        for (t,) in c.execute("SELECT title FROM entries WHERE kind = 'lesson'"):
            assert f"stopped {t.split()[-1]}" in cps
        for w in cps:
            assert f"lesson {w.split()[-1]}" in titles
        # (c) a package interrupted mid-write is never served as "the latest handoff"
        h = latest_handoff(c, pid)
        if h is not None:
            snap_dir = c.execute("SELECT reason FROM snapshots WHERE component = 'handoff' ORDER BY id DESC LIMIT 1"
                                 ).fetchone()[0]
            assert (Path(snap_dir) / "00_manifest.json").exists(), "latest handoff points at a finished package"
            assert "Where we stopped" in h["primary"]
        complete = c.execute("SELECT count(*) FROM handoff_runs").fetchone()[0]
        dirs = list((app.paths.exports / "kt").glob("kt_*")) if (app.paths.exports / "kt").exists() else []
        finished = [d for d in dirs if (d / "00_manifest.json").exists()]
        assert len(finished) >= complete, "every recorded handoff run has a manifest-complete folder"
        # (b) emergency handoff (no model): last durable save + the loss window, and never "complete"
        out = emergency(c, app.paths, app.config, pid, reason="test 24: process killed")
        text = Path(out["handoff"]).read_text(encoding="utf-8")
        assert "EMERGENCY HANDOFF" in text and "WITHOUT an AI summary" in text
        last_cp = c.execute("SELECT max(created_at) FROM checkpoints").fetchone()[0]
        assert (last_cp or "none yet") in text, "states the last durable checkpoint"
        assert "NOT in this file" in text, "states the loss window"
        assert "complete: false" in text
        # nothing unsaved is presented as saved: every fact named in the handoff exists in the DB
        import re
        named = set(re.findall(r"fact (M\d{3})", text))
        stored = {t.split()[-1] for t in titles if t.startswith("fact ")}
        assert named <= stored
        return recovered, len(acks) - 1
    finally:
        app.close()


@pytest.mark.parametrize("seed", range(12))
def test_abrupt_end_between_writes(tmp_path: Path, seed: int) -> None:
    rnd = random.Random(seed)
    k = rnd.randint(1, STEPS)
    acks = run_and_kill(tmp_path / "home", kill_after_acks=k, mid_handoff_delay=None)
    got, want = check_recovery(tmp_path / "home", acks)
    assert got == want, f"seed {seed}: recovered {got}/{want} acknowledged writes"


def test_abrupt_end_mid_handoff(tmp_path: Path) -> None:
    """Handoff files are written with a pause between them, so kills land while a package is half-written."""
    interrupted = 0
    for i, delay in enumerate([0.0, 0.01, 0.04, 0.07, 0.1, 0.13, 0.2]):
        home = tmp_path / f"h{i}"
        acks = run_and_kill(home, kill_after_acks=None, mid_handoff_delay=delay, slow_writes=True)
        exp = home / "exports" / "kt"
        interrupted += sum(1 for d in exp.glob("kt_*") if not (d / "00_manifest.json").exists()) if exp.exists() else 0
        got, want = check_recovery(home, acks)
        assert got == want, f"delay {delay}: recovered {got}/{want}"
    assert interrupted >= 2, f"the test must actually interrupt handoffs mid-write (did {interrupted})"


def test_abrupt_end_before_anything(tmp_path: Path) -> None:
    acks = run_and_kill(tmp_path / "home", kill_after_acks=0, mid_handoff_delay=None)
    assert acks == []
    check_recovery(tmp_path / "home", acks)  # whatever exists is a consistent database


def test_after_stop_failure_hook(tmp_path: Path) -> None:
    """Killed, then Claude Code reports an API error: the StopFailure hook writes an emergency handoff."""
    home = tmp_path / "home"
    acks = run_and_kill(home, kill_after_acks=10, mid_handoff_delay=None)
    repo = tmp_path / "repo"
    repo.mkdir()
    ctxb = Path(sys.executable).parent / "ctxb"
    env = {**os.environ, "CONTEXTBRIDGE_HOME": str(home)}
    subprocess.run([str(ctxb), "link", "kt", str(repo)], check=True, env=env, capture_output=True)
    r = subprocess.run([str(ctxb), "hook", "stop-failure"], input=json.dumps(
        {"session_id": "s", "cwd": str(repo), "error": "rate_limit"}), capture_output=True, text=True, env=env)
    assert r.returncode == 0
    deadline = time.time() + 20
    app = open_app(home, log_to_stderr=False)
    try:
        while time.time() < deadline and not app.conn.execute(
                "SELECT count(*) FROM handoff_runs WHERE mode LIKE 'emergency-%'").fetchone()[0]:
            time.sleep(0.2)
        assert app.conn.execute("SELECT count(*) FROM handoff_runs WHERE mode LIKE 'emergency-%'").fetchone()[0] == 1
    finally:
        app.close()
    got, want = check_recovery(home, acks)
    assert got == want
