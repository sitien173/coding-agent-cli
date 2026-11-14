from __future__ import annotations

import asyncio
import time
from pathlib import Path

from coding_agent_cli import DriverRegistry, Outcome, Request, RunCompleted, TextDelta, stream

from .procs import pid_running
from .toy_driver import ToyDriver


def wait_gone(pid: int, timeout_s: float = 3) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not pid_running(pid):
            return True
        time.sleep(0.02)
    return False


def _registry() -> DriverRegistry:
    registry = DriverRegistry(builtins=False)
    registry.register("toy", ToyDriver())
    return registry


def _req(*args: str, timeout_s: float | None = None) -> Request:
    return Request(driver="toy", prompt="hello", cwd=Path.cwd(), args=args, timeout_s=timeout_s)


async def test_stream_ends_with_run_completed() -> None:
    async with stream(_req("many_lines.py", "3"), registry=_registry()) as events:
        seen = [event async for event in events]
    assert isinstance(seen[-1], RunCompleted) and seen[-1].result.outcome == Outcome.SUCCESS
    assert sum(isinstance(event, TextDelta) for event in seen) == 3


async def test_stream_allows_silence_longer_than_one_and_a_half_seconds() -> None:
    req = Request(driver="toy", prompt="hello", cwd=Path.cwd(), args=("silent_then_output.py", "1.7"), timeout_s=3)
    async with asyncio.timeout(2.5):
        async with stream(req, registry=_registry()) as events:
            seen = [event async for event in events]
    assert any(isinstance(event, TextDelta) and event.text == "delayed-output" for event in seen)
    assert isinstance(seen[-1], RunCompleted)


async def test_early_break_kills_tree(tmp_path: Path) -> None:
    pid_file = tmp_path / "pid"
    async with stream(_req("spawn_and_sleep.py", str(pid_file)), registry=_registry()) as events:
        async for event in events:
            if isinstance(event, TextDelta):
                break
    assert wait_gone(int(pid_file.read_text()))


async def test_aclose_kills_tree(tmp_path: Path) -> None:
    pid_file = tmp_path / "pid"
    events = stream(_req("spawn_and_sleep.py", str(pid_file)), registry=_registry())
    async with asyncio.timeout(1.5):
        async with events:
            event = await events.__anext__()
            while not isinstance(event, TextDelta):
                event = await events.__anext__()
            await events.aclose()
    assert wait_gone(int(pid_file.read_text()))


async def test_full_queue_plus_cancel_terminates() -> None:
    cancel = asyncio.Event()
    events = stream(_req("many_lines.py", "5000"), cancel=cancel, registry=_registry())
    async with events:
        first = await events.__anext__()
        async with asyncio.timeout(1):
            await asyncio.sleep(0.2)
        cancel.set()
        last = first
        async with asyncio.timeout(15):
            async for event in events:
                last = event
    assert isinstance(last, RunCompleted)


async def test_aclose_without_start_is_noop() -> None:
    await stream(_req("many_lines.py", "1"), registry=_registry()).aclose()
