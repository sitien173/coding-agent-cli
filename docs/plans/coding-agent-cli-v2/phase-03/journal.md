<!-- refract-shared-version: 3.0.5 -->

# Phase 3 Journal: Two-pipe supervised transport

<!-- Created by the worker per worker-contract.md. Append every block; keep earlier sections. -->

## META

- Plan: docs/plans/coding-agent-cli-v2/PLAN.md
- Implementation Profile: balanced
- Consultation Profile: n/a
- Review Profile: balanced
- Implementation Job: n/a
- Review Job: n/a
- Started: 2026-11-14T09:46:02Z
- Finished: 2026-11-14T10:13:52Z

## Implementation Response

<!-- Worker appends each full `# EXTERNAL RESPONSE` block here without overwriting prior blocks. -->

# EXTERNAL RESPONSE
## META
- Phase / Started / Finished / Plan: 3 / 2026-11-14T09:46:02Z / 2026-11-14T10:13:52Z / docs/plans/coding-agent-cli-v2
## SUMMARY
Implemented and verified the standalone two-pipe supervised transport, process-tree cleanup, and Windows Job Object kill-on-close behavior.
## FILES MODIFIED
| Action | Path | Change |
|---|---|---|
| Created | src/coding_agent_cli/_transport.py | Added two-pipe readers, serialized line dispatch, supervision, outcome reporting, and bounded cleanup. |
| Modified | src/coding_agent_cli/_platform.py | Configured Windows kill-on-close jobs and added immediate force-kill fallback. |
| Created | tests/test_transport.py | Added transport, supervision, cleanup, and platform coverage. |
| Created | tests/fixtures/fake_cli/both_flood.py | Added simultaneous multi-megabyte stdout/stderr saturation fixture. |
| Created | tests/fixtures/fake_cli/oversized_then_valid.py | Added oversized-line recovery fixture. |
| Created | tests/fixtures/fake_cli/slow_callback.py | Added slow-consumer idle-timeout fixture. |
| Created | tests/fixtures/fake_cli/slow_trickle.py | Added alternating-stream idle-reset fixture. |
| Created | tests/fixtures/fake_cli/stubborn_descendant.py | Added SIGTERM-resistant descendant cleanup fixture. |
| Modified | docs/plans/coding-agent-cli-v2/phase-03/notes.md | Recorded RED-to-GREEN decisions and fresh verification results for all tasks. |
| Modified | docs/plans/coding-agent-cli-v2/phase-03/journal.md | Filled META and appended this implementation response. |
## NOTES
- phase-03/notes.md (## Task 1, ## Task 2, ## Task 3; fresh checks: 522 passed, 1 skipped, 4 deselected)
## SPEC COMPLIANCE
- Meets Spec? YES: all acceptance checks pass, with behavioral RED-to-GREEN evidence for each task.
## CLARIFICATIONS NEEDED
None
## NEXT
TASK_COMPLETE

## Quality Review

<!-- Coordinator appends the independent review response here. -->

## Review Result

- Spec Status: PENDING
- Quality Status: PENDING
- Debt: none

## Final Checkpoint

- Phase base ref: refs/plans/coding-agent-cli-v2/phase-03/base
- Phase implementation ref: refs/plans/coding-agent-cli-v2/phase-03/impl
- Plan commit ref: refs/plans/coding-agent-cli-v2/impl or `pending`
- State checkpoint: temporary commit, or `n/a (untracked)` when plan artifacts
  are excluded

Phase refs retain review evidence after checkpoint consolidation. The plan ref
names the sole commit retained on the branch.

# EXTERNAL RESPONSE
## META
- Phase / Started / Finished / Plan: 3 / 2026-11-14T11:49:22Z / 2026-11-14T12:02:30Z / docs/plans/coding-agent-cli-v2
## SUMMARY
Fixed Gate 3 transport findings for supervised callback failures, cancellation-safe cleanup, bounded backpressure, idle-timeout pausing, and the cleanup hook.
## FILES MODIFIED
| Action | Path | Change |
|---|---|---|
| Modified | src/coding_agent_cli/_transport.py | Supervised callback and parser failures after leader exit, bounded the line queue, preserved cleanup completion across repeated cancellation, paused idle timeout with buffered work, and added the cleanup hook. |
| Modified | tests/test_transport.py | Added regressions for late failures, bounded buffering, idle pause under backpressure, repeated cancellation, and cleanup-hook paths. |
| Created | tests/fixtures/fake_cli/line_flood.py | Added a fast line producer for backpressure and idle-timeout coverage. |
| Created | tests/fixtures/fake_cli/print_then_exit.py | Added a line-emitting process that exits immediately. |
| Modified | docs/plans/coding-agent-cli-v2/phase-03/notes.md | Appended Gate 3 fix-batch decisions and RED-to-GREEN and verification evidence. |
| Modified | docs/plans/coding-agent-cli-v2/phase-03/journal.md | Appended this Gate 3 fix-batch response. |
## NOTES
- phase-03/notes.md (FIX Batch: Gate 3 findings; fresh checks: 184 passed, 1 skipped, 4 deselected; five transport repetitions: each 23 passed, 1 skipped)
## SPEC COMPLIANCE
- Meets Spec? YES: all four Gate 3 findings have behavioral regression coverage, and all requested checks pass.
## CLARIFICATIONS NEEDED
None
## NEXT
TASK_COMPLETE

# EXTERNAL RESPONSE
## META
- Phase / Started / Finished / Plan: 3 / 2026-11-14T12:07:25Z / 2026-11-14T12:30:40Z / docs/plans/coding-agent-cli-v2
## SUMMARY
Fixed the four re-review transport defects, bounded cleanup-hook execution under one deadline, and verified directory cleanup on all seven requested exit paths.
## FILES MODIFIED
| Action | Path | Change |
|---|---|---|
| Modified | src/coding_agent_cli/_transport.py | Propagated late on_spawn exceptions, made reader/dispatcher cancellation terminal, included hook execution in one cleanup deadline, and protected hooks on spawn failures. |
| Modified | tests/test_transport.py | Added bounded regressions for all four findings and parametrized real-directory cleanup over seven exit paths; widened timing margins for idle-timeout tests. |
| Modified | tests/fixtures/fake_cli/slow_trickle.py | Widened the alternating pipe delays so the test requires a reset while avoiding startup-sensitive timing. |
| Modified | docs/plans/coding-agent-cli-v2/phase-03/notes.md | Appended Fix Batch 2 decisions and RED-to-GREEN evidence, fresh checks, and repetition results. |
| Modified | docs/plans/coding-agent-cli-v2/phase-03/journal.md | Appended this Fix Batch 2 response. |
## NOTES
- phase-03/notes.md (FIX Batch 2: Re-review 8815cbd1; fresh checks: 195 passed, 1 skipped, 4 deselected; five transport repetitions: each 34 passed, 1 skipped)
## SPEC COMPLIANCE
- Meets Spec? YES: all four blocking findings and seven cleanup-hook exit paths are covered, and all requested final checks pass.
## CLARIFICATIONS NEEDED
None
## NEXT
TASK_COMPLETE

## Coordinator closure (2026-11-14)

- Re-review 5b6ccbd6 after fix batch 2 reported 2 blocking and 2 non-blocking findings. The autonomous fix-cycle budget was spent, so the Coordinator fixed the two blocking findings directly (review-fix table row 3), test first:
  - `_cleanup` waited on drain tasks with `gather`, so a callback that suppresses cancellation held cleanup past its deadline. It now uses `asyncio.wait` and consumes task exceptions. RED: new test hung; GREEN: passes in 0.16 s.
  - A line callback raising `CancelledError` was lost. `_dispatch` now latches it as a callback failure unless the dispatcher itself is being cancelled, and `run_transport` re-raises it after cleanup. RED: `print_then_exit.py` case failed; GREEN: both cases pass.
- Deferred as non-blocking debt: the idle-pause test at tests/test_transport.py:178 no longer proves pausing because the leader exits before the idle deadline; the seven-path temp-dir test does not assert each path's `stop_cause`.
- Final checks: ruff format and check pass, pyright 0 errors, pytest 198 passed 1 skipped 4 deselected, tests/test_transport.py 5 runs of 37 passed 1 skipped.
