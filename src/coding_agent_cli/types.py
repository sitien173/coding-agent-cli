"""Public value types: requests, results, usage, diagnostics, and capabilities."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType


class Outcome(StrEnum):
    """How a run ended. ``retryable`` means possibly transient, not safe to replay."""

    SUCCESS = "success"
    RETRYABLE = "retryable"
    AGENT_FATAL = "agent_fatal"
    REQUEST_FATAL = "request_fatal"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True, kw_only=True)
class Diagnostic:
    code: str
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
    executable: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "cwd", Path(self.cwd))
        object.__setattr__(self, "args", tuple(self.args))
        object.__setattr__(self, "env", MappingProxyType(dict(self.env)))
        if self.timeout_s is not None:
            if isinstance(self.timeout_s, bool) or not math.isfinite(self.timeout_s) or self.timeout_s <= 0:
                raise ValueError("timeout_s must be None or a finite positive number")


@dataclass(frozen=True, slots=True, kw_only=True)
class Result:
    outcome: Outcome
    text: str = ""
    session_id: str | None = None
    model: str | None = None
    usage: Usage = Usage()
    returncode: int | None = None
    error: Diagnostic | None = None
    warnings: tuple[Diagnostic, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class Capabilities:
    """What a driver can do on this machine. ``None`` means unverified."""

    available: bool
    executable: Path | None = None
    structured_streaming: bool | None = None


__all__ = ["Capabilities", "Diagnostic", "Outcome", "Request", "Result", "Usage"]
