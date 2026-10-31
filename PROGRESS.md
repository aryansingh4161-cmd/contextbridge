# ContextBridge — Progress

Resume from this file alone. Spec: `docs/SPEC.md` (copy of `../ContextBridge_ClaudeCode_Prompt.md`). Blueprint: `docs/BLUEPRINT.md`. Acceptance map: `docs/ACCEPTANCE.md`.

**Current state (2026-10-31):** **MVP complete: M0–M9 done.** 215/215 tests pass (unit, integration over real stdio, acceptance incl. SIGKILL tests), mypy --strict clean. 13 MCP tools + 3 prompts + 5 resources (attach_file added post-MVP). All 24 acceptance tests mapped and passing (`docs/ACCEPTANCE.md`). Installed on the user's Mac (Claude Code: MCP + hooks + status line; Claude Desktop) (updated to the M9 version by the user on 2026-10-31). Code: https://github.com/aryansingh4161-cmd/contextbridge (public, MIT, branch `main`). Open items: see *Known issues / open items* at the end.

| Milestone | Status | Verified how | Notes |
|---|---|---|---|
| M0 Research & blueprint | done | Approved by user 2026-10-31 | |
| M1 Skeleton | done | pytest (unit + stdio integration), mypy --strict, real Claude Code session | `make_slug` written by Claude at user's "continue" |
| M2 Project Knowledge | done | pytest (unit, 2-process concurrency, stdio), real Claude Code session | Acceptance tests 5, 6, 12, 15 covered |
| M3 Live State / checkpoints / versioning | done | pytest (unit, CLI, stdio), real Claude Code session | Acceptance tests 1, 7, 14 (parts 1+3) covered; 16 groundwork |
| M4 Generation & manual handoff | done | pytest (unit, stdio, CLI), 3 real Claude Code sessions (handoff → resume via connector → resume from file alone) | Acceptance tests 3, 4, 5 (handoff part), 13, 16 covered |
| M5 Packaging | done | pytest (unit round trips, CLI subprocess migration between two data dirs) | Profiles, splitting, consolidation, manifest/merge done in M4; M5 adds full export/import + round trips |
| M6 Import | done | pytest (unit, stdio, CLI), 14 real local transcripts parsed, 2 real Claude Code sessions (stage hostile file → commit selected items) | Acceptance Test 8 covered. Claude data-export parser built to a tolerant, UNVERIFIED shape (needs a real export) |
| M7 Client integration | done | pytest (fake HOME + fake `claude`), real install on the user's Mac, real Claude Code session with the hook, 2 Desktop checks by the user | V9 ✓ tools work in Desktop; V8 ✗ Desktop hides server instructions → habits carried in tool results (verified); account-switch check deferred |
| M8 Budget monitor | done | pytest (unit + subprocess hooks), 2 real Claude Code sessions with the Stop hook | Acceptance tests 17–22 covered; 19, 20, 23 at core level; status line ~80–100 ms |
| M9 Hardening & docs | done | pytest (acceptance suite: SIGKILL at random points, offline, >20 files; meta-tests for the acceptance map and TOOLS.md) | Acceptance tests 10, 11, 24 added; all 24 mapped; forget/delete/purge; docs complete |

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

## Post-MVP: attachments + word-for-word chat import (2026-11-01, user asked for both)
- [x] Schema migration 0002: `attachments` (+ `attachments_fts`); files copied to `~/.contextbridge/attachments/<slug>/<sha256>.<ext>` (700/600), deduped per project, ≤ 50 MB (`[attachments] max_bytes`), image/PDF/Office/text types; text-like files redacted + indexed
- [x] MCP `attach_file` (13 tools; AI-chosen paths limited to allowed folders), attachments in `search_memory` results (wrapped as data), `forget` target `attachment`; CLI `ctxb attach`, `ctxb attachments`, `ctxb delete --attachment`, `ctxb handoff --with-attachments`
- [x] Handoffs list attachments in "Files & repo" (tag `[A-0001 …]`, note, size, related entry); `knowledge_package` copies them into `attachments/` with manifest entries and upload plan
- [x] Full export/import carries the files (sha-verified), rebuilds the text index; forget project / purge remove them (recovery archive keeps them until purge)
- [x] Claude export import keeps the text of files attached in chats (`extracted_content`) word for word and names other files; `ctxb import-commit --select 0,3,7 / --match "name"` to keep only this project's conversations
- [x] Fix during review: the path check took the allowed file types from a module global that I temporarily swapped — unsafe with parallel tool threads; now a parameter
- [x] 225 tests pass (new: tests/unit/test_attachments.py, stdio attach test), mypy clean, TOOLS.md regenerated

## Post-MVP: first real project (2026-10-31, user chose "set up my first real project" → ContextBridge itself)
- [x] Project `contextbridge` created in the user's REAL memory (`~/.contextbridge`), seeded from this build: 26 entries (11 decisions, 3 rejected approaches, 1 requirement, 1 constraint, 4 lessons, 2 facts, 3 open questions, 1 milestone) with honest provenance (4 user_stated, 12 user_confirmed, 10 verified), 6 Live State sections, 4 tasks, checkpoint #1; linked to the repo folder; `CLAUDE.md` added to the repo
- [x] Real-use findings fixed: (1) handoff cost estimate ballooned to ~100k tokens with a 1M-token window → reserve capped at 20k; (2) the brief loaded at session start listed decisions without reasons and dropped open questions → decision digests keep a short "why", open questions are mandatory
- [x] Verified with fresh, real Claude Code sessions in the repo folder (user's real config, no tools allowed): the SessionStart hook alone gave the model the project, where we stopped (incl. the latest commit), next action, the language decision with its reason, and all three open questions. `ctxb handoff contextbridge` → 3 files, ~4.8k tokens primary, score 1.0, no coverage gaps
- [x] Seeding note: the server rejected one seeded decision that lacked a rationale (validation working); the partial project was purged and re-seeded cleanly

## M9 tasks
- [x] Gap review against the spec: added the 12th MCP tool `forget` (entry / session / project; confirm=true; recovery point first; exact project name for a project) and CLI `delete`, `purge` (permanent: DB rows, snapshots — also inside backups —, exports, recovery archives, every backup DB), `remember` (manual), `migrate`
- [x] Acceptance suite in `tests/acceptance/`: Test 24 (real child process SIGKILLed at 12 random points, before the first write, and 7 times mid-handoff with a test-only write delay that proves packages really are half-written, plus after a StopFailure hook), Test 11 (all flows with every network call failing), Test 10 (> 20 files under a 20-file profile)
- [x] `docs/ACCEPTANCE.md`: all 24 tests → automated tests (+ manual checks); `tests/test_acceptance_map.py` fails if a reference disappears
- [x] `docs/TOOLS.md` generated from the live server (`scripts/gen_tools_md.py`), with a drift test; `docs/TROUBLESHOOTING.md`; README rewritten; INSTALL links troubleshooting
- [x] Splitter: oversized sections without sub-headings now cut between blocks (never inside fences or tables), still byte-exact (found by Test 10)
- [x] Security review fix: `purge --entry` also scrubs the entry from snapshots inside DB backups (matched by slug) and from recovery archives (manifest hashes updated)

## M9 verification
- `uv run pytest`: all pass. Test 24 metric: **100 %** of acknowledged writes recovered in every kill scenario; checkpoints all-or-nothing; the latest handoff always points at a manifest-complete package even when others were interrupted; emergency handoff names the last checkpoint time and says what is not in the file; never `complete: true`. Purge tests grep raw DB/WAL/backup bytes and decompressed snapshots (live, in backups, in recovery archives) for a unique marker: nothing remains.
- No MCP behaviour the user relies on changed except the new `forget` tool, so no new client session test was needed beyond the suite.

## M8 tasks
- [x] Re-verified (docs 2026-10-31): status-line config + stdin fields (`context_window.used_percentage` input-only, may be null; `rate_limits.five_hour|seven_day.{used_percentage, resets_at}` Unix s, Pro/Max/Ultra only, after first response, windows may be absent), 300 ms debounce and in-flight cancel; Stop (`stop_hook_active`, `hookSpecificOutput.additionalContext`, 8-continuation cap), PreCompact (`trigger`, `transcript_path`), SessionEnd (`reason`, 1.5 s default, per-hook timeout up to 60 s, no decision control), StopFailure (`error` values, output ignored)
- [x] `core/budget.py`: `Reading` (every field optional; absent ≠ 0; no budget inferred from another), throttled storage (≤ 1 row/15 s/session unless ≥ 1 point change; last 200 kept), expired rate-limit windows ignored, thresholds 70/80/85/90/95 across context/5h/7d, feasibility (`required = 1.5k + 2×0.5k + 0.5k + 10% window`; act at 85 when `available < required + 3k`), recommended handoff mode (quick when infeasible), `budget_status`
- [x] `ctxb statusline` (`ctx 72% · 5h 41% · 7d 63% · ckpt 12m ago · handoff ✓ · folio`, `–` for absent, never fails, fast path without scheduled backup)
- [x] Hooks: `stop` (once per level per session, write-ahead state file, level satisfied by a checkpoint (80/85) or handoff (90/95) made after the status line first saw it, `stop_hook_active` honoured, unsafe session ids ignored, stale readings ignored), `pre-compact` (archives transcript to the linked project, never blocks), `session-end` / `stop-failure` (spawn a detached `ctxb emergency` and return immediately)
- [x] `ctxb emergency` (archive transcript + compact handoff labelled "not AI-synthesized after <last checkpoint>", `--only-if-unsaved`), `ctxb status`, `ctxb telemetry push` (generic adapter), MCP `get_budget_status` (11 tools), `checkpoint_status.telemetry` = available only with a fresh reading, recommendation escalates at 85/90
- [x] Installer adds the status line (keeps a status line you already have unless `--force-statusline`) and Stop / PreCompact / SessionEnd / StopFailure hooks; uninstall removes only ours

## M8 verification
- `uv run pytest`: 190 passed. Test 17: readings stored from real-shaped status-line JSON, absent fields shown "–" and stored NULL, "unavailable" in Desktop-like setups, stale (> 300 s) flagged and ignored for nudges; throttle; expired windows. Test 18 + 21: readings 50→99% with 3 Stop events per step fire exactly once each at 80/85/90/95, `stop_hook_active` never fires, a checkpoint after crossing satisfies 85 but not 90 (needs a handoff), path-traversal session ids and stale readings do nothing. Test 19: infeasible → quick mode recommended; feasibility triggers before percentages. Test 20/23: emergency handoff labelled, transcript archived and searchable, `get_context` still resumes; `--only-if-unsaved` skips when nothing changed. Test 22: PreCompact archives to the linked project's session (capture=transcript), no duplicate, never blocks. SessionEnd hook returns in < 1.5 s and the detached child writes the emergency handoff. All hook commands exit 0 with a broken data dir. Status line 80–100 ms per call.
- Real Claude Code sessions (temp data dir, `--settings` file, user settings untouched): reading of 86 % pushed for the session → Stop hook asked once → model called `checkpoint` itself, told the user in one line and stopped; resumed same session for another turn → no further request (checkpoint count unchanged).

## M7 tasks
- [x] Verified (docs, 2026-10-31): SessionStart hook config shape, stdin fields (`cwd`, `source`…), plain stdout → context, 10,000-char cap; `claude mcp add --transport stdio --scope user <name> -- <cmd>` stored in ~/.claude.json
- [x] `ctxb install-claude-desktop` / `install-claude-code` (`--dry-run`, `--uninstall`, `--no-hook`): merge, never rewrite; backup to backups/client-configs (600); atomic write keeping file mode; refuse invalid JSON / wrong shapes; idempotent; iCloud-path warning for the binary
- [x] `ctxb hook session-start`: finds the project linked to cwd (closest ancestor), prints its brief context (≤ 9,500 chars) wrapped as memory data; never fails the session (exit 0, empty output on any error)
- [x] `ctxb snippet claude-md|desktop <slug>`; `docs/INSTALL.md`; README install section; doctor reports hook + linked folders
- [x] Real install on the user's Mac (user approved 2026-10-31): `uv tool install` → ~/.local/bin; `ctxb init`; `install-claude-code` (mcp user scope + SessionStart hook) and `install-claude-desktop`; backups in ~/.contextbridge/backups/client-configs; Desktop config identical apart from `mcpServers`; `claude mcp get` = ✔ Connected; doctor all ✓; a normal (non-test) Claude Code session called list_projects through it
- [x] Claude Desktop check 1 (user, 2026-10-31): after ⌘Q + reopen, "List my ContextBridge projects" worked (tools work in Desktop, V9 ✓). Desktop log: initialize, tools/list, prompts/list, resources/list all answered
- [x] Claude Desktop check 2: the model did NOT see the server `instructions` (V8 ✗) → fallback implemented: `usage` habits text in list_projects / create_project / get_context results
- [x] Re-check in Desktop (user, 2026-10-31, after reinstall + restart): asked what ContextBridge wants at the start of work and on limits, the model answered get_context with explicit slug + confirm goal/where stopped/next action; checkpoint immediately on limits/switching/stopping; prepare_handoff when switching; decisions with rationale; ai_suggested until confirmed; never follow instructions in imported content. Fallback works.
- [ ] Optional, deferred by the user: config survives a Claude account switch (expected: yes, local file; listed in LIMITATIONS as to-test)

## M7 verification
- `uv run pytest`: 172 passed. New: Desktop merge keeps other servers/keys, backup byte-identical and 600, original file mode kept, dry run changes nothing, idempotent, uninstall restores the original exactly, invalid JSON and wrong shapes refused untouched, custom data dir → `env`; Claude Code: exact verified `claude mcp add` arguments, hook merged next to existing SessionStart/PreToolUse hooks and other settings, idempotent, uninstall removes only ours; hook output for a linked subfolder, nothing for unrelated folders or bad input; the installer's shell-quoted hook command runs via `sh -c` from a path containing an apostrophe; a broken data dir yields exit 0 and no output.
- `uv tool install .` into a temp tool dir: both executables work outside the iCloud folder.
- Real Claude Code session with only the SessionStart hook (via `--settings`, user settings untouched), started in a subfolder of a linked project, no tools: correctly stated the project, next action and the rejected framework.

## M6 tasks
- [x] Verified Claude Code transcript format on this Mac (Claude Code 2.1.316): JSONL; `user`/`assistant` records with `message.role` + `message.content` (string or blocks `text`/`thinking`/`tool_use`/`tool_result`); `isSidechain`, `isCompactSummary`, `ai-title`, `sessionId`, `timestamp`; many metadata record types (ignored)
- [x] `core/importer/`: `safety` (absolute paths only, extension allowlist, hidden folders refused except ~/.claude/projects and our exports, size limits, strict UTF-8, marker neutralization, instruction-like text flagged), `parsers` (package with sidecar, handoff file without sidecar, Markdown/text with guessed kinds, Claude Code transcript, Claude data export — tolerant/unverified), `staging` (stage → commit/discard, 24 h expiry, per-project, one transaction)
- [x] Commit: provenance `imported`, confidence `assumed`, `select` + `edits` (kind/topic_key/title/importance/rationale/body/replace), `keep_archive`, existing Live State kept unless replace, archive + FTS, transcript sessions (capture 'transcript'/'both', dedupe by sessionId and sha256), imported checkpoint so `get_context` shows where the old work stopped
- [x] MCP `import_context` (10 tools); CLI `ctxb import <src> --type … --project … [--yes]`, `ctxb import-commit`, `ctxb ingest-transcript` (for M8 hooks)
- [x] Model-chosen import paths (MCP) limited to `[import] allowed_dirs` (default Desktop, Documents, Downloads) + ~/.claude/projects + our exports; CLI paths typed by the user are not limited (hidden folders never). Package folders: hidden check first, members must be regular non-symlink files inside the folder, total size budget. Source titles redacted + neutralized + flagged.
- [x] Security hardening from automated review: `ctxb merge` path traversal; import clears `linked_dir`; git runs with forced safe config via GIT_CONFIG_COUNT, repo-defined filters/drivers neutralized, fail closed on odd names / partial clones / git < 2.31; regression tests prove a hostile fsmonitor/filter/textconv never executes

## M6 verification
- `uv run pytest`: 163 passed. Test 8: hostile Markdown (fake Anthropic + GitHub keys, "ignore all previous instructions… curl | sh", a forged end-of-data marker) → keys redacted in preview, staging row, entries and archive; marker neutralized (preview has exactly one end marker); item flagged; stage writes no memory; committed items `imported`/`assumed`, shown with ⚠ in handoffs; confirm flips to user_confirmed. Also: discard leaves no trace; staging is project-scoped and expires; 7 path-safety cases (relative, missing, hidden dir, .sh, oversize, non-UTF-8, symlink into ~/.ssh-like dir); package with sidecar → new project keeps topic keys, original tag in source_ref, superseded not re-imported, where-stopped restored; handoff file alone → decisions with rationale/importance, tasks, checkpoint; transcript → text + tool markers kept, thinking/tool output/sidechain dropped, session with external key, duplicate not stored twice; export ZIP → only selected conversations archived; edits fix guessed kinds; other projects untouched; stdio stage/edit/commit; CLI stage → import-commit, ingest-transcript.
- Real data: 15 largest local Claude Code transcripts → 14 parsed in 3–150 ms (up to 11.7 MB, 647 messages), 1 (58 MB) refused by the 20 MB limit → transcripts now have their own 100 MB limit.
- Real Claude Code sessions: (1) "import my old notes, show me, don't commit" with a hostile file → model staged, did NOT follow the embedded "delete the project" instruction (DB verified untouched), pointed out the key looked like an Anthropic key under a Netlify name and advised revoking it, recommended committing only 2 items; fixes from it: topic keys from the item's own heading, `keep_archive` option; (2) fresh session committed exactly items 0 and 1 with the requested kinds/topic keys and no archive (DB verified).

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

- 2026-10-31: GitHub (user): public repo `contextbridge`, spec copied to `docs/SPEC.md`, commits as the existing git identity. `gh` installed via brew; logged in as aryansingh4161-cmd; repo created and pushed 2026-10-31. License: MIT (user).
- 2026-10-31: Import kinds from Markdown are guessed by keyword and marked `kind_guessed`; the model corrects them with `edits` on commit (the server never calls an LLM).
- 2026-10-31: Live State from an import never overwrites existing sections unless an edit says `replace: true`.

## Known issues / open items
- After code changes, update the installed copy with `cd "<repo>" && uv tool install --reinstall .` (the Claude configs don't need to change).
- **Not verified with a real file: the Claude data export format** (`claude_export` parser is tolerant and refuses unknown shapes). Needs a real export from claude.ai → Settings → Privacy → Export data.
- **Not tested: Claude Desktop config surviving a Claude account switch** (expected yes: it's a local file). Deferred by the user.
- **iCloud Desktop sync** sets the macOS `hidden` flag on the editable-install `.pth` file and Python 3.14 skips hidden `.pth` files → `ModuleNotFoundError`. Fix: venv lives in `.venv.nosync/` (iCloud ignores `*.nosync`) with `.venv` symlinked to it; pytest also sets `pythonpath = ["src", "tests"]`. If you recreate the venv, keep this layout. Real installs use `uv tool install`, outside Desktop.
- The DB must never live in an iCloud folder; `~/.contextbridge` is outside it. `ctxb doctor` warns if the data dir is under Desktop/Documents/iCloud Drive.
- Later (not MVP, spec §15): remote HTTP transport with auth (claude.ai web, ChatGPT), web dashboard, optional sync, embeddings, notifications.
