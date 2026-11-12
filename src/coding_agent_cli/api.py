"""Public entry points: ``run``, ``stream``, ``run_sync``, and ``check``."""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from collections.abc import Awaitable, Callable
from types import TracebackType

from .events import Event, RunCompleted
from .registry import DriverRegistry, SpawnHandler
from .types import Capabilities, Request, Result

log = logging.getLogger("coding_agent_cli")

STREAM_QUEUE_SIZE = 256

_default_registry: DriverRegistry | None = None


def _registry(registry: DriverRegistry | None) -> DriverRegistry:
    global _default_registry
    if registry is not None:
        return registry
    if _default_registry is None:
        _default_registry = DriverRegistry()
    return _default_registry


async def run(
    req: Request,
    *,
    on_event: Callable[[Event], Awaitable[None]] | None = None,
    cancel: asyncio.Event | None = None,
    on_spawn: SpawnHandler | None = None,
    registry: DriverRegistry | None = None,
) -> Result:
    """Run ``req`` to completion and return its ``Result``.

    ``on_event`` receives each event with a strictly increasing ``sequence``, ending with
    exactly one ``RunCompleted`` after the process tree is gone. An exception from
    ``on_event`` or ``on_spawn`` is raised after cleanup. Setting ``cancel`` ends the run
    with a ``cancelled`` result; from another thread use ``loop.call_soon_threadsafe(cancel.set)``.
    Raises ``KeyError`` for an unknown driver.
    """
    driver = _registry(registry).get(req.driver)
    sequence = 0
    failures: list[BaseException] = []

    async def emit(event: Event) -> None:
        nonlocal sequence
        if isinstance(event, RunCompleted):
            return
        sequence += 1
        if on_event is None:
            return
        try:
            await on_event(dataclasses.replace(event, sequence=sequence))
        except Exception as exc:
            failures.append(exc)
            raise

    log.debug("run start driver=%s", req.driver)
    result = await driver.run(req, on_event=emit, cancel=cancel or asyncio.Event(), on_spawn=on_spawn)
    if failures:
        raise failures[0]
    log.debug("run done driver=%s outcome=%s", req.driver, result.outcome)
    if on_event is not None:
        await on_event(RunCompleted(result=result, sequence=sequence + 1))
    return result


class EventStream:
    """Async iterator over one run's events, ending after ``RunCompleted``.

    Use it as ``async with stream(req) as events: async for event in events: ...``.
    Leaving the block or calling ``aclose()`` cancels the run and kills the process tree.
    A bare ``async for`` without ``async with`` is safe only when it runs to the end.
    """

    def __init__(
        self,
        req: Request,
        *,
        cancel: asyncio.Event | None,
        on_spawn: SpawnHandler | None,
        registry: DriverRegistry | None,
    ) -> None:
        self._req = req
        self._cancel = cancel
        self._on_spawn = on_spawn
        self._registry = registry
        self._queue: asyncio.Queue[Event] = asyncio.Queue(STREAM_QUEUE_SIZE)
        self._task: asyncio.Task[Result] | None = None
        self._finished = False

    def _start(self) -> asyncio.Task[Result]:
        if self._task is None:
            self._task = asyncio.ensure_future(
                run(
                    self._req,
                    on_event=self._queue.put,
                    cancel=self._cancel,
                    on_spawn=self._on_spawn,
                    registry=self._registry,
                )
            )
        return self._task

    def __aiter__(self) -> EventStream:
        return self

    async def __anext__(self) -> Event:
        if self._finished:
            raise StopAsyncIteration
        task = self._start()
        while True:
            if not self._queue.empty():
                return self._queue.get_nowait()
            if task.done():
                self._finished = True
                if not task.cancelled() and task.exception() is not None:
                    raise task.exception()  # type: ignore[misc]
                raise StopAsyncIteration
            getter = asyncio.ensure_future(self._queue.get())
            try:
                await asyncio.wait({getter, task}, return_when=asyncio.FIRST_COMPLETED)
            finally:
                if not getter.done():
                    getter.cancel()
            if getter.done() and not getter.cancelled():
                return getter.result()

    async def aclose(self) -> None:
        """Cancel the run if it is still going and wait for its cleanup."""
        self._finished = True
        task = self._task
        if task is None:
            return
        if not task.done():
            task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                raise
        except Exception:
            pass

    async def __aenter__(self) -> EventStream:
        self._start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()


def stream(
    req: Request,
    *,
    cancel: asyncio.Event | None = None,
    on_spawn: SpawnHandler | None = None,
    registry: DriverRegistry | None = None,
) -> EventStream:
    """Return an ``EventStream`` for ``req``. The run starts on ``async with`` or the first ``__anext__``."""
    return EventStream(req, cancel=cancel, on_spawn=on_spawn, registry=registry)


def run_sync(req: Request, *, registry: DriverRegistry | None = None) -> Result:
    """Run ``req`` with ``asyncio.run``. Raises ``RuntimeError`` inside a running event loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(run(req, registry=registry))
    raise RuntimeError("run_sync() cannot be called from a running event loop; await run() instead")


async def check(req: Request, *, registry: DriverRegistry | None = None, refresh: bool = False) -> Capabilities:
    """Report whether the driver's CLI is available and supports structured streaming."""
    return await _registry(registry).get(req.driver).check(req, refresh=refresh)


__all__ = ["EventStream", "check", "run", "run_sync", "stream"]
