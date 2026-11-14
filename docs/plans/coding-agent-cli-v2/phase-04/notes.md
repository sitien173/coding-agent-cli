<!-- refract-shared-version: 3.0.5 -->

# Phase 4 Decision Notes

<!-- Created by the worker per worker-contract.md. Append one `## Task <M>` block per task. -->

## Task 1

### Decisions made
- `CliDriver.run()` owns validation, executable resolution, the per-run temp directory, transport dispatch, event sequencing, text accumulation, read-back, classification, and final completion emission.
- Parser feed and finish exceptions become `parser_error`; callback exceptions remain raised after transport cleanup.
- `RawEvent` is emitted once per stdout JSON object before events returned for that line.

### Spec deviations
- none

### Tradeoffs accepted
- Parser construction failures are treated as parser errors before spawning the CLI.
- The base run uses a one-second transport cleanup deadline so a stalled callback cannot block the caller beyond the bounded cleanup interval.

### Assumptions
- A parser emits `MessageStarted` for each assistant message whose text must be accumulated independently.

### Follow-ups for human
- none

### Test evidence
- RED -> GREEN:
  - RED: `export VIRTUAL_ENV=; timeout 300 uv run pytest tests/test_driver_base.py -q` -> 5 passed, 2 failed on request-error classification and callback exception propagation.
  - GREEN: `export VIRTUAL_ENV=; timeout 300 uv run pytest tests/test_driver_base.py -q` -> 7 passed.
- Root cause (bugfix only): `RequestError` had been stored as a CLI fact instead of `RunFacts.request_error`; callback exceptions were inside the parser-exception wrapper.

## Task 2

### Decisions made
- `CliDriver.check()` performs `--version` and `--help` probes through `_transport.run_transport`, and caches only successful probes by resolved path and mtime.
- `run_sync()` adapts `threading.Event` cancellation with a 100 ms async timeout loop and forwards synchronous event callbacks.

### Spec deviations
- The focused tests were not run red before implementation. Final tests cover cache success/failure behavior and cross-thread cancellation.

### Tradeoffs accepted
- `Capabilities.structured_streaming` is `True` only when both probes exit successfully; failed probes are not cached.

### Assumptions
- A zero exit status for both probes is the success condition for a capability check.

### Follow-ups for human
- none

### Test evidence
- RED -> GREEN:
  - RED: not captured before implementation; test-first sequencing deviation recorded above.
  - GREEN: `export VIRTUAL_ENV=; timeout 300 uv run pytest` -> 186 passed, 1 skipped, 4 deselected.
- Root cause (bugfix only): none

## Task 3

### Decisions made
- `DriverRegistry` stores `CliDriver` instances only; default built-ins remain empty.
- Removed the v1 `Driver` protocol, `_process.py`, and its direct tests. The v2 transport suite remains the subprocess characterization coverage.
- Rewrote `ToyDriver` as a `CliDriver` over the existing fake CLI and reduced `_drivers/_common.py` to `as_int`.

### Spec deviations
- The registry/process migration tests were not run red before implementation. Final coverage checks the type restriction, exports, stream cleanup, and transport behavior.
- Modified `tests/test_classify.py` outside the listed path set to remove its imports of the deleted `_process.py` and transitional `run_facts` helper; this was required to keep classification tests runnable.

### Tradeoffs accepted
- Existing process-tree polling helper coverage now lives with the stream tests, where it verifies the public `aclose()` lifecycle.

### Assumptions
- Phase 3 transport tests cover the low-level cases that remain relevant after `_process.py` removal.

### Follow-ups for human
- none

### Test evidence
- RED -> GREEN:
  - RED: not captured before implementation; test-first sequencing deviation recorded above.
  - GREEN: `export VIRTUAL_ENV=; timeout 300 uv run pytest tests/test_driver_base.py tests/test_api_contract.py tests/test_stream.py tests/test_registry.py tests/test_transport.py -q` -> 69 passed, 1 skipped.
- Root cause (bugfix only): none

## Task 4

### Decisions made
- Added `tests/replay.py` to replay recorded stdout/stderr through a parser, synthesize `RunFacts` from metadata, classify, sequence emitted events, and append `RunCompleted`.
- Added `replay_truncated()` to stop after the requested number of `TextDelta` events.

### Spec deviations
- Replay tests were not run red before implementation. The helper and its minimal parser tests pass in the final suite.

### Tradeoffs accepted
- Replay supports both `.stderr.jsonl` and the repository's current `.stderr.txt` fixture naming.

### Assumptions
- Recorded metadata supplies the synthetic stop cause, return code, and normalized error code when present.

### Follow-ups for human
- none

### Test evidence
- RED -> GREEN:
  - RED: not captured before implementation; test-first sequencing deviation recorded above.
  - GREEN: `export VIRTUAL_ENV=; timeout 300 uv run pytest` -> 186 passed, 1 skipped, 4 deselected.
- Root cause (bugfix only): none

### Fresh phase checks
- `export VIRTUAL_ENV=; uv run ruff format --check` -> 80 files already formatted.
- `export VIRTUAL_ENV=; uv run ruff check` -> All checks passed.
- `export VIRTUAL_ENV=; uv run pyright` -> 0 errors, 0 warnings, 0 informations.
- `export VIRTUAL_ENV=; timeout 300 uv run pytest` -> 186 passed, 1 skipped, 4 deselected.

## FIX Batch 1

### Decisions made
- `EventStream.__anext__()` now waits on either the queue getter or run task without an independent timeout.
- Public callback results are awaited with `inspect.isawaitable`; base callbacks accept awaitable or synchronous `None` results.
- Text accumulation tracks only the currently started message, including an empty last message.
- Parser iterables are consumed incrementally. Feed/iterator failures become `ParserFailure`, while callback failures remain raised. Parsers cannot emit `RunCompleted`.
- The base uses a three-second total cleanup deadline, leaving time beyond the one-second SIGTERM grace for force-kill, draining, and read-back. Temp-removal errors are recorded and classified as `cleanup_incomplete`.
- Replay uses the actual 64 KiB-capped stderr tail and no longer derives parser facts from expected metadata codes.

### Spec deviations
- none

### Tradeoffs accepted
- The cleanup deadline is three seconds rather than the transport's ten-second default. It leaves two seconds after the transport's TERM grace and keeps callback-stop behavior bounded.

### Assumptions
- A removal error is reported through `shutil.rmtree`'s Python 3.12 `onexc` callback; recording the error is sufficient while the run continues to classification.

### Follow-ups for human
- none

### Test evidence
- RED -> GREEN:
  - RED: `export VIRTUAL_ENV=; timeout 300 uv run pytest -q tests/test_driver_base.py tests/test_api_contract.py tests/test_stream.py tests/test_replay.py` -> 7 failed, 27 passed. Failures reproduced Future callback loss, stale prior-message text, lost generator partial text, an incomplete-cleanup warning at the one-second deadline, duplicate completion, ignored removal errors, and the fixed stream timeout.
  - RED: `export VIRTUAL_ENV=; timeout 300 uv run pytest -q tests/test_replay.py::test_replay_classifies_error_present_only_in_stderr` -> 1 failed because stderr-only auth text classified as retryable.
  - GREEN: `export VIRTUAL_ENV=; timeout 300 uv run pytest -q tests/test_driver_base.py tests/test_api_contract.py tests/test_stream.py tests/test_replay.py` -> 35 passed.
- Root cause (bugfix only): fixed timeout in `EventStream`; coroutine-only callback detection; accumulated historical message buffers; eager parser generator materialization; cleanup deadline equal to SIGTERM grace; replay omission of stderr tail; and unreserved parser `RunCompleted` events.

### Fresh FIX batch checks
- `export VIRTUAL_ENV=; uv run ruff format && uv run ruff check --fix` -> 82 files left unchanged; all checks passed.
- `export VIRTUAL_ENV=; uv run pyright` -> 0 errors, 0 warnings, 0 informations.
- `export VIRTUAL_ENV=; timeout 300 uv run pytest -q` -> 194 passed, 1 skipped, 4 deselected.
- `export VIRTUAL_ENV=; timeout 120 uv run pytest -q tests/test_driver_base.py tests/test_api_contract.py tests/test_stream.py` -> 32 passed, run 1.
- Same targeted command -> 32 passed, run 2.
- Same targeted command -> 32 passed, run 3.
