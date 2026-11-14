# Phase 8: Agy driver

## Goal & Outcome
- Plan Goal: Rebuild `coding-agent-cli` as 0.2.0 on a public `CliDriver` API. Full design: `/home/ngosi/projects/coding-agent-cli/docs/plans/coding-agent-cli-v2/DESIGN.md`. Full plan: `/home/ngosi/projects/coding-agent-cli/docs/plans/coding-agent-cli-v2/PLAN.md`, section "Phase 8".
- Phase Outcome: `AgyDriver` and its parser on the v2 API per DESIGN.md "Drivers", Agy section. Every recorded scenario under `tests/fixtures/recorded/agy/` replays to the outcome and code in its `meta.json`, and tool errors and step correlation are correct.

## Context & Architecture
- Code root: `/home/ngosi/projects/coding-agent-cli/.worktrees/coding-agent-cli-v2-phase-08` | Records root: `/home/ngosi/projects/coding-agent-cli` | Branch: `refract/coding-agent-cli-v2/phase-08` | Base: `refs/plans/coding-agent-cli-v2/phase-08/base` (`bf3f356`)
- Prior State: Phases 1 to 4 are merged. Checks at base: ruff format/check pass, pyright 0, pytest 197 passed 1 skipped 4 deselected. Phases 5 to 8 run in parallel in other worktrees, one driver each; do not touch their files.
- Code Context, read first: `src/coding_agent_cli/driver.py` (`CliDriver`, `Launch`, `Parser`, `Facts`, `RequestError`; the base owns `run`, `check`, text accumulation, temp dir, `RawEvent`, and `RunCompleted`), `src/coding_agent_cli/_args.py` (request and portable-flag validation), `src/coding_agent_cli/_drivers/_common.py` (shared integer helper), `src/coding_agent_cli/types.py` and `events.py` (v2 schema), `src/coding_agent_cli/_classify.py` (how `Facts` become a `Result`), `tests/replay.py` (fixture replay and truncation helpers), `tests/toy_driver.py` (a minimal `CliDriver`), `tests/fixtures/recorded/agy/`, and `/home/ngosi/projects/coding-agent-cli/docs/plans/coding-agent-cli-v2/FINDINGS.md`. The deleted v1 driver is visible with `git show 24b8cca:src/coding_agent_cli/_drivers/agy.py` for flag lists and launch details only; do not copy its unproven branches.

## Required design
Implement DESIGN.md "Drivers", Agy section, PLAN.md "Phase 8" tasks, and the answers in `FINDINGS.md`. Parser branches exist only for shapes present in the recorded fixtures or in cited official docs; cite the fixture file in a short comment only where the shape is non-obvious. Every message emits `MessageStarted`, `TextDelta`s, `MessageCompleted`. `entity_id` is the CLI's native ID or `None`. Parser errors are structured `Facts.error` values; unstructured error text goes to `Facts.error_text`.
- Named tests: two overlapping tools correlated by step index, a failed tool producing status `error`. Prompt goes through stdin only if `FINDINGS.md` confirms `--input-format stream-json`; then the prompt never appears in argv (test). Otherwise argv delivery with the 100 KiB limit raised as `RequestError` code `invalid_prompt`. The session log file lives in `tmp` and is returned through `read_back`.

## Invariants & Boundaries
- Preserve: standard library only; never log prompts, argv, or environment values; every exit path kills the process tree and removes temporary files (the base handles this; do not bypass it).
- No `--dangerously-skip-permissions` or other permission flag is ever added by the driver. Add a test asserting it.
- Edit only the files listed below. If a shared helper in `driver.py`, `_args.py`, `_common.py`, `tests/replay.py`, or elsewhere must change, stop and report `BLOCKED` with the exact change needed.
- Do not register the driver in `registry.py`; Phase 9 does. Do not edit README. Do not push, tag, or commit.

## Files
Allowed production and test paths (relative to code root):
- Create: `src/coding_agent_cli/_drivers/agy.py`
- Create: `tests/test_agy_driver.py`

Worker-owned phase records:
- `/home/ngosi/projects/coding-agent-cli/docs/plans/coding-agent-cli-v2/phase-08/notes.md` (append `## Task <M>` blocks)
- `/home/ngosi/projects/coding-agent-cli/docs/plans/coding-agent-cli-v2/phase-08/journal.md` (append `# EXTERNAL RESPONSE` under `## Implementation Response`)

Read-only / Forbidden:
- Do not edit `PLAN.md`, `DESIGN.md`, `FINDINGS.md`, `.handover.md`, `prompt.md`, fixtures, other phases, dependencies, or secrets.

## Tasks
Follow PLAN.md "Phase 8" tasks 1 to 3, test first for each: write the failing test, confirm it fails, then implement.

## Done When
- Parametrized replay passes for every scenario under `tests/fixtures/recorded/agy/`, plus truncation after each `TextDelta` keeps partial text.
- The named tests above exist and pass.
- Every test is bounded: wrap async waits in `asyncio.timeout`; keep timeouts well under 2 s.
- Run from code root, with `export VIRTUAL_ENV=;` first:
  - `uv run ruff format --check`
  - `uv run ruff check`
  - `uv run pyright`
  - `timeout 300 uv run pytest` (Baseline: ruff format/check pass, pyright 0, pytest 197 passed 1 skipped 4 deselected; new tests add to it)

## Rules
Read and follow these bundled contracts:
- Contract: `/home/ngosi/.claude/plugins/cache/refract/refract/3.0.5/shared/worker-contract.md`
- Response: `/home/ngosi/.claude/plugins/cache/refract/refract/3.0.5/shared/erp.md`
- Notes: `/home/ngosi/.claude/plugins/cache/refract/refract/3.0.5/shared/notes-template.md`
- Journal: `/home/ngosi/.claude/plugins/cache/refract/refract/3.0.5/shared/journal-template.md`

Also follow the repository's `AGENTS.md` and `CONTRIBUTING.md` coding standard at the code root.

Role boundary: You are the phase implementer, not Coordinator. Edit code only in the code root; write phase records only under the records root. Leave Coordinator skills and Refract job submission to the Coordinator.

## Response Format
Append the complete `# EXTERNAL RESPONSE` block to `/home/ngosi/projects/coding-agent-cli/docs/plans/coding-agent-cli-v2/phase-08/journal.md`, then return that block and the matching status line from `erp.md`.
