"""Public entry points: ``run``, ``stream``, ``run_sync``, and ``check``."""

from __future__ import annotations

import asyncio
import inspect
import threading
from collections.abc import Awaitable, Callable, Mapping
from types import TracebackType

from .events import Event
from .registry import DriverRegistry
from .types import Capabilities, Request, Result

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
    on_event: Callable[[Event], Awaitable[None] | None] | None = None,
    cancel: asyncio.Event | None = None,
    on_spawn: Callable[[int], Awaitable[None] | None] | None = None,
    registry: DriverRegistry | None = None,
) -> Result:
    driver = _registry(registry).get(req.driver)

    async def emit(event: Event) -> None:
        if on_event is not None:
            result = on_event(event)
            if inspect.isawaitable(result):
                await result

    return await driver.run(req, on_event=emit, cancel=cancel or asyncio.Event(), on_spawn=on_spawn)


class EventStream:
    def __init__(
        self,
        req: Request,
        *,
        cancel: asyncio.Event | None,
        on_spawn: Callable[[int], Awaitable[None] | None] | None,
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
            self._task = asyncio.create_task(
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
            getter = asyncio.create_task(self._queue.get())
            try:
                await asyncio.wait({getter, task}, return_when=asyncio.FIRST_COMPLETED)
            finally:
                if not getter.done():
                    getter.cancel()
            if getter.done() and not getter.cancelled():
                return getter.result()

    async def aclose(self) -> None:
        self._finished = True
        task = self._task
        if task is not None and not task.done():
            task.cancel()
        if task is not None:
            try:
                async with asyncio.timeout(12):
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
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        await self.aclose()


def stream(
    req: Request,
    *,
    cancel: asyncio.Event | None = None,
    on_spawn: Callable[[int], Awaitable[None] | None] | None = None,
    registry: DriverRegistry | None = None,
) -> EventStream:
    return EventStream(req, cancel=cancel, on_spawn=on_spawn, registry=registry)


def run_sync(
    req: Request,
    *,
    on_event: Callable[[Event], None] | None = None,
    cancel: threading.Event | None = None,
    registry: DriverRegistry | None = None,
) -> Result:
    try:
        asyncio.get_running_loop()
    except RuntimeError:

        async def execute() -> Result:
            async_cancel = asyncio.Event()
            if cancel is not None and cancel.is_set():
                async_cancel.set()

            async def watch_cancel() -> None:
                if cancel is None:
                    return
                while not cancel.is_set():
                    try:
                        async with asyncio.timeout(0.1):
                            await async_cancel.wait()
                    except TimeoutError:
                        continue
                async_cancel.set()

            watcher = asyncio.create_task(watch_cancel())
            try:
                return await run(req, on_event=on_event, cancel=async_cancel, registry=registry)
            finally:
                watcher.cancel()
                try:
                    async with asyncio.timeout(0.2):
                        await watcher
                except asyncio.CancelledError:
                    pass

        return asyncio.run(execute())
    raise RuntimeError("run_sync() cannot be called from a running event loop; await run() instead")


async def check(
    driver: str,
    *,
    executable: str | None = None,
    env: Mapping[str, str] | None = None,
    refresh: bool = False,
    registry: DriverRegistry | None = None,
) -> Capabilities:
    return await _registry(registry).get(driver).check(executable=executable, env=env, refresh=refresh)


__all__ = ["EventStream", "check", "run", "run_sync", "stream"]
