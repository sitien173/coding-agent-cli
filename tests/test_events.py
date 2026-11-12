from __future__ import annotations

import dataclasses

import pytest

from coding_agent_cli import (
    MessageCompleted,
    MessageStarted,
    Outcome,
    Result,
    RunCompleted,
    SessionIdentified,
    TextDelta,
    ToolCompleted,
    ToolStarted,
    Usage,
    UsageUpdated,
)

EVENTS = [
    (SessionIdentified(session_id="s"), "session.identified"),
    (MessageStarted(), "assistant.message.started"),
    (TextDelta(text="t"), "assistant.text.delta"),
    (MessageCompleted(), "assistant.message.completed"),
    (ToolStarted(tool="Bash", activity="command", input={"c": 1}), "tool.started"),
    (ToolCompleted(status="completed"), "tool.completed"),
    (UsageUpdated(usage=Usage(input=1)), "usage.updated"),
    (RunCompleted(result=Result(outcome=Outcome.SUCCESS)), "run.completed"),
]


@pytest.mark.parametrize(("event", "kind"), EVENTS)
def test_event_kind_and_defaults(event, kind: str) -> None:
    assert event.kind == kind
    assert event.schema_version == 1
    assert event.sequence == 0
    assert event.entity_id is None


@pytest.mark.parametrize(("event", "kind"), EVENTS)
def test_events_are_frozen(event, kind: str) -> None:
    with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
        event.sequence = 5


def test_kind_is_not_a_field() -> None:
    assert "kind" not in [f.name for f in dataclasses.fields(TextDelta)]


def test_replace_assigns_sequence() -> None:
    event = dataclasses.replace(TextDelta(text="a", entity_id="msg-1"), sequence=3)
    assert (event.sequence, event.text, event.entity_id) == (3, "a", "msg-1")


def test_pattern_matching() -> None:
    def describe(event) -> str:
        match event:
            case TextDelta(text=text):
                return f"text:{text}"
            case ToolStarted(tool=tool):
                return f"tool:{tool}"
            case _:
                return "other"

    assert describe(TextDelta(text="hi")) == "text:hi"
    assert describe(ToolStarted(tool="Read", activity="tool_call")) == "tool:Read"
    assert describe(MessageStarted()) == "other"
