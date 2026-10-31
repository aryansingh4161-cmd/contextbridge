"""Acceptance Test 12: two processes write concurrently to one DB."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from contextbridge.core import projects as proj
from contextbridge.core.app import App

WRITER = r"""
import json, sys
from contextbridge.core.app import open_app
from contextbridge.core import knowledge as kn, projects as proj
from contextbridge.core.knowledge import EntryIn
home, tag, n, mode = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
app = open_app(home, log_to_stderr=False)
pid = proj.get_project_id(app.conn, "shared")
statuses = []
for i in range(n):
    if mode == "race":
        item = EntryIn(kind="decision", topic_key="backend.framework", title=f"Use {tag}",
                       rationale="r", provenance="user_stated")
    else:
        item = EntryIn(kind="fact", topic_key=f"{tag}.k{i}", title=f"{tag} fact number {i}",
                       body=f"unique body {tag} {i}", provenance="user_stated")
    statuses.append(kn.remember(app.conn, pid, [item]).results[0].status)
print(json.dumps(statuses))
"""


def _spawn(home: Path, tag: str, n: int, mode: str) -> subprocess.Popen[str]:
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[2] / "src")}
    return subprocess.Popen([sys.executable, "-c", WRITER, str(home), tag, str(n), mode],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)


def _run_pair(home: Path, n: int, mode: str) -> list[list[str]]:
    procs = [_spawn(home, "alpha", n, mode), _spawn(home, "beta", n, mode)]
    outs = []
    for p in procs:
        out, err = p.communicate(timeout=120)
        assert p.returncode == 0, err
        outs.append(json.loads(out))
    return outs


def test_concurrent_writes_no_loss(app: App, home: Path) -> None:
    proj.create_project(app.conn, name="Shared", objective="o", slug="shared")
    outs = _run_pair(home, 150, "distinct")
    assert all(s == "saved" for out in outs for s in out)
    c = app.conn
    assert c.execute("SELECT count(*) FROM entries").fetchone()[0] == 300
    assert c.execute("SELECT knowledge_version FROM projects WHERE slug='shared'").fetchone()[0] == 300
    assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    c.execute("INSERT INTO entries_fts(entries_fts) VALUES ('integrity-check')")
    # each side's writes are visible to the other (and to a third reader)
    assert c.execute("SELECT count(*) FROM entries_fts WHERE entries_fts MATCH 'alpha'").fetchone()[0] == 150
    assert c.execute("SELECT count(*) FROM entries_fts WHERE entries_fts MATCH 'beta'").fetchone()[0] == 150


def test_concurrent_same_topic_race_yields_conflicts_not_two_winners(app: App, home: Path) -> None:
    proj.create_project(app.conn, name="Shared", objective="o", slug="shared")
    _run_pair(home, 20, "race")
    active = app.conn.execute(
        "SELECT count(*) FROM entries WHERE topic_key='backend.framework' AND status='active'").fetchone()[0]
    assert active == 1  # BEGIN IMMEDIATE serializes check-then-insert: exactly one winner
    open_conflicts = app.conn.execute("SELECT count(*) FROM conflicts WHERE status='open'").fetchone()[0]
    total = app.conn.execute("SELECT count(*) FROM entries").fetchone()[0]
    assert open_conflicts == total - 1  # every other distinct entry is a recorded conflict (dupes merged)
