# AGENTS.md

Guidance for AI coding agents working in this repository. Humans should read [CONTRIBUTING.md](CONTRIBUTING.md), which this file follows.

## Project

`coding-agent-cli` is a stdlib-only Python 3.12+ library. It runs coding-agent CLIs (`claude`, `codex`, `pi`, `agy`) as subprocesses, streams typed events, and returns a classified `Result`.

## Layout

| Path | Contents |
|---|---|
| `src/coding_agent_cli/api.py` | Public entry points: `run`, `stream`, `run_sync`, `check` |
| `src/coding_agent_cli/types.py`, `events.py` | Public frozen dataclasses |
| `src/coding_agent_cli/registry.py` | `Driver` protocol and `DriverRegistry` |
| `src/coding_agent_cli/_process.py`, `_platform.py` | Subprocess transport and process-tree kill |
| `src/coding_agent_cli/_args.py`, `_classify.py` | Argument validation and outcome classification |
| `src/coding_agent_cli/_drivers/` | Built-in drivers and shared `CliDriver` helpers |
| `tests/fixtures/fake_cli/` | Fake CLIs used by all unit tests |
| `docs/plans/coding-agent-cli/` | Design, plan, and phase journals |

## Commands

Prefix uv commands with `export VIRTUAL_ENV=;` if a foreign virtualenv is active.

```bash
uv sync
uv run ruff format && uv run ruff check --fix
uv run pyright
uv run pytest
```

All four must pass before you finish. Do not run `pytest -m live` unless asked; it calls real CLIs and spends tokens.

## Hard rules

- Never add a runtime dependency.
- Never make a driver add a permission, approval, or sandbox-bypass flag.
- Never log prompts, argv, or environment values.
- Every exit path must kill the process tree and delete temp files.
- Reuse helpers in `_drivers/_common.py` and `_args.py` before writing new ones.
- Keep public signatures in sync with the README examples.
- Do not push, tag, or publish.

## Style

Follow the coding standard in [CONTRIBUTING.md](CONTRIBUTING.md#coding-standard). Match the surrounding code. Keep diffs minimal and add a test for every behavior change.
