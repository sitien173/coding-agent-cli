<!-- refract-shared-version: 3.0.5 -->

# Phase 2 Decision Notes

<!-- Created by the worker per worker-contract.md. Append one `## Task <M>` block per task. -->

## Task 1

### Decisions made
- Kept `build_result`, `process_facts`, and `deadline_for` in `src/coding_agent_cli/_drivers/_common.py` for now because `tests/toy_driver.py` still uses them until Task 4 adapts it to the v2 classification types. Removed all v1 driver-only helpers including `_VERIFIED_COMMANDS`, `classify_tool_activity`, `tool_started`, `tool_status`, `parse_json_object`, `as_int`, `read_and_unlink`, `parse_token_component`, `TokenUsage`, `NO_USAGE`, `read_usage`, `EventBuffer`, `Diagnostics`, `effective_env`, `resolve_executable`, `request_failure`, and `CliDriver`.
- Deleted `tests/cli_stub.py` since only the deleted v1 driver test files imported it.
- Updated `tests/toy_driver.py` to pass `Usage()` instead of `TokenCounters().usage()`.

### Spec deviations
- none

### Tradeoffs accepted
- none

### Assumptions
- none

### Follow-ups for human
- none

### Test evidence
- RED -> GREEN:
  - RED: `tests/test_registry.py::test_builtin_drivers_are_claude_pi_codex_and_agy` -> `DriverRegistry().names()` expected `{"claude", "pi", "codex", "agy"}`, which fails once `_builtin_drivers()` is emptied; updated to `test_builtin_drivers_are_empty`.
  - GREEN: `export VIRTUAL_ENV=; uv run pytest tests/test_registry.py` -> `6 passed in 0.04s`
- Root cause (bugfix only): none

## Task 2

### Decisions made
- Defined `type JsonValue = None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]` in `events.py` and re-exported `ErrorCode`, `JsonValue`, `FileChanged`, `Notice`, `RawEvent`, and `ReasoningDelta` from `__init__.py`.
- Defaulted `Result.duration_s` to `0.0` so `Result(outcome=...)` remains ergonomic when duration is not explicitly passed.
- Extracted `_validate_timeout(value, name)` in `types.py` so `timeout_s` and `idle_timeout_s` share identical validation.
- Removed `EventBuffer` from `_drivers/_common.py` in Task 1; nothing outside the deleted v1 drivers used it.

### Spec deviations
- none

### Tradeoffs accepted
- none

### Assumptions
- `FileChanged.path` is `str` (matching JSON-serialized event payloads and other event string fields).

### Follow-ups for human
- none

### Test evidence
- RED -> GREEN:
  - RED: `export VIRTUAL_ENV=; uv run pytest tests/test_types.py tests/test_events.py` -> `18 failed, 23 passed` (`test_outcome_values` missing `limit_reached`, `test_error_code_values` `ErrorCode` missing, `idle_timeout_s` unexpected kwarg, `Result`/`Capabilities` field mismatches, `SCHEMA_VERSION == 1 != 2`, missing `ReasoningDelta` etc.)
  - GREEN: `export VIRTUAL_ENV=; uv run pytest tests/test_types.py tests/test_events.py` -> `41 passed in 0.07s`
- Root cause (bugfix only): none

## Task 3

### Decisions made
- Used `classify(run: RunFacts, facts: Facts, *, text: str) -> Result` so text accumulation stays outside `classify` (in the base/dispatcher); `Facts.final_text` overrides `text` when not `None`.
- `RunFacts` stores `stop_cause: Literal["exited", "cancelled", "timeout", "idle_timeout"]`, `returncode`, `stderr_tail`, `request_error`, `spawn_error`, `stdin_failed: str | None`, `parser_error: str | None`, `cleanup_incomplete`, `line_too_long`, and `duration_s`.
- `keyword_code` and `http_status_code` now return `ErrorCode | None` instead of `str`.
- When `Facts.error` is present, keyword matching on `Facts.error_text` and `RunFacts.stderr_tail` is skipped; if `Facts.error` is a structured failure not handled by rows 1–9, it falls through to row 10 (`Outcome.RETRYABLE`).

### Spec deviations
- none

### Tradeoffs accepted
- none

### Assumptions
- `RunFacts.stdin_failed` is `str | None` (`None` means no stdin failure; a string carries the failure message, defaulting to `"failed to write prompt to stdin"` if empty).

### Follow-ups for human
- none

### Test evidence
- RED -> GREEN:
  - RED: `export VIRTUAL_ENV=; uv run pytest tests/test_classify.py` (against inert `classify` stub) -> `17 failed, 7 passed` (all row precedence assertions, `test_max_turns_limit_beats_timeout`, `test_warnings_include_no_session_id_line_too_long_cleanup_incomplete`, `test_structured_error_skips_keyword_matching`, `test_http_status_code`)
  - GREEN: `export VIRTUAL_ENV=; uv run pytest tests/test_classify.py` -> `24 passed in 0.09s`
- Root cause (bugfix only): none

## Task 4

### Decisions made
- Updated `src/coding_agent_cli/_args.py` to construct `Diagnostic` with `ErrorCode.INVALID_ARGS` and `ErrorCode.UNSUPPORTED_OPTION` instead of bare string literals so `pyright` passes with 0 errors after `Diagnostic.code` became `ErrorCode`.
- Replaced `build_result` and `process_facts` in `src/coding_agent_cli/_drivers/_common.py` with `run_facts(process, cancel) -> RunFacts`, and updated `tests/toy_driver.py` to call `classify(run_facts(process, cancel), Facts(session_id="toy-session"), text="\n".join(text))`.
- Kept `api.py` signatures unchanged for Phase 4; `api.py` needed no edits to stay green on the new types.

### Spec deviations
- Edited `src/coding_agent_cli/_args.py` (not listed in `prompt.md`'s Modify list, though `tests/test_args.py` was) because `Diagnostic.code: ErrorCode` caused 6 `pyright` errors on string literals in `_args.py`.

### Tradeoffs accepted
- none

### Assumptions
- none

### Follow-ups for human
- none

### Test evidence
- RED -> GREEN:
  - RED: `export VIRTUAL_ENV=; uv run pytest -q` -> `11 failed, 112 passed, 4 deselected` (`TypeError: Facts.__init__() got an unexpected keyword argument 'cancelled'` in `_common.py:28` called from `toy_driver.py`); `export VIRTUAL_ENV=; uv run pyright` -> `15 errors` across `_args.py`, `_drivers/_common.py`, and `tests/toy_driver.py`
  - GREEN: `export VIRTUAL_ENV=; uv run ruff format --check && uv run ruff check && uv run pyright && uv run pytest` -> `62 files already formatted`, `All checks passed!`, `0 errors, 0 warnings, 0 informations`, `123 passed, 4 deselected in 12.55s`
- Root cause (bugfix only): none


## FIX: Gate 3 Review Findings (1-3)
- Goal: Fix all 3 Gate 3 findings in `src/coding_agent_cli/_drivers/_common.py`, `src/coding_agent_cli/_classify.py`, `tests/toy_driver.py`, and `tests/test_classify.py`.
- Decisions:
  1. Latched stop cause in `run_facts`: Removed `cancel: asyncio.Event` from `run_facts(process: ProcessOutcome)` so `run_facts` uses only `process.error_class` (`"cancelled"`, `"timeout"`, else `"exited"`) and never re-reads the live cancel event. Updated `tests/toy_driver.py` to call `run_facts(process)`.
  2. `invalid_prompt` mapping in `run_facts`: Mapped `process.error_class == "invalid_prompt"` to `RunFacts.request_error = Diagnostic(code=ErrorCode.INVALID_PROMPT, message=process.error)`. Verified all other `ProcessOutcome.error_class` values (`cancelled`, `timeout`, `missing_cli`, `spawn_failed`, `stdin_failed`, `execution_error`, `""`).
  3. Unstructured HTTP 5xx in `classify`: Added `_HTTP_STATUS_RE` and `_http_code_from_text` in `_classify.py`, called by `keyword_code` and reusing `http_status_code(...)` so `"HTTP 504"`, `"HTTP error 504: Gateway Timeout"`, `"status 503"`, and `'"status": 502'` classify as `ErrorCode.SERVICE_ERROR` (row 9) over nonzero exit (`execution_error`), while 4xx statuses (other than 401/403/429) do not match.
- RED: `export VIRTUAL_ENV=; uv run pytest tests/test_classify.py` -> 6 failed, 27 passed (`test_run_facts_ignores_cancel_event_after_clean_exit`, `test_run_facts_maps_invalid_prompt_to_request_error`, and the 4 `test_unstructured_http_5xx_classifies_as_service_error_over_nonzero_exit` cases).
- GREEN: `export VIRTUAL_ENV=; uv run pytest tests/test_classify.py` -> 33 passed.
- Checks: `ruff format --check` (67 files already formatted), `ruff check` (All checks passed!), `pyright` (0 errors, 0 warnings, 0 informations), `pytest` (pending task-157).
