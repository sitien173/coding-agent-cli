# Changelog

## 0.1.0

First release. Extracted from the CLI-driver layer of Refract.

- `run`, `stream`, `run_sync`, and `check` entry points for running coding-agent CLIs from Python.
- Typed events with a strictly increasing `sequence`, ending with exactly one `RunCompleted`.
- A classified `Result` with `Outcome` and `Diagnostic`.
- Built-in drivers for `claude`, `pi`, `codex`, and `agy`, and a `DriverRegistry` for custom drivers.
- Process-tree cleanup on completion, cancel, timeout, and callback failure.
- No permission or approval flag is ever added; callers pass them through `args`.
