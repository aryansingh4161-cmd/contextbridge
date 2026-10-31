"""Acceptance Test 10: a package needing > 20 files under a 20-file profile → warning + a plan within the limit."""

from __future__ import annotations

import json
from pathlib import Path

from contextbridge.core import knowledge as kn
from contextbridge.core.app import open_app
from contextbridge.core.handoff import prepare_handoff
from contextbridge.core.knowledge import EntryIn

from fixtures.five_sessions import five_sessions


def test_more_than_20_files_under_a_20_file_profile(home: Path) -> None:
    home.mkdir(parents=True)
    # claude_chat's real limit is 20 files; tiny bytes force many parts so the 20-file rule is exercised
    (home / "config.toml").write_text("[profiles.chat20]\nmax_file_bytes = 700\nmax_files = 20\n"
                                      "formats = ['txt']\npreferred_ext = 'txt'\n")
    app = open_app(home, log_to_stderr=False)
    try:
        pid, _ = five_sessions(app)
        for start in (0, 40):  # batches of ≤ 50 per call
            kn.remember(app.conn, pid, [EntryIn(kind="fact", topic_key=f"f.k{i}", title=f"fact {i}",
                                                body="detail " * 250, provenance="user_stated")
                                        for i in range(start, start + 40)])
        out = prepare_handoff(app.conn, app.paths, app.config, pid, mode="knowledge_package", profile="chat20")
        manifest = json.loads((Path(out["directory"]) / "00_manifest.json").read_text())
        n_files = len([f for f in manifest["files"] if f.get("upload")])
        assert n_files > 20, "the scenario really needs more than 20 files"
        assert all(len(batch) <= 20 for batch in manifest["upload_plan"]), "every upload batch fits the limit"
        assert sum(len(b) for b in manifest["upload_plan"]) == n_files, "nothing left out of the plan"
        assert any("consolidated" in w for w in out["warnings"]) and any("exceed profile" in w for w in out["warnings"])
    finally:
        app.close()
