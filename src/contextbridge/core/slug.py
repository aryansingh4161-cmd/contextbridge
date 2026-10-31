"""Project slug generation.

A slug is the stable handle every tool call uses (`project="sih-docverify"`), so it must be
predictable for a model to guess from a project name, and must never change once created.
Constraint enforced by the DB: ^[a-z0-9][a-z0-9-]{1,62}$ (2–63 chars).
"""

from __future__ import annotations

import re
import unicodedata

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
MAX_LEN = 63


def is_valid_slug(slug: str) -> bool:
    return bool(SLUG_RE.match(slug))


def make_slug(name: str, existing: set[str]) -> str:
    """Derive a unique slug from a human project name.

    Args:
        name: the project name as the user typed it, e.g. "SIH Document Verification System",
              "Portfolio v2 (Next.js)", "डॉक्यूमेंट सत्यापन", "  ".
        existing: slugs already in use; the result must not be in this set.

    Returns:
        A slug matching SLUG_RE that is not in `existing`.

    Raises:
        ValueError: if no usable slug can be produced (the caller turns this into a clear
        "please pass slug explicitly" error).

    Policy:
      1. Accents are stripped via NFKD ("Café" -> "cafe"). Scripts with no ASCII decomposition
         (e.g. Devanagari) leave too little behind -> ValueError, so the caller asks for an
         explicit slug instead of inventing one the user would never guess.
      2. Collisions get "-2", "-3", ... until free.
      3. Length is capped at MAX_LEN including the suffix; never ends on "-".
      4. Fewer than 2 usable characters -> ValueError.
    """
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    base = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")
    if len(base) < 2:
        raise ValueError(f"'{name.strip()[:40]}' has fewer than 2 usable ASCII letters/digits")
    candidate = base[:MAX_LEN].rstrip("-")
    n = 2
    while candidate in existing:
        suffix = f"-{n}"
        candidate = base[: MAX_LEN - len(suffix)].rstrip("-") + suffix
        n += 1
    return candidate
