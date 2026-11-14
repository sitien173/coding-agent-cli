<!-- refract-shared-version: 3.0.5 -->

# Phase 6 Decision Notes

<!-- Created by the worker per worker-contract.md. Append one `## Task <M>` block per task. -->

## Task 1

### Decisions made
- Used `reject_unsupported`, `reject_transport_flags`, and `reject_conflicts` from `_args.py` for `model`/`reasoning` support, `--json` ownership, and `--model`/`-m` conflict checks.
- Implemented `_has_reasoning_conflict` to reject only `-c`/`--config` assignments to `model_reasoning_effort=...` when `Request.reasoning` is set, allowing unrelated `-c` args.
- Placed `resume` right after `exec` and before `--json`, options, `<session_id>`, and `-`, matching `codex/0.169.0/resume.meta.json` and `FINDINGS.md` #2.

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
  - RED: `export VIRTUAL_ENV=; timeout 300 uv run pytest tests/test_codex_driver.py < /dev/null` -> `14 failed, 4 passed in 0.12s` (`AssertionError: assert () == ...` and `Failed: DID NOT RAISE RequestError`)
  - GREEN: `export VIRTUAL_ENV=; timeout 300 uv run pytest tests/test_codex_driver.py < /dev/null` -> `18 passed in 0.06s`
- Root cause (bugfix only): none

## Task 2

### Decisions made
- Mapped `agent_message` to `MessageStarted`, `TextDelta` (when non-empty), and `MessageCompleted`, with `entity_id` from `item["id"]` or `None`.
- Mapped `command_execution` to `ToolStarted(activity="command")` and `ToolCompleted` with `aggregated_output` and status from `exit_code`.
- Mapped `file_change` to `ToolStarted(activity="tool_call")`, `ToolCompleted`, and one `FileChanged(evidence="reported")` per entry of `changes`, mapping `"update"` (observed in `codex/0.169.0/edit.stdout.jsonl`) to `"modified"`.
- Mapped `mcp_tool_call` and `web_search` to `ToolStarted`/`ToolCompleted` with `activity="tool_call"`, `reasoning` to `ReasoningDelta`, `error` items to `Notice`, and ignored `todo_list`.
- Classified `turn.failed` and top-level `error` messages via `keyword_code` with a fallback for `"model ... not supported"` (`codex/0.169.0/invalid_model.stdout.jsonl`) to `ErrorCode.INVALID_MODEL`, else `ErrorCode.EXECUTION_ERROR`; cleared transient `self._error` on `turn.completed`.

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
  - RED: `export VIRTUAL_ENV=; timeout 300 uv run pytest tests/test_codex_driver.py < /dev/null` -> `6 failed, 19 passed in 0.12s` (`AssertionError` on empty event lists, `facts.error is None`, and `facts.error_text == ''`)
  - GREEN: `export VIRTUAL_ENV=; timeout 300 uv run pytest tests/test_codex_driver.py < /dev/null` -> `25 passed in 0.15s`
- Root cause (bugfix only): none

## Task 3

### Decisions made
- Parametrized `test_recorded_codex_replay_and_truncation` over all 8 scenarios in `tests/fixtures/recorded/codex/0.169.0/`, verifying `outcome`, `error.code`, `model is None`, `RunCompleted` sequence monotonicity, and partial text preservation after every `TextDelta` via `replay_truncated`.
- Added `test_codex_driver_run_end_to_end` bounded with `asyncio.timeout(1.5)`.

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
  - RED: N/A (Task 3 verifies the Task 2 parser against all 8 recorded fixtures; all 8 passed on first run)
  - GREEN: `export VIRTUAL_ENV=; timeout 300 uv run pytest tests/test_codex_driver.py < /dev/null` -> `34 passed in 0.13s`
- Root cause (bugfix only): none

## FIX Batch 1 — Codex review

### Decisions made
- Retained the highest-precedence diagnostic (auth, invalid model/context overflow, service error, then generic execution error); `turn.completed` still clears transient errors.
- Recognized attached `-mMODEL` model conflicts and attached `-cmodel_reasoning_effort=...` conflicts without rejecting unrelated `-c` assignments.
- Synthesized a `ToolStarted` before any tool completion lacking a matching start, tracking missing IDs by item type; emitted the required empty `TextDelta` for empty agent messages.
- Restricted file-change kinds to the Codex schema values `add`, `update`, and `delete`; MCP tool naming now uses its schema `tool` field, and web-search naming uses its item type. Unsupported MCP items without `tool` are ignored. Schema sources: https://github.com/openai/codex/blob/main/sdk/typescript/src/items.ts and https://github.com/openai/codex/blob/main/codex-rs/exec/src/exec_events.rs.
- Command execution status is `unknown` unless a valid integer exit code is present.

### Spec deviations
- none

### Tradeoffs accepted
- Malformed MCP items that omit the schema-required `tool` field are ignored instead of inventing a display name.

### Assumptions
- Codex `exec --json` item fields and file-change kinds follow the official schema definitions cited above; fixture shapes remain supported.

### Follow-ups for human
- none

### Test evidence
- RED -> GREEN:
  - RED: `export VIRTUAL_ENV=; timeout 300 uv run pytest -q tests/test_codex_driver.py` -> `10 failed, 37 passed`; failures covered attached flag conflicts, unsupported aliases, missing tool starts, empty deltas, diagnostic precedence, and command status.
  - GREEN: `export VIRTUAL_ENV=; timeout 300 uv run pytest -q tests/test_codex_driver.py` -> `47 passed`.
  - RED (schema-only edge): `export VIRTUAL_ENV=; timeout 300 uv run pytest -q tests/test_codex_driver.py -k mcp_tool_name_uses_schema_field_consistently` -> `1 failed, 46 deselected`; GREEN: included in the 47-pass Codex run.
- Final verification: repository Ruff format check (`100 files already formatted`), Ruff check, pyright (`0 errors, 0 warnings, 0 informations`), and `export VIRTUAL_ENV=; timeout 300 uv run pytest -q` (`315 passed, 1 skipped, 4 deselected`).
- Root cause (bugfix only): parser accepted unsupported field aliases, replaced specific diagnostics with generic later failures, and failed to uphold event/status contracts in edge cases.
