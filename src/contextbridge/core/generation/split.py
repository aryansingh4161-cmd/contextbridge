"""Split Markdown at logical boundaries, and merge it back byte for byte (spec §8.5 steps 5 and 8).

Parts are plain slices of the original text: no headers are added, so concatenating the parts
in order reproduces the input exactly. Cuts happen at the start of a heading line (`#`/`##` first,
`###` if a chunk is still too large), then, inside a still-oversized section, at blank lines between
blocks (paragraphs, list items) — never inside a code fence and never between table rows. If a single
indivisible block is larger than the limit, it becomes its own oversize part and the caller reports
it rather than cutting through a fence or table.
"""

from __future__ import annotations

from collections.abc import Callable


def _boundaries(text: str, max_level: int) -> list[int]:
    """Offsets where a heading of level <= max_level starts, outside code fences."""
    cuts: list[int] = []
    in_fence = False
    pos = 0
    for line in text.splitlines(keepends=True):
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
        elif not in_fence and line.startswith("#"):
            level = len(line) - len(line.lstrip("#"))
            if 1 <= level <= max_level and line[level:level + 1] in (" ", "\t"):
                cuts.append(pos)
        pos += len(line)
    return cuts


def _pieces(text: str, max_level: int) -> list[str]:
    cuts = [c for c in _boundaries(text, max_level) if c > 0]
    out, start = [], 0
    for c in cuts:
        out.append(text[start:c])
        start = c
    out.append(text[start:])
    return [p for p in out if p]


def _blocks(text: str) -> list[str]:
    """Last resort inside one oversized section: cut at blank lines between blocks (paragraphs, list
    items), never inside a code fence and never between table rows."""
    out, start, pos = [], 0, 0
    in_fence = False
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
        pos += len(line)
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        prev_table = line.lstrip().startswith("|")
        if (not in_fence and not line.strip() and nxt.strip() and not nxt.lstrip().startswith("|")
                and not prev_table and pos < len(text)):
            out.append(text[start:pos])
            start = pos
    out.append(text[start:])
    return [p for p in out if p]


def split_markdown(text: str, fits: Callable[[str], bool]) -> list[str]:
    """Greedy packing of heading-delimited pieces into parts for which `fits(part)` holds."""
    if fits(text):
        return [text]
    pieces: list[str] = []
    for p in _pieces(text, 2):
        if fits(p):
            pieces.append(p)
            continue
        for q in _pieces(p, 3):
            pieces.extend([q] if fits(q) else _blocks(q))
    parts: list[str] = []
    cur = ""
    for p in pieces:
        if cur and not fits(cur + p):
            parts.append(cur)
            cur = p
        else:
            cur += p
    if cur:
        parts.append(cur)
    assert "".join(parts) == text
    return parts


def merge_parts(parts: list[str]) -> str:
    return "".join(parts)
