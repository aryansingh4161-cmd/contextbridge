"""M4: generation, ranking, validation, packages (acceptance tests 3, 4, 5, 13, 16 at the core level)."""

from __future__ import annotations

import hashlib
import json
import re
import stat
import subprocess
from pathlib import Path

import pytest

from contextbridge.core import knowledge as kn
from contextbridge.core import projects as proj
from contextbridge.core import state as st
from contextbridge.core.app import App, open_app
from contextbridge.core.checkpoints import CheckpointIn, checkpoint
from contextbridge.core.context import get_context
from contextbridge.core.errors import CBError
from contextbridge.core.generation.split import merge_parts, split_markdown
from contextbridge.core.generation.tokens import estimate
from contextbridge.core.handoff import latest_handoff, prepare_handoff
from contextbridge.core.knowledge import EntryIn
from contextbridge.core.repo import link

from fixtures.five_sessions import PORT_CMD, E, five_sessions

def all_text(out: dict[str, object]) -> str:
    return "".join(Path(f["path"]).read_text(encoding="utf-8") for f in out["files"])  # type: ignore[index, union-attr]


# ---------------------------------------------------------------- tokens / split

def test_estimate_is_a_range_and_code_and_non_ascii_cost_more() -> None:
    prose = estimate("word " * 200)
    assert prose.low < prose.est <= prose.high
    code = estimate("```\n" + "x = 1;\n" * 140 + "```\n")
    assert code.est > estimate("x" * len("x = 1;\n" * 140)).est
    assert estimate("प्रमाणपत्र" * 20).est > estimate("certificat" * 20).est


def test_split_is_byte_exact_and_respects_fences() -> None:
    doc = "# A\n\ntext\n\n## B\n\n```\n# not a heading\n" + "line\n" * 50 + "```\n\n## C\n\nend\n"
    parts = split_markdown(doc, lambda t: len(t) <= 120)
    assert merge_parts(parts) == doc and len(parts) > 1
    for p in parts:
        assert p.count("```") % 2 == 0, "never split inside a fence"
        assert p.startswith(("#", "```")), "cuts only at headings or between whole blocks"
    assert split_markdown("small", lambda t: True) == ["small"]
    # one big section without sub-headings is cut between blocks, never inside a fence or a table
    big = "# Facts\n\n" + "".join(f"- fact {i} " + "x" * 60 + "\n\n" for i in range(30)) \
        + "```\ncode\n\nstill code\n```\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\nend\n"
    parts = split_markdown(big, lambda t: len(t) <= 300)
    assert merge_parts(parts) == big and len(parts) > 5
    for p in parts:
        assert p.count("```") % 2 == 0 and not p.startswith("|---")


# ---------------------------------------------------------------- Test 13: five-session synthesis

def test_five_session_knowledge_package(app: App) -> None:
    pid, ids = five_sessions(app)
    out = prepare_handoff(app.conn, app.paths, app.config, pid, mode="knowledge_package", profile="claude_project")
    names = [Path(f["path"]).name for f in out["files"]]
    assert names == ["01_START_HERE_handoff.txt", "02_project_knowledge.txt", "03_current_state.txt",
                     "04_decisions_and_evolution.txt", "05_tasks_and_issues.txt", "06_technical_reference.txt"]
    text = all_text(out)
    tags = {e["id"]: e for e in (dict(r) for r in app.conn.execute("SELECT id FROM entries WHERE project_id=?", (pid,)))}
    # every entry from every session is represented (critical/important: all of them)
    for eid in tags:
        assert re.search(rf"-{eid:04d} ·", text), f"entry {eid} missing from the package"
    evo = Path(out["files"][3]["path"]).read_text(encoding="utf-8")
    line = next(ln for ln in evo.splitlines() if ln.startswith("- **backend.framework**"))
    assert line.index("Flask") < line.index("FastAPI"), "evolution ordered oldest -> newest"
    assert "obsolete" in line
    current = evo.split("## Decisions — current")[1].split("## ")[0]
    assert "FastAPI" in current and "Flask for" not in current, "superseded never listed as current"
    assert "superseded" in evo.split("## Obsolete entries")[1]
    # updated requirement: the new one is current, the old one only under "Obsolete"
    k = Path(out["files"][1]["path"]).read_text(encoding="utf-8")
    reqs, obsolete = k.split("## Requirements")[1].split("## ")[0], k.split("## Obsolete entries")[1]
    assert "OTP or institutional SSO" in reqs and "**Users log in with OTP**" not in reqs
    assert "**Users log in with OTP**" in obsolete


def test_working_handoff_separates_history_from_state(app: App) -> None:
    pid, ids = five_sessions(app)
    out = prepare_handoff(app.conn, app.paths, app.config, pid, mode="standard")
    primary = Path(out["primary_file"]).read_text(encoding="utf-8")
    sec = {m.group(1): m.start() for m in re.finditer(r"^# (\d+)\.", primary, re.M)}
    assert list(sec) == [str(i) for i in range(1, 14)], "all 13 template sections, in order"
    s5 = primary[sec["5"]:sec["6"]]
    s6 = primary[sec["6"]:sec["7"]]
    assert "Use FastAPI" in s5 and "Use Flask" not in s5
    assert "Use Flask" in s6 and "Use FastAPI" in s6
    assert "auth.login" in s6, "superseded requirement has an evolution line too"
    assert "app/auth/sso.py: SAML metadata parsed" in primary[sec["1"]:sec["2"]]
    assert "wire SAML callback" in primary[sec["2"]:sec["3"]] and "T-0001" in primary
    assert "FastAPI + Postgres" in primary[sec["4"]:sec["5"]]
    assert "Store JWTs in localStorage" in primary[sec["7"]:sec["8"]]
    assert out["report"]["score"] > 0.8
    assert primary.startswith("---\ncontextbridge_format: 1\npackage_type: \"session_handoff\"")
    assert "estimated_tokens: [REDACTED" not in primary


# ---------------------------------------------------------------- Test 4: critical + exact values

def test_critical_entries_and_exact_values_survive_tiny_budget(app: App, home: Path) -> None:
    pid, ids = five_sessions(app)
    filler = [E(kind="fact", topic_key=f"misc.fact_{i}", title=f"Filler fact number {i} about the OCR pipeline",
                body="detail " * 60, importance="optional") for i in range(120)]
    kn.remember(app.conn, pid, filler[:50]); kn.remember(app.conn, pid, filler[50:100]); kn.remember(app.conn, pid, filler[100:])
    out = prepare_handoff(app.conn, app.paths, app.config, pid, mode="quick")
    primary = Path(out["primary_file"]).read_text(encoding="utf-8")
    assert out["files"][0]["est_tokens_range"]["est"] <= 3000
    crit = [dict(r) for r in app.conn.execute(
        "SELECT id, kind FROM entries WHERE project_id=? AND importance='critical' AND status='active'", (pid,))]
    for e in crit:
        assert re.search(rf"-{e['id']:04d} ·", primary), "every critical active entry is in the primary file"
    assert PORT_CMD in all_text(out), "exact values verbatim somewhere in the package"
    assert out["omitted_from_primary"], "the budget forced omissions, and the file says so"
    assert "complete: false" in primary
    assert "Not in this file (budget)" in primary
    companion = Path(out["files"][1]["path"]).read_text(encoding="utf-8")
    assert all(f"Filler fact number {i} " in companion for i in range(120)), "nothing dropped from the package"


# ---------------------------------------------------------------- Tests 5, 16: conflicts, coverage

def test_conflict_surfaced_and_coverage_gap_named(app: App) -> None:
    pid, _ = five_sessions(app)
    kn.remember(app.conn, pid, [E(kind="decision", topic_key="backend.framework", title="Use Django",
                                  rationale="admin panel", importance="critical")], session_label="s6-unsaved")
    out = prepare_handoff(app.conn, app.paths, app.config, pid, mode="standard")
    primary = Path(out["primary_file"]).read_text(encoding="utf-8")
    top = primary.split("# 1. Where we stopped")[0]
    assert "Unresolved conflicts" in top and "backend.framework" in top
    assert "never checkpointed" in primary and "s6-unsaved" in primary
    assert any("s6-unsaved" in g for g in out["report"]["coverage"]["gaps"])
    assert any("change(s) since the last checkpoint" in g for g in out["report"]["coverage"]["gaps"])
    assert any(c["name"].startswith("no unresolved conflicts") and c["status"] == "warn"
               for c in out["report"]["checks"])


# ---------------------------------------------------------------- package mechanics

def test_manifest_hashes_permissions_snapshot_and_run(app: App) -> None:
    pid, _ = five_sessions(app)
    out = prepare_handoff(app.conn, app.paths, app.config, pid, mode="standard")
    d = Path(out["directory"])
    manifest = json.loads((d / "00_manifest.json").read_text(encoding="utf-8"))
    for f in manifest["files"]:
        if f.get("upload"):
            assert hashlib.sha256((d / f["name"]).read_bytes()).hexdigest() == f["sha256"]
    assert manifest["load_order"][0] == "01_START_HERE_handoff.md"
    assert stat.S_IMODE(d.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in d.iterdir())
    side = json.loads((d / "source_references.json").read_text(encoding="utf-8"))
    assert {e["provenance"] for e in side["entries"]} == {"user_stated"} and side["entries"][0]["id"] == 1
    assert app.conn.execute("SELECT count(*) FROM handoff_runs WHERE project_id=?", (pid,)).fetchone()[0] == 1
    h = latest_handoff(app.conn, pid)
    assert h and h["primary"] == (d / "01_START_HERE_handoff.md").read_text(encoding="utf-8")
    again = prepare_handoff(app.conn, app.paths, app.config, pid, mode="standard")
    assert again["directory"] != out["directory"], "never overwrites an earlier package"


def test_final_updates_saved_first(app: App) -> None:
    pid, _ = five_sessions(app)
    st.update_state(app.conn, pid, {"broken": st.SectionIn("SAML callback 500s")})
    out = prepare_handoff(app.conn, app.paths, app.config, pid, final_updates=CheckpointIn(
        summary="SSO half done", where_stopped="sso.py line 88: signature check fails", next_action="debug cert"))
    primary = Path(out["primary_file"]).read_text(encoding="utf-8")
    assert "sso.py line 88: signature check fails" in primary and "debug cert" in primary
    kind = app.conn.execute("SELECT kind FROM checkpoints ORDER BY id DESC LIMIT 1").fetchone()[0]
    assert kind == "handoff"
    assert not any(c["name"] == "where-stopped is current" for c in out["report"]["checks"])


def test_split_and_file_count_against_profile(home: Path) -> None:
    home.mkdir(parents=True)
    (home / "config.toml").write_text(
        "[profiles.tiny]\nmax_file_bytes = 2500\nmax_files = 3\nformats = ['md']\npreferred_ext = 'md'\n"
        "max_total_tokens = 500\n")
    app = open_app(home, log_to_stderr=False)
    try:
        pid, _ = five_sessions(app)
        out = prepare_handoff(app.conn, app.paths, app.config, pid, mode="knowledge_package", profile="tiny")
        d = Path(out["directory"])
        manifest = json.loads((d / "00_manifest.json").read_text(encoding="utf-8"))
        split = [f for f in manifest["files"] if f.get("of", 1) > 1]
        assert split, "something had to be split at 2.5 kB"
        for src in {f["source"] for f in split}:
            parts = sorted((f for f in split if f["source"] == src), key=lambda f: f["part"])
            merged = b"".join((d / f["name"]).read_bytes() for f in parts)
            assert hashlib.sha256(merged).hexdigest() == parts[0]["source_sha256"]
        assert len(manifest["upload_plan"]) > 1 and all(len(b) <= 3 for b in manifest["upload_plan"])
        assert any("exceed profile 'tiny'" in w for w in out["warnings"])
        assert any("consolidated into 02_companion_files" in w for w in out["warnings"])
        assert any("max_total_tokens=500" in w for w in out["warnings"])
    finally:
        app.close()


def test_export_redacts_secrets_that_bypassed_write_time(app: App) -> None:
    pid, _ = five_sessions(app)
    fake = "sk-ant-" + "api03-" + "A" * 12 + "b" * 12 + "C9" * 6
    app.conn.execute("UPDATE live_state SET content = content || ? WHERE project_id = ? AND section = 'stack'",
                     (f"\nANTHROPIC key {fake}", pid))
    out = prepare_handoff(app.conn, app.paths, app.config, pid, mode="detailed")
    assert fake not in all_text(out)
    assert fake not in Path(out["directory"], "source_references.json").read_text(encoding="utf-8")
    assert any("redacted" in w for w in out["warnings"])
    assert any(c["name"] == "secrets scan clean" and c["status"] == "fail" for c in out["report"]["checks"])


def test_project_isolation_in_packages(app: App) -> None:
    pid, _ = five_sessions(app)
    proj.create_project(app.conn, name="Other", objective="o", slug="other")
    oid = proj.get_project_id(app.conn, "other")
    kn.remember(app.conn, oid, [E(kind="fact", topic_key="secret.plan", title="OTHER-PROJECT-MARKER")])
    out = prepare_handoff(app.conn, app.paths, app.config, pid, mode="knowledge_package")
    assert "OTHER-PROJECT-MARKER" not in all_text(out)
    assert "OTHER-PROJECT-MARKER" not in Path(out["directory"], "source_references.json").read_text(encoding="utf-8")


def test_linked_repo_summary(app: App, tmp_path: Path) -> None:
    pid, _ = five_sessions(app)
    repo = tmp_path / "repo"
    repo.mkdir()
    run = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)  # noqa: E731
    run("init", "-q", "-b", "main")
    (repo / "app.py").write_text("SECRET_CONTENT_NEVER_EXPORTED = 1\n")
    run("add", "."); run("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "first commit")
    (repo / "app.py").write_text("changed\n")
    link(app.conn, pid, str(repo))
    out = prepare_handoff(app.conn, app.paths, app.config, pid, mode="standard")
    primary = Path(out["primary_file"]).read_text(encoding="utf-8")
    files = primary.split("# 11. Files & repo")[1].split("# 12.")[0]
    assert "Branch: `main`" in files and "first commit" in files and "`app.py`" in files
    assert "SECRET_CONTENT_NEVER_EXPORTED" not in all_text(out)
    with pytest.raises(CBError):
        link(app.conn, pid, str(tmp_path / "nope"))


# ---------------------------------------------------------------- get_context

def test_get_context_scopes_budget_and_file_fallback(app: App) -> None:
    pid, _ = five_sessions(app)
    b = get_context(app.conn, app.paths, app.config, pid)
    assert b["content"].startswith("<<<CONTEXTBRIDGE_MEMORY") and b["est_tokens"]["est"] <= 2500
    assert "wire SAML callback" in b["content"] and "file_path" not in b
    s = get_context(app.conn, app.paths, app.config, pid, scope="state")
    assert "FastAPI + Postgres" in s["content"] and "Implement SAML SSO" in s["content"]
    k = get_context(app.conn, app.paths, app.config, pid, scope="knowledge", topics=["auth"])
    assert "localStorage" in k["content"] and "Use FastAPI" not in k["content"].split("## Evolution")[0]
    f = get_context(app.conn, app.paths, app.config, pid, scope="full")
    assert Path(f["file_path"]).exists() and "written to" in f["content"]
    with pytest.raises(CBError):
        get_context(app.conn, app.paths, app.config, pid, budget_tokens=100)


def test_get_context_inline_cap_writes_file(home: Path) -> None:
    home.mkdir(parents=True)
    (home / "config.toml").write_text("[mcp]\ninline_token_cap = 150\n")
    app = open_app(home, log_to_stderr=False)
    try:
        pid, _ = five_sessions(app)
        s = get_context(app.conn, app.paths, app.config, pid, scope="state")
        assert "file_path" in s and Path(s["file_path"]).read_text(encoding="utf-8")
        assert "wire SAML callback" in s["content"], "inline summary still says where we stopped"
    finally:
        app.close()


def test_empty_project_handoff_is_honest(app: App) -> None:
    proj.create_project(app.conn, name="Empty", objective="tbd", slug="empty")
    pid = proj.get_project_id(app.conn, "empty")
    out = prepare_handoff(app.conn, app.paths, app.config, pid, mode="quick")
    primary = Path(out["primary_file"]).read_text(encoding="utf-8")
    assert "No checkpoint has been saved yet" in primary
    assert any("next action" in m for m in out["report"]["missing"])
    assert "complete: false" in primary


def test_brief_keeps_decision_reasons_and_open_questions(app: App) -> None:
    pid, _ = five_sessions(app)
    kn.remember(app.conn, pid, [E(kind="open_question", topic_key="ocr.vendor", title="Which OCR vendor handles seals?",
                                  importance="optional")])
    for start in (0, 40):
        kn.remember(app.conn, pid, [E(kind="fact", topic_key=f"misc.f{i}", title=f"filler {i}", body="x " * 300,
                                      importance="important") for i in range(start, start + 40)])
    b = get_context(app.conn, app.paths, app.config, pid, scope="brief", budget_tokens=1500)["content"]
    assert "Which OCR vendor handles seals?" in b, "open questions are mandatory, even optional ones"
    assert "Use FastAPI for the backend" in b and "why: async io" in b.lower(), "a decision keeps its reason"
    from contextbridge.core.generation.render import entry_md
    d = entry_md({"tag": "[D-0001 · user_stated · 2026-10-31]", "title": "Use FastAPI", "kind": "decision",
                  "rationale": "async IO for OCR calls", "importance": "important", "provenance": "user_stated",
                  "status": "active", "confidence": "confirmed"}, "digest")
    assert d.strip().endswith("— why: async IO for OCR calls"), "the one-line form keeps the reason too"
