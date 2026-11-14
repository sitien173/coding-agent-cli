from __future__ import annotations

import asyncio
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from coding_agent_cli._transport import ParserFailure, run_transport

FAKES = Path(__file__).parent / "fixtures" / "fake_cli"


def _pid_is_running(pid: int) -> bool:
    stat_path = Path(f"/proc/{pid}/stat")
    if stat_path.exists():
        try:
            return stat_path.read_text(encoding="utf-8").split(") ", 1)[1][0] != "Z"
        except (IndexError, OSError):
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _command(tmp_path: Path, fake: str) -> list[str]:
    del tmp_path
    return [sys.executable, str(FAKES / fake)]


@pytest.mark.asyncio
async def test_both_pipes_drain_under_saturation_with_slow_consumer(tmp_path: Path) -> None:
    lines: dict[str, list[str]] = {"stdout": [], "stderr": []}

    async def on_line(stream: str, line: str) -> None:
        lines[stream].append(line)
        await asyncio.sleep(0.001)

    result = await run_transport(_command(tmp_path, "both_flood.py"), on_line, timeout_s=10)

    assert result.stop_cause == "exited"
    assert len(lines["stdout"]) == 512
    assert len(lines["stderr"]) == 512
    assert result.returncode == 0


@pytest.mark.asyncio
async def test_line_queue_is_bounded_for_fast_producer_and_slow_consumer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import coding_agent_cli._transport as transport

    real_queue = asyncio.Queue
    max_observed = 0

    class ObservedQueue(real_queue):
        def put_nowait(self, item: Any) -> None:
            nonlocal max_observed
            super().put_nowait(item)
            max_observed = max(max_observed, self.qsize())

    monkeypatch.setattr(transport.asyncio, "Queue", ObservedQueue)

    async def on_line(stream: str, line: str) -> None:
        await asyncio.sleep(0.005)

    result = await run_transport(_command(tmp_path, "both_flood.py"), on_line, timeout_s=10)

    assert result.stop_cause == "exited"
    assert max_observed <= 16


@pytest.mark.asyncio
async def test_oversized_stdout_line_is_skipped_and_next_line_delivered(tmp_path: Path) -> None:
    lines: list[tuple[str, str]] = []

    async def on_line(stream: str, line: str) -> None:
        lines.append((stream, line))

    result = await run_transport(_command(tmp_path, "oversized_then_valid.py"), on_line, max_line_bytes=100_000)

    assert lines == [("stdout", "valid-after-oversized")]
    assert result.line_too_long is True


@pytest.mark.asyncio
async def test_stderr_tail_is_capped_at_64_kib(tmp_path: Path) -> None:
    lines: list[str] = []

    async def on_line(stream: str, line: str) -> None:
        if stream == "stderr":
            lines.append(line)

    result = await run_transport(_command(tmp_path, "both_flood.py"), on_line)

    assert len(result.stderr_tail.encode("utf-8")) <= 64 * 1024
    assert result.stderr_tail.endswith("e" * 8191 + "\n")


@pytest.mark.asyncio
async def test_stdin_is_delivered_and_closed_stdin_failure_is_recorded(tmp_path: Path) -> None:
    lines: list[str] = []

    async def on_line(stream: str, line: str) -> None:
        lines.append(line)

    result = await run_transport(
        [sys.executable, str(FAKES / "echo_stdin.py")],
        on_line,
        stdin=b"payload",
    )

    assert result.returncode == 0
    assert result.stdin_failure is False
    assert lines[0] == "7"

    result = await run_transport(
        [sys.executable, "-c", "import sys; sys.stdin.close(); print('closed')"],
        on_line,
        stdin=b"x" * (8 * 1024 * 1024),
    )

    assert result.stdin_failure is True
    assert result.stdin_failure_message


@pytest.mark.asyncio
async def test_wall_timeout_stops_sleeper(tmp_path: Path) -> None:
    async def on_line(stream: str, line: str) -> None:
        pass

    result = await run_transport(_command(tmp_path, "sleeper.py"), on_line, timeout_s=0.08)

    assert result.stop_cause == "timeout"
    assert result.returncode is not None


@pytest.mark.asyncio
async def test_idle_timeout_stops_silent_process(tmp_path: Path) -> None:
    async def on_line(stream: str, line: str) -> None:
        pass

    result = await run_transport(_command(tmp_path, "sleeper.py"), on_line, idle_timeout_s=0.15, timeout_s=0.5)

    assert result.stop_cause == "idle_timeout"
    assert result.returncode is not None


@pytest.mark.asyncio
async def test_parser_failure_is_returned_for_classification(tmp_path: Path) -> None:
    async def on_line(stream: str, line: str) -> None:
        raise ParserFailure("invalid parser input")

    result = await run_transport(_command(tmp_path, "sleeper.py"), on_line)

    assert result.stop_cause == "parser_failure"
    assert result.parser_failure_message == "invalid parser input"
    assert result.returncode is not None


@pytest.mark.asyncio
async def test_idle_timeout_resets_on_activity_from_both_pipes(tmp_path: Path) -> None:
    async def on_line(stream: str, line: str) -> None:
        pass

    result = await run_transport(_command(tmp_path, "slow_trickle.py"), on_line, idle_timeout_s=0.6, timeout_s=2)

    assert result.stop_cause == "exited"


@pytest.mark.asyncio
async def test_idle_timeout_is_paused_during_slow_line_callback(tmp_path: Path) -> None:
    lines: list[str] = []

    async def on_line(stream: str, line: str) -> None:
        lines.append(line)
        await asyncio.sleep(0.4)

    result = await run_transport(_command(tmp_path, "slow_callback.py"), on_line, idle_timeout_s=0.25, timeout_s=2)

    assert result.stop_cause == "exited"
    assert lines == ["first", "second"]


@pytest.mark.asyncio
async def test_idle_timeout_stays_paused_under_queue_backpressure(tmp_path: Path) -> None:
    async def on_line(stream: str, line: str) -> None:
        await asyncio.sleep(0.15)

    result = await run_transport(_command(tmp_path, "line_flood.py"), on_line, idle_timeout_s=0.1, timeout_s=5)

    assert result.stop_cause == "exited"


@pytest.mark.asyncio
async def test_cancel_stops_process_and_repeated_cancel_is_latched(tmp_path: Path) -> None:
    cancel = asyncio.Event()

    async def on_line(stream: str, line: str) -> None:
        cancel.set()
        cancel.set()

    result = await run_transport(_command(tmp_path, "sleeper.py"), on_line, cancel=cancel, timeout_s=0.5)

    assert result.stop_cause == "cancelled"
    assert result.returncode is not None


@pytest.mark.asyncio
async def test_cancel_set_after_exit_does_not_replace_exited(tmp_path: Path) -> None:
    cancel = asyncio.Event()

    async def on_line(stream: str, line: str) -> None:
        cancel.set()

    result = await run_transport([sys.executable, str(FAKES / "exit_now.py")], on_line, cancel=cancel)

    assert result.stop_cause == "exited"


@pytest.mark.asyncio
async def test_callback_failure_after_leader_exit_is_reraised(tmp_path: Path) -> None:
    async def on_line(stream: str, line: str) -> None:
        await asyncio.sleep(0.05)
        raise ValueError("late callback failure")

    with pytest.raises(ValueError, match="late callback failure"):
        await run_transport([sys.executable, str(FAKES / "print_then_exit.py")], on_line)


@pytest.mark.asyncio
async def test_parser_failure_after_leader_exit_is_returned(tmp_path: Path) -> None:
    async def on_line(stream: str, line: str) -> None:
        await asyncio.sleep(0.05)
        raise ParserFailure("late parser failure")

    result = await run_transport([sys.executable, str(FAKES / "print_then_exit.py")], on_line)

    assert result.stop_cause == "exited"
    assert result.parser_failure_message == "late parser failure"


@pytest.mark.asyncio
async def test_stalled_on_spawn_is_bounded_by_wall_timeout_and_cancel(tmp_path: Path) -> None:
    async def on_line(stream: str, line: str) -> None:
        pass

    async def stalled(pid: int) -> None:
        await asyncio.Event().wait()

    result = await run_transport(_command(tmp_path, "sleeper.py"), on_line, on_spawn=stalled, timeout_s=0.06)
    assert result.stop_cause == "timeout"

    cancel = asyncio.Event()

    async def set_cancel(pid: int) -> None:
        cancel.set()
        await asyncio.Event().wait()

    result = await run_transport(_command(tmp_path, "sleeper.py"), on_line, on_spawn=set_cancel, cancel=cancel)
    assert result.stop_cause == "cancelled"


@pytest.mark.asyncio
async def test_line_callback_exception_is_reraised_after_cleanup(tmp_path: Path) -> None:
    pid = 0

    async def on_spawn(child_pid: int) -> None:
        nonlocal pid
        pid = child_pid

    async def on_line(stream: str, line: str) -> None:
        raise ValueError("callback failed")

    with pytest.raises(ValueError, match="callback failed"):
        await run_transport(_command(tmp_path, "sleeper.py"), on_line, on_spawn=on_spawn)
    assert pid
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process-group behavior")
async def test_descendant_is_killed_after_leader_exits(tmp_path: Path) -> None:
    pid_file = tmp_path / "child.pid"

    async def on_line(stream: str, line: str) -> None:
        pass

    result = await run_transport(
        [sys.executable, str(FAKES / "grandchild.py"), str(pid_file)],
        on_line,
        eof_timeout_s=0.02,
    )

    assert result.stop_cause == "exited"
    child_pid = int(pid_file.read_text(encoding="utf-8"))
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        if not _pid_is_running(child_pid):
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail(f"descendant {child_pid} remained alive after transport cleanup")


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process-group behavior")
async def test_cleanup_deadline_reports_incomplete_and_force_kills_descendant(tmp_path: Path) -> None:
    pid_file = tmp_path / "child.pid"

    async def on_line(stream: str, line: str) -> None:
        pass

    result = await run_transport(
        [sys.executable, str(FAKES / "stubborn_descendant.py"), str(pid_file)],
        on_line,
        eof_timeout_s=0.01,
        cleanup_timeout_s=0.03,
    )

    assert result.cleanup_incomplete is True
    child_pid = int(pid_file.read_text(encoding="utf-8"))
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        if not _pid_is_running(child_pid):
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail(f"descendant {child_pid} survived cleanup deadline")


@pytest.mark.asyncio
async def test_missing_executable_is_reported(tmp_path: Path) -> None:
    async def on_line(stream: str, line: str) -> None:
        pass

    result = await run_transport([str(tmp_path / "does-not-exist")], on_line)

    assert result.spawn_failure == "missing_cli"
    assert result.spawn_failure_message


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object behavior")
def test_job_object_configures_kill_on_close(monkeypatch: pytest.MonkeyPatch) -> None:
    import ctypes

    from coding_agent_cli import _platform

    observed: dict[str, int] = {}
    kernel: Any = getattr(_platform, "_kernel32")  # noqa: B009
    extended_information: Any = getattr(_platform, "_JobObjectExtendedLimitInformation")  # noqa: B009
    monkeypatch.setattr(kernel, "CreateJobObjectW", lambda *_: 11)
    monkeypatch.setattr(kernel, "OpenProcess", lambda *_: 22)
    monkeypatch.setattr(kernel, "AssignProcessToJobObject", lambda *_: 1)
    monkeypatch.setattr(kernel, "CloseHandle", lambda *_: 1)

    def set_information(job: int, info_class: int, info: Any, size: int) -> int:
        limits = ctypes.cast(info, ctypes.POINTER(extended_information)).contents
        observed["job"] = job
        observed["info_class"] = info_class
        observed["size"] = size
        observed["flags"] = limits.BasicLimitInformation.LimitFlags
        return 1

    monkeypatch.setattr(kernel, "SetInformationJobObject", set_information)
    _platform.track_process_tree(33)
    _platform._jobs.pop(33)

    assert observed["job"] == 11
    assert observed["info_class"] == 9
    assert observed["flags"] == _platform._JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE


@pytest.mark.asyncio
async def test_caller_cancellation_is_reraised_after_cleanup(tmp_path: Path) -> None:
    spawned = asyncio.Event()
    pid = 0

    async def on_spawn(child_pid: int) -> None:
        nonlocal pid
        pid = child_pid
        spawned.set()

    async def on_line(stream: str, line: str) -> None:
        pass

    task = asyncio.create_task(run_transport(_command(tmp_path, "sleeper.py"), on_line, on_spawn=on_spawn))
    await spawned.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert pid
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process-group behavior")
async def test_repeated_caller_cancellation_waits_for_descendant_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import coding_agent_cli._transport as transport

    pid_file = tmp_path / "child.pid"
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()
    kill_tree = transport.kill_process_tree

    async def gated_kill_tree(pid: int) -> None:
        cleanup_started.set()
        await release_cleanup.wait()
        await kill_tree(pid)

    monkeypatch.setattr(transport, "kill_process_tree", gated_kill_tree)

    async def on_line(stream: str, line: str) -> None:
        pass

    task = asyncio.create_task(
        run_transport(
            [sys.executable, str(FAKES / "grandchild.py"), str(pid_file)],
            on_line,
            eof_timeout_s=0.01,
        )
    )
    deadline = time.monotonic() + 2
    while not pid_file.exists() and time.monotonic() < deadline:
        await asyncio.sleep(0.005)
    assert pid_file.exists()
    child_pid = int(pid_file.read_text(encoding="utf-8"))
    task.cancel()
    await cleanup_started.wait()
    task.cancel()
    await asyncio.sleep(0)
    premature_exit = task.done()
    release_cleanup.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not premature_exit
    assert not _pid_is_running(child_pid)


@pytest.mark.asyncio
async def test_cleanup_hook_runs_after_callback_failure_and_cancellation(tmp_path: Path) -> None:
    seen: list[str] = []
    spawned = asyncio.Event()

    async def on_line(stream: str, line: str) -> None:
        raise ValueError("callback failure")

    async def callback_cleanup() -> None:
        seen.append("callback")

    with pytest.raises(ValueError, match="callback failure"):
        await run_transport(_command(tmp_path, "sleeper.py"), on_line, cleanup_hook=callback_cleanup)

    async def on_spawn(pid: int) -> None:
        spawned.set()

    async def no_lines(stream: str, line: str) -> None:
        pass

    async def cancellation_cleanup() -> None:
        seen.append("cancel")

    task = asyncio.create_task(
        run_transport(
            _command(tmp_path, "sleeper.py"),
            no_lines,
            on_spawn=on_spawn,
            cleanup_hook=cancellation_cleanup,
        )
    )
    await spawned.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert seen == ["callback", "cancel"]


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process-group behavior")
async def test_late_on_spawn_failure_after_leader_exit_is_reraised_and_tree_killed(
    tmp_path: Path,
) -> None:
    pid_file = tmp_path / "child.pid"
    line_seen = asyncio.Event()

    async def on_spawn(pid: int) -> None:
        await line_seen.wait()
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0.05):
                await asyncio.Event().wait()
        raise ValueError("late on_spawn failure")

    async def on_line(stream: str, line: str) -> None:
        line_seen.set()

    with pytest.raises(ValueError, match="late on_spawn failure"):
        async with asyncio.timeout(3):
            await run_transport(
                [sys.executable, str(FAKES / "grandchild.py"), str(pid_file)],
                on_line,
                on_spawn=on_spawn,
                eof_timeout_s=0.5,
                cleanup_timeout_s=1.5,
            )

    child_pid = int(pid_file.read_text(encoding="utf-8"))
    assert not _pid_is_running(child_pid)


@pytest.mark.asyncio
async def test_full_queue_cleanup_deadline_cancels_dispatcher_and_runs_hook(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import coding_agent_cli._transport as transport

    real_queue = asyncio.Queue
    queue_full = asyncio.Event()
    callback_started = asyncio.Event()
    queues: list[asyncio.Queue[Any]] = []

    class ObservedQueue(real_queue):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            queues.append(self)

        def put_nowait(self, item: Any) -> None:
            super().put_nowait(item)
            if self.full():
                queue_full.set()

    monkeypatch.setattr(transport.asyncio, "Queue", ObservedQueue)
    cancel = asyncio.Event()
    hook_ran = asyncio.Event()
    release_callback = asyncio.Event()
    pid = 0

    async def on_line(stream: str, line: str) -> None:
        callback_started.set()
        await release_callback.wait()

    def cleanup_hook() -> None:
        hook_ran.set()

    async def on_spawn(child_pid: int) -> None:
        nonlocal pid
        pid = child_pid

    async def rescue_stalled_test() -> None:
        release_callback.set()
        cancel.set()
        if pid:
            from coding_agent_cli._platform import force_kill_process_tree

            async with asyncio.timeout(1):
                await force_kill_process_tree(pid)
        if queues:
            queue = queues[0]
            while not queue.empty():
                queue.get_nowait()
            queue.put_nowait(None)
            queue.put_nowait(None)

    task = asyncio.create_task(
        run_transport(
            _command(tmp_path, "line_flood.py"),
            on_line,
            cancel=cancel,
            on_spawn=on_spawn,
            cleanup_hook=cleanup_hook,
            cleanup_timeout_s=0.1,
        )
    )
    try:
        async with asyncio.timeout(2):
            await callback_started.wait()
            await queue_full.wait()
    except TimeoutError:
        await rescue_stalled_test()
        async with asyncio.timeout(2):
            await task
        pytest.fail("the producer did not fill the line queue")
    cancel.set()
    try:
        async with asyncio.timeout(0.5):
            await asyncio.shield(task)
        completed_before_release = True
    except TimeoutError:
        completed_before_release = task.done()
    release_callback.set()
    if not completed_before_release:
        await rescue_stalled_test()
    async with asyncio.timeout(2):
        result = await task

    assert completed_before_release
    assert result.cleanup_incomplete is True
    assert hook_ran.is_set()


@pytest.mark.asyncio
async def test_spawn_failure_hook_survives_repeated_caller_cancellation(tmp_path: Path) -> None:
    hook_started = asyncio.Event()
    release_hook = asyncio.Event()
    hook_finished = asyncio.Event()

    async def cleanup_hook() -> None:
        hook_started.set()
        await release_hook.wait()
        hook_finished.set()

    async def on_line(stream: str, line: str) -> None:
        pass

    task = asyncio.create_task(run_transport([str(tmp_path / "missing-cli")], on_line, cleanup_hook=cleanup_hook))
    async with asyncio.timeout(2):
        await hook_started.wait()
    task.cancel()
    task.cancel()
    release_hook.set()
    with pytest.raises(asyncio.CancelledError):
        async with asyncio.timeout(2):
            await task

    assert hook_finished.is_set()


@pytest.mark.asyncio
async def test_stalled_cleanup_hook_is_bounded_and_reported_incomplete(tmp_path: Path) -> None:
    hook_started = asyncio.Event()
    release_hook = asyncio.Event()

    async def on_line(stream: str, line: str) -> None:
        pass

    async def cleanup_hook() -> None:
        hook_started.set()
        await release_hook.wait()

    task = asyncio.create_task(
        run_transport(
            [sys.executable, str(FAKES / "exit_now.py")],
            on_line,
            cleanup_hook=cleanup_hook,
            cleanup_timeout_s=0.1,
        )
    )
    async with asyncio.timeout(2):
        await hook_started.wait()
    try:
        async with asyncio.timeout(0.5):
            result = await asyncio.shield(task)
        completed_before_release = True
    except TimeoutError:
        completed_before_release = task.done()
    release_hook.set()
    async with asyncio.timeout(2):
        result = await task

    assert completed_before_release
    assert result.cleanup_incomplete is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exit_path",
    [
        "success",
        "cancel event",
        "wall timeout",
        "idle timeout",
        "spawn failure",
        "callback failure",
        "caller cancellation",
    ],
)
async def test_cleanup_hook_removes_temp_directory_on_every_exit_path(tmp_path: Path, exit_path: str) -> None:
    temp_dir = tmp_path / "hook-owned"
    temp_dir.mkdir()
    cancel = asyncio.Event()
    spawned = asyncio.Event()

    def cleanup_hook() -> None:
        shutil.rmtree(temp_dir)

    async def on_line(stream: str, line: str) -> None:
        if exit_path == "cancel event":
            cancel.set()
        elif exit_path == "callback failure":
            raise ValueError("callback failure")

    async def on_spawn(pid: int) -> None:
        spawned.set()

    if exit_path == "success":
        command = [sys.executable, str(FAKES / "exit_now.py")]
    elif exit_path in {"cancel event", "wall timeout", "callback failure", "caller cancellation"}:
        command = _command(tmp_path, "sleeper.py")
    elif exit_path == "idle timeout":
        command = [sys.executable, "-c", "import time; time.sleep(60)"]
    else:
        command = [str(tmp_path / "missing-cli")]

    if exit_path == "caller cancellation":
        task = asyncio.create_task(run_transport(command, on_line, on_spawn=on_spawn, cleanup_hook=cleanup_hook))
        async with asyncio.timeout(2):
            await spawned.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            async with asyncio.timeout(2):
                await task
    elif exit_path == "callback failure":
        with pytest.raises(ValueError, match="callback failure"):
            async with asyncio.timeout(2):
                await run_transport(command, on_line, cleanup_hook=cleanup_hook)
    else:
        options: dict[str, object] = {"cleanup_hook": cleanup_hook}
        if exit_path == "cancel event":
            options["cancel"] = cancel
        elif exit_path == "wall timeout":
            options["timeout_s"] = 0.1
        elif exit_path == "idle timeout":
            options["idle_timeout_s"] = 0.1
        async with asyncio.timeout(2):
            await run_transport(command, on_line, **options)  # type: ignore[arg-type]

    assert not temp_dir.exists()


@pytest.mark.asyncio
async def test_cleanup_deadline_holds_when_callback_suppresses_cancellation(tmp_path: Path) -> None:
    cancel = asyncio.Event()
    callback_started = asyncio.Event()
    release_callback = asyncio.Event()
    hook_ran = asyncio.Event()

    async def on_line(stream: str, line: str) -> None:
        callback_started.set()
        while not release_callback.is_set():
            try:
                await release_callback.wait()
            except asyncio.CancelledError:
                pass

    task = asyncio.create_task(
        run_transport(
            _command(tmp_path, "line_flood.py"),
            on_line,
            cancel=cancel,
            cleanup_hook=hook_ran.set,
            cleanup_timeout_s=0.1,
        )
    )
    try:
        async with asyncio.timeout(2):
            await callback_started.wait()
        cancel.set()
        async with asyncio.timeout(1):
            result = await asyncio.shield(task)
    finally:
        release_callback.set()
        async with asyncio.timeout(2):
            await asyncio.gather(task, return_exceptions=True)

    assert result.cleanup_incomplete is True
    assert hook_ran.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize("fake", ["sleeper.py", "print_then_exit.py"])
async def test_cancelled_error_raised_by_line_callback_is_reraised(tmp_path: Path, fake: str) -> None:
    async def on_line(stream: str, line: str) -> None:
        raise asyncio.CancelledError

    async with asyncio.timeout(5):
        with pytest.raises(asyncio.CancelledError):
            await run_transport(_command(tmp_path, fake), on_line, cleanup_timeout_s=1.0)
