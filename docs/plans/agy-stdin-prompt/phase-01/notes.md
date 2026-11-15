<!-- refract-shared-version: 3.0.6 -->

# Phase 1 Decision Notes

<!-- Created by the worker per worker-contract.md. Append one `## Task <M>` block per task. -->

## Task 1

### Decisions made
- Replaced `test_launch_rejects_oversized_and_invalid_prompt` with `test_launch_rejects_invalid_prompt` (retaining NUL and `"\ud800"` checks) and removed the `PROMPT_LIMIT_BYTES` import.
- Added `test_launch_accepts_large_prompt_on_stdin` (200 KiB ASCII prompt) and `test_launch_encodes_prompt_as_single_ndjson_line` (multiline, non-ASCII Unicode, double quotes, backslashes, and combined edge case).
- Updated `test_launch_new_project_and_log_file_read_back` to assert `--output-format stream-json`, `--input-format stream-json`, `launch.argv[-1] == "--print="`, `"--print" not in launch.argv`, prompt absence from argv, and single-line NDJSON `launch.stdin`.

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
  - RED: `export VIRTUAL_ENV=; uv run pytest tests/test_agy_driver.py -v` -> `7 failed, 31 passed in 0.32s` (`test_launch_new_project_and_log_file_read_back` and 5 parametrized cases of `test_launch_encodes_prompt_as_single_ndjson_line` failed `AssertionError` on `isinstance(None, bytes)` for `launch.stdin`; `test_launch_accepts_large_prompt_on_stdin` failed with `RequestError: agy prompt is 204800 bytes, above the 100 KiB (102400 bytes) limit for the prompt argument`)
  - GREEN: deferred to Task 2
- Root cause (bugfix only): none

## Task 2

### Decisions made
- Dropped `PROMPT_LIMIT_BYTES` (including from `__all__`) and the size check in `_validate_prompt`, preserving NUL and UTF-8 validation.
- Updated `AgyDriver.launch` to build argv in the order: executable, `--output-format stream-json`, `--input-format stream-json`, `--log-file <tmp>/agy.log`, `--conversation <id>` or `--new-project`, `--model` and `--effort` when set, `req.args`, and `--print=` last; set `stdin` to `(json.dumps({"event": "user", "message": {"content": [{"type": "text", "text": req.prompt}]}}) + "\n").encode("utf-8")`.
- Updated the module, `_validate_prompt`, and `AgyDriver` docstrings and reworded the `--print=` ordering comment. Left `_validate_command_line` intact because its message (`"agy command line is ... above the Windows limit ..."`) already does not claim the prompt is in argv.

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
  - RED: see Task 1 (`7 failed, 31 passed in 0.32s`)
  - GREEN: `export VIRTUAL_ENV=; uv run pytest tests/test_agy_driver.py -v` -> `38 passed in 0.11s`
  - Done When verification:
    - `grep -rn PROMPT_LIMIT_BYTES src tests` -> no output (exit code 1)
    - `export VIRTUAL_ENV=; uv run ruff format && uv run ruff check --fix` -> `113 files left unchanged`, `All checks passed!`
    - `export VIRTUAL_ENV=; uv run pyright` -> `0 errors, 0 warnings, 0 informations`
    - `export VIRTUAL_ENV=; uv run pytest` -> `380 passed, 1 skipped, 4 deselected in 28.96s`
- Root cause (bugfix only): none
