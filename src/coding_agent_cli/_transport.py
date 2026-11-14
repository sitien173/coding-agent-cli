"""Two-pipe subprocess transport with supervised process-tree cleanup."""

from __future__ import annotations

import asyncio
import inspect
import os
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Literal

from ._platform import (
    force_kill_process_tree,
    kill_process_tree,
    prepare_command,
    process_group_kwargs,
    track_process_tree,
)

StreamName = Literal["stdout", "stderr"]
LineCallback = Callable[[StreamName, str], Awaitable[None]]
SpawnCallback = Callable[[int], Awaitable[None] | None]
TAIL_BYTES = 64 * 1024
READ_BYTES = 64 * 1024


class ParserFailure(Exception):
    """Parser-side failure that Phase 4 can convert into an outcome."""


@dataclass(frozen=True, slots=True)
class TransportOutcome:
    stop_cause: str = ""
    returncode: int | None = None
    stderr_tail: str = ""
    spawn_failure: str | None = None
    spawn_failure_message: str = ""
    stdin_failure: bool = False
    stdin_failure_message: str = ""
    parser_failure_message: str = ""
    line_too_long: bool = False
    cleanup_incomplete: bool = False
    duration_s: float = 0.0


@dataclass(slots=True)
class _State:
    last_activity: float
    idle_timeout_s: float | None = None
    idle_deadline: float | None = None
    paused_idle_s: float = 0.0
    callback_busy: bool = False
    callback_failure: BaseException | None = None
    line_too_long: bool = False
    parser_failure_message: str = ""
    stdin_failure_message: str = ""
    stderr_tail: bytearray = field(default_factory=bytearray)


async def _write_stdin(writer: asyncio.StreamWriter, payload: bytes) -> None:
    writer.write(payload)
    await writer.drain()
    writer.close()
    await writer.wait_closed()


async def _read_stream(
    reader: asyncio.StreamReader,
    name: StreamName,
    queue: asyncio.Queue[tuple[StreamName, bytes] | None],
    state: _State,
    activity: asyncio.Event,
    max_line_bytes: int,
) -> None:
    pending = bytearray()
    discarding = False
    try:
        while chunk := await reader.read(READ_BYTES):
            now = time.monotonic()
            state.last_activity = now
            if name == "stderr":
                state.stderr_tail.extend(chunk)
                if len(state.stderr_tail) > TAIL_BYTES:
                    del state.stderr_tail[: len(state.stderr_tail) - TAIL_BYTES]
            if state.idle_timeout_s is not None:
                if state.callback_busy:
                    state.paused_idle_s = state.idle_timeout_s
                else:
                    state.idle_deadline = now + state.idle_timeout_s
            activity.set()
            offset = 0
            while offset < len(chunk):
                newline = chunk.find(b"\n", offset)
                end = len(chunk) if newline < 0 else newline
                part = chunk[offset:end]
                if discarding:
                    if newline >= 0:
                        discarding = False
                    offset = end + (newline >= 0)
                    continue
                if len(pending) + len(part) > max_line_bytes:
                    state.line_too_long = True
                    pending.clear()
                    discarding = newline < 0
                    offset = end + (newline >= 0)
                    continue
                pending.extend(part)
                if newline >= 0:
                    if pending.endswith(b"\r"):
                        pending.pop()
                    await queue.put((name, bytes(pending)))
                    pending.clear()
                offset = end + (newline >= 0)
        if pending and not discarding:
            await queue.put((name, bytes(pending)))
    finally:
        task = asyncio.current_task()
        if task is None or not task.cancelling():
            await queue.put(None)


async def _dispatch(
    queue: asyncio.Queue[tuple[StreamName, bytes] | None],
    on_line: LineCallback,
    state: _State,
    activity: asyncio.Event,
) -> None:
    ended = 0
    while ended < 2:
        item = await queue.get()
        if item is None:
            ended += 1
            continue
        stream, raw = item
        if state.callback_failure is not None:
            continue
        line = raw.decode("utf-8", errors="replace")
        state.callback_busy = True
        if state.idle_deadline is not None:
            state.paused_idle_s = (
                state.idle_timeout_s
                if not queue.empty() and state.idle_timeout_s is not None
                else max(0.0, state.idle_deadline - time.monotonic())
            )
        activity.set()
        try:
            await on_line(stream, line)
        except asyncio.CancelledError as exc:
            task = asyncio.current_task()
            if task is None or task.cancelling():
                raise
            state.callback_failure = exc
        except ParserFailure as exc:
            state.parser_failure_message = str(exc)
            state.callback_failure = exc
        except BaseException as exc:
            state.callback_failure = exc
        finally:
            state.callback_busy = False
            if state.idle_timeout_s is not None:
                if not queue.empty():
                    state.paused_idle_s = state.idle_timeout_s
                state.idle_deadline = time.monotonic() + state.paused_idle_s
            activity.set()
    if state.callback_failure is not None:
        raise state.callback_failure


async def _wait_for_eof(readers: set[asyncio.Task[None]], duration: float) -> bool:
    _, pending = await asyncio.wait(readers, timeout=duration)
    return not pending


async def _cleanup(
    process: asyncio.subprocess.Process,
    tasks: set[asyncio.Task[object]],
    deadline: float,
    drain_tasks: set[asyncio.Task[object]],
) -> bool:
    for task in tasks:
        if task not in drain_tasks and not task.done():
            task.cancel()
    try:
        async with asyncio.timeout_at(deadline):
            await kill_process_tree(process.pid)
            if process.stdin is not None:
                process.stdin.close()
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            await process.wait()
            await asyncio.wait(drain_tasks)
            others = [task for task in tasks if task not in drain_tasks]
            if others:
                await asyncio.wait(others)
        for task in tasks:
            if task.done():
                _consume_task_exception(task)
        return False
    except TimeoutError:
        force_task = asyncio.create_task(force_kill_process_tree(process.pid))
        try:
            async with asyncio.timeout_at(deadline):
                await asyncio.shield(force_task)
        except TimeoutError:
            force_task.add_done_callback(_consume_task_exception)
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        for task in drain_tasks:
            if not task.done():
                task.cancel()
        return True


async def _run_cleanup_hook(hook: Callable[[], Awaitable[None] | None] | None) -> None:
    if hook is None:
        return
    result = hook()
    if inspect.isawaitable(result):
        await result


def _consume_task_exception(task: asyncio.Task[object]) -> None:
    if not task.cancelled():
        task.exception()


async def _run_cleanup_hook_until(
    hook: Callable[[], Awaitable[None] | None] | None,
    deadline: float,
) -> bool:
    if hook is None:
        return False
    task = asyncio.create_task(_run_cleanup_hook(hook))
    try:
        async with asyncio.timeout_at(deadline):
            await asyncio.shield(task)
        return False
    except TimeoutError:
        task.cancel()
        task.add_done_callback(_consume_task_exception)
        return True


async def _await_cleanup_hook(
    hook: Callable[[], Awaitable[None] | None] | None,
    timeout_s: float,
) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout_s
    task = asyncio.create_task(_run_cleanup_hook_until(hook, deadline))
    return await _await_cleanup(task)


async def _cleanup_with_hook(
    process: asyncio.subprocess.Process,
    tasks: set[asyncio.Task[object]],
    timeout_s: float,
    drain_tasks: set[asyncio.Task[object]],
    hook: Callable[[], Awaitable[None] | None] | None,
) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout_s
    try:
        incomplete = await _cleanup(process, tasks, deadline, drain_tasks)
    finally:
        hook_incomplete = await _run_cleanup_hook_until(hook, deadline)
    return incomplete or hook_incomplete


async def _await_cleanup(task: asyncio.Task[bool]) -> bool:
    """Await cleanup despite repeated cancellation, then deliver cancellation."""
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    result = task.result()
    if cancelled:
        raise asyncio.CancelledError
    return result


async def run_transport(
    argv: list[str],
    on_line: LineCallback,
    *,
    stdin: bytes | None = None,
    env: Mapping[str, str] | None = None,
    cwd: str | None = None,
    max_line_bytes: int = 16 * 1024 * 1024,
    timeout_s: float | None = None,
    idle_timeout_s: float | None = None,
    cancel: asyncio.Event | None = None,
    on_spawn: SpawnCallback | None = None,
    cleanup_hook: Callable[[], Awaitable[None] | None] | None = None,
    cleanup_timeout_s: float = 10.0,
    eof_timeout_s: float = 0.2,
) -> TransportOutcome:
    """Run argv, dispatch decoded lines serially, and supervise all stop conditions."""
    started = time.monotonic()
    try:
        command = prepare_command(list(argv))
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.DEVNULL if stdin is None else asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=None if env is None else {**os.environ, **env},
            shell=False,
            **process_group_kwargs(),
        )
    except asyncio.CancelledError:
        await _await_cleanup_hook(cleanup_hook, cleanup_timeout_s)
        raise
    except FileNotFoundError as exc:
        cleanup_incomplete = await _await_cleanup_hook(cleanup_hook, cleanup_timeout_s)
        return TransportOutcome(
            spawn_failure="missing_cli",
            spawn_failure_message=exc.strerror or str(exc),
            cleanup_incomplete=cleanup_incomplete,
            duration_s=time.monotonic() - started,
        )
    except OSError as exc:
        cleanup_incomplete = await _await_cleanup_hook(cleanup_hook, cleanup_timeout_s)
        return TransportOutcome(
            spawn_failure="spawn_failed",
            spawn_failure_message=exc.strerror or str(exc),
            cleanup_incomplete=cleanup_incomplete,
            duration_s=time.monotonic() - started,
        )
    except BaseException:
        await _await_cleanup_hook(cleanup_hook, cleanup_timeout_s)
        raise
    track_process_tree(process.pid)
    assert process.stdout is not None and process.stderr is not None
    now = time.monotonic()
    state = _State(
        now,
        idle_timeout_s=idle_timeout_s,
        idle_deadline=None if idle_timeout_s is None else now + idle_timeout_s,
    )
    activity = asyncio.Event()
    queue: asyncio.Queue[tuple[StreamName, bytes] | None] = asyncio.Queue(maxsize=16)
    readers = {
        asyncio.create_task(_read_stream(process.stdout, "stdout", queue, state, activity, max_line_bytes)),
        asyncio.create_task(_read_stream(process.stderr, "stderr", queue, state, activity, max_line_bytes)),
    }
    dispatcher = asyncio.create_task(_dispatch(queue, on_line, state, activity))
    all_tasks: set[asyncio.Task[object]] = {*readers, dispatcher}
    drain_tasks: set[asyncio.Task[object]] = {*readers, dispatcher}
    writer: asyncio.Task[None] | None = None
    if stdin is not None:
        assert process.stdin is not None
        writer = asyncio.create_task(_write_stdin(process.stdin, stdin))
        all_tasks.add(writer)
        drain_tasks.add(writer)
    wait_task = asyncio.create_task(process.wait())
    all_tasks.add(wait_task)
    leader_exited = asyncio.Event()
    protocol = process._transport._protocol  # type: ignore[attr-defined]
    process_exited = protocol.process_exited

    def note_process_exit() -> None:
        process_exited()
        leader_exited.set()

    protocol.process_exited = note_process_exit
    if process.returncode is not None:
        leader_exited.set()
    cancel_task = asyncio.create_task(cancel.wait()) if cancel is not None else None
    if cancel_task is not None:
        all_tasks.add(cancel_task)
    activity_task = asyncio.create_task(activity.wait())
    exit_task = asyncio.create_task(leader_exited.wait())
    all_tasks.update((activity_task, exit_task))
    spawn_task: asyncio.Task[object] | None = None
    if on_spawn is not None:
        try:
            spawn_result = on_spawn(process.pid)
            if inspect.isawaitable(spawn_result):
                spawn_task = asyncio.ensure_future(spawn_result)
                all_tasks.add(spawn_task)
        except BaseException:
            cleanup_task = asyncio.create_task(
                _cleanup_with_hook(process, all_tasks, cleanup_timeout_s, drain_tasks, cleanup_hook)
            )
            try:
                await _await_cleanup(cleanup_task)
            except asyncio.CancelledError:
                await _await_cleanup(cleanup_task)
                raise
            raise

    wall_deadline = None if timeout_s is None else started + timeout_s
    stop = ""
    callback_error: BaseException | None = None
    spawn_error = False
    cleanup_incomplete = False
    writer_observed = False
    cleanup_task: asyncio.Task[bool] | None = None
    try:
        while True:
            activity.clear()
            if activity_task.done():
                activity_task = asyncio.create_task(activity.wait())
                all_tasks.add(activity_task)
            if leader_exited.is_set():
                stop = "exited"
                break
            if cancel is not None and cancel.is_set():
                stop = "cancelled"
                break
            if wall_deadline is not None and time.monotonic() >= wall_deadline:
                stop = "timeout"
                break
            if state.callback_failure is not None:
                callback_error = state.callback_failure
                stop = "parser_failure" if isinstance(callback_error, ParserFailure) else "callback_failure"
                break
            if (
                spawn_task is not None
                and spawn_task.done()
                and not spawn_task.cancelled()
                and spawn_task.exception() is not None
            ):
                callback_error = spawn_task.exception()
                spawn_error = True
                stop = "callback_failure"
                break
            if writer is not None and writer.done() and not writer.cancelled() and not writer_observed:
                try:
                    writer.result()
                except Exception as exc:
                    state.stdin_failure_message = f"{type(exc).__name__}: {exc}"
                writer_observed = True

            deadlines: list[float] = []
            if wall_deadline is not None:
                deadlines.append(wall_deadline)
            if state.idle_deadline is not None and not state.callback_busy and queue.empty():
                deadlines.append(state.idle_deadline)
            delay = max(0.0, min(deadlines) - time.monotonic()) if deadlines else None
            wait_set: set[asyncio.Task[object]] = {exit_task, activity_task}
            if not dispatcher.done():
                wait_set.add(dispatcher)
            if cancel_task is not None and not cancel_task.done():
                wait_set.add(cancel_task)
            if spawn_task is not None and not spawn_task.done():
                wait_set.add(spawn_task)
            if writer is not None and not writer.done():
                wait_set.add(writer)
            done, _ = await asyncio.wait(wait_set, timeout=delay, return_when=asyncio.FIRST_COMPLETED)
            if not done and state.idle_deadline is not None and not state.callback_busy and queue.empty():
                if time.monotonic() >= state.idle_deadline:
                    stop = "idle_timeout"
                    break
        if stop == "exited":
            await _wait_for_eof(readers, eof_timeout_s)
        if writer is not None and writer.done() and not writer.cancelled() and not writer_observed:
            try:
                writer.result()
            except (BrokenPipeError, ConnectionResetError, OSError) as exc:
                state.stdin_failure_message = f"{type(exc).__name__}: {exc}"
        cleanup_task = asyncio.create_task(
            _cleanup_with_hook(process, all_tasks, cleanup_timeout_s, drain_tasks, cleanup_hook)
        )
        cleanup_incomplete = await _await_cleanup(cleanup_task)
    except asyncio.CancelledError:
        if cleanup_task is None:
            cleanup_task = asyncio.create_task(
                _cleanup_with_hook(process, all_tasks, cleanup_timeout_s, drain_tasks, cleanup_hook)
            )
        try:
            cleanup_incomplete = await _await_cleanup(cleanup_task)
        except asyncio.CancelledError:
            pass
        raise

    if dispatcher.done() and not dispatcher.cancelled():
        try:
            dispatcher.result()
        except ParserFailure as exc:
            state.parser_failure_message = str(exc)
        except BaseException as exc:
            callback_error = exc
    if callback_error is None and isinstance(state.callback_failure, asyncio.CancelledError):
        callback_error = state.callback_failure

    if spawn_task is not None and spawn_task.done() and not spawn_task.cancelled():
        try:
            spawn_task.result()
        except BaseException as exc:
            callback_error = exc
            spawn_error = True

    if spawn_error and callback_error is not None:
        raise callback_error
    if callback_error is not None and not isinstance(callback_error, ParserFailure):
        raise callback_error
    return TransportOutcome(
        stop_cause=stop,
        returncode=process.returncode,
        stderr_tail=bytes(state.stderr_tail).decode("utf-8", errors="replace"),
        stdin_failure=bool(state.stdin_failure_message),
        stdin_failure_message=state.stdin_failure_message,
        parser_failure_message=state.parser_failure_message,
        line_too_long=state.line_too_long,
        cleanup_incomplete=cleanup_incomplete,
        duration_s=time.monotonic() - started,
    )


__all__ = ["ParserFailure", "TransportOutcome", "run_transport"]
