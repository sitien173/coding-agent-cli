<!-- refract-shared-version: 3.0.5 -->

# Phase 4 Journal: Public CliDriver API and entry points

<!-- Created by the worker per worker-contract.md. Append every block; keep earlier sections. -->

## META

- Plan: docs/plans/coding-agent-cli-v2/PLAN.md
- Implementation Profile: balanced
- Consultation Profile: n/a
- Review Profile: balanced
- Implementation Job: n/a
- Review Job: n/a
- Started: 2026-11-14T12:40:00Z
- Finished: 2026-11-14T13:01:17Z

## Implementation Response

<!-- Worker appends each full `# EXTERNAL RESPONSE` block here without overwriting prior blocks. -->

# EXTERNAL RESPONSE
## META
- Phase 4 / Started 2026-11-14T12:40:00Z / Finished 2026-11-14T13:01:17Z / Plan dir docs/plans/coding-agent-cli-v2
## SUMMARY
Implemented the public `CliDriver` lifecycle and entry points, registry migration, process-module removal, and recorded-fixture replay helper.
## FILES MODIFIED
| Action | Path | Change |
|---|---|---|
| Modify | `src/coding_agent_cli/__init__.py` | Export the public `CliDriver` API and remove `Driver`. |
| Modify | `src/coding_agent_cli/_args.py` | Add common request validation. |
| Modify | `src/coding_agent_cli/_drivers/_common.py` | Keep the shared integer conversion helper only. |
| Delete | `src/coding_agent_cli/_process.py` | Remove the v1 subprocess transport. |
| Modify | `src/coding_agent_cli/api.py` | Add v2 `run`, `stream`, `run_sync`, and `check` entry points. |
| Modify | `src/coding_agent_cli/driver.py` | Add `Launch`, `Parser`, `CliDriver`, base lifecycle, and base capability check. |
| Modify | `src/coding_agent_cli/registry.py` | Accept only `CliDriver` instances. |
| Modify | `tests/test_api_contract.py` | Cover v2 entry points, positive-only check cache, and threaded sync cancellation. |
| Modify | `tests/test_args.py` | Cover common request validation. |
| Modify | `tests/test_classify.py` | Remove characterization imports tied to deleted v1 process helpers. |
| Delete | `tests/test_process.py` | Remove tests for the deleted v1 transport. |
| Modify | `tests/test_registry.py` | Cover `CliDriver`-only registration and public exports. |
| Modify | `tests/test_stream.py` | Cover stream process-tree cleanup through `aclose()`. |
| Modify | `tests/toy_driver.py` | Rewrite the toy driver as a `CliDriver` subclass. |
| Create | `tests/replay.py` | Add recorded fixture replay and truncation helpers. |
| Create | `tests/test_driver_base.py` | Cover base lifecycle, outcomes, sequencing, and cleanup. |
| Create | `tests/test_replay.py` | Cover replay and truncation with a minimal parser. |
| Modify | `docs/plans/coding-agent-cli-v2/phase-04/notes.md` | Record decisions, evidence, and deviations for each task. |
| Modify | `docs/plans/coding-agent-cli-v2/phase-04/journal.md` | Record phase metadata and this response. |
## NOTES
- `phase-04/notes.md` (`## Task 1` through `## Task 4`, plus fresh check counts).
- Fresh checks: format passed; Ruff passed; Pyright reported 0 errors; pytest reported 186 passed, 1 skipped, 4 deselected.
## SPEC COMPLIANCE
- Meets Spec? WITH_DEBT: functional acceptance criteria and final checks pass; test-first RED evidence was not captured for Tasks 2 to 4, and `tests/test_classify.py` was changed outside the path allowlist because it referenced the deleted module.
## CLARIFICATIONS NEEDED
None
## NEXT
TASK_COMPLETE

# EXTERNAL RESPONSE
## META
- Phase 4 FIX Batch 1 / Started 2026-11-14T13:02:00Z / Finished 2026-11-14T13:14:40Z / Plan dir docs/plans/coding-agent-cli-v2
## SUMMARY
Resolved all nine review findings with regression coverage for streaming waits, callbacks, parser lifecycle, read-back cleanup, replay classification, and removal warnings.
## FILES MODIFIED
| Action | Path | Change |
|---|---|---|
| Modify | `src/coding_agent_cli/api.py` | Remove the fixed stream wait timeout, await all awaitable callbacks, and type the public callback parameters. |
| Modify | `src/coding_agent_cli/driver.py` | Fix last-message accumulation, incremental parser handling, completion ownership, cleanup deadline, removal-error reporting, and callback types. |
| Modify | `tests/replay.py` | Classify from the capped stderr tail and stop synthesizing parser facts from expected metadata. |
| Modify | `tests/test_api_contract.py` | Bound the stalled-callback regression test. |
| Modify | `tests/test_driver_base.py` | Cover Future callbacks, empty final messages, generator failures, stubborn descendants/read-back, parser completion rejection, and removal errors. |
| Modify | `tests/test_replay.py` | Cover auth classification from stderr only. |
| Modify | `tests/test_stream.py` | Cover a CLI silent for longer than 1.5 seconds. |
| Create | `tests/fixtures/fake_cli/silent_then_output.py` | Add a silent-delay fake CLI for stream regression coverage. |
| Create | `tests/fixtures/fake_cli/two_messages.py` | Add a fake CLI for empty-final-message coverage. |
| Modify | `docs/plans/coding-agent-cli-v2/phase-04/notes.md` | Append FIX Batch 1 decisions and RED-to-GREEN evidence. |
| Modify | `docs/plans/coding-agent-cli-v2/phase-04/journal.md` | Append this FIX Batch 1 response. |
## NOTES
- `phase-04/notes.md` (`## FIX Batch 1`): focused RED run had 7 failures / 27 passes; stderr-only replay RED had 1 failure; GREEN focused run had 35 passes.
- Fresh checks: Ruff passed; Pyright reported 0 errors; full pytest reported 194 passed, 1 skipped, 4 deselected; the requested targeted suite passed 32 tests in all 3 runs.
## SPEC COMPLIANCE
- Meets Spec? YES: all requested findings are fixed, RED-to-GREEN evidence is recorded, and all requested checks pass.
## CLARIFICATIONS NEEDED
None
## NEXT
TASK_COMPLETE

## COORDINATOR CLOSURE

- Re-review 528bf134 returned FAIL with 2 blocking and 1 non-blocking finding.
- Coordinator fixed all three directly in b64ae36, each with a regression test that failed first:
  1. `on_event` raising `ParserFailure` was classified as `parser_error`. `CliDriver.run` now wraps caller exceptions from event delivery and re-raises the original after cleanup.
  2. `tests/replay.py` returned earlier text for an empty last message. It now uses the same last-started-message rule as the driver.
  3. Truncated replay classified stderr it never fed. Replay now builds the stderr tail only from fed lines; a truncated replay sees no stderr because separate captures carry no cross-stream timing.
- Checks at b64ae36: ruff format and check pass, pyright 0 errors, pytest 197 passed, 1 skipped, 4 deselected; driver, API, stream and replay tests 3 runs green.
- Phase 4 closed.
