"""Run coding-agent CLIs, stream typed events, and return a classified result."""

from .api import EventStream, check, run, run_sync, stream
from .events import (
    SCHEMA_VERSION,
    Event,
    MessageCompleted,
    MessageStarted,
    RunCompleted,
    SessionIdentified,
    TextDelta,
    ToolCompleted,
    ToolStarted,
    UsageUpdated,
)
from .registry import Driver, DriverRegistry, EventHandler, SpawnHandler
from .types import Capabilities, Diagnostic, Outcome, Request, Result, Usage

__version__ = "0.2.0"

__all__ = [
    "SCHEMA_VERSION",
    "Capabilities",
    "Diagnostic",
    "Driver",
    "EventStream",
    "DriverRegistry",
    "Event",
    "EventHandler",
    "MessageCompleted",
    "MessageStarted",
    "Outcome",
    "Request",
    "Result",
    "RunCompleted",
    "SessionIdentified",
    "SpawnHandler",
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
