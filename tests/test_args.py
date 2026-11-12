from __future__ import annotations

from pathlib import Path

import pytest

from coding_agent_cli import Request
from coding_agent_cli._args import reject_conflicts, reject_transport_flags, reject_unsupported, validate_args


def _req(**kwargs) -> Request:
    return Request(driver="d", prompt="p", cwd=Path("."), **kwargs)


@pytest.mark.parametrize("args", [(1,), ("a\x00b",), ("--",)])
def test_invalid_args(args) -> None:
    error = validate_args(args)
    assert error is not None and error.code == "invalid_args"


def test_valid_args() -> None:
    assert validate_args(("--x", "y", "-")) is None


def test_transport_flags_rejected_bare_and_with_value() -> None:
    owned = ("--output-format", "-p")
    assert reject_transport_flags(("--output-format", "json"), owned).code == "invalid_args"  # type: ignore[union-attr]
    assert reject_transport_flags(("--output-format=json",), owned).code == "invalid_args"  # type: ignore[union-attr]
    assert reject_transport_flags(("--output-formats",), owned) is None


def test_unsupported_option() -> None:
    error = reject_unsupported(_req(system_prompt="s"), ("model",))
    assert error is not None and error.code == "unsupported_option"
    assert reject_unsupported(_req(model="m"), ("model",)) is None


def test_conflicting_portable_option() -> None:
    flags = {"model": ("--model",)}
    assert reject_conflicts(_req(model="m", args=("--model", "x")), flags).code == "invalid_args"  # type: ignore[union-attr]
    assert reject_conflicts(_req(model="m", args=("--model=x",)), flags) is not None
    assert reject_conflicts(_req(args=("--model", "x")), flags) is None
