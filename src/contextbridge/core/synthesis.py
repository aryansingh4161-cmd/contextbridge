"""Deterministic synthesis helpers (BLUEPRINT §7.3): topic-key normalization, text
normalization, near-duplicate similarity, similar-key suggestions. No LLM, no network."""

from __future__ import annotations

import difflib
import hashlib
import re
import unicodedata

TOPIC_KEY_RE = re.compile(r"^[a-z0-9_]+(\.[a-z0-9_]+)*$")
TOPIC_KEY_MAX = 120

# Kinds where a topic should have exactly one current answer. A second active entry on the
# same topic is either an explicit supersession or a conflict. Other kinds (bug, fix, lesson,
# fact, ...) legitimately have many active entries per topic; they are only deduplicated.
# (Change from spec §5.2, which applied this to every kind; see BLUEPRINT §7.3.)
SINGULAR_KINDS = frozenset({"decision", "requirement", "constraint", "convention", "preference"})

_WORD = re.compile(r"[a-z0-9]+")


def normalize_topic_key(raw: str) -> str:
    """'Backend / Framework' -> 'backend_framework'; 'Auth.Token Storage' -> 'auth.token_storage'."""
    s = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode("ascii").lower().strip()
    parts = [re.sub(r"[^a-z0-9]+", "_", seg).strip("_") for seg in s.split(".")]
    return ".".join(p for p in parts if p)


def is_valid_topic_key(key: str) -> bool:
    return bool(TOPIC_KEY_RE.match(key)) and len(key) <= TOPIC_KEY_MAX


def normalize_text(text: str) -> str:
    return " ".join(_WORD.findall(text.lower()))


def tokens(text: str) -> frozenset[str]:
    return frozenset(_WORD.findall(text.lower()))


def norm_hash(title: str, body: str) -> str:
    return hashlib.sha256(normalize_text(f"{title} {body}").encode()).hexdigest()[:16]


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def similarity(title_a: str, body_a: str, title_b: str, body_b: str) -> float:
    return jaccard(tokens(f"{title_a} {body_a}"), tokens(f"{title_b} {body_b}"))


def similar_topic_keys(key: str, existing: set[str], limit: int = 5) -> list[str]:
    """Existing keys a model may have meant (to avoid near-duplicate keys)."""
    if key in existing:
        return []
    last = key.rsplit(".", 1)[-1]
    scored: list[tuple[float, str]] = []
    for k in existing:
        ratio = difflib.SequenceMatcher(None, key, k).ratio()
        if ratio >= 0.8 or k.rsplit(".", 1)[-1] == last:
            scored.append((ratio, k))
    return [k for _, k in sorted(scored, reverse=True)[:limit]]
