# Phase 9: Registration, docs, and release readiness

## Goal & Outcome
- Plan Goal: Rebuild `coding-agent-cli` as 0.2.0 on a public `CliDriver` API. Full design: `/home/ngosi/projects/coding-agent-cli/docs/plans/coding-agent-cli-v2/DESIGN.md`. Full plan: `/home/ngosi/projects/coding-agent-cli/docs/plans/coding-agent-cli-v2/PLAN.md`, section "Phase 9".
- Phase Outcome: `run(Request(driver="claude", ...))` resolves a built-in driver, and README, AGENTS.md, and CONTRIBUTING.md match the code.

## Context & Architecture
- Code root and records root: `/home/ngosi/projects/coding-agent-cli` | Branch: `main` | Base: `refs/plans/coding-agent-cli-v2/phase-09/base`
- Prior State: Phases 1 to 8 are merged. The four drivers live in `src/coding_agent_cli/_drivers/{claude,codex,pi,agy}.py` but are not registered. Checks at base: ruff format/check pass, pyright 0, pytest 369 passed 1 skipped 4 deselected.
- Code Context, read first: `src/coding_agent_cli/__init__.py` (`__all__`), `api.py`, `driver.py`, `registry.py`, `types.py`, `events.py`, the four drivers, `tests/test_registry.py`, `tests/test_live.py`, `tests/test_packaging.py`, `README.md`, `AGENTS.md`, `CONTRIBUTING.md`, `docs/plans/coding-agent-cli-v2/FINDINGS.md`, and `.handover.md` section `debt`. The v1 public API is visible with `git show 24b8cca:src/coding_agent_cli/__init__.py` and `git show 24b8cca:README.md`.

## Required design
Implement PLAN.md "Phase 9" tasks 1 to 3 and its acceptance criteria:
1. Register `claude`, `codex`, `pi`, `agy` as built-ins. Tests: `DriverRegistry().names()` returns `["agy", "claude", "codex", "pi"]` (or the sorted equivalent of the existing return type), and the default registry resolves each name to the right driver class.
2. Update `tests/test_live.py` to the v2 API (keep the `live` marker; do not run it). `tests/test_packaging.py` asserts `scripts/` is not in the built wheel.
3. Rewrite README for v2: install, Quick start, `run`, `stream`, `run_sync` (with `threading.Event` cancel), `check`, `Request` and `Result` fields, events table, `Outcome` values including `limit_reached`, `ErrorCode` list, retry guidance, authoring a custom driver by subclassing `CliDriver` with `Launch` and `Parser`, per-CLI permission flags that callers pass through `Request.args` (the library never adds them), "Migrating from 0.1" listing every renamed, removed, and added public name (diff `__all__` between `24b8cca` and HEAD, plus behavior changes: `Result.text` is the last assistant message, `check()` signature, `schema_version` moved to module constant `SCHEMA_VERSION`, `entity_id` native or None, Codex model is None, codex rollout removed), and Known limits: Windows Job Object assignment window, Agy prompt in argv with 100 KiB limit, Agy mixed structured and plain stdout unsupported, idle timeout must exceed the longest tool command. Signatures in README must match `api.py` and `driver.py` exactly.
4. Create `tests/test_readme.py`: extract every fenced `python` block from README, compile each, and assert every `from coding_agent_cli import ...` name is in `coding_agent_cli.__all__`; assert a "Migrating from 0.1" heading exists and README has no `codex_rollout`, `_process`, or public `Driver` protocol reference. Remember ruff formats Python blocks in Markdown: run `uv run ruff format` after editing README.
5. Update AGENTS.md layout table and CONTRIBUTING.md module references to the v2 modules (`driver.py`, `_transport.py`, `_classify.py`, `_drivers/`, `tests/fixtures/recorded/`, `tests/replay.py`, `scripts/record_fixtures.py`). Keep AGENTS.md hard rules.

## Invariants & Boundaries
- Do not edit `CHANGELOG.md`, the `version` in `pyproject.toml`, `__version__`, or `.release-please-manifest.json`.
- Do not run `pytest -m live`. Standard library only. Do not push, tag, or commit.
- Edit only the files listed below. If production code other than `registry.py` must change, stop and report `BLOCKED` with the exact change.
- Wrap pytest in `timeout 300`.

## Files
- Modify: `src/coding_agent_cli/registry.py`, `tests/test_registry.py`, `tests/test_live.py`, `tests/test_packaging.py`, `README.md`, `AGENTS.md`, `CONTRIBUTING.md`
- Create: `tests/test_readme.py`
- Worker-owned records: `docs/plans/coding-agent-cli-v2/phase-09/notes.md` (append `## Task N` blocks), `docs/plans/coding-agent-cli-v2/phase-09/journal.md` (append `# EXTERNAL RESPONSE` under `## Implementation Response`)

## Verification
- `export VIRTUAL_ENV=; uv run ruff format --check`
- `export VIRTUAL_ENV=; uv run ruff check`
- `export VIRTUAL_ENV=; uv run pyright`
- `export VIRTUAL_ENV=; timeout 300 uv run pytest -q`

Report `TASK_COMPLETE` with check output tails, or `BLOCKED` with the reason.
