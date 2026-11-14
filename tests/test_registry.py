from __future__ import annotations

import pytest

import coding_agent_cli
from coding_agent_cli import CliDriver, DriverRegistry
from coding_agent_cli._drivers.agy import AgyDriver
from coding_agent_cli._drivers.claude import ClaudeDriver
from coding_agent_cli._drivers.codex import CodexDriver
from coding_agent_cli._drivers.pi import PiDriver

from .toy_driver import ToyDriver


def test_public_exports_include_cli_driver_api_without_low_level_driver() -> None:
    assert {"CliDriver", "Launch", "Parser", "Facts", "RequestError", "ErrorCode"} <= set(coding_agent_cli.__all__)
    assert "Driver" not in coding_agent_cli.__all__
    assert not hasattr(coding_agent_cli, "Driver")


def test_register_and_get_cli_driver() -> None:
    registry = DriverRegistry(builtins=False)
    driver = ToyDriver()
    registry.register("toy", driver)
    assert registry.get("toy") is driver
    assert registry.names() == ("toy",)
    assert isinstance(driver, CliDriver)


def test_registry_rejects_non_cli_driver() -> None:
    with pytest.raises(TypeError, match="CliDriver"):
        DriverRegistry(builtins=False).register("bad", object())  # type: ignore[arg-type]


def test_duplicate_name_is_rejected_unless_replaced() -> None:
    registry = DriverRegistry(builtins=False)
    registry.register("toy", ToyDriver())
    with pytest.raises(ValueError):
        registry.register("toy", ToyDriver())
    replacement = ToyDriver()
    registry.register("toy", replacement, replace=True)
    assert registry.get("toy") is replacement


def test_unknown_name_raises_key_error() -> None:
    with pytest.raises(KeyError):
        DriverRegistry(builtins=False).get("missing")


def test_builtin_drivers_are_registered() -> None:
    registry = DriverRegistry()
    assert registry.names() == ("agy", "claude", "codex", "pi")
    assert isinstance(registry.get("agy"), AgyDriver)
    assert isinstance(registry.get("claude"), ClaudeDriver)
    assert isinstance(registry.get("codex"), CodexDriver)
    assert isinstance(registry.get("pi"), PiDriver)


def test_default_registry_resolves_builtins() -> None:
    from coding_agent_cli import api

    registry = api._registry(None)
    assert registry is api._registry(None)
    expected = {"agy": AgyDriver, "claude": ClaudeDriver, "codex": CodexDriver, "pi": PiDriver}
    for name, cls in expected.items():
        assert isinstance(registry.get(name), cls)


def test_registries_are_independent() -> None:
    first = DriverRegistry(builtins=False)
    second = DriverRegistry(builtins=False)
    first.register("toy", ToyDriver())
    assert second.names() == ()


def test_names_are_sorted() -> None:
    registry = DriverRegistry(builtins=False)
    registry.register("b", ToyDriver())
    registry.register("a", ToyDriver())
    assert registry.names() == ("a", "b")
