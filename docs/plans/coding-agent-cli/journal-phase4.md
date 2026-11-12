# Phase 4 journal: Claude and Pi drivers

Source: Refract commit 99d155bd. The read-only snapshot is at /tmp/refsrc/server/tests.

## Status

- Ported: the test keeps its assertion, adapted to the typed-event API.
- Rewritten: the test keeps its intent, but the expected value changes under DESIGN.md or the PLAN Phase 4 invariants.
- Partial: only part of the Refract test has a Phase 4 counterpart. The rest is noted.
- Dropped: the test covers a Refract concept that the new package does not have, or it belongs to a later phase.

## Shared helper decisions

- `tests/fixtures/fake_cli/fake_cli.py`, `tests/fixtures/usage/claude.jsonl`, and `tests/fixtures/usage/pi.jsonl` are byte-identical to Refract.
- `tests/cli_stub.py`: `TargetConfig`, `BackendParams`, `BackendResult`, and `DriverResult` are removed. `execute_sync` and `run_target` are replaced by `run_driver`, which calls `Driver.run` directly. `stub_cli`, `fake_run_process`, and `shell_fake_as_process` are kept. `log_text` and `last_message` stay in `stub_cli` for the Phase 5 Codex port.
- `_drivers/_common.py` gains `CliDriver.portable_argv`. The Claude and Pi drivers use it to build portable flags from the `portable_flags` table.

## test_claude_backend.py

| Refract test | Status | New test or reason |
|---|---|---|
| test_recorded_stream_pins_text_session_model_and_tokens | Ported | test_claude_driver.py::test_recorded_stream_pins_text_session_model_and_usage |
| test_resume_flag_and_prompt_on_stdin_are_pinned | Ported | test_claude_driver.py::test_resume_argv_and_prompt_on_stdin_are_pinned |
| test_streaming_argv_uses_stream_json_when_on_event_is_set | Dropped | The new API always streams. `on_event` is required, so there is no non-streaming mode. |
| test_command_failure_takes_precedence_over_result_error | Ported | test_claude_driver.py::test_command_failure_takes_precedence_over_a_plain_result_error |
| test_result_error_without_command_failure_is_fatal_backend | Rewritten | test_result_error_without_status_is_retryable_execution_error. DESIGN.md maps execution errors to retryable. `fatal_backend` no longer exists. |
| test_fatal_token_in_diagnostic_line_is_fatal_backend | Rewritten | test_fatal_token_in_diagnostic_line_is_request_fatal_invalid_model. The outcome now comes from the keyword code. |
| test_no_output_is_no_agent_messages | Rewritten | test_no_output_is_retryable_no_output. DESIGN.md names this outcome `no_output`. |
| test_result_without_session_id_is_ok_with_warning | Ported | test_claude_driver.py::test_result_without_session_id_is_success_with_warning |
| test_timeout_maps_to_timeout_with_seconds | Rewritten | test_timeout_kills_the_child_and_maps_retryable_timeout. The outcome is retryable with code `timeout`. The seconds text is not part of the `Diagnostic` contract. |
| test_counters_report_any_known_value | Dropped | `TokenCounters.has_any` is removed. `usage()` equality covers the same check. |
| test_async_cancel_event_kills_child_and_maps_cancelled | Ported | test_claude_driver.py::test_cancel_kills_the_child_and_maps_cancelled |
| test_async_timeout_kills_child_and_maps_timeout | Ported | test_claude_driver.py::test_timeout_kills_the_child_and_maps_retryable_timeout |
| test_driver_threading_cancel_reaches_claude_child | Dropped | This tests the Refract threaded wrapper. Cross-thread cancel is covered by tests/test_api_contract.py::test_cross_thread_cancel. |
| test_async_on_event_failure_fails_attempt | Ported | test_claude_driver.py::test_failing_sink_kills_the_child_and_surfaces_execution_error |
| test_parser_keeps_bounded_diagnostics_and_the_first_fatal_line | Ported | test_claude_driver.py::test_parser_keeps_bounded_diagnostics_and_the_first_fatal_line |
| test_result_capacity_comes_from_the_selected_model_usage_entry | Ported | test_claude_driver.py::test_result_capacity_comes_from_the_selected_model_usage_entry (parametrized) |
| test_result_usage_records_totals_then_capacity_with_one_event_each | Ported | test_claude_driver.py::test_result_usage_records_totals_then_capacity_with_one_event_each |
| test_result_without_observed_usage_records_no_totals | Ported | test_claude_driver.py::test_result_without_observed_usage_records_no_totals (parametrized) |
| test_result_usage_without_a_sink_still_records_totals_and_capacity | Dropped | `on_event` is always present in the new API, so a run without a sink cannot occur. The totals and capacity checks are in the test above. |
| test_result_with_a_malformed_count_is_observed_and_emits_empty_totals | Ported | test_claude_driver.py::test_result_with_a_malformed_count_is_observed_and_emits_empty_totals |

## test_pi_backend.py

| Refract test | Status | New test or reason |
|---|---|---|
| test_recorded_stream_pins_text_session_and_model | Ported | test_pi_driver.py::test_recorded_stream_pins_text_session_model_and_usage |
| test_recorded_stream_pins_telemetry | Ported | Merged into test_pi_driver.py::test_recorded_stream_pins_text_session_model_and_usage |
| test_resume_flag_and_json_mode_are_pinned | Rewritten | test_pi_driver.py::test_resume_argv_and_prompt_on_stdin_are_pinned. Refract's expected argv included `--approve`. The new argv is `pi --mode json --session <id>`. |
| test_session_event_supplies_session_id | Ported | test_pi_driver.py::test_session_event_supplies_session_id |
| test_structured_error_with_partial_text_is_execution_error | Rewritten | test_structured_error_with_partial_text_is_retryable_service_error_on_rate_limit and test_structured_error_with_partial_text_cannot_hide_the_failure. Keyword classification now selects `service_error`. |
| test_command_failure_with_text_is_execution_error | Ported | test_pi_driver.py::test_command_failure_with_text_is_retryable_execution_error |
| test_fatal_token_in_diagnostic_line_is_fatal_backend | Rewritten | test_fatal_token_in_diagnostic_line_is_agent_fatal_auth |
| test_context_overflow_signature_is_context_overflow | Rewritten | test_context_overflow_signature_is_request_fatal_context_overflow. DESIGN.md classifies context overflow as request-fatal. |
| test_no_output_is_no_agent_messages | Rewritten | test_no_output_is_retryable_no_output |
| test_timeout_without_reply_is_no_agent_messages_with_timeout_text | Rewritten | test_timeout_without_reply_is_retryable_timeout. PLAN Phase 4 requires a Pi timeout without text to map to `timeout`. |
| test_cancel_before_start_is_cancelled | Ported | test_pi_driver.py::test_cancel_before_start_is_cancelled_and_spawns_nothing |
| test_prompt_is_sent_on_stdin_not_argv | Ported | Covered by test_pi_driver.py::test_resume_argv_and_prompt_on_stdin_are_pinned, which also checks that the prompt is absent from argv |
| test_event_model_picks_the_assistant_model | Ported | test_pi_driver.py::test_event_model_picks_the_assistant_model (parametrized, extended with message-versus-messages cases) |
| test_assistant_usage_records_the_response_and_context_once | Ported | test_pi_driver.py::test_assistant_usage_records_the_response_and_context_once |
| test_repeated_response_id_is_ignored | Ported | test_pi_driver.py::test_repeated_response_id_is_ignored |
| test_id_is_used_when_response_id_is_missing_and_distinct_ids_sum | Ported | test_pi_driver.py::test_id_is_used_when_response_id_is_missing_and_distinct_ids_sum |
| test_message_without_an_id_uses_turn_one_and_a_second_one_in_the_turn_is_ignored | Ported | test_pi_driver.py::test_message_without_an_id_uses_turn_one_and_a_second_one_in_the_turn_is_ignored |
| test_each_turn_start_allows_one_message_without_an_id | Ported | test_pi_driver.py::test_each_turn_start_allows_one_message_without_an_id |
| test_turn_id_already_recorded_by_a_response_id_is_ignored | Ported | test_pi_driver.py::test_turn_id_already_recorded_by_a_response_id_is_ignored |
| test_message_without_usable_usage_is_marked_recorded_but_emits_nothing | Ported | test_pi_driver.py::test_message_without_usable_usage_is_marked_recorded_but_emits_nothing (parametrized) |
| test_non_assistant_message_is_not_recorded | Ported | test_pi_driver.py::test_non_assistant_message_is_not_recorded (parametrized) |
| test_output_only_usage_records_no_context_used | Ported | test_pi_driver.py::test_output_only_usage_records_no_context_used |
| test_turn_end_events_record_usage_like_message_end | Ported | test_pi_driver.py::test_turn_end_events_record_usage_like_message_end |

## test_streaming_backends.py (Claude and Pi parts only)

Codex, Agy, and rollout tests are out of scope for Phase 4 and belong to Phase 5.

| Refract test | Status | New test or reason |
|---|---|---|
| test_claude_streaming_normalization | Rewritten | Split into test_claude_driver.py::test_text_deltas_get_a_distinct_entity_per_message and test_tool_started_and_completed_pair_by_tool_use_id. Entity IDs are per run, and completion comes from tool results. |
| test_claude_streaming_bash_activity_command | Partial | Only the classification unit is ported, as test_claude_driver.py::test_command_activity_allow_list_is_exact_and_case_sensitive. The stream-level lowercase `bash` fixture is not ported. |
| test_claude_streaming_nested_and_missing_payloads | Rewritten | test_claude_driver.py::test_nested_tool_input_is_preserved_and_missing_or_null_input_is_none. `ToolStarted.input` is `None` for both missing and null input, so the two cases cannot be told apart. |
| test_claude_streaming_content_block_stop_output_ignored | Rewritten | test_claude_driver.py::test_content_block_stop_does_not_complete_a_tool. PLAN Phase 4 requires completion from tool-result evidence. |
| test_pi_streaming_normalization | Rewritten | test_pi_driver.py::test_text_and_tools_normalize_and_thinking_deltas_are_excluded. `ToolCompleted` has no output field, so the output assertion is dropped. |
| test_pi_streaming_nested_and_missing_payloads | Rewritten | test_pi_driver.py::test_nested_camel_and_snake_ids_pair_tool_start_and_completion |
| test_exact_command_classification_and_unsafe_substring_rejection | Partial | The Claude part is test_claude_driver.py::test_command_activity_allow_list_is_exact_and_case_sensitive. The Codex part is Phase 5. |
| test_negative_reasoning_and_diagnostics_excluded | Rewritten | Claude: test_claude_driver.py::test_thinking_deltas_and_non_text_lines_are_never_emitted. Pi: test_pi_driver.py::test_text_and_tools_normalize_and_thinking_deltas_are_excluded. The Codex and Agy parts are Phase 5. |
| test_classify_tool_activity_unit | Partial | The Claude assertions are ported. The Codex assertions are Phase 5. |
| test_fixture_usage_parsing | Partial | The Claude and Pi fixture totals are ported in the recorded-stream tests. The Codex and Agy totals are Phase 5. |
| test_malformed_usage_never_raises | Partial | Claude and Pi malformed usage is covered by the malformed-count and malformed-cache tests and by test_pi_driver.py::test_message_without_usable_usage_is_marked_recorded_but_emits_nothing. |
| test_backend_result_tokens_default_to_unknown | Ported | test_usage.py::test_result_usage_defaults_to_all_unknown |
| test_result_model_identity_replaces_usage | Dropped | This pins the Refract `BackendResult` and `DriverResult` shapes, which the new package does not have. The model field is checked in the recorded-stream tests. |
| test_behavioral_model_extraction_all_routes_and_propagation | Partial | Pi routes are covered by test_event_model_picks_the_assistant_model. Claude routes are covered by the recorded-stream and capacity tests. The Codex routes and the Refract `TargetExecutor` routes are dropped. |
| test_backend_cancellation_and_failure_precedence_preserved | Partial | Claude and Pi precedence are covered by the command-failure and structured-error tests. |
| test_claude_capacity_not_selected_arbitrarily | Ported | test_claude_driver.py::test_result_capacity_comes_from_the_selected_model_usage_entry |
| test_pi_lifecycle_duplicates_do_not_double_count | Ported | test_pi_driver.py::test_repeated_message_end_and_turn_end_do_not_double_count |
| test_multi_response_pi_telemetry_exceeds_final_message_alone | Ported | test_pi_driver.py::test_recorded_fixture_totals_exceed_the_final_message_alone and test_id_is_used_when_response_id_is_missing_and_distinct_ids_sum |
| test_claude_cache_reads_and_writes_enter_normalized_input_once | Ported | Cache totals are pinned in test_claude_driver.py::test_recorded_stream_pins_text_session_model_and_usage. The telemetry-event count check is replaced by `UsageUpdated` assertions. |
| test_no_emitter_execution_returns_normalized_telemetry | Dropped | `on_event` is always present in the new API, so a run without an emitter cannot occur. |
| test_parsing_failures_leave_text_and_outcome_unchanged | Partial | The Claude and Pi parts are covered by the malformed-usage, malformed-cache, and bounded-diagnostics tests. The Codex and Agy parts are Phase 5. |
| test_pi_lifecycle_deduplication_without_provider_ids | Ported | test_pi_driver.py::test_lifecycle_deduplication_without_provider_ids |
| test_missing_input_tokens_leaves_input_unavailable_and_partial | Partial | The Claude and Pi output-only cases are covered by test_pi_driver.py::test_output_only_usage_records_no_context_used and the Claude malformed-cache tests. The Codex part is Phase 5. |
| test_backends_emit_derived_context_quality | Dropped | `Usage` has no `quality` or `source` provenance fields. The derived context value is checked in test_pi_driver.py::test_total_tokens_is_never_context_occupancy_without_retained_input. |
| test_pi_no_id_lifecycle_scoped_to_actual_turns_and_dedups_repeated_message_end | Ported | test_pi_driver.py::test_repeated_message_end_within_one_turn_is_counted_once and test_repeated_no_id_turn_end_does_not_open_an_implicit_turn |
| test_claude_and_pi_malformed_cache_preserves_unknown_normalized_input | Partial | The Claude part is ported as test_claude_driver.py::test_malformed_cache_read_leaves_input_unknown_and_keeps_output and test_malformed_cumulative_cache_write_leaves_input_unknown. The Pi malformed-cache cases are not ported. |
| test_pi_total_tokens_never_labeled_reported_context_and_derivation_provenance | Rewritten | test_pi_driver.py::test_total_tokens_is_never_context_occupancy_without_retained_input. The provenance labels are dropped with `Usage` having no provenance fields. |
| test_pi_repeated_no_id_turn_end_does_not_open_implicit_turn | Ported | test_pi_driver.py::test_repeated_no_id_turn_end_does_not_open_an_implicit_turn |
| test_pi_capacity_remains_null_offline | Ported | test_pi_driver.py::test_capacity_stays_unknown_when_the_stream_reports_no_window |
| test_fixture_usage_produces_flat_tokens_matching_v2_totals | Ported | test_pi_driver.py::test_recorded_fixture_totals_exceed_the_final_message_alone and test_claude_driver.py::test_recorded_stream_pins_text_session_model_and_usage |
| test_backend_without_usage_reports_all_tokens_unknown | Ported | test_usage.py::test_result_usage_defaults_to_all_unknown |

## test_telemetry.py

| Refract test | Status | New test or reason |
|---|---|---|
| test_new_counters_are_all_unknown | Ported | test_usage.py::test_new_counters_are_all_unknown |
| test_measurement_model_is_gone | Dropped | Checks that Refract-only names are absent. The new package never had them. |
| test_responses_sum_and_a_repeated_response_id_replaces_its_earlier_values | Ported | test_usage.py::test_responses_sum_and_a_repeated_response_id_replaces_its_earlier_values |
| test_reported_zero_is_kept_and_unreported_field_stays_null | Ported | test_usage.py::test_reported_zero_is_kept_and_unreported_field_stays_null |
| test_backend_totals_override_summed_responses_per_field | Ported | test_usage.py::test_backend_totals_override_summed_responses_per_field |
| test_context_keeps_latest_value_and_none_leaves_it_unchanged | Ported | test_usage.py::test_context_keeps_latest_value_and_none_leaves_it_unchanged |
| test_invalid_values_are_ignored | Ported | test_usage.py::test_invalid_values_are_ignored |
| test_has_any_reports_whether_any_counter_is_known | Dropped | `TokenCounters.has_any` is not part of the new API. `usage()` equality covers it. |

## test_backend_common.py

| Refract test | Status | New test or reason |
|---|---|---|
| test_read_usage_normalizes_input_and_keeps_malformed_components_visible | Ported | test_usage.py::test_read_usage_normalizes_input_and_keeps_malformed_components_visible |
| test_as_int_rejects_bool_negative_and_non_int | Ported | test_usage.py::test_as_int_rejects_bool_negative_and_non_int |

## test_drivers.py

The argv characterization cases map onto the Phase 4 argv policy. Codex, Agy, isolated, read-only, and profile cases are not Phase 4 behavior.

| Refract case | Status | New test or reason |
|---|---|---|
| test_normalize_preserves_backend_tokens | Dropped | Tests `_normalize` for the Refract job-layer result shape. |
| test_driver_result_tokens_default_to_all_unknown | Ported | test_usage.py::test_result_usage_defaults_to_all_unknown |
| claude_plain | Rewritten | The argv is pinned by the no-permission-flag matrix and the resume and portable argv tests. |
| claude_isolated | Dropped | Isolated mode is out of scope for this package. |
| claude_system_prompt, claude_model, claude_reasoning | Ported | test_claude_driver.py::test_portable_options_map_to_claude_flags_after_transport_flags |
| claude_read_only | Dropped | Read-only mode is out of scope. Permission flags are the caller's choice through `args`. |
| claude_all | Rewritten | The isolated, read-only, and profile settings are removed. The portable and raw-arg parts are covered by the portable-options test. |
| unknown_backend_passthrough | Dropped | This case concerns a non-built-in backend name. |
| args_not_mutated_order | Rewritten | test_claude_driver.py::test_raw_args_cannot_conflict_with_a_portable_option. A raw `--model` next to a portable `model` is now rejected, not appended. |
| reject_dashdash | Ported | test_pi_driver.py::test_invalid_raw_args_are_rejected_before_spawn (case `dashdash`). Claude is not separately parametrized. |
| reject_nul | Ported | test_pi_driver.py::test_invalid_raw_args_are_rejected_before_spawn (case `nul`) |
| reject_codex_cd, reject_codex_short_c, reject_codex_cd_agy_ok | Dropped | Codex and Agy behavior, Phase 5. |
| reject_pi_isolated_skill | Dropped | Isolated mode is out of scope. |
| pi_plain | Rewritten | The expected argv no longer contains `--approve`. |
| pi_isolated, pi_isolated_all | Dropped | Isolated mode is out of scope. |
| pi_user_args_then_approve | Rewritten | test_pi_driver.py::test_extension_style_args_pass_through_without_an_approval_flag |
| pi_system_prompt, pi_model, pi_reasoning | Rewritten | Covered by test_pi_driver.py::test_portable_options_map_to_pi_flags_after_transport_flags. The expected argv no longer contains `--approve`. |
| pi_read_only | Dropped | Read-only mode is out of scope. |
| pi_all | Rewritten | The read-only and profile settings are removed. `--approve` is removed. |
| pi_not_isolated_skill_allowed | Rewritten | test_pi_driver.py::test_extension_style_args_pass_through_without_an_approval_flag |
| test_target_args_does_not_leak_env_into_argv | Ported | test_pi_driver.py::test_env_values_never_reach_argv. The Claude form is not separately ported. |

## Tests added in Phase 4 with no Refract source

- test_pi_driver.py::test_no_approval_or_permission_flag_is_ever_added and test_claude_driver.py::test_no_permission_or_approval_flag_is_ever_added run the full option matrix, 32 combinations per driver. They assert that no permission or approval flag appears.
- test_registry.py::test_builtin_drivers_include_claude_and_pi.
- test_claude_driver.py::test_command_activity_allow_list_is_exact_and_case_sensitive is the Claude part of the classification test. It is listed above.

## Open items

- The fixtures are recorded streams. No test in this phase ran a live `claude` or `pi` process.
- The TDD RED step was not observed. The driver modules were written before the Phase 4 test modules were run, so no failing run was recorded first.
- `claude -p --output-format stream-json` may require `--verbose` in the current CLI. This phase does not add `--verbose`, as the PLAN requires. The installed `claude --help` text does not state the requirement, so the flag set is unverified against a live run.
- Pi without `--approve` may block or prompt on tool use in unattended runs. Refract added `--approve` for non-isolated runs. This phase removes it. Unattended behavior is unverified.
- `_VERIFIED_COMMANDS` lists lowercase `bash` only. Claude's shell tool may be named `Bash`, which would classify as `tool_call`. Refract pins this behavior in its tests, so the port keeps it. Human review is needed.
