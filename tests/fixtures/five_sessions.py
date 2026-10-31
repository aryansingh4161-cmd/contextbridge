"""Shared fixture: a project built over five sessions, each adding different information
(spec acceptance Test 13): requirements; architecture decision; rejected approach;
Flask -> FastAPI migration; bug fix + updated requirement + progress."""

from __future__ import annotations

from contextbridge.core import knowledge as kn
from contextbridge.core import projects as proj
from contextbridge.core import state as st
from contextbridge.core.app import App
from contextbridge.core.checkpoints import CheckpointIn, checkpoint
from contextbridge.core.knowledge import EntryIn

PORT_CMD = "Serve on port 8443: `uvicorn app.main:app --host 0.0.0.0 --port 8443`"


def E(**kw: object) -> EntryIn:  # noqa: N802
    kw.setdefault("provenance", "user_stated")
    return EntryIn(**kw)  # type: ignore[arg-type]


def five_sessions(app: App) -> tuple[int, dict[str, int]]:
    """Fixture project, ≥5 sessions, each adding different information (spec Test 13)."""
    c = app.conn
    proj.create_project(c, name="SIH Document Verification", objective="Verify academic certificates", slug="sih")
    pid = proj.get_project_id(c, "sih")
    ids: dict[str, int] = {}

    def cp(label: str, summary: str, where: str, nxt: str) -> None:
        checkpoint(c, pid, CheckpointIn(summary=summary, where_stopped=where, next_action=nxt, session_label=label))

    # s1: original requirements
    r = kn.remember(c, pid, [
        E(kind="requirement", topic_key="auth.login", title="Users log in with OTP", body=PORT_CMD,
          importance="critical"),
        E(kind="constraint", topic_key="hosting.budget", title="Free-tier hosting only", importance="important"),
    ], session_label="s1-requirements")
    ids["req_otp"], ids["budget"] = r.results[0].id or 0, r.results[1].id or 0
    cp("s1-requirements", "gathered requirements", "requirements doc drafted", "pick a backend")
    # s2: architecture decision
    r = kn.remember(c, pid, [E(kind="decision", topic_key="backend.framework", title="Use Flask for the backend",
                               rationale="team already knows Flask", importance="critical")],
                    session_label="s2-architecture")
    ids["flask"] = r.results[0].id or 0
    st.update_state(c, pid, {"stack": st.SectionIn("Flask + Postgres", [ids["flask"]])})
    cp("s2-architecture", "chose Flask", "app skeleton created", "add auth")
    # s3: rejected approach
    r = kn.remember(c, pid, [E(kind="rejected_approach", topic_key="auth.token_storage",
                               title="Store JWTs in localStorage", rationale="XSS exposure", importance="important")],
                    session_label="s3-auth")
    ids["rejected"] = r.results[0].id or 0
    cp("s3-auth", "designed auth", "token storage decided: httpOnly cookie", "implement login")
    # s4: Flask -> FastAPI migration
    r = kn.remember(c, pid, [E(kind="decision", topic_key="backend.framework", title="Use FastAPI for the backend",
                               rationale="async IO for OCR calls and pydantic validation", importance="critical",
                               supersedes_id=ids["flask"])], session_label="s4-migration")
    ids["fastapi"] = r.results[0].id or 0
    st.update_state(c, pid, {"stack": st.SectionIn("FastAPI + Postgres", [ids["fastapi"]]),
                             "architecture": st.SectionIn("API + OCR worker + Postgres")})
    cp("s4-migration", "migrated to FastAPI", "all routes ported", "fix token refresh")
    # s5: bug fix, updated requirement, progress
    r = kn.remember(c, pid, [
        E(kind="bug", topic_key="auth.refresh", title="Refresh token rejected after 15 min", status="resolved",
          importance="important"),
        E(kind="fix", topic_key="auth.refresh", title="Use clock-skew leeway of 30 s", importance="important"),
        E(kind="requirement", topic_key="auth.login", title="Users log in with OTP or institutional SSO",
          body=PORT_CMD + "\nSSO via SAML.", importance="critical", supersedes_id=ids["req_otp"]),
    ], session_label="s5-progress")
    ids["bug"], ids["fix"], ids["req_sso"] = (r.results[0].id or 0, r.results[1].id or 0, r.results[2].id or 0)
    st.update_state(c, pid, {"implemented": st.SectionIn("login, refresh"),
                             "in_progress": st.SectionIn("SSO")},
                    [st.TaskIn(title="Implement SAML SSO", status="in_progress", priority=1),
                     st.TaskIn(title="Write OCR worker tests", status="todo", priority=2)])
    cp("s5-progress", "fixed refresh, started SSO", "app/auth/sso.py: SAML metadata parsed", "wire SAML callback")
    return pid, ids
