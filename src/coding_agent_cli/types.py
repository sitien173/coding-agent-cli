"""Public value types: requests, results, usage, diagnostics, and capabilities."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType


class ErrorCode(StrEnum):
    """Normalized error and warning codes produced by validation, transport, and drivers."""

    BAD_CWD = "bad_cwd"
    INVALID_ARGS = "invalid_args"
    UNSUPPORTED_OPTION = "unsupported_option"
    INVALID_PROMPT = "invalid_prompt"
    MISSING_CLI = "missing_cli"
    SPAWN_FAILED = "spawn_failed"
    AUTH = "auth"
    INVALID_MODEL = "invalid_model"
    CONTEXT_OVERFLOW = "context_overflow"
    SERVICE_ERROR = "service_error"
    MAX_TURNS = "max_turns"
    BUDGET = "budget"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    IDLE_TIMEOUT = "idle_timeout"
    STDIN_FAILED = "stdin_failed"
    EXECUTION_ERROR = "execution_error"
    PARSER_ERROR = "parser_error"
    NO_OUTPUT = "no_output"
    NO_SESSION_ID = "no_session_id"
    LINE_TOO_LONG = "line_too_long"
    CLEANUP_INCOMPLETE = "cleanup_incomplete"


class Outcome(StrEnum):
    """How a run ended. ``retryable`` means possibly transient, not safe to replay."""

    SUCCESS = "success"
    RETRYABLE = "retryable"
    LIMIT_REACHED = "limit_reached"
    AGENT_FATAL = "agent_fatal"
    REQUEST_FATAL = "request_fatal"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True, kw_only=True)
class Diagnostic:
    code: ErrorCode
    message: str


@dataclass(frozen=True, slots=True, kw_only=True)
class Usage:
    """Token usage for one invocation. Unknown values are ``None``, never zero.

    ``input`` includes cache reads and writes once.
    """

    input: int | None = None
    output: int | None = None
    cache_read: int | None = None
    cache_write: int | None = None
    context_used: int | None = None
    context_window: int | None = None


def _validate_timeout(value: float | None, name: str) -> None:
    if value is not None:
        if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"{name} must be None or a finite positive number")


@dataclass(frozen=True, slots=True, kw_only=True)
class Request:
    """One prompt for one driver. ``env`` overlays the inherited environment."""

    driver: str
    prompt: str
    cwd: Path
    session_id: str | None = None
    model: str | None = None
    reasoning: str | None = None
    system_prompt: str | None = None
    args: tuple[str, ...] = ()
    env: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    timeout_s: float | None = None
    idle_timeout_s: float | None = None
    raw_events: bool = False
    executable: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "cwd", Path(self.cwd))
        object.__setattr__(self, "args", tuple(self.args))
        object.__setattr__(self, "env", MappingProxyType(dict(self.env)))
        _validate_timeout(self.timeout_s, "timeout_s")
        _validate_timeout(self.idle_timeout_s, "idle_timeout_s")


@dataclass(frozen=True, slots=True, kw_only=True)
class Result:
    outcome: Outcome
    text: str = ""
    session_id: str | None = None
    model: str | None = None
    usage: Usage = Usage()
    cost_usd: float | None = None
    duration_s: float = 0.0
    num_turns: int | None = None
    stop_reason: str | None = None
    returncode: int | None = None
    error: Diagnostic | None = None
    warnings: tuple[Diagnostic, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class Capabilities:
    """What a driver can do on this machine. ``None`` means unverified."""

    available: bool
    executable: Path | None = None
    version: str | None = None
    structured_streaming: bool | None = None


__all__ = ["Capabilities", "Diagnostic", "ErrorCode", "Outcome", "Request", "Result", "Usage"]
