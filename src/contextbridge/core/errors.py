"""Error type shared by the core, the MCP layer and the CLI.

Messages are short, specific and actionable (spec §14). They never contain memory content
or secrets, so they are safe to log and to return to a model.
"""

from __future__ import annotations

from typing import Literal

ErrorCode = Literal[
    "not_found",
    "invalid_input",
    "conflict",
    "provenance_violation",
    "too_large",
    "confirm_required",
    "db_busy",
    "internal",
]


class CBError(Exception):
    """A user-facing error with a stable machine-readable code."""

    def __init__(self, code: ErrorCode, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.code: ErrorCode = code
        self.message = message
        self.hint = hint

    def to_dict(self) -> dict[str, str]:
        out = {"code": self.code, "message": self.message}
        if self.hint:
            out["hint"] = self.hint
        return out

    def __str__(self) -> str:
        return f"{self.code}: {self.message}" + (f" ({self.hint})" if self.hint else "")
