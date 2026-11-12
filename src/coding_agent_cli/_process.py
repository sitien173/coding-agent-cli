"""Asyncio subprocess transport for agent CLIs."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

from ._platform import kill_process_tree, prepare_command, process_group_kwargs

log = logging.getLogger("coding_agent_cli.process")

TAIL_BYTES = 64 * 1024
LINE_LIMIT_BYTES = 1024 * 1024
EXIT_GRACE_S = 2.0
POLL_INTERVAL_S = 0.1
CLEANUP_S = 10.0

LineHandler = Callable[[str], Awaitable[None]]
SpawnHandler = Callable[[int], Awaitable[None]]


@dataclass(slots=True)
class ProcessOutcome:
    """Exit status, newest output tail, and the failure that ended the run, if any."""

    returncode: int | None = None
    tail: str = ""
    error: str = ""
    error_class: str = ""
    spawned: bool = False


class OutputTail:
    """Newest lines within ``TAIL_BYTES``. The newest line is always kept, even when larger."""

    def __init__(self) -> None:
        self._lines: deque[tuple[str, int]] = deque()
        self._size = 0

    def append(self, line: str) -> None:
        size = len(line.encode("utf-8")) + 1
        self._lines.append((line, size))
        self._size += size
        while self._size > TAIL_BYTES and len(self._lines) > 1:
            self._size -= self._lines.popleft()[1]

    def text(self) -> str:
        return "\n".join(line for line, _ in self._lines)


async def _write_stdin(stdin: asyncio.StreamWriter, payload: bytes) -> None:
    stdin.write(payload)
    await stdin.drain()
    stdin.close()


async def _discard_until_eof(stdout: asyncio.StreamReader) -> None:
    """Drain the killed group's leftover output so asyncio can close the pipe transport."""
    try:
        while await asyncio.wait_for(stdout.read(64 * 1024), EXIT_GRACE_S):
            pass
    except (TimeoutError, OSError):
        pass


def _stdin_failure(writer: asyncio.Future[None] | None) -> BaseException | None:
    if writer is None or not writer.done() or writer.cancelled():
        return None
    return writer.exception()


def _stdin_outcome(failure: BaseException) -> tuple[str, str]:
    return f"stdin write failed: {type(failure).__name__}: {failure}", "stdin_failed"


def _expired(deadline: float | None, now: float) -> bool:
    return deadline is not None and now >= deadline


def _stop_reason(
    now: float,
    deadline: float | None,
    cancel: asyncio.Event | None,
    writer: asyncio.Future[None] | None,
    outcome: ProcessOutcome,
) -> str:
    """Return the first stop reason in check order: cancel, deadline, stdin failure. Empty means keep going."""
    if cancel is not None and cancel.is_set():
        return "cancelled"
    if _expired(deadline, now):
        return "timeout"
    failure = _stdin_failure(writer)
    if failure is None:
        return ""
    outcome.error, outcome.error_class = _stdin_outcome(failure)
    return "failed"


def _note_exit(process: asyncio.subprocess.Process, exited_at: float | None, now: float) -> float | None:
    """Return when the leader was first seen exited, or ``exited_at`` unchanged while it still runs."""
    if process.returncode is not None and exited_at is None:
        return now
    return exited_at


async def _read_one(
    stdout: asyncio.StreamReader,
    tail: OutputTail,
    on_line: LineHandler,
    outcome: ProcessOutcome,
) -> str:
    """Read and handle one line. Return "" to keep looping, "eof" at end of output, or "failed"."""
    try:
        raw = await asyncio.wait_for(stdout.readline(), POLL_INTERVAL_S)
    except TimeoutError:
        return ""
    except ValueError:
        outcome.error, outcome.error_class = "output line exceeds 1 MiB", "execution_error"
        return "failed"
    if not raw:
        return "eof"
    line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
    tail.append(line)
    try:
        await on_line(line)
    except Exception as exc:  # noqa: BLE001 - a parser or callback failure fails the attempt
        outcome.error, outcome.error_class = f"{type(exc).__name__}: {exc}", "execution_error"
        return "failed"
    return ""


async def _pump(
    process: asyncio.subprocess.Process,
    writer: asyncio.Future[None] | None,
    deadline: float | None,
    cancel: asyncio.Event | None,
    on_line: LineHandler,
    tail: OutputTail,
    outcome: ProcessOutcome,
) -> str:
    """Poll the child until a stop reason, leader exit plus grace, or EOF. Return the stop reason or ""."""
    assert process.stdout is not None
    exited_at: float | None = None
    eof = False
    while True:
        now = time.monotonic()
        stop = _stop_reason(now, deadline, cancel, writer, outcome)
        if stop:
            return stop
        exited_at = _note_exit(process, exited_at, now)
        if exited_at is not None and (eof or now - exited_at >= EXIT_GRACE_S):
            if writer is not None:
                return await _finish_stdin(writer, outcome)
            return ""
        if eof:
            await asyncio.sleep(POLL_INTERVAL_S)
            continue
        step = await _read_one(process.stdout, tail, on_line, outcome)
        if step == "failed":
            return step
        eof = step == "eof"


async def _watch(deadline: float | None, cancel: asyncio.Event | None) -> str:
    """Return "cancelled" or "timeout" when one fires, independent of the line handler."""
    event = cancel if cancel is not None else asyncio.Event()
    timeout = None if deadline is None else max(0.0, deadline - time.monotonic())
    try:
        async with asyncio.timeout(timeout):
            await event.wait()
    except TimeoutError:
        return "timeout"
    return "cancelled"


async def _finish_stdin(writer: asyncio.Future[None], outcome: ProcessOutcome) -> str:
    """Give the stdin writer ``EXIT_GRACE_S`` to finish. Return "failed" when it failed, else ""."""
    await asyncio.wait({writer}, timeout=EXIT_GRACE_S)
    failure = _stdin_failure(writer)
    if failure is None:
        return ""
    outcome.error, outcome.error_class = _stdin_outcome(failure)
    return "failed"


def _map_stop(outcome: ProcessOutcome, stop: str, name: str) -> None:
    """Set the final error from the stop reason, or from a nonzero exit when nothing stopped the run."""
    if stop == "cancelled":
        outcome.error, outcome.error_class = "command cancelled", "cancelled"
    elif stop == "timeout":
        outcome.error, outcome.error_class = f"{name} subprocess timed out", "timeout"
    elif not stop and outcome.returncode:
        outcome.error = f"command exited with status {outcome.returncode}"
        outcome.error_class = "execution_error"


async def _spawn(
    command: list[str],
    payload: bytes | None,
    env: Mapping[str, str] | None,
    cwd: str | None,
) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.DEVNULL if payload is None else asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=cwd,
        env=None if env is None else {**os.environ, **env},
        limit=LINE_LIMIT_BYTES,
        **process_group_kwargs(),
    )


async def _supervise(
    process: asyncio.subprocess.Process,
    writer: asyncio.Future[None] | None,
    deadline: float | None,
    cancel: asyncio.Event | None,
    on_line: LineHandler,
    tail: OutputTail,
    outcome: ProcessOutcome,
) -> str:
    """Run the pump while an independent watcher enforces cancel and the deadline."""
    pump = asyncio.ensure_future(_pump(process, writer, deadline, cancel, on_line, tail, outcome))
    watcher = asyncio.ensure_future(_watch(deadline, cancel))
    try:
        await asyncio.wait({pump, watcher}, return_when=asyncio.FIRST_COMPLETED)
        if pump.done():
            return pump.result()
        return watcher.result()
    finally:
        for task in (pump, watcher):
            if not task.done():
                task.cancel()
        await asyncio.gather(pump, watcher, return_exceptions=True)


async def _cleanup(process: asyncio.subprocess.Process, writer: asyncio.Future[None] | None) -> None:
    """Kill the tree, reap the leader, and drain output, all within ``CLEANUP_S``."""
    assert process.stdout is not None
    if writer is not None and not writer.done():
        writer.cancel()
    try:
        async with asyncio.timeout(CLEANUP_S):
            await kill_process_tree(process.pid)
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            await process.wait()
            if process.stdin is not None:
                process.stdin.close()
            await _discard_until_eof(process.stdout)
    except TimeoutError:
        log.warning("process cleanup exceeded %.1fs pid=%d", CLEANUP_S, process.pid)


async def _shielded(cleanup: asyncio.Future[None]) -> None:
    """Await ``cleanup`` to completion even when the caller is cancelled, then re-raise the cancellation."""
    cancelled = False
    while not cleanup.done():
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            if cleanup.done():
                break
            cancelled = True
    if cancelled:
        raise asyncio.CancelledError


async def run_process(
    argv: list[str],
    stdin: str | None,
    env: Mapping[str, str] | None,
    cwd: str | None,
    deadline: float | None,
    cancel: asyncio.Event | None,
    on_line: LineHandler,
    *,
    on_spawn: SpawnHandler | None = None,
) -> ProcessOutcome:
    """Run ``argv`` in a new process group and await ``on_line`` for each output line.

    stderr is merged into stdout. ``stdin`` is UTF-8 encoded before spawn, written, then
    closed. ``env`` overlays ``os.environ``. A ``cancel`` already set prevents the spawn.
    ``deadline`` is a ``time.monotonic`` value. Cancel and the deadline are enforced even
    while ``on_line`` stalls. The process tree is killed on every exit path, and a cancelled
    caller task gets ``CancelledError`` after cleanup. Exceptions from ``on_spawn`` propagate
    after cleanup. A spawn ``OSError`` is returned as ``missing_cli`` or ``spawn_failed``.
    """
    if cancel is not None and cancel.is_set():
        return ProcessOutcome(error="command cancelled", error_class="cancelled")
    try:
        payload = None if stdin is None else stdin.encode("utf-8")
    except UnicodeEncodeError as exc:
        return ProcessOutcome(error=f"prompt is not valid UTF-8: {exc}", error_class="invalid_prompt")

    name = os.path.basename(argv[0])
    started_at = time.monotonic()
    try:
        process = await _spawn(prepare_command(list(argv)), payload, env, cwd)
    except FileNotFoundError as exc:
        return ProcessOutcome(error=f"{name} could not be started: {exc.strerror or exc}", error_class="missing_cli")
    except OSError as exc:
        return ProcessOutcome(error=f"{name} could not be started: {exc.strerror or exc}", error_class="spawn_failed")
    log.debug("spawned %s pid=%d", name, process.pid)
    writer = None
    if payload is not None:
        assert process.stdin is not None
        writer = asyncio.ensure_future(_write_stdin(process.stdin, payload))
    tail = OutputTail()
    outcome = ProcessOutcome(spawned=True)
    stop = ""
    try:
        if on_spawn is not None:
            await on_spawn(process.pid)
        stop = await _supervise(process, writer, deadline, cancel, on_line, tail, outcome)
    finally:
        await _shielded(asyncio.ensure_future(_cleanup(process, writer)))
        outcome.returncode = process.returncode
        outcome.tail = tail.text()

    _map_stop(outcome, stop, name)
    if outcome.error_class:
        log.info("%s subprocess ended class=%s after %.2fs", name, outcome.error_class, time.monotonic() - started_at)
    return outcome


__all__ = ["LineHandler", "OutputTail", "ProcessOutcome", "SpawnHandler", "run_process"]
