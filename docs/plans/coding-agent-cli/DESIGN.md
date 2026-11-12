# coding-agent-cli Library Design

**Status:** Confirmed 2026-11-12
**Consultation:** One `consult` job on the `openai` profile, `d3b69a05-0a1c-4762-8e21-d7d64a6ce879`, completed. Its findings on stream cleanup, outcome gaps, and implicit permission flags were verified against the repository and adopted. A `google` cross-check job, `9052a41d-2f01-4d8f-adb2-2f6518bd9737`, was cancelled at the user's request.

## Purpose

Extract Refract's coding-agent CLI backends into a standalone Python library, `coding-agent-cli`, imported as `coding_agent_cli`. The library runs a prompt through an external agent CLI, streams normalized events, and returns a classified result. Refract and other projects consume it as an ordinary dependency.

## Users and Success Criteria

Users:

- The Refract server, after a later migration design.
- Other Python projects that drive external coding-agent CLIs.

Success criteria:

- `coding-agent-cli` 0.1.0 is published to PyPI from its own repository.
- Built-in drivers `claude`, `codex`, `pi`, and `agy` pass the contract suite and the per-driver suites on Linux, macOS, and Windows with Python 3.12.
- An installed wheel has zero runtime dependencies, ships `py.typed`, and imports nothing from `refract`.
- A consumer can register a custom driver without forking.
- No built-in driver adds a permission, approval, sandbox, or tool flag.

## Constraints, Invariants, and Non-Goals

Constraints:

- Python 3.12 or later. Linux, macOS, and Windows.
- Standard library only at runtime.
- Separate repository, PyPI distribution, MIT license, with a provenance note crediting Refract's backends.
- Child processes launch as argv lists with `shell=False`.

Invariants:

- The library adds only transport flags: output mode, prompt delivery, and resume selector.
- Portable options are exactly `model`, `reasoning`, and `system_prompt`. Everything else is raw `args`.
- A portable option the driver cannot express is rejected, never dropped.
- Every run ends with the process tree killed and temporary files removed, on every exit path.
- The library never configures logging handlers and never logs prompts, argv, environment values, or tool payloads.
- The library reads and injects no `REFRACT_*` variable.

Non-goals:

- Retries, fallback across agents, scheduling, concurrency limits, persistence, and config files.
- `read_only` and `isolated` options. Callers express them through `args`.
- Pi `--approve` or `--no-approve` handling. The caller passes either flag.
- Implicit permission bypass. Codex `--yolo`, Claude `--permission-mode bypassPermissions`, and Agy `--dangerously-skip-permissions` are removed; callers pass them through `args`.
- Entry-point plugin discovery.
- Windows Job Objects in 0.1.
- Migrating Refract. That is a separate later design.

## Chosen Approach

Contract-first port. Define the public types, event schema, outcome precedence, and lifecycle with a contract test suite first. Then port the existing parsers and transport from `server/src/refract/backends/` behind that contract, fixing the classification gaps listed under Errors and Edge Cases.

Rejected:

- Lift-and-shift, then harden: 0.1 would publish outcome semantics already known to be wrong.
- Clean rewrite: discards tested parser edge cases and carries the highest regression risk.

## Architecture and Data Flow

### Package layout

```text
src/coding_agent_cli/
  __init__.py      public re-exports
  api.py           run, stream, run_sync, check
  types.py         Request, Result, Usage, Diagnostic, Outcome, Capabilities
  events.py        Event variants
  registry.py      Driver protocol, DriverRegistry
  _process.py      private: spawn, pump, process-tree kill
  _platform.py     private: POSIX process groups, Windows termination
  _drivers/        private: claude, codex, codex_rollout, pi, agy
  py.typed
```

Source modules ported from Refract: `backends/*.py`, `processes.py` launch and kill helpers, `telemetry.py` counters, and the argument builders and capability probe in `drivers.py`.

### Public types

All value types are `@dataclass(frozen=True, slots=True, kw_only=True)`.

| Type | Fields |
|---|---|
| `Request` | `driver: str`, `prompt: str`, `cwd: Path`, `session_id: str \| None`, `model: str \| None`, `reasoning: str \| None`, `system_prompt: str \| None`, `args: tuple[str, ...]`, `env: Mapping[str, str]`, `timeout_s: float \| None`, `executable: str \| None` |
| `Result` | `outcome: Outcome`, `text: str`, `session_id: str \| None`, `model: str \| None`, `usage: Usage`, `returncode: int \| None`, `error: Diagnostic \| None`, `warnings: tuple[Diagnostic, ...]` |
| `Usage` | `input`, `output`, `cache_read`, `cache_write`, `context_used`, `context_window`, each `int \| None` |
| `Diagnostic` | `code: str`, `message: str` |
| `Outcome` | `StrEnum`: `success`, `retryable`, `agent_fatal`, `request_fatal`, `cancelled` |
| `Capabilities` | `available: bool`, `executable: Path \| None`, `structured_streaming: bool \| None` |

Field rules:

- `env` overlays the inherited environment and is stored as an immutable copy.
- `timeout_s=None` disables the timeout. Other values must be finite and positive.
- `executable` overrides the PATH lookup of the driver's default executable.
- `Usage` covers this invocation. Unknown values stay `None`, never zero. `input` includes cache reads and writes once.
- `Result.model` is the observed model, not the requested one.
- `Capabilities` uses `None` for unverified.

Refract fields not carried over: `PROMPT`, `cd`, `SESSION_ID` are renamed; `cancel_event` and `emitter` become call arguments; `"OK"`/`"FATAL"` and `TARGET_FATAL` are replaced by `Outcome`; `agent_messages` becomes `text`; `tokens` dict becomes `Usage`; `error`/`error_class` become `Diagnostic`; the `timeout_s=0` sentinel becomes `None`. `TargetConfig`, `backend_profile`, `read_only`, and `isolated` are dropped.

### Entry points

```python
async def run(req, *, on_event=None, cancel=None, on_spawn=None, registry=None) -> Result
def stream(req, *, cancel=None, on_spawn=None, registry=None) -> EventStream
def run_sync(req, *, registry=None) -> Result
async def check(req, *, registry=None, refresh=False) -> Capabilities
```

- `on_event`: `async (Event) -> None`.
- `on_spawn`: `async (pid: int) -> None`, replacing Refract's `spawn_hook` ContextVar.
- `cancel`: `asyncio.Event | None`.
- `EventStream` is an async iterator and an async context manager with `aclose()`.

### Driver extension

```python
class Driver(Protocol):
    async def check(self, req: Request, *, refresh: bool = False) -> Capabilities: ...
    async def run(self, req: Request, *, on_event, cancel, on_spawn) -> Result: ...
```

- `DriverRegistry(builtins=True)` is instance-scoped. There is no global mutable registry.
- `register(name, driver, *, replace=False)` rejects a duplicate name unless `replace=True`.
- `get(name)` raises `KeyError` for an unknown name. `names()` lists registered names.
- `registry=None` uses a private built-ins-only registry.

### Argument policy

Argv order: built-in transport flags, then portable flags, then caller `args`.

Portable flag mapping ported from `server/src/refract/drivers.py`:

| Driver | `model` | `reasoning` | `system_prompt` |
|---|---|---|---|
| claude | `--model` | `--effort` | `--system-prompt` |
| codex | `--model` | `-c model_reasoning_effort=<v>` | Unsupported; callers pass `-c developer_instructions=...` in `args` |
| pi | `--model` | `--thinking` | `--system-prompt` |
| agy | `--model` | Unsupported | Unsupported |

Always rejected as `request_fatal` with code `invalid_args`:

- Non-string args and args containing NUL.
- A bare `--` token.
- Codex `--cd`, `--cd=...`, `-C`, and attached `-C...`.
- A raw arg that sets a field already given as a portable option, such as `--model` with `model=`.
- A raw arg that overrides a driver-owned transport flag.

Moved to callers: Pi extension, skill, and prompt-template restrictions; tool allowlists; sandbox, approval, and permission flags; Refract environment-key rules.

### Events

Every variant is a frozen dataclass with `schema_version: int = 1`, `sequence: int`, `entity_id: str | None`, and a fixed `kind`.

| Variant | `kind` | Fields |
|---|---|---|
| `SessionIdentified` | `session.identified` | `session_id` |
| `MessageStarted` | `assistant.message.started` | none |
| `TextDelta` | `assistant.text.delta` | `text` |
| `MessageCompleted` | `assistant.message.completed` | none |
| `ToolStarted` | `tool.started` | `tool`, `activity: "command" \| "tool_call"`, `input` |
| `ToolCompleted` | `tool.completed` | `status: "completed" \| "error" \| "unknown"` |
| `UsageUpdated` | `usage.updated` | `usage` |
| `RunCompleted` | `run.completed` | `result` |

Rules:

- The API assigns `sequence`, strictly increasing per run. Drivers do not.
- `UsageUpdated` is a full replacement snapshot, not an increment. Refract's `telemetry.updated` is renamed `usage.updated`.
- `entity_id` is unique within a run. Refract's reuse of `msg-<index>` across messages is fixed.
- The API emits exactly one `RunCompleted`, after cleanup. Drivers never emit it.
- Adding a field or kind is compatible. Consumers must ignore unknown kinds. Unmapped CLI records are dropped, not forwarded raw.
- Claude `tool.completed` is emitted from tool-result evidence, not from `content_block_stop`.

### Data flow for `run`

1. Resolve the driver from the registry.
2. The driver validates the request, checks `cwd`, and resolves the executable using the request's effective `PATH`.
3. The driver builds argv.
4. `_process` spawns the CLI in a new process group, merges stderr into stdout, and awaits `on_spawn(pid)`.
5. Each output line goes to the driver's parser. The parser produces typed events. The API numbers them and awaits `on_event`.
6. On exit, cancel, or timeout, the process tree is killed and drained. The driver classifies a `Result`. The API emits `RunCompleted` and returns the `Result`.

### Lifecycle

| Trigger | Behavior |
|---|---|
| `cancel` set | Cleanup, then a `cancelled` Result keeping partial text and usage. An already-set event prevents spawning. |
| Caller task cancelled | Cleanup, then `CancelledError` is re-raised. |
| `timeout_s` reached | Cleanup, then a `retryable` Result with code `timeout`. |
| `on_event` raises or stalls | An independent watcher still enforces cancel and the deadline. A raised exception propagates after cleanup and never maps to an outcome. |
| `stream()` | A producer task feeds a bounded queue with backpressure. Queued events are delivered before a producer exception is re-raised. `async with` exit or `aclose()` cancels the producer and kills the process tree. Bare `async for` is supported only when it runs to the end, and this is documented. |
| `run_sync()` | Uses `asyncio.run()`. Raises `RuntimeError` when called inside a running loop. |
| Cross-thread cancel | Documented as `loop.call_soon_threadsafe(cancel.set)`. Refract's 50 ms `threading.Event` polling bridge is removed. |

Codex rollout collection stays private and best-effort. It reads `CODEX_HOME` from the request's effective environment, not `os.environ`.

### Logging

Logger names are `coding_agent_cli`, `coding_agent_cli.process`, and `coding_agent_cli.drivers.<name>`. Log lifecycle metadata and diagnostic codes only.

## Errors and Edge Cases

### Outcome precedence

The first matching row wins.

| # | Condition | Outcome | `code` |
|---|---|---|---|
| 1 | Cancel event set | `cancelled` | `cancelled` |
| 2 | Bad `cwd`, invalid arg, unsupported or conflicting portable option | `request_fatal` | `bad_cwd`, `invalid_args`, `unsupported_option` |
| 3 | Prompt cannot be encoded or exceeds the argv limit | `request_fatal` | `invalid_prompt` |
| 4 | Executable missing or spawn denied | `agent_fatal` | `missing_cli`, `spawn_failed` |
| 5 | CLI reports auth or login failure | `agent_fatal` | `auth` |
| 6 | CLI reports invalid or unknown model | `request_fatal` | `invalid_model` |
| 7 | CLI reports context window exceeded | `request_fatal` | `context_overflow` |
| 8 | Deadline reached | `retryable` | `timeout` |
| 9 | CLI reports HTTP 5xx, overload, or rate limit | `retryable` | `service_error` |
| 10 | Nonzero exit, stdin write failure, malformed stream, or no output | `retryable` | `execution_error`, `stdin_failed`, `no_output` |
| 11 | Clean exit with text | `success` | none; a missing session ID adds warning `no_session_id` |

Rules:

- Structured CLI error data is checked before keyword matching on stderr.
- A specific diagnostic beats a generic nonzero exit.
- `retryable` means possibly transient. It does not mean safe to replay; the agent may already have changed files.

Fixes to Refract behavior, verified in the consultation:

- Claude HTTP 500 is classified `fatal_backend`; it becomes `service_error`. The test at `server/tests/test_claude_backend.py:149` pins the old behavior and is not ported as-is.
- Codex rewrites empty output to `request_fatal` at `server/src/refract/backends/codex.py:49`; it becomes `no_output`.
- A Codex nonzero exit overwrites an auth diagnostic; the auth diagnostic wins.
- A Pi timeout without text can report `no_agent_messages`; `timeout` wins.
- Agy ignores its structured status; it is read.
- Exceptions from spawning escape the taxonomy; they map to row 4.

### Raised, not returned

- Exceptions from `on_event` or `on_spawn`.
- Unknown driver name, as `KeyError`.
- `run_sync` inside a running loop, as `RuntimeError`.
- Caller task cancellation, as `CancelledError`.
- Library bugs.

### Process cleanup

- POSIX: new session and process group; SIGTERM, then SIGKILL after a grace period.
- Windows: `taskkill /T /F`, run in a thread so it does not block the event loop.
- Cleanup has one total deadline, not a renewable per-read timeout.
- Temporary files use the prefix `coding-agent-cli-` and are removed in `finally`.

### Capability checks

- `check()` runs the CLI's `--help` asynchronously with a bounded timeout, under the same process cleanup.
- Only positive results are cached, keyed by resolved executable path and mtime. `refresh=True` bypasses the cache.
- A failed or timed-out probe returns `None`, never a cached `False`.
- `run()` never probes on its own.

## Migration

n/a. This design delivers the library only. A later Refract migration design will drop `read_only`, `isolated`, and the implicit bypass flags, and move them into target `args`.

## Testing

Runner: `uv run pytest` with `pytest-asyncio`. Type checker: `pyright` in CI.

1. Contract suite, written before any driver is ported. It covers:
   - Immutable types and event ordering.
   - Exactly one `RunCompleted` per run.
   - Outcome precedence rows 1 to 11.
   - Lifecycle cases: cancel before spawn, cancel during spawn, repeated cancel, stalled callback, full stream queue with cancel, early exit from `async with`, descendant processes killed, temporary files removed, and `run_sync` inside a running loop.
2. Driver suites. Port the fake CLI, the Windows `.cmd` and `.ps1` launchers, and fixtures from `server/tests/`, with `TargetConfig` removed. Each driver gets parser, argv and policy, usage, and transport tests. Argv tests assert that no permission or approval flag is ever added. Characterization tests that conflict with the Errors section are rewritten, not ported.
3. Custom driver test. A toy driver registered on a `DriverRegistry` gets the full lifecycle and event numbering.
4. Package test. Build a wheel and an sdist, install each into a clean virtual environment, and check `py.typed`, zero runtime dependencies, and no `refract` import.
5. Live tests carry the `live` marker, are excluded by default, and record the CLI version. Refract's live Agy and Codex tests call `execute()` without `await` at `server/tests/test_live_backends.py:31`; the ports fix this.
6. CI matrix: Linux, macOS, and Windows on Python 3.12. Python 3.13 and 3.14 on Linux. Classifiers list 3.12, 3.13, and 3.14.

Release: version `0.1.0`, event `schema_version` 1, PyPI trusted publishing. Re-check that the name `coding-agent-cli` is unclaimed before the first publish; it returned HTTP 404 on 2026-11-12.

## Risks and Open Questions

Risks:

| Risk | Mitigation |
|---|---|
| Windows process leak when the launcher exits before its descendants | Windows CI test. Job Objects deferred and documented as a known limit. |
| Event or outcome semantics freeze in a wrong form | Contract suite first. Stay on 0.x until Refract migrates. |
| CLI flag or output format drift | Fixtures labelled with the CLI version. Opt-in live tests. |
| Unattended runs hit permission prompts once implicit bypass flags are gone | README documents the flag for each CLI. The Refract migration design must add them to target `args`. |
| Usage mixed across concurrent runs | Per-run usage. Codex rollout uses the request environment and an exact session match. |

Resolved during planning, 2026-11-12:

- Codex has no `--system-prompt` flag. Its config offers `developer_instructions`, which is appended to the base prompt, and `model_instructions_file`, which replaces it and is discouraged upstream. Neither matches Claude's replacing `--system-prompt`, so Codex rejects `system_prompt` with `unsupported_option`. Source: Codex `codex-rs/config/src/config_toml.rs` and `codex-rs/core/src/session/mod.rs` via Context7 `/openai/codex`.
- 0.1 supports Python 3.12, 3.13, and 3.14.
- Repository: local `~/projects/coding-agent-cli`, branch `main`. No remote yet.
