"""Run coding-agent CLIs, stream typed events, and return a classified result."""

from .api import EventStream, check, run, run_sync, stream
from .driver import CliDriver, Facts, Launch, Parser, RequestError
from .events import (
    SCHEMA_VERSION,
    Event,
    FileChanged,
    JsonValue,
    MessageCompleted,
    MessageStarted,
    Notice,
    RawEvent,
    ReasoningDelta,
    RunCompleted,
    SessionIdentified,
    TextDelta,
    ToolCompleted,
    ToolStarted,
    UsageUpdated,
)
from .registry import DriverRegistry
from .types import Capabilities, Diagnostic, ErrorCode, Outcome, Request, Result, Usage

__version__ = "0.2.2"

__all__ = [
    "SCHEMA_VERSION",
    "Capabilities",
    "Diagnostic",
    "CliDriver",
    "DriverRegistry",
    "ErrorCode",
    "Event",
    "EventStream",
    "FileChanged",
    "Facts",
    "JsonValue",
    "MessageCompleted",
    "MessageStarted",
    "Launch",
    "Notice",
    "Outcome",
    "RawEvent",
    "ReasoningDelta",
    "Request",
    "Result",
    "RunCompleted",
    "SessionIdentified",
    "Parser",
    "RequestError",
    "TextDelta",
    "ToolCompleted",
    "ToolStarted",
    "Usage",
    "UsageUpdated",
    "check",
    "run",
    "run_sync",
    "stream",
]
