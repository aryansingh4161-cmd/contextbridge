# Acceptance tests (spec §16 + BLUEPRINT §0) → where they are verified

Every row lists automated tests (run with `uv run pytest`; `tests/test_acceptance_map.py` fails if any
reference below stops existing) and, where a real client was needed, the manual check recorded in
`PROGRESS.md`.

| # | Test | Automated | Manual / real client |
|---|---|---|---|
| 1 | Persistence | `tests/integration/test_mcp_stdio.py::test_persists_across_restart`, `tests/integration/test_mcp_stdio.py::test_persistence_identical_after_restart` | |
| 2 | Account switch | `tests/integration/test_mcp_stdio.py::test_switch_accounts_flow` | M4: fresh Claude Code session resumed "Folio" via `get_context` |
| 3 | Conversation handoff | `tests/integration/test_mcp_stdio.py::test_switch_accounts_flow`, `tests/unit/test_generation.py::test_working_handoff_separates_history_from_state` | M4: session with NO connector, file only → next action, decision + reason, rejected approach |
| 4 | Compression quality | `tests/unit/test_generation.py::test_critical_entries_and_exact_values_survive_tiny_budget` | |
| 5 | Conflicts | `tests/unit/test_knowledge.py::test_conflict_on_singular_kind_is_recorded_not_chosen`, `tests/unit/test_generation.py::test_conflict_surfaced_and_coverage_gap_named` | M2: real session resolved Flask/FastAPI with a note |
| 6 | Isolation | `tests/unit/test_knowledge.py::test_isolation_between_projects`, `tests/unit/test_generation.py::test_project_isolation_in_packages`, `tests/unit/test_import.py::test_import_never_touches_other_projects`, `tests/unit/test_forget.py::test_cross_project_ids_are_refused` | |
| 7 | Recovery | `tests/unit/test_state_checkpoints.py::test_corrupt_latest_snapshot_falls_back_to_earlier`, `tests/unit/test_state_checkpoints.py::test_scheduled_backup_and_restore` | |
| 8 | Security | `tests/unit/test_import.py::test_injection_and_secrets_are_data_not_instructions`, `tests/unit/test_import.py::test_source_title_is_cleaned` | M6: real session ignored "delete the project" inside an imported file |
| 9 | Size limits | `tests/unit/test_generation.py::test_split_and_file_count_against_profile`, `tests/unit/test_generation.py::test_split_is_byte_exact_and_respects_fences`, `tests/integration/test_cli.py::test_handoff_context_link_merge_cli` | |
| 10 | File count | `tests/acceptance/test_file_count.py::test_more_than_20_files_under_a_20_file_profile` | |
| 11 | Offline | `tests/acceptance/test_offline.py::test_everything_works_offline` | |
| 12 | Multi-client | `tests/integration/test_concurrency.py::test_concurrent_writes_no_loss`, `tests/integration/test_concurrency.py::test_concurrent_same_topic_race_yields_conflicts_not_two_winners` | |
| 13 | Five-session synthesis | `tests/unit/test_generation.py::test_five_session_knowledge_package`, `tests/unit/test_generation.py::test_working_handoff_separates_history_from_state` | |
| 14 | Three components | `tests/unit/test_state_checkpoints.py::test_state_update_never_touches_knowledge`, `tests/unit/test_state_checkpoints.py::test_consistency_validator`, `tests/unit/test_generation.py::test_get_context_scopes_budget_and_file_fallback` | |
| 15 | Provenance | `tests/unit/test_knowledge.py::test_provenance_gate_and_confirmation`, `tests/unit/test_migrate.py::test_schema_enforces_provenance_rule` | |
| 16 | Coverage honesty | `tests/unit/test_state_checkpoints.py::test_sessions_without_checkpoint_stay_uncaptured`, `tests/unit/test_generation.py::test_conflict_surfaced_and_coverage_gap_named` | |
| 17 | Budget telemetry | `tests/unit/test_budget.py::test_statusline_records_and_renders`, `tests/unit/test_budget.py::test_unavailable_and_stale_are_honest` | M8: status line live on the user's Mac |
| 18 | Threshold behaviour | `tests/unit/test_budget.py::test_each_threshold_fires_exactly_once` | |
| 19 | Insufficient budget | `tests/unit/test_budget.py::test_feasibility_acts_before_percentages` | |
| 20 | Emergency | `tests/unit/test_budget.py::test_emergency_handoff_is_labelled_and_archives`, `tests/unit/test_budget.py::test_session_end_hook_returns_fast_and_spawns_detached` | |
| 21 | Stop-hook loop guard | `tests/unit/test_budget.py::test_each_threshold_fires_exactly_once`, `tests/unit/test_budget.py::test_stop_hook_active_is_always_honoured`, `tests/unit/test_budget.py::test_checkpoint_after_crossing_satisfies_level` | M8: real session at 86 % checkpointed once; next turn no repeat |
| 22 | PreCompact | `tests/unit/test_budget.py::test_pre_compact_archives_transcript_for_the_linked_project` | |
| 23 | Resume after emergency | `tests/unit/test_budget.py::test_emergency_handoff_is_labelled_and_archives`, `tests/unit/test_import.py::test_handoff_file_without_sidecar` | |
| 24 | Abrupt end at an arbitrary point | `tests/acceptance/test_abrupt_end.py::test_abrupt_end_between_writes`, `tests/acceptance/test_abrupt_end.py::test_abrupt_end_mid_handoff`, `tests/acceptance/test_abrupt_end.py::test_abrupt_end_before_anything`, `tests/acceptance/test_abrupt_end.py::test_after_stop_failure_hook` | |

**Test 24 metric** (share of acknowledged writes recovered after a SIGKILL): **100 %** across 12 random
kill points, 7 mid-handoff kills (with a test-only write delay so packages really are half-written)
and a kill before the first write.
