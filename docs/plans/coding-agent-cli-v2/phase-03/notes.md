<!-- refract-shared-version: 3.0.5 -->

# Phase 3 Decision Notes

<!-- Created by the worker per worker-contract.md. Append one `## Task <M>` block per task. -->

## Task 1

### Decisions made
- Read stdout and stderr concurrently in 64 KiB chunks, then dispatch decoded lines serially from a queue.
- Capture stderr as a raw byte tail so it remains capped at 64 KiB independently of callback speed.

### Spec deviations
- none

### Tradeoffs accepted
- The line queue is unbounded so pipe readers never block behind a slow callback and can continue draining both pipes.

### Assumptions
- `max_line_bytes` limits line content bytes before UTF-8 replacement decoding.

### Follow-ups for human
- none

### Test evidence
- RED -> GREEN:
  - RED: `export VIRTUAL_ENV=; uv run pytest tests/test_transport.py -q` -> 4 failed on intended assertions: no lines were dispatched, stderr tail was empty, and return code was not captured by the inert transport.
  - GREEN: `export VIRTUAL_ENV=; uv run pytest tests/test_transport.py -q` -> 4 passed in 1.25s.
- Root cause (bugfix only): none

## Task 2

### Decisions made
- Use one supervisor loop with process-exit, cancellation, activity, callback, and deadline wakeups.
- Freeze the remaining idle interval while a line callback runs and resume it when the callback returns.
- Wrap parser errors in `ParserFailure`; return the parser failure message while re-raising other callback exceptions after cleanup.

### Spec deviations
- none

### Tradeoffs accepted
- The subprocess protocol's process-exit callback is observed to distinguish leader exit from pipe EOF when descendants inherit the pipes.

### Assumptions
- Wall timeout includes spawn time; idle timeout begins after successful spawn.

### Follow-ups for human
- none

### Test evidence
- RED -> GREEN:
  - RED: `export VIRTUAL_ENV=; uv run pytest tests/test_transport.py::test_idle_timeout_is_paused_during_slow_line_callback -q` -> failed because stop cause was `idle_timeout` instead of `exited` while the callback held the line.
  - GREEN: `export VIRTUAL_ENV=; uv run pytest tests/test_transport.py -q` -> 12 passed in 2.82s.
- Root cause (bugfix only): the idle deadline continued elapsing during the awaited callback; supervisor now pauses and resumes the remaining interval.

## Task 3

### Decisions made
- After leader exit, allow one bounded EOF wait, then kill the process group/job and drain remaining reader tasks under the total cleanup deadline.
- If the cleanup deadline expires during graceful tree termination, immediately force-kill the tracked tree and report `cleanup_incomplete`.
- Configure Windows jobs with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` before assigning the process.

### Spec deviations
- none

### Tradeoffs accepted
- Leader exit notification uses the asyncio subprocess protocol callback because `Process.wait()` can remain pending while descendants hold inherited pipes open.

### Assumptions
- POSIX descendant PID checks treat a zombie as terminated because it cannot execute or retain pipe handles.

### Follow-ups for human
- Windows Job Object behavior test is present but was skipped on this Linux runner.

### Test evidence
- RED -> GREEN:
  - RED: `export VIRTUAL_ENV=; uv run pytest tests/test_transport.py::test_cleanup_deadline_reports_incomplete_and_force_kills_descendant -q` -> failed because the cleanup path waited for the inherited pipe to close naturally, returned `cleanup_incomplete=False`, and took 5.15s despite a 0.03s deadline.
  - GREEN: `export VIRTUAL_ENV=; uv run pytest tests/test_transport.py::test_cleanup_deadline_reports_incomplete_and_force_kills_descendant tests/test_transport.py::test_descendant_is_killed_after_leader_exits -q` -> 2 passed in 0.24s.
- Root cause (bugfix only): `Process.wait()` completion was coupled to pipe EOF, so the leader exit was not observed until the descendant closed inherited pipes. The transport now observes leader exit separately and applies its bounded cleanup.

## Fresh phase checks

- `export VIRTUAL_ENV=; uv run ruff format --check` -> 81 files already formatted.
- `export VIRTUAL_ENV=; uv run ruff check` -> all checks passed.
- `export VIRTUAL_ENV=; uv run pyright` -> 0 errors, 0 warnings, 0 informations.
- `export VIRTUAL_ENV=; uv run pytest` -> 522 passed, 1 skipped, 4 deselected in 34.50s.

## FIX Batch: Gate 3 findings

### Decisions made
- Keep the first stop cause latched while examining callback and parser failures after bounded drain and cleanup.
- Bound the shared stdout/stderr line queue at 16 entries and pause idle timeout while callbacks or queued lines represent pending consumer work.
- Run the optional cleanup hook after process-tree cleanup, and await the existing cleanup task through repeated caller cancellation.

### Spec deviations
- none

### Tradeoffs accepted
- The two pipe readers share a bounded queue, so either reader can apply pipe backpressure while the serial callback consumes buffered lines.
- After a callback fails, the dispatcher drops further callbacks but continues consuming queued lines until both readers reach EOF, allowing both pipes to drain during cleanup.

### Assumptions
- The optional `cleanup_hook` may be synchronous or return an awaitable; callers own its cleanup behavior.
- `queue.empty()` is used with callback state to distinguish actual idle time from buffered work awaiting the serial consumer.

### Follow-ups for human
- none

### Test evidence
- RED -> GREEN:
  - RED: `export VIRTUAL_ENV=; uv run pytest tests/test_transport.py::test_line_queue_is_bounded_for_fast_producer_and_slow_consumer tests/test_transport.py::test_callback_failure_after_leader_exit_is_reraised tests/test_transport.py::test_parser_failure_after_leader_exit_is_returned tests/test_transport.py::test_repeated_caller_cancellation_waits_for_descendant_cleanup tests/test_transport.py::test_cleanup_hook_runs_after_callback_failure_and_cancellation -q` -> 4 failed, 1 passed. The queue reached 1020 entries, the late callback exception was lost, parser message was empty, and the hook did not run. The cancellation regression was subsequently strengthened with a gated kill.
  - RED: `export VIRTUAL_ENV=; uv run pytest tests/test_transport.py::test_repeated_caller_cancellation_waits_for_descendant_cleanup -q` with the one-shot shield behavior -> 1 failed because the caller task completed before cleanup.
  - RED: `export VIRTUAL_ENV=; uv run pytest tests/test_transport.py::test_idle_timeout_stays_paused_under_queue_backpressure -q` -> 1 failed: stop cause was `idle_timeout` rather than `exited` while buffered lines awaited the slow callback.
  - GREEN: `export VIRTUAL_ENV=; uv run pytest tests/test_transport.py -q` -> 23 passed, 1 skipped.
  - GREEN: gated repeated-cancellation regression -> 1 passed; descendant was dead when `CancelledError` surfaced.
- Root cause (bugfix only): the exit branch completed cleanup without checking a callback task that failed during drain; cleanup discarded gathered exceptions; shielding a coroutine did not preserve an awaitable cleanup reference through another cancellation; the unbounded queue accumulated output; and no hook ran after process-tree cleanup.
- Fresh verification: `export VIRTUAL_ENV=; uv run ruff format --check` -> 76 files already formatted; `export VIRTUAL_ENV=; uv run ruff check` -> all checks passed; `export VIRTUAL_ENV=; uv run pyright` -> 0 errors, 0 warnings, 0 informations; `export VIRTUAL_ENV=; uv run pytest` -> 184 passed, 1 skipped, 4 deselected.
- Repetition: `for i in 1 2 3 4 5; do export VIRTUAL_ENV=; uv run pytest tests/test_transport.py -q || exit $?; done` -> all 5 runs passed, each 23 passed and 1 skipped; no flakes.

## FIX Batch 2: Re-review 8815cbd1

### Decisions made
- Re-check the asynchronous `on_spawn` task after cleanup so failures that arrive during the leader-exit EOF wait are propagated.
- Make cancellation of readers and the dispatcher terminal: cancelled readers do not enqueue EOF sentinels, and the dispatcher re-raises callback cancellation.
- Use one absolute cleanup deadline for tree termination, draining, and the cleanup hook. A hook timeout contributes to `cleanup_incomplete`.
- Protect cleanup hooks on spawn failures with a shielded, deadline-bounded task, preserving caller cancellation until hook completion or deadline expiry.
- Increase timing separation in the existing alternating-pipe and slow-callback tests after repetition exposed startup-sensitive idle-timeout flakes; both tests still distinguish reset/pause behavior from timeout behavior.

### Spec deviations
- none

### Tradeoffs accepted
- A hook that exceeds the cleanup deadline is cancelled and reported through `cleanup_incomplete`; its task exception is consumed if cancellation is delayed.
- The full-queue regression has a bounded test-only rescue path so a failing implementation cannot leave its own subprocess and queue tasks hanging during teardown.

### Assumptions
- A cleanup hook that completes before the deadline owns removal of its resources; the transport does not infer temporary-directory paths.
- Immediate process-tree force kill is scheduled on cleanup deadline expiry and awaited only while time remains on the same absolute deadline.

### Follow-ups for human
- none

### Test evidence
- RED -> GREEN:
  - RED: `export VIRTUAL_ENV=; timeout 10 uv run pytest tests/test_transport.py::test_late_on_spawn_failure_after_leader_exit_is_reraised_and_tree_killed -vv -s` -> failed because the expected late `ValueError` was not raised.
  - RED: `export VIRTUAL_ENV=; timeout 10 uv run pytest tests/test_transport.py::test_full_queue_cleanup_deadline_cancels_dispatcher_and_runs_hook -vv -s` -> failed because cleanup had not completed before test rescue released the stalled consumer.
  - RED: `export VIRTUAL_ENV=; timeout 8 uv run pytest tests/test_transport.py::test_spawn_failure_hook_survives_repeated_caller_cancellation tests/test_transport.py::test_stalled_cleanup_hook_is_bounded_and_reported_incomplete -vv -s` -> 2 failed: repeated cancellation prevented hook completion, and the stalled hook held the caller past the cleanup deadline.
  - GREEN: `export VIRTUAL_ENV=; timeout 30 uv run pytest tests/test_transport.py::test_late_on_spawn_failure_after_leader_exit_is_reraised_and_tree_killed tests/test_transport.py::test_full_queue_cleanup_deadline_cancels_dispatcher_and_runs_hook tests/test_transport.py::test_spawn_failure_hook_survives_repeated_caller_cancellation tests/test_transport.py::test_stalled_cleanup_hook_is_bounded_and_reported_incomplete tests/test_transport.py::test_cleanup_hook_removes_temp_directory_on_every_exit_path -q` -> 11 passed.
  - GREEN: `export VIRTUAL_ENV=; timeout 300 uv run pytest -q` -> 195 passed, 1 skipped, 4 deselected.
- Parametrized cleanup-hook coverage verifies real temporary-directory removal for success, cancel event, wall timeout, idle timeout, spawn failure, callback failure, and caller cancellation.
- Repetition: `for i in 1 2 3 4 5; do export VIRTUAL_ENV=; timeout 120 uv run pytest -q tests/test_transport.py || exit $?; done` -> final five runs passed, each 34 passed, 1 skipped. Earlier repetitions exposed startup-sensitive idle-timeout flakes in `slow_trickle.py` and the slow-callback test; after widening their timing margins, the final five runs had no flakes.
- Fresh checks: `export VIRTUAL_ENV=; uv run ruff format` -> 76 files left unchanged; `export VIRTUAL_ENV=; uv run ruff check --fix` -> all checks passed; `export VIRTUAL_ENV=; uv run pyright` -> 0 errors, 0 warnings, 0 informations.
- Root cause (bugfix only): the post-exit path did not inspect `spawn_task`; reader finalizers depended on queue sentinels after cancellation and the dispatcher swallowed its own cancellation; spawn-failure hook awaits were unshielded; and the cleanup hook was outside the process cleanup deadline.
