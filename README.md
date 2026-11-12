# coding-agent-cli

Run coding-agent CLIs from Python, stream typed events, and get a classified result.

Built-in drivers: `claude`, `codex`, `pi`, and `agy`. Python 3.12 or later on Linux, macOS, and Windows. No runtime dependencies.

## Install

```bash
pip install coding-agent-cli
```

The CLIs themselves are not bundled. Install and log in to each one you use.

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

### Streaming events

Use `stream` inside `async with`. Leaving the block, by `break` or by an exception, cancels the run and kills the process tree.

```python
from coding_agent_cli import RunCompleted, TextDelta, stream


async def main() -> None:
    async with stream(Request(driver="codex", prompt="List the tests", cwd=Path.cwd())) as events:
        async for event in events:
            if isinstance(event, TextDelta):
                print(event.text, end="")
            elif isinstance(event, RunCompleted):
                print("\n", event.result.outcome)
```

`run` also accepts `on_event`, an async callback that receives the same events. An exception raised by `on_event` is raised from `run` after the process tree is killed.

### Synchronous use

```python
from coding_agent_cli import run_sync

result = run_sync(Request(driver="pi", prompt="Hello", cwd=Path.cwd()))
```

`run_sync` raises `RuntimeError` when called inside a running event loop. Await `run` there instead.

### Checking a CLI

```python
from coding_agent_cli import check

caps = await check(Request(driver="agy", prompt="", cwd=Path.cwd()))
print(caps.available, caps.executable, caps.structured_streaming)
```

## Request

| Field | Meaning |
|---|---|
| `driver` | Driver name, such as `claude`. |
| `prompt` | The prompt text. |
| `cwd` | Working directory for the CLI. |
| `session_id` | Resume this CLI session. |
| `model` | Portable model option. |
| `reasoning` | Portable reasoning or effort option. |
| `system_prompt` | Portable system prompt option. |
| `args` | Extra CLI arguments, appended by the driver. |
| `env` | Variables layered over the inherited environment. |
| `timeout_s` | Wall-clock limit in seconds. |
| `executable` | Path or name of the CLI to run instead of the default. |

## Portable options

| Option | claude | codex | pi | agy |
|---|---|---|---|---|
| `model` | `--model` | `--model` | `--model` | `--model` |
| `reasoning` | `--effort` | `-c model_reasoning_effort=<value>` | `--thinking` | unsupported |
| `system_prompt` | `--system-prompt` | unsupported | `--system-prompt` | unsupported |

An unsupported option returns a `request_fatal` result with code `unsupported_option`. Setting a portable option and the same flag in `args` returns `invalid_args`. So does passing a flag the driver owns for its transport, such as `--output-format`.

## Permission and approval flags

The drivers never add a permission, approval, or sandbox-bypass flag. A run that needs tools without prompts must pass the flag through `args`:

| CLI | Flag for unattended runs | Notes |
|---|---|---|
| claude | `--permission-mode bypassPermissions` | `--dangerously-skip-permissions` is equivalent. |
| codex | `--dangerously-bypass-approvals-and-sandbox` | Also accepted as `--yolo`. Use only inside an external sandbox. |
| pi | none | `--approve` trusts project-local files for the run. It is not a tool-permission bypass. |
| agy | `--dangerously-skip-permissions` | Auto-approves all tool permission requests. |

```python
Request(driver="codex", prompt="Fix the failing test", cwd=repo, args=("--dangerously-bypass-approvals-and-sandbox",))
```

These flags give the agent full control of the machine it runs on. Use them only in an environment you are prepared to lose.

## Events

Every event has a `kind`, a `sequence` that strictly increases within a run, an optional `entity_id`, and a `schema_version`. Each run ends with exactly one `RunCompleted`, sent after the process tree is gone.

| Class | `kind` | Fields |
|---|---|---|
| `SessionIdentified` | `session.identified` | `session_id` |
| `MessageStarted` | `assistant.message.started` | none |
| `TextDelta` | `assistant.text.delta` | `text` |
| `MessageCompleted` | `assistant.message.completed` | none |
| `ToolStarted` | `tool.started` | `tool`, `activity`, `input` |
| `ToolCompleted` | `tool.completed` | `status` |
| `UsageUpdated` | `usage.updated` | `usage` |
| `RunCompleted` | `run.completed` | `result` |

## Outcomes

| `Outcome` | Meaning |
|---|---|
| `success` | The CLI finished and returned text. |
| `retryable` | Possibly transient: timeout, service error, nonzero exit, or no output. |
| `agent_fatal` | The CLI cannot run here: missing CLI, spawn failure, or authentication. |
| `request_fatal` | The request itself is wrong: bad arguments, unsupported option, invalid model, or context overflow. |
| `cancelled` | The caller cancelled the run. |

`Result.error` holds a `Diagnostic` with a `code` and `message`. `Result.warnings` holds non-fatal diagnostics.

**Retryable is not replay-safe.** A `retryable` run may already have edited files, run commands, or spent tokens. Check the working tree before you send the same prompt again.

## Cancellation

Pass an `asyncio.Event` as `cancel`. Setting it ends the run with a `cancelled` result that keeps any partial text. From another thread, set it through the loop:

```python
cancel = asyncio.Event()
loop = asyncio.get_running_loop()
# in the other thread:
loop.call_soon_threadsafe(cancel.set)
```

## Custom drivers

A driver implements the `Driver` protocol: `async check(req, *, refresh=False) -> Capabilities` and `async run(req, *, on_event, cancel, on_spawn) -> Result`. Register it in a `DriverRegistry` and pass the registry to any entry point:

```python
from coding_agent_cli import DriverRegistry

registry = DriverRegistry()
registry.register("my-cli", MyDriver())
result = await run(Request(driver="my-cli", prompt="Hi", cwd=Path.cwd()), registry=registry)
```

`DriverRegistry(builtins=False)` starts empty. An unknown driver name raises `KeyError`.

## Known limits

- **Windows process trees.** Cleanup uses `taskkill /T /F`. A descendant whose launcher exits first can be missed. Job Objects are not used in 0.1.
- **Live behavior.** The test suite uses fake CLIs. The live tests in `tests/test_live.py` run the real CLIs and are excluded by default: `uv run pytest -m live -rP`.

## Development

```bash
uv sync --locked
uv run pytest
uv run pyright
```

## Provenance

This library is extracted from the CLI-driver layer of [Refract](https://github.com/sitien173/refract), a multi-model coordination tool. Its drivers, process handling, and many of its tests are ported from Refract under the MIT license.
