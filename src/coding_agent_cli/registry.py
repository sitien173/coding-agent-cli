"""Driver protocol and the instance-scoped driver registry."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Protocol, runtime_checkable

from .events import Event
from .types import Capabilities, Request, Result

EventHandler = Callable[[Event], Awaitable[None]]
SpawnHandler = Callable[[int], Awaitable[None]]


@runtime_checkable
class Driver(Protocol):
    """Runs one request through one CLI.

    ``run`` awaits ``on_event`` for each event it produces, leaving ``sequence`` to the API,
    and never emits ``RunCompleted``. It kills the process tree before returning.
    """

    async def check(self, req: Request, *, refresh: bool = False) -> Capabilities: ...

    async def run(
        self,
        req: Request,
        *,
        on_event: EventHandler,
        cancel: asyncio.Event,
        on_spawn: SpawnHandler | None,
    ) -> Result: ...


def _builtin_drivers() -> dict[str, Driver]:
    # Imported here: the drivers import this module for the handler aliases.
    from ._drivers.agy import AgyDriver
    from ._drivers.claude import ClaudeDriver
    from ._drivers.codex import CodexDriver
    from ._drivers.pi import PiDriver

    return {"claude": ClaudeDriver(), "pi": PiDriver(), "codex": CodexDriver(), "agy": AgyDriver()}


class DriverRegistry:
    """Maps driver names to drivers. Each instance is independent."""

    def __init__(self, *, builtins: bool = True) -> None:
        self._drivers: dict[str, Driver] = _builtin_drivers() if builtins else {}

    def register(self, name: str, driver: Driver, *, replace: bool = False) -> None:
        if name in self._drivers and not replace:
            raise ValueError(f"driver {name!r} is already registered")
        self._drivers[name] = driver

    def get(self, name: str) -> Driver:
        try:
            return self._drivers[name]
        except KeyError:
            raise KeyError(f"unknown driver {name!r}") from None

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._drivers))


__all__ = ["Driver", "DriverRegistry", "EventHandler", "SpawnHandler"]
