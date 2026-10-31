"""Consistency validator between Live State and Project Knowledge (spec P2).

Checks:
  1. refs: every entry a state section references must be active (not superseded/outdated).
  2. superseded-value heuristic: if a decision on topic T was superseded, the words that
     distinguish the old title from the new one (e.g. {"flask"} vs {"fastapi"}) are its "value".
     A stack/architecture/environment/dependencies section that mentions the old value and none
     of the new one is flagged. This is a heuristic (documented in LIMITATIONS.md).
  3. open conflicts are reported, since state may depend on an undecided topic.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from .synthesis import tokens

CHECKED_SECTIONS = ("stack", "architecture", "environment", "dependencies")
STOP = frozenset({"use", "using", "the", "for", "and", "with", "to", "a", "an", "of", "in", "on", "our",
                  "we", "backend", "frontend", "switch", "migrate", "migrated", "move", "moved", "from",
                  "instead", "as", "is", "be", "will", "now"})


def _value_terms(title: str) -> set[str]:
    return {t for t in tokens(title) if len(t) >= 3 and t not in STOP}


def check(conn: sqlite3.Connection, project_id: int) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    sections = {r["section"]: r for r in conn.execute(
        "SELECT section, content, refs FROM live_state WHERE project_id = ?", (project_id,))}

    for name, row in sections.items():
        for ref in json.loads(row["refs"]) if row["refs"] else []:
            e = conn.execute("SELECT id, status, superseded_by_id, title FROM entries WHERE id = ? AND project_id = ?",
                             (ref, project_id)).fetchone()
            if e is None:
                warnings.append({"code": "dangling_ref", "section": name, "entry_id": ref,
                                 "message": f"section '{name}' references entry {ref}, which no longer exists"})
            elif e["status"] not in ("active", "unverified", "resolved"):
                nxt = f"; current entry is {e['superseded_by_id']}" if e["superseded_by_id"] else ""
                warnings.append({"code": "stale_ref", "section": name, "entry_id": ref,
                                 "message": f"section '{name}' references {e['status']} entry {ref} "
                                            f"('{e['title']}'){nxt}"})

    superseded = conn.execute(
        """SELECT old.id AS old_id, old.title AS old_title, new.id AS new_id, new.title AS new_title,
                  old.topic_key AS topic
           FROM entries old JOIN entries new ON new.id = old.superseded_by_id
           WHERE old.project_id = ? AND old.kind = 'decision' AND old.status = 'superseded'
             AND new.status = 'active'""", (project_id,)).fetchall()
    for s in superseded:
        old_terms, new_terms = _value_terms(s["old_title"]), _value_terms(s["new_title"])
        old_only, new_only = old_terms - new_terms, new_terms - old_terms
        if not old_only:
            continue
        for name in CHECKED_SECTIONS:
            if name not in sections:
                continue
            text = tokens(sections[name]["content"])
            if text & old_only and not text & new_only:
                warnings.append({
                    "code": "inconsistent_state", "section": name, "entry_id": s["new_id"],
                    "message": f"section '{name}' mentions {sorted(text & old_only)} from superseded decision "
                               f"{s['old_id']} ('{s['old_title']}'), but not the active decision {s['new_id']} "
                               f"('{s['new_title']}') on '{s['topic']}'"})

    n_open = conn.execute("SELECT count(*) FROM conflicts WHERE project_id = ? AND status = 'open'",
                          (project_id,)).fetchone()[0]
    if n_open:
        warnings.append({"code": "open_conflicts", "count": n_open,
                         "message": f"{n_open} unresolved knowledge conflict(s); state may rest on an undecided topic"})
    return warnings
