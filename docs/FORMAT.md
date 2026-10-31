# ContextBridge file formats (format version 1)

Two families. Both carry `contextbridge_format: 1` (or `"format": 1`).

| | Handoff / knowledge package | Full export (migration archive) |
|---|---|---|
| Made by | `prepare_handoff` tool, `ctxb handoff` | `ctxb export --full` |
| For | an AI to read (attach/upload, or read on the same Mac) | moving memory to another Mac / data dir |
| Shape | a folder of Markdown (`.md` or `.txt`) + JSON | one `.zip` |
| Contains | rendered knowledge + state + coverage; sidecar with entries | every row of every table for the chosen projects |
| Re-import | `ctxb import --full <package dir>` (entries, state, tasks, sessions, checkpoints) | `ctxb import --full <zip>` (everything, incl. history and snapshots) |

## 1. Packages

Folder: `~/.contextbridge/exports/<slug>/<slug>_<mode>_<YYYY-MM-DD_HHMM>/` (mode 700, files 600; a new
folder every time, never overwritten).

| File | Role | Notes |
|---|---|---|
| `00_manifest.json` | manifest | not for upload |
| `01_START_HERE_handoff.<ext>` | primary, self-sufficient | budgeted: quick ≈ 3k, standard ≈ 10k, detailed ≈ 30k tokens (`[handoff.budgets]`) |
| `02_project_knowledge.<ext>` | every entry in full, evolution, obsolete history | working handoffs |
| `03_current_state.<ext>` | full Live State, all tasks, sessions | |
| `04_decisions_and_evolution`, `05_tasks_and_issues`, `06_technical_reference` | split by topic | `knowledge_package` only (then 02 = requirements, rules, open questions, milestones) |
| `02_companion_files.<ext>` | 02–06 consolidated | only when the profile's `max_files` would be exceeded |
| `NN_name.partK.<ext>` | a part of a split file | split at `#`/`##` (then `###`) headings, never inside a code fence |
| `source_references.json` | sidecar | not for upload; for re-import |
| `attachments/A-<id>_<name>` | attachment | copies of the project's attached files; included by default only in `knowledge_package` (`--with-attachments` / `include_attachments` to choose). Manifest role `attachment`, with sha256 and mime |

`<ext>` comes from the upload profile's `preferred_ext` (`txt` for `claude_chat` / `claude_project`, because
`.md` is not in Claude's documented upload types) or `format`. The content is Markdown either way.

### 1.1 Primary file

YAML-style front matter (values are JSON literals), then the sections of spec §8.2, in this order:
instructions for the reading AI; ⚠ unresolved conflicts (only if any); 1 where we stopped; 2 next action
+ open tasks; 3 identity + requirements; 4 current state (+ milestones); 5 current decisions; 6 evolution
(superseded = obsolete, oldest → newest); 7 rejected approaches; 8 constraints, conventions,
preferences; 9 bugs & lessons; 10 technical reference; 11 files & repo; 12 open questions, risks,
unconfirmed items, consistency warnings; 13 provenance legend & coverage (+ what was left out for budget).

Front matter keys: `contextbridge_format, package_type (session_handoff|knowledge_package), level,
project, project_slug, generated_at, knowledge_version, state_version, sources_covered, coverage_gaps,
estimated_tokens, completeness_score, complete`. `complete` is `true` only if nothing was left out of this
file and no validation item is missing.

Item tags: `[<K>-<id 4 digits> · <provenance> · <created YYYY-MM-DD>( · <status if not active>)]`.
Kind letters: D decision, R requirement, X rejected approach, C constraint, CV convention, P preference,
F fact, E explanation, B bug, FX fix, L lesson, M milestone, K risk, Q open question, REF reference.
Tasks: `[T-<id> · <status> · P<priority>]`. Marks: `**critical**`, `⚠ unconfirmed AI suggestion`,
`⚠ imported, unconfirmed`, `⚠ unverified`.

### 1.2 `00_manifest.json`

```json
{
  "contextbridge_format": 1, "package_type": "session_handoff", "level": "standard",
  "project": {"name": "…", "slug": "…"}, "generated_at": "…",
  "versions": {"knowledge": 42, "state": 17}, "profile": "conservative", "format": "md",
  "load_order": ["01_START_HERE_handoff.md", "02_project_knowledge.md", "03_current_state.md"],
  "files": [{"name": "…", "role": "handoff|knowledge|state|tasks|sidecar", "bytes": 0,
             "est_tokens": {"low": 0, "est": 0, "high": 0, "confidence": "…"}, "sha256": "…",
             "part": 1, "of": 1, "required": true, "upload": true, "source": "01_START_HERE_handoff",
             "source_sha256": "(only on split files: hash of the reassembled whole)"}],
  "upload_plan": [["batch 1 file names"], ["batch 2 …"]],
  "estimated_tokens_total": {"low": 0, "est": 0, "high": 0},
  "coverage": {"sessions_captured": 0, "sessions_total": 0, "gaps": []},
  "validation": {"score": 0.92, "missing": []},
  "complete": false, "omitted_from_primary": {"fact": 12}, "warnings": []
}
```

`ctxb merge <package dir>` concatenates the parts of each split file in order and checks the result
against `source_sha256` (byte for byte).

### 1.3 `source_references.json`

`{"contextbridge_format": 1, "kind": "source_references", "generated_at", "project" (incl. id),
"entries" (all columns, `alternatives` as a list), "conflicts", "live_state", "tasks", "sessions",
"checkpoints"}`. Secrets are redacted as in every export.

## 2. Full export archive

`contextbridge_full_<YYYY-MM-DD_HHMMSS>.zip` (mode 600; never overwritten):

- `manifest.json`: `{"format": 1, "kind": "contextbridge_full_export", "schema_version", "exported_at",
  "app_version", "projects": [{"slug", "name", "file", "sha256", "counts"}], "global_memory": {"file",
  "sha256", "count"}}`
- `projects/<slug>.json`: `{"format": 1, "kind": "contextbridge_project_dump", "schema_version",
  "tables": {"projects": […], "sessions": […], "entries": […], "entry_history": […], "conflicts": […],
  "live_state": […], "live_state_history": […], "tasks": […], "task_history": […], "checkpoints": […],
  "snapshots": […], "raw_archive": […], "file_refs": […], "handoff_runs": […]}}` — rows exactly as in the
  database; BLOB columns as `{"$b64": "…"}`.
- `global_memory.json`: rows of `global_memory`.
- `attachments/<slug>/<sha256>.<ext>`: the attached files' bytes, listed per project in the manifest
  (`attachment_files`); verified against their sha256 (the file name) on import.

Import rules: every member's sha256 is checked before anything is written; a newer `schema_version`
than the installed one is refused; a pre-import backup of the DB is taken; each project imports in one
transaction; an existing slug is refused (`--as NEW-SLUG` to import a copy); ids are preserved when free,
otherwise remapped with every reference rewritten (and the id map reported); FTS indexes are rebuilt.
