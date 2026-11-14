# Phase 2: v2 schema and classification

## Goal & Outcome
- Plan Goal: Rebuild `coding-agent-cli` as 0.2.0 on a public `CliDriver` API. Full design: `/home/ngosi/projects/coding-agent-cli/docs/plans/coding-agent-cli-v2/DESIGN.md`. Full plan: `/home/ngosi/projects/coding-agent-cli/docs/plans/coding-agent-cli-v2/PLAN.md`, section "Phase 2".
- Phase Outcome: The v2 public value types, events, `ErrorCode`, `Outcome.LIMIT_REACHED`, `Facts`, `RequestError`, `RunFacts`, and `classify(RunFacts, Facts) -> Result` exist and are fully tested; no v1 built-in driver code remains; the suite is green.

## Context & Architecture
- Code root: `/home/ngosi/projects/coding-agent-cli/.worktrees/coding-agent-cli-v2-phase-02` | Records root: `/home/ngosi/projects/coding-agent-cli` | Branch: `refract/coding-agent-cli-v2/phase-02` | Base: `refs/plans/coding-agent-cli-v2/phase-02/base`
- Prior State: v1 library at plan base `24b8cca`. Baseline: `ruff format --check` pass, `ruff check` pass, `pyright` 0 errors, `pytest` 505 passed 4 deselected. Phase 1 (fixtures, `scripts/`) and Phase 3 (`_transport.py`, `_platform.py`, `tests/test_transport.py`, new fake CLIs) run in parallel in other worktrees; do not touch their files.
- Code Context: read `src/coding_agent_cli/{types,events,_classify,registry,api,__init__,_args}.py`, `src/coding_agent_cli/_drivers/_common.py`, `src/coding_agent_cli/_process.py`, `tests/toy_driver.py`, and the tests named below. v1 `classify` lives in `_classify.py` and keyword-matches diagnostics; v2 replaces it with `classify(RunFacts, Facts)`. The toy driver in `tests/toy_driver.py` drives `tests/fixtures/fake_cli/fake_cli.py` through `_process.py`; keep it working on the new types.
- Design sections to implement exactly: DESIGN.md "Public types", "Events", "Driver API" (only the `Facts` dataclass and `RequestError`), "`ErrorCode`", "Outcome precedence", "Raised, not returned".

## Invariants & Boundaries
- Preserve: standard library only; all value types `@dataclass(frozen=True, slots=True, kw_only=True)`; `Request` keeps every v1 field and validation; `DriverRegistry` `register(name, driver, *, replace=False)`, `get(name)` raising `KeyError`, `names()`; `run`, `stream`, `run_sync`, `check` keep working for the toy driver (their v2 signatures are Phase 4 work; only adapt them to the new types now). No prompt, argv, or environment value is logged.
- `classify` contains no CLI names and no native subtype checks; it reads only `RunFacts` and `Facts`, checks `Facts.error` first, and keyword-matches only on `Facts.error_text` and the stderr tail.
- The default registry returns no built-in drivers; `DriverRegistry(builtins=True)` (or whatever v1 constructor flag exists) must not fail.
- Out of Scope: do not create `_transport.py`, do not change `_platform.py` or `_process.py` beyond the minimum type adaptation needed to stay green, do not add `CliDriver`, `Launch`, or `Parser` (Phase 4). Do not touch `scripts/`, `tests/fixtures/recorded/`, `tests/test_transport.py`, `tests/test_process.py` except for type adaptation, or `pyproject.toml`. Do not push, tag, or commit.

## Files
Allowed production and test paths (relative to code root):
- Delete: `src/coding_agent_cli/_drivers/claude.py`, `codex.py`, `codex_rollout.py`, `pi.py`, `agy.py`, `_usage.py`
- Delete: `tests/test_claude_driver.py`, `tests/test_codex_driver.py`, `tests/test_codex_rollout.py`, `tests/test_pi_driver.py`, `tests/test_agy_driver.py`, `tests/test_usage.py`, `tests/fixtures/usage/`, and `tests/cli_stub.py` if no remaining test imports it
- Modify: `src/coding_agent_cli/types.py`, `src/coding_agent_cli/events.py`, `src/coding_agent_cli/_classify.py`, `src/coding_agent_cli/registry.py`, `src/coding_agent_cli/api.py`, `src/coding_agent_cli/__init__.py`, `src/coding_agent_cli/_drivers/_common.py`
- Modify only if needed to stay green after type changes: `src/coding_agent_cli/_process.py`, `tests/test_process.py`, `tests/test_args.py`, `tests/test_packaging.py`, `tests/test_live.py` (mark live tests skipped with a reason pointing at Phase 9 if they import deleted drivers)
- Create: `src/coding_agent_cli/driver.py` holding `Facts` and `RequestError` only
- Modify: `tests/toy_driver.py`, `tests/test_types.py`, `tests/test_events.py`, `tests/test_classify.py`, `tests/test_api_contract.py`, `tests/test_stream.py`, `tests/test_registry.py`

Worker-owned phase records:
- `/home/ngosi/projects/coding-agent-cli/docs/plans/coding-agent-cli-v2/phase-02/notes.md` (append `## Task <M>` blocks)
- `/home/ngosi/projects/coding-agent-cli/docs/plans/coding-agent-cli-v2/phase-02/journal.md` (append `# EXTERNAL RESPONSE` under `## Implementation Response`)

Read-only / Forbidden:
- Do not edit `PLAN.md`, `DESIGN.md`, `.handover.md`, `prompt.md`, other phases, dependencies, or secrets.

## Tasks
- task-1: Delete the v1 drivers, rollout, usage counters, their tests, and fixtures listed above. Empty the built-in map in `registry.py`. Remove helpers in `_drivers/_common.py` orphaned by the deletion, including the shared command allow-list. Keep one integer helper only if something still uses it.
- task-2: Failing tests first, then implement in `types.py` and `events.py`:
  - `ErrorCode(StrEnum)` with exactly these values: `bad_cwd`, `invalid_args`, `unsupported_option`, `invalid_prompt`, `missing_cli`, `spawn_failed`, `auth`, `invalid_model`, `context_overflow`, `service_error`, `max_turns`, `budget`, `cancelled`, `timeout`, `idle_timeout`, `stdin_failed`, `execution_error`, `parser_error`, `no_output`, `no_session_id`, `line_too_long`, `cleanup_incomplete`.
  - `Diagnostic(code: ErrorCode, message: str)`.
  - `Outcome` values `success`, `retryable`, `limit_reached`, `agent_fatal`, `request_fatal`, `cancelled`.
  - `Request` adds `idle_timeout_s: float | None = None` (same validation as `timeout_s`: `None` or finite and positive) and `raw_events: bool = False`.
  - `Usage(input, output, cache_read, cache_write, context_used, context_window)`, each `int | None = None`.
  - `Result(outcome, text, session_id, model, usage, cost_usd: float | None, duration_s: float, num_turns: int | None, stop_reason: str | None, returncode, error: Diagnostic | None, warnings: tuple[Diagnostic, ...])`.
  - `Capabilities(available: bool, executable: Path | None, version: str | None, structured_streaming: bool | None)`.
  - `events.py`: module constant `SCHEMA_VERSION = 2`; no event carries `schema_version`; every event has `sequence: int` and `entity_id: str | None`; variants and `kind` strings exactly as DESIGN.md "Events": `SessionIdentified`, `MessageStarted`, `TextDelta`, `ReasoningDelta`, `MessageCompleted`, `ToolStarted(tool, activity: Literal["command","tool_call"], input)`, `ToolCompleted(tool, status: Literal["completed","error","unknown"], output: JsonValue | None)`, `FileChanged(path, change: Literal["added","modified","deleted","unknown"], evidence: Literal["reported","inferred"])`, `Notice(code: str, message: str)`, `UsageUpdated(usage)`, `RawEvent(stream: Literal["stdout","stderr"], data: dict[str, Any])`, `RunCompleted(result)`. Remove `EventBuffer` if it exists and nothing outside v1 drivers needs it; otherwise note why it stays.
- task-3: Failing tests first, then implement `Facts` (fields exactly as DESIGN.md "Driver API") and `RequestError(code: ErrorCode, message: str)` (an `Exception` subclass) in `driver.py`, and `RunFacts` plus `classify(run: RunFacts, facts: Facts, *, text: str) -> Result` (or an equivalent signature that keeps text accumulation outside classify; document the choice in notes) in `_classify.py`. `RunFacts` carries: latched stop cause (`exited`, `cancelled`, `timeout`, `idle_timeout`), `returncode`, stderr tail, request error, spawn failure (`missing_cli` or `spawn_failed`), stdin failure, parser exception message, cleanup-incomplete flag, `line_too_long` flag, and `duration_s`. Implement DESIGN.md rows 1 to 12 in order, with one named test per row (`test_row01_cancelled` ... `test_row12_success`), plus: latched `exited` with a later cancel stays `success`; a `max_turns` limit with a `timeout` stop cause yields `limit_reached`; a parser error keeps partial text in `Result.text`; `no_session_id`, `line_too_long`, and `cleanup_incomplete` appear in `warnings`. Keyword matching for auth, invalid model, context overflow, and service errors runs on `Facts.error_text` and the stderr tail only when `Facts.error` is absent.
- task-4: Adapt `api.py`, `tests/toy_driver.py`, and the contract, stream, and registry tests to the new types so the suite is green. Keep the current `run`/`stream`/`run_sync`/`check` signatures except where a type change forces an edit; Phase 4 rewrites them.

## Done When
- `src/coding_agent_cli/_drivers/` contains only `__init__.py` and `_common.py`.
- `tests/test_classify.py` has a named test for each precedence row 1 to 12.
- `ErrorCode` members equal the list above exactly.
- No event dataclass has a `schema_version` field.
- Run from code root, with `export VIRTUAL_ENV=;` first:
  - `uv run ruff format --check` (Baseline: pass)
  - `uv run ruff check` (Baseline: pass)
  - `uv run pyright` (Baseline: 0 errors)
  - `uv run pytest` (Baseline: 505 passed, 4 deselected; the count will drop with deleted driver tests and rise with new ones; report both)

## Rules
Read and follow these bundled contracts:
- Contract: `/home/ngosi/.claude/plugins/cache/refract/refract/3.0.5/shared/worker-contract.md`
- Response: `/home/ngosi/.claude/plugins/cache/refract/refract/3.0.5/shared/erp.md`
- Notes: `/home/ngosi/.claude/plugins/cache/refract/refract/3.0.5/shared/notes-template.md`
- Journal: `/home/ngosi/.claude/plugins/cache/refract/refract/3.0.5/shared/journal-template.md`

Also follow the repository's `AGENTS.md` and `CONTRIBUTING.md` coding standard at the code root.

Role boundary: You are the phase implementer, not Coordinator. Edit code only in the code root; write phase records only under the records root. Leave Coordinator skills and Refract job submission to the Coordinator.

## Response Format
Append the complete `# EXTERNAL RESPONSE` block to `/home/ngosi/projects/coding-agent-cli/docs/plans/coding-agent-cli-v2/phase-02/journal.md`, then return that block and the matching status line from `erp.md`.
