# Troubleshooting

Start with **`ctxb doctor`**: it checks the data folder and file permissions, the database
(integrity, journal mode, migrations, checksums, search index), backups, and whether Claude Desktop,
Claude Code and the SessionStart hook are configured. Each line is ✓ (ok), ! (warning), ✗ (problem)
or · (information). Run commands in Terminal, or in Claude Code with `!` in front.

| Symptom | Likely cause | Fix |
|---|---|---|
| `ctxb: command not found` | `~/.local/bin` isn't on your PATH | `uv tool update-shell`, then close and reopen Terminal |
| Claude Desktop doesn't know ContextBridge | Desktop wasn't fully restarted, or the config entry is missing | Quit with ⌘Q (closing the window isn't enough) and reopen. `ctxb doctor` should show "client: Claude Desktop: contextbridge configured"; if not, `ctxb install-claude-desktop` |
| Desktop: the server fails to start | The program moved or was uninstalled | Check `~/Library/Logs/Claude/mcp-server-contextbridge.log`; reinstall with `uv tool install --reinstall ~/contextbridge`, then `ctxb install-claude-desktop` |
| Claude Desktop uses the tools but forgets the habits (no checkpoints) | Desktop doesn't show server instructions to the model (verified) | The habits are repeated in tool results; for a Desktop Project also paste `ctxb snippet desktop <slug>` into its instructions |
| `claude mcp list` doesn't show contextbridge | Not installed for Claude Code | `ctxb install-claude-code` |
| Claude Code doesn't load the project automatically | The folder isn't linked | `ctxb link <slug> <folder>`; the hook loads the project linked to the folder you start Claude Code in (or a parent of it) |
| Status line shows `5h – · 7d –` | Normal: usage limits are only reported for Pro/Max/Ultra plans, and only after Claude's first reply in a session | Nothing to do; `–` means "not reported", never 0 |
| Status line shows `no linked project` | The current folder isn't linked | `ctxb link <slug> <folder>` |
| `ModuleNotFoundError: contextbridge` when developing in a folder on Desktop/Documents | iCloud marks files hidden and Python 3.14 skips hidden `.pth` files | Keep the dev venv in `.venv.nosync/` (symlinked as `.venv`), or work outside iCloud folders. Real installs use `uv tool install`, which is outside iCloud |
| `import_context`: "outside the folders imports may read" | The AI may only import from `[import] allowed_dirs` (Desktop, Documents, Downloads by default) | Add the folder to `allowed_dirs` in `~/.contextbridge/config.toml`, or run `ctxb import <file> --type … --project …` yourself |
| `import_context`: "files inside hidden folders can't be imported" | Safety rule (e.g. `~/.ssh`) | Copy the file to a normal folder first if you really mean it |
| A transcript import says "too_large" | Over `[import] max_transcript_bytes` (100 MB) | Raise it in config.toml |
| "staging N not found / expired" | Staged imports expire after 24 h (`[import] staging_ttl_hours`) | Stage the file again |
| `database is locked` / `db_busy` | Another process held a long write | Retry; ContextBridge waits 5 s (`[database] busy_timeout_ms`). Never keep the database inside a cloud-synced folder |
| Handoff warns "files exceed profile … limit" | The package has more files than the upload profile allows | Upload batch 1 now (listed in the warning and in `00_manifest.json` `upload_plan`), the rest when asked; or use `--profile claude_project` |
| Split files `…part1.txt`, `…part2.txt` | A file was bigger than the profile's size limit | Upload all parts; `ctxb merge <package folder>` rebuilds and verifies the original |
| Handoff says `complete: false` | Something was shortened for the token budget, or a check didn't pass | Read the "Not in this file" note and the report's `missing` list; use a larger mode (`detailed`) or the companion files |
| Wrong or unwanted memory | | `ctxb delete <slug> --entry ID --yes` (recoverable), `ctxb purge <slug> --entry ID --yes` (permanent, incl. snapshots and backups) |
| Need to undo a change | | `ctxb snapshots <slug>`, `ctxb diff <slug> <id>`, `ctxb restore <slug> <id> --yes` (reversible); whole DB: `ctxb backup --list`, `ctxb backup --restore FILE --yes` |
| Moving to another Mac | | `ctxb export --full` → copy the ZIP → on the new Mac `ctxb import --full <zip>` |

Logs: `~/.contextbridge/logs/contextbridge.log` (never contains memory content or secrets at INFO level).
Client config backups made by the installers: `~/.contextbridge/backups/client-configs/`.
