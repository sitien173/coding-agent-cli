# Phase 3: Two-pipe supervised transport

## Goal & Outcome
- Plan Goal: Rebuild `coding-agent-cli` as 0.2.0 on a public `CliDriver` API. Full design: `/home/ngosi/projects/coding-agent-cli/docs/plans/coding-agent-cli-v2/DESIGN.md`. Full plan: `/home/ngosi/projects/coding-agent-cli/docs/plans/coding-agent-cli-v2/PLAN.md`, section "Phase 3".
- Phase Outcome: A standalone, tested `src/coding_agent_cli/_transport.py` implementing DESIGN.md "Transport", which Phase 4 plugs into. `_process.py` is untouched. The Windows Job Object gains `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`.

## Context & Architecture
- Code root: `/home/ngosi/projects/coding-agent-cli/.worktrees/coding-agent-cli-v2-phase-03` | Records root: `/home/ngosi/projects/coding-agent-cli` | Branch: `refract/coding-agent-cli-v2/phase-03` | Base: `refs/plans/coding-agent-cli-v2/phase-03/base`
- Prior State: v1 library at plan base `24b8cca`. Baseline: `ruff format --check` pass, `ruff check` pass, `pyright` 0 errors, `pytest` 505 passed 4 deselected. Phase 1 (fixtures, `scripts/`) and Phase 2 (types, events, classify, driver deletions) run in parallel in other worktrees; do not touch their files. Phase 2 changes `types.py`; so `_transport.py` must not import `Result`, `Outcome`, `Diagnostic`, `ErrorCode`, or `_classify`. Use plain strings or transport-local types for stop causes and signals.
- Code Context: read `src/coding_agent_cli/_process.py` (v1 transport: single merged pipe, 0.1 s `wait_for(readline)` polling, `_watch` task) and `src/coding_agent_cli/_platform.py` (POSIX process groups, Windows Job Object, safe `.cmd` to PowerShell `-File` launch at about line 45, Job Object creation at about line 94). Reuse `_platform.py` spawn and kill helpers. Existing fake CLIs live in `tests/fixtures/fake_cli/` (`emit_both.py`, `grandchild.py`, `long_line.py`, `many_lines.py`, `sleeper.py`, `spawn_and_sleep.py`, `echo_stdin.py`, `non_utf8.py`, ...); `tests/launchers.py` shows how tests launch them.

## Required design (DESIGN.md "Transport")
- Spawn: argv list, `shell=False`, stdout and stderr as separate pipes, stdin pipe when stdin bytes are given else `DEVNULL`. POSIX: new session and process group; terminate with SIGTERM to the group, then SIGKILL after a grace period. Windows: Job Object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, assigned right after spawn; keep the handle open for the run and close it at cleanup.
- Readers: stdout, stderr, and the stdin writer are separate tasks. Both pipes drain to EOF, including during cleanup. Stdout is read in 64 KiB chunks and split on `\n` with a scan offset; lines decode as UTF-8 with `errors="replace"` and lose the trailing `\r\n`/`\n`. A line longer than `max_line_bytes` (parameter) is discarded up to the next newline and sets a `line_too_long` signal; the run continues. Stderr is split into lines the same way and also kept as a byte-capped 64 KiB tail. Order within each stream is preserved; no ordering across streams.
- Consumer: the transport calls an async line callback `on_line(stream: Literal["stdout","stderr"], line: str)` from one dispatcher (one consumer at a time, never concurrently). It never parses JSON.
- Supervisor, no polling: one supervisor awaits the first of process exit, `cancel` (an `asyncio.Event`), wall deadline (`timeout_s`), idle deadline (`idle_timeout_s`). The idle deadline is rescheduled on any received bytes from either pipe and paused while the `on_line` callback is being awaited. `on_spawn(pid)` (async or sync callback) runs under the same supervisor, so a stalled `on_spawn` is bounded by cancel and the wall deadline. The stop cause is latched once to the first of `exited`, `cancelled`, `timeout`, `idle_timeout` and never changes; a cancel set after exit leaves `exited`.
- Stop and cleanup: after the leader exits, both pipes get one bounded wait for EOF, then the tree is killed. Cleanup has one total deadline; exceeding it sets `cleanup_incomplete`. An exception from `on_line` or `on_spawn` triggers cleanup, then re-raises. Caller task cancellation triggers cleanup, then re-raises `CancelledError`. A parser-side exception surfaced by `on_line` should be distinguishable: accept an exception type or flag so Phase 4 can map parser errors to a returned outcome; simplest is that the transport catches exceptions of a dedicated `ParserFailure` wrapper type (defined in `_transport.py`) and records its message, while re-raising any other exception after cleanup.
- Return value: a frozen transport outcome dataclass with stop cause, `returncode`, stderr tail (str), spawn failure (`missing_cli` or `spawn_failed`, plus message), stdin failure flag/message, parser failure message, `line_too_long`, `cleanup_incomplete`, and measured `duration_s`.
- A `finally` hook or callback for temporary-directory removal may be accepted, but Phase 4 owns the temp directory; keep it minimal.

## Invariants & Boundaries
- Preserve: standard library only; never log prompts, argv, or environment values; every exit path kills the process tree. Keep the safe `.cmd` to PowerShell `-File` handling in `_platform.py`.
- No `asyncio.sleep` polling of pipes or of the process; the only allowed polling is the existing POSIX process-group exit check in `_platform.py`, if still needed for descendant reaping.
- Every `await` that can block has a deadline or is cancelled by the supervisor.
- Out of Scope: do not modify `_process.py`, `_classify.py`, `types.py`, `events.py`, drivers, `api.py`, or `registry.py`. Do not push, tag, or commit.

## Files
Allowed production and test paths (relative to code root):
- `src/coding_agent_cli/_transport.py` (create)
- `src/coding_agent_cli/_platform.py` (modify: `KILL_ON_JOB_CLOSE`, any helper the transport needs)
- `tests/test_transport.py` (create)
- `tests/fixtures/fake_cli/<new>.py` (create: both-stream flood, silence then output, oversized line on stdout with a valid line after it, stalled leader with descendants, slow trickle for idle tests; reuse existing scripts where they already fit)
- `tests/launchers.py` (modify only if a helper is needed for new fake CLIs)

Worker-owned phase records:
- `/home/ngosi/projects/coding-agent-cli/docs/plans/coding-agent-cli-v2/phase-03/notes.md` (append `## Task <M>` blocks)
- `/home/ngosi/projects/coding-agent-cli/docs/plans/coding-agent-cli-v2/phase-03/journal.md` (append `# EXTERNAL RESPONSE` under `## Implementation Response`)

Read-only / Forbidden:
- Do not edit `PLAN.md`, `DESIGN.md`, `.handover.md`, `prompt.md`, other phases, dependencies, or secrets.

## Tasks
- task-1: Failing tests first, then readers and stdin writer: both pipes drain to EOF under saturation (several MiB on each pipe, slow consumer), a line over `max_line_bytes` is skipped with `line_too_long` and the next line is delivered, stderr tail capped at 64 KiB, stdin bytes delivered and a closed-stdin failure recorded.
- task-2: Failing tests first, then the supervisor: wall timeout, idle timeout reset by bytes on either pipe, idle paused during a slow `on_line` callback (callback slower than `idle_timeout_s` must not cause `idle_timeout`), cancel, repeated cancel, latched stop cause, cancel after exit staying `exited`, stalled `on_spawn` bounded by the wall deadline and by cancel, `on_line` exception re-raised after cleanup, caller cancellation re-raised after cleanup.
- task-3: Failing tests first, then cleanup: one total deadline, descendants killed (POSIX test with `grandchild.py` or a new stalled-leader script verifying the descendant PID is gone), `cleanup_incomplete` reported when the deadline passes (inject a tiny cleanup deadline or a stubborn child that ignores SIGTERM and holds a pipe), missing executable -> `missing_cli`. Add `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` to the Windows job with tests skipped off Windows.

## Done When
- Tests cover pipe saturation, oversized line, idle timeout, idle pause during a slow consumer, wall timeout, latched cancel, repeated cancel, stalled `on_spawn`, descendant kill, and cleanup deadline.
- `_transport.py` has no `asyncio.sleep` polling of pipes.
- Tests run fast: keep timeouts small (well under 2 s each) so the suite stays quick, and avoid flakiness by using events rather than sleeps in fake CLIs where possible.
- Run from code root, with `export VIRTUAL_ENV=;` first:
  - `uv run ruff format --check` (Baseline: pass)
  - `uv run ruff check` (Baseline: pass)
  - `uv run pyright` (Baseline: 0 errors)
  - `uv run pytest` (Baseline: 505 passed, 4 deselected; new tests add to it)

## Rules
Read and follow these bundled contracts:
- Contract: `/home/ngosi/.claude/plugins/cache/refract/refract/3.0.5/shared/worker-contract.md`
- Response: `/home/ngosi/.claude/plugins/cache/refract/refract/3.0.5/shared/erp.md`
- Notes: `/home/ngosi/.claude/plugins/cache/refract/refract/3.0.5/shared/notes-template.md`
- Journal: `/home/ngosi/.claude/plugins/cache/refract/refract/3.0.5/shared/journal-template.md`

Also follow the repository's `AGENTS.md` and `CONTRIBUTING.md` coding standard at the code root.

Role boundary: You are the phase implementer, not Coordinator. Edit code only in the code root; write phase records only under the records root. Leave Coordinator skills and Refract job submission to the Coordinator.

## Response Format
Append the complete `# EXTERNAL RESPONSE` block to `/home/ngosi/projects/coding-agent-cli/docs/plans/coding-agent-cli-v2/phase-03/journal.md`, then return that block and the matching status line from `erp.md`.
