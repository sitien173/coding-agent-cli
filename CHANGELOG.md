# Changelog

## [0.2.2](https://github.com/sitien173/coding-agent-cli/compare/v0.2.1...v0.2.2) (2026-11-15)


### Bug Fixes

* **agy:** classify slash-command rejection as request_fatal ([3c8b94a](https://github.com/sitien173/coding-agent-cli/commit/3c8b94aa7bb0bbf4e7a43b28c172d2336e6c263d))
* **agy:** send the prompt on stdin to remove the 100 KiB limit ([e1ddf81](https://github.com/sitien173/coding-agent-cli/commit/e1ddf816267cb52543fed6c52495999148eaa52c))

## [0.2.1](https://github.com/sitien173/coding-agent-cli/compare/v0.2.0...v0.2.1) (2026-11-15)


### Bug Fixes

* **codex:** place options before the resume subcommand ([f401d23](https://github.com/sitien173/coding-agent-cli/commit/f401d2379c008f95a4a51268c1734b81f769ace3))

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
