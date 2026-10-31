"""Secret redaction (spec §9). Runs on every write and every export.

Pattern rules apply everywhere, including inside code fences (a key pasted in a snippet is
still a key). The high-entropy heuristic is skipped inside fences and on pure hex (git hashes,
sha256 digests), to limit false positives. Values are never logged or returned, only types.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

# Order matters: specific provider patterns first, generic assignments last.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----")),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}")),
    ("openai_key", re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_\-]{20,}")),
    ("github_token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})")),
    ("slack_token", re.compile(r"\bxox[abposr]-[A-Za-z0-9\-]{10,}")),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("google_api_key", re.compile(r"\bAIza[A-Za-z0-9_\-]{35}\b")),
    ("stripe_key", re.compile(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{20,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}")),
    ("url_credentials", re.compile(r"(?<=://)[^\s:/@]+:[^\s@/]+(?=@)")),
    # KEY=value / key: "value" where the key name looks secret. Only the value is replaced.
    ("secret_assignment", re.compile(
        r"(?i)(?P<k>\b[A-Z0-9_]*(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|"
        r"private[_-]?key|client[_-]?secret|auth(?![a-z]))[A-Z0-9_]*\b\s*[:=]\s*)"
        r"(?P<q>[\"']?)(?P<v>[^\s\"',;]{4,})(?P=q)")),
]

_FENCE = re.compile(r"```[\s\S]*?(?:```|\Z)")
_CANDIDATE = re.compile(r"[A-Za-z0-9+/=_\-]{24,}")
_HEX = re.compile(r"^[0-9a-fA-F]+$")
ENTROPY_THRESHOLD = 4.2  # bits/char; hex maxes out at 4.0, so hashes never trip this


@dataclass(frozen=True)
class Redaction:
    type: str
    count: int


def shannon_entropy(s: str) -> float:
    counts = Counter(s)
    n = len(s)
    return -sum(c / n * math.log2(c / n) for c in counts.values())


def _is_high_entropy_token(tok: str) -> bool:
    if _HEX.match(tok) or "[REDACTED" in tok:
        return False
    has_alpha = any(c.isalpha() for c in tok)
    has_digit = any(c.isdigit() for c in tok)
    return has_alpha and has_digit and shannon_entropy(tok) >= ENTROPY_THRESHOLD


_TOKEN_COUNT_KEY = re.compile(r"(?i)tokens\b|tokens_|token_(?:count|limit|budget|usage)")


def _is_token_count(key: str, value: str) -> bool:
    """`max_tokens: 4096`, `estimated_tokens: 1140` are LLM token counts, not credentials."""
    return value.isdigit() and bool(_TOKEN_COUNT_KEY.search(key))


def redact(text: str, *, entropy: bool = True) -> tuple[str, list[Redaction]]:
    """Return (redacted_text, findings). Idempotent: redacting twice changes nothing."""
    if not text:
        return text, []
    found: Counter[str] = Counter()

    for rtype, pat in _PATTERNS:
        def repl(m: re.Match[str], rtype: str = rtype) -> str:
            if rtype == "secret_assignment" and _is_token_count(m.group("k"), m.group("v")):
                return m.group(0)
            found[rtype] += 1
            if rtype == "secret_assignment":
                return f"{m.group('k')}{m.group('q')}[REDACTED:{rtype}]{m.group('q')}"
            return f"[REDACTED:{rtype}]"
        text = pat.sub(repl, text)

    if entropy:
        fences = [(m.start(), m.end()) for m in _FENCE.finditer(text)]

        def in_fence(pos: int) -> bool:
            return any(a <= pos < b for a, b in fences)

        out: list[str] = []
        last = 0
        for m in _CANDIDATE.finditer(text):
            if not in_fence(m.start()) and _is_high_entropy_token(m.group()):
                out.append(text[last:m.start()])
                out.append("[REDACTED:high_entropy]")
                found["high_entropy"] += 1
                last = m.end()
        out.append(text[last:])
        text = "".join(out)

    return text, [Redaction(t, c) for t, c in sorted(found.items())]
