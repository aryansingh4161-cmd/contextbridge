"""Offline token estimation (spec §10.3, BLUEPRINT §8).

There is no tokenizer here (no network, no API key), so estimates are heuristic and reported
as a range with a confidence label, never as an exact number:

- prose: ~3.6 chars/token, code fences / JSON: ~3.0 chars/token,
- non-ASCII characters (Devanagari, CJK, emoji …) cost far more: ~0.7 tokens each,
- `est` = base × safety factor (1.15), the number compared with budgets (errs high),
- range = [base × 0.85, base × 1.3].
"""

from __future__ import annotations

import math
from dataclasses import dataclass

PROSE_CPT = 3.6
CODE_CPT = 3.0
NON_ASCII_TPC = 0.7
SAFETY = 1.15


@dataclass(frozen=True)
class Estimate:
    low: int
    est: int
    high: int
    confidence: str = "low (heuristic, no tokenizer)"

    def to_dict(self) -> dict[str, object]:
        return {"low": self.low, "est": self.est, "high": self.high, "confidence": self.confidence}


def _base(text: str) -> float:
    prose = code = non_ascii = 0
    in_fence = False
    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        n_non = sum(1 for ch in line if ord(ch) > 127)
        non_ascii += n_non
        if in_fence:
            code += len(line) - n_non
        else:
            prose += len(line) - n_non
    return prose / PROSE_CPT + code / CODE_CPT + non_ascii * NON_ASCII_TPC


def estimate(text: str) -> Estimate:
    b = _base(text)
    return Estimate(low=math.floor(b * 0.85), est=math.ceil(b * SAFETY), high=math.ceil(b * 1.3))


def est_tokens(text: str) -> int:
    """The conservative point estimate used for budget decisions."""
    return estimate(text).est
