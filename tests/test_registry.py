from __future__ import annotations

import pytest

from coding_agent_cli import Capabilities, Driver, DriverRegistry, Outcome, Request, Result


class _Stub:
    async def check(self, req: Request, *, refresh: bool = False) -> Capabilities:
        return Capabilities(available=True)

    async def run(self, req: Request, *, on_event, cancel, on_spawn) -> Result:
        return Result(outcome=Outcome.SUCCESS, text="ok")


def test_register_and_get() -> None:
    registry = DriverRegistry(builtins=False)
    stub = _Stub()
    registry.register("toy", stub)
    assert registry.get("toy") is stub
    assert registry.names() == ("toy",)
    assert isinstance(stub, Driver)


def test_duplicate_name_is_rejected_unless_replaced() -> None:
    registry = DriverRegistry(builtins=False)
    registry.register("toy", _Stub())
    with pytest.raises(ValueError):
        registry.register("toy", _Stub())
    replacement = _Stub()
    registry.register("toy", replacement, replace=True)
    assert registry.get("toy") is replacement


def test_unknown_name_raises_key_error() -> None:
    with pytest.raises(KeyError):
        DriverRegistry(builtins=False).get("missing")


def test_builtin_drivers_are_claude_pi_codex_and_agy() -> None:
    assert set(DriverRegistry().names()) == {"claude", "pi", "codex", "agy"}


def test_registries_are_independent() -> None:
    first = DriverRegistry(builtins=False)
    second = DriverRegistry(builtins=False)
    first.register("toy", _Stub())
    assert second.names() == ()


def test_names_are_sorted() -> None:
    registry = DriverRegistry(builtins=False)
    registry.register("b", _Stub())
    registry.register("a", _Stub())
    assert registry.names() == ("a", "b")
