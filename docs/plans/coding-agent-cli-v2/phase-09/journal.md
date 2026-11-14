# Phase 9 Journal: Registration, docs, and release readiness

## META

- Plan: docs/plans/coding-agent-cli-v2/PLAN.md
- Implementation Profile: openai
- Review Profile: balanced

## Implementation Response

```text
# EXTERNAL RESPONSE
## META
- Phase: 9
- Plan dir: docs/plans/coding-agent-cli-v2
## SUMMARY
Registered the four built-in CliDriver implementations, updated live and packaging coverage for the v2 API and wheel contents, rewrote the README and added executable README checks, and updated repository guidance for the v2 layout.
## FILES MODIFIED
| Action | Path | Change |
|---|---|---|
| Modified | src/coding_agent_cli/registry.py | Register agy, claude, codex, and pi drivers by default. |
| Modified | tests/test_registry.py | Assert sorted built-in names and concrete driver resolution. |
| Modified | tests/test_live.py | Use the v2 check(driver) API and keep live markers. |
| Modified | tests/test_packaging.py | Assert scripts/ is excluded from the wheel. |
| Created | tests/test_readme.py | Compile README Python blocks and validate exported imports and migration content. |
| Modified | README.md | Document v2 APIs, data types, events, errors, migration, custom drivers, permissions, and limits. |
| Modified | AGENTS.md | Update the project layout table while retaining hard rules. |
| Modified | CONTRIBUTING.md | Update v2 module and fixture-tool references. |
| Modified | docs/plans/coding-agent-cli-v2/phase-09/notes.md | Record task decisions and verification results. |
| Modified | docs/plans/coding-agent-cli-v2/phase-09/journal.md | Record this implementation response. |
## NOTES
- Checks: format 106 files already formatted; Ruff all checks passed; Pyright 0 errors, 0 warnings, 0 informations; pytest 371 passed, 1 skipped, 4 deselected.
- Live tests were not run.
## SPEC COMPLIANCE
- Meets Spec? YES: Built-ins resolve by default, required docs and tests are updated, all four checks pass, and no prohibited release files or production modules were changed.
## CLARIFICATIONS NEEDED
None
## NEXT
TASK_COMPLETE
```

## Coordinator spec review and Gate 3

- Spec review: migration section missed `ErrorCode`, `RawEvent`, `Outcome.LIMIT_REACHED`, `Diagnostic.code` type, and new `Result`, `ToolCompleted`, `Capabilities` fields. Coordinator added them before commit d5bf7c7.
- Review cf4b9927 FAIL 5 blocking + 1 non-blocking (Windows window consequence, RunCompleted guarantee, Result.text precedence, registry and callback migration notes, default-registry test, args placement). All are documentation or test gaps; Coordinator closed them directly (review-fix row 3) with a default-registry test.
- Checks: ruff format and check pass, pyright 0, pytest 372 passed 1 skipped 4 deselected.

## Closure

Phase closed by Coordinator on 2026-11-14.
