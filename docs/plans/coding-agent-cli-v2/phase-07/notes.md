<!-- refract-shared-version: 3.0.5 -->

# Phase 7 Decision Notes

<!-- Created by the worker per worker-contract.md. Append one `## Task <M>` block per task. -->

## Task 1

### Decisions made
- Omitted `-p` from `PiDriver.launch()` argv and delivered the prompt via `stdin` (UTF-8), per `tests/fixtures/recorded/FINDINGS.md` (`pi --mode json` without `-p` reads stdin and runs non-interactively).
- Rejected `-p` and `--print` alongside `--mode` and `--session` as owned transport flags via `_args.reject_transport_flags`, and mapped `model` -> `--model`, `reasoning` -> `--thinking`, `system_prompt` -> `--system-prompt` via `_args.reject_unsupported` and `_args.reject_conflicts`.

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
  - RED: `export VIRTUAL_ENV=; timeout 300 uv run pytest tests/test_pi_driver.py` -> `11 failed, 1 passed` (`AssertionError` on `launch.argv` and `Failed: DID NOT RAISE RequestError` on owned/conflicting flags).
  - GREEN: `export VIRTUAL_ENV=; timeout 300 uv run pytest tests/test_pi_driver.py` -> `12 passed in 0.04s`.
- Root cause (bugfix only): none

## Task 2

### Decisions made
- Used `keyword_code(text) or ErrorCode.EXECUTION_ERROR` for structured assistant message errors so `_classify.py` preserves `auth`, `invalid_model`, `context_overflow`, and `service_error` precedence instead of masking them as `execution_error`.
- Cleared `self._error = None` on a subsequent successful assistant `message_end` so a recovered CLI retry does not retain a stale error.
- Set `entity_id=None` on assistant message events when `message` lacks a native `id`/`responseId`, and used `toolCallId` for `ToolStarted`/`ToolCompleted` (`bash` -> `"command"`, others -> `"tool_call"`).
- Included `cacheRead` and `cacheWrite` in `Usage.input` once per turn per `types.py` docstring, summed `usage.cost.total` across turns, and counted `turn_end` events for `num_turns`.

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
  - RED: `export VIRTUAL_ENV=; timeout 300 uv run pytest tests/test_pi_driver.py` -> `4 failed, 13 passed` (`AssertionError` on `SessionIdentified`, `ToolStarted`/`ToolCompleted`, `num_turns`, and `Facts.error`).
  - GREEN: `export VIRTUAL_ENV=; timeout 300 uv run pytest tests/test_pi_driver.py` -> `17 passed in 0.08s`.
- Root cause (bugfix only): none

## Task 3

### Decisions made
- Added parametrized replay tests for all 9 recorded Pi 1.5.2 scenarios and truncation-after-each-`TextDelta` tests.
- Left `PiParser.feed_text` storing unstructured stderr text in `Facts.error_text` per `DESIGN.md` and `prompt.md` rather than hardcoding a Pi-specific stderr regex into `Facts.error`.

### Spec deviations
- `test_replay_recorded_pi_scenario[1.5.2-invalid_model]` fails (`Outcome.RETRYABLE` vs expected `Outcome.REQUEST_FATAL` / `ErrorCode.INVALID_MODEL`) because `src/coding_agent_cli/_classify.py` (`_KEYWORDS[ErrorCode.INVALID_MODEL]`) does not match Pi's unstructured stderr `Error: Model "nonexistent-model-xyz-9999" not found. Use --list-models to see available models.`, and `_classify.py` is outside the Phase 7 allowed `Files` list.

### Tradeoffs accepted
- none

### Assumptions
- none

### Follow-ups for human
- Update `src/coding_agent_cli/_classify.py` (`keyword_code` / `_KEYWORDS[ErrorCode.INVALID_MODEL]`) to recognize `Model "..." not found` (e.g. `re.compile(r'\bmodel\s+"[^"]+"\s+not\s+found\b')` or equivalent) so unstructured `invalid_model` stderr from Pi classifies as `ErrorCode.INVALID_MODEL` (`Outcome.REQUEST_FATAL`).

### Test evidence
- RED -> GREEN:
  - RED/PARTIAL: `export VIRTUAL_ENV=; timeout 300 uv run pytest tests/test_pi_driver.py` -> `1 failed, 34 passed in 0.13s` (`test_replay_recorded_pi_scenario[1.5.2-invalid_model]` failed because `_classify.py` does not match `Model "..." not found`; all 8 other recorded scenarios and all truncation checks passed).
- Root cause (bugfix only): none
