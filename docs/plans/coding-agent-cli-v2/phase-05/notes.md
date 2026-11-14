# Phase 5 Decision Notes

## Task 1

### Decisions made
- Included `-p`, `--print`, `--output-format`, `--verbose`, `--include-partial-messages`, and `--resume` in `_OWNED_FLAGS` so callers cannot override any transport flag via `Request.args`.
- Mapped portable options `model` -> `--model`, `reasoning` -> `--effort`, `system_prompt` -> `--system-prompt`, and validated conflicts and unsupported options through `_args.py` helpers.

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
  - RED: `export VIRTUAL_ENV=; timeout 300 uv run pytest tests/test_claude_driver.py` -> `16 failed, 3 passed` (`AssertionError` on `--verbose` / transport argv and `Failed: DID NOT RAISE RequestError` on owned/conflicting flags).
  - GREEN: `export VIRTUAL_ENV=; timeout 300 uv run pytest tests/test_claude_driver.py` -> `19 passed in 0.06s`.
- Root cause (bugfix only): none

## Task 2

### Decisions made
- Read `ToolStarted.input` from `assistant` `tool_use` blocks (not `content_block_start`, which carries `{}` under `--include-partial-messages`), and deduplicate `tool_use` blocks by `id`.
- Deduplicate `assistant` `text` and `thinking` blocks against previously streamed `text_delta` and `thinking_delta` content per message ID, emitting only unstreamed suffixes (or full text when `assistant` arrives without prior `stream_event`s).
- Infer `FileChanged(..., evidence="inferred")` on successful `tool_result` for `Edit`, `MultiEdit`, `NotebookEdit` (`change="modified"`), and `Write` (`change="unknown"`).
- Track per-message token snapshots so `Usage.input` includes `input_tokens + cache_creation_input_tokens + cache_read_input_tokens` once, while `context_used` reflects the latest turn's total context.

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
  - RED: `export VIRTUAL_ENV=; timeout 300 uv run pytest tests/test_claude_driver.py` -> `8 failed, 21 passed` (assertions on `SessionIdentified`/`MessageStarted`/`TextDelta`/`MessageCompleted`, `ReasoningDelta`, `ToolStarted`/`ToolCompleted`, `FileChanged`, and `UsageUpdated`).
  - GREEN: `export VIRTUAL_ENV=; timeout 300 uv run pytest tests/test_claude_driver.py` -> `29 passed in 0.09s`.
- Root cause (bugfix only): none

## Task 3

### Decisions made
- Ignore `<synthetic>` in `message.model` so API error messages do not overwrite a real model name, and resolve `contextWindow` from `result.modelUsage`.
- Keep summed per-message usage when `result.usage` is all zeros (observed on `error_max_budget_usd` in `claude/2.1.296/budget.stdout.jsonl`).
- Map `error_max_turns` -> `ErrorCode.MAX_TURNS`, `error_max_budget_usd` -> `ErrorCode.BUDGET`, `authentication_failed` -> `ErrorCode.AUTH`, `model_not_found` -> `ErrorCode.INVALID_MODEL`, and `api_error_status` via `http_status_code()`. Unmapped `is_error` messages go to `Facts.error_text` so `_classify.py` can keyword-match against `Facts.error_text` + `stderr_tail`.

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
  - RED: `export VIRTUAL_ENV=; timeout 300 uv run pytest tests/test_claude_driver.py` -> `11 failed, 37 passed` (recorded scenario replay outcomes for `auth_failure`, `budget`, `invalid_model`, `max_turns`, `test_error_max_turns_as_limit_reached`, `test_facts_from_result_record`, `test_api_error_status_mapping`, and `test_claude_driver_end_to_end_with_fake_cli`).
  - GREEN: `export VIRTUAL_ENV=; timeout 300 uv run pytest tests/test_claude_driver.py` -> `48 passed in 0.15s`.
- Root cause (bugfix only): none
