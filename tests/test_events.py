from __future__ import annotations

import dataclasses

import pytest

import coding_agent_cli.events as events_mod
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


def _events() -> list[tuple[object, str]]:
    reasoning_cls = getattr(events_mod, "ReasoningDelta", None)
    file_changed_cls = getattr(events_mod, "FileChanged", None)
    notice_cls = getattr(events_mod, "Notice", None)
    raw_cls = getattr(events_mod, "RawEvent", None)
    assert reasoning_cls is not None
    assert file_changed_cls is not None
    assert notice_cls is not None
    assert raw_cls is not None
    return [
        (SessionIdentified(session_id="s"), "session.identified"),
        (MessageStarted(), "assistant.message.started"),
        (TextDelta(text="t"), "assistant.text.delta"),
        (reasoning_cls(text="r"), "assistant.reasoning.delta"),
        (MessageCompleted(), "assistant.message.completed"),
        (ToolStarted(tool="Bash", activity="command", input={"c": 1}), "tool.started"),
        (ToolCompleted(tool="Bash", status="completed", output={"ok": True}), "tool.completed"),  # type: ignore[call-arg]
        (file_changed_cls(path="a.py", change="modified", evidence="reported"), "file.changed"),
        (notice_cls(code="retry", message="retrying"), "notice"),
        (UsageUpdated(usage=Usage(input=1)), "usage.updated"),
        (raw_cls(stream="stdout", data={"type": "x"}), "raw"),
        (RunCompleted(result=Result(outcome=Outcome.SUCCESS)), "run.completed"),
    ]


def test_schema_version_is_2() -> None:
    assert events_mod.SCHEMA_VERSION == 2


def test_events_have_no_schema_version_field() -> None:
    for event, kind in _events():
        assert event.kind == kind  # type: ignore[attr-defined]
        assert not hasattr(event, "schema_version")
        assert event.sequence == 0  # type: ignore[attr-defined]
        assert event.entity_id is None  # type: ignore[attr-defined]


def test_events_are_frozen() -> None:
    for event, _ in _events():
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            event.sequence = 5  # type: ignore[attr-defined]


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
