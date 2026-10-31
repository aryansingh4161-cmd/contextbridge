"""Split Markdown at logical boundaries, and merge it back byte for byte (spec §8.5 steps 5 and 8).

Parts are plain slices of the original text: no headers are added, so concatenating the parts
in order reproduces the input exactly. Cuts happen only at the start of a heading line
(`#`/`##` first, `###` if a chunk is still too large) and never inside a code fence. If a single
indivisible block is larger than the limit, it becomes its own oversize part and the caller
reports it rather than cutting through a fence or table.
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


def split_markdown(text: str, fits: Callable[[str], bool]) -> list[str]:
    """Greedy packing of heading-delimited pieces into parts for which `fits(part)` holds."""
    if fits(text):
        return [text]
    pieces: list[str] = []
    for p in _pieces(text, 2):
        pieces.extend([p] if fits(p) else _pieces(p, 3))
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
