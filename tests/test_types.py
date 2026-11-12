from __future__ import annotations

import dataclasses
import math
from pathlib import Path

import pytest

from coding_agent_cli import Capabilities, Diagnostic, Outcome, Request, Result, Usage


def _request(**kwargs) -> Request:
    return Request(driver="toy", prompt="p", cwd=Path("."), **kwargs)


def test_outcome_values() -> None:
    assert [o.value for o in Outcome] == ["success", "retryable", "agent_fatal", "request_fatal", "cancelled"]


@pytest.mark.parametrize(
    ("value", "field"),
    [
        (_request(), "prompt"),
        (Result(outcome=Outcome.SUCCESS), "text"),
        (Usage(), "input"),
        (Diagnostic(code="c", message="m"), "code"),
        (Capabilities(available=True), "available"),
    ],
)
def test_value_types_are_frozen(value: object, field: str) -> None:
    with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
        setattr(value, field, "x")


def test_value_types_reject_new_attributes() -> None:
    with pytest.raises((dataclasses.FrozenInstanceError, AttributeError, TypeError)):
        setattr(Usage(), "extra", 1)  # noqa: B010 - pyright rejects the direct assignment this test exercises


def test_request_env_is_an_immutable_copy() -> None:
    env = {"A": "1"}
    req = _request(env=env)
    env["A"] = "2"
    env["B"] = "3"
    assert dict(req.env) == {"A": "1"}
    with pytest.raises(TypeError):
        req.env["A"] = "x"  # pyright: ignore[reportIndexIssue]


def test_request_args_is_a_tuple() -> None:
    assert _request(args=["--x", "y"]).args == ("--x", "y")  # pyright: ignore[reportArgumentType]


def test_request_cwd_is_a_path() -> None:
    assert isinstance(_request().cwd, Path)


@pytest.mark.parametrize("timeout", [0, -1, -0.5, math.nan, math.inf, -math.inf])
def test_request_rejects_bad_timeout(timeout: float) -> None:
    with pytest.raises(ValueError):
        _request(timeout_s=timeout)


@pytest.mark.parametrize("timeout", [None, 0.1, 5, 3600.0])
def test_request_accepts_valid_timeout(timeout: float | None) -> None:
    assert _request(timeout_s=timeout).timeout_s == timeout


def test_defaults() -> None:
    req = _request()
    assert (req.session_id, req.model, req.reasoning, req.system_prompt, req.executable, req.timeout_s) == (None,) * 6
    assert req.args == ()
    assert dict(req.env) == {}
    result = Result(outcome=Outcome.SUCCESS)
    assert result.usage == Usage()
    assert result.warnings == ()
    assert Usage() == Usage(
        input=None, output=None, cache_read=None, cache_write=None, context_used=None, context_window=None
    )
    assert Capabilities(available=False).structured_streaming is None


def test_field_names_match_design() -> None:
    names = lambda cls: [f.name for f in dataclasses.fields(cls)]  # noqa: E731
    assert names(Request) == [
        "driver",
        "prompt",
        "cwd",
        "session_id",
        "model",
        "reasoning",
        "system_prompt",
        "args",
        "env",
        "timeout_s",
        "executable",
    ]
    assert names(Result) == ["outcome", "text", "session_id", "model", "usage", "returncode", "error", "warnings"]
    assert names(Usage) == ["input", "output", "cache_read", "cache_write", "context_used", "context_window"]
    assert names(Diagnostic) == ["code", "message"]
    assert names(Capabilities) == ["available", "executable", "structured_streaming"]
