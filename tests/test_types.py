from __future__ import annotations

import dataclasses
import math
from pathlib import Path

import pytest

import coding_agent_cli.types as types_mod
from coding_agent_cli import Capabilities, Diagnostic, Outcome, Request, Result, Usage


def _request(**kwargs) -> Request:
    return Request(driver="toy", prompt="p", cwd=Path("."), **kwargs)


def test_outcome_values() -> None:
    assert [o.value for o in Outcome] == [
        "success",
        "retryable",
        "limit_reached",
        "agent_fatal",
        "request_fatal",
        "cancelled",
    ]


def test_error_code_values() -> None:
    error_code_cls = getattr(types_mod, "ErrorCode", None)
    assert error_code_cls is not None
    expected = [
        "bad_cwd",
        "invalid_args",
        "unsupported_option",
        "invalid_prompt",
        "missing_cli",
        "spawn_failed",
        "auth",
        "invalid_model",
        "context_overflow",
        "service_error",
        "max_turns",
        "budget",
        "cancelled",
        "timeout",
        "idle_timeout",
        "stdin_failed",
        "execution_error",
        "parser_error",
        "no_output",
        "no_session_id",
        "line_too_long",
        "cleanup_incomplete",
    ]
    assert [c.value for c in error_code_cls] == expected


@pytest.mark.parametrize(
    ("value", "field"),
    [
        (_request(), "prompt"),
        (Result(outcome=Outcome.SUCCESS), "text"),
        (Usage(), "input"),
        (Diagnostic(code="bad_cwd", message="m"), "code"),  # type: ignore[arg-type]
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


@pytest.mark.parametrize("timeout", [0, -1, -0.5, math.nan, math.inf, -math.inf, True])
def test_request_rejects_bad_timeout(timeout: float) -> None:
    with pytest.raises(ValueError):
        _request(timeout_s=timeout)


@pytest.mark.parametrize("timeout", [None, 0.1, 5, 3600.0])
def test_request_accepts_valid_timeout(timeout: float | None) -> None:
    assert _request(timeout_s=timeout).timeout_s == timeout


@pytest.mark.parametrize("timeout", [0, -1, -0.5, math.nan, math.inf, -math.inf, True])
def test_request_rejects_bad_idle_timeout(timeout: float) -> None:
    with pytest.raises(ValueError):
        _request(idle_timeout_s=timeout)


@pytest.mark.parametrize("timeout", [None, 0.1, 5, 3600.0])
def test_request_accepts_valid_idle_timeout(timeout: float | None) -> None:
    assert _request(idle_timeout_s=timeout).idle_timeout_s == timeout


def test_defaults() -> None:
    req = _request()
    assert (
        req.session_id,
        req.model,
        req.reasoning,
        req.system_prompt,
        req.executable,
        req.timeout_s,
        req.idle_timeout_s,
    ) == (None,) * 7
    assert req.raw_events is False
    assert req.args == ()
    assert dict(req.env) == {}
    result = Result(outcome=Outcome.SUCCESS)
    assert result.usage == Usage()
    assert result.warnings == ()
    assert result.cost_usd is None
    assert result.duration_s == 0.0
    assert result.num_turns is None
    assert result.stop_reason is None
    assert Usage() == Usage(
        input=None, output=None, cache_read=None, cache_write=None, context_used=None, context_window=None
    )
    caps = Capabilities(available=False)
    assert caps.version is None
    assert caps.structured_streaming is None


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
        "idle_timeout_s",
        "raw_events",
        "executable",
    ]
    assert names(Result) == [
        "outcome",
        "text",
        "session_id",
        "model",
        "usage",
        "cost_usd",
        "duration_s",
        "num_turns",
        "stop_reason",
        "returncode",
        "error",
        "warnings",
    ]
    assert names(Usage) == ["input", "output", "cache_read", "cache_write", "context_used", "context_window"]
    assert names(Diagnostic) == ["code", "message"]
    assert names(Capabilities) == ["available", "executable", "version", "structured_streaming"]
