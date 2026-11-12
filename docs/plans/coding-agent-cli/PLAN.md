# coding-agent-cli Plan

**Design:** [DESIGN.md](DESIGN.md)
**Context key:** `coding-agent-cli`
**Base:** `refs/plans/coding-agent-cli/base` (`e309ab7`)
**Commit:** `feat: add coding-agent-cli 0.1.0 library for coding-agent CLIs`

## Outcome & Scope

Build `coding-agent-cli` 0.1.0, imported as `coding_agent_cli`, in this repository: a standard-library-only Python 3.12+ library that runs a prompt through an external coding-agent CLI, streams typed events, and returns a classified `Result`. Built-in drivers: `claude`, `codex`, `pi`, `agy`. Consumers can register custom drivers.

Source to port: Refract commit `99d155bd60eda0072853188dd2bab288a9f028d8`. Read any source file with:

```text
git -C /home/ngosi/projects/refract show 99d155bd60eda0072853188dd2bab288a9f028d8:<path>
```

Paths below such as `server/src/refract/backends/claude.py` are relative to that commit. The commit is kept alive by `refs/plans/coding-agent-cli/base` in the Refract repository.

Preserved invariants, from DESIGN.md:

- Zero runtime dependencies. No module imports `refract`.
- The library adds only transport flags. It never adds permission, approval, sandbox, or tool flags: no `--yolo`, `bypassPermissions`, `--dangerously-skip-permissions`, `--approve`, or `--no-approve`.
- Portable options are exactly `model`, `reasoning`, `system_prompt`. Unsupported combinations are rejected with `unsupported_option`: Codex `system_prompt`, Agy `reasoning`, Agy `system_prompt`.
- Every exit path kills the process tree and removes temporary files.
- No logging handler configuration. No prompt, argv, env value, or tool payload in logs. No `REFRACT_*` reads or writes.
- Child processes launch as argv lists with `shell=False`.
- Portable across Linux, macOS, and Windows.

Out of scope: retries, fallback, scheduling, persistence, config files, `read_only`, `isolated`, entry-point plugins, Windows Job Objects, Refract migration, creating a Git remote, and publishing to PyPI.

Secrets: workers must not read or print `~/.codex`, `~/.claude`, `~/.pi`, `~/.gemini`, any `auth.json`, or any credential file. Live tests are not run.

## Baseline Evidence

At plan base `e309ab7` the repository holds one empty commit. No project files exist.

- `uv sync --locked`: fails at base, no `pyproject.toml`. Phase 1 repairs this.
- `uv run pytest`: fails at base, no project. Phase 1 repairs this.
- `uv run pyright`: fails at base, no project. Phase 1 repairs this.
- Tooling present: `uv 0.11.26`; CPython 3.12.13 and 3.14.4 installed through uv. `pyright` is not installed globally; Phase 1 adds it as a dev dependency.

### Phase 1: Scaffold and public types

**Phase Request:** Create the package scaffold and the public value types, events, and registry with no driver or process code. Use cases: a consumer constructs a `Request`; inspects a `Result`; pattern-matches `Event` variants; creates a `DriverRegistry`, registers a driver, and resolves it by name.
**Goal:** An installable, type-checked package exposing the public types from DESIGN.md "Public types", "Events", and "Driver extension".
**Depends on:** none
**Invariants & Boundaries:** Runtime dependencies stay empty. Dev dependencies only: `pytest`, `pytest-asyncio`, `pyright`. `requires-python = ">=3.12"`. No process, driver, or `api.py` code in this phase. `DriverRegistry(builtins=True)` registers nothing yet; Phase 4 and Phase 5 add built-ins.
**Files:**
- Create: `pyproject.toml`
- Create: `uv.lock`
- Create: `.gitignore`
- Create: `LICENSE`
- Create: `README.md`
- Create: `CHANGELOG.md`
- Create: `src/coding_agent_cli/__init__.py`
- Create: `src/coding_agent_cli/py.typed`
- Create: `src/coding_agent_cli/types.py`
- Create: `src/coding_agent_cli/events.py`
- Create: `src/coding_agent_cli/registry.py`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Create: `tests/test_types.py`
- Create: `tests/test_events.py`
- Create: `tests/test_registry.py`
**Tasks:**
1. Write `pyproject.toml` with hatchling build backend, name `coding-agent-cli`, version `0.1.0`, MIT license, classifiers for Python 3.12, 3.13, 3.14 and OS Independent, `dependencies = []`, a `dev` dependency group with `pytest`, `pytest-asyncio`, `pyright`, pytest config with `asyncio_mode = "auto"`, `addopts = "-m 'not live'"`, and a `live` marker, and `[tool.pyright]` with `include = ["src", "tests"]` and `pythonVersion = "3.12"`. Run `uv lock`.
2. TDD `types.py`: `Outcome` StrEnum, `Diagnostic`, `Usage`, `Request`, `Result`, `Capabilities` exactly as DESIGN.md "Public types". All `@dataclass(frozen=True, slots=True, kw_only=True)`. `Request.env` is copied to an immutable mapping. `Request.timeout_s` rejects zero, negative, NaN, and infinity with `ValueError`. `Request.args` is a tuple.
3. TDD `events.py`: the eight variants from DESIGN.md "Events" with `schema_version=1`, `sequence`, `entity_id`, and a fixed `kind`; an `Event` union type alias.
4. TDD `registry.py`: `Driver` Protocol with `check` and `run`; `DriverRegistry` with `register(name, driver, *, replace=False)`, `get(name)` raising `KeyError`, and `names()`. `__init__.py` re-exports all public names. `README.md` is a short placeholder; `CHANGELOG.md` has `## Unreleased`.
**Acceptance Criteria:**
- `uv sync --locked` succeeds and installs no runtime dependency.
- Assigning to any field of a public value type raises `FrozenInstanceError` or `AttributeError`.
- Mutating the dict passed as `Request.env` after construction does not change the request.
- Registering a duplicate name raises `ValueError`; `replace=True` succeeds.
- `pyright` reports 0 errors.
**Reviewer Checklist:**
- Field names and types match DESIGN.md exactly.
- No runtime dependency and no `refract` import.
- No global mutable registry.
**Verification Checks:**
- `uv sync --locked`
- `uv run pytest`
- `uv run pyright`

### Phase 2: Process transport

**Phase Request:** Port the asyncio subprocess transport and the cross-platform process-tree kill, with lifecycle hardening from DESIGN.md "Lifecycle" and "Process cleanup". Use cases: run argv with stdin and an env overlay, read lines, enforce a deadline, honor a cancel event, call `on_spawn(pid)`, and kill all descendants on every exit path.
**Goal:** A private `_process.run_process` that never leaks a process or blocks the loop during cleanup, on Linux, macOS, and Windows.
**Depends on:** Phase 1
**Invariants & Boundaries:** Port from `server/src/refract/backends/_process.py` and the launch and kill helpers of `server/src/refract/processes.py`: `prepare_command`, `process_group_kwargs`, `kill_process_tree`, and their private helpers. Do not port `terminate_process_tree`, `kill_stale_group`, `_group_members`, or `REFRACT_JOB_ID` logic. Replace the `spawn_hook` ContextVar with an `on_spawn` parameter. Windows `taskkill` runs via `asyncio.to_thread`. Cleanup has one total deadline. Logger name `coding_agent_cli.process`.
**Files:**
- Create: `src/coding_agent_cli/_process.py`
- Create: `src/coding_agent_cli/_platform.py`
- Create: `tests/launchers.py`
- Create: `tests/fixtures/fake_cli/echo_stdin.py`
- Create: `tests/fixtures/fake_cli/emit_both.py`
- Create: `tests/fixtures/fake_cli/exit_now.py`
- Create: `tests/fixtures/fake_cli/exit_status.py`
- Create: `tests/fixtures/fake_cli/grandchild.py`
- Create: `tests/fixtures/fake_cli/long_line.py`
- Create: `tests/fixtures/fake_cli/many_lines.py`
- Create: `tests/fixtures/fake_cli/non_utf8.py`
- Create: `tests/fixtures/fake_cli/sleeper.py`
- Create: `tests/fixtures/fake_cli/spawn_and_sleep.py`
- Create: `tests/test_process.py`
**Tasks:**
1. Port `server/tests/launchers.py` and the listed `server/tests/fixtures/fake_cli/*.py` helpers. Port `server/tests/test_transport.py` into `tests/test_process.py` against the new signature, and confirm they fail before the implementation exists.
2. Implement `_platform.py` with `prepare_command`, `process_group_kwargs`, and `kill_process_tree`, the last made async with Windows `taskkill` in a thread.
3. Implement `_process.py` from the Refract transport with: `on_spawn` callback, an already-set cancel preventing spawn, spawn `OSError` returned as an outcome with `error_class` `missing_cli` or `spawn_failed` instead of raising, a total cleanup deadline, and caller task cancellation re-raising `CancelledError` after cleanup.
4. Add tests: cancel set before spawn spawns nothing; repeated cancel is safe; `on_spawn` receives the pid; a grandchild process is dead after cancel, timeout, and normal exit; caller task cancellation kills the tree and re-raises.
**Acceptance Criteria:**
- Every ported transport test passes.
- New lifecycle tests pass on Linux locally.
- No module imports `refract`.
**Reviewer Checklist:**
- Kill runs in `finally` on every path, including `CancelledError`.
- No synchronous blocking call on the event loop during cleanup.
- Windows paths kept: `.cmd` resolution in `prepare_command`, `CREATE_NEW_PROCESS_GROUP`, `taskkill /T /F`.
**Verification Checks:**
- `uv sync --locked`
- `uv run pytest`
- `uv run pyright`

### Phase 3: Public API facade and contract suite

**Phase Request:** Implement `run`, `stream`, `run_sync`, and `check` on top of the registry and transport, plus shared argument validation and the outcome-precedence helper. Prove the contract with a toy driver before any built-in driver exists. Use cases: await `run` with `on_event`; iterate `stream` inside `async with` and break early; call `run_sync` from plain code; cancel from another thread via `call_soon_threadsafe`; register a custom driver.
**Goal:** A tested public API whose lifecycle and event guarantees hold for any `Driver`.
**Depends on:** Phase 2
**Invariants & Boundaries:** The API assigns `sequence` and emits exactly one `RunCompleted` after cleanup. Callback exceptions propagate after cleanup and never become an `Outcome`. `run_sync` raises `RuntimeError` inside a running loop. A bare `async for` over `stream` is documented as safe only when fully consumed. The `on_event` callback cannot block cancel or deadline enforcement. Outcome precedence follows DESIGN.md rows 1 to 11.
**Files:**
- Create: `src/coding_agent_cli/api.py`
- Create: `src/coding_agent_cli/_args.py`
- Create: `src/coding_agent_cli/_classify.py`
- Modify: `src/coding_agent_cli/__init__.py`
- Create: `tests/toy_driver.py`
- Create: `tests/test_api_contract.py`
- Create: `tests/test_stream.py`
- Create: `tests/test_args.py`
- Create: `tests/test_classify.py`
**Tasks:**
1. `_args.py`: shared validation from `validate_target_args` in `server/src/refract/config.py`: non-string args, NUL bytes, bare `--`. A helper that rejects raw args setting a portable field already given, and a helper that rejects unsupported portable options. All raise or return `invalid_args` and `unsupported_option` diagnostics. Do not port the isolated-Pi rule.
2. `_classify.py`: a pure function mapping driver-collected facts to `Outcome` and `Diagnostic` by DESIGN.md precedence rows 1 to 11, with structured CLI errors checked before stderr keywords and a specific diagnostic beating a generic nonzero exit. Table-driven tests cover every row and each tie in the Errors section.
3. `api.py`: `run`, `stream` returning an `EventStream` that is an async iterator and an async context manager with `aclose()` and a bounded queue, `run_sync`, and `check`. The default registry is private and built-ins-only.
4. `tests/toy_driver.py` uses the Phase 2 fake CLI helpers. Contract tests cover: strictly increasing `sequence`; exactly one `RunCompleted`; cancel before spawn; cancel during run returns `cancelled` with partial text; timeout returns `retryable` with code `timeout`; a stalled `on_event` does not block the deadline; a raising `on_event` propagates after the tree is killed; early `break` inside `async with` kills the tree; a full queue plus cancel terminates; `run_sync` inside a loop raises; cross-thread cancel works; unknown driver raises `KeyError`.
**Acceptance Criteria:**
- All contract tests pass.
- `pyright` reports 0 errors.
**Reviewer Checklist:**
- No path where a process outlives `run`, `stream` context exit, or `aclose()`.
- Classification order matches DESIGN.md exactly.
- `run_sync` does not nest loops or offload to a thread.
**Verification Checks:**
- `uv sync --locked`
- `uv run pytest`
- `uv run pyright`

### Phase 4: Claude and Pi drivers

**Phase Request:** Port the Claude and Pi drivers onto the new contract, register them as built-ins, and port their tests and fixtures. Use cases: run, resume by `session_id`, stream text and tool events, report usage, and classify failures for each CLI.
**Goal:** Built-in `claude` and `pi` drivers that pass ported and corrected tests.
**Depends on:** Phase 3
**Invariants & Boundaries:** Port parsers from `server/src/refract/backends/claude.py`, `server/src/refract/backends/pi.py`, `server/src/refract/backends/_common.py`, and `TokenCounters` from `server/src/refract/telemetry.py`. Port portable flag builders `_claude_args` and `_pi_args` from `server/src/refract/drivers.py` without `isolated`, `read_only`, `backend_profile`, or Pi `--approve`. Remove Claude's `--permission-mode bypassPermissions`. Claude `tool.completed` comes from tool-result evidence, not `content_block_stop`. Entity IDs are unique per run. Claude HTTP 500 maps to `service_error`. A Pi timeout without text maps to `timeout`. Do not port characterization tests that contradict DESIGN.md; rewrite them.
**Files:**
- Create: `src/coding_agent_cli/_drivers/__init__.py`
- Create: `src/coding_agent_cli/_drivers/_common.py`
- Create: `src/coding_agent_cli/_drivers/_usage.py`
- Create: `src/coding_agent_cli/_drivers/claude.py`
- Create: `src/coding_agent_cli/_drivers/pi.py`
- Modify: `src/coding_agent_cli/registry.py`
- Create: `tests/fixtures/fake_cli/fake_cli.py`
- Create: `tests/fixtures/usage/claude.jsonl`
- Create: `tests/fixtures/usage/pi.jsonl`
- Create: `tests/cli_stub.py`
- Create: `tests/test_claude_driver.py`
- Create: `tests/test_pi_driver.py`
- Create: `tests/test_usage.py`
**Tasks:**
1. Port `server/tests/fixtures/fake_cli/fake_cli.py`, `server/tests/cli_stub.py` with `TargetConfig` removed, and the Claude and Pi usage fixtures. Port the Claude and Pi parts of `server/tests/test_claude_backend.py`, `server/tests/test_pi_backend.py`, `server/tests/test_streaming_backends.py`, `server/tests/test_backend_common.py`, `server/tests/test_telemetry.py`, and `server/tests/test_drivers.py`. Confirm they fail first.
2. Port `_common.py` and the usage counters, emitting typed events instead of dicts.
3. Port the Claude driver with the corrections in Invariants. Register it as a built-in.
4. Port the Pi driver with the corrections in Invariants. Register it as a built-in. Add argv tests asserting no permission or approval flag appears for any option combination.
**Acceptance Criteria:**
- `DriverRegistry().names()` contains `claude` and `pi`.
- Argv tests prove no flag from the Preserved invariants list is ever added.
- Ported and rewritten driver tests pass.
**Reviewer Checklist:**
- Every dropped or rewritten Refract test is listed in the phase journal with its reason.
- No prompt, argv, or env value is logged.
- Raw `args` overriding a driver-owned transport flag is rejected.
**Verification Checks:**
- `uv sync --locked`
- `uv run pytest`
- `uv run pyright`

### Phase 5: Codex and Agy drivers

**Phase Request:** Port the Codex driver with its rollout collector and the Agy driver, register them as built-ins, and port their tests and fixtures. Use cases: same as Phase 4 for each CLI.
**Goal:** Built-in `codex` and `agy` drivers that pass ported and corrected tests.
**Depends on:** Phase 4
**Invariants & Boundaries:** Port from `server/src/refract/backends/codex.py`, `server/src/refract/backends/codex_rollout.py`, `server/src/refract/backends/agy.py`, and `_codex_args` and `_agy_args` from `server/src/refract/drivers.py` without `backend_profile`. Remove Codex `--yolo` and Agy `--dangerously-skip-permissions`. Codex rejects `system_prompt`; Agy rejects `reasoning` and `system_prompt`. Codex keeps rejecting `--cd`, `--cd=`, `-C`, and attached `-C...`. The rollout collector reads `CODEX_HOME` from the request's effective environment. Codex empty output maps to `no_output`, not `request_fatal`. A Codex auth diagnostic beats a nonzero exit. Agy reads its structured status. Temporary files use prefix `coding-agent-cli-`.
**Files:**
- Create: `src/coding_agent_cli/_drivers/codex.py`
- Create: `src/coding_agent_cli/_drivers/codex_rollout.py`
- Create: `src/coding_agent_cli/_drivers/agy.py`
- Modify: `src/coding_agent_cli/registry.py`
- Modify: `tests/fixtures/fake_cli/fake_cli.py`
- Create: `tests/fixtures/usage/codex.jsonl`
- Create: `tests/fixtures/usage/agy.jsonl`
- Create: `tests/test_codex_driver.py`
- Create: `tests/test_codex_rollout.py`
- Create: `tests/test_agy_driver.py`
- Modify: `tests/test_usage.py`
**Tasks:**
1. Port the Codex and Agy parts of `server/tests/test_codex_backend.py`, `server/tests/test_agy_backend.py`, `server/tests/test_streaming_backends.py`, and `server/tests/test_drivers.py`, plus their usage fixtures. Confirm they fail first.
2. Port the Codex driver and rollout collector with the corrections in Invariants. Register it.
3. Port the Agy driver with the corrections in Invariants. Register it. Extend argv tests to Codex and Agy.
**Acceptance Criteria:**
- `DriverRegistry().names()` equals `("agy", "claude", "codex", "pi")` in sorted order.
- A Codex `Request` with `system_prompt` returns `request_fatal` with code `unsupported_option` without spawning.
- A rollout test proves `CODEX_HOME` from `Request.env` wins over `os.environ`.
- Ported and rewritten driver tests pass.
**Reviewer Checklist:**
- Every dropped or rewritten Refract test is listed in the phase journal with its reason.
- Temporary files are removed on every path.
- Codex `-c` values are TOML-quoted correctly.
**Verification Checks:**
- `uv sync --locked`
- `uv run pytest`
- `uv run pyright`

### Phase 6: Packaging, CI, live tests, and documentation

**Phase Request:** Make the package releasable: package-integrity tests, CI and release workflows, ported live tests, and user documentation. Use cases: a consumer installs the wheel and imports the library; a maintainer runs CI on push; a maintainer tags a release that publishes through PyPI trusted publishing.
**Goal:** A wheel and sdist that pass integrity tests, plus CI and docs that describe the 0.1.0 contract.
**Depends on:** Phase 5
**Invariants & Boundaries:** Do not publish, push, tag, or create a remote. The release workflow runs only on a `v*` tag and uses `pypa/gh-action-pypi-publish` with `id-token: write`; no token secret. CI matrix: ubuntu, macos, windows on 3.12; ubuntu on 3.13 and 3.14; steps `uv sync --locked`, `uv run pytest`, `uv run pyright`. Live tests keep the `live` marker, are excluded by default, and are not run in this phase.
**Files:**
- Create: `tests/test_packaging.py`
- Create: `tests/test_live.py`
- Create: `.github/workflows/ci.yml`
- Create: `.github/workflows/release.yml`
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `pyproject.toml`
**Tasks:**
1. `tests/test_packaging.py`: run `uv build` into a temporary directory, install the wheel and the sdist each into a fresh venv, and assert `py.typed` is present, `importlib.metadata.requires("coding-agent-cli")` is empty or `None`, and no installed module imports `refract`.
2. Port `server/tests/test_live_backends.py` into `tests/test_live.py` against `run`, awaiting correctly, recording each CLI's `--version` in the test output, and passing permission flags through `args`.
3. Write `.github/workflows/ci.yml` and `.github/workflows/release.yml` per Invariants.
4. Write `README.md`: install, quick start for `run`, `stream` with `async with`, `run_sync`, custom drivers, the event table, the outcome table, the portable-option support matrix, a per-CLI table of the permission and approval flags callers must pass for unattended runs, cross-thread cancel, the retryable-is-not-replay-safe warning, and the Windows Job Object limitation. Add a provenance note crediting Refract. Update `CHANGELOG.md` for 0.1.0.
**Acceptance Criteria:**
- `uv build` produces one wheel and one sdist.
- Packaging tests pass.
- `uv run pytest -m live --collect-only` collects the live tests; the default run skips them.
- README shows the permission flag table for all four CLIs.
**Reviewer Checklist:**
- No secret, token, or personal path in workflows or docs.
- README examples match the actual public signatures.
- CI matrix matches DESIGN.md.
**Verification Checks:**
- `uv sync --locked`
- `uv run pytest`
- `uv run pyright`
- `uv build`
- `uv run pytest -m live --collect-only -q`
