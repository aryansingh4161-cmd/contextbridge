"""`prepare_handoff`: save final updates → collect ALL knowledge → rank within budget → render →
validate → redact → size/split against the upload profile → write files + manifest + sidecar →
snapshot (component 'handoff') + `handoff_runs` row (spec §6, §8).

Deterministic and model-free (P3): the only model input is the optional `final_updates`
checkpoint. Everything else is assembled from what is already in the database.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from .checkpoints import CheckpointIn, checkpoint
from .config import Config
from .db.connection import now_iso, write_tx
from .errors import CBError
from .generation.collect import ProjectData, collect
from .generation.rank import select
from .generation.render import (Selection, handoff_md, knowledge_md, state_file_md, tasks_issues_md,
                                tech_reference_md, coverage)
from .generation.split import split_markdown
from .generation.tokens import estimate
from .generation.validate import validate
from .paths import DataPaths, ensure_private_dir, ensure_private_file
from .repo import repo_summary
from .security.redact import redact
from .snapshots import create_snapshot_tx

Mode = Literal["quick", "standard", "detailed", "knowledge_package"]
MODES: tuple[Mode, ...] = ("quick", "standard", "detailed", "knowledge_package")
FORMAT_VERSION = 1
PRIMARY = "01_START_HERE_handoff"


@dataclass
class Profile:
    name: str
    max_file_bytes: int
    max_files: int
    formats: list[str]
    preferred_ext: str
    max_total_tokens: int = 0  # 0 = not checked (no verified number exists for Claude Projects)


def load_profile(config: Config, name: str | None) -> Profile:
    name = name or str(config.get("defaults.profile", "conservative"))
    raw = config.get(f"profiles.{name}")
    if not isinstance(raw, dict):
        known = sorted((config.get("profiles") or {}).keys())
        raise CBError("invalid_input", f"unknown upload profile '{name}'", f"known profiles: {', '.join(known)}")
    return Profile(name=name, max_file_bytes=int(raw.get("max_file_bytes", 25_000_000)),
                   max_files=int(raw.get("max_files", 0)), formats=list(raw.get("formats", ["md", "txt"])),
                   preferred_ext=str(raw.get("preferred_ext", "md")),
                   max_total_tokens=int(raw.get("max_total_tokens", 0)))


def _budget(config: Config, level: str) -> int:
    return int(config.get(f"handoff.budgets.{level}", {"quick": 3000, "standard": 10000, "detailed": 30000}[level]))


def _companions(data: ProjectData, mode: Mode, gen_at: str) -> dict[str, str]:
    if mode != "knowledge_package":
        return {
            "02_project_knowledge": knowledge_md(
                data, ["requirements", "decisions", "rejected", "rules", "bugs", "milestones", "reference", "open"],
                include_evolution=True, include_obsolete=True, include_identity=True,
                title="Project knowledge (complete)", generated_at=gen_at),
            "03_current_state": state_file_md(data, gen_at),
        }
    return {
        "02_project_knowledge": knowledge_md(data, ["requirements", "rules", "open", "milestones"],
                                             include_evolution=False, include_obsolete=True, include_identity=True,
                                             title="Project knowledge", generated_at=gen_at),
        "03_current_state": state_file_md(data, gen_at, include_tasks=False),
        "04_decisions_and_evolution": knowledge_md(data, ["decisions", "rejected"], include_evolution=True,
                                                   include_obsolete=True, title="Decisions & evolution",
                                                   generated_at=gen_at),
        "05_tasks_and_issues": tasks_issues_md(data, gen_at),
        "06_technical_reference": tech_reference_md(data, gen_at),
    }


def build_primary(data: ProjectData, *, level: str, budget: int, package_type: str, gen_at: str,
                  companions: dict[str, str], stale_after_days: int,
                  topics: list[str] | None = None) -> tuple[str, Selection, dict[str, Any], list[str]]:
    """Render the budgeted primary file; returns (text, selection, validation report, warnings)."""
    cov = coverage(data)
    p = data.project
    latest = data.latest_session_id
    latest_tags = [e["tag"] for e in data.entries if latest is not None and e["session_id"] == latest
                   and e["status"] not in ("superseded", "outdated")][:15]
    fm: dict[str, Any] = {
        "contextbridge_format": FORMAT_VERSION, "package_type": package_type, "level": level,
        "project": p["name"], "project_slug": p["slug"], "generated_at": gen_at,
        "knowledge_version": p["knowledge_version"], "state_version": p["state_version"],
        "sources_covered": cov["captured"], "coverage_gaps": cov["gaps"],
        "estimated_tokens": 0, "completeness_score": 0.0,
        "complete": False,
    }
    note = ("02_project_knowledge holds every entry in full; 03_current_state the full Live State and tasks."
            if package_type == "session_handoff" else "See files 02–06 of this package.")

    def render(sel: Selection) -> str:
        return handoff_md(data, sel, fm, latest_session_tags=latest_tags, companion_note=note)

    sel, text, warnings = select(data, budget, level, render, topics)
    report = validate(data, text, companions, stale_after_days=stale_after_days)
    fm["estimated_tokens"] = estimate(text).est
    fm["completeness_score"] = report["score"]
    # A small file is never labelled complete (spec §8.1): only when nothing was left out of it.
    fm["complete"] = not sel.omitted and not report["missing"]
    text = render(sel)
    return text, sel, report, warnings


def _materialize(docs: list[tuple[str, str, str]], prof: Profile, ext: str,
                 warnings: list[str]) -> tuple[list[dict[str, Any]], dict[str, str], int]:
    """Redact each document, split it at heading boundaries to fit the profile, describe each file."""
    redacted_total = 0
    files: list[dict[str, Any]] = []
    out_texts: dict[str, str] = {}
    for name, role, text in docs:
        text, found = redact(text)
        redacted_total += sum(f.count for f in found)
        parts = split_markdown(text, lambda t: len(t.encode("utf-8")) <= prof.max_file_bytes)
        source_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if len(parts) > 1 and name == PRIMARY:
            warnings.append("the primary handoff exceeded the profile's file size and was split; "
                            "upload all its parts together")
        for i, part in enumerate(parts, 1):
            fname = f"{name}.{ext}" if len(parts) == 1 else f"{name}.part{i}.{ext}"
            b = part.encode("utf-8")
            if len(b) > prof.max_file_bytes:
                warnings.append(f"{fname} is {len(b)} bytes, over the {prof.max_file_bytes}-byte limit, and has no "
                                "safe split point (one indivisible block)")
            files.append({"name": fname, "role": role, "bytes": len(b), "est_tokens": estimate(part).to_dict(),
                          "sha256": hashlib.sha256(b).hexdigest(), "part": i, "of": len(parts),
                          "required": name == PRIMARY, "upload": True, "source": name}
                         | ({"source_sha256": source_sha} if len(parts) > 1 else {}))
            out_texts[fname] = part
    return files, out_texts, redacted_total


def _unique_dir(base: Path) -> Path:
    d, n = base, 2
    while d.exists():
        d = base.with_name(f"{base.name}-{n}")
        n += 1
    return d


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    ensure_private_file(path)


def prepare_handoff(conn: sqlite3.Connection, paths: DataPaths, config: Config, project_id: int, *,
                    mode: Mode = "standard", profile: str | None = None, fmt: str | None = None,
                    final_updates: CheckpointIn | None = None, near_duplicate_threshold: float = 0.85,
                    emergency_banner: str | None = None,
                    emergency_meta: dict[str, Any] | None = None) -> dict[str, Any]:
    if mode not in MODES:
        raise CBError("invalid_input", f"mode '{mode}' is invalid", f"one of {', '.join(MODES)}")
    prof = load_profile(config, profile)
    ext = (fmt or prof.preferred_ext).lower().lstrip(".")
    if ext not in ("md", "txt"):
        raise CBError("invalid_input", f"format '{ext}' is not supported", "md or txt")
    warnings: list[str] = []
    if ext not in prof.formats:
        warnings.append(f"'.{ext}' is not in profile '{prof.name}' formats {prof.formats}")

    cp_out = None
    if final_updates is not None:
        final_updates.kind = "handoff"
        cp_out = checkpoint(conn, project_id, final_updates, near_duplicate_threshold=near_duplicate_threshold)

    data = collect(conn, project_id)
    data.repo = repo_summary(data.project.get("linked_dir"))
    gen_at = now_iso()
    level = "standard" if mode == "knowledge_package" else mode
    package_type = "knowledge_package" if mode == "knowledge_package" else "session_handoff"
    companions = _companions(data, mode, gen_at)
    primary, sel, report, w = build_primary(
        data, level=level, budget=_budget(config, level), package_type=package_type, gen_at=gen_at,
        companions=companions, stale_after_days=int(config.get("synthesis.stale_after_days", 30)))
    warnings += w
    if emergency_banner:
        primary = primary.replace("# Instructions for the AI reading this", emergency_banner
                                  + "# Instructions for the AI reading this", 1)
        primary = primary.replace('package_type: "session_handoff"', 'package_type: "session_handoff"\nemergency: true', 1)

    # Redact every exported file (spec §9), then measure, split, and write.
    docs: list[tuple[str, str, str]] = [(PRIMARY, "handoff", primary)] + [
        (name, "knowledge" if "knowledge" in name or "decisions" in name or "reference" in name
         else "state" if "state" in name else "tasks", text) for name, text in companions.items()]
    files, out_texts, redacted_total = _materialize(docs, prof, ext, warnings)
    if prof.max_files and len(files) > prof.max_files and len(docs) > 2:
        # Consolidate companions into one file before falling back to a staged upload plan (spec §8.5.6).
        combined = "\n\n".join(text for _, _, text in docs[1:])
        docs = [docs[0], ("02_companion_files", "knowledge", combined)]
        files, out_texts, redacted_total = _materialize(docs, prof, ext, warnings)
        warnings.append(f"companion files were consolidated into 02_companion_files to fit profile "
                        f"'{prof.name}' ({prof.max_files} files per upload)")
    if redacted_total:
        warnings.append(f"{redacted_total} possible secret(s) were redacted from the export")

    primary_tokens = sum(f["est_tokens"]["est"] for f in files if f["source"] == PRIMARY)
    budget = _budget(config, level)
    if primary_tokens > budget:
        warnings.append(f"primary file is ~{primary_tokens} tokens, over the {level} budget of {budget}")

    upload = [f["name"] for f in files]
    plan: list[list[str]] = [upload]
    if prof.max_files and len(upload) > prof.max_files:
        plan = [upload[i:i + prof.max_files] for i in range(0, len(upload), prof.max_files)]
        warnings.append(f"{len(upload)} files exceed profile '{prof.name}' limit of {prof.max_files} per upload: "
                        f"upload batch 1 now ({', '.join(plan[0])}); the rest when the assistant asks for them")

    stamp = datetime.now().astimezone().strftime("%Y-%m-%d_%H%M")
    slug = data.project["slug"]
    ensure_private_dir(paths.exports)
    ensure_private_dir(paths.exports / slug)
    out_dir = _unique_dir(paths.exports / slug / f"{slug}_{mode}_{stamp}")
    ensure_private_dir(out_dir)
    for fname, text in out_texts.items():
        _write(out_dir / fname, text)

    sidecar = _sidecar(conn, project_id, data, gen_at)
    sidecar_text, _ = redact(json.dumps(sidecar, ensure_ascii=False, indent=1))
    _write(out_dir / "source_references.json", sidecar_text)
    total_tokens = {k: sum(f["est_tokens"][k] for f in files) for k in ("low", "est", "high")}
    if prof.max_total_tokens and total_tokens["est"] > prof.max_total_tokens:
        warnings.append(f"the package is ~{total_tokens['est']} tokens in total, over profile '{prof.name}' "
                        f"max_total_tokens={prof.max_total_tokens}: upload 01 first and add companions as needed")
    manifest = {
        "contextbridge_format": FORMAT_VERSION, "package_type": package_type, "level": mode,
        "project": {"name": data.project["name"], "slug": slug}, "generated_at": gen_at,
        "versions": {"knowledge": data.project["knowledge_version"], "state": data.project["state_version"]},
        "profile": prof.name, "format": ext,
        "load_order": [f["name"] for f in files],
        "files": files + [{"name": "source_references.json", "role": "sidecar", "upload": False,
                           "note": "machine-readable entries/ids/provenance for re-import (ctxb import --full)"}],
        "upload_plan": plan,
        "estimated_tokens_total": total_tokens,
        "coverage": {"sessions_captured": report["coverage"]["sessions_captured"],
                     "sessions_total": report["coverage"]["sessions_total"], "gaps": report["coverage"]["gaps"]},
        "validation": {"score": report["score"], "missing": report["missing"]},
        "complete": False if sel.omitted or report["missing"] else True,
        "omitted_from_primary": sel.omitted,
        "warnings": warnings,
    }
    if emergency_meta is not None:
        manifest["emergency"] = emergency_meta | {"note": "not AI-synthesized after the last checkpoint"}
    _write(out_dir / "00_manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")

    with write_tx(conn):
        snap_id, _ = create_snapshot_tx(conn, project_id, "handoff",
                                        label=f"{'emergency ' if emergency_banner else ''}{mode} handoff",
                                        reason=str(out_dir),
                                        handoff={"manifest": manifest, "primary": out_texts.get(f"{PRIMARY}.{ext}")
                                                 or "".join(t for n, t in out_texts.items() if n.startswith(PRIMARY))})
        conn.execute(
            "INSERT INTO handoff_runs(project_id, mode, profile, estimated_tokens_low, estimated_tokens_high,"
            " actual_chars, bytes, file_count, validation_score, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (project_id, ("emergency-" if emergency_banner else "") + mode, prof.name, total_tokens["low"], total_tokens["high"],
             sum(len(t) for t in out_texts.values()), sum(f["bytes"] for f in files), len(files),
             report["score"], gen_at))

    cp = data.latest_checkpoint
    gaps = report["coverage"]["gaps"]
    summary = [
        f"Where we stopped: {cp['where_stopped'] if cp else 'unknown (no checkpoint yet)'}",
        f"Next: {cp['next_action'] if cp else 'unknown'}",
        f"Package: {len(files)} file(s), ~{primary_tokens} tokens in the primary file "
        f"(~{total_tokens['est']} total), score {report['score']}"
        + (f"; {len(gaps)} coverage gap(s)" if gaps else "; no coverage gaps"),
    ]
    result: dict[str, Any] = {
        "directory": str(out_dir),
        "primary_file": str(out_dir / files[0]["name"]),
        "files": [{"path": str(out_dir / f["name"]), "bytes": f["bytes"], "est_tokens_range": f["est_tokens"],
                   "role": f["role"]} for f in files],
        "manifest_path": str(out_dir / "00_manifest.json"),
        "report": report, "summary_3_lines": summary, "warnings": warnings,
        "upload_plan": plan, "snapshot_id": snap_id, "omitted_from_primary": sel.omitted,
    }
    if cp_out is not None:
        result["final_checkpoint"] = {k: cp_out[k] for k in ("checkpoint_id", "session_id") if k in cp_out} | (
            {"item_results": cp_out["item_results"]} if "item_results" in cp_out else {})
    return result


def _sidecar(conn: sqlite3.Connection, project_id: int, data: ProjectData, gen_at: str) -> dict[str, Any]:
    """Everything needed to rebuild this project's memory in an empty DB (spec §8.4 round trip)."""
    def rows(sql: str) -> list[dict[str, Any]]:
        return [dict(r) for r in conn.execute(sql, (project_id,))]

    entries = [{k: v for k, v in e.items() if k != "tag"} for e in data.entries]
    project = dict(data.project)
    return {
        "contextbridge_format": FORMAT_VERSION, "kind": "source_references", "generated_at": gen_at,
        "project": project,
        "entries": entries,
        "conflicts": rows("SELECT * FROM conflicts WHERE project_id = ? ORDER BY id"),
        "live_state": rows("SELECT section, content, refs, version, updated_at FROM live_state WHERE project_id = ?"
                           " ORDER BY section"),
        "tasks": rows("SELECT * FROM tasks WHERE project_id = ? ORDER BY id"),
        "sessions": rows("SELECT * FROM sessions WHERE project_id = ? ORDER BY id"),
        "checkpoints": rows("SELECT * FROM checkpoints WHERE project_id = ? ORDER BY id"),
    }


def latest_handoff(conn: sqlite3.Connection, project_id: int) -> dict[str, Any] | None:
    from .snapshots import load_snapshot

    row = conn.execute("SELECT id FROM snapshots WHERE project_id = ? AND component = 'handoff'"
                       " ORDER BY id DESC LIMIT 1", (project_id,)).fetchone()
    if row is None:
        return None
    payload = load_snapshot(conn, project_id, int(row["id"]))
    return dict(payload.get("handoff") or {})
