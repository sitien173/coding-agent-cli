# Phase 8 Decision Notes

## Task 1

### Decisions made
- Used argv delivery (`--print <prompt>`) with `PROMPT_LIMIT_BYTES = 100 * 1024` because `tests/fixtures/recorded/FINDINGS.md` marks `--input-format stream-json` as not settled.
- Supported portable options `model` (`--model`) and `reasoning` (`--effort`, per `agy --help` and `reasoning.meta.json`); rejected `system_prompt` as `unsupported_option`.
- Placed the session log file at `tmp / "agy.log"` and returned it via `Launch.read_back`.

### Spec deviations
- none

### Tradeoffs accepted
- Kept `WINDOWS_COMMAND_LINE_LIMIT_CHARS = 32767` check on Windows via `subprocess.list2cmdline(argv)` to surface `invalid_prompt` rather than a Windows spawn error.

### Assumptions
- `agy --effort` accepts the values passed in `Request.reasoning` without driver-side translation.

### Follow-ups for human
- none

### Test evidence
- RED -> GREEN:
  - RED: `export VIRTUAL_ENV=; timeout 300 uv run pytest tests/test_agy_driver.py` -> `6 failed, 1 passed` (`AssertionError: assert 0 == 1`, `Failed: DID NOT RAISE RequestError`, `AssertionError: assert <Outcome.RETRYABLE: 'retryable'> == <Outcome.SUCCESS: 'success'>`)
  - GREEN: `export VIRTUAL_ENV=; timeout 300 uv run pytest tests/test_agy_driver.py` -> `7 passed in 0.07s`
- Root cause (bugfix only): none

## Task 2

### Decisions made
- Correlated `ToolStarted` and `ToolCompleted` by `step_index` in a dict (`entity_id = f"step-{step_index}"`), replacing v1's single `active_tool_id`.
- Mapped `run_command` to `activity="command"` and all other tools to `activity="tool_call"`.
- Detected tool failure from `is_error`, `error`, or `status` (`ERROR`/`FAILED`) on `step_update` or `tool_info`.
- Kept `num_turns=None` per DESIGN.md; mapped `result.finished_reason` to `Facts.stop_reason` and `result.status != "SUCCESS"` to `Facts.error` (preserving keyword classification via `keyword_code` so `invalid_model` maps to `ErrorCode.INVALID_MODEL` instead of `EXECUTION_ERROR`).
- Accumulated `step_update` usage per step and replaced totals with authoritative `result.usage` when present; included `cache_read_tokens` and `cache_write_tokens` in `Usage.input`.

### Spec deviations
- none

### Tradeoffs accepted
- none

### Assumptions
- `tool_info.error`, `update.error`, `is_error`, or `status in ("ERROR", "FAILED")` cover Agy's tool-failure reporting; `tests/fixtures/recorded/agy/` has no failed-tool fixture.

### Follow-ups for human
- none

### Test evidence
- RED -> GREEN:
  - RED: `export VIRTUAL_ENV=; timeout 300 uv run pytest tests/test_agy_driver.py` -> `7 failed, 7 passed` (`AssertionError` on `ToolStarted` count, `ToolCompleted` count, `SessionIdentified`, and `UsageUpdated`)
  - GREEN: `export VIRTUAL_ENV=; timeout 300 uv run pytest tests/test_agy_driver.py` -> `14 passed in 0.07s`
- Root cause (bugfix only): none

## Task 3

### Decisions made
- Parametrized replay and `replay_truncated` over all 8 `*.meta.json` scenarios under `tests/fixtures/recorded/agy/1.5.9/`.
- Verified `ToolStarted` / `ToolCompleted` pairing and `MessageStarted` / `MessageCompleted` pairing on all success scenarios, plus non-empty `Result.text` after truncation at every `TextDelta` boundary.

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
  - Note: Task 3 replay tests passed immediately against the Task 2 parser implementation (`30 passed in 0.14s`).
  - GREEN: `export VIRTUAL_ENV=; timeout 300 uv run pytest tests/test_agy_driver.py` -> `30 passed in 0.14s`
- Root cause (bugfix only): none

## Fix cycle 2

### Decisions made
- `CliDriver.run()` tracks unmatched `MessageStarted` events and emits one sequenced `MessageCompleted` per open message after transport/parser completion, before `RunCompleted`; a prior completion removes its matching entity ID from the open set.
- Agy plain-text fallback starts on the first stdout line even when blank, preserving leading newlines. Mixed structured and plain stdout remains unsupported and is documented in a one-line code comment.
- Agy emits tool status `completed` for a `DONE` update without unsupported status fields. If an unevidenced `error` or `status` field appears, it emits `unknown`, never `error`.
- Agy session IDs come only from the read-back log or the requested resume ID. Stderr and structured stdout conversation IDs are ignored.
- The overlapping-tool completion updates omit tool names; assertions verify names are recovered from each step index.

### Spec deviations
- none

### Tradeoffs accepted
- The base flushes every unmatched start in start order. Entity IDs, including `None`, are preserved on generated completion events.
- Presence of unsupported `error` or `status` fields makes tool status unknown; these fields are not interpreted as failure evidence.

### Assumptions
- Agy's `DONE` state is sufficient evidence for a completed tool only when the update contains no unevidenced error/status marker.

### Follow-ups for human
- none

### Test evidence
- RED -> GREEN:
  - RED: `export VIRTUAL_ENV=; timeout 300 uv run pytest -q tests/test_driver_base.py::test_open_message_is_completed_at_transport_end` -> `1 failed, 1 passed`; the open message had no completion.
  - GREEN: same command -> `2 passed`.
  - RED: `export VIRTUAL_ENV=; timeout 300 uv run pytest -q tests/test_agy_driver.py::test_plain_text_full_run_preserves_leading_blank_and_completes_message` -> `1 failed`; `Result.text` was `plain answer`, not `\nplain answer`.
  - RED: `export VIRTUAL_ENV=; timeout 300 uv run pytest -q tests/test_agy_driver.py::test_parser_unevidenced_tool_error_field_yields_unknown_status` -> `1 failed`; status was `error`, not `unknown`.
  - RED: `export VIRTUAL_ENV=; timeout 300 uv run pytest -q tests/test_agy_driver.py::test_parser_plain_text_fallback_through_feed_text` -> `1 failed`; stderr supplied a session ID.
  - RED: `export VIRTUAL_ENV=; timeout 300 uv run pytest -q tests/test_agy_driver.py::test_parser_structured_status_and_usage` -> `1 failed`; structured stdout supplied a session ID.
  - GREEN: `export VIRTUAL_ENV=; timeout 300 uv run pytest -q tests/test_driver_base.py tests/test_agy_driver.py` -> `47 passed in 2.30s`.
  - Characterization: `export VIRTUAL_ENV=; timeout 300 uv run pytest -q tests/test_agy_driver.py::test_parser_overlapping_tools_correlated_by_step_index` -> `1 passed` with DONE tool names omitted.
- Fresh checks: `export VIRTUAL_ENV=; uv run ruff format && uv run ruff check --fix` -> `102 files left unchanged; All checks passed`; `export VIRTUAL_ENV=; uv run pyright` -> `0 errors, 0 warnings, 0 informations`; `export VIRTUAL_ENV=; timeout 300 uv run pytest -q` -> `368 passed, 1 skipped, 4 deselected in 27.75s`.
- Root cause (bugfix only): The base did not close parser-started messages at transport EOF; Agy dropped empty first fallback lines and sourced session IDs from untrusted output streams; unsupported tool error fields were treated as failure evidence.
