# ContextBridge — Progress

Resume from this file alone. Spec: `../ContextBridge_ClaudeCode_Prompt.md`. Blueprint: `docs/BLUEPRINT.md`.

**Current state (2026-10-31):** M1–M5 done. 138/138 tests pass, mypy --strict clean. 9 MCP tools + 3 prompts + 5 resources. Next: **M6** (import: `import_context`, transcripts, Claude data export — VERIFY formats first).

| Milestone | Status | Verified how | Notes |
|---|---|---|---|
| M0 Research & blueprint | done | Approved by user 2026-10-31 | |
| M1 Skeleton | done | pytest (unit + stdio integration), mypy --strict, real Claude Code session | `make_slug` written by Claude at user's "continue" |
| M2 Project Knowledge | done | pytest (unit, 2-process concurrency, stdio), real Claude Code session | Acceptance tests 5, 6, 12, 15 covered |
| M3 Live State / checkpoints / versioning | done | pytest (unit, CLI, stdio), real Claude Code session | Acceptance tests 1, 7, 14 (parts 1+3) covered; 16 groundwork |
| M4 Generation & manual handoff | done | pytest (unit, stdio, CLI), 3 real Claude Code sessions (handoff → resume via connector → resume from file alone) | Acceptance tests 3, 4, 5 (handoff part), 13, 16 covered |
| M5 Packaging | done | pytest (unit round trips, CLI subprocess migration between two data dirs) | Profiles, splitting, consolidation, manifest/merge done in M4; M5 adds full export/import + round trips |
| M6 Import | todo | | Verify Claude data-export format (V15) |
| M7 Client integration | todo | | V8, V9, V11; SessionStart hook; install via `uv tool install` (non-editable) |
| M8 Budget monitor | todo | | |
| M9 Hardening & docs | todo | | |

## M1 tasks
- [x] Repo `contextbridge/` (git init, branch `main`, no commits yet), `pyproject.toml` (uv, hatchling)
- [x] Config: packaged `default_config.toml` + user `~/.contextbridge/config.toml`, deep-merged; invalid TOML → clear error
- [x] Data dir layout with modes 700/600, repaired on every start
- [x] Logging: stderr + rotating file; key=value; no content at INFO
- [x] DB: WAL, busy_timeout, foreign keys, `BEGIN IMMEDIATE` write tx
- [x] Migration framework: contiguous versioned `.sql`, checksum drift detection, pre-migration online backup, rollback on failure, integrity check, backup pruning
- [x] Full v1 schema (BLUEPRINT §7.2) incl. FTS5 + sync triggers, provenance and rationale CHECKs
- [x] MCP server (stdio, `mcp` 2.3 `MCPServer`) with instructions, `list_projects`, `create_project`; CBError → structured tool error
- [x] CLI `ctxb init | projects | doctor | version` (stdlib-only import path)
- [x] `make_slug`: NFKD accent stripping; non-ASCII-only names refused → explicit slug

## M5 tasks
- [x] Upload profiles from config, size + token checks, heading-boundary splitting, manifest with hashes, `ctxb merge`, `.txt` fallback (built in M4, tested again here)
- [x] File-count handling: consolidate companions into `02_companion_files` first, staged upload plan only if still too many
- [x] Optional per-profile `max_total_tokens` warning (default off: no verified number for Claude Projects)
- [x] `ctxb export --full [--project …] [-o …]`: one ZIP (local migration only) with every table of the selected projects + global memory; per-member sha256; one consistent read; 600; never overwrites
- [x] `ctxb import --full <zip | package dir | source_references.json> [--project …] [--as SLUG] [--dry-run]`: hash check before any write, schema-version guard, pre-import backup, one transaction per project, ids preserved when free / remapped with all references rewritten (FKs, refs, depends_on, history JSON, snapshot payloads + hashes), FTS rebuilt (incl. archive), existing slug refused, redaction re-applied
- [x] `docs/FORMAT.md` (package + archive format v1)

## M5 verification
- `uv run pytest`: 138 passed. New: export → import into an empty data dir reproduces knowledge, state, history count, checkpoints and snapshot list exactly, ids preserved, knowledge + archive search work, snapshots hash-verify and restore, handoff tags identical; import into a DB whose ids collide remaps everything and the newest snapshots still diff as "no change" against current data, FK check clean, local project untouched; existing slug refused with nothing written, `--as` imports a copy; tampered member (sha256) / missing file / non-ZIP refused with nothing written; a package's sidecar alone rebuilds the project with the same tags and the original provenance (ai_suggested stays ai_suggested); dry run writes nothing (not even a backup); `--project` selection; CLI migration between two data dirs in subprocesses; consolidation + total-token warning.
- No MCP surface changed in M5, so no new Claude session test; the M4 sessions still apply.

## M4 tasks
- [x] `core/generation/`: `collect` (one consistent read of ALL knowledge + state + sessions + coverage), `rank` (budgeted selection), `render` (13-section template §8.2, companion files §8.3), `validate` (§8.7 report, checked against the rendered text), `split` (heading-boundary, fence-safe, byte-exact), `tokens` (offline range estimate)
- [x] Tags `[D-0123 · user_confirmed · 2026-10-10]` on every item; ⚠ marks for ai_suggested / imported / unverified
- [x] Evolution: follows `superseded_by_id` (handles merges/resolutions), oldest → newest with reasons; superseded items never listed as current
- [x] `prepare_handoff` (quick / standard / detailed / knowledge_package): optional `final_updates` checkpoint (kind `handoff`) first → package dir `exports/<slug>/<slug>_<mode>_<stamp>/` (700/600, never overwritten) with `00_manifest.json`, `01_START_HERE_handoff`, companions, `source_references.json` sidecar; export redaction; profile size/token checks, splitting, staged upload plan; `handoff` snapshot + `handoff_runs` row; 3-line summary
- [x] `get_context` (brief / state / knowledge / handoff / full; topics filter; 8k inline cap → file + where-stopped summary); output wrapped as memory data
- [x] MCP prompts `resume`, `handoff`, `checkpoint`; resources `ctx://projects`, `ctx://{project}/brief|state|knowledge|handoff/latest`
- [x] Repo summary for an explicitly linked dir (`ctxb link`): branch, last commit, changed paths, diffstat via read-only git; contents never read
- [x] CLI `ctxb handoff`, `context`, `link [--unlink]`, `merge` (verifies sha256 of reassembled parts)
- [x] Fixes found while building: redactor false positives (`estimated_tokens: 1140`, `author:`); timestamps second → microsecond (writes in the same second as a checkpoint were not counted as unsaved)

## M4 verification
- `uv run pytest`: 131 passed. New: five-session fixture (Test 13: every entry from every session in the package, evolution Flask → FastAPI ordered, superseded requirement only under "Obsolete"); 125-entry project in `quick` mode stays ≤ 3k tokens with every critical tag in the primary file and all 120 fillers in the companion (Test 4); exact port/command verbatim (Test 4); conflict at the top of the handoff (Test 5); never-checkpointed session and post-checkpoint writes named as coverage gaps (Test 16); manifest hashes, 700/600 modes, no overwrite; split at 2.5 kB reassembles byte-for-byte; 6 files vs max 3 → staged upload plan; a secret injected past write-time redaction is removed from every export file and the sidecar; project isolation in packages; linked git repo shows branch/commit/changed path but never contents; get_context scopes, topic filter, inline-cap file fallback; empty project → honest "unknown"; stdio switch-accounts flow across two server processes incl. prompts and resources; CLI handoff/context/link/merge.
- Real Claude Code sessions (temp data dir, user config untouched): (1) new project described in one message + "prepare a complete handoff, I'm switching accounts" → model saved decision/rejected approach/fact/bug/state/task, called `prepare_handoff` with final_updates, reported path, size, ~1.5k tokens, score 0.97, and the one warning (architecture empty); (2) a fresh session: "Load my Folio context and continue" → correct goal/state/stopping point/next action, asked only for the repo path; (3) a session with NO connector and only the file → stated next action, Astro + why, and "don't propose Next.js" (Test 3).

## M3 tasks
- [x] `update_state`: 12 sections (full replace, history incl. newest, refs validated), tasks upsert (deps validated, per-task results), redaction, state_version; never writes `entries`
- [x] Consistency validator: stale/dangling refs, superseded-value text heuristic, open conflicts
- [x] Sessions: label/client/account_label, capture ('none' until a checkpoint → 'checkpoints')
- [x] `checkpoint`: atomic (session + items + state + checkpoint row + snapshots); rolls back fully on any error
- [x] Snapshots for knowledge/state/handoff: zstd, hash-verified, deduped, list/diff/restore, reversible restore, retention pruning (30 days all, then 1/day)
- [x] Opportunistic scheduled DB backups + `ctxb backup [--list | --restore FILE --yes]`
- [x] `checkpoint_status` counts knowledge + state + task writes
- [x] CLI `checkpoint`, `snapshots [--create]`, `diff`, `restore --yes`
- [x] MCP `update_state`, `checkpoint` (7 tools total)

## M3 verification
- `uv run pytest`: 113 passed. New: state never touches knowledge (Test 14.1); consistency validator flags a Flask-in-stack-after-FastAPI state (Test 14.3); checkpoint atomic rollback; corrupt latest snapshot → earlier restores (Test 7); DB backup restore with safety copy + path-traversal refusal (Test 7); persistence identical across server restart (Test 1); CLI diff/restore/backup.
- Real Claude Code session: model saved 4 entries + 4 sections + 1 task unprompted, server nudged "call checkpoint" at 9 writes, model checkpointed on "about to hit my limit" with a concrete where_stopped/next_action. Fix from it: topic_key guidance (rejected approaches share the decision's key).

## M2 tasks
- [x] Write pipeline: validate → redact → normalize topic_key → provenance gate → near-duplicate merge → supersede/conflict → history → knowledge_version (one `BEGIN IMMEDIATE` tx per batch)
- [x] `remember` (batch ≤ 50, per-item results, `confirm` list, similar_topic_keys suggestions, redaction report without values)
- [x] Conflicts only on singular kinds (decision/requirement/constraint/convention/preference): change from spec, see BLUEPRINT §7.3
- [x] `resolve_conflict` (a_supersedes_b / b_supersedes_a / both_valid / merge; note required)
- [x] `search_memory` (FTS5 bm25, AND then OR fallback, query-syntax safe, superseded hidden by default, archive snippets wrapped as untrusted)
- [x] Secret redaction: provider patterns, PEM, JWT, URL creds, secret assignments, entropy heuristic (outside fences, not hex)
- [x] Project isolation on every query, incl. supersedes/confirm/resolve across projects
- [x] `checkpoint_status` (loss window: last checkpoint, writes since, recommendation; telemetry "unavailable")
- [x] Minimal sessions (get-or-create by label; full in M3)
- [x] CLI `ctxb search`, `ctxb conflicts`; doctor warns if data dir is in an iCloud-synced folder

## M2 verification
- `uv run pytest`: 92 passed (unit: redaction, synthesis, knowledge pipeline; integration: 2 processes × 150 concurrent writes = 300 rows, integrity + FTS ok; same-topic race → exactly 1 active, rest conflicts; stdio remember→conflict→resolve→search).
- `uv run mypy` (strict): clean.
- Real Claude Code session: Flask decision → FastAPI decision without supersedes_id → conflict reported, nothing chosen → model resolved b_supersedes_a with note → search returns FastAPI only. Two usability fixes made from it: snippets from any matching column; conflict reason names entry_a/entry_b explicitly.

## M1 verification
- `uv run pytest`: 33 passed; 10 fail only on `NotImplementedError` in `make_slug`.
- `uv run mypy` (strict): no issues in 22 files.
- Integration, real subprocess over stdio: tool listing + instructions, create/list, persistence across server restart (Test 1 partial), structured errors, schema validation, **stdout carries only JSON-RPC** (spec §14).
- Real Claude Code 2.1.316 session (`claude -p --mcp-config … --strict-mcp-config`, temp data dir): list → create → list returned the expected JSON; DB row present; files 600, dirs 700.
- CLI cold start: **53 ms** (`ctxb version`; `import contextbridge.cli.main` ≈ 20 ms). Target < 100 ms met → no Go helper needed.

## Decisions log
- 2026-10-31: **Product promise reframed** (user): preserve as much accumulated knowledge and progress as was made available, whenever the conversation ends, and never promise to prevent interruptions. Made measurable as a "loss window" + acceptance Test 24 (abrupt end at arbitrary point). Vision: personal memory infrastructure, Claude is the first client. MVP scope unchanged. See BLUEPRINT §0.
- 2026-10-31: Language approved: Python 3.14 + official `mcp` SDK. All BLUEPRINT §13 items approved; uv installed (0.13.6); repo in `contextbridge/` subfolder.
- 2026-10-31: `mcp` 2.3 renamed FastMCP → `mcp.server.MCPServer`; model fields are snake_case (`read_only_hint`), serialized camelCase on the wire.
- 2026-10-31: Sync tool handlers run in worker threads → each tool call opens its own short-lived SQLite connection (`App.db()`), never a shared one.
- 2026-10-31: Default config lives inside the package (`src/contextbridge/default_config.toml`) so it ships with the wheel.
- 2026-10-31: Near-duplicate = same project+kind+topic_key, token Jaccard ≥ 0.85 (config `synthesis.near_duplicate_threshold`). An explicit `supersedes_id` wins over merging. Merge keeps the longer body and the higher importance; it never changes provenance.
- 2026-10-31: Merges don't change provenance; only an explicit `confirm` turns ai_suggested into user_confirmed (P8).
- 2026-10-31: Snapshot retention default (user: "continue"): keep all 30 days, then 1 per component per day.
- 2026-10-31: Knowledge restore is non-destructive (newer entries → outdated); state restore may remove tasks (reversible via pre-restore snapshot).
- 2026-10-31: `telemetry.stale` computed at read time, not stored (BLUEPRINT §7.2).

- 2026-10-31: Ranking weights (user: "continue"): importance 100/30/5 + status + kind + recency (half-life 30 d, max 15) + 25 if referenced by Live State + 10 if the topic appears in open tasks. Critical live entries are mandatory as digests; full detail priority 0.6 × score (+50 critical).
- 2026-10-31: Working handoffs are always a small package (01 primary + 02 complete knowledge + 03 full state + manifest + sidecar), so a budget can only shorten the primary file, never lose data. `knowledge_package` splits into 02–06 by topic, with 01 at the standard budget.
- 2026-10-31: `complete: true` only when nothing was left out of the primary file AND no validation item is missing (spec §8.1: a small file is never labelled complete).
- 2026-10-31: Timestamps now microsecond precision (`now_iso`); no migration needed (no user data yet; mixed formats still order correctly except within the same second).

- 2026-10-31: Full import preserves provenance (it's the user's own migration, an explicit CLI action), unlike `import_context` (M6), which marks everything `imported` and stages it for confirmation.
- 2026-10-31: Full import never merges into an existing project; `--as` imports a copy. Merging two diverged copies of a project is out of scope for the MVP.
- 2026-10-31: M5 scope correction: client installers belong to M7 (I mis-stated this in the M4 report).

## Known issues
- **iCloud Desktop sync** sets the macOS `hidden` flag on the editable-install `.pth` file and Python 3.14 skips hidden `.pth` files → `ModuleNotFoundError`. Fix: venv lives in `.venv.nosync/` (iCloud ignores `*.nosync`) with `.venv` symlinked to it; pytest also sets `pythonpath = ["src"]`. If you recreate the venv, keep this layout. Real installs (M7) use `uv tool install`, outside Desktop.
- The DB must never live in an iCloud folder; `~/.contextbridge` is outside it. `ctxb doctor` warns if the data dir is under Desktop/Documents/iCloud Drive (done in M2).
- No git commits yet (not requested).
