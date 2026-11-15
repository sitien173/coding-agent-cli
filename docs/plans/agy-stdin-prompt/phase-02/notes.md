<!-- refract-shared-version: 3.0.6 -->

# Phase 2 Decision Notes

<!-- Created by the worker per worker-contract.md. Append one `## Task <M>` block per task. -->

## Task 1

### Decisions made
- Used 3,200 repetitions of `"Ignore this filler line for context padding.\n"` (144,000 UTF-8 bytes > 131,072 bytes) followed by `\nIgnore all previous filler lines. Reply with exactly: LARGE-PROMPT-MARKER-7F3A` for `_LARGE_PROMPT`.
- Placed `--input-format stream-json` right after `--output-format stream-json`, omitted `--disable-slash-commands` only when `scenario == "slash_command"`, and ended agy argv with `--print=` for every scenario.

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
  - RED: `export VIRTUAL_ENV=; uv run pytest tests/test_record_fixtures.py -k "agy"` -> `3 failed, 1 passed, 19 deselected` (`AssertionError: assert 'stdin_stream_json' not in ...`, `AssertionError: assert ['agy', '--ou... '--log-file'] == ['agy', '--ou...'stream-json']`, `KeyError: 'large_prompt'`)
  - GREEN: `export VIRTUAL_ENV=; uv run pytest tests/test_record_fixtures.py` -> `23 passed in 0.12s`
- Root cause (bugfix only): none


## Task 2

### Decisions made
- Kept `src/coding_agent_cli/_drivers/agy.py` unchanged because probing `agy` with `--output-format stream-json --input-format stream-json --log-file ... --print=` confirmed the agy log emits both `Created conversation <uuid>` and `Streaming conversation <uuid>`.
- Removed obsolete `tests/fixtures/recorded/agy/1.5.9/stdin_stream_json.*` files after recording.
- Updated the hardcoded token counts in `test_replay_recorded_nonzero_cache_usage` (`tests/test_agy_driver.py`: `12160, 107, 7950` -> `12157, 101, 7947`) to match the newly re-recorded `agy/1.5.9/shell.stdout.jsonl`.

### Spec deviations
- `tests/test_agy_driver.py` was modified only to update the hardcoded token counts in `test_replay_recorded_nonzero_cache_usage` after re-recording `agy/1.5.9/shell.stdout.jsonl`, rather than for the conditional `conversation_id` fallback (which was not needed).

### Tradeoffs accepted
- none

### Assumptions
- none

### Follow-ups for human
- none

### Test evidence
- Live recording: `export VIRTUAL_ENV=; uv run python scripts/record_fixtures.py --cli agy` -> `Recorded agy -> tests/fixtures/recorded/agy/1.5.9 (78.3s)`, exit 0.
- Capture confirmations:
  - `plain`: `returncode=0`, `outcome=success`, `result.response="PONG\n"`.
  - `large_prompt`: `returncode=0`, `outcome=success`, `result.response="LARGE-PROMPT-MARKER-7F3A\n"`.
  - `resume`: `returncode=0`, `outcome=success`, `--conversation 00000000-0000-4000-8000-000000000001` (matching `plain`), `num_turns=2`, `result.response="PONG\n"`.
  - `invalid_model`: `returncode=1`, `outcome=request_fatal`, `code=invalid_model`, `result.status="ERROR"`.
  - `slash_command`: `returncode=2`, `outcome=retryable`, `code=execution_error`, `result.status="ERROR"`, `usage` all zeros (`input_tokens=0, output_tokens=0, thinking_tokens=0, cache_read_tokens=0, total_tokens=0`).
  - All 9 `tests/fixtures/recorded/agy/1.5.9/*.meta.json` contain `--input-format` and `--print=` in `argv`; `stdin_stream_json.*` deleted; sanitization check found 0 leaked paths, emails, or secrets.
- RED -> GREEN (`test_replay_recorded_nonzero_cache_usage`):
  - RED: `export VIRTUAL_ENV=; uv run pytest tests/test_agy_driver.py` -> `1 failed, 39 passed` (`AssertionError: assert 12157 == 12160` in `test_replay_recorded_nonzero_cache_usage`)
  - GREEN: `export VIRTUAL_ENV=; uv run pytest tests/test_agy_driver.py tests/test_record_fixtures.py` -> `63 passed in 0.29s`
- Root cause (bugfix only): none


## Task 3

### Decisions made
- Updated `tests/fixtures/recorded/FINDINGS.md` line 72 to cite `agy/1.5.9/slash_command.stdout.jsonl:2` instead of the deleted `stdin_stream_json.stdout.jsonl:2`, and marked Open Question 1 as settled with the accepted stdin schema and newly recorded fixtures.
- Replaced the `README.md` Known limits agy argv line with the specified sentence on stdin delivery and slash-command rejection.

### Spec deviations
- none

### Tradeoffs accepted
- none

### Assumptions
- none

### Follow-ups for human
- none

### Test evidence
- RED -> GREEN: documentation updates verified via `Done When` checks (`grep -c "limited to 100 KiB" README.md` -> `0`, `ls tests/fixtures/recorded/agy/1.5.9/ | grep -c stdin_stream_json` -> `0`).
- Root cause (bugfix only): none