# ContextBridge

Personal memory infrastructure for AI-assisted work: a local-first, provider-independent
project memory that AI assistants read and write over [MCP](https://modelcontextprotocol.io).

**The promise:** no matter when or how an AI conversation ends, ContextBridge preserves as much
of your project's accumulated knowledge and development progress as was made available to it,
and tells you exactly what it has and what it is missing.

It does **not** promise to prevent usage or context limits from interrupting a conversation;
no supported mechanism can do that. It makes interruptions cheap: work is saved continuously,
the unsaved window is always visible, and a handoff is one command away.

## What it does

- **Three separate memories per project:** Project Knowledge (decisions with their reasons,
  rejected approaches, requirements, bugs, lessons, with full history and supersession), Live State
  (what is true now: stack, done, in progress, broken, tasks), and generated Session Handoffs.
- **Saves as you work:** the assistant records knowledge and state through tools while it still has
  the conversation in mind; duplicates merge, contradictions become explicit conflicts (never a silent
  winner), secrets are redacted on every write and export.
- **Handoffs in seconds, without an AI:** `prepare_handoff` / `ctxb handoff` assembles a budgeted
  handoff file (quick ≈ 3k, standard ≈ 10k, detailed ≈ 30k tokens) or a complete multi-file knowledge
  package from *all* stored history, with an "Evolution" section, an honest coverage report and a
  completeness check. Fits Claude's upload limits (splitting, `.txt`, file counts).
- **Resume anywhere:** a new chat, another Claude account, Claude Code or Claude Desktop calls
  `get_context` and continues; a client without the connector can just attach the handoff file.
- **Budget monitor (Claude Code):** a status line (`ctx 72% · 5h 41% · 7d 63% · ckpt 12m ago`),
  automatic checkpoint requests at 80/85/90/95 %, transcript archiving before compaction, and an
  emergency handoff when a session dies with unsaved work.
- **Import & migrate:** old handoff files, notes, Claude Code transcripts, Claude data exports (staged,
  treated as untrusted data); full export/import between Macs with ids and history preserved.
- **Private by design:** one SQLite file in `~/.contextbridge` (mode 700/600), no network, no API key,
  no LLM calls, nothing leaves your Mac unless you upload a file yourself.

## Quick start (macOS)

```bash
brew install uv
git clone https://github.com/aryansingh4161-cmd/contextbridge.git ~/contextbridge
uv tool install ~/contextbridge
ctxb init && ctxb doctor
ctxb install-claude-code        # Claude Code: MCP server, hooks, status line (backs up first)
ctxb install-claude-desktop     # Claude Desktop: then quit (⌘Q) and reopen it
```

Then ask Claude: *"Create a ContextBridge project called My App: …"*, work as usual, and when you
switch: *"Prepare a complete handoff, I'm switching accounts."* In a fresh chat: *"Load my My App
context and continue where I left off."* Full guide: **[docs/INSTALL.md](docs/INSTALL.md)**.

## Documentation

| | |
|---|---|
| [INSTALL.md](docs/INSTALL.md) | Step-by-step setup for macOS, connecting clients, uninstalling |
| [TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | Symptoms → causes → fixes |
| [TOOLS.md](docs/TOOLS.md) | Every MCP tool, prompt and resource (generated from the server) |
| [FORMAT.md](docs/FORMAT.md) | Handoff package and migration archive format (v1) |
| [LIMITATIONS.md](docs/LIMITATIONS.md) | What is not possible or not verified, and the design response |
| [ACCEPTANCE.md](docs/ACCEPTANCE.md) | The 24 acceptance tests and where each is verified |
| [BLUEPRINT.md](docs/BLUEPRINT.md) | Architecture, verified facts, decisions |
| [PROGRESS.md](PROGRESS.md) | Build log, milestone by milestone |

## Development

```bash
uv sync
uv run pytest          # unit, integration (real stdio server), acceptance (incl. SIGKILL tests)
uv run mypy            # strict
uv run python scripts/gen_tools_md.py   # regenerate docs/TOOLS.md after changing tools
```

Python 3.14, the official `mcp` SDK, SQLite (WAL, FTS5). On an iCloud-synced Desktop, keep the
virtualenv in `.venv.nosync/` (see TROUBLESHOOTING).

License: MIT.
