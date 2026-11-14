# Changelog

## [0.2.0](https://github.com/sitien173/coding-agent-cli/compare/v0.1.0...v0.2.0) (2026-11-14)


### ⚠ BREAKING CHANGES

* rebuild on public CliDriver API with recorded CLI fixtures

### Features

* rebuild on public CliDriver API with recorded CLI fixtures ([3a5de96](https://github.com/sitien173/coding-agent-cli/commit/3a5de960c13d4da99791d02f3ef28cfc61671713))


### Bug Fixes

* **scripts:** narrow recorder process-group kill so pyright passes on Windows ([b1dd2a0](https://github.com/sitien173/coding-agent-cli/commit/b1dd2a00d098220c316945091fc568f7dfb63da3))

## 0.1.0

First release. Extracted from the CLI-driver layer of Refract.

- `run`, `stream`, `run_sync`, and `check` entry points for running coding-agent CLIs from Python.
- Typed events with a strictly increasing `sequence`, ending with exactly one `RunCompleted`.
- A classified `Result` with `Outcome` and `Diagnostic`.
- Built-in drivers for `claude`, `pi`, `codex`, and `agy`, and a `DriverRegistry` for custom drivers.
- Process-tree cleanup on completion, cancel, timeout, and callback failure.
- No permission or approval flag is ever added; callers pass them through `args`.
