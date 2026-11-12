from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

import pytest

from coding_agent_cli import DriverRegistry, Outcome, Request, RunCompleted, TextDelta, run, run_sync
from coding_agent_cli.events import Event

from .test_process import wait_gone
from .toy_driver import ToyDriver


def _registry() -> tuple[DriverRegistry, ToyDriver]:
    driver = ToyDriver()
    registry = DriverRegistry(builtins=False)
    registry.register("toy", driver)
    return registry, driver


def _req(*args: str, timeout_s: float | None = None) -> Request:
    return Request(driver="toy", prompt="hello", cwd=Path.cwd(), args=args, timeout_s=timeout_s)


async def test_sequence_increases_and_one_run_completed() -> None:
    registry, _ = _registry()
    events: list[Event] = []

    async def on_event(event: Event) -> None:
        events.append(event)

    result = await run(_req("many_lines.py", "5"), on_event=on_event, registry=registry)
    assert result.outcome == Outcome.SUCCESS
    sequences = [event.sequence for event in events]
    assert sequences == sorted(set(sequences)) and sequences[0] == 1
    assert [type(e) for e in events].count(RunCompleted) == 1
    assert isinstance(events[-1], RunCompleted) and events[-1].result is result


async def test_cancel_before_spawn() -> None:
    registry, _ = _registry()
    cancel = asyncio.Event()
    cancel.set()
    spawned: list[int] = []

    async def on_spawn(pid: int) -> None:
        spawned.append(pid)

    result = await run(_req("sleeper.py"), cancel=cancel, on_spawn=on_spawn, registry=registry)
    assert result.outcome == Outcome.CANCELLED and spawned == []


async def test_cancel_during_run_keeps_partial_text() -> None:
    registry, _ = _registry()
    cancel = asyncio.Event()

    async def on_event(event: Event) -> None:
        if isinstance(event, TextDelta):
            cancel.set()

    result = await run(_req("sleeper.py"), on_event=on_event, cancel=cancel, registry=registry)
    assert result.outcome == Outcome.CANCELLED
    assert result.error is not None and result.error.code == "cancelled"
    assert "tick" in result.text


async def test_timeout_is_retryable() -> None:
    registry, _ = _registry()
    result = await run(_req("sleeper.py", timeout_s=0.5), registry=registry)
    assert result.outcome == Outcome.RETRYABLE
    assert result.error is not None and result.error.code == "timeout"


async def test_stalled_on_event_does_not_block_deadline() -> None:
    registry, _ = _registry()

    async def on_event(event: Event) -> None:
        if isinstance(event, TextDelta):
            await asyncio.sleep(30)

    start = time.monotonic()
    result = await run(_req("sleeper.py", timeout_s=0.5), on_event=on_event, registry=registry)
    assert time.monotonic() - start < 10
    assert result.error is not None and result.error.code == "timeout"


async def test_raising_on_event_propagates_after_kill(tmp_path: Path) -> None:
    registry, _ = _registry()
    pid_file = tmp_path / "pid"

    async def on_event(event: Event) -> None:
        if isinstance(event, TextDelta):
            raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        await run(_req("spawn_and_sleep.py", str(pid_file)), on_event=on_event, registry=registry)
    assert wait_gone(int(pid_file.read_text()))


def test_run_sync_outside_loop() -> None:
    registry, _ = _registry()
    assert run_sync(_req("many_lines.py", "1"), registry=registry).outcome == Outcome.SUCCESS


async def test_run_sync_inside_loop_raises() -> None:
    registry, _ = _registry()
    with pytest.raises(RuntimeError):
        run_sync(_req("many_lines.py", "1"), registry=registry)


async def test_cross_thread_cancel() -> None:
    registry, _ = _registry()
    cancel = asyncio.Event()
    loop = asyncio.get_running_loop()
    timer = threading.Timer(0.5, lambda: loop.call_soon_threadsafe(cancel.set))
    timer.start()
    try:
        result = await run(_req("sleeper.py", timeout_s=20), cancel=cancel, registry=registry)
    finally:
        timer.cancel()
    assert result.outcome == Outcome.CANCELLED


async def test_unknown_driver_raises_key_error() -> None:
    with pytest.raises(KeyError):
        await run(Request(driver="nope", prompt="p", cwd=Path.cwd()), registry=DriverRegistry(builtins=False))


async def test_temp_files_removed() -> None:
    registry, driver = _registry()
    await run(_req("many_lines.py", "1"), registry=registry)
    assert driver.temp_files and not any(path.exists() for path in driver.temp_files)
