# Changelog

## [0.2.0](https://github.com/sitien173/coding-agent-cli/compare/coding-agent-cli-v0.1.0...coding-agent-cli-v0.2.0) (2026-11-12)


### Features

* add coding-agent-cli 0.1.0 library for coding-agent CLIs ([c58b3c2](https://github.com/sitien173/coding-agent-cli/commit/c58b3c28d670646aea5bb48b0299d8bad48cf2b9))


### Bug Fixes

* **types:** narrow POSIX-only helpers so pyright passes on Windows ([24b8cca](https://github.com/sitien173/coding-agent-cli/commit/24b8ccad055fd506cf34a76e26864f18961e69e5))
* **windows:** kill orphaned grandchildren through a job object ([3318a7f](https://github.com/sitien173/coding-agent-cli/commit/3318a7feaa63dc3415230bd06034453895710d90))


### Documentation

* add open-source contributor docs, policies, and GitHub templates ([eb47779](https://github.com/sitien173/coding-agent-cli/commit/eb47779a7070cb4014b0aa463341d14a30ab2094))
* **plan:** add coding-agent-cli design and plan ([f9dbe0c](https://github.com/sitien173/coding-agent-cli/commit/f9dbe0cca0ff869511b2bc11b1e5f2f0aaad8553))

## 0.1.0

First release. Extracted from the CLI-driver layer of Refract.

- `run`, `stream`, `run_sync`, and `check` entry points for running coding-agent CLIs from Python.
- Typed events with a strictly increasing `sequence`, ending with exactly one `RunCompleted`.
- A classified `Result` with `Outcome` and `Diagnostic`.
- Built-in drivers for `claude`, `pi`, `codex`, and `agy`, and a `DriverRegistry` for custom drivers.
- Process-tree cleanup on completion, cancel, timeout, and callback failure.
- No permission or approval flag is ever added; callers pass them through `args`.
