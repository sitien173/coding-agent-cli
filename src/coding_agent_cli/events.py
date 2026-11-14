"""Typed run events. Consumers must ignore kinds they do not know."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar, Literal

from .types import Result, Usage

SCHEMA_VERSION = 2

type JsonValue = None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]


@dataclass(frozen=True, slots=True, kw_only=True)
class SessionIdentified:
    kind: ClassVar[str] = "session.identified"
    session_id: str
    sequence: int = 0
    entity_id: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class MessageStarted:
    kind: ClassVar[str] = "assistant.message.started"
    sequence: int = 0
    entity_id: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class TextDelta:
    kind: ClassVar[str] = "assistant.text.delta"
    text: str
    sequence: int = 0
    entity_id: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ReasoningDelta:
    kind: ClassVar[str] = "assistant.reasoning.delta"
    text: str
    sequence: int = 0
    entity_id: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class MessageCompleted:
    kind: ClassVar[str] = "assistant.message.completed"
    sequence: int = 0
    entity_id: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ToolStarted:
    kind: ClassVar[str] = "tool.started"
    tool: str
    activity: Literal["command", "tool_call"]
    input: Any = None
    sequence: int = 0
    entity_id: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ToolCompleted:
    kind: ClassVar[str] = "tool.completed"
    tool: str
    status: Literal["completed", "error", "unknown"]
    output: JsonValue | None = None
    sequence: int = 0
    entity_id: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class FileChanged:
    kind: ClassVar[str] = "file.changed"
    path: str
    change: Literal["added", "modified", "deleted", "unknown"]
    evidence: Literal["reported", "inferred"]
    sequence: int = 0
    entity_id: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class Notice:
    kind: ClassVar[str] = "notice"
    code: str
    message: str
    sequence: int = 0
    entity_id: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class UsageUpdated:
    """A full replacement snapshot of usage so far, not an increment."""

    kind: ClassVar[str] = "usage.updated"
    usage: Usage
    sequence: int = 0
    entity_id: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class RawEvent:
    kind: ClassVar[str] = "raw"
    stream: Literal["stdout", "stderr"]
    data: dict[str, Any]
    sequence: int = 0
    entity_id: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class RunCompleted:
    kind: ClassVar[str] = "run.completed"
    result: Result
    sequence: int = 0
    entity_id: str | None = None


type Event = (
    SessionIdentified
    | MessageStarted
    | TextDelta
    | ReasoningDelta
    | MessageCompleted
    | ToolStarted
    | ToolCompleted
    | FileChanged
    | Notice
    | UsageUpdated
    | RawEvent
    | RunCompleted
)

__all__ = [
    "SCHEMA_VERSION",
    "Event",
    "FileChanged",
    "JsonValue",
    "MessageCompleted",
    "MessageStarted",
    "Notice",
    "RawEvent",
    "ReasoningDelta",
    "RunCompleted",
    "SessionIdentified",
    "TextDelta",
    "ToolCompleted",
    "ToolStarted",
    "UsageUpdated",
]
