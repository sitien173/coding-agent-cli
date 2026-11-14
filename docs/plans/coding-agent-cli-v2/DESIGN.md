# coding-agent-cli v2 Design

**Status:** Confirmed 2026-11-14
**Consultation:** Two `consult` jobs completed: `openai` `6c10cfc2-1978-4f76-8320-42659e6f80a0` and `anthropic` `78463fd6-afeb-4160-89ab-83fd35b21118`. Both confirmed v1 defects 1 to 7, with defect 5 narrowed for Codex. Both recommended the chosen approach. Their pushbacks on `run_sync` cancel, `feed_text`, nullable `entity_id`, and the Codex model are adopted. Their conflicts on oversized lines and limit-versus-timeout precedence are resolved below.

## Purpose

Rebuild `coding-agent-cli` as 0.2.0. v1 passes its own suite but does not match the real CLIs: Claude fails on every run without `--verbose`, Codex tool and error events are never parsed, and partial text is lost on cancel. The hand-written fixtures encode these defects. v2 fixes the defects, removes code without proven value, splits parsing from transport, and opens a safe driver-authoring API.

v1 defects carried into this design:

| # | Defect | Location |
|---|---|---|
| 1 | Claude argv omits `--verbose`; `-p --output-format stream-json` exits with an error | `_drivers/claude.py:268` |
| 2 | Codex parser expects item type `tool_call`; Codex emits `command_execution`, `file_change`, `mcp_tool_call`, `web_search`, `todo_list`, `reasoning`, `agent_message`, `error` | `_drivers/codex.py:141`, `:158` |
| 3 | Codex `turn.failed` and top-level `error` events are ignored | `_drivers/codex.py:162` |
| 4 | Command allow-list holds lowercase `bash`; Claude's tool is `Bash` | `_drivers/_common.py:25` |
| 5 | Cancel or timeout loses text: Claude loses all of it, Pi loses the in-flight message; Codex keeps completed messages | `_drivers/claude.py:106`, `_drivers/pi.py:149` |
| 6 | Claude `error_max_turns` becomes retryable `execution_error` | `_drivers/claude.py:102`, `_classify.py:147` |
| 7 | README says Job Objects are unused; commit `3318a7f` added them | `README.md:174` |

Additional v1 defects found in consultation:

- `on_spawn` runs before deadline supervision, so a stalled callback defeats cancel and timeout. `_process.py:323`
- The cancelled outcome reads `cancel.is_set()` at classification time, so a cancel set after a clean exit turns success into `cancelled`. `_drivers/_common.py:203`
- The Windows Job Object lacks `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`. `_platform.py:94`
- A cleanup timeout is only logged. `_process.py:261`
- Parser exceptions become retryable `execution_error`. `_process.py:129`
- Pi keeps a stale structured error after a successful CLI retry. `_drivers/pi.py:139`
- Claude `ToolStarted.input` is read from `content_block_start`, which carries an empty input under `--include-partial-messages`. `_drivers/claude.py:163`
- Only Codex emits `MessageStarted` and `MessageCompleted`.
- Codex rejects every `-c key=value` once `reasoning` is set. `_drivers/codex.py:265`
- Agy marks every finished tool successful and assumes one active tool. `_drivers/agy.py:172`

## Users and Success Criteria

Users:

- Refract, which will be updated by its maintainer. No compatibility shim is provided.
- Other Python projects that drive coding-agent CLIs.
- Authors of external drivers, through the public `CliDriver` API.

Success criteria:

- `uv run ruff format --check`, `uv run ruff check`, `uv run pyright`, and `uv run pytest` pass on the CI matrix.
- Scrubbed fixtures recorded from the real CLIs exist for `claude`, `codex`, `pi`, and `agy`, labelled with the CLI version.
- Parser replay tests pass on every recorded fixture.
- v1 defects listed under Purpose each have a test that failed before the fix.
- README examples match the public signatures, and README has a "Migrating from 0.1" section.
- Live tests are not a completion criterion.

## Constraints, Invariants, and Non-Goals

Constraints:

- Python 3.12 or later on Linux, macOS, and Windows.
- Standard library only at runtime.
- Child processes launch as argv lists with `shell=False`.

Invariants kept from v1:

- Drivers add only transport flags. No driver adds a permission, approval, sandbox, or bypass flag.
- Portable options are exactly `model`, `reasoning`, and `system_prompt`. A portable option a driver cannot express is rejected, never dropped.
- Every exit path kills the process tree and removes temporary files.
- The library never configures logging handlers and never logs prompts, argv, environment values, or tool payloads.

Invariants new in v2:

- `CliDriver.run()` and `CliDriver.check()` are implemented once in the base and are not overridden.
- Parsers are synchronous, perform no I/O, and are called by one dispatcher.
- A parser branch exists only for an event shape proven by a recorded fixture or official documentation.
- The stop cause is latched once and never changes afterwards.

Non-goals:

- Codex rollout file reading. `codex_rollout.py` is deleted.
- A public low-level `Driver` protocol. It stays private until a non-JSONL transport such as `agy acp` or `codex app-server` is needed.
- Authentication status in `Capabilities`.
- Retries, fallback across agents, scheduling, persistence, config files, entry-point plugin discovery.
- A compatibility layer for 0.1.
- Live tests as a gate.

## Chosen Approach

`CliDriver` plus `Launch`, `Parser`, and a shared transport. `CliDriver` is the only public extension point. A driver declares how to launch the CLI and how to parse its output; the base owns spawn, supervision, cleanup, text accumulation, and classification.

Rejected:

- Same split with a public low-level `Driver` protocol: lets external drivers bypass process-tree and temp-file cleanup; adding it later is non-breaking.
- One class with overridable hooks: keeps parsing coupled to transport and invites I/O inside hooks.
- Declarative event-mapping table: real streams need stateful correlation and deduplication, so the table grows into a DSL.

## Architecture and Data Flow

### Package layout

```text
src/coding_agent_cli/
  __init__.py      public re-exports
  api.py           run, stream, run_sync, check
  types.py         Request, Result, Usage, Diagnostic, Outcome, ErrorCode, Capabilities
  events.py        Event variants, SCHEMA_VERSION
  driver.py        CliDriver, Launch, Parser, Facts, RequestError
  registry.py      DriverRegistry
  _transport.py    spawn, pipe readers, stdin writer, supervisor, cleanup
  _platform.py     POSIX process groups, Windows Job Object
  _args.py         argument validation
  _classify.py     classify(RunFacts, Facts) -> Result
  _drivers/        claude, codex, pi, agy, shared helpers
  py.typed
```

Removed: `_drivers/codex_rollout.py`, `_process.py` replaced by `_transport.py`, `_drivers/_usage.py` folded into `Facts`, `EventBuffer`, the public `Driver` protocol, the duplicate `SpawnHandler`, and the shared command allow-list.

### Driver API

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class Launch:
    argv: tuple[str, ...]
    stdin: bytes | None = None
    read_back: tuple[Path, ...] = ()


class Parser(Protocol):
    def feed_json(self, obj: dict[str, Any]) -> Iterable[Event]: ...
    def feed_text(self, line: str, *, stream: Literal["stdout", "stderr"]) -> Iterable[Event]: ...
    def finish(self, read_back: Mapping[Path, bytes]) -> Facts: ...


class CliDriver:
    name: ClassVar[str]
    executable: ClassVar[str]
    max_line_bytes: ClassVar[int] = 16 * 1024 * 1024

    def launch(self, req: Request, exe: Path, tmp: Path) -> Launch: ...
    def parser(self, req: Request) -> Parser: ...
```

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class Facts:
    final_text: str | None = None
    session_id: str | None = None
    model: str | None = None
    usage: Usage = Usage()
    cost_usd: float | None = None
    num_turns: int | None = None
    stop_reason: str | None = None
    error: Diagnostic | None = None
    error_text: str = ""
```

Rules:

- The base creates the per-run temporary directory `tmp` before `launch()` and removes it in `finally`.
- `launch()` raises `RequestError(code, message)` for request problems such as an oversized prompt.
- `parser()` returns a new parser per run.
- Lines that parse as a JSON object go to `feed_json`. Other stdout lines and every stderr line go to `feed_text`.
- Every driver emits `MessageStarted`, `TextDelta`, and `MessageCompleted` for each assistant message.
- The base accumulates `TextDelta` text of the current message. `Result.text` is `Facts.final_text` when set, else the accumulated text of the last assistant message.
- A parser maps structured CLI errors to `Facts.error` with an `ErrorCode`. Unstructured error text goes to `Facts.error_text`.
- `DriverRegistry` accepts `CliDriver` instances only. `register(name, driver, *, replace=False)`, `get(name)` raising `KeyError`, and `names()` keep their v1 behavior.

### Data flow for `run`

1. Resolve the driver from the registry.
2. Validate `cwd`, `args`, and portable options through `_args.py`.
3. Resolve the executable with the request's effective `PATH`.
4. Create `tmp` and call `launch()`.
5. The transport spawns the CLI and awaits `on_spawn(pid)` under supervision.
6. Each line goes to the parser. The base numbers events with `sequence`, emits `RawEvent` first when `raw_events=True`, and awaits `on_event`.
7. On stop, the transport kills the tree, drains both pipes, reads `read_back` files, and the base calls `finish()`.
8. `classify(RunFacts, Facts)` builds the `Result`. The base emits `RunCompleted` and returns the `Result`.

`RunFacts` comes from the transport: latched stop cause, return code, stderr tail, spawn or stdin failure, parser exception, cleanup status, and measured `duration_s`.

### Transport

Spawn:

- POSIX: new session and process group. Termination is SIGTERM, then SIGKILL after a grace period.
- Windows: Job Object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, assigned right after spawn. The safe `.cmd` to PowerShell `-File` handling in `_platform.py:45` is kept.

Readers:

- Stdout, stderr, and the stdin writer run as separate tasks. Both pipes drain to EOF, including during cleanup.
- Stdout is read in 64 KiB chunks and split on `\n` with a scan offset. Lines decode as UTF-8 with `errors="replace"`.
- A line longer than `max_line_bytes` is discarded up to the next newline, and the run gets warning `line_too_long`. The run continues.
- Stderr lines go to `feed_text`. A byte-capped tail of 64 KiB is kept for classification.
- Order between stdout and stderr is not guaranteed. Order within each stream is preserved.

Supervision without polling:

- One supervisor awaits the first of: process exit, `cancel`, wall deadline from `timeout_s`, idle deadline from `idle_timeout_s`.
- The idle deadline is rescheduled on any received bytes from either pipe. It pauses while the base waits on `on_event` or a full `stream()` queue. It is off by default.
- `on_spawn` runs under the same supervisor.
- The stop cause is latched to the first of `exited`, `cancelled`, `timeout`, `idle_timeout`.
- After the leader exits, both pipes get one bounded wait for EOF, then the tree is killed.
- Cleanup has one total deadline. Exceeding it adds warning `cleanup_incomplete`.

### Entry points

```python
async def run(req, *, on_event=None, cancel=None, on_spawn=None, registry=None) -> Result
def stream(req, *, cancel=None, on_spawn=None, registry=None) -> EventStream
def run_sync(req, *, on_event: Callable[[Event], None] | None = None,
             cancel: threading.Event | None = None, registry=None) -> Result
async def check(driver: str, *, executable: str | None = None,
                env: Mapping[str, str] | None = None, refresh: bool = False,
                registry=None) -> Capabilities
```

- `run_sync` uses `asyncio.run()` and raises `RuntimeError` inside a running loop. It checks the `threading.Event` every 100 ms; this is the only remaining poll, because `threading.Event` has no async wait.
- `stream()` keeps the v1 bounded queue and `aclose()` semantics.
- `check()` runs `--version` and `--help`. Only successful probes are cached, keyed by resolved executable path and mtime. `run()` never probes.

### Public types

All value types are `@dataclass(frozen=True, slots=True, kw_only=True)`.

| Type | Fields |
|---|---|
| `Request` | v1 fields plus `idle_timeout_s: float \| None = None` and `raw_events: bool = False` |
| `Result` | `outcome`, `text`, `session_id`, `model`, `usage`, `cost_usd: float \| None`, `duration_s: float`, `num_turns: int \| None`, `stop_reason: str \| None`, `returncode`, `error: Diagnostic \| None`, `warnings: tuple[Diagnostic, ...]` |
| `Usage` | `input`, `output`, `cache_read`, `cache_write`, `context_used`, `context_window`, each `int \| None` |
| `Diagnostic` | `code: ErrorCode`, `message: str` |
| `Outcome` | `success`, `retryable`, `limit_reached`, `agent_fatal`, `request_fatal`, `cancelled` |
| `Capabilities` | `available: bool`, `executable: Path \| None`, `version: str \| None`, `structured_streaming: bool \| None` |

Field rules:

- `idle_timeout_s` follows the `timeout_s` rule: `None` or finite and positive.
- `Result.model` is the model the CLI reported. Codex reports none, so it is `None`.
- `Usage` fields are set only from values the CLI reports. Unknown stays `None`.
- `cost_usd` comes from Claude `total_cost_usd` and the sum of Pi `usage.cost.total`. Others are `None`.
- `num_turns` counts assistant turns: Claude `num_turns`, Pi `turn_end` events. Codex and Agy are `None`, because their turn counts mean something else.
- `stop_reason` is the native CLI value, unnormalized and nullable.
- `duration_s` is measured by the library.

### Events

`SCHEMA_VERSION = 2` is a module constant; events carry no version field. Every event has `sequence: int`, assigned by the base and strictly increasing per run, and `entity_id: str | None`, the CLI-native ID, unique within a run when present.

| Variant | `kind` | Fields |
|---|---|---|
| `SessionIdentified` | `session.identified` | `session_id` |
| `MessageStarted` | `assistant.message.started` | none |
| `TextDelta` | `assistant.text.delta` | `text` |
| `ReasoningDelta` | `assistant.reasoning.delta` | `text` |
| `MessageCompleted` | `assistant.message.completed` | none |
| `ToolStarted` | `tool.started` | `tool`, `activity: "command" \| "tool_call"`, `input` |
| `ToolCompleted` | `tool.completed` | `tool`, `status: "completed" \| "error" \| "unknown"`, `output: JsonValue \| None` |
| `FileChanged` | `file.changed` | `path`, `change: "added" \| "modified" \| "deleted" \| "unknown"`, `evidence: "reported" \| "inferred"` |
| `Notice` | `notice` | `code: str`, `message: str` |
| `UsageUpdated` | `usage.updated` | `usage` |
| `RawEvent` | `raw` | `stream: "stdout" \| "stderr"`, `data: dict[str, Any]` |
| `RunCompleted` | `run.completed` | `result` |

Rules:

- `UsageUpdated` is a full snapshot.
- `Notice` reports retries, compaction, warnings, and nonfatal CLI errors. It never changes the outcome.
- `RawEvent` is emitted only when `raw_events=True`, only for stdout JSON objects, before the normalized events of the same line. It is never logged.
- `FileChanged` is evidence from the CLI, not a filesystem audit.
- Exactly one `RunCompleted` per run, emitted by the base after cleanup.
- Consumers must ignore unknown kinds.

### Drivers

Every branch below that names an unverified shape is confirmed or removed using the recorded fixtures.

Claude:

- Transport argv: `-p --output-format stream-json --verbose --include-partial-messages`. Prompt through stdin.
- `text_delta` gives `TextDelta`; `thinking_delta` gives `ReasoningDelta`. Text duplicated between `assistant` and `stream_event` records is emitted once.
- `ToolStarted.input` comes from the `tool_use` block of the `assistant` message.
- `ToolCompleted` comes from `tool_result`, with `content` as output and `is_error` as status.
- `FileChanged` with `inferred` evidence comes from successful `Edit`, `Write`, `MultiEdit`, and `NotebookEdit`.
- `Bash` has activity `command`.
- The `result` record gives `total_cost_usd`, `num_turns`, and `stop_reason`. Subtype `error_max_turns` maps to `max_turns`; the budget subtype maps to `budget`. `api_error_status` maps to `auth` or `service_error`.

Codex:

- The `-o` temporary file and the session-ID regex over model text are removed. Session ID comes from `thread.started` only.

| Item type | Events |
|---|---|
| `agent_message` | `MessageStarted`, `TextDelta`, `MessageCompleted` |
| `reasoning` | `ReasoningDelta` |
| `command_execution` | `ToolStarted` with activity `command`; `ToolCompleted` with `aggregated_output` and status from `exit_code` |
| `file_change` | Tool events and one `FileChanged` with `reported` evidence per entry of `changes` |
| `mcp_tool_call`, `web_search` | Tool events with activity `tool_call` |
| `error` | `Notice` |
| `todo_list` | No normalized event |

- `turn.failed` and top-level `error` map to `Facts.error`.
- With `reasoning` set, only `-c model_reasoning_effort=...` in `args` is rejected.
- The placement of options relative to `resume` is set from the fixture.

Pi:

- Whether `-p` is required is set from the fixture.
- `message_update` gives `TextDelta` and `ReasoningDelta`.
- `tool_execution_start` and `tool_execution_end` give tool events with output.
- `usage.cost.total` gives cost; `stopReason` gives `stop_reason`.
- A later successful message clears an earlier structured error.
- Branches for `text_delta`, `tool_call`, `tool_result`, `summary`, `server_error`, and `message` are removed unless a fixture shows them.

Agy:

- The prompt moves to stdin through `--input-format stream-json` if the fixture proves the format. Otherwise the v1 argv delivery and its 100 KiB limit stay.
- Plain-text fallback output goes through `feed_text`.
- Tools are correlated by step index, and tool error status is read.
- The session ID log file is delivered through `read_back`.

Shared helpers:

- `as_int`, `parse_token_component`, and `_count` merge into one helper.
- Each driver owns its command-tool names.

## Errors and Edge Cases

### `ErrorCode`

| Group | Codes |
|---|---|
| Request | `bad_cwd`, `invalid_args`, `unsupported_option`, `invalid_prompt` |
| Spawn | `missing_cli`, `spawn_failed` |
| CLI | `auth`, `invalid_model`, `context_overflow`, `service_error` |
| Limit | `max_turns`, `budget` |
| Transport | `cancelled`, `timeout`, `idle_timeout`, `stdin_failed` |
| Execution | `execution_error`, `parser_error`, `no_output` |
| Warning | `no_session_id`, `line_too_long`, `cleanup_incomplete` |

### Outcome precedence

The first matching row wins.

| # | Condition | Outcome | Code |
|---|---|---|---|
| 1 | Latched stop cause is `cancelled` | `cancelled` | `cancelled` |
| 2 | Validation failure or `RequestError` | `request_fatal` | request codes |
| 3 | Executable missing or spawn denied | `agent_fatal` | `missing_cli`, `spawn_failed` |
| 4 | Auth failure | `agent_fatal` | `auth` |
| 5 | Invalid model or context overflow | `request_fatal` | `invalid_model`, `context_overflow` |
| 6 | Parser raised | `agent_fatal` | `parser_error` |
| 7 | CLI reports a caller-set limit | `limit_reached` | `max_turns`, `budget` |
| 8 | Latched stop cause is a deadline | `retryable` | `timeout`, `idle_timeout` |
| 9 | HTTP 5xx, overload, or rate limit | `retryable` | `service_error` |
| 10 | Stdin failure, nonzero exit, or structured failure | `retryable` | `stdin_failed`, `execution_error` |
| 11 | No text | `retryable` | `no_output` |
| 12 | Otherwise | `success` | none; a missing session ID adds `no_session_id` |

Rules:

- `Facts.error` is checked before keyword matching on `Facts.error_text` and the stderr tail. `classify` has no CLI names or native subtype checks.
- Row 7 precedes row 8 because a limit reported by the CLI is more specific than a deadline reached during shutdown.
- `timeout_s` is caller-set but stays `retryable`; this is documented.
- `retryable` means possibly transient, not safe to replay.

### Raised, not returned

- Exceptions from `on_event` or `on_spawn`, after cleanup.
- Caller task cancellation as `CancelledError`, after cleanup.
- Unknown driver name as `KeyError`.
- `run_sync` inside a running loop as `RuntimeError`.

Parser exceptions are returned as row 6 with partial text kept, because CLI format drift should not crash the caller and a retry gives the same result.

## Migration

- Release `0.2.0` through release-please with a `feat!` commit. No compatibility layer.
- README is rewritten for the v2 API and gains "Migrating from 0.1", listing every renamed, removed, and added public name for the Refract maintainer.
- README "Known limits" is corrected: Job Objects are used; the spawn-to-assignment window remains.
- The v1 plan in `docs/plans/coding-agent-cli/` stays unchanged.

## Testing

Order: record fixtures, then failing tests for the v1 defects, then implementation.

### Fixture recording

- Dev script `scripts/record_fixtures.py`, not shipped in the wheel.
- Runs in a fresh git repository under `mktemp -d` holding only synthetic files. Raw captures go to a mode `700` directory outside the repository and are never committed.
- Scenarios per CLI: plain answer, shell command, file edit, reasoning, invalid model, auth failure, cancel after the first delta, resume. Claude adds `--max-turns 1` and a tiny `--max-budget-usd`.
- Tool scenarios may pass permission flags inside the throwaway repository only. Drivers never add them.
- Auth-failure scenarios point the CLI config directory at an empty temporary directory and never touch real credentials.
- Each capture stores stdout and stderr separately, the exit code, `--version` output, and argv with the prompt replaced by `<PROMPT>`.
- The scrubber walks every JSON value and replaces home paths, username, hostname, `cwd`, emails, secret-named keys, and known secret prefixes. IDs are replaced through one consistent map so correlations survive.
- Layout: `tests/fixtures/recorded/<cli>/<version>/<scenario>.stdout.jsonl`, `<scenario>.stderr.txt`, and `meta.json` with expected outcome, code, and return code.
- A gate test fails if any fixture contains the username, `$HOME`, the hostname, an email, or a secret pattern. The fixture diff is reviewed by hand before commit.

### Suites

1. Parser replay, pure and without processes, parametrized over every recorded fixture: outcome and code match `meta.json`, every `ToolStarted` on a success fixture has a `ToolCompleted`, and truncating after any `TextDelta` still yields non-empty partial text.
2. Transport with the fake CLI emitting both streams: pipe saturation, idle timeout, oversized line, stalled `on_spawn`, latched cancel after exit, repeated cancel, cleanup deadline, descendants killed, temporary directory removed.
3. Classification precedence rows 1 to 12.
4. Custom driver subclassing `CliDriver` through the full lifecycle and event numbering.
5. Package test: wheel and sdist install, `py.typed`, zero runtime dependencies.
6. CI matrix as in v1, plus a Windows test of separate stderr with non-ASCII text.

Live tests keep the `live` marker and stay excluded by default.

## Risks and Open Questions

| Risk | Mitigation |
|---|---|
| Windows descendants spawned before Job assignment escape | Documented in Known limits |
| PowerShell shim re-encodes or buffers separate stderr | Windows CI test with non-ASCII output |
| Idle timeout fires during a long silent tool run | Off by default; documented to exceed the longest tool run |
| CLI output formats drift | Version-labelled fixtures; drift yields `parser_error` or `Notice`, not a crash |
| Recorded fixtures leak personal or account data | Scrubber, gate test, manual review, raw captures never committed |

Open, settled by the fixture recording; if a fixture cannot settle one, v1 behavior stays and the item is logged as debt:

- Agy `--input-format stream-json` message schema.
- Codex option placement around `resume`.
- Claude budget-exceeded subtype name and whether `--max-turns` is accepted.
- Pi non-interactive behavior without `-p`.
- Pi and Claude reasoning event shapes.
