"""Validation and completeness report (spec §8.7).

Checks are run against the RENDERED text where possible (e.g. "is every critical entry's tag
actually in the primary file?"), not against what the generator intended to write. The score
is informative (share of checks passing, warnings count half); it is never presented as a
guarantee, and a failing check always appears in `missing`.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ..security.redact import redact
from .collect import ProjectData
from .render import LIVE, coverage, evolution_chains


def _check(checks: list[dict[str, Any]], name: str, ok: bool, detail: str = "", *, warn_only: bool = False) -> None:
    status = "ok" if ok else ("warn" if warn_only else "fail")
    checks.append({"name": name, "status": status, "detail": detail})


def validate(data: ProjectData, primary: str, companions: dict[str, str], *,
             stale_after_days: int = 30) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    secs = data.state["sections"]
    everything = primary + "".join(companions.values())

    _check(checks, "project objective present", bool(data.project.get("objective")),
           "" if data.project.get("objective") else "no objective recorded")
    _check(checks, "stack identified", "stack" in secs, "" if "stack" in secs else "Live State 'stack' is empty",
           warn_only=True)
    _check(checks, "architecture identified", "architecture" in secs,
           "" if "architecture" in secs else "Live State 'architecture' is empty", warn_only=True)

    critical = [e for e in data.entries if e["importance"] == "critical" and e["status"] in LIVE]
    missing_crit = [e["tag"] for e in critical if e["tag"] not in primary]
    _check(checks, "all critical active entries in the primary file", not missing_crit,
           f"{len(critical)} critical entr{'y' if len(critical) == 1 else 'ies'}"
           + (f"; missing: {', '.join(missing_crit)}" if missing_crit else ""))
    verbatim_missing = [e["tag"] for e in critical if (e.get("body") or "").strip()
                        and (e["body"] or "").strip("\n") not in everything]
    _check(checks, "critical entry bodies verbatim in the package", not verbatim_missing,
           ", ".join(verbatim_missing))

    since = data.last_handoff_at
    recent = [e for e in data.entries if e["kind"] == "decision" and e["status"] in LIVE
              and (since is None or e["created_at"] > since)]
    recent_missing = [e["tag"] for e in recent if e["tag"] not in primary]
    _check(checks, "recent decisions (since last handoff) in the primary file", not recent_missing,
           (f"{len(recent)} recent" + (f"; only in companion files: {', '.join(recent_missing)}" if recent_missing
                                       else "")), warn_only=True)

    open_tasks = [t for t in data.state["tasks"] if t["status"] in ("todo", "in_progress", "blocked")]
    shown = [t for t in open_tasks if f"T-{t['id']:04d}" in primary]
    _check(checks, "unfinished tasks present", len(shown) == len(open_tasks) or bool(shown),
           f"{len(shown)}/{len(open_tasks)} open tasks in the primary file; all in 03_current_state.md",
           warn_only=True)
    _check(checks, "blockers recorded or none", True,
           "blockers section present" if "blockers" in secs else "no blockers recorded")
    has_next = data.latest_checkpoint is not None
    _check(checks, "immediate next action present", has_next,
           "" if has_next else "no checkpoint yet: next action unknown (call checkpoint or pass final_updates)")
    if data.latest_checkpoint is not None and data.writes_since_checkpoint:
        _check(checks, "where-stopped is current", False,
               f"{data.writes_since_checkpoint} change(s) since the last checkpoint "
               f"({data.latest_checkpoint['created_at']}); pass final_updates to refresh it", warn_only=True)

    questions = [e for e in data.entries if e["kind"] == "open_question" and e["status"] in LIVE]
    _check(checks, "open questions listed", all(e["tag"] in primary for e in questions),
           f"{len(questions)} open question(s)", warn_only=True)
    unconfirmed = [e for e in data.entries if e["status"] in LIVE and e["provenance"] in ("ai_suggested", "imported")]
    _check(checks, "unconfirmed AI suggestions marked", all(e["tag"] in primary for e in unconfirmed),
           f"{len(unconfirmed)} unconfirmed", warn_only=True)
    _check(checks, "no unresolved conflicts (or listed prominently)",
           all(f"Conflict #{k['id']}" in primary for k in data.conflicts),
           f"{len(data.conflicts)} open conflict(s) listed at the top" if data.conflicts else "",
           warn_only=bool(data.conflicts))
    if data.conflicts:
        checks[-1]["status"] = "warn"
    _check(checks, "Live State consistent with active decisions", not data.consistency,
           "; ".join(w["message"] for w in data.consistency), warn_only=True)

    cutoff = (datetime.now().astimezone() - timedelta(days=stale_after_days)).isoformat(timespec="seconds")
    stale = [e["tag"] for e in data.entries if e["status"] in LIVE
             and (e.get("last_verified_at") or e["updated_at"]) < cutoff]
    _check(checks, f"no stale items (> {stale_after_days} days unverified)", not stale,
           f"{len(stale)} stale: {', '.join(stale[:10])}" if stale else "", warn_only=True)

    leaks = sum(f.count for f in redact(everything)[1])
    _check(checks, "secrets scan clean", leaks == 0,
           f"{leaks} possible secret(s) found and redacted in the output" if leaks else "")

    root = Path(data.repo["path"]) if data.repo and data.repo.get("path") else None
    bad_refs = [f["path"] for f in data.file_refs
                if root is not None and not (root / f["path"]).exists() and not Path(f["path"]).is_absolute()]
    _check(checks, "referenced files exist or are marked external", not bad_refs, ", ".join(bad_refs),
           warn_only=True)

    chains = evolution_chains(data)
    chain_heads_in_package = sum(1 for h in chains if data.by_id[h]["tag"] in everything)
    _check(checks, "evolution of superseded items in the package", chain_heads_in_package == len(chains),
           f"{len(chains)} chain(s)")

    cov = coverage(data)
    _check(checks, "coverage: no gaps", not cov["gaps"], "; ".join(cov["gaps"]), warn_only=True)

    weights = {"ok": 1.0, "warn": 0.5, "fail": 0.0}
    score = round(sum(weights[c["status"]] for c in checks) / len(checks), 2)
    missing = [f"{c['name']}: {c['detail']}" if c["detail"] else c["name"] for c in checks if c["status"] != "ok"]
    return {"score": score, "checks": checks, "missing": missing, "coverage": cov,
            "note": "The score is the share of checks passing (warnings count half). It is informative, "
                    "not a guarantee that nothing is missing from the user's real project."}
