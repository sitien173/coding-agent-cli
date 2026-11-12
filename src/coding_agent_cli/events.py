"""Typed run events. Consumers must ignore kinds they do not know."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar, Literal

from .types import Result, Usage

SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True, kw_only=True)
class SessionIdentified:
    kind: ClassVar[str] = "session.identified"
    session_id: str
    sequence: int = 0
    entity_id: str | None = None
    schema_version: int = SCHEMA_VERSION


@dataclass(frozen=True, slots=True, kw_only=True)
class MessageStarted:
    kind: ClassVar[str] = "assistant.message.started"
    sequence: int = 0
    entity_id: str | None = None
    schema_version: int = SCHEMA_VERSION


@dataclass(frozen=True, slots=True, kw_only=True)
class TextDelta:
    kind: ClassVar[str] = "assistant.text.delta"
    text: str
    sequence: int = 0
    entity_id: str | None = None
    schema_version: int = SCHEMA_VERSION


@dataclass(frozen=True, slots=True, kw_only=True)
class MessageCompleted:
    kind: ClassVar[str] = "assistant.message.completed"
    sequence: int = 0
    entity_id: str | None = None
    schema_version: int = SCHEMA_VERSION


@dataclass(frozen=True, slots=True, kw_only=True)
class ToolStarted:
    kind: ClassVar[str] = "tool.started"
    tool: str
    activity: Literal["command", "tool_call"]
    input: Any = None
    sequence: int = 0
    entity_id: str | None = None
    schema_version: int = SCHEMA_VERSION


@dataclass(frozen=True, slots=True, kw_only=True)
class ToolCompleted:
    kind: ClassVar[str] = "tool.completed"
    status: Literal["completed", "error", "unknown"]
    sequence: int = 0
    entity_id: str | None = None
    schema_version: int = SCHEMA_VERSION


@dataclass(frozen=True, slots=True, kw_only=True)
class UsageUpdated:
    """A full replacement snapshot of usage so far, not an increment."""

    kind: ClassVar[str] = "usage.updated"
    usage: Usage
    sequence: int = 0
    entity_id: str | None = None
    schema_version: int = SCHEMA_VERSION


@dataclass(frozen=True, slots=True, kw_only=True)
class RunCompleted:
    kind: ClassVar[str] = "run.completed"
    result: Result
    sequence: int = 0
    entity_id: str | None = None
    schema_version: int = SCHEMA_VERSION


type Event = (
    SessionIdentified
    | MessageStarted
    | TextDelta
    | MessageCompleted
    | ToolStarted
    | ToolCompleted
    | UsageUpdated
    | RunCompleted
)

__all__ = [
    "SCHEMA_VERSION",
    "Event",
    "MessageCompleted",
    "MessageStarted",
    "RunCompleted",
    "SessionIdentified",
    "TextDelta",
    "ToolCompleted",
    "ToolStarted",
    "UsageUpdated",
]
