<!-- refract-shared-version: 3.0.5 -->

# Phase 2 Journal: v2 schema and classification

<!-- Created by the worker per worker-contract.md. Append every block; keep earlier sections. -->

## META

- Plan: docs/plans/coding-agent-cli-v2/PLAN.md
- Implementation Profile: balanced
- Consultation Profile: n/a
- Review Profile: balanced
- Implementation Job: <Refract job ID or n/a>
- Review Job: <Refract job ID or n/a>
- Started: 2026-11-14T16:45:37+07:00
- Finished: 2026-11-14T16:55:00+07:00

## Implementation Response

<!-- Worker appends each full `# EXTERNAL RESPONSE` block here without overwriting prior blocks. -->

# EXTERNAL RESPONSE
## META
- Phase: 2
- Started: 2026-11-14T16:45:37+07:00
- Finished: 2026-11-14T16:55:00+07:00
- Plan dir: docs/plans/coding-agent-cli-v2

## SUMMARY
Deleted v1 built-in drivers, rollout reader, usage counters, and their tests/fixtures; implemented v2 public value types, `ErrorCode`, v2 events (`SCHEMA_VERSION = 2`), `Facts`/`RequestError`, and the 12-row `classify(RunFacts, Facts, *, text)` table; and adapted `_args.py`, `_drivers/_common.py`, and `tests/toy_driver.py` so all checks pass.

## FILES MODIFIED
| Action | Path | Change |
|---|---|---|
| Deleted | `src/coding_agent_cli/_drivers/claude.py` | Removed v1 Claude driver |
| Deleted | `src/coding_agent_cli/_drivers/codex.py` | Removed v1 Codex driver |
| Deleted | `src/coding_agent_cli/_drivers/codex_rollout.py` | Removed v1 Codex rollout reader |
| Deleted | `src/coding_agent_cli/_drivers/pi.py` | Removed v1 Pi driver |
| Deleted | `src/coding_agent_cli/_drivers/agy.py` | Removed v1 Agy driver |
| Deleted | `src/coding_agent_cli/_drivers/_usage.py` | Removed v1 token counter module |
| Deleted | `tests/test_claude_driver.py` | Removed v1 Claude driver tests |
| Deleted | `tests/test_codex_driver.py` | Removed v1 Codex driver tests |
| Deleted | `tests/test_codex_rollout.py` | Removed v1 Codex rollout tests |
| Deleted | `tests/test_pi_driver.py` | Removed v1 Pi driver tests |
| Deleted | `tests/test_agy_driver.py` | Removed v1 Agy driver tests |
| Deleted | `tests/test_usage.py` | Removed v1 usage tests |
| Deleted | `tests/fixtures/usage/` | Removed v1 usage fixtures |
| Deleted | `tests/cli_stub.py` | Removed orphaned v1 CLI stub |
| Created | `src/coding_agent_cli/driver.py` | Added `Facts` and `RequestError` |
| Modified | `src/coding_agent_cli/types.py` | Added `ErrorCode`, `Outcome.LIMIT_REACHED`, v2 `Request`/`Result`/`Capabilities` fields |
| Modified | `src/coding_agent_cli/events.py` | Set `SCHEMA_VERSION = 2`, dropped `schema_version`, added v2 events and fields |
| Modified | `src/coding_agent_cli/_classify.py` | Added `RunFacts` and 12-row `classify(run, facts, *, text)` |
| Modified | `src/coding_agent_cli/registry.py` | Emptied `_builtin_drivers()` |
| Modified | `src/coding_agent_cli/__init__.py` | Re-exported new public types and events |
| Modified | `src/coding_agent_cli/_args.py` | Used `ErrorCode` enum members in `Diagnostic` construction |
| Modified | `src/coding_agent_cli/_drivers/_common.py` | Removed orphaned v1 helpers; added `run_facts()` for `toy_driver.py` |
| Modified | `tests/toy_driver.py` | Adapted to `RunFacts`, `Facts`, and `classify()` |
| Modified | `tests/test_types.py` | Added v2 `ErrorCode`, `Outcome`, `Request`, `Result`, `Capabilities` tests |
| Modified | `tests/test_events.py` | Added v2 `SCHEMA_VERSION` and event variant tests |
| Modified | `tests/test_classify.py` | Added `test_row01_cancelled`..`test_row12_success` and precedence/warning tests |
| Modified | `tests/test_registry.py` | Updated built-in registry test for empty built-ins |
| Modified | `docs/plans/coding-agent-cli-v2/phase-02/notes.md` | Recorded Task 1–4 decisions and RED->GREEN evidence |
| Modified | `docs/plans/coding-agent-cli-v2/phase-02/journal.md` | Updated META and appended `# EXTERNAL RESPONSE` |

## NOTES
- phase-02/notes.md (## Task 1, ## Task 2, ## Task 3, ## Task 4)
- Fresh checks: `ruff format --check` pass (62 files); `ruff check` pass; `pyright` 0 errors; `pytest` 123 passed, 4 deselected (baseline 505 passed, 4 deselected; dropped with deleted v1 driver tests, rose with new v2 schema/classification tests).

## SPEC COMPLIANCE
- Meets Spec? YES: All Phase 2 Done When criteria and design sections pass with behavioral RED->GREEN evidence and fresh checks.

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

- Phase base ref: refs/plans/coding-agent-cli-v2/phase-02/base
- Phase implementation ref: refs/plans/coding-agent-cli-v2/phase-02/impl
- Plan commit ref: refs/plans/coding-agent-cli-v2/impl or `pending`
- State checkpoint: temporary commit, or `n/a (untracked)` when plan artifacts
  are excluded

Phase refs retain review evidence after checkpoint consolidation. The plan ref
names the sole commit retained on the branch.

# EXTERNAL RESPONSE
## META
- Phase: 2 (FIX batch: Gate 3 findings)
- Started: 2026-11-14T17:36:51+07:00
- Finished: 2026-11-14T17:39:30+07:00
- Plan dir: docs/plans/coding-agent-cli-v2
## SUMMARY
Fixed all three Gate 3 findings in `_drivers/_common.py` and `_classify.py` with failing-first tests in `tests/test_classify.py` and updated `tests/toy_driver.py` to match.
## FILES MODIFIED
| Action | Path | Change |
|---|---|---|
| Modify | `src/coding_agent_cli/_drivers/_common.py` | Removed live `cancel` event from `run_facts(process)` so `stop_cause` uses latched `process.error_class`; mapped `invalid_prompt` to `RunFacts.request_error` |
| Modify | `src/coding_agent_cli/_classify.py` | Added `_HTTP_STATUS_RE` and `_http_code_from_text` reusing `http_status_code` in `keyword_code` so unstructured HTTP 5xx classifies as `SERVICE_ERROR` over nonzero exit |
| Modify | `tests/toy_driver.py` | Updated `run_facts(process)` call |
| Modify | `tests/test_classify.py` | Added tests for latched `run_facts` stop cause, `invalid_prompt` mapping, unstructured HTTP 5xx precedence, and 4xx non-match |
| Modify | `docs/plans/coding-agent-cli-v2/phase-02/notes.md` | Appended `## FIX: Gate 3 Review Findings (1-3)` |
| Modify | `docs/plans/coding-agent-cli-v2/phase-02/journal.md` | Appended Gate 3 FIX `# EXTERNAL RESPONSE` |
## NOTES
- `docs/plans/coding-agent-cli-v2/phase-02/notes.md` (`## FIX: Gate 3 Review Findings (1-3)`)
- Fresh checks: `ruff format --check` (67 files already formatted), `ruff check` (All checks passed!), `pyright` (0 errors, 0 warnings, 0 informations), `pytest` (160 passed, 4 deselected).
## SPEC COMPLIANCE
- Meets Spec? YES: All three Gate 3 findings are fixed with RED->GREEN tests, `classify` keeps its keyword-only `text` signature and contains no CLI names, and all required checks pass.
## CLARIFICATIONS NEEDED
None
## NEXT
TASK_COMPLETE

## Coordinator decision: Gate 3 closure (2026-11-14)
- Review ac7f3579 FAIL (4 blocking). Finding 4 (`classify` keyword-only `text`) closed by Coordinator decision: accumulated text is owned by the base driver, not by parser `Facts` or transport `RunFacts`; the signature is `classify(run, facts, *, text)`.
- FIX 8aa12831 resolved findings 1-3 (latched stop cause, invalid_prompt request error, unstructured HTTP 5xx). Re-review b9d6f9c4 confirmed them and found one new defect: the first HTTP status in text won over precedence.
- Coordinator fixed it as a local logic fix with a regression test (review-fix table row 3): `_http_codes_from_text` collects all statuses and the keyword loop applies precedence. `test_mixed_http_statuses_follow_precedence_not_text_order` failed before, passes after.
- Fresh checks: ruff format --check 67 files already formatted; ruff check passed; pyright 0 errors; pytest 161 passed, 4 deselected.
