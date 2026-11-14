"""Unit tests for PiDriver and its parser."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from coding_agent_cli._classify import RunFacts, classify
from coding_agent_cli._drivers.pi import PiDriver
from coding_agent_cli.driver import RequestError
from coding_agent_cli.events import (
    MessageCompleted,
    MessageStarted,
    ReasoningDelta,
    SessionIdentified,
    TextDelta,
    ToolCompleted,
    ToolStarted,
    UsageUpdated,
)
from coding_agent_cli.types import ErrorCode, Outcome, Request
from tests.replay import FIXTURES, replay, replay_truncated

_FORBIDDEN_APPROVAL_FLAGS = ("--approve", "--no-approve", "--dangerously-skip-permissions", "--yolo")


def _req(tmp_path: Path, **overrides: object) -> Request:
    kwargs: dict[str, object] = {
        "driver": "pi",
        "prompt": "hello pi",
        "cwd": tmp_path,
    }
    kwargs.update(overrides)
    return Request(**kwargs)  # type: ignore[arg-type]


def test_launch_basic_argv_and_stdin_without_p(tmp_path: Path) -> None:
    driver = PiDriver()
    launch = driver.launch(_req(tmp_path), Path("/usr/bin/pi"), tmp_path)

    assert launch.argv == ("/usr/bin/pi", "--mode", "json")
    assert "-p" not in launch.argv
    assert "--print" not in launch.argv
    assert launch.stdin == b"hello pi"
    assert "hello pi" not in launch.argv


def test_launch_portable_flags_and_session_id(tmp_path: Path) -> None:
    driver = PiDriver()
    req = _req(
        tmp_path,
        session_id="sess-123",
        model="openai-codex/gpt-5.6-luna",
        reasoning="low",
        system_prompt="Be concise.",
        args=("--no-extensions",),
    )
    launch = driver.launch(req, Path("/usr/bin/pi"), tmp_path)

    assert launch.argv == (
        "/usr/bin/pi",
        "--mode",
        "json",
        "--session",
        "sess-123",
        "--model",
        "openai-codex/gpt-5.6-luna",
        "--thinking",
        "low",
        "--system-prompt",
        "Be concise.",
        "--no-extensions",
    )


@pytest.mark.parametrize("flag", ["--mode", "--mode=json", "--session", "-p", "--print"])
def test_launch_rejects_owned_transport_flags(tmp_path: Path, flag: str) -> None:
    driver = PiDriver()
    with pytest.raises(RequestError) as exc_info:
        driver.launch(_req(tmp_path, args=(flag,)), Path("/usr/bin/pi"), tmp_path)
    assert exc_info.value.code == ErrorCode.INVALID_ARGS


@pytest.mark.parametrize(
    ("field", "flag"),
    [
        ("model", "--model"),
        ("model", "--model=foo"),
        ("reasoning", "--thinking"),
        ("system_prompt", "--system-prompt"),
    ],
)
def test_launch_rejects_conflicting_portable_flags(tmp_path: Path, field: str, flag: str) -> None:
    driver = PiDriver()
    req = _req(tmp_path, **{field: "value", "args": (flag,)})
    with pytest.raises(RequestError) as exc_info:
        driver.launch(req, Path("/usr/bin/pi"), tmp_path)
    assert exc_info.value.code == ErrorCode.INVALID_ARGS


def test_launch_never_adds_approval_flags(tmp_path: Path) -> None:
    driver = PiDriver()
    for kwargs in (
        {},
        {"session_id": "s1", "model": "m", "reasoning": "high", "system_prompt": "sys"},
    ):
        launch = driver.launch(_req(tmp_path, **kwargs), Path("/usr/bin/pi"), tmp_path)
        for forbidden in _FORBIDDEN_APPROVAL_FLAGS:
            assert forbidden not in launch.argv


def test_parser_message_lifecycle_and_entity_id_none(tmp_path: Path) -> None:
    parser = PiDriver().parser(_req(tmp_path))
    events = []
    for obj in (
        {"type": "session", "id": "sess-1"},
        {"type": "message_start", "message": {"role": "system", "content": ""}},
        {"type": "message_end", "message": {"role": "system", "content": ""}},
        {"type": "message_start", "message": {"role": "user", "content": [{"type": "text", "text": "hi"}]}},
        {"type": "message_end", "message": {"role": "user", "content": [{"type": "text", "text": "hi"}]}},
        {"type": "message_start", "message": {"role": "assistant", "model": "gpt-5.6-luna", "stopReason": "pending"}},
        {"type": "message_update", "assistantMessageEvent": {"type": "thinking_delta", "delta": "hmm"}},
        {"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": "PO"}},
        {"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": "NG"}},
        {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "model": "gpt-5.6-luna",
                "stopReason": "stop",
                "usage": {
                    "input": 100,
                    "output": 5,
                    "cacheRead": 20,
                    "cacheWrite": 0,
                    "totalTokens": 125,
                    "cost": {"total": 0.00025},
                },
            },
        },
        {"type": "turn_end"},
    ):
        events.extend(parser.feed_json(obj))

    facts = parser.finish({})
    assert any(isinstance(e, SessionIdentified) and e.session_id == "sess-1" for e in events)
    msg_events = [e for e in events if isinstance(e, (MessageStarted, ReasoningDelta, TextDelta, MessageCompleted))]
    assert [type(e) for e in msg_events] == [
        MessageStarted,
        ReasoningDelta,
        TextDelta,
        TextDelta,
        MessageCompleted,
    ]
    assert all(e.entity_id is None for e in msg_events)
    assert any(isinstance(e, UsageUpdated) for e in events)
    assert facts.session_id == "sess-1"
    assert facts.model == "gpt-5.6-luna"
    assert facts.stop_reason == "stop"
    assert facts.num_turns == 1
    assert facts.cost_usd == pytest.approx(0.00025)
    assert facts.usage.input == 120
    assert facts.usage.output == 5
    assert facts.usage.cache_read == 20
    assert facts.usage.cache_write == 0
    assert facts.usage.context_used == 125


def test_parser_tool_execution_start_and_end_with_output(tmp_path: Path) -> None:
    parser = PiDriver().parser(_req(tmp_path))
    events = []
    for obj in (
        {"type": "tool_execution_start", "toolCallId": "call-1", "toolName": "bash", "args": {"command": "ls"}},
        {
            "type": "tool_execution_end",
            "toolCallId": "call-1",
            "toolName": "bash",
            "result": {"content": [{"type": "text", "text": "a.py\n"}]},
            "isError": False,
        },
        {"type": "tool_execution_start", "toolCallId": "call-2", "toolName": "edit", "args": {"path": "a.py"}},
        {
            "type": "tool_execution_end",
            "toolCallId": "call-2",
            "toolName": "edit",
            "result": {"content": [{"type": "text", "text": "not found"}]},
            "isError": True,
        },
    ):
        events.extend(parser.feed_json(obj))

    assert events == [
        ToolStarted(tool="bash", activity="command", input={"command": "ls"}, entity_id="call-1"),
        ToolCompleted(
            tool="bash",
            status="completed",
            output={"content": [{"type": "text", "text": "a.py\n"}]},
            entity_id="call-1",
        ),
        ToolStarted(tool="edit", activity="tool_call", input={"path": "a.py"}, entity_id="call-2"),
        ToolCompleted(
            tool="edit",
            status="error",
            output={"content": [{"type": "text", "text": "not found"}]},
            entity_id="call-2",
        ),
    ]


def test_parser_cost_sum_and_turn_count_across_turns(tmp_path: Path) -> None:
    parser = PiDriver().parser(_req(tmp_path))
    for cost, inp, cache, out in ((0.0002, 100, 0, 10), (0.0003, 50, 110, 8)):
        for obj in (
            {"type": "message_start", "message": {"role": "assistant", "model": "gpt-5.6-luna"}},
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "model": "gpt-5.6-luna",
                    "stopReason": "stop",
                    "usage": {
                        "input": inp,
                        "output": out,
                        "cacheRead": cache,
                        "cacheWrite": 0,
                        "cost": {"total": cost},
                    },
                },
            },
            {"type": "turn_end"},
        ):
            list(parser.feed_json(obj))

    facts = parser.finish({})
    assert facts.num_turns == 2
    assert facts.cost_usd == pytest.approx(0.0005)
    assert facts.usage.input == 260
    assert facts.usage.output == 18
    assert facts.usage.cache_read == 110
    assert facts.usage.context_used == 168


def test_parser_error_followed_by_successful_message_yields_success(tmp_path: Path) -> None:
    parser = PiDriver().parser(_req(tmp_path))
    for obj in (
        {"type": "session", "id": "sess-retry"},
        {"type": "message_start", "message": {"role": "assistant", "model": "gpt-5.6-luna"}},
        {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "stopReason": "error",
                "errorMessage": "rate limit exceeded",
            },
        },
        {"type": "turn_end"},
        {"type": "message_start", "message": {"role": "assistant", "model": "gpt-5.6-luna"}},
        {"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": "OK"}},
        {"type": "message_end", "message": {"role": "assistant", "stopReason": "stop"}},
        {"type": "turn_end"},
    ):
        list(parser.feed_json(obj))

    facts = parser.finish({})
    assert facts.error is None
    result = classify(RunFacts(returncode=0), facts, text="OK")
    assert result.outcome == Outcome.SUCCESS
    assert result.error is None
    assert result.text == "OK"


def test_parser_error_not_cleared_by_aborted_message_and_failed_final_message(tmp_path: Path) -> None:
    parser = PiDriver().parser(_req(tmp_path))
    for obj in (
        {"type": "session", "id": "sess-abort"},
        {"type": "message_start", "message": {"role": "assistant", "model": "gpt-5.6-luna"}},
        {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "stopReason": "error",
                "errorMessage": "rate limit exceeded",
            },
        },
        {"type": "turn_end"},
        {"type": "message_start", "message": {"role": "assistant", "model": "gpt-5.6-luna"}},
        {"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": "partial"}},
        {"type": "message_end", "message": {"role": "assistant", "stopReason": "aborted"}},
        {"type": "turn_end"},
    ):
        list(parser.feed_json(obj))

    facts = parser.finish({})
    assert facts.error is not None
    assert facts.error.code == ErrorCode.SERVICE_ERROR
    result = classify(RunFacts(returncode=0), facts, text="partial")
    assert result.outcome == Outcome.RETRYABLE
    assert result.error is not None
    assert result.error.code == ErrorCode.SERVICE_ERROR

    # A failed final message with stopReason="error" and no errorMessage still fails.
    parser2 = PiDriver().parser(_req(tmp_path))
    for obj in (
        {"type": "session", "id": "sess-fail"},
        {"type": "message_start", "message": {"role": "assistant", "model": "gpt-5.6-luna"}},
        {"type": "message_end", "message": {"role": "assistant", "stopReason": "error"}},
        {"type": "turn_end"},
    ):
        list(parser2.feed_json(obj))
    facts2 = parser2.finish({})
    assert facts2.error is not None
    assert facts2.error.code == ErrorCode.EXECUTION_ERROR
    result2 = classify(RunFacts(returncode=0), facts2, text="")
    assert result2.outcome == Outcome.RETRYABLE


def test_parser_ignores_unsubstantiated_error_and_id_aliases(tmp_path: Path) -> None:
    parser = PiDriver().parser(_req(tmp_path))
    events = []
    for obj in (
        {"type": "session", "id": "sess-aliases"},
        {"type": "message_start", "message": {"role": "assistant", "id": "unsubstantiated-id"}},
        {"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": "OK"}},
        {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "stopReason": "stop",
                "error": "unrelated error field",
            },
        },
        {"type": "turn_end"},
    ):
        events.extend(parser.feed_json(obj))

    msg_events = [e for e in events if isinstance(e, (MessageStarted, TextDelta, MessageCompleted))]
    assert all(e.entity_id is None for e in msg_events)
    facts = parser.finish({})
    assert facts.error is None
    result = classify(RunFacts(returncode=0), facts, text="OK")
    assert result.outcome == Outcome.SUCCESS

    # Substantiated responseId on message_start sets entity_id.
    parser2 = PiDriver().parser(_req(tmp_path))
    events2 = []
    for obj in (
        {"type": "message_start", "message": {"role": "assistant", "responseId": "resp-1"}},
        {"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": "hi"}},
        {"type": "message_end", "message": {"role": "assistant", "stopReason": "stop"}},
    ):
        events2.extend(parser2.feed_json(obj))
    msg_events2 = [e for e in events2 if isinstance(e, (MessageStarted, TextDelta, MessageCompleted))]
    assert [e.entity_id for e in msg_events2] == ["resp-1", "resp-1", "resp-1"]


def test_parser_cost_only_usage_without_token_counters(tmp_path: Path) -> None:
    parser = PiDriver().parser(_req(tmp_path))
    for obj in (
        {"type": "message_start", "message": {"role": "assistant", "model": "gpt-5.6-luna"}},
        {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "stopReason": "stop",
                "usage": {"cost": {"total": 0.25}},
            },
        },
        {"type": "turn_end"},
    ):
        list(parser.feed_json(obj))

    facts = parser.finish({})
    assert facts.cost_usd == pytest.approx(0.25)
    assert facts.usage.input is None
    assert facts.usage.output is None


def test_parser_structured_error_and_unstructured_stderr(tmp_path: Path) -> None:
    parser = PiDriver().parser(_req(tmp_path))
    list(
        parser.feed_json(
            {
                "type": "message_end",
                "message": {"role": "assistant", "stopReason": "error", "errorMessage": "HTTP 503 service unavailable"},
            }
        )
    )
    list(parser.feed_text("warning line", stream="stderr"))
    facts = parser.finish({})
    assert facts.error is not None
    assert facts.error.code == ErrorCode.SERVICE_ERROR
    assert "warning line" in facts.error_text


_PI_SCENARIOS = sorted((p.parent.name, p.stem.removesuffix(".meta")) for p in (FIXTURES / "pi").glob("*/*.meta.json"))


@pytest.mark.parametrize(("version", "scenario"), _PI_SCENARIOS)
def test_replay_recorded_pi_scenario(tmp_path: Path, version: str, scenario: str) -> None:
    meta = json.loads((FIXTURES / "pi" / version / f"{scenario}.meta.json").read_text(encoding="utf-8"))
    parser = PiDriver().parser(_req(tmp_path))
    events, result = replay(parser, "pi", version, scenario)

    assert result.outcome == Outcome(meta["outcome"])
    expected_code = ErrorCode(meta["code"]) if meta["code"] is not None else None
    actual_code = result.error.code if result.error is not None else None
    assert actual_code == expected_code
    if result.outcome == Outcome.SUCCESS:
        assert result.text.strip() != ""
        assert result.model is not None
        assert result.session_id is not None
        assert result.num_turns is not None and result.num_turns >= 1
    if scenario == "cancel":
        assert any(isinstance(e, TextDelta) for e in events)
        assert result.text != ""


@pytest.mark.parametrize(("version", "scenario"), _PI_SCENARIOS)
def test_replay_truncated_after_each_text_delta_keeps_partial_text(tmp_path: Path, version: str, scenario: str) -> None:
    full_events, _ = replay(PiDriver().parser(_req(tmp_path)), "pi", version, scenario)
    deltas = [e for e in full_events if isinstance(e, TextDelta)]
    if not deltas:
        return
    # Check partial text accumulation after each TextDelta in the last message.
    for count in range(1, len(deltas) + 1):
        events, result = replay_truncated(PiDriver().parser(_req(tmp_path)), "pi", version, scenario, count)
        expected_parts: list[str] = []
        for event in events:
            if isinstance(event, MessageStarted):
                expected_parts = []
            elif isinstance(event, TextDelta):
                expected_parts.append(event.text)
        assert result.text == "".join(expected_parts)
