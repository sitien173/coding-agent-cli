# coding-agent-cli v2 Plan

**Design:** [DESIGN.md](DESIGN.md)
**Context key:** `coding-agent-cli-v2`
**Base:** `refs/plans/coding-agent-cli-v2/base` (`24b8ccad055fd506cf34a76e26864f18961e69e5`)
**Commit:** `feat!: rebuild on public CliDriver API with recorded CLI fixtures`

## Outcome & Scope

Deliver the confirmed v2 design: recorded CLI fixtures, the v2 schema and classification, a two-pipe supervised transport, the public `CliDriver` API, the four built-in drivers rebuilt on it, and docs for the 0.2.0 break.

Preserved invariants, for every phase:

- Standard library only at runtime. No new runtime dependency.
- No driver adds a permission, approval, sandbox, or bypass flag.
- Prompts, argv, and environment values are never logged.
- Every exit path kills the process tree and removes temporary files.
- Public signatures match README examples by the end of Phase 9.
- No push, tag, or publish.

Out of scope: Codex rollout reading, a public low-level `Driver` protocol, auth status in `Capabilities`, a 0.1 compatibility layer, live tests as a gate, version bumps and `CHANGELOG.md` edits, which release-please owns.

Intermediate state: between Phase 2 and Phase 9 the default registry has no built-in drivers. Every checkpoint still passes all verification commands.

## Baseline Evidence

Recorded at plan base `24b8cca` with `export VIRTUAL_ENV=;`:

- `uv run ruff format --check`: pass, 63 files already formatted, including this plan's `DESIGN.md`.
- `uv run ruff check`: pass, all checks passed.
- `uv run pyright`: pass, 0 errors, 0 warnings.
- `uv run pytest`: pass, 505 passed, 4 deselected.

Installed CLI versions reported during consultation, to be re-read in Phase 1: claude 2.1.296, codex 0.169.0, pi 1.5.2, agy 1.5.9.

### Phase 1: Recorded and scrubbed CLI fixtures

**Phase Request:** Write a dev-only recorder and scrubber, run them against the four installed CLIs, commit only scrubbed fixtures, add a privacy gate test, and write the answers to the design's open questions. Recording spends tokens; the user approved it on 2026-11-14.
**Goal:** Version-labelled, scrubbed fixtures for `claude`, `codex`, `pi`, and `agy` exist under `tests/fixtures/recorded/`, and every design open question has a recorded answer or an explicit "not settled".
**Depends on:** none
**Invariants & Boundaries:**
- Recording runs in a fresh git repository created with `mktemp -d` that holds only synthetic files. Never run a recording with the project checkout as `cwd`.
- Raw captures go to a mode `700` directory outside the repository and are never committed.
- Permission flags may appear only in the recorder's argv for the throwaway repository. No file under `src/` changes.
- Auth-failure scenarios point the CLI config directory at an empty temporary directory. Never read, copy, or print real credential files.
- Use the cheapest model each CLI offers and the shortest prompts. Claude scenarios pass a small `--max-budget-usd`.
- No change to `src/`.
**Files:**
- Create: `scripts/record_fixtures.py`
- Create: `scripts/scrub_fixtures.py`
- Create: `tests/fixtures/recorded/<cli>/<version>/<scenario>.stdout.jsonl`, `<scenario>.stderr.txt`, and `meta.json` for each CLI
- Create: `tests/fixtures/recorded/FINDINGS.md`
- Create: `tests/test_fixture_privacy.py`
- Modify: `pyproject.toml`, adding `scripts` to the pyright `include` list only
**Tasks:**
1. Write `scripts/scrub_fixtures.py` with tests in `tests/test_fixture_privacy.py` first: a recursive JSON walker that replaces home paths, username, hostname, `cwd`, emails, values under secret-named keys, and known secret prefixes, and maps IDs through one consistent table. Non-JSON stderr text gets the same string replacements.
2. Write `scripts/record_fixtures.py`: per CLI and scenario, run the CLI with the v2 transport argv from DESIGN.md, capture stdout and stderr separately, exit code, `--version`, and argv with the prompt replaced by `<PROMPT>`. Scenarios: plain answer, shell command, file edit, reasoning, invalid model, auth failure, cancel after the first text delta, resume. Claude adds `--max-turns 1` and a tiny `--max-budget-usd`. Agy adds a `--input-format stream-json` stdin attempt.
3. Run the recorder, scrub, write `meta.json` per scenario with expected `outcome`, `code`, and `returncode` per DESIGN.md precedence, and commit only scrubbed files. A scenario a CLI cannot produce is listed in `FINDINGS.md` with the reason.
4. Write `FINDINGS.md`: CLI versions; observed event and item types per CLI; answers for Agy stdin `stream-json` schema, Codex option placement around `resume`, Claude budget subtype name and `--max-turns` acceptance, Pi behavior without `-p`, Pi and Claude reasoning event shapes; and whether Claude fails without `--verbose`.
**Acceptance Criteria:**
- `tests/fixtures/recorded/` holds at least the plain-answer scenario for each of the four CLIs, each with `meta.json`.
- `tests/test_fixture_privacy.py` fails on a planted username, home path, hostname, email, or `sk-` secret, and passes on the committed fixtures.
- `FINDINGS.md` answers each open question in DESIGN.md or marks it "not settled" with the reason.
- `git status --porcelain` after the checkpoint shows no raw capture file.
- `uv run pytest` passes with the new tests added to the 505 baseline.
**Reviewer Checklist:**
- Grep fixtures for the local username, home directory, hostname, any `@` email, `sk-`, `ghp_`, `AIza`, and bearer tokens.
- Confirm no recorder code path writes inside the project checkout except the scrubbed output directory.
- Confirm ID mapping keeps correlations, such as tool start and end sharing one ID.
- Confirm no `src/` file changed.
**Verification Checks:**
- `export VIRTUAL_ENV=; uv run ruff format --check`
- `export VIRTUAL_ENV=; uv run ruff check`
- `export VIRTUAL_ENV=; uv run pyright`
- `export VIRTUAL_ENV=; uv run pytest`

### Phase 2: v2 schema and classification

**Phase Request:** Remove the v1 built-in drivers and their support code, then implement the v2 public value types, events, `ErrorCode`, `Outcome.LIMIT_REACHED`, `Facts`, `RunFacts`, and the 12-row classification precedence from DESIGN.md. Adapt the toy driver and API only as far as needed to stay green.
**Goal:** The v2 schema and `classify(RunFacts, Facts) -> Result` exist and are fully tested; no v1 driver code remains.
**Depends on:** none
**Invariants & Boundaries:**
- `classify` contains no CLI names and no native subtype checks.
- Do not create `_transport.py`, change `_platform.py`, or add `CliDriver`; later phases own them.
- `_process.py` stays in use by the toy driver until Phase 4.
- The default registry returns no built-in drivers; `DriverRegistry(builtins=True)` must not fail.
**Files:**
- Delete: `src/coding_agent_cli/_drivers/claude.py`, `codex.py`, `codex_rollout.py`, `pi.py`, `agy.py`, `_usage.py`
- Delete: `tests/test_claude_driver.py`, `tests/test_codex_driver.py`, `tests/test_codex_rollout.py`, `tests/test_pi_driver.py`, `tests/test_agy_driver.py`, `tests/test_usage.py`, `tests/fixtures/usage/`, and `tests/cli_stub.py` if no remaining test imports it
- Modify: `src/coding_agent_cli/types.py`, `src/coding_agent_cli/events.py`, `src/coding_agent_cli/_classify.py`, `src/coding_agent_cli/registry.py`, `src/coding_agent_cli/api.py`, `src/coding_agent_cli/__init__.py`, `src/coding_agent_cli/_drivers/_common.py`
- Create: `src/coding_agent_cli/driver.py` holding `Facts` and `RequestError` only
- Modify: `tests/toy_driver.py`, `tests/test_types.py`, `tests/test_events.py`, `tests/test_classify.py`, `tests/test_api_contract.py`, `tests/test_stream.py`, `tests/test_registry.py`
**Tasks:**
1. Delete the v1 drivers, rollout, usage counters, their tests, and fixtures. Empty the built-in map in `registry.py`. Remove helpers in `_common.py` orphaned by the deletion.
2. Write failing tests, then implement in `types.py` and `events.py`: `ErrorCode` StrEnum with the exact DESIGN.md codes, `Diagnostic.code: ErrorCode`, `Outcome.LIMIT_REACHED`, `Request.idle_timeout_s` and `raw_events` with validation, new `Result` fields, `Capabilities.version`, `SCHEMA_VERSION = 2`, events without `schema_version`, nullable `entity_id`, and the new `ReasoningDelta`, `FileChanged`, `Notice`, `RawEvent`, plus `ToolCompleted.tool` and `output`.
3. Write failing tests, then implement `Facts` and `RequestError` in `driver.py` and `RunFacts` plus `classify` in `_classify.py`, one test per precedence row 1 to 12, including latched cancel after a clean exit staying `success`, limit beating deadline, and parser error keeping partial text.
4. Adapt `api.py`, `toy_driver.py`, and the contract, stream, and registry tests to the new types so the suite is green.
**Acceptance Criteria:**
- `src/coding_agent_cli/_drivers/` contains only `__init__.py` and `_common.py`.
- `tests/test_classify.py` has a named test for each precedence row 1 to 12.
- `ErrorCode` members equal the DESIGN.md table exactly.
- No event dataclass has a `schema_version` field.
- All verification checks pass.
**Reviewer Checklist:**
- Compare every field name, type, and default against DESIGN.md "Public types" and "Events".
- Confirm `classify` reads only `RunFacts` and `Facts`, and matches keywords only on `Facts.error_text` and the stderr tail.
- Confirm no orphaned helper or import remains in `_common.py`.
**Verification Checks:**
- `export VIRTUAL_ENV=; uv run ruff format --check`
- `export VIRTUAL_ENV=; uv run ruff check`
- `export VIRTUAL_ENV=; uv run pyright`
- `export VIRTUAL_ENV=; uv run pytest`

### Phase 3: Two-pipe supervised transport

**Phase Request:** Build `_transport.py` per DESIGN.md "Transport": separate stdout and stderr readers, a stdin writer, chunked line splitting with `max_line_bytes`, one supervisor for exit, cancel, wall deadline, and idle deadline, a latched stop cause, supervised `on_spawn`, bounded drain, one cleanup deadline, and a `cleanup_incomplete` signal. Add `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` on Windows.
**Goal:** A standalone tested transport that later phases plug into; `_process.py` is untouched.
**Depends on:** none
**Invariants & Boundaries:**
- No polling loop except the existing POSIX process-group exit check.
- The transport returns its own outcome value: stop cause, return code, stderr tail, spawn and stdin failures, parser exception, cleanup status, and `duration_s`. It does not import `_classify`.
- The transport calls a line callback and never parses JSON semantics beyond splitting lines.
- Keep the safe `.cmd` to PowerShell `-File` handling in `_platform.py`.
- Do not modify `_process.py`, drivers, or the API.
**Files:**
- Create: `src/coding_agent_cli/_transport.py`
- Modify: `src/coding_agent_cli/_platform.py`
- Create: `tests/test_transport.py`
- Create: fake CLI scripts under `tests/fixtures/fake_cli/` for both-stream floods, silence then output, oversized lines, and a stalled leader with descendants
**Tasks:**
1. Write failing tests, then implement the readers and stdin writer: both pipes drain to EOF under saturation, a line over `max_line_bytes` is skipped with a `line_too_long` signal and the run continues, stderr tail capped at 64 KiB.
2. Write failing tests, then implement the supervisor: wall timeout, idle timeout reset by bytes on either pipe and paused while the consumer callback is awaited, cancel, latched stop cause, cancel after exit not changing the stop cause, stalled `on_spawn` bounded by the deadline.
3. Write failing tests, then implement cleanup: one total deadline, descendants killed, `cleanup_incomplete` reported when the deadline passes, temporary directory removal hook. Add `KILL_ON_JOB_CLOSE` to the Windows job.
**Acceptance Criteria:**
- Tests cover pipe saturation, oversized line, idle timeout, idle pause during a slow consumer, wall timeout, latched cancel, repeated cancel, stalled `on_spawn`, descendant kill, and cleanup deadline.
- `_transport.py` has no `asyncio.sleep` polling of pipes.
- All verification checks pass on Linux. Windows-specific paths are covered by tests skipped off Windows.
**Reviewer Checklist:**
- Every `await` that can block has a deadline or is cancelled by the supervisor.
- Both pipes are read during cleanup.
- No prompt, argv, or environment value is logged.
- Job Object limit flags and handle lifetime are correct.
**Verification Checks:**
- `export VIRTUAL_ENV=; uv run ruff format --check`
- `export VIRTUAL_ENV=; uv run ruff check`
- `export VIRTUAL_ENV=; uv run pyright`
- `export VIRTUAL_ENV=; uv run pytest`

### Phase 4: Public CliDriver API and entry points

**Phase Request:** Implement `CliDriver`, `Launch`, and `Parser` in `driver.py`, the base `run` and `check` on the Phase 3 transport, text accumulation, `RawEvent` emission, and the new `run`, `stream`, `run_sync`, and `check` signatures. Make the registry accept `CliDriver` only and remove `_process.py` and the public `Driver` protocol. Add a fixture replay helper for driver phases.
**Goal:** An external driver written as a `CliDriver` subclass runs through the full v2 lifecycle.
**Depends on:** Phase 1, Phase 2, Phase 3
**Invariants & Boundaries:**
- `run` and `check` live in the base only.
- The base creates the per-run temporary directory before `launch()` and removes it in `finally`.
- Parser calls go through one dispatcher.
- Do not add built-in drivers; Phases 5 to 8 own them, Phase 9 registers them.
**Files:**
- Modify: `src/coding_agent_cli/driver.py`, `src/coding_agent_cli/api.py`, `src/coding_agent_cli/registry.py`, `src/coding_agent_cli/__init__.py`, `src/coding_agent_cli/_drivers/_common.py`, `src/coding_agent_cli/_args.py`
- Delete: `src/coding_agent_cli/_process.py`, `tests/test_process.py` after moving any still-relevant case to `tests/test_transport.py`
- Modify: `tests/toy_driver.py`, `tests/test_api_contract.py`, `tests/test_stream.py`, `tests/test_registry.py`, `tests/test_types.py`, `tests/test_args.py`, `tests/test_transport.py`
- Create: `tests/replay.py`, `tests/test_driver_base.py`
**Tasks:**
1. Write failing tests, then implement `Launch`, `Parser`, `CliDriver` and the base `run`: validation, executable resolution with the request `PATH`, temporary directory ownership, `RequestError` mapping, dispatcher, `feed_json` versus `feed_text` routing, sequence numbering, `RawEvent` when `raw_events=True`, last-message text accumulation, `read_back`, `finish`, `classify`, one `RunCompleted`, parser exception to `parser_error`.
2. Write failing tests, then implement `check(driver, *, executable, env, refresh, registry)` with `--version` and `--help`, positive-only cache keyed by path and mtime; and `run_sync` with sync `on_event` and `threading.Event` cancel.
3. Make `DriverRegistry` accept `CliDriver` only, remove `_process.py` and the public `Driver` protocol, rewrite the toy driver as a `CliDriver` subclass over the fake CLI, and reduce `_common.py` to shared helpers with one integer helper.
4. Write `tests/replay.py`: load a recorded scenario from `tests/fixtures/recorded/`, feed it to a parser, call `finish` and `classify`, and return events and `Result`; plus a truncation helper that cuts after the n-th `TextDelta`.
**Acceptance Criteria:**
- `tests/test_driver_base.py` runs a `CliDriver` subclass through success, cancel with partial text, timeout, idle timeout, `RequestError`, parser exception, and `raw_events`.
- `coding_agent_cli.__all__` exports `CliDriver`, `Launch`, `Parser`, `Facts`, `RequestError`, `ErrorCode`, and no `Driver`.
- `src/coding_agent_cli/_process.py` does not exist.
- A `run_sync` test cancels from another thread through `threading.Event`.
- All verification checks pass.
**Reviewer Checklist:**
- Temporary directory removed on every exit path, including `RequestError` from `launch()` and exceptions from `on_event`.
- `stream()` `aclose()` still kills the tree.
- Text accumulation resets at each `MessageStarted` and prefers `Facts.final_text`.
- No logging of prompts, argv, or environment values.
**Verification Checks:**
- `export VIRTUAL_ENV=; uv run ruff format --check`
- `export VIRTUAL_ENV=; uv run ruff check`
- `export VIRTUAL_ENV=; uv run pyright`
- `export VIRTUAL_ENV=; uv run pytest`

### Phase 5: Claude driver

**Phase Request:** Implement `ClaudeDriver` and its parser on the v2 API per DESIGN.md "Drivers", Claude section, using the Phase 1 fixtures and findings.
**Goal:** Every recorded Claude scenario replays to the outcome and code in its `meta.json`, and v1 defects 1, 4, 5, and 6 have tests that fail on the v1 behavior.
**Depends on:** Phase 4
**Invariants & Boundaries:**
- Edit only the files listed. If a shared helper is needed, stop with `BLOCKED`.
- Parser branches only for shapes present in fixtures or official docs.
- Do not register the driver in `registry.py`; Phase 9 does.
**Files:**
- Create: `src/coding_agent_cli/_drivers/claude.py`
- Create: `tests/test_claude_driver.py`
**Tasks:**
1. Launch: transport argv with `--verbose`, prompt through stdin, portable flags, owned-flag and conflict rejection. Tests assert no permission flag is added.
2. Parser: messages, text and reasoning deltas without duplication, tool input from the `assistant` `tool_use` block, tool results with output, `Bash` as `command`, inferred `FileChanged`, usage snapshots.
3. Facts: cost, turns, stop reason, session, model, context window, `max_turns` and `budget` mapping, `api_error_status` mapping. Replay every recorded Claude scenario, plus truncation after each `TextDelta`.
**Acceptance Criteria:**
- Parametrized replay passes for every scenario under `tests/fixtures/recorded/claude/`.
- Named tests exist for `--verbose` in argv, `Bash` as command, partial text on truncation, and `error_max_turns` as `limit_reached`.
- All verification checks pass.
**Reviewer Checklist:**
- Compare each parser branch against a fixture line or cited doc.
- Check text deduplication between `assistant` and `stream_event` records.
**Verification Checks:**
- `export VIRTUAL_ENV=; uv run ruff format --check`
- `export VIRTUAL_ENV=; uv run ruff check`
- `export VIRTUAL_ENV=; uv run pyright`
- `export VIRTUAL_ENV=; uv run pytest`

### Phase 6: Codex driver

**Phase Request:** Implement `CodexDriver` and its parser on the v2 API per DESIGN.md "Drivers", Codex section, using the Phase 1 fixtures and findings.
**Goal:** Every recorded Codex scenario replays to the outcome and code in its `meta.json`, and v1 defects 2 and 3 have tests that fail on the v1 behavior.
**Depends on:** Phase 4
**Invariants & Boundaries:**
- Edit only the files listed. If a shared helper is needed, stop with `BLOCKED`.
- No `-o` temporary file, no rollout reading, no session-ID regex over model text.
- Do not register the driver in `registry.py`; Phase 9 does.
**Files:**
- Create: `src/coding_agent_cli/_drivers/codex.py`
- Create: `tests/test_codex_driver.py`
**Tasks:**
1. Launch: `exec --json` argv, resume placement per `FINDINGS.md`, portable flags, `reasoning` conflict limited to `-c model_reasoning_effort=...`, `--cd` and `-C` rejection. Tests assert no permission flag is added.
2. Parser: the item-type table in DESIGN.md, `thread.started` session, `turn.completed` usage, `turn.failed` and `error` to `Facts.error`, `error` items to `Notice`.
3. Replay every recorded Codex scenario, plus truncation after each `TextDelta`.
**Acceptance Criteria:**
- Parametrized replay passes for every scenario under `tests/fixtures/recorded/codex/`.
- Named tests exist for `command_execution` producing `ToolStarted` with activity `command`, `file_change` producing `FileChanged`, and `turn.failed` auth producing `agent_fatal`.
- `Result.model` is `None` for Codex.
- All verification checks pass.
**Reviewer Checklist:**
- Compare each parser branch against a fixture line or cited doc.
- Confirm unrelated `-c` args are accepted with `reasoning` set.
**Verification Checks:**
- `export VIRTUAL_ENV=; uv run ruff format --check`
- `export VIRTUAL_ENV=; uv run ruff check`
- `export VIRTUAL_ENV=; uv run pyright`
- `export VIRTUAL_ENV=; uv run pytest`

### Phase 7: Pi driver

**Phase Request:** Implement `PiDriver` and its parser on the v2 API per DESIGN.md "Drivers", Pi section, using the Phase 1 fixtures and findings.
**Goal:** Every recorded Pi scenario replays to the outcome and code in its `meta.json`, and a recovered CLI retry no longer leaves a stale error.
**Depends on:** Phase 4
**Invariants & Boundaries:**
- Edit only the files listed. If a shared helper is needed, stop with `BLOCKED`.
- No `--approve` or `--no-approve` added.
- Do not register the driver in `registry.py`; Phase 9 does.
**Files:**
- Create: `src/coding_agent_cli/_drivers/pi.py`
- Create: `tests/test_pi_driver.py`
**Tasks:**
1. Launch: argv per `FINDINGS.md` on `-p`, portable flags, owned-flag rejection. Tests assert no permission flag is added.
2. Parser: `message_update` text and reasoning deltas, tool execution start and end with output, cost sum, `stopReason`, `turn_end` count, stale error cleared by a later successful message.
3. Replay every recorded Pi scenario, plus truncation after each `TextDelta`.
**Acceptance Criteria:**
- Parametrized replay passes for every scenario under `tests/fixtures/recorded/pi/`.
- A named test shows an error followed by a successful message yields `success`.
- All verification checks pass.
**Reviewer Checklist:**
- Compare each parser branch against a fixture line or cited doc.
- Confirm messages without native IDs get `entity_id=None`.
**Verification Checks:**
- `export VIRTUAL_ENV=; uv run ruff format --check`
- `export VIRTUAL_ENV=; uv run ruff check`
- `export VIRTUAL_ENV=; uv run pyright`
- `export VIRTUAL_ENV=; uv run pytest`

### Phase 8: Agy driver

**Phase Request:** Implement `AgyDriver` and its parser on the v2 API per DESIGN.md "Drivers", Agy section, using the Phase 1 fixtures and findings.
**Goal:** Every recorded Agy scenario replays to the outcome and code in its `meta.json`; tool errors and step correlation are correct.
**Depends on:** Phase 4
**Invariants & Boundaries:**
- Edit only the files listed. If a shared helper is needed, stop with `BLOCKED`.
- Prompt delivery through stdin only if `FINDINGS.md` confirms `--input-format stream-json`; otherwise argv with the 100 KiB limit raised as `RequestError` code `invalid_prompt`.
- No `--dangerously-skip-permissions` added.
- Do not register the driver in `registry.py`; Phase 9 does.
**Files:**
- Create: `src/coding_agent_cli/_drivers/agy.py`
- Create: `tests/test_agy_driver.py`
**Tasks:**
1. Launch: prompt delivery per `FINDINGS.md`, session log file in `tmp` returned through `read_back`, portable flags. Tests assert no permission flag is added.
2. Parser: structured events, plain-text fallback through `feed_text`, tool correlation by step index, tool error status, structured `status`, usage and context window.
3. Replay every recorded Agy scenario, plus truncation after each `TextDelta`.
**Acceptance Criteria:**
- Parametrized replay passes for every scenario under `tests/fixtures/recorded/agy/`.
- Named tests exist for two overlapping tools and a failed tool producing status `error`.
- All verification checks pass.
**Reviewer Checklist:**
- Compare each parser branch against a fixture line or cited doc.
- Confirm the prompt never appears in argv when stdin delivery is used.
**Verification Checks:**
- `export VIRTUAL_ENV=; uv run ruff format --check`
- `export VIRTUAL_ENV=; uv run ruff check`
- `export VIRTUAL_ENV=; uv run pyright`
- `export VIRTUAL_ENV=; uv run pytest`

### Phase 9: Registration, docs, and release readiness

**Phase Request:** Register the four drivers as built-ins, update live tests to the v2 API, rewrite README for v2 with "Migrating from 0.1" and corrected Known limits, and update the AGENTS.md layout table and CONTRIBUTING.md references to removed modules.
**Goal:** `run(Request(driver="claude", ...))` resolves a built-in driver, and the docs match the code.
**Depends on:** Phase 5, Phase 6, Phase 7, Phase 8
**Invariants & Boundaries:**
- Do not edit `CHANGELOG.md`, the `version` in `pyproject.toml`, `__version__`, or `.release-please-manifest.json`.
- Do not run `pytest -m live`.
**Files:**
- Modify: `src/coding_agent_cli/registry.py`
- Modify: `tests/test_registry.py`, `tests/test_live.py`, `tests/test_packaging.py`
- Create: `tests/test_readme.py`
- Modify: `README.md`, `AGENTS.md`, `CONTRIBUTING.md`
**Tasks:**
1. Register `claude`, `codex`, `pi`, and `agy` as built-ins with tests for `names()` and default-registry resolution.
2. Update `tests/test_live.py` to the v2 API and `tests/test_packaging.py` to assert `scripts/` is not in the wheel.
3. Rewrite README: v2 API, events table, outcomes and `ErrorCode`, `CliDriver` authoring example, per-CLI permission flags callers pass through `args`, "Migrating from 0.1" listing every renamed, removed, and added public name, and Known limits with the Job Object assignment window. Update AGENTS.md and CONTRIBUTING.md module references.
**Acceptance Criteria:**
- `DriverRegistry().names()` returns `agy`, `claude`, `codex`, `pi` in a test.
- Every Python code block in README imports only names in `coding_agent_cli.__all__`, checked by a test that extracts and compiles the blocks.
- README contains a "Migrating from 0.1" heading and no reference to `codex_rollout`, `_process`, or the public `Driver` protocol.
- All verification checks pass.
**Reviewer Checklist:**
- README signatures match `api.py` and `driver.py` exactly.
- Migration section covers every `__all__` difference between `refs/plans/coding-agent-cli-v2/base` and HEAD.
**Verification Checks:**
- `export VIRTUAL_ENV=; uv run ruff format --check`
- `export VIRTUAL_ENV=; uv run ruff check`
- `export VIRTUAL_ENV=; uv run pyright`
- `export VIRTUAL_ENV=; uv run pytest`
