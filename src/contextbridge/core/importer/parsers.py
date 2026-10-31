"""Deterministic parsers: source → candidates + archives. No LLM: kinds guessed from Markdown are
marked `kind_guessed` so the model (or user) can correct them on commit.

Candidate: {"type": "entry"|"section"|"task"|"checkpoint"|"conversation", "data": {...},
            "source_ref": str, "kind_guessed"?: bool}
Archive:   {"title", "text", "source_type", "external_key"?, "started_at"?, "ended_at"?,
            "candidate"?: index of the 'conversation' candidate that controls it}
"""

from __future__ import annotations

import json
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..errors import CBError
from ..generation.collect import KIND_PREFIX
from ..generation.render import SECTION_TITLES
from ..synthesis import normalize_topic_key
from .safety import decode

PREFIX_KIND = {v: k for k, v in KIND_PREFIX.items()}
TITLE_SECTION = {v.lower(): k for k, v in SECTION_TITLES.items()}
MAX_CANDIDATES = 500
TITLE_MAX = 300


@dataclass
class Parsed:
    source_type: str
    title: str
    candidates: list[dict[str, Any]] = field(default_factory=list)
    archives: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _title(text: str) -> str:
    t = re.sub(r"\s+", " ", text).strip().strip("#*-: ").strip()
    return (t[: TITLE_MAX - 1] + "…") if len(t) > TITLE_MAX else t


def _topic(*parts: str) -> str:
    key = normalize_topic_key(".".join(p for p in parts if p))
    return (key or "imported.note")[:120].rstrip("._")


# ---------------------------------------------------------------- kind guessing (Markdown / text)

_GUESS: list[tuple[str, re.Pattern[str]]] = [
    ("rejected_approach", re.compile(r"(?i)\b(rejected|ruled out|decided against|not using|won'?t use|abandoned)\b")),
    ("decision", re.compile(r"(?i)\b(decision|decided|we chose|chose|going with|we will use|we'll use)\b")),
    ("requirement", re.compile(r"(?i)\b(requirements?|must|shall|needs? to)\b")),
    ("constraint", re.compile(r"(?i)\b(constraints?|limitations?|budget|deadline|cannot|can'?t)\b")),
    ("bug", re.compile(r"(?i)\b(bugs?|broken|crash(es)?|error|fails?|failing)\b")),
    ("fix", re.compile(r"(?i)\b(fix(ed)?|workaround|resolved by)\b")),
    ("lesson", re.compile(r"(?i)\b(lessons?|learned|gotcha|note to self)\b")),
    ("risk", re.compile(r"(?i)\b(risks?|concern)\b")),
    ("open_question", re.compile(r"(?i)(\?\s*$|\b(open questions?|unclear|tbd|to decide)\b)")),
    ("convention", re.compile(r"(?i)\b(conventions?|naming|style guide|always use)\b")),
    ("preference", re.compile(r"(?i)\b(prefer(s|ence)?|i like)\b")),
    ("milestone", re.compile(r"(?i)\b(milestones?|shipped|released|launched)\b")),
    ("reference", re.compile(r"(?i)(https?://|\b(docs?|reference|link)\b)")),
]


def guess_kind(heading: str, body: str) -> str:
    for text in (heading, body[:200]):
        for kind, pat in _GUESS:
            if pat.search(text):
                return kind
    return "explanation"


def _entry_candidate(kind: str, topic: str, title: str, body: str, ref: str, *, guessed: bool,
                     rationale: str | None = None, importance: str = "important",
                     extra: dict[str, Any] | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {"kind": kind, "topic_key": topic, "title": _title(title) or "Imported note",
                            "body": body.strip(), "importance": importance}
    if kind in ("decision", "rejected_approach"):
        data["rationale"] = rationale or "(rationale not stated in the imported source)"
    elif rationale:
        data["rationale"] = rationale
    if extra:
        data |= extra
    c: dict[str, Any] = {"type": "entry", "data": data, "source_ref": ref}
    if guessed:
        c["kind_guessed"] = True
    return c


# ---------------------------------------------------------------- Markdown / text

def _sections(text: str) -> list[tuple[list[str], str]]:
    """[(heading path, body)] split at ATX headings outside code fences."""
    out: list[tuple[list[str], str]] = []
    path: list[str] = []
    buf: list[str] = []
    in_fence = False

    def flush() -> None:
        body = "".join(buf).strip()
        if body or path:
            out.append((list(path), body))
        buf.clear()

    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
        m = None if in_fence else re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if m:
            flush()
            level = len(m.group(1))
            path = path[: level - 1] + [m.group(2)]
            continue
        buf.append(line)
    flush()
    return out


def parse_markdown(text: str, name: str, source_type: str = "markdown") -> Parsed:
    p = Parsed(source_type=source_type, title=name)
    secs = _sections(text)
    if len(secs) <= 1 and not (secs and secs[0][0]):
        # no headings: split into paragraphs
        paras = [x.strip() for x in re.split(r"\n\s*\n", text) if x.strip()]
        secs = [([], para) for para in paras]
    for i, (path, body) in enumerate(secs):
        if not body and not path:
            continue
        heading = path[-1] if path else body.splitlines()[0]
        if not body:
            continue  # a heading with nothing under it (its children are separate sections)
        kind = guess_kind(heading, body)
        title = heading if path else body.splitlines()[0]
        topic = _topic("imported", normalize_topic_key(path[-1])[:60] if path else f"note_{i + 1}")
        p.candidates.append(_entry_candidate(kind, topic, title, body, f"{name} § {' > '.join(path) or f'¶{i + 1}'}",
                                             guessed=True))
    p.archives.append({"title": name, "text": text, "source_type": source_type})
    if not p.candidates:
        p.notes.append("no content found to stage; the text is still archived on commit")
    return p


# ---------------------------------------------------------------- ContextBridge packages

_TAG = re.compile(r"^- \[(?P<k>[A-Z]+)-(?P<id>\d{4,}) · (?P<prov>[a-z_]+) · (?P<date>[\d-]+)(?: · (?P<st>[a-z]+))?\] "
                  r"(?:\*\*(?P<tb>.+?)\*\*|(?P<t>.+?))(?P<marks> — .*?)?\s*$")
_TASK = re.compile(r"^- \[T-(?P<id>\d{4,}) · (?P<st>[a-z_]+)(?: · P(?P<p>\d))?\] (?P<title>.+?)"
                   r"(?: \(depends on [^)]*\))?(?: — done when: (?P<exp>.+))?$")


def parse_package(path: Path) -> Parsed:
    if path.is_dir():
        side = path / "source_references.json"
        if side.is_file():
            return _from_sidecar(json.loads(decode(side.read_bytes(), side.name)), path.name)
        files = sorted(x for x in path.iterdir() if x.suffix in (".md", ".txt") and x.name[:2].isdigit())
        if not files:
            raise CBError("invalid_input", f"{path} has no package files")
        text = "".join(decode(f.read_bytes(), f.name) for f in files)
        return _from_package_text(text, path.name)
    text = decode(path.read_bytes(), path.name)
    if path.suffix == ".json":
        obj = json.loads(text)
        if isinstance(obj, dict) and obj.get("kind") == "source_references":
            return _from_sidecar(obj, path.name)
        raise CBError("invalid_input", f"{path.name} is not a ContextBridge sidecar")
    return _from_package_text(text, path.name)


def _orig(e: dict[str, Any]) -> str:
    return f"package: originally {KIND_PREFIX.get(e['kind'], 'E')}-{int(e['id']):04d}, {e['provenance']}"


def _from_sidecar(side: dict[str, Any], name: str) -> Parsed:
    if side.get("contextbridge_format") != 1:
        raise CBError("invalid_input", "unsupported sidecar format")
    p = Parsed(source_type="package", title=name)
    skipped = 0
    for e in side.get("entries", []):
        if e.get("status") in ("superseded", "outdated"):
            skipped += 1
            continue
        alts = e.get("alternatives")
        p.candidates.append(_entry_candidate(
            e["kind"], e["topic_key"], e["title"], e.get("body") or "", _orig(e), guessed=False,
            rationale=e.get("rationale"), importance=e.get("importance", "important"),
            extra={"alternatives": alts if isinstance(alts, list) else (json.loads(alts) if alts else None),
                   "status": e["status"] if e["status"] in ("active", "unverified", "resolved", "rejected")
                   else "active"}))
    for s in side.get("live_state", []):
        if s.get("content"):
            p.candidates.append({"type": "section", "data": {"section": s["section"], "content": s["content"]},
                                 "source_ref": f"package: live state '{s['section']}'"})
    for t in side.get("tasks", []):
        if t.get("status") != "done":
            p.candidates.append({"type": "task", "data": {k: t.get(k) for k in
                                                          ("title", "status", "priority", "expected_outcome", "notes")},
                                 "source_ref": f"package: task T-{int(t['id']):04d}"})
    cps = side.get("checkpoints") or []
    if cps:
        cp = cps[-1]
        p.candidates.append({"type": "checkpoint", "data": {k: cp[k] for k in ("summary", "where_stopped",
                                                                                "next_action")},
                             "source_ref": f"package: last checkpoint {cp.get('created_at', '')}"})
    if skipped:
        p.notes.append(f"{skipped} superseded/outdated entr{'y' if skipped == 1 else 'ies'} skipped (history only)")
    return p


def _from_package_text(text: str, name: str) -> Parsed:
    """A ContextBridge handoff Markdown file without its sidecar (e.g. the user only has 01_*.md)."""
    if "contextbridge_format" not in text[:2000] and "# 1. Where we stopped" not in text:
        return parse_markdown(text, name)
    p = Parsed(source_type="package", title=name)
    seen: set[str] = set()
    section = ""
    sub = ""
    lines = text.splitlines()
    i = 0
    state: dict[str, list[str]] = {}
    where = nxt = summary = ""
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("# "):
            section, sub = ln[2:].strip(), ""
        elif ln.startswith("## "):
            sub = ln[3:].strip()
        elif section.startswith("4. Current state") and sub.lower() in TITLE_SECTION and not ln.startswith("_"):
            state.setdefault(TITLE_SECTION[sub.lower()], []).append(ln)
        if ln.startswith("**Where we stopped:**"):
            where = ln.split(":**", 1)[1].strip()
        elif ln.startswith("**Session summary:**"):
            summary = ln.split(":**", 1)[1].strip()
        elif ln.startswith("**Next:**"):
            nxt = ln.split(":**", 1)[1].strip()
        m = _TAG.match(ln)
        if m and not section.startswith("6.") and m.group("k") in PREFIX_KIND:
            key = f"{m.group('k')}-{m.group('id')}"
            body_lines, rationale = [], None
            j = i + 1
            while j < len(lines) and not lines[j].startswith(("- [", "# ", "## ")):
                s = lines[j]
                if s.startswith("  Why: "):
                    rationale = s[7:].rstrip()
                elif not s.startswith(("  Alternatives considered: ", "  Source: ")):
                    body_lines.append(s)
                j += 1
            if key not in seen:
                seen.add(key)
                kind = PREFIX_KIND[m.group("k")]
                title = m.group("tb") or m.group("t") or ""
                marks = m.group("marks") or ""
                p.candidates.append(_entry_candidate(
                    kind, _topic("imported", kind, title[:40]), title, "\n".join(body_lines).strip(),
                    f"package file {name}: originally {key}, {m.group('prov')}", guessed=False,
                    rationale=rationale, importance="critical" if "**critical**" in marks else "important"))
            i = j
            continue
        t = _TASK.match(ln)
        if t:
            p.candidates.append({"type": "task", "data": {
                "title": t.group("title"), "status": t.group("st") if t.group("st") in
                ("todo", "in_progress", "blocked", "done", "postponed") else "todo",
                "priority": int(t.group("p")) if t.group("p") else None, "expected_outcome": t.group("exp")},
                "source_ref": f"package file {name}: T-{t.group('id')}"})
        i += 1
    for sec, body in state.items():
        content = "\n".join(body).strip()
        if content:
            p.candidates.append({"type": "section", "data": {"section": sec, "content": content},
                                 "source_ref": f"package file {name}: state '{sec}'"})
    if where and nxt:
        p.candidates.append({"type": "checkpoint", "data": {"summary": summary or "imported from a handoff file",
                                                             "where_stopped": where, "next_action": nxt},
                             "source_ref": f"package file {name}"})
    p.notes.append("parsed from a handoff file without its sidecar: topic keys are new (imported.*), "
                   "superseded history (section 6) is not re-imported")
    p.archives.append({"title": name, "text": text, "source_type": "package"})
    return p


# ---------------------------------------------------------------- Claude Code transcripts (verified 2026-10-31)

def parse_claude_code_transcript(text: str, name: str) -> Parsed:
    """JSONL, one record per line. Verified on Claude Code 2.1.316: records with type 'user' /
    'assistant' carry message.role and message.content (string, or blocks of type text, thinking,
    tool_use, tool_result). Other record types are metadata. Sidechains (subagents), thinking and
    tool results are skipped; tool calls are kept as one-line markers. Unknown shapes are skipped."""
    p = Parsed(source_type="transcript_claude_code", title=name)
    out: list[str] = []
    session_id = title = None
    first = last = None
    msgs = bad = 0
    summaries: list[tuple[str, str]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            bad += 1
            continue
        if not isinstance(rec, dict):
            continue
        session_id = session_id or rec.get("sessionId")
        if rec.get("type") == "ai-title" and isinstance(rec.get("aiTitle"), str):
            title = rec["aiTitle"]
        if rec.get("type") not in ("user", "assistant") or rec.get("isSidechain"):
            continue
        msg = rec.get("message")
        if not isinstance(msg, dict):
            continue
        ts = rec.get("timestamp") if isinstance(rec.get("timestamp"), str) else None
        first, last = first or ts, ts or last
        content = msg.get("content")
        parts: list[str] = []
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for b in content:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text" and isinstance(b.get("text"), str):
                    parts.append(b["text"])
                elif b.get("type") == "tool_use":
                    parts.append(f"[tool call: {b.get('name', '?')}]")
        body = "\n".join(x for x in parts if x.strip()).strip()
        if not body:
            continue
        role = msg.get("role") or rec["type"]
        if rec.get("isCompactSummary"):
            summaries.append((ts or "", body))
            role = "summary (compacted context)"
        out.append(f"### {role} · {ts or ''}\n\n{body}\n")
        msgs += 1
    if msgs == 0:
        raise CBError("invalid_input", f"{name}: no Claude Code messages found",
                      "expected a ~/.claude/projects/<dir>/<session>.jsonl transcript")
    label = title or f"Claude Code session {str(session_id)[:8]}"
    p.title = label
    p.archives.append({"title": label, "text": "\n".join(out), "source_type": "transcript_claude_code",
                       "external_key": session_id, "started_at": first, "ended_at": last})
    for ts, s in summaries:
        p.candidates.append(_entry_candidate("explanation", _topic("imported", "session_summary"),
                                             f"Compacted summary of '{label}' ({ts[:10]})", s,
                                             f"transcript {name} (compact summary {ts})", guessed=False,
                                             importance="optional"))
    p.notes.append(f"{msgs} messages archived (thinking, tool results and subagent sidechains skipped)")
    if bad:
        p.notes.append(f"{bad} unparseable line(s) skipped")
    return p


# ---------------------------------------------------------------- Claude data export (format NOT yet verified)

def parse_claude_export(path: Path, max_member_bytes: int) -> Parsed:
    """Tolerant parser for the claude.ai data export. The format has NOT been verified against a
    real export yet (docs/LIMITATIONS.md); it accepts `conversations.json` (directly or inside the
    export ZIP) as a list of conversations with `chat_messages` (or `messages`) whose items have
    `sender`/`role` and `text` or `content[].text`. Anything else is refused, never guessed."""
    if path.suffix == ".zip":
        try:
            with zipfile.ZipFile(path) as z:
                member = next((i for i in z.infolist() if Path(i.filename).name == "conversations.json"), None)
                if member is None:
                    raise CBError("invalid_input", "no conversations.json in this ZIP",
                                  f"files: {', '.join(i.filename for i in z.infolist()[:10])}")
                if member.file_size > max_member_bytes:
                    raise CBError("too_large", f"conversations.json is {member.file_size} bytes uncompressed")
                raw = z.read(member)
        except zipfile.BadZipFile as exc:
            raise CBError("invalid_input", f"{path.name} is not a valid ZIP") from exc
    else:
        raw = path.read_bytes()
    try:
        convs = json.loads(decode(raw, "conversations.json"))
    except ValueError as exc:
        raise CBError("invalid_input", "conversations.json is not valid JSON") from exc
    if not isinstance(convs, list) or not convs or not all(isinstance(c, dict) for c in convs):
        raise CBError("invalid_input", "unrecognized Claude export: expected a list of conversations",
                      "this parser has not been verified against a real export yet; please report the structure")
    p = Parsed(source_type="claude_export", title=path.name)
    for c in convs[:MAX_CANDIDATES]:
        msgs = c.get("chat_messages", c.get("messages"))
        if not isinstance(msgs, list):
            raise CBError("invalid_input", "unrecognized Claude export: a conversation has no message list",
                          f"conversation keys: {', '.join(sorted(c))[:200]}")
        lines = []
        for m in msgs:
            if not isinstance(m, dict):
                continue
            text: str = m["text"] if isinstance(m.get("text"), str) else ""
            if not text and isinstance(m.get("content"), list):
                text = "\n".join(b.get("text", "") for b in m["content"] if isinstance(b, dict)
                                 and isinstance(b.get("text"), str))
            if text.strip():
                role = m.get("sender") or m.get("role") or "?"
                lines.append(f"### {role} · {m.get('created_at', '')}\n\n{text.strip()}\n")
        name = str(c.get("name") or c.get("title") or "untitled")[:200]
        idx = len(p.candidates)
        p.candidates.append({"type": "conversation", "data": {
            "name": name, "messages": len(lines), "created_at": c.get("created_at"),
            "updated_at": c.get("updated_at")}, "source_ref": f"claude export: conversation {c.get('uuid', '')}"})
        p.archives.append({"title": name, "text": "\n".join(lines), "source_type": "claude_export",
                           "external_key": c.get("uuid"), "started_at": c.get("created_at"),
                           "ended_at": c.get("updated_at"), "candidate": idx})
    if len(convs) > MAX_CANDIDATES:
        p.notes.append(f"only the first {MAX_CANDIDATES} of {len(convs)} conversations were staged")
    p.notes.append("select the conversations that belong to this project; each selected one is archived "
                   "and counted as a transcript session (format unverified: check the result)")
    return p
