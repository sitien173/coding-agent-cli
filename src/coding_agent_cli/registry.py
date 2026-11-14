"""Instance-scoped registry for public CLI drivers."""

from __future__ import annotations

from .driver import CliDriver


def _builtin_drivers() -> dict[str, CliDriver]:
    from ._drivers.agy import AgyDriver
    from ._drivers.claude import ClaudeDriver
    from ._drivers.codex import CodexDriver
    from ._drivers.pi import PiDriver

    return {
        "agy": AgyDriver(),
        "claude": ClaudeDriver(),
        "codex": CodexDriver(),
        "pi": PiDriver(),
    }


class DriverRegistry:
    def __init__(self, *, builtins: bool = True) -> None:
        self._drivers = _builtin_drivers() if builtins else {}

    def register(self, name: str, driver: CliDriver, *, replace: bool = False) -> None:
        if not isinstance(driver, CliDriver):
            raise TypeError("driver must be a CliDriver instance")
        if name in self._drivers and not replace:
            raise ValueError(f"driver {name!r} is already registered")
        self._drivers[name] = driver

    def get(self, name: str) -> CliDriver:
        try:
            return self._drivers[name]
        except KeyError:
            raise KeyError(f"unknown driver {name!r}") from None

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._drivers))


__all__ = ["DriverRegistry"]
