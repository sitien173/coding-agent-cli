# Phase 4: Public CliDriver API and entry points

## Goal & Outcome
- Plan Goal: Rebuild `coding-agent-cli` as 0.2.0 on a public `CliDriver` API. Full design: `/home/ngosi/projects/coding-agent-cli/docs/plans/coding-agent-cli-v2/DESIGN.md`. Full plan: `/home/ngosi/projects/coding-agent-cli/docs/plans/coding-agent-cli-v2/PLAN.md`, section "Phase 4".
- Phase Outcome: An external driver written as a `CliDriver` subclass runs through the full v2 lifecycle on the Phase 3 transport. The public low-level `Driver` protocol and `_process.py` are gone. A fixture replay helper exists for Phases 5 to 8.

## Context & Architecture
- Code root: `/home/ngosi/projects/coding-agent-cli` (main checkout, branch `main`) | Records root: same | Base: `refs/plans/coding-agent-cli-v2/phase-04/base`
- Prior State at base: Phases 1 to 3 are merged. Checks: `ruff format --check` pass, `ruff check` pass, `pyright` 0 errors, `pytest` 198 passed 1 skipped 4 deselected. The default registry has no built-in drivers; do not add any.
- Code Context, read first:
  - `src/coding_agent_cli/_transport.py`: `run_transport(argv, on_line, *, stdin, env, cwd, max_line_bytes, timeout_s, idle_timeout_s, cancel, on_spawn, cleanup_hook, cleanup_timeout_s, eof_timeout_s) -> TransportOutcome`, and `ParserFailure`. The transport dispatches lines serially from one dispatcher; wrap parser exceptions in `ParserFailure` inside your `on_line` so the transport returns them instead of raising. Caller `on_event`/`on_spawn` exceptions must stay unwrapped so they re-raise after cleanup.
  - `src/coding_agent_cli/_classify.py`: `RunFacts` and `classify(run, facts, *, text)`. The keyword-only `text` argument is an accepted Coordinator decision: the base driver owns accumulated text.
  - `src/coding_agent_cli/driver.py`: `Facts`, `RequestError`.
  - `src/coding_agent_cli/_drivers/_common.py`: transitional `run_facts(ProcessOutcome)` and `deadline_for`; replace with a mapping from `TransportOutcome` to `RunFacts`, and keep one integer helper for drivers.
  - `src/coding_agent_cli/types.py`, `events.py`: v2 schema from Phase 2. `api.py`, `registry.py`, `__init__.py`, `_args.py`: still v1-shaped entry points over `_process.py`.
  - `tests/fixtures/recorded/<cli>/<version>/<scenario>.{stdout,stderr}.jsonl` plus `meta.json`; `docs/plans/coding-agent-cli-v2/FINDINGS.md`.

## Required design
Implement exactly DESIGN.md sections "Driver API", "Data flow for `run`", "Entry points", "Public types", "Events", and "Raised, not returned". Key points:
- `Launch(argv, stdin=None, read_back=())`, `Parser` protocol with `feed_json(obj)`, `feed_text(line, *, stream)`, `finish(read_back) -> Facts`; `CliDriver` with `name`, `executable`, `max_line_bytes` (default 16 MiB), abstract `launch(req, exe, tmp)` and `parser(req)`; `run` and `check` implemented in the base only.
- Base `run`: validate request with `_args.py`; resolve the executable with the request `PATH`; create the per-run temp directory before `launch()` and remove it on every exit path (use the transport `cleanup_hook` and a `finally` for paths before spawn); `RequestError` from `launch()` maps to `request_fatal`; stdout lines that parse as a JSON object go to `feed_json`, all other stdout lines and every stderr line go to `feed_text`; number every event with `sequence`; when `req.raw_events` is true emit `RawEvent` before the normalized events from the same line; accumulate `TextDelta` text of the current message, reset at each `MessageStarted`, and use `Facts.final_text` when present, else the last message's accumulated text, so partial text survives cancel and timeout; read `read_back` files after exit and before temp removal; call `finish`, then `classify`; emit exactly one `RunCompleted` last; a parser exception yields `agent_fatal` with `parser_error` and keeps partial text.
- `run(req, *, on_event=None, cancel=None, on_spawn=None, registry=None)`, `stream(req, *, cancel=None, on_spawn=None, registry=None)` whose `aclose()` kills the tree, `run_sync(req, *, on_event=None, cancel: threading.Event | None = None, registry=None)`, `check(driver: str, *, executable=None, env=None, refresh=False, registry=None) -> Capabilities` running `--version` and `--help`, cached by resolved path and mtime only on success.
- `DriverRegistry` accepts `CliDriver` instances only. `__all__` exports `CliDriver`, `Launch`, `Parser`, `Facts`, `RequestError`, `ErrorCode`, and no `Driver`.

## Invariants & Boundaries
- Preserve: standard library only; never log prompts, argv, or environment values; every exit path kills the process tree and removes the temp directory; drivers never add permission, approval, sandbox, or bypass flags.
- Parser calls go only through the transport's single dispatcher.
- Out of Scope: no built-in drivers (Phases 5 to 8 own them, Phase 9 registers them); do not change `_transport.py` behavior except a minimal fix with a test if integration exposes a defect; do not edit README (Phase 9 owns docs). Do not push, tag, or commit.

## Files
Allowed production and test paths:
- Modify: `src/coding_agent_cli/driver.py`, `api.py`, `registry.py`, `__init__.py`, `_drivers/_common.py`, `_args.py`
- Delete: `src/coding_agent_cli/_process.py`, `tests/test_process.py` after moving any still-relevant case to `tests/test_transport.py`
- Modify: `tests/toy_driver.py` (rewrite as a `CliDriver` subclass over a fake CLI), `tests/test_api_contract.py`, `tests/test_stream.py`, `tests/test_registry.py`, `tests/test_types.py`, `tests/test_args.py`, `tests/test_transport.py`, `tests/test_packaging.py` if it lists modules
- Create: `tests/replay.py`, `tests/test_driver_base.py`, new fake CLIs under `tests/fixtures/fake_cli/` if needed

Worker-owned phase records:
- `/home/ngosi/projects/coding-agent-cli/docs/plans/coding-agent-cli-v2/phase-04/notes.md` (append `## Task <M>` blocks)
- `/home/ngosi/projects/coding-agent-cli/docs/plans/coding-agent-cli-v2/phase-04/journal.md` (append `# EXTERNAL RESPONSE` under `## Implementation Response`)

Read-only / Forbidden:
- Do not edit `PLAN.md`, `DESIGN.md`, `.handover.md`, `prompt.md`, other phases, dependencies, or secrets.

## Tasks
- task-1: Failing tests first, then `Launch`, `Parser`, `CliDriver`, and the base `run` as described above.
- task-2: Failing tests first, then `check` with the positive-only cache, and `run_sync` with a sync `on_event` and a `threading.Event` cancel set from another thread.
- task-3: Registry accepts `CliDriver` only; remove `_process.py` and the public `Driver` protocol; rewrite the toy driver; reduce `_common.py` to shared helpers with one integer helper.
- task-4: `tests/replay.py`: load a recorded scenario, feed stdout lines (JSON to `feed_json`, others to `feed_text`) and stderr lines to `feed_text` of a given parser, call `finish` and `classify` with synthetic `RunFacts` built from `meta.json`, and return events and `Result`; plus a truncation helper that cuts the stream after the n-th `TextDelta`. Test it with a minimal test-only parser.

## Done When
- `tests/test_driver_base.py` runs a `CliDriver` subclass through success, cancel with partial text, timeout, idle timeout, `RequestError`, parser exception, and `raw_events`, and checks temp-directory removal on each path including `RequestError` from `launch()` and an exception from `on_event`.
- `stream()` `aclose()` kills the tree (test).
- `coding_agent_cli.__all__` matches the export list; `src/coding_agent_cli/_process.py` does not exist.
- Every test is bounded: wrap waits in `asyncio.timeout`, keep timeouts well under 2 s.
- Run from code root, with `export VIRTUAL_ENV=;` first:
  - `uv run ruff format --check` (Baseline: pass)
  - `uv run ruff check` (Baseline: pass)
  - `uv run pyright` (Baseline: 0 errors)
  - `timeout 300 uv run pytest` (Baseline: 198 passed, 1 skipped, 4 deselected; counts change as v1 tests are removed and new ones added)

## Rules
Read and follow these bundled contracts:
- Contract: `/home/ngosi/.claude/plugins/cache/refract/refract/3.0.5/shared/worker-contract.md`
- Response: `/home/ngosi/.claude/plugins/cache/refract/refract/3.0.5/shared/erp.md`
- Notes: `/home/ngosi/.claude/plugins/cache/refract/refract/3.0.5/shared/notes-template.md`
- Journal: `/home/ngosi/.claude/plugins/cache/refract/refract/3.0.5/shared/journal-template.md`

Also follow the repository's `AGENTS.md` and `CONTRIBUTING.md` coding standard at the code root.

Role boundary: You are the phase implementer, not Coordinator. Edit code only in the code root; write phase records only under the records root. Leave Coordinator skills and Refract job submission to the Coordinator.

## Response Format
Append the complete `# EXTERNAL RESPONSE` block to `/home/ngosi/projects/coding-agent-cli/docs/plans/coding-agent-cli-v2/phase-04/journal.md`, then return that block and the matching status line from `erp.md`.
