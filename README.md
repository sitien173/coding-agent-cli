# coding-agent-cli

[![CI](https://github.com/sitien173/coding-agent-cli/actions/workflows/ci.yml/badge.svg)](https://github.com/sitien173/coding-agent-cli/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/coding-agent-cli)](https://pypi.org/project/coding-agent-cli/)
[![Python](https://img.shields.io/pypi/pyversions/coding-agent-cli)](https://pypi.org/project/coding-agent-cli/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

Run coding-agent CLIs from Python, stream typed events, and get a classified result.

Built-in drivers: `claude`, `codex`, `pi`, and `agy`. Python 3.12 or later on Linux, macOS, and Windows. No runtime dependencies. The CLIs themselves are not bundled; install and authenticate each CLI you use.

## Install

```bash
pip install coding-agent-cli
```

## Quick start

```python
import asyncio
from pathlib import Path

from coding_agent_cli import Request, run


async def main() -> None:
    result = await run(Request(driver="claude", prompt="Summarize README.md", cwd=Path.cwd()))
    print(result.outcome, result.text)


asyncio.run(main())
```

## API

The public entry points have these signatures:

```python
async def run(
    req: Request,
    *,
    on_event: Callable[[Event], Awaitable[None] | None] | None = None,
    cancel: asyncio.Event | None = None,
    on_spawn: Callable[[int], Awaitable[None] | None] | None = None,
    registry: DriverRegistry | None = None,
) -> Result: ...


def stream(
    req: Request,
    *,
    cancel: asyncio.Event | None = None,
    on_spawn: Callable[[int], Awaitable[None] | None] | None = None,
    registry: DriverRegistry | None = None,
) -> EventStream: ...


def run_sync(
    req: Request,
    *,
    on_event: Callable[[Event], None] | None = None,
    cancel: threading.Event | None = None,
    registry: DriverRegistry | None = None,
) -> Result: ...


async def check(
    driver: str,
    *,
    executable: str | None = None,
    env: Mapping[str, str] | None = None,
    refresh: bool = False,
    registry: DriverRegistry | None = None,
) -> Capabilities: ...
```

`run` and `stream` are asynchronous. `run` accepts a sync or async event callback. `stream` is an async iterator; use it as an async context manager so leaving the block closes the run. Callback exceptions propagate after process cleanup. Unknown driver names raise `KeyError`.

```python
import asyncio
from pathlib import Path

from coding_agent_cli import MessageCompleted, Request, RunCompleted, TextDelta, stream


async def main() -> None:
    req = Request(driver="codex", prompt="List the tests", cwd=Path.cwd())
    async with stream(req) as events:
        async for event in events:
            if isinstance(event, TextDelta):
                print(event.text, end="")
            elif isinstance(event, MessageCompleted):
                print()
            elif isinstance(event, RunCompleted):
                print(event.result.outcome)


asyncio.run(main())
```

`run_sync` is for synchronous applications and raises `RuntimeError` inside an active event loop. Its `threading.Event` cancellation signal can be set from another thread:

```python
import threading
from pathlib import Path

from coding_agent_cli import Request, run_sync

cancel = threading.Event()
request = Request(driver="pi", prompt="Inspect this project", cwd=Path.cwd())
# Pass cancel to run_sync and call cancel.set() from another thread to stop the run.
result = run_sync(request, cancel=cancel)
```

`check(driver, ...)` probes `--version` and `--help` and returns `Capabilities(available, executable, version, structured_streaming)`. Successful probe results are cached by executable path and modification time. `run` does not probe first.

## Request and Result

`Request` is frozen and keyword-only:

| Field | Type | Meaning |
|---|---|---|
| `driver` | `str` | Registered driver name: `claude`, `codex`, `pi`, or `agy`. |
| `prompt` | `str` | Prompt text. |
| `cwd` | `Path` | Working directory for the CLI. |
| `session_id` | `str \| None` | Resume a CLI session when supported. |
| `model` | `str \| None` | Portable model option when supported. |
| `reasoning` | `str \| None` | Portable reasoning or effort option when supported. |
| `system_prompt` | `str \| None` | Portable system prompt when supported. |
| `args` | `tuple[str, ...]` | Additional CLI arguments passed through unchanged. Each driver decides their position; some drivers append the prompt or session arguments after them. |
| `env` | `Mapping[str, str]` | Environment overlay on the inherited environment. |
| `timeout_s` | `float \| None` | Positive finite wall-clock limit, or `None` for no limit. |
| `idle_timeout_s` | `float \| None` | Positive finite idle limit, reset by process output, or `None` for no limit. |
| `raw_events` | `bool` | Emit parsed JSON stdout lines as `RawEvent` events. |
| `executable` | `str \| None` | Executable path or name overriding the driver's default. |

`Result` is frozen and keyword-only. Its fields are `outcome: Outcome`, `text: str`, `session_id: str | None`, `model: str | None`, `usage: Usage`, `cost_usd: float | None`, `duration_s: float`, `num_turns: int | None`, `stop_reason: str | None`, `returncode: int | None`, `error: Diagnostic | None`, and `warnings: tuple[Diagnostic, ...]`. `text` is the parser's reported final text when the CLI provides one, otherwise the accumulated text of the last assistant message. It is never the concatenation of every assistant turn. Unknown reported values remain `None`. Codex does not report a model, so its `Result.model` is `None`.

## Events

Each event has an increasing `sequence` and optional CLI-native `entity_id`; the `entity_id` is `None` when the CLI provides no native identifier. Events have no `schema_version` field. The module constant `SCHEMA_VERSION` is `2`. A run that returns a `Result` emits one final `RunCompleted` after cleanup. If cleanup misses its deadline, the result carries a `cleanup_incomplete` warning and termination is not confirmed. If `on_event` or `on_spawn` raises, or the calling task is cancelled, the library cleans up and re-raises without emitting `RunCompleted`.

| Event | `kind` | Additional fields |
|---|---|---|
| `SessionIdentified` | `session.identified` | `session_id` |
| `MessageStarted` | `assistant.message.started` | none |
| `TextDelta` | `assistant.text.delta` | `text` |
| `ReasoningDelta` | `assistant.reasoning.delta` | `text` |
| `MessageCompleted` | `assistant.message.completed` | none |
| `ToolStarted` | `tool.started` | `tool`, `activity` (`command` or `tool_call`), `input` |
| `ToolCompleted` | `tool.completed` | `tool`, `status` (`completed`, `error`, or `unknown`), `output` |
| `FileChanged` | `file.changed` | `path`, `change` (`added`, `modified`, `deleted`, or `unknown`), `evidence` (`reported` or `inferred`) |
| `Notice` | `notice` | `code`, `message` |
| `UsageUpdated` | `usage.updated` | `usage` |
| `RawEvent` | `raw` | `stream`, `data` |
| `RunCompleted` | `run.completed` | `result` |

## Outcomes and errors

| `Outcome` | Meaning |
|---|---|
| `success` | The CLI completed successfully. |
| `retryable` | The run may succeed later, for example after a timeout, service error, nonzero exit, or missing output. |
| `limit_reached` | A CLI-reported turn or budget limit stopped the run. |
| `agent_fatal` | The CLI cannot run here, such as when it is missing or authentication fails. |
| `request_fatal` | The request is invalid or unsupported. |
| `cancelled` | The caller cancelled the run. |

`ErrorCode` values are `bad_cwd`, `invalid_args`, `unsupported_option`, `invalid_prompt`, `missing_cli`, `spawn_failed`, `auth`, `invalid_model`, `context_overflow`, `service_error`, `max_turns`, `budget`, `cancelled`, `timeout`, `idle_timeout`, `stdin_failed`, `execution_error`, `parser_error`, `no_output`, `no_session_id`, `line_too_long`, and `cleanup_incomplete`. `Result.error` is a `Diagnostic(code, message)` when the run has a primary error; `Result.warnings` contains non-fatal diagnostics.

`retryable` means possibly transient, not safe to replay. The agent may already have edited files, run commands, or spent tokens. Inspect the working tree before sending the same prompt again.

## CLI arguments and permissions

Drivers do not add permission, approval, or sandbox-bypass flags. Callers pass any CLI-specific permission flags through `Request.args`:

| CLI | Example flag |
|---|---|
| Claude Code | `--permission-mode bypassPermissions` |
| Codex | `--dangerously-bypass-approvals-and-sandbox` |
| Pi | No automatic permission flag; `--approve` trusts project-local files and is not a tool-permission bypass. |
| Agy | `--dangerously-skip-permissions` |

Only use unattended permission flags in an environment you are prepared to lose. Portable `model`, `reasoning`, and `system_prompt` options are supported selectively; unsupported options and conflicts with `args` are classified as request errors.

## Custom drivers

Subclass `CliDriver` and implement `launch` and `parser`. `Launch` describes argv, optional stdin, and files to read back after the run. `Parser` converts JSON or text lines into events and final `Facts`:

```python
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Literal

from coding_agent_cli import CliDriver, DriverRegistry, Event, Facts, Launch, Parser, Request


class ExampleParser:
    def feed_json(self, obj: dict[str, Any]) -> Iterable[Event]:
        return ()

    def feed_text(self, line: str, *, stream: Literal["stdout", "stderr"]) -> Iterable[Event]:
        return ()

    def finish(self, read_back: Mapping[Path, bytes]) -> Facts:
        return Facts()


class ExampleDriver(CliDriver):
    name = "example"
    executable = "example-cli"

    def launch(self, req: Request, exe: Path, tmp: Path) -> Launch:
        return Launch(argv=(str(exe), "--prompt", req.prompt))

    def parser(self, req: Request) -> Parser:
        return ExampleParser()


registry = DriverRegistry()
registry.register("example", ExampleDriver())
```

`CliDriver` owns process supervision, sequencing, classification, and cleanup. A driver must not add permission flags on behalf of a caller.

## Migrating from 0.1

The v2 API has no compatibility layer.

- **Renamed:** `Driver` is now `CliDriver`, an abstract base class. Implement `launch()` and `parser()` instead of `check()` and `run()`. `check()` is now a module function taking a driver name and optional `executable`, `env`, `refresh`, and `registry` keywords.
- **Removed:** `EventHandler` and `SpawnHandler` public aliases. Their callback types appear directly in the `run`, `stream`, and `run_sync` signatures. `Driver` is no longer exported; use `CliDriver`.
- **Added public names:** `CliDriver`, `ErrorCode`, `Facts`, `FileChanged`, `JsonValue`, `Launch`, `Notice`, `Parser`, `RawEvent`, `RequestError`, and `ReasoningDelta`.
- **Outcomes and errors:** `Outcome.LIMIT_REACHED` is new for turn and budget limits, which 0.1 reported as retryable. `Diagnostic.code` is an `ErrorCode` instead of a free-form `str`.
- **Fields added:** `ToolCompleted.tool` and `ToolCompleted.output`; `Result.cost_usd`, `Result.duration_s`, `Result.num_turns`, and `Result.stop_reason`; `Capabilities.version`.
- **Event schema:** `schema_version` is no longer stored on each event. Import the `SCHEMA_VERSION` module constant instead. `entity_id` contains the CLI-native identifier or `None` when unavailable.
- **Result behavior:** `Result.text` is the parser's final text when reported, otherwise the last assistant message, rather than the concatenation of all assistant messages. Codex reports `Result.model` as `None`.
- **Removed behavior:** codex rollout-file parsing is removed; Codex output is parsed from its structured stream.
- **Registry:** `DriverRegistry.register` accepts only `CliDriver` instances; structurally compatible objects are rejected.
- **Callbacks and cancel:** `run` accepts synchronous or awaitable `on_event` and `on_spawn` callbacks. `run_sync` now accepts `on_event` and a `threading.Event` for `cancel`.
- **Request:** `idle_timeout_s` and `raw_events` are available in addition to the 0.1 request fields.

## Known limits

- On Windows, Job Object assignment happens immediately after process spawn. In the short window before assignment, the process can exit or spawn descendants; descendants created in that window are outside the job and can survive process-tree cleanup. If creating, configuring, or assigning the Job Object fails, the library silently falls back to `taskkill /T`, which cannot reach a descendant whose parent already exited; no `cleanup_incomplete` warning is reported in that case.
- Agy receives the prompt on stdin through `--input-format stream-json`, and agy rejects CLI-answered slash-command prompts such as `/help` in that mode. The run then returns `request_fatal` with code `invalid_prompt`.
- Agy output that mixes structured JSON events and plain text on stdout is unsupported.
- Set `idle_timeout_s` longer than the longest tool command, because a quiet but still-running command can reach the idle timeout.

## Contributing

Contributions are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) for setup, the coding standard, and the pull request process. Report security problems privately as described in [SECURITY.md](SECURITY.md). Everyone taking part follows the [Code of Conduct](CODE_OF_CONDUCT.md).

```bash
uv sync
uv run ruff format --check && uv run ruff check
uv run pyright
uv run pytest
```

## License

[MIT](LICENSE)

## Provenance

This library is extracted from the CLI-driver layer of [Refract](https://github.com/sitien173/refract), a multi-model coordination tool. Its drivers, process handling, and many of its tests are ported from Refract under the MIT license.
