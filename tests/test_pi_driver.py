"""Pi driver tests: recorded stream parsing, usage de-duplication, argv policy, and transport.

The parsing section feeds recorded JSON-mode lines through ``PiRun``. The transport section runs the
real driver against the fake ``pi`` launcher.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from coding_agent_cli import (
    Diagnostic,
    DriverRegistry,
    Outcome,
    Request,
    Result,
    SessionIdentified,
    TextDelta,
    ToolCompleted,
    ToolStarted,
    Usage,
    UsageUpdated,
)
from coding_agent_cli._drivers.pi import PiRun, _event_model
from coding_agent_cli._process import ProcessOutcome
from coding_agent_cli.events import Event

from .cli_stub import run_driver, stub_cli
from .test_process import wait_gone

USAGE_FIXTURE = Path(__file__).parent / "fixtures" / "usage" / "pi.jsonl"
SESSION_FIXTURE = "00000000-0000-0000-0000-000000000000"
FORBIDDEN_FLAGS = (
    "--dangerously-skip-permissions",
    "--permission-mode",
    "bypassPermissions",
    "--yolo",
    "--approve",
    "--no-approve",
)


def _event(**fields: object) -> str:
    return json.dumps(fields)


def _assistant_message(text: str, **fields: object) -> str:
    return _event(type="message_end", message={"role": "assistant", "content": [{"type": "text", "text": text}], **fields})


def _assistant(**fields: object) -> dict[str, Any]:
    return {"role": "assistant", **fields}


def _end(message: object) -> str:
    return _event(type="message_end", message=message)


def _feed(*lines: str) -> tuple[PiRun, list[Event]]:
    """Feed recorded lines through the parser without a process."""
    events: list[Event] = []

    async def sink(event: Event) -> None:
        events.append(event)

    async def go() -> PiRun:
        run = PiRun(sink)
        for line in lines:
            await run.feed(line)
        return run

    return asyncio.run(go()), events


def _parse(*lines: str, process: ProcessOutcome | None = None) -> tuple[PiRun, Result, list[Event]]:
    run, events = _feed(*lines)
    return run, run.finish(process or ProcessOutcome(), asyncio.Event(), None), events


# --- Recorded fixture and outcome classification ---


def test_recorded_stream_pins_text_session_model_and_usage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stub_cli(monkeypatch, tmp_path, "pi", USAGE_FIXTURE.read_text(encoding="utf-8").splitlines())

    result = asyncio.run(run_driver("pi", tmp_path, session_id="resume-id"))

    assert result.outcome is Outcome.SUCCESS
    assert result.error is None
    assert result.text == "OK"
    assert result.session_id == SESSION_FIXTURE
    assert result.model == "gpt-6.1-sol"
    assert result.usage == Usage(
        context_used=12627,
        context_window=None,
        input=24638,
        output=47,
        cache_read=11904,
        cache_write=0,
    )


def test_session_event_supplies_session_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stub_cli(monkeypatch, tmp_path, "pi", [
        _event(type="session", id="pi-sess-1"),
        _assistant_message("hello"),
    ])
    events: list[Event] = []

    result = asyncio.run(run_driver("pi", tmp_path, events=events))

    assert result.outcome is Outcome.SUCCESS
    assert result.session_id == "pi-sess-1"
    assert result.text == "hello"
    assert SessionIdentified(session_id="pi-sess-1") in events


def test_structured_error_with_partial_text_is_retryable_service_error_on_rate_limit() -> None:
    _run, result, _events = _parse(
        _assistant_message("partial"),
        _end({"role": "assistant", "content": [], "stopReason": "error", "errorMessage": "rate limited"}),
    )

    assert result.outcome is Outcome.RETRYABLE
    assert result.error is not None and result.error.code == "service_error"
    assert result.text == "partial"


def test_structured_error_with_partial_text_cannot_hide_the_failure() -> None:
    _run, result, _events = _parse(
        _assistant_message("partial"),
        _end({"role": "assistant", "content": [], "stopReason": "error", "errorMessage": "boom"}),
    )

    assert result.outcome is Outcome.RETRYABLE
    assert result.error == Diagnostic(code="execution_error", message="boom")
    assert result.text == "partial"


def test_command_failure_with_text_is_retryable_execution_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stub_cli(monkeypatch, tmp_path, "pi", [_assistant_message("reply")], exit_code=3)

    result = asyncio.run(run_driver("pi", tmp_path, session_id="s"))

    assert result.outcome is Outcome.RETRYABLE
    assert result.error == Diagnostic(code="execution_error", message="command exited with status 3")
    assert result.text == "reply"


def test_fatal_token_in_diagnostic_line_is_agent_fatal_auth() -> None:
    _run, result, _events = _parse("Error: not logged in")

    assert result.outcome is Outcome.AGENT_FATAL
    assert result.error is not None and result.error.code == "auth"


def test_context_overflow_signature_is_request_fatal_context_overflow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stub_cli(monkeypatch, tmp_path, "pi", [_assistant_message("partial"), "context_length_exceeded"], exit_code=1)

    result = asyncio.run(run_driver("pi", tmp_path))

    assert result.outcome is Outcome.REQUEST_FATAL
    assert result.error is not None and result.error.code == "context_overflow"
    assert result.text == "partial"


def test_no_output_is_retryable_no_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stub_cli(monkeypatch, tmp_path, "pi", [])

    result = asyncio.run(run_driver("pi", tmp_path))

    assert result.outcome is Outcome.RETRYABLE
    assert result.error is not None and result.error.code == "no_output"


def test_timeout_without_reply_is_retryable_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pid_file = tmp_path / "pid"
    stub_cli(monkeypatch, tmp_path, "pi", ["Pi diagnostic: startup"], sleep_s=30, pid_file=pid_file)

    result = asyncio.run(run_driver("pi", tmp_path, timeout_s=1))

    assert result.outcome is Outcome.RETRYABLE
    assert result.error is not None and result.error.code == "timeout"
    assert wait_gone(int(pid_file.read_text(encoding="utf-8")))


@pytest.mark.asyncio
async def test_cancel_before_start_is_cancelled_and_spawns_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    record = tmp_path / "record.json"
    stub_cli(monkeypatch, tmp_path, "pi", [_assistant_message("never")], record=record)
    cancel = asyncio.Event()
    cancel.set()

    result = await run_driver("pi", tmp_path, cancel=cancel)

    assert result.outcome is Outcome.CANCELLED
    assert result.error is not None and result.error.code == "cancelled"
    assert not record.exists()


# --- Transport: argv, stdin, cwd ---


@pytest.mark.asyncio
async def test_resume_argv_and_prompt_on_stdin_are_pinned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    record = tmp_path / "record.json"
    stub_cli(monkeypatch, tmp_path, "pi", [_assistant_message("ok")], record=record)
    prompt = "line one\nline two with --flag-looking text"

    await run_driver("pi", tmp_path, prompt=prompt, session_id="resume-id")

    seen = json.loads(record.read_text(encoding="utf-8"))
    assert seen["argv"] == ["pi", "--mode", "json", "--session", "resume-id"]
    assert prompt not in seen["argv"]
    assert seen["stdin"] == prompt
    assert Path(seen["cwd"]).resolve() == tmp_path.resolve()


@pytest.mark.asyncio
async def test_portable_options_map_to_pi_flags_after_transport_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = tmp_path / "record.json"
    stub_cli(monkeypatch, tmp_path, "pi", [_assistant_message("ok")], record=record)

    await run_driver("pi", tmp_path, model="m", reasoning="high", system_prompt="sp", args=("--x",))

    assert json.loads(record.read_text(encoding="utf-8"))["argv"] == [
        "pi",
        "--mode", "json",
        "--system-prompt", "sp",
        "--model", "m",
        "--thinking", "high",
        "--x",
    ]


@pytest.mark.parametrize("model", [None, "m"])
@pytest.mark.parametrize("reasoning", [None, "high"])
@pytest.mark.parametrize("system_prompt", [None, "sp"])
@pytest.mark.parametrize("session_id", [None, "resume-id"])
@pytest.mark.parametrize("args", [(), ("--tools", "read")])
@pytest.mark.asyncio
async def test_no_approval_or_permission_flag_is_ever_added(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    model: str | None,
    reasoning: str | None,
    system_prompt: str | None,
    session_id: str | None,
    args: tuple[str, ...],
) -> None:
    record = tmp_path / "record.json"
    stub_cli(monkeypatch, tmp_path, "pi", [_assistant_message("ok")], record=record)

    await run_driver(
        "pi",
        tmp_path,
        model=model,
        reasoning=reasoning,
        system_prompt=system_prompt,
        session_id=session_id,
        args=args,
    )

    argv = json.loads(record.read_text(encoding="utf-8"))["argv"]
    assert argv[1:3] == ["--mode", "json"]
    for flag in FORBIDDEN_FLAGS:
        assert not any(token == flag or token.startswith(f"{flag}=") for token in argv), flag


@pytest.mark.parametrize(
    "args",
    [
        pytest.param(("--mode", "text"), id="mode"),
        pytest.param(("--mode=text",), id="mode-equals"),
        pytest.param(("--session", "other"), id="session"),
        pytest.param(("--session=other",), id="session-equals"),
    ],
)
@pytest.mark.asyncio
async def test_raw_args_cannot_override_a_driver_owned_transport_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, args: tuple[str, ...]
) -> None:
    record = tmp_path / "record.json"
    stub_cli(monkeypatch, tmp_path, "pi", [_assistant_message("ok")], record=record)

    result = await run_driver("pi", tmp_path, args=args)

    assert result.outcome is Outcome.REQUEST_FATAL
    assert result.error is not None and result.error.code == "invalid_args"
    assert not record.exists()


@pytest.mark.parametrize(
    ("option", "args"),
    [
        pytest.param({"model": "m"}, ("--model", "other"), id="model"),
        pytest.param({"model": "m"}, ("--model=other",), id="model-equals"),
        pytest.param({"reasoning": "high"}, ("--thinking", "low"), id="reasoning"),
        pytest.param({"system_prompt": "sp"}, ("--system-prompt", "other"), id="system-prompt"),
    ],
)
@pytest.mark.asyncio
async def test_raw_args_cannot_conflict_with_a_portable_option(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, option: dict[str, Any], args: tuple[str, ...]
) -> None:
    record = tmp_path / "record.json"
    stub_cli(monkeypatch, tmp_path, "pi", [_assistant_message("ok")], record=record)

    result = await run_driver("pi", tmp_path, args=args, **option)

    assert result.outcome is Outcome.REQUEST_FATAL
    assert result.error is not None and result.error.code == "invalid_args"
    assert not record.exists()


@pytest.mark.asyncio
async def test_bad_cwd_is_rejected_before_spawn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    record = tmp_path / "record.json"
    stub_cli(monkeypatch, tmp_path, "pi", [_assistant_message("ok")], record=record)

    result = await DriverRegistry().get("pi").run(
        Request(driver="pi", prompt="p", cwd=tmp_path / "missing"),
        on_event=_discard,
        cancel=asyncio.Event(),
        on_spawn=None,
    )

    assert result.outcome is Outcome.REQUEST_FATAL
    assert result.error is not None and result.error.code == "bad_cwd"
    assert not record.exists()


async def _discard(event: Event) -> None:
    return None


@pytest.mark.asyncio
async def test_extension_style_args_pass_through_without_an_approval_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = tmp_path / "record.json"
    stub_cli(monkeypatch, tmp_path, "pi", [_assistant_message("ok")], record=record)

    await run_driver("pi", tmp_path, args=("--skill=x",))

    assert json.loads(record.read_text(encoding="utf-8"))["argv"] == ["pi", "--mode", "json", "--skill=x"]


@pytest.mark.parametrize("args", [pytest.param(("--",), id="dashdash"), pytest.param(("a\x00",), id="nul")])
@pytest.mark.asyncio
async def test_invalid_raw_args_are_rejected_before_spawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, args: tuple[str, ...]
) -> None:
    record = tmp_path / "record.json"
    stub_cli(monkeypatch, tmp_path, "pi", [_assistant_message("ok")], record=record)

    result = await run_driver("pi", tmp_path, args=args)

    assert result.outcome is Outcome.REQUEST_FATAL
    assert result.error is not None and result.error.code == "invalid_args"
    assert not record.exists()


@pytest.mark.asyncio
async def test_env_values_never_reach_argv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    record = tmp_path / "record.json"
    stub_cli(monkeypatch, tmp_path, "pi", [_assistant_message("ok")], record=record)

    await run_driver("pi", tmp_path, model="m", env={"SECRET_TOKEN": "hunter2"})

    argv = json.loads(record.read_text(encoding="utf-8"))["argv"]
    assert "hunter2" not in argv
    assert "SECRET_TOKEN" not in argv


# --- Stream normalization ---


def test_text_and_tools_normalize_and_thinking_deltas_are_excluded() -> None:
    _run, result, events = _parse(
        _event(type="session", id="pi-sess-abc-123"),
        _event(type="message_update", assistantMessageEvent={"type": "thinking_delta", "delta": "secret_thinking"}),
        _event(type="message_update", assistantMessageEvent={"type": "text_delta", "delta": "Checking files."}),
        _event(type="tool_execution_start", toolCallId="pi_tool_1", toolName="grep", args={"pattern": "p"}),
        _event(type="tool_execution_end", toolCallId="pi_tool_1", isError=False, result="matched"),
        _event(type="message_end", message={"role": "assistant", "content": "Final Pi Answer"}),
    )

    assert result.outcome is Outcome.SUCCESS
    assert result.session_id == "pi-sess-abc-123"
    assert result.text == "Final Pi Answer"
    assert events == [
        SessionIdentified(session_id="pi-sess-abc-123"),
        TextDelta(text="Checking files.", entity_id="msg-1"),
        ToolStarted(tool="grep", activity="tool_call", input={"pattern": "p"}, entity_id="tool-1"),
        ToolCompleted(status="completed", entity_id="tool-1"),
    ]
    assert "secret_thinking" not in json.dumps([str(event) for event in events])


def test_nested_camel_and_snake_ids_pair_tool_start_and_completion() -> None:
    nested_args = {"nested": [1, False, None, {"sub": "data"}], "zero": 0}
    _run, result, events = _parse(
        _event(type="session", id="pi-nested-sess"),
        _event(type="tool_execution_start", toolCallId="call_1", toolName="nested_pi_tool", args=nested_args),
        _event(type="tool_execution_end", toolCallId="call_1", isError=True, result={"count": 1}),
        _event(type="tool_call", tool_call_id="call_2", name="bare_pi_tool"),
        _event(type="tool_result", tool_call_id="call_2", status="completed"),
        _event(type="tool_execution_start", toolCallId="call_3", toolName="null_pi_tool", args=None),
        _event(type="tool_execution_end", toolCallId="call_3", isError=False, result=None),
        _event(type="message_end", message={"role": "assistant", "content": "Done"}),
    )

    assert result.outcome is Outcome.SUCCESS
    assert events == [
        SessionIdentified(session_id="pi-nested-sess"),
        ToolStarted(tool="nested_pi_tool", activity="tool_call", input=nested_args, entity_id="tool-1"),
        ToolCompleted(status="error", entity_id="tool-1"),
        ToolStarted(tool="bare_pi_tool", activity="tool_call", entity_id="tool-2"),
        ToolCompleted(status="completed", entity_id="tool-2"),
        ToolStarted(tool="null_pi_tool", activity="tool_call", input=None, entity_id="tool-3"),
        ToolCompleted(status="completed", entity_id="tool-3"),
    ]


def test_completion_for_an_unknown_tool_call_is_ignored() -> None:
    _run, _result, events = _parse(_event(type="tool_execution_end", toolCallId="never", isError=False))

    assert events == []


def test_text_entity_is_per_turn() -> None:
    _run, _result, events = _parse(
        _event(type="turn_start"),
        _event(type="message_update", assistantMessageEvent={"type": "text_delta", "delta": "a"}),
        _event(type="turn_start"),
        _event(type="message_update", assistantMessageEvent={"type": "text_delta", "delta": "b"}),
    )

    assert events == [TextDelta(text="a", entity_id="msg-1"), TextDelta(text="b", entity_id="msg-2")]


# --- Model identity ---


@pytest.mark.parametrize(
    ("event", "model"),
    [
        pytest.param({"message": _assistant(model="m1")}, "m1", id="assistant-message"),
        pytest.param({"message": {"role": "user", "model": "u"}}, None, id="non-assistant-message"),
        pytest.param({"message": _assistant(model="")}, None, id="empty-message-model"),
        pytest.param({"message": _assistant(model=3)}, None, id="non-string-message-model"),
        pytest.param({"message": "text"}, None, id="message-not-a-dict"),
        pytest.param(
            {"message": _assistant(model="first"), "messages": [_assistant(model="later")]},
            "first",
            id="message-wins-over-messages",
        ),
        pytest.param(
            {"messages": [_assistant(model="a"), _assistant(model="b")]},
            "b",
            id="last-assistant-in-messages",
        ),
        pytest.param(
            {"messages": [_assistant(model="a"), {"role": "user", "model": "u"}, "x", _assistant()]},
            "a",
            id="skips-non-assistant-non-dict-and-modelless-entries",
        ),
        pytest.param(
            {"message": _assistant(model=""), "messages": [_assistant(model="fallback")]},
            "fallback",
            id="empty-message-model-falls-back-to-messages",
        ),
        pytest.param({"messages": [_assistant(model="")]}, None, id="messages-with-empty-model"),
        pytest.param({"messages": "x"}, None, id="messages-not-a-list"),
        pytest.param({}, None, id="no-message-fields"),
    ],
)
def test_event_model_picks_the_assistant_model(event: dict[str, Any], model: str | None) -> None:
    assert _event_model(event) == model


# --- Per-response usage ---


def test_assistant_usage_records_the_response_and_context_once() -> None:
    run, events = _feed(_end(_assistant(responseId="r1", usage={"input": 10, "output": 5})))

    expected = Usage(input=10, output=5, context_used=15)
    assert run.counters.usage() == expected
    assert events == [UsageUpdated(usage=expected)]
    assert run.recorded_responses == {"r1"}


def test_repeated_response_id_is_ignored() -> None:
    run, events = _feed(
        _end(_assistant(responseId="r1", usage={"input": 10, "output": 5})),
        _end(_assistant(responseId="r1", usage={"input": 99, "output": 99})),
    )

    assert run.counters.usage() == Usage(input=10, output=5, context_used=15)
    assert len(events) == 1


def test_id_is_used_when_response_id_is_missing_and_distinct_ids_sum() -> None:
    run, events = _feed(
        _end(_assistant(id="a", usage={"input": 1, "output": 1})),
        _end(_assistant(id="b", usage={"input": 2, "output": 2})),
    )

    assert run.recorded_responses == {"a", "b"}
    assert run.counters.usage() == Usage(input=3, output=3, context_used=4)
    assert len(events) == 2


def test_message_without_an_id_uses_turn_one_and_a_second_one_in_the_turn_is_ignored() -> None:
    run, events = _feed(
        _end(_assistant(usage={"input": 1, "output": 1})),
        _end(_assistant(usage={"input": 50, "output": 50})),
    )

    assert run.recorded_responses == {"turn_1"}
    assert run.turn_counter == 1
    assert run.counters.usage() == Usage(input=1, output=1, context_used=2)
    assert len(events) == 1


def test_each_turn_start_allows_one_message_without_an_id() -> None:
    run, events = _feed(
        _event(type="turn_start"),
        _end(_assistant(usage={"input": 1, "output": 1})),
        _event(type="turn_start"),
        _end(_assistant(usage={"input": 2, "output": 2})),
    )

    assert run.recorded_responses == {"turn_1", "turn_2"}
    assert run.counters.usage() == Usage(input=3, output=3, context_used=4)
    assert len(events) == 2


def test_turn_id_already_recorded_by_a_response_id_is_ignored() -> None:
    run, events = _feed(
        _event(type="turn_start"),
        _end(_assistant(responseId="turn_1", usage={"input": 1, "output": 1})),
        _end(_assistant(usage={"input": 7, "output": 7})),
    )

    assert run.recorded_responses == {"turn_1"}
    assert run.counters.usage() == Usage(input=1, output=1, context_used=2)
    assert len(events) == 1


@pytest.mark.parametrize(
    "message",
    [
        pytest.param(_assistant(responseId="r1", usage="x"), id="usage-not-a-dict"),
        pytest.param(_assistant(responseId="r1"), id="no-usage"),
        pytest.param(_assistant(responseId="r1", usage={}), id="unobserved-usage"),
    ],
)
def test_message_without_usable_usage_is_marked_recorded_but_emits_nothing(message: object) -> None:
    run, events = _feed(_end(message), _end(_assistant(responseId="r1", usage={"input": 4})))

    assert run.recorded_responses == {"r1"}
    assert run.counters.usage() == Usage()
    assert events == []


@pytest.mark.parametrize(
    "message",
    [
        pytest.param({"role": "user", "responseId": "r1", "usage": {"input": 1}}, id="user-role"),
        pytest.param("text", id="not-a-dict"),
    ],
)
def test_non_assistant_message_is_not_recorded(message: object) -> None:
    run, events = _feed(_end(message))

    assert run.recorded_responses == set()
    assert run.turn_counter == 0
    assert events == []


def test_output_only_usage_records_no_context_used() -> None:
    run, events = _feed(_end(_assistant(responseId="r1", usage={"output": 5})))

    assert run.counters.usage() == Usage(output=5)
    assert events == [UsageUpdated(usage=Usage(output=5))]


def test_turn_end_events_record_usage_like_message_end() -> None:
    run, _events = _feed(_event(type="turn_end", message=_assistant(responseId="r9", usage={"input": 2, "output": 3})))

    assert run.counters.usage() == Usage(input=2, output=3, context_used=5)


# --- Lifecycle de-duplication across events ---


def test_repeated_message_end_and_turn_end_do_not_double_count() -> None:
    message = {
        "role": "assistant",
        "responseId": "resp-1",
        "content": [{"type": "text", "text": "Turn 1"}],
        "usage": {"input": 500, "output": 25, "cacheRead": 100, "cacheWrite": 0, "totalTokens": 625},
    }
    _run, result, _events = _parse(
        _event(type="message_update", usage={"input": 0, "output": 0}),
        _end(message),
        _event(type="turn_end", message=message),
    )

    assert result.outcome is Outcome.SUCCESS
    assert result.usage.input == 600
    assert result.usage.output == 25
    assert result.usage.cache_read == 100


def test_lifecycle_deduplication_without_provider_ids() -> None:
    message = {"role": "assistant", "content": [{"type": "text", "text": "turn 1 message"}], "usage": {"input": 150, "output": 25}}
    _run, result, _events = _parse(
        _event(type="session", id="pi-sess-noid"),
        _end(message),
        _event(type="turn_end", message=message),
    )

    assert result.outcome is Outcome.SUCCESS
    assert result.usage.input == 150
    assert result.usage.output == 25


def test_repeated_message_end_within_one_turn_is_counted_once() -> None:
    message = {"role": "assistant", "content": [{"type": "text", "text": "Turn 1 answer"}], "usage": {"input": 100, "output": 20}}
    _run, result, _events = _parse(
        _event(type="session", id="pi-sess-no-id-turns"),
        _event(type="turn_start"),
        _end(message),
        _end(message),
        _event(type="turn_end", message=message),
    )

    assert result.usage.input == 100
    assert result.usage.output == 20


def test_repeated_no_id_turn_end_does_not_open_an_implicit_turn() -> None:
    message = {"role": "assistant", "content": [{"type": "text", "text": "Turn 1 message"}], "usage": {"input": 100, "output": 20}}
    _run, result, _events = _parse(
        _event(type="session", id="pi-sess-repro"),
        _event(type="turn_start"),
        _end(message),
        _event(type="turn_end", message=message),
        _event(type="turn_end", message=message),
    )

    assert result.usage.input == 100
    assert result.usage.output == 20


def test_total_tokens_is_never_context_occupancy_without_retained_input() -> None:
    with_input = {"role": "assistant", "responseId": "r1", "usage": {"input": 500, "output": 25, "cacheRead": 100, "cacheWrite": 0, "totalTokens": 625}}
    without_input = {"role": "assistant", "responseId": "r2", "usage": {"output": 25, "totalTokens": 625}}

    _run, result, _events = _parse(_end(with_input))
    assert result.usage.context_used == 625

    _run, result, _events = _parse(_end(without_input))
    assert result.usage.context_used is None


def test_capacity_stays_unknown_when_the_stream_reports_no_window() -> None:
    _run, result, _events = _parse(
        _event(type="session", id="pi-sess-111"),
        _event(type="turn_start"),
        _end({"role": "assistant", "id": "msg_1", "content": "Hello", "usage": {"input": 400, "output": 50, "cacheRead": 100}}),
        _event(type="turn_end"),
    )

    assert result.usage.context_window is None
    assert result.usage.context_used == 550


def test_recorded_fixture_totals_exceed_the_final_message_alone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stub_cli(monkeypatch, tmp_path, "pi", USAGE_FIXTURE.read_text(encoding="utf-8").splitlines())
    events: list[Event] = []

    result = asyncio.run(run_driver("pi", tmp_path, events=events))

    assert result.usage.input == 24638
    assert result.usage.output == 47
    assert result.usage.cache_read == 11904
    assert any(isinstance(event, UsageUpdated) for event in events)
    assert result.model == "gpt-6.1-sol"

