"""Transport tests for run_process against fake CLI scripts."""

from __future__ import annotations

import asyncio
import hashlib
import os
import sys
import time
from pathlib import Path

import pytest

from coding_agent_cli._process import run_process

FAKE_CLI = Path(__file__).parent / "fixtures" / "fake_cli"


def fake(script: str, *args: object) -> list[str]:
    return [sys.executable, str(FAKE_CLI / script), *(str(arg) for arg in args)]


def _is_running(pid: int) -> bool:
    """Return False for an exited or zombie PID."""
    if sys.platform == "linux":
        try:
            with open(f"/proc/{pid}/stat", encoding="utf-8") as handle:
                state = handle.read().rsplit(")", 1)[1].split()[0]
        except (FileNotFoundError, ProcessLookupError):
            return False
        return state != "Z"
    if os.name == "nt":
        import subprocess

        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def wait_gone(pid: int, timeout_s: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not _is_running(pid):
            return True
        time.sleep(0.05)
    return not _is_running(pid)


def _run(argv, *, stdin=None, timeout_s=None, deadline=None, cancel=None, on_line=None, env=None, cwd=None):
    lines: list[str] = []

    async def collect(line: str) -> None:
        lines.append(line)
        if on_line is not None:
            await on_line(line)

    if deadline is None and timeout_s is not None:
        deadline = time.monotonic() + timeout_s
    outcome = asyncio.run(run_process(argv, stdin, env, cwd, deadline, cancel, collect))
    return outcome, lines


async def _noop(line: str) -> None:
    return None


def test_stderr_is_merged_into_stdout_and_exit_status_is_returned() -> None:
    outcome, lines = _run(fake("emit_both.py"))

    assert sorted(lines) == ["err-line", "out-line"]
    assert outcome.returncode == 0
    assert outcome.error == ""


def test_one_mib_prompt_reaches_stdin_reading_child_intact() -> None:
    prompt = "p" * (1024 * 1024)
    outcome, lines = _run(fake("echo_stdin.py"), stdin=prompt)

    data = prompt.encode("utf-8")
    assert lines == [str(len(data)), hashlib.sha256(data).hexdigest()]
    assert outcome.error == ""


def test_non_utf8_output_is_decoded_with_replacement_characters() -> None:
    outcome, lines = _run(fake("non_utf8.py"))

    assert lines == ["�ok"]
    assert outcome.error == ""


def test_grandchild_holding_stdout_does_not_stall_after_parent_exit(tmp_path: Path) -> None:
    pid_file = tmp_path / "grandchild.pid"
    started = time.monotonic()
    outcome, lines = _run(fake("grandchild.py", pid_file), timeout_s=30)
    elapsed = time.monotonic() - started

    assert lines == ["parent-done"]
    assert outcome.returncode == 0
    assert outcome.error == ""
    assert elapsed < 6
    assert wait_gone(int(pid_file.read_text(encoding="utf-8")))


def test_timeout_kills_the_whole_process_group(tmp_path: Path) -> None:
    pid_file = tmp_path / "grandchild.pid"
    started = time.monotonic()
    outcome, lines = _run(fake("spawn_and_sleep.py", pid_file), timeout_s=1.5)
    elapsed = time.monotonic() - started

    assert lines == ["ready"]
    assert outcome.error_class == "timeout"
    assert elapsed < 6
    assert wait_gone(int(pid_file.read_text(encoding="utf-8")))


def test_cancel_kills_the_process_group_immediately(tmp_path: Path) -> None:
    pid_file = tmp_path / "grandchild.pid"
    cancel = asyncio.Event()
    started = time.monotonic()

    async def cancel_on_ready(line: str) -> None:
        if line == "ready":
            cancel.set()

    outcome, _ = _run(fake("spawn_and_sleep.py", pid_file), cancel=cancel, on_line=cancel_on_ready)
    elapsed = time.monotonic() - started

    assert outcome.error_class == "cancelled"
    assert elapsed < 5
    assert wait_gone(int(pid_file.read_text(encoding="utf-8")))


def test_repeated_cancel_is_safe(tmp_path: Path) -> None:
    pid_file = tmp_path / "grandchild.pid"
    cancel = asyncio.Event()

    async def cancel_on_ready(line: str) -> None:
        if line == "ready":
            cancel.set()
            cancel.set()

    outcome, _ = _run(fake("spawn_and_sleep.py", pid_file), cancel=cancel, on_line=cancel_on_ready)
    cancel.set()

    assert outcome.error_class == "cancelled"
    assert wait_gone(int(pid_file.read_text(encoding="utf-8")))


def test_cancelled_caller_task_kills_the_tree_and_propagates_cancelled_error(tmp_path: Path) -> None:
    pid_file = tmp_path / "grandchild.pid"
    ready = asyncio.Event()

    async def scenario() -> None:
        async def on_line(line: str) -> None:
            if line == "ready":
                ready.set()

        task = asyncio.create_task(
            run_process(fake("spawn_and_sleep.py", pid_file), None, None, None, None, None, on_line)
        )
        await asyncio.wait_for(ready.wait(), 10)
        task.cancel()
        await task

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(scenario())
    assert wait_gone(int(pid_file.read_text(encoding="utf-8")))


def test_stalled_line_handler_does_not_block_cancel(tmp_path: Path) -> None:
    pid_file = tmp_path / "grandchild.pid"

    async def scenario():
        cancel = asyncio.Event()

        async def stall(line: str) -> None:
            if line == "ready":
                asyncio.get_running_loop().call_later(0.2, cancel.set)
                await asyncio.sleep(3600)

        return await run_process(fake("spawn_and_sleep.py", pid_file), None, None, None, None, cancel, stall)

    started = time.monotonic()
    outcome = asyncio.run(scenario())

    assert outcome.error_class == "cancelled"
    assert time.monotonic() - started < 5
    assert wait_gone(int(pid_file.read_text(encoding="utf-8")))


def test_stalled_line_handler_does_not_block_the_deadline(tmp_path: Path) -> None:
    pid_file = tmp_path / "grandchild.pid"

    async def stall(line: str) -> None:
        await asyncio.sleep(3600)

    started = time.monotonic()
    outcome, _ = _run(fake("spawn_and_sleep.py", pid_file), on_line=stall, timeout_s=1.0)

    assert outcome.error_class == "timeout"
    assert time.monotonic() - started < 5
    assert wait_gone(int(pid_file.read_text(encoding="utf-8")))


def test_stdin_write_failure_is_reported() -> None:
    outcome, _ = _run(fake("exit_now.py"), stdin="x" * (1024 * 1024))

    assert outcome.error_class == "stdin_failed"
    assert outcome.error.startswith("stdin write failed:")


def test_parser_exception_fails_attempt_and_kills_group(tmp_path: Path) -> None:
    pid_file = tmp_path / "grandchild.pid"

    async def explode(line: str) -> None:
        if line == "ready":
            raise ValueError("boom")

    outcome, _ = _run(fake("spawn_and_sleep.py", pid_file), on_line=explode, timeout_s=30)

    assert outcome.error == "ValueError: boom"
    assert outcome.error_class == "execution_error"
    assert wait_gone(int(pid_file.read_text(encoding="utf-8")))


def test_nonzero_exit_reports_status_and_keeps_output() -> None:
    outcome, lines = _run(fake("exit_status.py"))

    assert lines == ["failing"]
    assert outcome.returncode == 3
    assert outcome.error == "command exited with status 3"
    assert outcome.error_class == "execution_error"


def test_tail_keeps_last_64_kib_while_every_line_reaches_the_parser() -> None:
    count = 20_000
    outcome, lines = _run(fake("many_lines.py", count))

    assert len(lines) == count
    assert len(outcome.tail.encode("utf-8")) <= 64 * 1024
    assert outcome.tail.endswith(lines[-1])
    assert lines[0] not in outcome.tail


def test_line_longer_than_one_mib_fails_the_attempt() -> None:
    outcome, _ = _run(fake("long_line.py"), timeout_s=30)

    assert outcome.error == "output line exceeds 1 MiB"
    assert outcome.error_class == "execution_error"


def test_unencodable_prompt_fails_before_the_child_starts(tmp_path: Path) -> None:
    marker = tmp_path / "started"
    argv = [sys.executable, "-c", f"open({str(marker)!r}, 'w').close()"]

    outcome, lines = _run(argv, stdin="bad \ud800 prompt")

    assert outcome.error_class == "invalid_prompt"
    assert "UTF-8" in outcome.error
    assert lines == []
    assert not marker.exists()
    assert not outcome.spawned


def test_on_spawn_receives_the_child_pid_before_any_output() -> None:
    seen: list[int] = []
    order: list[str] = []

    async def hook(pid: int) -> None:
        seen.append(pid)
        order.append("hook")

    async def on_line(line: str) -> None:
        order.append(line)

    outcome = asyncio.run(run_process(fake("emit_both.py"), None, None, None, None, None, on_line, on_spawn=hook))

    assert len(seen) == 1
    assert seen[0] > 0
    assert order[0] == "hook"
    assert sorted(order[1:]) == ["err-line", "out-line"]
    assert outcome.error == ""


def test_failing_on_spawn_still_kills_the_group_and_propagates() -> None:
    pids: list[int] = []

    async def hook(pid: int) -> None:
        pids.append(pid)
        raise RuntimeError("hook failed")

    with pytest.raises(RuntimeError, match="hook failed"):
        asyncio.run(run_process(fake("sleeper.py"), None, None, None, None, None, _noop, on_spawn=hook))

    assert len(pids) == 1
    assert wait_gone(pids[0])


def test_env_is_merged_over_the_parent_environment_and_cwd_is_applied(tmp_path: Path) -> None:
    code = "import os, sys; print(os.environ['CAC_T'], os.environ.get('PATH') is not None, os.getcwd())"
    outcome, lines = _run([sys.executable, "-c", code], env={"CAC_T": "yes"}, cwd=str(tmp_path))

    assert lines == [f"yes True {tmp_path.resolve()}"]
    assert outcome.error == ""
    assert outcome.returncode == 0


def test_stdin_is_written_and_closed_for_a_reading_child() -> None:
    outcome, lines = _run(fake("echo_stdin.py"), stdin="hello")

    assert lines[0] == "5"
    assert outcome.error == ""
    assert outcome.returncode == 0


def test_cancel_already_set_spawns_nothing(tmp_path: Path) -> None:
    marker = tmp_path / "started"
    argv = [sys.executable, "-c", f"open({str(marker)!r}, 'w').close()"]
    cancel = asyncio.Event()
    cancel.set()
    spawned: list[int] = []

    async def hook(pid: int) -> None:
        spawned.append(pid)

    outcome = asyncio.run(run_process(argv, None, None, None, None, cancel, _noop, on_spawn=hook))

    assert outcome.error_class == "cancelled"
    assert outcome.returncode is None
    assert not outcome.spawned
    assert spawned == []
    assert not marker.exists()


def test_deadline_already_passed_reports_timeout_with_the_program_name() -> None:
    outcome, lines = _run(fake("sleeper.py"), deadline=time.monotonic() - 1)

    assert outcome.error == f"{os.path.basename(sys.executable)} subprocess timed out"
    assert outcome.error_class == "timeout"


def test_cancel_wins_over_an_expired_deadline() -> None:
    cancel = asyncio.Event()
    cancel.set()

    outcome, _ = _run(fake("sleeper.py"), cancel=cancel, deadline=time.monotonic() - 1)

    assert outcome.error_class == "cancelled"


def test_clean_exit_with_a_deadline_set_reports_no_error() -> None:
    outcome, lines = _run(fake("emit_both.py"), timeout_s=30)

    assert sorted(lines) == ["err-line", "out-line"]
    assert outcome.error == ""
    assert outcome.error_class == ""
    assert outcome.returncode == 0
    assert sorted(outcome.tail.splitlines()) == ["err-line", "out-line"]


def test_parser_failure_message_names_the_exception_type_and_keeps_the_tail() -> None:
    async def explode(line: str) -> None:
        raise KeyError("k")

    outcome, _ = _run(fake("exit_status.py"), on_line=explode, timeout_s=30)

    assert outcome.error == "KeyError: 'k'"
    assert outcome.error_class == "execution_error"
    assert outcome.tail == "failing"


def test_missing_executable_is_returned_as_missing_cli(tmp_path: Path) -> None:
    outcome, lines = _run([str(tmp_path / "does-not-exist")])

    assert outcome.error_class == "missing_cli"
    assert lines == []
    assert not outcome.spawned


@pytest.mark.skipif(os.name == "nt", reason="POSIX execute permission")
def test_non_executable_file_is_returned_as_spawn_failed(tmp_path: Path) -> None:
    target = tmp_path / "not-executable"
    target.write_text("#!/bin/sh\n", encoding="utf-8")
    target.chmod(0o644)

    outcome, _ = _run([str(target)])

    assert outcome.error_class == "spawn_failed"
