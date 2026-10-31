"""`get_context`: the main resume/read path (spec §7.1 #3). Read-only, budgeted, never dumps the
raw archive. Output is wrapped as memory data (not instructions). Anything over the inline cap
(BLUEPRINT V6: 8k tokens by default) is written to `exports/` and returned as a path + summary.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import datetime
from typing import Any, Literal

from .config import Config
from .errors import CBError
from .generation.collect import ProjectData, collect
from .generation.rank import select
from .generation.render import (GROUPS, MEMORY_CLOSE, MEMORY_OPEN, Selection, coverage, coverage_md, doc_header,
                                entries_of, evolution_md, handoff_md, knowledge_md, render_entries, state_file_md,
                                where_md)
from .generation.tokens import estimate
from .paths import DataPaths, ensure_private_dir, ensure_private_file
from .repo import repo_summary

Scope = Literal["brief", "state", "knowledge", "handoff", "full"]
SCOPES: tuple[Scope, ...] = ("brief", "state", "knowledge", "handoff", "full")
DEFAULT_BUDGET = {"brief": 2500, "knowledge": 6000, "handoff": 8000, "state": 8000}
MIN_BUDGET, MAX_BUDGET = 500, 8000

_KNOWLEDGE_GROUPS = [("requirements", "Requirements"), ("decisions", "Decisions — current"),
                     ("rejected", "Rejected approaches"), ("rules", "Constraints, conventions & preferences"),
                     ("bugs", "Bugs, fixes & lessons"), ("reference", "Technical reference"),
                     ("open", "Open questions & risks"), ("milestones", "Milestones")]


def _knowledge_render(data: ProjectData, gen_at: str) -> Callable[[Selection], str]:
    def render(sel: Selection) -> str:
        out = [doc_header(data, "Project knowledge", "context", gen_at)]
        for g, title in _KNOWLEDGE_GROUPS:
            statuses = ("active", "unverified", "resolved", "rejected") if g in ("rejected", "bugs", "open",
                                                                                 "milestones") else ("active", "unverified")
            body = render_entries(entries_of(data, GROUPS[g], statuses), sel)
            if body:
                out.append(f"## {title}\n\n{body}\n")
        evo = evolution_md(data, sel.evolution_heads)
        if evo:
            out.append("## Evolution (superseded = obsolete)\n\n" + evo + "\n")
        if sel.omitted:
            out.append("_Not shown (budget): " + ", ".join(f"{n} {k}" for k, n in sorted(sel.omitted.items()))
                       + ". Use search_memory, a larger budget_tokens, or scope 'full'._\n")
        return "".join(out)
    return render


def get_context(conn: sqlite3.Connection, paths: DataPaths, config: Config, project_id: int, *,
                scope: Scope = "brief", budget_tokens: int | None = None,
                topics: list[str] | None = None) -> dict[str, Any]:
    if scope not in SCOPES:
        raise CBError("invalid_input", f"scope '{scope}' is invalid", f"one of {', '.join(SCOPES)}")
    cap = int(config.get("mcp.inline_token_cap", 8000))
    if budget_tokens is not None and not MIN_BUDGET <= budget_tokens <= MAX_BUDGET:
        raise CBError("invalid_input", f"budget_tokens must be {MIN_BUDGET}–{MAX_BUDGET}",
                      "for more, use scope 'full' (written to a file) or prepare_handoff")
    data = collect(conn, project_id)
    data.repo = repo_summary(data.project.get("linked_dir"))
    gen_at = datetime.now().astimezone().isoformat(timespec="seconds")
    budget = min(budget_tokens or DEFAULT_BUDGET.get(scope, 8000), cap)
    truncated = False
    omitted: dict[str, int] = {}

    if scope in ("brief", "handoff"):
        fm = {"contextbridge_format": 1, "package_type": "context", "level": scope,
              "project_slug": data.project["slug"], "generated_at": gen_at,
              "knowledge_version": data.project["knowledge_version"], "state_version": data.project["state_version"],
              "complete": False}
        latest = data.latest_session_id
        tags = [e["tag"] for e in data.entries if latest is not None and e["session_id"] == latest
                and e["status"] not in ("superseded", "outdated")][:10]

        def render(sel: Selection) -> str:
            return handoff_md(data, sel, fm, latest_session_tags=tags,
                              companion_note="Use search_memory or get_context(scope='knowledge') for details.")

        sel, content, _ = select(data, budget, "brief" if scope == "brief" else "standard", render, topics)
        omitted, truncated = sel.omitted, bool(sel.omitted)
    elif scope == "knowledge":
        sel, content, _ = select(data, budget, "standard", _knowledge_render(data, gen_at), topics)
        omitted, truncated = sel.omitted, bool(sel.omitted)
    elif scope == "state":
        content = state_file_md(data, gen_at)
    else:  # full: complete knowledge + state, always to a file
        content = (knowledge_md(data, [g for g, _ in _KNOWLEDGE_GROUPS], include_evolution=True,
                                include_obsolete=True, include_identity=True, title="Project knowledge (complete)",
                                generated_at=gen_at)
                   + "\n" + state_file_md(data, gen_at) + "\n## Coverage\n\n" + coverage_md(coverage(data)))

    est = estimate(content)
    where, nxt = where_md(data)
    result: dict[str, Any] = {
        "scope": scope, "versions": {"knowledge": data.project["knowledge_version"],
                                     "state": data.project["state_version"]},
        "coverage": {k: v for k, v in coverage(data).items() if k in ("sessions_total", "sessions_captured", "gaps")},
        "open_conflicts": len(data.conflicts), "truncated": truncated, "est_tokens": est.to_dict(),
    }
    if omitted:
        result["omitted"] = omitted
    if scope == "full" or est.est > cap:
        ensure_private_dir(paths.exports)
        d = paths.exports / data.project["slug"]
        ensure_private_dir(d)
        f = d / f"context_{scope}_{datetime.now().astimezone().strftime('%Y-%m-%d_%H%M%S')}.md"
        f.write_text(content, encoding="utf-8")
        ensure_private_file(f)
        result["file_path"] = str(f)
        result["content"] = (f"{MEMORY_OPEN}\n{where}\n\n**Next action:** {nxt}\n\n_The full {scope} context "
                             f"(~{est.est} tokens) was written to {f} because it exceeds the inline limit "
                             f"of {cap} tokens or scope is 'full'._\n{MEMORY_CLOSE}")
    else:
        result["content"] = f"{MEMORY_OPEN}\n{content}\n{MEMORY_CLOSE}"
    return result
