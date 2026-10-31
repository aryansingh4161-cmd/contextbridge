"""docs/ACCEPTANCE.md must list all 24 acceptance tests, and every test it references must exist."""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_every_acceptance_test_is_mapped_to_real_tests() -> None:
    doc = (ROOT / "docs" / "ACCEPTANCE.md").read_text(encoding="utf-8")
    rows = {int(m.group(1)): m.group(0) for m in re.finditer(r"^\| (\d+) \|.*$", doc, re.M)}
    assert sorted(rows) == list(range(1, 25)), "all 24 acceptance tests are listed"
    for n, row in rows.items():
        refs = re.findall(r"`(tests/[\w/]+\.py)::(\w+)`", row)
        assert refs, f"acceptance test {n} has no automated test"
        for path, name in refs:
            tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
            names = {f.name for f in ast.walk(tree) if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))}
            assert name in names, f"acceptance test {n}: {path}::{name} does not exist"
