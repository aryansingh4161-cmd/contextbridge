# ContextBridge

Personal memory infrastructure for AI-assisted work: a local-first, provider-independent
project memory that AI assistants read and write over MCP.

**The promise:** no matter when or how an AI conversation ends, ContextBridge preserves as much
of your project's accumulated knowledge and development progress as was made available to it,
and tells you exactly what it has and what it is missing.

It does **not** promise to prevent usage or context limits from interrupting a conversation;
no supported mechanism can do that. It makes interruptions cheap: work is saved continuously,
the unsaved window is always visible, and a handoff is one command away.

Status: early development. See `PROGRESS.md` and `docs/BLUEPRINT.md`.


## Install

See **[docs/INSTALL.md](docs/INSTALL.md)** (macOS, step by step): `uv tool install`, then
`ctxb install-claude-code` and `ctxb install-claude-desktop`. Formats: [docs/FORMAT.md](docs/FORMAT.md).
Known limits: [docs/LIMITATIONS.md](docs/LIMITATIONS.md). License: MIT.
