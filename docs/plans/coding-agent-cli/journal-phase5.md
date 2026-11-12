# Phase 5 journal: Codex and Agy drivers

Source: Refract commit 99d155bd. The read-only snapshot is at /tmp/refsrc/server/tests.

## Status

- Ported: the test keeps its assertion, adapted to the typed-event API.
- Rewritten: the test keeps its intent, but the expected value changes under DESIGN.md or the PLAN Phase 5 invariants.
- Partial: only part of the Refract test has a Phase 5 counterpart. The missing part is noted.
- Dropped: the test covers a Refract concept that the new package does not have.
- Gap: the test should be ported but is not in Phase 5. Follow-up is required before the Codex and Agy coverage is complete.

## Shared helper decisions

- `tests/fixtures/usage/codex.jsonl` and `tests/fixtures/usage/agy.jsonl` are byte-identical to Refract.
- `tests/fixtures/fake_cli/fake_cli.py` and `tests/cli_stub.py` are unchanged. `log_text`, `last_message`, and `--log-file` already support the Codex and Agy tests.
- `tests/test_registry.py` now asserts the exact built-in set `{claude, pi, codex, agy}`.
- `tests/test_drivers.py` and `tests/test_telemetry.py` contain Codex or Agy argv cases and no Codex or Agy test functions. The argv cases are mapped below.

## test_codex_backend.py

| Refract test | Status | New test or reason |
|---|---|---|
| test_recorded_stream_pins_text_session_and_telemetry | Ported | test_codex_driver.py::test_recorded_stream_pins_text_session_and_telemetry |
| test_resume_argv_and_prompt_delivery_are_pinned | Rewritten | test_codex_driver.py::test_resume_argv_and_prompt_delivery_are_pinned. The argv has no `--cd` and no `--yolo`. The prompt is sent on stdin with `-`. |
| test_thread_started_wins_over_last_message_and_stdout_session_line | Ported | test_codex_driver.py::test_thread_started_wins_over_last_message_and_stdout_session_line |
| test_last_message_session_line_is_used_without_thread_event | Ported | test_codex_driver.py::test_last_message_session_line_is_used_without_thread_event |
| test_stdout_session_line_is_used_without_thread_or_last_message | Ported | test_codex_driver.py::test_stdout_session_line_is_used_without_thread_or_last_message |
| test_fresh_run_without_session_source_warns_and_stores_no_id | Ported | test_codex_driver.py::test_fresh_run_without_session_source_stores_no_session_id, which asserts the `no_session_id` warning |
| test_last_message_file_is_answer_when_no_agent_item_exists | Ported | test_codex_driver.py::test_last_message_file_is_answer_when_no_agent_item_exists |
| test_blank_last_message_falls_back_to_agent_items | Ported | test_codex_driver.py::test_blank_last_message_falls_back_to_agent_items |
| test_nonzero_exit_with_agent_message_is_execution_error | Rewritten | test_codex_driver.py::test_nonzero_exit_with_agent_message_is_retryable_execution_error. DESIGN.md maps execution errors to retryable. |
| test_nonzero_exit_with_fatal_diagnostic_stays_execution_error | Rewritten | test_codex_driver.py::test_auth_diagnostic_beats_a_nonzero_exit. PLAN Phase 5 requires an auth diagnostic to take precedence over a nonzero exit. |
| test_timeout_keeps_partial_text_and_timeout_class | Ported | test_codex_driver.py::test_timeout_keeps_partial_text_and_timeout_class |
| test_fatal_token_with_agent_message_does_not_fail_the_run | Ported | test_codex_driver.py::test_fatal_token_with_agent_message_does_not_fail_the_run |
| test_rollout_of_exact_session_supplies_model_and_context | Ported | test_codex_rollout.py::test_rollout_supplies_the_model_name and test_codex_rollout.py::test_rollout_token_count_wins_over_turn_consumption_snapshot |
| test_exit_zero_without_agent_message_is_request_fatal_and_never_returns_jsonl | Rewritten | test_codex_driver.py::test_exit_zero_without_agent_message_is_no_output_and_never_returns_jsonl. PLAN Phase 5 maps empty Codex output to `no_output`. |
| test_blank_last_message_without_agent_message_is_request_fatal | Rewritten | test_codex_driver.py::test_blank_last_message_without_agent_message_is_no_output |
| test_fatal_token_in_diagnostic_without_agent_message_is_fatal_backend | Rewritten | test_codex_driver.py::test_fatal_token_in_diagnostic_without_agent_message_is_agent_fatal_auth. `fatal_backend` no longer exists. |
| test_resume_without_exact_session_source_stores_no_session_id | Ported | test_codex_driver.py::test_resume_without_exact_session_source_stores_no_session_id |
| test_thread_started_sets_session_before_first_poll | Rewritten | test_codex_driver.py::test_thread_started_emits_session_once. Events are emitted in stream order. The poll-ordering check is an internal detail and is not ported. |
| test_events_without_a_started_thread_keep_session_empty | Partial | The empty-session case is covered by test_codex_driver.py::test_fresh_run_without_session_source_stores_no_session_id. The separate event-only variant is not ported. |
| test_collector_is_polled_around_usage_events | Dropped | Tests internal poll call counts. The observable behavior is covered by the rollout end-to-end tests. |
| test_item_started_emits_message_and_tool_start_events | Ported | test_codex_driver.py::test_item_started_emits_message_and_tool_start_events |
| test_item_completed_emits_text_and_tool_completion_events | Ported | test_codex_driver.py::test_item_completed_emits_text_and_tool_completion_events |
| test_non_object_item_is_ignored | Ported | test_codex_driver.py::test_non_object_item_is_ignored |
| test_turn_completed_usage_updates_counters_and_emits | Ported | test_codex_driver.py::test_turn_completed_usage_updates_counters_and_emits |
| test_turn_completed_without_usage_object_is_ignored | Ported | test_codex_driver.py::test_turn_completed_without_usage_object_is_ignored |
| test_turn_completed_sums_consecutive_turns | Ported | test_codex_driver.py::test_turns_sum_across_consecutive_turn_completed_events |
| test_turn_completed_leaves_context_alone_when_rollout_owns_it | Partial | The precedence rule is covered end to end by test_codex_rollout.py::test_rollout_token_count_wins_over_turn_consumption_snapshot. The direct turn-level check is not ported. |
| test_final_usage_records_totals_for_fresh_runs | Ported | test_codex_driver.py::test_final_usage_records_totals_for_fresh_runs. Final totals override turn sums for fresh runs. |
| test_final_usage_totals_override_turn_sums_without_context | Ported | Merged into test_codex_driver.py::test_final_usage_records_totals_for_fresh_runs |
| test_final_usage_without_totals_to_record_emits_nothing | Ported | test_codex_driver.py::test_final_usage_without_totals_to_record_emits_nothing |

## test_agy_backend.py

| Refract test | Status | New test or reason |
|---|---|---|
| test_recorded_stream_pins_text_session_and_telemetry | Ported | test_agy_driver.py::test_recorded_stream_pins_text_session_and_telemetry |
| test_plain_text_output_is_the_reply_and_stream_regex_supplies_session | Ported | test_agy_driver.py::test_plain_text_output_is_the_reply_and_stream_regex_supplies_session |
| test_log_file_session_wins_over_stream_session | Ported | test_agy_driver.py::test_log_file_session_wins_over_stream_session |
| test_first_stream_regex_match_beats_last_conversation_id_field | Ported | test_agy_driver.py::test_first_stream_regex_match_beats_a_later_conversation_id_field |
| test_model_is_taken_from_last_event_that_carries_one | Ported | test_agy_driver.py::test_model_is_taken_from_last_event_that_carries_one |
| test_nonzero_exit_keeps_partial_reply_as_execution_error | Ported | test_agy_driver.py::test_nonzero_exit_keeps_partial_reply_as_execution_error. The outcome is retryable. |
| test_plain_fatal_token_is_not_classified_for_agy_output | Ported | test_agy_driver.py::test_exit_zero_plain_text_is_the_reply_even_with_an_auth_keyword. Renamed to state the expected result. |
| test_no_output_is_no_agent_messages_with_agy_wording | Rewritten | test_agy_driver.py::test_no_output_is_retryable_no_output. DESIGN.md names this outcome `no_output`. |
| test_timeout_reports_seconds | Rewritten | test_agy_driver.py::test_timeout_is_retryable_timeout_and_keeps_partial_text. The seconds text is not part of the `Diagnostic` contract. |
| test_cancel_before_start_is_cancelled | Ported | test_agy_driver.py::test_cancel_before_start_is_cancelled_and_spawns_nothing |
| test_argv_carries_prompt_and_log_file | Rewritten | test_agy_driver.py::test_resume_argv_carries_conversation_prompt_and_log_file. Refract put `--dangerously-skip-permissions` first. Phase 5 never adds it. |
| test_prompt_over_100_kib_fails_before_spawn | Ported | test_agy_driver.py::test_prompt_over_100_kib_fails_before_spawn |
| test_prompt_limit_counts_utf8_bytes_not_characters | Ported | test_agy_driver.py::test_prompt_limit_counts_utf8_bytes_not_characters |
| test_prompt_at_exactly_100_kib_is_spawned | Ported | test_agy_driver.py::test_prompt_at_exactly_100_kib_is_spawned. Skipped on Windows. |
| test_prompt_inside_the_windows_command_line_limit_is_spawned | Ported | test_agy_driver.py::test_prompt_inside_the_windows_command_line_limit_is_spawned. The platform flag is patched, so the check runs on Linux. |
| test_non_utf8_output_does_not_truncate_later_lines | Ported | test_agy_driver.py::test_non_utf8_output_does_not_truncate_later_lines |
| test_missing_workspace_fails_before_spawn | Ported | test_agy_driver.py::test_missing_workspace_is_rejected_before_spawn |
| test_unencodable_prompt_is_request_fatal_with_the_session | Ported | test_agy_driver.py::test_unencodable_prompt_is_request_fatal_and_keeps_the_session (NUL and surrogate cases) |
| test_new_session_argv_uses_new_project_and_log_file_order | Rewritten | test_agy_driver.py::test_new_session_argv_uses_new_project_and_places_args_before_the_prompt. The argv has no permission flag. |
| test_windows_command_line_over_the_limit_is_request_fatal_and_removes_the_log | Ported | test_agy_driver.py::test_windows_command_line_over_the_limit_is_request_fatal_and_removes_the_log |

## test_streaming_backends.py (Codex, Agy, and rollout parts)

| Refract test | Status | New test or reason |
|---|---|---|
| test_codex_streaming_normalization | Rewritten | Split into test_codex_driver.py::test_item_started_emits_message_and_tool_start_events and test_item_completed_emits_text_and_tool_completion_events |
| test_codex_streaming_non_bash_tool_activity_tool_call | Ported | test_codex_driver.py::test_item_started_emits_message_and_tool_start_events asserts `activity == "tool_call"` |
| test_codex_streaming_nested_and_missing_payloads | Partial | test_codex_driver.py::test_non_object_item_is_ignored and test_item_completion_without_start_uses_fallback_entity_ids. The nested-payload assertion is not ported. |
| test_agy_streaming_normalization | Rewritten | test_agy_driver.py::test_agent_response_and_tool_steps_dispatch_before_usage |
| test_agy_current_stream_json_protocol | Ported | Covered by test_agy_driver.py::test_recorded_stream_pins_text_session_and_telemetry, which reads the current `init`, `step_update`, and `result` protocol. Legacy event shapes are not ported. |
| test_agy_streaming_nested_and_missing_payloads | Partial | test_agy_driver.py::test_non_object_step_updates_are_ignored and test_step_without_usable_usage_leaves_counters_and_events_alone. The nested-payload assertion is not ported. |
| test_exact_command_classification_and_unsafe_substring_rejection | Partial | test_codex_driver.py::test_tool_activity_allow_list_is_exact_and_case_sensitive covers the exact allow-list. The unsafe-substring rejection cases are not ported. |
| test_negative_reasoning_and_diagnostics_excluded | Ported | test_codex_driver.py::test_reasoning_items_and_non_json_diagnostics_are_never_emitted_as_text |
| test_classify_tool_activity_unit | Partial | The Codex allow-list is covered in test_codex_driver.py. The shared unit cases are covered by Phase 4 tests. |
| test_fixture_usage_parsing | Rewritten | test_codex_driver.py::test_recorded_stream_pins_text_session_and_telemetry and test_agy_driver.py::test_recorded_stream_pins_text_session_and_telemetry, which parse the recorded fixtures |
| test_behavioral_model_extraction_all_routes_and_propagation | Partial | Agy model extraction is covered by test_agy_driver.py::test_model_is_taken_from_last_event_that_carries_one. Codex model from rollout is covered by test_codex_rollout.py::test_rollout_supplies_the_model_name. Other Codex routes are not ported. |
| test_all_four_adapters_final_only_non_streaming_and_cache | Partial | Non-streaming mode is dropped because `on_event` is required. Cache cases are covered by test_codex_driver.py::test_cached_input_is_not_added_twice and test_agy_driver.py::test_final_totals_do_not_duplicate_prior_step_consumption. |
| test_codex_cached_input_not_added_twice | Ported | test_codex_driver.py::test_cached_input_is_not_added_twice |
| test_agy_final_totals_do_not_duplicate_prior_step_consumption | Ported | test_agy_driver.py::test_final_totals_do_not_duplicate_prior_step_consumption |
| test_no_emitter_execution_returns_normalized_telemetry | Dropped | `on_event` is required, so a run without a sink cannot occur. |
| test_parsing_failures_leave_text_and_outcome_unchanged | Partial | test_codex_driver.py::test_malformed_usage_fields_are_ignored_and_do_not_alter_the_outcome. The Agy variant is covered by test_agy_driver.py::test_non_utf8_output_does_not_truncate_later_lines. |
| test_missing_input_tokens_leaves_input_unavailable_and_partial | Ported | test_codex_driver.py::test_turn_completed_without_input_leaves_input_unavailable |
| test_malformed_codex_usage_does_not_alter_backend_outcome_or_subsequent_events | Ported | test_codex_driver.py::test_malformed_usage_fields_are_ignored_and_do_not_alter_the_outcome |
| test_backends_emit_derived_context_quality | Partial | Agy context provenance is covered by test_agy_driver.py::test_context_provenance_is_separate_from_capacity. Codex context is covered by the rollout tests. The Claude and Pi assertions belong to Phase 4. |
| test_codex_rollout_exact_session_enrichment | Ported | test_codex_rollout.py::test_codex_rollout_exact_session_enrichment |
| test_codex_rollout_refuses_mismatched_session_identity | Ported | test_codex_rollout.py::test_codex_rollout_refuses_mismatched_session_identity |
| test_codex_rollout_never_selects_unrelated_latest_session | Ported | test_codex_rollout.py::test_codex_rollout_never_selects_unrelated_latest_session |
| test_codex_rollout_handles_partial_and_malformed_lines | Ported | test_codex_rollout.py::test_codex_rollout_poll_keeps_partial_line_until_it_completes and test_codex_rollout_poll_skips_blank_and_non_object_lines |
| test_codex_rollout_handles_file_growth_and_incremental_reads | Partial | test_codex_rollout.py::test_codex_rollout_growth_invalidates_verified_context_until_next_snapshot. The offset-by-offset incremental read check is not ported. |
| test_codex_rollout_handles_file_truncation_and_rotation | Ported | test_codex_rollout.py::test_codex_rollout_truncation_requires_the_session_header_again and test_codex_rollout.py::test_codex_rollout_rotation_replaces_the_inode_and_rediscovers |
| test_codex_rollout_compaction_invalidates_stale_context | Ported | test_codex_rollout.py::test_codex_rollout_compaction_record_clears_context_and_marks_compacted and test_codex_rollout.py::test_codex_rollout_compacted_event_msg_clears_context |
| test_codex_rollout_concurrent_sessions_isolated | Ported | test_codex_rollout.py::test_codex_rollout_concurrent_sessions_are_isolated |
| test_codex_rollout_bounded_reads | Gap | Not ported. The bounded-read budget is covered only by the budget-exhaustion tests. |
| test_codex_token_count_nested_usage_enriches_latest_context | Partial | Covered by test_codex_rollout.py::test_rollout_token_count_wins_over_turn_consumption_snapshot. The nested-usage shape is not separately asserted. |
| test_codex_verified_rollout_context_wins_over_turn_consumption_snapshot | Ported | test_codex_rollout.py::test_rollout_token_count_wins_over_turn_consumption_snapshot |
| test_codex_rollout_session_switch_invalidates_context | Ported | test_codex_rollout.py::test_codex_rollout_session_switch_invalidates_context |
| test_codex_rollout_rejects_replacement_between_discovery_and_read | Gap | Not ported. The race between discovery and read is not tested. |
| test_codex_rollout_budget_exhaustion_invalidates_prefix_snapshot | Ported | test_codex_rollout.py::test_codex_rollout_budget_exhaustion_invalidates_once |
| test_codex_same_inode_truncation_requires_session_header_again | Ported | test_codex_rollout.py::test_codex_rollout_truncation_requires_the_session_header_again |
| test_codex_rollout_refuses_mismatched_identity | Partial | test_codex_rollout.py::test_codex_rollout_refuses_mismatched_session_identity and test_codex_rollout.py::test_codex_rollout_refuses_a_file_with_the_wrong_cwd |
| test_codex_rollout_missing_file_degrades_gracefully | Partial | test_codex_rollout.py::test_codex_rollout_poll_discards_state_when_file_disappears. The retry-count assertion is not ported. |
| test_codex_rollout_discovery_finds_nested_exact_file_and_records_identity | Ported | test_codex_rollout.py::test_codex_rollout_discovery_finds_nested_exact_file_and_records_identity |
| test_codex_rollout_discovery_skips_mismatched_file_in_sibling_directory | Ported | test_codex_rollout.py::test_codex_rollout_discovery_skips_mismatched_file_in_sibling_directory |
| test_codex_rollout_discovery_honors_depth_limit | Ported | test_codex_rollout.py::test_codex_rollout_discovery_honors_depth_limit and test_codex_rollout.py::test_codex_rollout_discovery_finds_a_file_at_the_depth_limit |
| test_codex_rollout_discovery_rejects_invalid_session_id_without_retry | Ported | test_codex_rollout.py::test_codex_rollout_discovery_rejects_invalid_session_id_without_retry |
| test_codex_rollout_discovery_stops_after_retry_budget | Ported | test_codex_rollout.py::test_codex_rollout_discovery_stops_after_retry_budget |
| test_codex_rollout_discovery_counts_missing_sessions_dir | Ported | test_codex_rollout.py::test_codex_rollout_discovery_counts_missing_sessions_dir |
| test_codex_rollout_discovery_refuses_symlinked_sessions_dir | Ported | test_codex_rollout.py::test_codex_rollout_discovery_refuses_symlinked_sessions_dir |
| test_codex_rollout_discovery_ignores_symlinked_rollout_file | Ported | test_codex_rollout.py::test_codex_rollout_discovery_ignores_symlinked_rollout_file |
| test_codex_rollout_discovery_rejects_unverifiable_files | Ported | test_codex_rollout.py::test_codex_rollout_discovery_rejects_unverifiable_files |
| test_codex_rollout_discovery_stops_at_entry_limit | Ported | test_codex_rollout.py::test_codex_rollout_discovery_stops_at_entry_limit |
| test_codex_rollout_poll_without_session_id_does_nothing | Ported | test_codex_rollout.py::test_codex_rollout_poll_without_session_id_does_nothing |
| test_codex_rollout_poll_counts_retry_while_file_is_missing | Gap | Not ported. The retry counter while the file is missing is not asserted. |
| test_codex_rollout_poll_first_read_verifies_file_and_records_context | Ported | test_codex_rollout.py::test_codex_rollout_poll_first_read_verifies_file_and_records_context |
| test_codex_rollout_poll_discards_stat_failure_after_discovery | Ported | test_codex_rollout.py::test_codex_rollout_stat_failure_after_discovery_is_not_fatal |
| test_codex_rollout_poll_invalidates_when_file_disappears | Ported | test_codex_rollout.py::test_codex_rollout_poll_discards_state_when_file_disappears |
| test_codex_rollout_poll_growth_invalidates_verified_context_until_next_snapshot | Ported | test_codex_rollout.py::test_codex_rollout_growth_invalidates_verified_context_until_next_snapshot |
| test_codex_rollout_poll_without_new_bytes_does_not_emit | Ported | test_codex_rollout.py::test_codex_rollout_poll_without_new_bytes_does_not_emit |
| test_codex_rollout_poll_keeps_partial_line_until_it_completes | Ported | test_codex_rollout.py::test_codex_rollout_poll_keeps_partial_line_until_it_completes |
| test_codex_rollout_poll_skips_blank_and_non_object_lines | Ported | test_codex_rollout.py::test_codex_rollout_poll_skips_blank_and_non_object_lines |
| test_codex_rollout_poll_defers_context_until_file_is_fully_read | Ported | test_codex_rollout.py::test_codex_rollout_poll_defers_context_until_file_is_fully_read |
| test_codex_rollout_poll_exhausted_budget_invalidates_once | Ported | test_codex_rollout.py::test_codex_rollout_budget_exhaustion_invalidates_once |
| test_codex_rollout_poll_truncation_with_foreign_header_resets_state | Partial | test_codex_rollout.py::test_codex_rollout_truncation_requires_the_session_header_again. The foreign-header variant is not ported. |
| test_codex_rollout_token_count_sets_pending_context | Gap | Not ported. The pending-context state is covered only indirectly by the end-to-end context test. |
| test_codex_rollout_session_meta_record_controls_verification | Partial | test_codex_rollout.py::test_codex_rollout_session_meta_mid_file_for_another_session_stops_verification |
| test_codex_rollout_records_are_ignored_while_file_is_unverified | Ported | test_codex_rollout.py::test_codex_rollout_records_are_ignored_while_file_is_unverified |
| test_codex_rollout_compaction_record_clears_context_and_marks_compacted | Ported | test_codex_rollout.py::test_codex_rollout_compaction_record_clears_context_and_marks_compacted |
| test_codex_rollout_model_records_update_model_name | Ported | test_codex_rollout.py::test_codex_rollout_model_records_update_model_name |
| test_codex_rollout_unrecognized_records_change_nothing | Ported | test_codex_rollout.py::test_codex_rollout_unrecognized_records_change_nothing |
| test_codex_rollout_lifecycle_cancellation | Gap | Not ported. Cancellation with an open rollout is not tested at the rollout level. |
| test_codex_resumed_session_without_baseline_marks_incomplete | Dropped | The `incomplete` marker is a Refract telemetry concept. The typed `Usage` has no completeness field. |
| test_codex_fresh_cumulative_reconciles_complete | Partial | Fresh-run cumulative totals are covered by test_codex_driver.py::test_final_usage_records_totals_for_fresh_runs. The completeness marker is dropped for the same reason as above. |
| test_codex_discovery_bounded_entries_and_depth | Ported | test_codex_rollout.py::test_codex_rollout_discovery_stops_at_entry_limit and test_codex_rollout.py::test_codex_rollout_discovery_honors_depth_limit |
| test_codex_discovery_missing_file_retry_cap | Ported | test_codex_rollout.py::test_codex_rollout_discovery_stops_after_retry_budget |
| test_codex_discovery_refuses_symlinks_and_hostile_paths | Ported | test_codex_rollout.py::test_codex_rollout_discovery_refuses_symlinked_sessions_dir and test_codex_rollout.py::test_codex_rollout_discovery_ignores_symlinked_rollout_file |
| test_codex_rollout_rotation_equal_size_revalidates_and_emits_invalidation | Partial | test_codex_rollout.py::test_codex_rollout_rotation_replaces_the_inode_and_rediscovers. The equal-size rotation case is not ported. |
| test_agy_capacity_remains_null_without_explicit_metadata | Ported | test_agy_driver.py::test_capacity_remains_null_without_explicit_metadata |
| test_agy_explicit_context_metadata_handled | Ported | test_agy_driver.py::test_explicit_context_metadata_is_recorded |
| test_agy_context_provenance_separated_from_capacity | Ported | test_agy_driver.py::test_context_provenance_is_separate_from_capacity |
| test_fixture_usage_produces_flat_tokens_matching_v2_totals | Rewritten | The flat v2 token dict is a Refract concept. test_agy_driver.py::test_recorded_stream_pins_text_session_and_telemetry asserts the typed `Usage` totals from the same fixture. |

## test_drivers.py (Codex and Agy argv cases)

| Refract case | Status | New test or reason |
|---|---|---|
| agy_plain | Rewritten | test_agy_driver.py::test_new_session_argv_uses_new_project_and_places_args_before_the_prompt. No permission flag is added. |
| agy_model | Ported | test_agy_driver.py::test_model_maps_to_the_model_flag_before_caller_args |
| agy_ignores_other_settings | Rewritten | test_agy_driver.py::test_reasoning_and_system_prompt_are_unsupported_and_spawn_nothing. Reasoning and system_prompt are now rejected. |
| codex_plain | Rewritten | test_codex_driver.py::test_no_approval_sandbox_or_bypass_flag_is_ever_added. The argv has no `--cd` and no `--yolo`. |
| codex_profile | Dropped | `backend_profile` is not a field of the new `Request`. |
| codex_model | Ported | test_codex_driver.py::test_portable_options_map_to_codex_flags_before_caller_args |
| codex_profile_model | Dropped | `backend_profile` is not a field of the new `Request`. |
| codex_profile_model_escaped | Dropped | Escaping was needed for the profile config value. `backend_profile` is removed. |
| codex_model_escaped_without_profile | Gap | Not ported. The model is passed as a separate argv element, so escaping is not needed. The argv value is not asserted for a quoted model. |
| codex_reasoning | Rewritten | test_codex_driver.py::test_reasoning_value_is_toml_quoted_in_the_config_flag. The value is TOML-quoted. |
| codex_all | Gap | Not ported as a combined case. The separate cases cover model, reasoning, and system_prompt rejection. |
| reject_codex_cd | Ported | test_codex_driver.py::test_working_directory_args_are_rejected_before_spawn (`--cd=/tmp`) |
| reject_codex_short_c | Ported | test_codex_driver.py::test_working_directory_args_are_rejected_before_spawn (`-C/tmp`) |
| reject_codex_cd_agy_ok | Ported | test_agy_driver.py::test_cd_is_not_owned_by_agy_and_passes_through_as_a_caller_arg |
| reject_dashdash | Ported | test_codex_driver.py::test_invalid_raw_args_are_rejected_before_spawn (`--`) |

## test_telemetry.py

No Codex or Agy test functions exist in this file. Nothing to port.

## Verified behaviors not in the Refract test list

- test_codex_driver.py::test_driver_registry_returns_the_codex_driver and test_agy_driver.py::test_driver_registry_returns_the_agy_driver check registry membership.
- test_codex_driver.py::test_working_directory_args_are_rejected_before_spawn and test_codex_driver.py::test_raw_args_cannot_override_a_driver_owned_transport_flag cover the transport-flag policy.
- test_codex_driver.py::test_system_prompt_is_unsupported_and_spawns_nothing covers the unsupported option path.
- test_agy_driver.py::test_no_permission_or_sandbox_flag_is_ever_added covers the argv combinations for Agy.
- test_codex_driver.py::test_no_approval_sandbox_or_bypass_flag_is_ever_added covers the argv combinations for Codex.
- test_codex_rollout.py::test_codex_home_from_request_env_wins_over_os_environ and test_codex_rollout.py::test_os_environ_codex_home_is_ignored_when_request_env_overrides_it cover the `CODEX_HOME` precedence.

## Open gaps

The Gap rows above are not yet ported. The main gaps are the rollout race, bounded-read, pending-context, and cancellation tests, plus the combined Codex argv case.
