"""Importance ranking within a token budget (spec §6, BLUEPRINT §8).

The budget decides how much DETAIL goes into the primary file, never which history is forgotten:
everything left out of the primary file is in the companion files, and the primary file says so.

Order of inclusion:
1. mandatory: every critical live entry as at least a one-line digest with its tag, the evolution
   chain of every critical topic, open conflicts, where-stopped / next action (always rendered);
2. then greedy by priority: digests of the remaining entries (score), full detail (0.6 × score,
   +50 for critical), evolution chains (0.8 × score of the head);
3. a final render is measured; optional items are dropped in reverse order until it fits.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import datetime
from typing import Any

from ..synthesis import tokens
from .collect import ProjectData
from .render import LIVE, Detail, Selection, entry_md, evolution_chains, evolution_md
from .tokens import est_tokens

IMPORTANCE = {"critical": 100, "important": 30, "optional": 5}
STATUS = {"active": 20, "unverified": 15, "rejected": 5, "resolved": 0}
KIND = {"decision": 10, "requirement": 10, "constraint": 8, "rejected_approach": 6, "bug": 4, "fix": 2}
REFERENCED = 25
TASK_TOPIC = 10
RECENCY_MAX = 15
RECENCY_HALF_LIFE_DAYS = 30

LEVELS: dict[str, dict[str, int | None]] = {
    "brief": {"state_char_cap": 400, "task_cap": 5},
    "quick": {"state_char_cap": 600, "task_cap": 5},
    "standard": {"state_char_cap": 2500, "task_cap": 15},
    "detailed": {"state_char_cap": None, "task_cap": None},
}

PRIMARY_STATUSES = ("active", "unverified", "rejected", "resolved")


def candidates(data: ProjectData) -> list[dict[str, Any]]:
    """Entries the primary file may show (superseded/outdated live only in Evolution / companions)."""
    out = []
    for e in data.entries:
        if e["status"] in LIVE:
            out.append(e)
        elif e["status"] == "rejected" or (e["status"] == "resolved" and e["kind"] in
                                           ("bug", "fix", "lesson", "milestone", "open_question", "risk")):
            out.append(e)
        elif e["kind"] == "rejected_approach" and e["status"] == "resolved":
            out.append(e)
    return out


def _age_days(ts: str, now: datetime) -> float:
    try:
        return max(0.0, (now - datetime.fromisoformat(ts)).total_seconds() / 86400)
    except ValueError:
        return 365.0


def score(e: dict[str, Any], *, now: datetime, referenced: set[int], task_terms: frozenset[str]) -> float:
    s = float(IMPORTANCE.get(e["importance"], 5) + STATUS.get(e["status"], 0) + KIND.get(e["kind"], 0))
    s += RECENCY_MAX * math.pow(0.5, _age_days(e["updated_at"], now) / RECENCY_HALF_LIFE_DAYS)
    if e["id"] in referenced:
        s += REFERENCED
    topic_terms = {t for t in tokens(e["topic_key"].replace(".", " ").replace("_", " ")) if len(t) >= 3}
    if topic_terms and topic_terms <= task_terms:
        s += TASK_TOPIC
    return s


def _context(data: ProjectData) -> tuple[set[int], frozenset[str]]:
    refs = {int(r) for sec in data.state["sections"].values() for r in sec.get("refs") or []}
    text = " ".join(f"{t['title']} {t.get('notes') or ''}" for t in data.state["tasks"] if t["status"] != "done")
    return refs, tokens(text)


def rank(data: ProjectData) -> list[tuple[float, dict[str, Any]]]:
    now = datetime.now().astimezone()
    refs, terms = _context(data)
    scored = [(score(e, now=now, referenced=refs, task_terms=terms), e) for e in candidates(data)]
    scored.sort(key=lambda x: (-x[0], x[1]["id"]))
    return scored


def select(data: ProjectData, budget: int, level: str, render: Callable[[Selection], str],
           topics: list[str] | None = None) -> tuple[Selection, str, list[str]]:
    """Return (selection, rendered text, warnings). `render` turns a Selection into the document."""
    params = LEVELS[level]
    full_budget = budget
    budget = int(budget - min(150, budget * 0.05))  # reserve room for the "not in this file" note
    ranked = rank(data)
    if topics:
        pref = tuple(t.strip().lower() for t in topics if t.strip())
        ranked = [(s, e) for s, e in ranked if e["topic_key"].startswith(pref)]
    chains = evolution_chains(data)
    head_score = {e["id"]: s for s, e in ranked}

    detail: dict[int, Detail] = {}
    heads: set[int] = set()
    for _, e in ranked:
        if e["importance"] == "critical" and e["status"] in LIVE:
            detail[e["id"]] = "digest"
    for h, chain in chains.items():
        if chain[-1]["importance"] == "critical" or any(c["importance"] == "critical" for c in chain):
            if not topics or chain[-1]["id"] in head_score:
                heads.add(h)

    sel = Selection(detail=detail, evolution_heads=heads,
                    state_char_cap=params["state_char_cap"], task_cap=params["task_cap"])
    text = render(sel)
    cost = est_tokens(text)
    warnings: list[str] = []
    if cost > full_budget:
        warnings.append(f"mandatory content alone is ~{cost} tokens, over the {full_budget}-token budget "
                        "(every critical entry must appear); consider a larger level")

    actions: list[tuple[float, str, int]] = []
    for s, e in ranked:
        if e["id"] not in detail:
            actions.append((s, "digest", e["id"]))
        actions.append((s * 0.6 + (50 if e["importance"] == "critical" else 0), "full", e["id"]))
    for h in chains:
        if h not in heads and (not topics or h in head_score):
            actions.append((head_score.get(h, 10.0) * 0.8, "evo", h))
    actions.sort(key=lambda a: (-a[0], a[2], a[1]))

    applied: list[tuple[str, int, Detail | None]] = []
    for _, kind, eid in actions:
        if kind == "evo":
            delta = est_tokens(evolution_md(data, [eid]))
            if cost + delta <= budget:
                heads.add(eid)
                applied.append((kind, eid, None))
                cost += delta
            continue
        e = data.by_id[eid]
        if kind == "digest":
            delta = est_tokens(entry_md(e, "digest"))
        else:
            if detail.get(eid) != "digest":
                continue
            delta = est_tokens(entry_md(e, "full")) - est_tokens(entry_md(e, "digest"))
        if cost + delta <= budget:
            applied.append((kind, eid, detail.get(eid)))
            detail[eid] = kind  # type: ignore[assignment]
            cost += delta

    text = render(sel)
    while est_tokens(text) > budget and applied:  # estimates are per-piece; verify the whole and back off
        kind, eid, prev = applied.pop()
        if kind == "evo":
            heads.discard(eid)
        elif prev is None:
            detail.pop(eid, None)
        else:
            detail[eid] = prev
        text = render(sel)

    omitted: dict[str, int] = {}
    for _, e in ranked:
        if e["id"] not in detail:
            omitted[e["kind"]] = omitted.get(e["kind"], 0) + 1
        elif detail[e["id"]] == "digest":
            omitted["shortened to one line"] = omitted.get("shortened to one line", 0) + 1
    missing_chains = len([h for h in chains if h not in heads])
    if missing_chains:
        omitted["evolution chains"] = missing_chains
    sel.omitted = omitted
    if omitted:
        text = render(sel)
    return sel, text, warnings
