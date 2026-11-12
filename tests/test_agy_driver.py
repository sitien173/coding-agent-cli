"""Agy driver tests: recorded stream parsing, step usage, prompt limits, argv policy, and transport.

The parsing section feeds recorded stream-json lines through ``AgyRun``. The transport section runs the real
driver against the fake ``agy`` launcher.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import pytest

from coding_agent_cli import (
    Diagnostic,
    DriverRegistry,
    Outcome,
    Result,
    ToolCompleted,
    ToolStarted,
    Usage,
    UsageUpdated,
)
from coding_agent_cli._drivers import agy as agy_module
from coding_agent_cli._drivers.agy import PROMPT_LIMIT_BYTES, AgyDriver, AgyRun, _prompt_error
from coding_agent_cli._process import ProcessOutcome
from coding_agent_cli.events import Event

from .cli_stub import run_driver, stub_cli

USAGE_FIXTURE = Path(__file__).parent / "fixtures" / "usage" / "agy.jsonl"
CONVERSATION = "b658ef34-d18c-4294-b329-0ae5dee0157b"
OTHER_CONVERSATION = "c658ef34-d18c-4294-b329-0ae5dee0157c"
FIXTURE_CONVERSATION = "00000000-0000-0000-0000-000000000000"
FORBIDDEN_FLAGS = ("--dangerously-skip-permissions", "--sandbox", "--yolo", "--permission-mode", "--approve")
OWNED_ARGS = [
    pytest.param(("--print", "x"), id="print"),
    pytest.param(("-p", "x"), id="p"),
    pytest.param(("--prompt=x",), id="prompt-equals"),
    pytest.param(("--conversation", "x"), id="conversation"),
    pytest.param(("--new-project",), id="new-project"),
    pytest.param(("--output-format", "json"), id="output-format"),
    pytest.param(("--log-file", "/x"), id="log-file"),
    pytest.param(("--continue",), id="continue"),
    pytest.param(("-c",), id="c"),
]


@pytest.fixture
def temp_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Route ``tempfile`` to a per-test directory so temporary-file cleanup is observable."""
    directory = tmp_path / "tmp"
    directory.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(directory))
    return directory


def _step(text: str, *, step_index: int = 1, usage: dict[str, Any] | None = None, state: str = "ACTIVE") -> str:
    update: dict[str, Any] = {
        "conversation_id": CONVERSATION,
        "step_index": step_index,
        "state": state,
        "step_type": "agent_response",
        "text_delta": text,
    }
    if usage is not None:
        update["usage"] = usage
    return json.dumps({"event": "step_update", "step_update": update})


def _result(response: str, **fields: object) -> str:
    return json.dumps(
        {
            "event": "result",
            "result": {"conversation_id": CONVERSATION, "status": "SUCCESS", "response": response, **fields},
        }
    )


def _done(usage: dict[str, Any], **fields: object) -> dict[str, Any]:
    return {"step_type": "agent_response", "state": "DONE", "usage": usage, **fields}


def _feed(*lines: str, requested_session: str | None = None) -> tuple[AgyRun, list[Event]]:
    """Feed lines through the parser without a process."""
    events: list[Event] = []

    async def sink(event: Event) -> None:
        events.append(event)

    async def go() -> AgyRun:
        run = AgyRun(sink, requested_session=requested_session)
        for line in lines:
            await run.feed(line)
        return run

    return asyncio.run(go()), events


def _feed_updates(*updates: dict[str, Any]) -> tuple[AgyRun, list[Event]]:
    lines = [json.dumps({"event": "step_update", "step_update": update}) for update in updates]
    return _feed(*lines)


def _parse(*lines: str, log_text: str = "", requested_session: str | None = None) -> tuple[Result, list[Event]]:
    run, events = _feed(*lines, requested_session=requested_session)
    return run.finish(ProcessOutcome(), asyncio.Event(), log_text), events


def _run(tmp_path: Path, **fields: Any) -> Result:
    return asyncio.run(run_driver("agy", tmp_path, **fields))


def _argv(record: Path) -> list[str]:
    return json.loads(record.read_text(encoding="utf-8"))["argv"]


# --- Recorded stream and outcome classification ---


def test_recorded_stream_pins_text_session_and_telemetry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stub_cli(monkeypatch, tmp_path, "agy", USAGE_FIXTURE.read_text(encoding="utf-8").splitlines())

    result = _run(tmp_path)

    assert result.outcome == Outcome.SUCCESS
    assert result.text == "Step 1\nStep 2"
    assert result.session_id == FIXTURE_CONVERSATION
    assert result.usage == Usage(input=14072, output=168, cache_read=1000, context_used=5010)
    assert result.usage.context_window is None


def test_plain_text_output_is_the_reply_and_stream_regex_supplies_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub_cli(monkeypatch, tmp_path, "agy", ["PONG", f"Created conversation {CONVERSATION}"])

    result = _run(tmp_path)

    assert result.outcome == Outcome.SUCCESS
    assert result.text == "PONG"
    assert result.session_id == CONVERSATION


def test_log_file_session_wins_over_stream_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stub_cli(
        monkeypatch,
        tmp_path,
        "agy",
        [f"Created conversation {OTHER_CONVERSATION}", _step("ok")],
        log_text=f"Created conversation {CONVERSATION}",
    )

    result = _run(tmp_path)

    assert result.session_id == CONVERSATION


def test_first_stream_regex_match_beats_a_later_conversation_id_field() -> None:
    result, _ = _parse(
        f"Created conversation {CONVERSATION}",
        json.dumps(
            {
                "event": "step_update",
                "step_update": {
                    "conversation_id": OTHER_CONVERSATION,
                    "step_type": "agent_response",
                    "text_delta": "x",
                },
            }
        ),
    )

    assert result.session_id == CONVERSATION


def test_model_is_taken_from_last_event_that_carries_one() -> None:
    result, _ = _parse(
        json.dumps({"event": "init", "model": "m1"}),
        json.dumps({"event": "step_update", "step_update": {"step_type": "agent_response", "text_delta": "x"}}),
        json.dumps({"event": "result", "result": {"response": "x", "model": "m2"}}),
    )

    assert result.model == "m2"


def test_nonzero_exit_keeps_partial_reply_as_execution_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stub_cli(monkeypatch, tmp_path, "agy", [_step("partial")], exit_code=2)

    result = _run(tmp_path)

    assert result.outcome == Outcome.RETRYABLE
    assert result.error is not None and result.error.code == "execution_error"
    assert result.text == "partial"
    assert result.returncode == 2


def test_exit_zero_plain_text_is_the_reply_even_with_an_auth_keyword(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub_cli(monkeypatch, tmp_path, "agy", ["not logged in"])

    result = _run(tmp_path)

    assert result.outcome == Outcome.SUCCESS
    assert result.text == "not logged in"


def test_no_output_is_retryable_no_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Rewritten: Refract returned no_agent_messages with Agy-specific wording. The shared no_output class now applies.
    stub_cli(monkeypatch, tmp_path, "agy", [])

    result = _run(tmp_path)

    assert result.outcome == Outcome.RETRYABLE
    assert result.error is not None and result.error.code == "no_output"


def test_timeout_is_retryable_timeout_and_keeps_partial_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stub_cli(monkeypatch, tmp_path, "agy", [_step("partial")], sleep_s=30)

    result = _run(tmp_path, timeout_s=1)

    assert result.outcome == Outcome.RETRYABLE
    assert result.error is not None and result.error.code == "timeout"
    assert result.text == "partial"


@pytest.mark.asyncio
async def test_cancel_before_start_is_cancelled_and_spawns_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = tmp_path / "record.json"
    stub_cli(monkeypatch, tmp_path, "agy", [_step("never")], record=record)
    cancel = asyncio.Event()
    cancel.set()

    result = await run_driver("agy", tmp_path, cancel=cancel)

    assert result.outcome == Outcome.CANCELLED
    assert not record.exists()


@pytest.mark.parametrize(
    ("status", "outcome", "code"),
    [
        pytest.param("SUCCESS", Outcome.SUCCESS, None, id="success"),
        pytest.param("ERROR", Outcome.RETRYABLE, "execution_error", id="error"),
    ],
)
def test_structured_result_status_drives_classification(status: str, outcome: Outcome, code: str | None) -> None:
    # New in Phase 5: the structured status is read. Only SUCCESS is verified in the Refract snapshot.
    result, _ = _parse(json.dumps({"event": "result", "result": {"status": status, "response": "answer"}}))

    assert result.outcome == outcome
    assert (result.error.code if result.error else None) == code


def test_non_utf8_output_does_not_truncate_later_lines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stub_cli(monkeypatch, tmp_path, "agy", ["after"], raw_hex=["6d6964ff64"])

    result = _run(tmp_path)

    assert result.outcome == Outcome.SUCCESS
    assert "after" in result.text


# --- Step usage and stream normalization ---


@pytest.mark.parametrize(
    ("usage", "fields", "expected"),
    [
        pytest.param(
            {"input_tokens": 10, "output_tokens": 5, "thinking_tokens": 2, "context_window_size": 100},
            {},
            Usage(input=10, output=5, context_used=13, context_window=100),
            id="input-output-thinking-and-window",
        ),
        pytest.param(
            {"input_tokens": 10, "output_tokens": 5},
            {},
            Usage(input=10, output=5, context_used=15),
            id="no-thinking-no-window",
        ),
        pytest.param(
            {"input_tokens": 1, "output_tokens": 1, "thinking_tokens": 9},
            {},
            Usage(input=1, output=1),
            id="negative-context-is-skipped",
        ),
        pytest.param(
            {"cache_read_tokens": 4, "cache_write_tokens": 3},
            {},
            Usage(cache_read=4, cache_write=3),
            id="cache-only-has-no-context-used",
        ),
        pytest.param(
            {"input_tokens": 2},
            {"model_context_window": 50},
            Usage(input=2, context_window=50),
            id="input-without-output-skips-context-used",
        ),
        pytest.param(
            {"input_tokens": 2, "output_tokens": 3},
            {"context_window_size": 64},
            Usage(input=2, output=3, context_used=5, context_window=64),
            id="window-falls-back-to-the-update",
        ),
        pytest.param(
            {"input_tokens": 2, "output_tokens": 3, "model_context_window": 10, "context_window_size": 20},
            {"context_window_size": 30},
            Usage(input=2, output=3, context_used=5, context_window=10),
            id="usage-model-window-wins",
        ),
    ],
)
def test_done_step_usage_updates_counters_and_emits_one_usage_event(
    usage: dict[str, Any], fields: dict[str, Any], expected: Usage
) -> None:
    run, events = _feed_updates(_done(usage, **fields))

    assert run.counters.usage() == expected
    assert [e.usage for e in events if isinstance(e, UsageUpdated)] == [expected]
    assert len(events) == 1


@pytest.mark.parametrize(
    "update",
    [
        pytest.param({"step_type": "agent_response", "state": "ACTIVE", "usage": {"input_tokens": 1}}, id="not-done"),
        pytest.param({"step_type": "agent_response", "state": "DONE", "usage": "x"}, id="usage-not-a-dict"),
        pytest.param({"step_type": "agent_response", "state": "DONE"}, id="no-usage"),
        pytest.param(
            _done({"input_tokens": -1, "output_tokens": True, "cache_read_tokens": "7"}), id="no-valid-counts"
        ),
    ],
)
def test_step_without_usable_usage_leaves_counters_and_events_alone(update: dict[str, Any]) -> None:
    run, events = _feed_updates(update)

    assert run.counters.usage() == Usage()
    assert events == []


def test_non_object_step_updates_are_ignored() -> None:
    run, events = _feed(
        "not a dict",
        json.dumps({"event": "step_update", "step_update": ["x"]}),
        json.dumps({"event": "step_update", "step_update": None}),
    )

    assert run.counters.usage() == Usage()
    assert run.conversation_id == ""
    assert events == []


def test_step_ids_use_step_index_when_present_else_a_running_counter() -> None:
    run, _ = _feed_updates(
        _done({"input_tokens": 1}, step_index=7),
        _done({"input_tokens": 2}, step_index=7),
        _done({"input_tokens": 4}),
        _done({"input_tokens": 8}),
    )

    # Same index overwrites (2), then counter ids step_3 (4) and step_4 (8).
    assert run.counters.usage().input == 14
    assert run.step_counter == 4


def test_agent_response_and_tool_steps_dispatch_before_usage() -> None:
    tool = {"step_type": "tool", "state": "ACTIVE", "tool_name": "run_command", "tool_info": {"parameters": {"a": 1}}}
    done_tool = {"step_type": "tool", "state": "DONE", "tool_info": {}, "usage": {"input_tokens": 3}}
    run, events = _feed_updates(
        {"step_type": "agent_response", "text_delta": "hi", "conversation_id": CONVERSATION},
        tool,
        done_tool,
    )

    assert run.conversation_id == CONVERSATION
    assert run.deltas == ["hi"]
    assert [e.kind for e in events] == [
        "session.identified",
        "assistant.text.delta",
        "tool.started",
        "tool.completed",
        "usage.updated",
    ]
    started, completed, usage = events[2], events[3], events[4]
    assert isinstance(started, ToolStarted) and started.tool == "run_command" and started.input == {"a": 1}
    assert isinstance(completed, ToolCompleted) and completed.entity_id == "tool-1"
    assert isinstance(usage, UsageUpdated) and usage.usage == Usage(input=3)


def test_capacity_remains_null_without_explicit_metadata() -> None:
    result, _ = _parse(
        _step("Working", usage={"input_tokens": 800, "output_tokens": 120}).replace("ACTIVE", "DONE"),
        _result("Done", conversation_id=CONVERSATION, usage={"input_tokens": 800, "output_tokens": 120}),
    )

    assert result.usage.context_used == 920
    assert result.usage.context_window is None


def test_explicit_context_metadata_is_recorded() -> None:
    result, _ = _parse(
        _step("Working", usage={"input_tokens": 800, "output_tokens": 120, "model_context_window": 200000}).replace(
            "ACTIVE", "DONE"
        ),
        _result("Done", usage={"input_tokens": 800, "output_tokens": 120, "model_context_window": 200000}),
    )

    assert result.usage.context_used == 920
    assert result.usage.context_window == 200000


def test_context_provenance_is_separate_from_capacity() -> None:
    result, _ = _parse(
        _step("Working", usage={"input_tokens": 800, "output_tokens": 120}).replace("ACTIVE", "DONE"),
        _result("Done", usage={"input_tokens": 800, "output_tokens": 120, "context_window_size": 4096}),
    )

    assert result.usage.context_used == 920
    assert result.usage.context_window == 4096


def test_final_totals_do_not_duplicate_prior_step_consumption(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stub_cli(monkeypatch, tmp_path, "agy", USAGE_FIXTURE.read_text(encoding="utf-8").splitlines())

    result = _run(tmp_path)

    assert result.usage.input == 14072
    assert result.usage.output == 168


def test_finals_result_totals_override_summed_steps_without_context_change() -> None:
    result, _ = _parse(
        _step("a", usage={"input_tokens": 10, "output_tokens": 2}).replace("ACTIVE", "DONE"),
        _result("a", usage={"input_tokens": 30, "output_tokens": 4}),
    )

    assert result.usage.input == 30
    assert result.usage.output == 4
    assert result.usage.context_used == 12


def test_unknown_event_kinds_are_ignored() -> None:
    result, events = _parse(json.dumps({"event": "something_else"}), _step("x"))

    assert result.text == "x"
    assert [e.kind for e in events] == ["session.identified", "assistant.text.delta"]


# --- Prompt limit and command line ---


def test_prompt_over_100_kib_fails_before_spawn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    record = tmp_path / "record.json"
    stub_cli(monkeypatch, tmp_path, "agy", ["never"], record=record)

    result = _run(tmp_path, prompt="a" * (PROMPT_LIMIT_BYTES + 1))

    assert result.outcome == Outcome.REQUEST_FATAL
    assert result.error == Diagnostic(
        code="invalid_prompt",
        message=(
            f"agy prompt is {PROMPT_LIMIT_BYTES + 1} bytes, "
            f"above the 100 KiB ({PROMPT_LIMIT_BYTES} bytes) limit for the prompt argument"
        ),
    )
    assert not record.exists()


def test_prompt_limit_counts_utf8_bytes_not_characters(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    record = tmp_path / "record.json"
    stub_cli(monkeypatch, tmp_path, "agy", ["never"], record=record)
    prompt = "€" * (PROMPT_LIMIT_BYTES // 3 + 1)  # 3 bytes per character, over the limit in bytes

    result = _run(tmp_path, prompt=prompt)

    assert result.error is not None and result.error.code == "invalid_prompt"
    assert not record.exists()


def test_prompt_at_exactly_the_limit_passes_the_size_check() -> None:
    assert _prompt_error("a" * PROMPT_LIMIT_BYTES) is None


@pytest.mark.skipif(os.name == "nt", reason="a 100 KiB argv prompt exceeds the Windows command-line limit")
def test_prompt_at_exactly_100_kib_is_spawned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    record = tmp_path / "record.json"
    stub_cli(monkeypatch, tmp_path, "agy", ["ok"], record=record)

    result = _run(tmp_path, prompt="a" * PROMPT_LIMIT_BYTES)

    assert result.outcome == Outcome.SUCCESS
    assert record.exists()


def test_prompt_inside_the_windows_command_line_limit_is_spawned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = tmp_path / "record.json"
    stub_cli(monkeypatch, tmp_path, "agy", ["ok"], record=record)
    monkeypatch.setattr(agy_module, "_WINDOWS", True)

    result = _run(tmp_path, prompt="a" * 20_000)

    assert result.outcome == Outcome.SUCCESS
    assert record.exists()


def test_windows_command_line_over_the_limit_is_request_fatal_and_removes_the_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, temp_dir: Path
) -> None:
    record = tmp_path / "record.json"
    stub_cli(monkeypatch, tmp_path, "agy", ["never"], record=record)
    monkeypatch.setattr(agy_module, "_WINDOWS", True)

    result = _run(tmp_path, prompt="a" * 40_000)

    assert result.outcome == Outcome.REQUEST_FATAL
    assert result.error is not None and result.error.code == "invalid_prompt"
    assert "32767" in result.error.message
    assert not record.exists()
    assert list(temp_dir.iterdir()) == []


@pytest.mark.parametrize(
    "prompt", [pytest.param("a\x00b", id="nul"), pytest.param("bad \ud800 prompt", id="surrogate")]
)
def test_unencodable_prompt_is_request_fatal_and_keeps_the_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, prompt: str
) -> None:
    record = tmp_path / "record.json"
    stub_cli(monkeypatch, tmp_path, "agy", ["never"], record=record)

    result = _run(tmp_path, prompt=prompt, session_id=CONVERSATION)

    assert result.outcome == Outcome.REQUEST_FATAL
    assert result.error is not None and result.error.code == "invalid_prompt"
    assert result.session_id == CONVERSATION
    assert not record.exists()


# --- Argv policy and transport ---


def test_resume_argv_carries_conversation_prompt_and_log_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, temp_dir: Path
) -> None:
    record = tmp_path / "record.json"
    stub_cli(monkeypatch, tmp_path, "agy", ["ok"], record=record)

    _run(tmp_path, prompt="hello there", session_id=CONVERSATION)

    seen = json.loads(record.read_text(encoding="utf-8"))
    argv = seen["argv"]
    assert argv[:4] == ["agy", "--output-format", "stream-json", "--log-file"]
    assert Path(argv[4]).parent == temp_dir and Path(argv[4]).name.startswith("coding-agent-cli-")
    assert argv[5:] == ["--conversation", CONVERSATION, "--print", "hello there"]
    assert seen["stdin"] == ""


def test_new_session_argv_uses_new_project_and_places_args_before_the_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Rewritten: Refract put --dangerously-skip-permissions first. The Phase 5 invariant removes it.
    record = tmp_path / "record.json"
    stub_cli(monkeypatch, tmp_path, "agy", ["ok"], record=record)

    _run(tmp_path, prompt="hi", args=("--flag",))

    argv = _argv(record)
    assert argv[:3] == ["agy", "--output-format", "stream-json"]
    assert argv[3] == "--log-file"
    assert argv[5:] == ["--new-project", "--flag", "--print", "hi"]
    assert "--conversation" not in argv


@pytest.mark.asyncio
async def test_cd_is_not_owned_by_agy_and_passes_through_as_a_caller_arg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Refract forwarded --cd unchanged for agy. The process cwd still comes from Request.cwd.
    record = tmp_path / "record.json"
    stub_cli(monkeypatch, tmp_path, "agy", ["ok"], record=record)

    result = await run_driver("agy", tmp_path, args=("--cd", "x"))

    assert result.outcome == Outcome.SUCCESS
    assert _argv(record)[-4:] == ["--cd", "x", "--print", "p"]


def test_model_maps_to_the_model_flag_before_caller_args(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    record = tmp_path / "record.json"
    stub_cli(monkeypatch, tmp_path, "agy", ["ok"], record=record)

    _run(tmp_path, model="m", args=("--x",))

    assert _argv(record)[5:] == ["--new-project", "--model", "m", "--x", "--print", "p"]


@pytest.mark.parametrize("model", [None, "m"])
@pytest.mark.parametrize("session_id", [None, CONVERSATION])
@pytest.mark.parametrize("args", [(), ("--x",)])
def test_no_permission_or_sandbox_flag_is_ever_added(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, model: str | None, session_id: str | None, args: tuple[str, ...]
) -> None:
    record = tmp_path / "record.json"
    stub_cli(monkeypatch, tmp_path, "agy", ["ok"], record=record)

    _run(tmp_path, model=model, session_id=session_id, args=args)

    argv = _argv(record)
    for flag in FORBIDDEN_FLAGS:
        assert not any(token == flag or token.startswith(f"{flag}=") for token in argv), flag


@pytest.mark.parametrize("args", OWNED_ARGS)
@pytest.mark.asyncio
async def test_raw_args_cannot_override_a_driver_owned_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, args: tuple[str, ...]
) -> None:
    record = tmp_path / "record.json"
    stub_cli(monkeypatch, tmp_path, "agy", ["never"], record=record)

    result = await run_driver("agy", tmp_path, args=args)

    assert result.outcome == Outcome.REQUEST_FATAL
    assert result.error is not None and result.error.code == "invalid_args"
    assert not record.exists()


@pytest.mark.asyncio
async def test_model_flag_in_args_conflicts_with_the_model_option(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = tmp_path / "record.json"
    stub_cli(monkeypatch, tmp_path, "agy", ["never"], record=record)

    result = await run_driver("agy", tmp_path, model="m", args=("--model=other",))

    assert result.outcome == Outcome.REQUEST_FATAL
    assert result.error is not None and result.error.code == "invalid_args"
    assert not record.exists()


@pytest.mark.parametrize(
    "options",
    [
        pytest.param({"reasoning": "high"}, id="reasoning"),
        pytest.param({"system_prompt": "sp"}, id="system-prompt"),
    ],
)
@pytest.mark.asyncio
async def test_reasoning_and_system_prompt_are_unsupported_and_spawn_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, options: dict[str, Any]
) -> None:
    record = tmp_path / "record.json"
    stub_cli(monkeypatch, tmp_path, "agy", ["never"], record=record)

    result = await run_driver("agy", tmp_path, **options)

    assert result.outcome == Outcome.REQUEST_FATAL
    assert result.error is not None and result.error.code == "unsupported_option"
    assert not record.exists()


@pytest.mark.asyncio
async def test_missing_workspace_is_rejected_before_spawn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    record = tmp_path / "record.json"
    stub_cli(monkeypatch, tmp_path, "agy", ["never"], record=record)

    result = await run_driver("agy", tmp_path / "absent")

    assert result.outcome == Outcome.REQUEST_FATAL
    assert result.error is not None and result.error.code == "bad_cwd"
    assert not record.exists()


@pytest.mark.asyncio
async def test_temporary_log_file_is_removed_on_success_and_on_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, temp_dir: Path
) -> None:
    record = tmp_path / "record.json"
    stub_cli(monkeypatch, tmp_path, "agy", [_step("ok")], record=record)
    await run_driver("agy", tmp_path)
    assert not Path(_argv(record)[4]).exists()

    stub_cli(monkeypatch, tmp_path, "agy", [_step("ok")], sleep_s=5)
    timed_out = await run_driver("agy", tmp_path, timeout_s=0.5)
    assert timed_out.error is not None and timed_out.error.code == "timeout"
    assert list(temp_dir.iterdir()) == []


@pytest.mark.asyncio
async def test_log_file_session_is_read_from_the_cli_written_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, temp_dir: Path
) -> None:
    stub_cli(monkeypatch, tmp_path, "agy", [_step("ok")], log_text=f"Created conversation {CONVERSATION}")

    result = await run_driver("agy", tmp_path)

    assert result.session_id == CONVERSATION
    assert list(temp_dir.iterdir()) == []


def test_driver_registry_returns_the_agy_driver() -> None:
    assert isinstance(DriverRegistry().get("agy"), AgyDriver)


def test_tool_completion_is_emitted_for_done_tool_steps() -> None:
    _, events = _feed_updates(
        {"step_type": "tool", "state": "ACTIVE", "tool_name": "x", "tool_info": {}},
        {"step_type": "tool", "state": "DONE", "tool_info": {}},
    )

    assert [type(e).__name__ for e in events] == ["ToolStarted", "ToolCompleted"]
