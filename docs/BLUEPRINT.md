# ContextBridge — Blueprint (Milestone M0)

Status: **DRAFT — awaiting approval.** No implementation code has been written.
Date: 2026-10-31. Author: Claude Code (Opus 5.5) from `ContextBridge_ClaudeCode_Prompt.md`.

Decisions needing approval are collected in **§13**.

---

## 0. Product promise (added 2026-10-31)

**What ContextBridge promises:**
> No matter when or how an AI conversation ends, ContextBridge preserves as much of the
> project's accumulated knowledge and development progress as was made available to it, and
> states exactly what it has and what it is missing.

**What it does not promise:** that a usage limit, a context limit or a crash can always be
prevented from interrupting a conversation. No supported mechanism allows that (§2 V2/V3,
`docs/LIMITATIONS.md`). The budget monitor makes interruptions *cheaper* and *rarer to lose
work in*, not impossible.

**The promise, made measurable:**
- **Loss window**: the work that exists only in the conversation, i.e. everything after the
  last durable write (a checkpoint, `remember`/`update_state`, or a transcript archive). The
  design goal is to keep this window small and **known**: `checkpoint_status`, `ctxb status`
  and the status line show "unsaved since <time>" instead of a reassuring "safe".
- **Honest coverage**: every handoff says which sessions and sources it is built from, and
  where the gaps are (P8, Test 16). Something that was never given to the server is reported
  as *missing*, never as *nonexistent*.
- **Acceptance test 24, abrupt end at an arbitrary point** (added to §16 of the spec): a
  simulated session is killed at random points (between tool calls, mid-handoff, after
  StopFailure, with no hooks at all). For each run, check that:
  (a) every item written before the kill is recoverable;
  (b) the emergency or next handoff states the last durable save time and the loss window;
  (c) nothing unsaved is presented as saved.
  The metric is the share of pre-kill writes recovered (target 100 %), tracked across releases.

**Larger direction: personal memory infrastructure for AI-assisted work.**
Claude is the first client, not the product. This is already reflected in P6 (provider-independent
core) and in the layering (core / MCP / CLI / thin adapters). In practice:
- the core, schema and package format must never take a dependency on a specific assistant;
- `global_memory` (cross-project preferences and conventions) is a first-class layer, not an afterthought;
- new clients (ChatGPT via remote HTTP, IDE agents, others) are added as adapters in later phases.

The MVP scope is **unchanged**: this direction guides decisions but doesn't add milestones.

---

## 1. Environment inspection (2026-10-31)

| Item | Found | Implication |
|---|---|---|
| macOS | 26.5.2 (build 25F84), Apple Silicon (arm64) | — |
| Shell | zsh | Install docs written for zsh |
| Python | 3.14.6 (python.org build) | stdlib `sqlite3`, **`compression.zstd`** (new in 3.14), `tomllib` all present |
| SQLite (Python-linked) | 3.50.4, **FTS5 verified working** | No extra SQLite dependency needed |
| SQLite CLI | 3.51.0 | Useful for debugging |
| uv | **not installed** | Needed for `uv tool install`; install via `brew install uv` (asks you first) |
| Node / npm | v24.22.0 / 11.19.1 | Candidate stack available |
| Go | 1.27.1 darwin/arm64 | Candidate stack available |
| zstd CLI | 1.5.7 | — |
| git | 2.54.0 | Repo summaries via `git` commands |
| Homebrew | 7.0.11 | — |
| Claude Code | 2.1.316 | All hook and status-line fields cited below are present at this version |
| Claude Desktop | Installed (`/Applications/Claude.app`) | Config exists; top-level keys: `coworkUserFilesPath`, `preferences`; **no `mcpServers` yet** → the installer must *merge*, never overwrite |
| `~/.claude/settings.json` | Exists; no `statusLine`, no `hooks` | We can add both without conflict (merge, back up first) |
| `~/.contextbridge/` | Does not exist | Clean start |
| Project folder | Contains only the spec `.md` and a PDF copy; **not a git repo** | M1 creates the repo layout here; ask before `git init` |

---

## 2. Verified facts (VERIFY items) and sources

| # | Claim in spec | Verified result | Source |
|---|---|---|---|
| V1 | Claude Code status line gets session JSON on stdin incl. context + 5h/7d usage | **True.** Fields: `session_id`, `transcript_path`, `context_window.{used_percentage, remaining_percentage, context_window_size, total_input_tokens, total_output_tokens, current_usage}`, `exceeds_200k_tokens`, `rate_limits.five_hour.{used_percentage, resets_at}`, `rate_limits.seven_day.{…}`. `rate_limits` only for Pro/Max/Ultra (or gateway spend limit), only after the first API response, and each window may be absent. `used_percentage` may be `null` early and after `/compact`. Optional `refreshInterval` (≥1 s). | [statusline docs](https://code.claude.com/docs/en/statusline) |
| V2 | Hooks `PreCompact`, `Stop`, `SessionEnd`, `StopFailure` exist; input has `session_id`, `transcript_path` | **True.** Common fields include `session_id`, `transcript_path` (JSONL under `~/.claude/projects/…`), `cwd`, `hook_event_name`. PreCompact: `trigger` (`manual`/`auto`), `custom_instructions`. SessionEnd: `reason`; **no decision control; default timeout 1.5 s** (raise per-hook `timeout`, up to 60 s). StopFailure: `error` (`rate_limit`, `billing_error`, …), `error_details`; **output ignored**. | [hooks docs](https://code.claude.com/docs/en/hooks) |
| V3 | Stop hook can make Claude do one more turn; loop guard exists | **True.** Either exit code 2 (stderr = reason) or JSON `{"decision":"block","reason":"…"}`. Alternatively `hookSpecificOutput.additionalContext` continues without being labelled an error. Loop guards: input `stop_hook_active` (true when already continuing because of a stop hook) **and** a built-in cap of 8 consecutive continuations. | hooks docs |
| V4 | (New, not in spec) A hook can inject context into a new session | **True for Claude Code.** `SessionStart` (matchers `startup`, `resume`, `clear`, `compact`, `fork`) may return `hookSpecificOutput.additionalContext`; strings > 10,000 chars become a file + 2,000-char preview. Should be written as facts, not imperatives. | hooks docs |
| V5 | `claude mcp add` user-scope syntax | `claude mcp add --transport stdio --scope user contextbridge -- <command> [args…]`. Scopes: `local` (default), `project` (`.mcp.json`), `user` (stored in `~/.claude.json`). | [Claude Code MCP docs](https://code.claude.com/docs/en/mcp) |
| V6 | (New) MCP output limits in Claude Code | Warning above **10,000 tokens**; default cap **25,000 tokens** (`MAX_MCP_OUTPUT_TOKENS`); text over 50,000 chars is saved to a file. → `get_context` must not return a 30k `detailed` handoff inline. | Claude Code MCP docs |
| V7 | Claude Desktop config location and format | `~/Library/Application Support/Claude/claude_desktop_config.json`, top-level `mcpServers` → `{name: {command, args, env}}`. Full restart needed. Logs: `~/Library/Logs/Claude/mcp-server-<name>.log` (server stderr). Desktop Extensions (`.mcpb`) are an alternative install path. | [MCP: connect local servers](https://modelcontextprotocol.io/quickstart/user) |
| V8 | Desktop config survives account switch | **Expected yes** (local per-macOS-user file, not tied to the claude.ai account) but **not stated in the docs**. → Manual test in M7. | — (to test) |
| V9 | Desktop supports tools / prompts / resources / server instructions | Tools: yes. Prompts and resources: third-party sources say yes; **instructions: not documented**. → Design works with **tools alone**; nudges also live in tool descriptions and in `checkpoint_status`. Test in M7. | secondary sources only, e.g. [developersdigest](https://www.developersdigest.tech/mcp-servers/clients/claude-desktop) |
| V10 | Upload limits | Chat: **500 MB/file, 20 files/chat**. Projects: **30 MB/file, unlimited count**, but content must fit the context window; text extraction only. Listed types include **TXT, JSON, PDF, CSV, HTML, DOCX**. **`.md` and `.zip` are not listed.** | [Upload files to Claude](https://support.claude.com/en/articles/8241126-upload-files-to-claude) |
| V11 | ChatGPT MCP support | Developer mode supports **remote HTTPS servers only** (streamable HTTP/SSE); no local stdio. Sources are third-party; OpenAI help centre still to be checked in M7. → Standalone file is the ChatGPT path for the MVP. | [designrevision](https://designrevision.com/blog/add-mcp-server-to-chatgpt), [hjarni](https://hjarni.com/blog/how-to-use-mcp-with-chatgpt) |
| V12 | Claude.ai web custom connectors need remote MCP | Consistent with V11 and with the MCP docs' "connect to remote servers" path. Later phase. | MCP docs |
| V13 | SDK maturity | **Python `mcp` 2.3.0** (2026-10-02, requires ≥3.10; FastMCP decorators for tools/prompts/resources). **TypeScript `@modelcontextprotocol/sdk` 1.32.2** (modified 2026-10-23). **Go `modelcontextprotocol/go-sdk` v1.8.0** (stable; negotiates spec `2026-07-28`). The current MCP spec revision is **2026-07-28**. | [PyPI mcp](https://pypi.org/project/mcp/), npm registry, [go-sdk releases](https://github.com/modelcontextprotocol/go-sdk/releases) |
| V14 | Official token-counting API | Anthropic's Messages API has a token-count endpoint. Using it needs a key and a network connection, which breaks P7. It is off by default and won't be implemented in the MVP. | (claude-api reference; to re-verify if ever enabled) |
| V15 | Claude data export format | **Not verified yet**; deferred to M6 (it needs a real export file). | — |

Still to verify, assigned to milestones: V8 and V9 (M7), V11 via OpenAI's own docs (M7), V15 (M6).

---

## 3. Problem analysis

**Symptoms:** re-explaining after a limit; lost decisions and rejected approaches; the new chat re-proposes things already ruled out; progress state lost; nuance ("why") lost before facts are.

**Root cause:** project memory lives *inside a conversation*, which is owned by a provider and an account and is bounded by a context window. Summaries are a symptom-level fix: they get produced at the worst moment (near the limit), by a model that may already have lost the early context, and they are stored where the next assistant can't reach them.

**Fix:** move memory *out* of the conversation into a store that belongs to the user. Fill it **incrementally while the context is fresh** (P3, P5). Make reading and writing it a habit (instructions + nudges + hooks). Produce handoff files deterministically from the store.

---

## 4. Three candidate architectures

**A. Pure MCP exporter.** The server exposes one or two tools ("save summary", "export"). The model writes a prose summary at the end, and the server stores it as a file.

**B. MCP + local context-manager core.** A structured database (typed entries, supersession, conflicts, Live State, sessions) behind ~12 MCP tools and a CLI. The model writes small structured increments during the session, and the server assembles handoffs deterministically.

**C. Hook-and-transcript-driven capture.** Rely mainly on Claude Code hooks and transcript files: archive every transcript, and have a later model pass (or a server-side LLM) extract knowledge from them.

| Criterion | A. Exporter | B. Context manager | C. Hook/transcript |
|---|---|---|---|
| Reliability near a limit | Poor: everything depends on one late call | Good: already checkpointed (P5) | Good for *raw* capture; extraction still needs a model later |
| Context quality | Prose, lossy, no evolution | Typed, rationale kept, evolution chains | Raw is complete, but unsynthesized until processed |
| Storage efficiency | Duplicated summaries | Deduplicated entries + compressed snapshots | Large raw JSONL |
| Ease of implementation | Very easy | Medium–hard | Medium |
| Cross-account / cross-app | File only | Any MCP client + files | **Claude Code only** (Desktop/web have no hooks or local transcripts) |
| Automation | None | Nudges in every client; hooks where available | Strong in Claude Code, none elsewhere |
| Privacy | Local | Local | Local, but raw transcripts may hold secrets → redaction is critical |
| Scalability (long projects) | Breaks: summaries of summaries | Good: FTS + ranking + budgets | Raw grows without bound |
| Offline / no API key (P7) | Yes | Yes | Only if extraction is done by the user's model, not the server |

**Recommendation: B as the core, with C as an adapter layer.** B satisfies every non-negotiable principle in every client. C's hooks and transcript archiving are added where they exist (Claude Code). They act as a safety net, never as the primary path. That matches P4 and P6.

---

## 5. Final architecture

```
 ┌─────────────── clients (thin adapters) ───────────────┐
 │ Claude Desktop      Claude Code             others     │
 │  (stdio MCP)        (stdio MCP + statusline  (file     │
 │                      + hooks: SessionStart,   upload)  │
 │                      PreCompact, Stop,                 │
 │                      StopFailure, SessionEnd)          │
 └──────┬──────────────────┬──────────────┬───────────────┘
        │ MCP (stdio)      │ ctxb CLI      │ .md/.txt
 ┌──────▼──────────────────▼──────────────▼───────────────┐
 │  mcp/ (tools, prompts, resources, instructions)  cli/   │
 ├─────────────────────────────────────────────────────────┤
 │ core/ (provider-independent, deterministic, offline)    │
 │  security → synthesis → db        generate → package    │
 │  (redact,   (normalize, dedupe,   (rank, assemble,      │
 │   validate,  supersede, conflict,  templates, validate, │
 │   untrusted) consistency)          size, split, manifest)│
 │  importer (staging)   budget (readings, estimator,      │
 │                               thresholds, feasibility)  │
 └──────────────────────────┬──────────────────────────────┘
                            │
             ~/.contextbridge/contextbridge.db (SQLite WAL + FTS5)
             backups/  exports/  logs/  telemetry/state/  config.toml
```

Each client launches its own server process. Every process opens the same DB. Writes are short transactions with `BEGIN IMMEDIATE` and `busy_timeout=5000`. There is **no in-process global state** about "the current project".

### 5.1 Data flows

1. **Write (any client):** model → `remember`/`update_state`/`checkpoint` → validate → redact → normalize `topic_key` → similarity check (merge?) → same-topic active check (supersede or conflict?) → write row + `entry_history` + bump `knowledge_version` → FTS update → consistency check vs Live State → response (per-item result + warnings + `checkpoint_status`).
2. **Resume (any client):** `resume` prompt or a direct ask → `get_context(project, scope=brief)` → brief (≤ ~2.5k tokens) → model confirms with the user.
3. **Resume (Claude Code, automatic):** the `SessionStart` hook runs `ctxb hook session-start`, which maps `cwd` to a linked project and returns a short factual `additionalContext` (project name, where stopped, next action, "call get_context for detail"). Capped well below 10k chars.
4. **Handoff:** `prepare_handoff(final_updates)` → apply the updates as a checkpoint → assemble from **all** entries → validate → size against the profile and level → split → write files → `handoff_runs` row → return paths + report.
5. **Claude Code safety net:**
   - status line → `ctxb statusline` stores a `BudgetReading` (deduplicated, at most one row every N seconds) and prints a line;
   - `Stop` → `ctxb hook stop` reads the latest reading and per-session state; if a threshold level was newly crossed and no checkpoint since → `decision: block` with reason "call checkpoint…" (guards in §10);
   - `PreCompact` → `ctxb hook pre-compact` archives the transcript (compressed, linked to the session);
   - `StopFailure`(`rate_limit`, `billing_error`, …) and `SessionEnd` → spawn a **detached** `ctxb emergency --from-hook`, so the 1.5 s SessionEnd budget is never the bottleneck.
6. **Emergency (no model):** `ctxb emergency <project>` → latest DB state + last checkpoint → compact handoff labelled "not AI-synthesized after <time>" + archive reference.

---

## 6. Tool specification (12 tools)

Shared conventions:
- `project` is a slug (string, `^[a-z0-9][a-z0-9-]{1,62}$`). An unknown slug → error with close matches ("project 'sih' not found; known: sih-docverify, portfolio").
- Every project-scoped response carries `checkpoint_status`:
  `{"minutes_since_checkpoint": int|null, "writes_since_checkpoint": int, "recommendation": "none"|"call checkpoint"|"prepare handoff", "telemetry": "available"|"unavailable"}`.
- Errors return `isError: true` with `{code, message, hint}`. Codes: `not_found`, `invalid_input`, `conflict`, `provenance_violation`, `too_large`, `confirm_required`, `db_busy`, `internal`.
- Inline output is capped at a configurable **8,000 tokens** (V6). Anything larger is written to `exports/` and returned as a path + summary.

| # | Tool | Inputs (JSON Schema summary) | Output | Validation / side effects |
|---|---|---|---|---|
| 1 | `list_projects` | `{include_archived?: bool}` | `[{slug, name, updated_at, last_checkpoint_age_min, open_conflicts}]` | Read-only |
| 2 | `create_project` | `{name: str 1–120, objective: str, problem?, target_users?, scope?, success_criteria?, slug?}` | `{slug, created: true}` or `{exists: true, slug}` | Slug derived + uniquified; snapshot of the empty project |
| 3 | `get_context` | `{project, scope: brief\|state\|knowledge\|handoff\|full = brief, budget_tokens?: 500–8000, topics?: [str]}` | `{content (markdown, wrapped as memory data), versions, coverage, truncated: bool, file_path?}` | `full` always goes to a file; never includes raw archive |
| 4 | `remember` | `{project, session_label?, items: [Entry] (1–50)}`, Entry = §7.2 fields minus server-managed ones, plus optional `supersedes_id`, `confirms_id` | `{results: [{index, status: saved\|merged\|conflict\|rejected, id?, conflict_id?, reason?, similar_topic_keys?}], redactions: [{index, field, type}]}` | `rationale` required for decision/rejected_approach; provenance rule (§7.3); `confirms_id` flips provenance `ai_suggested→user_confirmed` (the explicit confirmation call) |
| 5 | `update_state` | `{project, sections?: {section_name: {content: str, refs?: [entry_id]}}, tasks?: [{id?, title, status, priority?, depends_on?, expected_outcome?, notes?}]}` | `{updated_sections, task_results, consistency_warnings}` | Snapshots the previous section versions; **never touches `entries`** (tested) |
| 6 | `checkpoint` | `{project, session_label?, client?, summary, where_stopped, next_action, items?: [Entry], state?: {…update_state shape}}` | `{session_id, checkpoint_id, item_results?, state_results?}` | Single transaction; target < 50 ms; used by emergency/Stop path |
| 7 | `search_memory` | `{project, query: str, kinds?: [kind], include_superseded?: false, include_archive?: false, limit?: 1–50 = 10}` | `[{id, kind, topic_key, status, title, snippet, rank}]` | FTS5 `bm25`; archive snippets wrapped as untrusted |
| 8 | `resolve_conflict` | `{project, conflict_id, resolution: a_supersedes_b\|b_supersedes_a\|both_valid\|merge, note: str (required), merged_entry?: Entry}` | `{resolved: true, active_ids}` | `merge` requires `merged_entry`; history rows written |
| 9 | `prepare_handoff` | `{project, mode: quick\|standard\|detailed\|knowledge_package = standard, profile?: str, format?: md\|txt, final_updates?: checkpoint shape}` | `{files: [{path, bytes, est_tokens_range, role}], manifest_path, report: {score, checks[], missing[], coverage}, summary_3_lines, warnings}` | Writes into `~/.contextbridge/exports/<slug>/…`; `handoff_runs` row |
| 10 | `import_context` | `{project?, source_type: package\|markdown\|transcript_claude_code\|claude_export\|text, path? \| text?, action: stage\|commit\|discard = stage, staging_id?, select?: [index]}` | stage → `{staging_id, items preview (untrusted-wrapped), redactions}`; commit → per-item results | Size limit (default 20 MB); UTF-8; path must be a regular file; content never executed |
| 11 | `get_budget_status` | `{project?, session_key?}` | `{context:{…}\|"unavailable", five_hour, seven_day, stale, est_handoff_cost_range, feasible, recommendation, context_health}` | Read-only |
| 12 | `forget` | `{project, target_type: entry\|session\|project, target_id, confirm: true, confirm_project_name?: str}` | `{deleted, snapshot_id}` | Snapshot first; project delete requires exact name typed |

**Changes from the spec, with reasons:**
- `import_context` gets an `action` parameter (stage/commit/discard). §9 needs staging + confirmation, and doing that with one tool keeps the count at 12.
- `remember` gets a separate `confirm: [entry_id]` list (implemented in M2; it replaced the per-item `confirms_id` from the first draft because the input schema stays simpler). That gives the "explicit call to change provenance" required by §5.2 without a 13th tool.
- `resolve_conflict` takes `project`, so every project-data tool is explicitly scoped (§4 row 5).
- There is a hard inline output cap and file fallback, because of V6.

Prompts: `resume`, `handoff`, `checkpoint` (arguments: `project`). Resources: `ctx://projects`, `ctx://{project}/brief|state|knowledge|handoff/latest`. Neither is required: everything also works via tools (V9).

Server `instructions`: ~180 words covering the six §7.4 rules. The tool descriptions **repeat** the key habits (e.g. `checkpoint` says "call every ~20–30 min or when the user mentions limits/switching"), because Desktop instruction support is unverified (V9).

---

## 7. Schema (SQLite, migration 0001)

### 7.1 Connection settings
`PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000; PRAGMA foreign_keys=ON; PRAGMA synchronous=NORMAL;`
Before every migration, the DB is backed up with SQLite's online backup API to `backups/pre-migrate-<ver>-<ts>.db`. The last N (default 10) backups are kept. Directory mode `700`, files `600`.

### 7.2 Tables (abridged DDL)

```sql
schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL, checksum TEXT NOT NULL);

projects(id INTEGER PK, slug TEXT UNIQUE NOT NULL, name TEXT NOT NULL, objective TEXT, problem TEXT,
  target_users TEXT, scope TEXT, success_criteria TEXT, linked_dir TEXT,
  knowledge_version INTEGER NOT NULL DEFAULT 0, state_version INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, archived_at TEXT);

global_memory(id INTEGER PK, kind TEXT CHECK(kind IN ('preference','convention','fact')),
  topic_key TEXT NOT NULL, title TEXT, body TEXT, provenance TEXT, created_at, updated_at);

entries(id INTEGER PK, project_id INTEGER REFERENCES projects ON DELETE CASCADE, -- NULL never used; globals live in global_memory
  session_id INTEGER REFERENCES sessions,
  kind TEXT NOT NULL CHECK(kind IN ('requirement','decision','rejected_approach','constraint','preference',
       'fact','explanation','bug','fix','lesson','milestone','risk','open_question','reference','convention')),
  topic_key TEXT NOT NULL,            -- normalized: ^[a-z0-9_]+(\.[a-z0-9_]+)*$
  title TEXT NOT NULL, body TEXT NOT NULL DEFAULT '', rationale TEXT, alternatives TEXT, -- JSON array
  status TEXT NOT NULL CHECK(status IN ('active','superseded','rejected','resolved','outdated','unverified')),
  provenance TEXT NOT NULL CHECK(provenance IN ('user_stated','ai_suggested','user_confirmed','verified','imported')),
  confidence TEXT NOT NULL CHECK(confidence IN ('confirmed','assumed','uncertain')),
  importance TEXT NOT NULL CHECK(importance IN ('critical','important','optional')),
  supersedes_id INTEGER REFERENCES entries, superseded_by_id INTEGER REFERENCES entries,
  source_ref TEXT, norm_hash TEXT NOT NULL,   -- hash of normalized text, fast dedupe pre-filter
  created_at, updated_at, last_verified_at, version INTEGER NOT NULL DEFAULT 1,
  CHECK (kind NOT IN ('decision','rejected_approach') OR rationale IS NOT NULL));
CREATE INDEX entries_topic ON entries(project_id, kind, topic_key, status);

entry_history(id PK, entry_id REFERENCES entries, version INTEGER, snapshot_json TEXT NOT NULL,
  change_type TEXT CHECK(change_type IN ('create','update','merge','supersede','confirm','resolve','restore','delete')),
  changed_at TEXT, session_id INTEGER);

conflicts(id PK, project_id, topic_key, kind, entry_a, entry_b, status CHECK(status IN ('open','resolved')),
  resolution, note, detected_at, resolved_at);

live_state(id PK, project_id, section TEXT CHECK(section IN ('architecture','stack','implemented','in_progress',
  'broken','needs_testing','postponed','blockers','dependencies','outstanding_decisions','environment','repo')),
  content TEXT NOT NULL, refs TEXT /* JSON entry ids */, version INTEGER, updated_at, UNIQUE(project_id, section));
live_state_history(id PK, live_state_id, version, content, refs, changed_at, session_id);

tasks(id PK, project_id, title, status CHECK(status IN ('todo','in_progress','blocked','done','postponed')),
  priority INTEGER, depends_on TEXT /* JSON ids */, expected_outcome, notes, created_at, updated_at);
task_history(id PK, task_id, snapshot_json, changed_at, session_id);

sessions(id PK, project_id, label TEXT, client TEXT, account_label TEXT, external_key TEXT /* e.g. Claude Code session_id */,
  started_at, ended_at, summary, where_stopped, next_action,
  capture TEXT CHECK(capture IN ('checkpoints','transcript','both','none')) DEFAULT 'none');
checkpoints(id PK, session_id, project_id, summary, where_stopped, next_action, kind CHECK(kind IN ('manual','auto','emergency','handoff')), created_at);

snapshots(id PK, project_id, component CHECK(component IN ('knowledge','state','handoff')), version INTEGER,
  label, reason, content_hash TEXT, codec TEXT DEFAULT 'zstd', payload BLOB, created_at);
raw_archive(id PK, project_id, session_id, source_type, title, source_path, sha256, codec, content BLOB, created_at);
staging(id PK, project_id, source_type, items_json TEXT, redactions_json, created_at, expires_at);
file_refs(id PK, project_id, path, purpose, included_in_exports BOOL, required BOOL, last_seen_commit);
telemetry(id PK, session_key, project_id NULL, ts, source, context_used_pct REAL, context_used_tokens INT,
  context_window_tokens INT, five_hour_pct REAL, five_hour_reset_at INT, seven_day_pct REAL, seven_day_reset_at INT);
  -- "stale" is computed at read time (now - ts > configured age), not stored: a stored flag would itself go stale.
handoff_runs(id PK, project_id, mode, profile, estimated_tokens_low, estimated_tokens_high, actual_chars,
  bytes, file_count, validation_score, created_at);

-- FTS5 (external content, kept in sync by triggers)
CREATE VIRTUAL TABLE entries_fts USING fts5(title, body, rationale, content='entries', content_rowid='id',
  tokenize='porter unicode61');
CREATE VIRTUAL TABLE archive_fts USING fts5(title, text);   -- stores decompressed text (contentless option considered; see risks)
```

**Changes from the spec:**
- An explicit `checkpoints` table (sessions can have many checkpoints).
- A `staging` table for imports.
- A project-level `knowledge_version`/`state_version` counter, so the handoff's frontmatter versions are trivial to produce.
- `telemetry.stale` is computed, not stored.
- Global memory lives in its own table instead of `entries.project_id IS NULL`. Every `entries` query is then scoped by a non-null `project_id`, which makes isolation easy to prove.

### 7.3 Synthesis rules (deterministic)
1. **Normalize `topic_key`**: lowercase, trim, spaces/`-`/`/` → `_` within segments, collapse dots. Suggest existing keys with an edit distance ≤ 2 or the same last segment (`similar_topic_keys`).
2. **Provenance gate**: `provenance=ai_suggested ∧ kind=decision ∧ status=active` → rejected (`provenance_violation`) with a hint to store it as `status=unverified` or `kind=open_question`.
3. **Near-duplicate**: same project + kind + topic_key, and the token-set Jaccard similarity of normalized `title+body` is **≥ 0.85** (configurable) → merge into the existing entry (new `entry_history` row, `version+1`, `last_verified_at=now`); result `merged`.
4. **Same-topic active** (only for *singular* kinds: `decision`, `requirement`, `constraint`, `convention`, `preference`. **Change from spec §5.2**, which applied it to every kind: two active bugs, lessons or facts on `auth.login` are normal, and flagging them would bury real conflicts in noise). If another `active` entry exists with the same project+kind+topic_key and is not a near-duplicate:
   - if `supersedes_id` points to it → the old entry becomes `superseded`, links are set both ways, result `saved`;
   - otherwise → the new entry is stored as `unverified`, a `conflicts` row is opened, result `conflict` (the server never picks a winner).
5. **Consistency**: the `refs` of each Live State section must point to active entries. Additionally, each `stack`/`architecture` section is text-checked against the titles of active `decision` entries whose topic_key starts with the same domain (e.g. `backend.*`). A ref to a superseded entry, or text naming a value from a superseded decision while omitting the active one → warning `inconsistent_state`. (Heuristic; documented as such.)

---

### 7.4 Snapshots and restore (implemented in M3)
- Snapshots are canonical JSON, zstd-compressed, sha256-verified on load. An unchanged component isn't re-snapshotted.
- A checkpoint snapshots knowledge + state in the same transaction.
- Restore always snapshots the current version first, so it's reversible (`undo` is printed).
- **Knowledge restore never deletes**: entries newer than the snapshot become `outdated` (P2: knowledge is permanent); conflicts opened since are closed with a note.
- **State restore** replaces sections and tasks; removed tasks are recoverable via the pre-restore snapshot.
- Retention: everything for `snapshots.keep_all_days` (30), then one per component per day; the newest per component is always kept.
- Whole-DB backups are opportunistic (no daemon): at startup and after checkpoints when the newest backup is older than `database.backup_interval_hours` (24). `ctxb backup --restore` validates integrity first and takes a safety backup.

## 8. Generation, packages, sizing

- **Ranking** (within a budget): score = importance weight (critical 100 / important 30 / optional 5) + status (active > unverified > superseded) + recency decay + whether the topic is referenced by Live State or open tasks. **Critical active entries are always included** (as a one-line digest `[D-0123 · user_confirmed · 2026-10-10] title`) before anything else. Overflow goes to companion files; nothing is dropped from the package as a whole.
- **Evolution**: follow `supersedes_id` chains per topic_key, render oldest → newest with dates and rationale, and mark obsolete ones.
- **Coverage**: from `sessions.capture` + `raw_archive`. A session with `capture='none'` is listed as a gap (Test 16).
- **Templates**: Python `string.Template`-style section builders (no Jinja dependency). Format version 1 as in the spec §8.2/8.3.
- **Token estimate**: `chars / 3.6` (prose) and `chars / 3.0` (code/JSON), × safety factor 1.15. Reported as a range (`low = est × 0.85`, `high = est × 1.15`) with a confidence label. Calibrated per project from `handoff_runs`.
- **Formats**: because `.md` is **not** in Claude's documented upload types (V10), the default upload profiles prefer **`.txt`** for chat/project uploads (same Markdown content, `.txt` extension), and `.md` remains the local/default file. Each profile carries this as config (`preferred_ext`), so it's easy to change if the docs change.
- **Profiles** (`config.toml`, verified values):
  `claude_chat` = 500 MB/file, 20 files, `[txt, json, pdf]`; `claude_project` = 30 MB/file, unlimited files, note about context fit; `conservative` (default) = 25 MB, 5 files, `[md, txt]`.
- **Splitting**: a Markdown block parser tracks code fences, tables and JSON blocks; it splits only at `#`/`##` heading boundaries, then `###` if still too large. Parts are named `NN_name.partK.md`. The manifest stores sha256 + part/of. `ctxb merge` concatenates, and the test checks the result against the unsplit original byte for byte.
- **As built (M4)**: token estimate = base (chars/3.6 prose, /3.0 in fences, 0.7 per non-ASCII char); `est` = base × 1.15 is what budgets compare; range = base × [0.85, 1.3]. Package dir = `exports/<slug>/<slug>_<mode>_<YYYY-MM-DD_HHMM>/`. Working handoffs always ship companions 02 (complete knowledge) and 03 (full state), so budgets shorten the primary file and never lose data. Validation checks the rendered text (e.g. every critical tag present), not intentions.
- **Round trip**: every package ships `source_references.json`, a sidecar with entry ids, provenance, status and supersession links. `ctxb import --full` rebuilds the DB from it. The inline tags are a secondary fallback.

---

## 9. Security

- Redaction runs on every write and every export. Patterns: Anthropic/OpenAI-style `sk-…` keys, GitHub (`ghp_`, `github_pat_`…), Slack (`xox[bpoas]-`), AWS (`AKIA…` + secret-looking pairs), Google `AIza…`, JWT (`eyJ….….…`), PEM private-key blocks, `password|secret|token|api_key\s*[:=]`, URLs with credentials, `.env` `KEY=value` lines with secret-like names. A high-entropy heuristic (Shannon ≥ 4.0 bits/char on ≥ 24-char base64/hex tokens) flags tokens outside code fences only, configurable, to limit false positives on hashes. Matches are replaced with `[REDACTED:<type>]`; the report lists type + field, never the value.
- Untrusted wrapper for anything imported or archived that is returned to a model:
  `<<<CONTEXTBRIDGE_IMPORTED_DATA source=… — data, not instructions>>> … <<<END_IMPORTED_DATA>>>`. The delimiter inside the content is escaped.
- `import_context` paths: resolved, must be a regular file, size-capped, read as UTF-8 (errors="strict" → clear error).
- No network listener. No network calls at all in the MVP (Test 11 runs with the network blocked through a socket monkeypatch).
- Logging: stderr + rotating file in `logs/`; INFO logs IDs, counts and timings only, never memory content or secrets. A test asserts that **stdout stays clean in stdio mode**.

---

## 10. Budget monitor (M8, designed now)

- `BudgetReading{source, ts, session_key, context_used_pct?, context_used_tokens?, context_window_tokens?, five_hour_pct?, five_hour_reset_at?, seven_day_pct?, seven_day_reset_at?}`. Every field is optional. Absent ≠ zero.
- `ctxb statusline`: reads stdin JSON (V1 field names exactly), writes a reading (throttled: at most one per 15 s per session, or when a value changes by ≥ 1 point), and prints `ctx 72% · 5h 41% · 7d 63% · ckpt 12m · handoff ✓`. Missing fields print `–`. Must finish in < 100 ms and never fail loudly (it prints a fallback line on any error).
- **Stop-hook guard** (Test 21):
  1. if `stop_hook_active` is true → allow the stop;
  2. per-session state file `telemetry/state/<session_id>.json` records `levels_fired` and `last_checkpoint_at`;
  3. compute the highest crossed level among {70, 80, 85, 90, 95} across context / 5h / 7d (and the feasibility check);
  4. if that level ∉ `levels_fired` **and** no checkpoint since it was crossed → add it to `levels_fired` first (write-ahead), then emit `decision: block` with a level-specific reason (80 → "sync unsaved decisions with remember", 85 → "call checkpoint", 90 → "recommend prepare_handoff to the user", 95 → "emergency: call checkpoint now");
  5. otherwise allow. The built-in 8-continuation cap is the last backstop.
- **Feasibility**: `required = final_review (≈1.5k) + tool calls (≈0.5k each × 2) + response (≈0.5k) + reserve (10 % of window)`. `available = window × (1 − used_pct)`. If `available < required + next_op (≈3k)` → act now regardless of the percentage.
- **Desktop/web**: `telemetry: "unavailable"` everywhere; nudges come from `checkpoint_status` (time since checkpoint, writes since checkpoint).

---

## 11. Tech stack decision

| Criterion | Python 3.14 + `mcp` 2.3 (FastMCP) | TypeScript + `@modelcontextprotocol/sdk` 1.32 | Go 1.27 + `go-sdk` v1.8 |
|---|---|---|---|
| SDK maturity / docs | Official, very widely used; decorators for tools/prompts/resources; v2 line | Official, the reference SDK, most examples | Official, stable v1.x, maintained with Google; smaller example base |
| SQLite + FTS5 | **stdlib `sqlite3`, FTS5 verified on this Mac** — zero deps | `node:sqlite` (Node 24) or `better-sqlite3` (native build) | `modernc.org/sqlite` (pure Go, FTS5 included) — one dep |
| zstd | **stdlib `compression.zstd` (3.14)** | Node 24 has zlib zstd (experimental) / or dep | `klauspost/compress` — one dep |
| Config (TOML) | stdlib `tomllib` (read) | dep | dep |
| Testing | pytest, mature; easy subprocess MCP tests | vitest/jest | `go test`, excellent, race detector |
| Distribution on macOS | `uv tool install .` (needs uv; not yet installed) | `npm -g` | **single static binary** — best |
| Runtime deps (est.) | `mcp` (brings pydantic, anyio, starlette…) + `pytest` (dev) | sdk + zod + sqlite binding | go-sdk + sqlite + compress |
| Startup time per process | ~300–500 ms (pydantic import) | ~150 ms | ~10 ms |
| Status-line/hook latency (runs often) | slowest; mitigate with a light CLI path that avoids importing `mcp` | medium | **best** |
| Maintainability | Very readable; types via mypy/pyright strict | Strong types | Strong types, simple deploy |

**Recommendation: Python 3.14 + official `mcp` SDK**, with Go as a close second.
Why: the three heaviest needs (SQLite with FTS5, zstd, TOML) are **all in the standard library on this exact machine**, so the core ends up with essentially one runtime dependency (`mcp`). FastMCP covers tools, prompts, resources and instructions directly. pytest makes the 23 acceptance tests the cheapest to write.
The Python weakness is per-invocation startup for the status line and hooks. I'd handle it by keeping `ctxb statusline`/`ctxb hook *` on a lean import path (stdlib only, no `mcp`/pydantic import), and measuring it in M1 (target < 100 ms). If that target can't be met, the fallback is a tiny Go helper for just those two commands, not a rewrite.
Go would win if single-binary distribution and hook latency matter more to you than iteration speed and test ergonomics.

**Dependencies (Python):**
- runtime: `mcp>=2.3,<3` (MCP protocol; pulls in pydantic for schema validation, which we also reuse for input models);
- dev: `pytest`, `pytest-timeout`, `mypy` (or pyright);
- tooling: `uv`.
- Nothing else: no ORM, no Jinja, no click (stdlib `argparse`), no zstandard package (stdlib).

---

## 12. Risks

| Risk | Mitigation |
|---|---|
| The model doesn't call `remember`/`checkpoint` often enough (the biggest risk) | Repeated nudges: instructions + tool descriptions + `checkpoint_status`; Claude Code Stop-hook backstop; tune in M7 with real use |
| Desktop ignores server `instructions` / prompts (V9) | Tools-only workflow; CLAUDE.md / Project-instructions snippet |
| Conflict detection misses semantic conflicts with different `topic_key`s | `similar_topic_keys` suggestions; the consistency validator; the model is told to reuse keys from `get_context` |
| Similarity threshold merges two genuinely different entries | Merge keeps full history (reversible via `restore`); threshold configurable; tests with fixtures |
| Python startup too slow for the status line | Lean import path; measured in M1; Go helper fallback |
| WAL + several processes on one DB | `BEGIN IMMEDIATE`, busy timeout, short transactions; Test 12 with two real processes |
| SessionEnd 1.5 s timeout kills emergency work | Detached child process; also run on StopFailure |
| FTS index on archive text duplicates storage | Measure; option to use a contentless FTS5 table (`content=''`) over decompressed text |
| Redaction false positives on hashes/IDs | Entropy check only outside code fences, plus an allowlist; report what was redacted |
| `.md` not accepted by an upload target | `.txt` default per profile (V10) |
| Spec features drift (hooks, limits) | Values in config, `ctxb doctor` checks client configs, LIMITATIONS.md dated |

---

## 13. Decisions for your approval

1. **Architecture B + C adapters** (§4). Approve / change?
2. **Language: Python 3.14 + `mcp` SDK** (§11), Go as runner-up. Approve, or choose Go/TypeScript?
3. **Install `uv` via Homebrew** in M1 (`brew install uv`). OK?
4. **`git init` this folder** (`aryan's connector`) as the repo root, or create `contextbridge/` inside it?
5. **Tool-surface changes** in §6 (`import_context` action param, `remember.confirms_id`, `resolve_conflict.project`, 8k inline cap). OK?
6. **`.txt` default for upload profiles** because `.md` isn't in Claude's documented types. OK?
7. **SessionStart auto-context in Claude Code** (new capability, V4). Include it in M7/M8?

---

## 14. Milestone plan (unchanged from spec, with additions)
- M1 adds: measure CLI cold start for `statusline`.
- M4 adds: the 8k inline cap + file fallback.
- M7 adds: SessionStart hook; manual tests for V8 and V9.
- M8 uses the exact hook and status-line field names from §2.
