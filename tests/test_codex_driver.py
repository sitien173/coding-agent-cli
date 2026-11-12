"""Codex driver tests: recorded stream parsing, session sources, argv policy, and transport.

The parsing section feeds recorded JSONL lines through ``CodexRun``. The transport section runs the
real driver against the fake ``codex`` launcher. Rollout enrichment is covered in ``test_codex_rollout.py``.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
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
from coding_agent_cli._drivers._common import classify_tool_activity
from coding_agent_cli._drivers.codex import MAX_DIAGNOSTICS, CodexDriver, CodexRun, _toml_string
from coding_agent_cli._process import ProcessOutcome
from coding_agent_cli.events import Event

from .cli_stub import run_driver, stub_cli

USAGE_FIXTURE = Path(__file__).parent / "fixtures" / "usage" / "codex.jsonl"
FIXTURE_THREAD_ID = "00000000-0000-0000-0000-000000000000"
SESSION = "11111111-2222-3333-4444-555555555555"
OTHER = "66666666-7777-8888-9999-aaaaaaaaaaaa"
THIRD = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
FORBIDDEN_FLAGS = (
    "--yolo",
    "--full-auto",
    "--dangerously-bypass-approvals-and-sandbox",
    "--sandbox",
    "-s",
    "--ask-for-approval",
    "-a",
    "--approve-for-me",
)
TRANSPORT_PREFIX = ["codex", "exec", "--json", "-o"]


@pytest.fixture(autouse=True)
def _isolated_codex_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point CODEX_HOME at an empty tree so no run reads the real ~/.codex."""
    home = tmp_path / "codex-home"
    (home / "sessions").mkdir(parents=True)
    monkeypatch.setenv("CODEX_HOME", str(home))
    return home


@pytest.fixture
def temp_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Route ``tempfile`` to a per-test directory so temporary-file cleanup is observable."""
    directory = tmp_path / "tmp"
    directory.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(directory))
    return directory


def _event(**fields: object) -> str:
    return json.dumps(fields)


def _thread(thread_id: str) -> str:
    return _event(type="thread.started", thread_id=thread_id)


def _message(text: str, item_id: str = "item_1") -> str:
    return _event(type="item.completed", item={"id": item_id, "type": "agent_message", "text": text})


def _turn(input_tokens: int = 1000, output_tokens: int = 50, **extra: int) -> str:
    return _event(type="turn.completed", usage={"input_tokens": input_tokens, "output_tokens": output_tokens, **extra})


def _feed(*lines: str, cwd: Path, requested_session: str | None = None) -> tuple[CodexRun, list[Event]]:
    """Feed lines through the parser without a process."""
    events: list[Event] = []

    async def sink(event: Event) -> None:
        events.append(event)

    async def go() -> CodexRun:
        run = CodexRun(sink, cwd=cwd, env={}, requested_session=requested_session)
        for line in lines:
            await run.feed(line)
        return run

    return asyncio.run(go()), events


def _parse(
    *lines: str,
    cwd: Path,
    last_message: str = "",
    process: ProcessOutcome | None = None,
    requested_session: str | None = None,
) -> tuple[Result, list[Event]]:
    run, events = _feed(*lines, cwd=cwd, requested_session=requested_session)
    return run.finish(process or ProcessOutcome(), asyncio.Event(), last_message), events


def _run(tmp_path: Path, **fields: Any) -> Result:
    return asyncio.run(run_driver("codex", tmp_path, **fields))


def _argv(record: Path) -> list[str]:
    return json.loads(record.read_text(encoding="utf-8"))["argv"]


# --- Recorded stream and session sources ---


def test_recorded_stream_pins_text_session_and_telemetry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stub_cli(monkeypatch, tmp_path, "codex", USAGE_FIXTURE.read_text(encoding="utf-8").splitlines())

    result = _run(tmp_path)

    assert result.outcome == Outcome.SUCCESS
    assert result.error is None
    assert result.text == "Turn 1 replyTurn 2 reply"
    assert result.session_id == FIXTURE_THREAD_ID
    assert result.model is None
    assert result.usage == Usage(input=22000, output=50, cache_read=10000, cache_write=800, context_used=12015)


def test_thread_started_wins_over_last_message_and_stdout_session_line(tmp_path: Path) -> None:
    result, _ = _parse(
        _thread(SESSION),
        _message("ok"),
        f"session id: {THIRD}",
        cwd=tmp_path,
        last_message=f"session id: {OTHER}",
    )

    assert result.session_id == SESSION


def test_last_message_session_line_is_used_without_thread_event(tmp_path: Path) -> None:
    result, _ = _parse(_message("ok"), f"session id: {THIRD}", cwd=tmp_path, last_message=f"done\nsession id: {OTHER}")

    assert result.session_id == OTHER


def test_stdout_session_line_is_used_without_thread_or_last_message(tmp_path: Path) -> None:
    result, _ = _parse(_message("ok"), f"session id: {THIRD}", cwd=tmp_path)

    assert result.session_id == THIRD


def test_fresh_run_without_session_source_stores_no_session_id(tmp_path: Path) -> None:
    result, _ = _parse(_message("ok"), cwd=tmp_path)

    assert result.outcome == Outcome.SUCCESS
    assert result.session_id is None
    assert [w.code for w in result.warnings] == ["no_session_id"]


def test_resume_without_exact_session_source_stores_no_session_id(tmp_path: Path) -> None:
    result, _ = _parse(_message("ok"), cwd=tmp_path, requested_session=SESSION)

    assert result.session_id is None


def test_last_message_file_is_answer_when_no_agent_item_exists(tmp_path: Path) -> None:
    result, _ = _parse(_thread(SESSION), cwd=tmp_path, last_message="from the file\n")

    assert result.text == "from the file"


def test_blank_last_message_falls_back_to_agent_items(tmp_path: Path) -> None:
    result, _ = _parse(_thread(SESSION), _message("from items"), cwd=tmp_path, last_message="   \n")

    assert result.text == "from items"


# --- Outcome classification ---


def test_nonzero_exit_with_agent_message_is_retryable_execution_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stub_cli(monkeypatch, tmp_path, "codex", [_thread(SESSION), _message("partial")], exit_code=2)

    result = _run(tmp_path)

    assert result.outcome == Outcome.RETRYABLE
    assert result.error is not None and result.error.code == "execution_error"
    assert result.text == "partial"
    assert result.returncode == 2


def test_auth_diagnostic_beats_a_nonzero_exit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Rewritten: Refract mapped this to execution_error. The Phase 5 invariant makes an auth diagnostic win over the exit code.
    stub_cli(monkeypatch, tmp_path, "codex", ["error: unknown option, not logged in"], exit_code=2)

    result = _run(tmp_path)

    assert result.outcome == Outcome.AGENT_FATAL
    assert result.error == Diagnostic(code="auth", message="error: unknown option, not logged in")


def test_timeout_keeps_partial_text_and_timeout_class(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stub_cli(monkeypatch, tmp_path, "codex", [_thread(SESSION), _message("partial")], sleep_s=5)

    result = _run(tmp_path, timeout_s=0.5)

    assert result.outcome == Outcome.RETRYABLE
    assert result.error is not None and result.error.code == "timeout"
    assert result.text == "partial"


def test_fatal_token_with_agent_message_does_not_fail_the_run(tmp_path: Path) -> None:
    result, _ = _parse("Error: not logged in", _message("answer"), cwd=tmp_path)

    assert result.outcome == Outcome.SUCCESS
    assert result.text == "answer"


def test_fatal_token_in_diagnostic_without_agent_message_is_agent_fatal_auth(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Rewritten: Refract returned TARGET_FATAL fatal_backend. The auth keyword now maps to agent_fatal with code auth.
    stub_cli(monkeypatch, tmp_path, "codex", ["Error: not logged in"])

    result = _run(tmp_path)

    assert result.outcome == Outcome.AGENT_FATAL
    assert result.error == Diagnostic(code="auth", message="Error: not logged in")


def test_exit_zero_without_agent_message_is_no_output_and_never_returns_jsonl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Rewritten: Refract returned request_fatal. The Phase 5 invariant maps empty output to no_output.
    stub_cli(monkeypatch, tmp_path, "codex", [_thread(SESSION), _turn()])

    result = _run(tmp_path)

    assert result.outcome == Outcome.RETRYABLE
    assert result.error is not None and result.error.code == "no_output"
    assert result.text == ""


def test_blank_last_message_without_agent_message_is_no_output(tmp_path: Path) -> None:
    result, _ = _parse(_thread(SESSION), cwd=tmp_path, last_message="  \n")

    assert result.outcome == Outcome.RETRYABLE
    assert result.error is not None and result.error.code == "no_output"


def test_no_output_message_names_the_missing_assistant_text(tmp_path: Path) -> None:
    result, _ = _parse(cwd=tmp_path, last_message="   ")

    assert result.error is not None
    assert result.error.message == "the CLI produced no assistant text"


# --- Parsed events ---


def test_thread_started_emits_session_once(tmp_path: Path) -> None:
    _, events = _feed(_thread(SESSION), _thread(SESSION), cwd=tmp_path)

    assert [e for e in events if isinstance(e, SessionIdentified)] == [SessionIdentified(session_id=SESSION)]


def test_item_started_emits_message_and_tool_start_events(tmp_path: Path) -> None:
    _, events = _feed(
        _event(type="item.started", item={"id": "m", "type": "agent_message"}),
        _event(type="item.started", item={"id": "t", "type": "tool_call", "name": "bash", "input": {"cmd": "ls"}}),
        _event(type="item.started", item={"id": "u", "type": "tool_call", "name": "Bash"}),
        cwd=tmp_path,
    )

    assert [e.kind for e in events] == ["assistant.message.started", "tool.started", "tool.started"]
    assert events[1] == ToolStarted(tool="bash", activity="command", input={"cmd": "ls"}, entity_id="tool-2")
    assert isinstance(events[2], ToolStarted) and events[2].activity == "tool_call"


def test_item_completed_emits_text_and_tool_completion_events(tmp_path: Path) -> None:
    _, events = _feed(
        _event(type="item.started", item={"id": "t", "type": "tool_call", "name": "bash"}),
        _event(type="item.completed", item={"id": "t", "type": "tool_call", "status": "failed"}),
        _event(type="item.completed", item={"id": "m", "type": "agent_message", "text": "hi"}),
        cwd=tmp_path,
    )

    assert events[1] == ToolCompleted(status="error", entity_id="tool-1")
    assert events[2] == TextDelta(text="hi", entity_id="msg-1")
    assert events[3].kind == "assistant.message.completed"


def test_item_completion_without_start_uses_fallback_entity_ids(tmp_path: Path) -> None:
    _, events = _feed(_event(type="item.completed", item={"id": "orphan", "type": "tool_call", "status": "completed"}), cwd=tmp_path)

    assert events == [ToolCompleted(status="completed", entity_id="tool-1")]


@pytest.mark.parametrize("item", [None, "text", 3, ["x"]])
def test_non_object_item_is_ignored(tmp_path: Path, item: object) -> None:
    result, events = _parse(_event(type="item.started", item=item), _event(type="item.completed", item=item), cwd=tmp_path)

    assert events == []
    assert result.outcome == Outcome.RETRYABLE


def test_reasoning_items_and_non_json_diagnostics_are_never_emitted_as_text(tmp_path: Path) -> None:
    _, events = _feed(
        _event(type="item.completed", item={"id": "r", "type": "reasoning", "text": "secret thoughts"}),
        "some diagnostic line",
        cwd=tmp_path,
    )

    assert events == []


def test_turn_completed_usage_updates_counters_and_emits(tmp_path: Path) -> None:
    result, events = _parse(_turn(1000, 50, cached_input_tokens=200), cwd=tmp_path)

    assert result.usage.input == 1000
    assert result.usage.cache_read == 200
    assert events == [UsageUpdated(usage=Usage(input=1000, output=50, cache_read=200, context_used=1050))]


def test_cached_input_is_not_added_twice(tmp_path: Path) -> None:
    # Codex input_tokens already includes cached_input_tokens, so the normalized input stays 1000.
    result, _ = _parse(_turn(1000, 10, cached_input_tokens=200), cwd=tmp_path)

    assert result.usage.input == 1000


def test_turn_completed_without_usage_object_is_ignored(tmp_path: Path) -> None:
    _, events = _feed(_event(type="turn.completed"), _event(type="turn.completed", usage="not-a-dict"), cwd=tmp_path)

    assert events == []


def test_turn_completed_without_input_leaves_input_unavailable(tmp_path: Path) -> None:
    result, _ = _parse(_event(type="turn.completed", usage={"output_tokens": 7}), cwd=tmp_path)

    assert result.usage.input is None
    assert result.usage.output == 7


def test_malformed_usage_fields_are_ignored_and_do_not_alter_the_outcome(tmp_path: Path) -> None:
    result, events = _parse(
        _message("ok"),
        _event(type="turn.completed", usage={"input_tokens": "x", "output_tokens": True, "cached_input_tokens": -1}),
        _turn(100, 5),
        cwd=tmp_path,
    )

    assert result.outcome == Outcome.SUCCESS
    assert result.text == "ok"
    assert result.usage.input == 100
    assert result.usage.output == 5


def test_turns_sum_across_consecutive_turn_completed_events(tmp_path: Path) -> None:
    result, _ = _parse(_turn(100, 10), _turn(200, 20), cwd=tmp_path)

    assert result.usage.input == 300
    assert result.usage.output == 30
    assert result.usage.context_used == 220


def test_final_usage_records_totals_for_fresh_runs(tmp_path: Path) -> None:
    result, _ = _parse(
        _turn(100, 10),
        _event(type="thread.completed", usage={"input_tokens": 500, "output_tokens": 40, "cached_input_tokens": 30}),
        cwd=tmp_path,
    )

    assert result.usage.input == 500
    assert result.usage.output == 40
    assert result.usage.cache_read == 30


def test_final_usage_is_not_recorded_on_resume(tmp_path: Path) -> None:
    result, _ = _parse(
        _turn(100, 10),
        _event(type="summary", usage={"input_tokens": 500, "output_tokens": 40}),
        cwd=tmp_path,
        requested_session=SESSION,
    )

    assert result.usage.input == 100


def test_final_usage_without_totals_to_record_emits_nothing(tmp_path: Path) -> None:
    _, events = _feed(_event(type="thread.completed", usage={"other": 1}), cwd=tmp_path)

    assert events == []


@pytest.mark.parametrize(
    ("tool_name", "expected"),
    [("bash", "command"), ("Bash", "tool_call"), ("bash ", "tool_call"), ("shell", "tool_call")],
)
def test_tool_activity_allow_list_is_exact_and_case_sensitive(tool_name: str, expected: str) -> None:
    assert classify_tool_activity("codex", tool_name) == expected


def test_diagnostic_lines_are_bounded_and_a_later_fatal_line_is_kept(tmp_path: Path) -> None:
    noise = [f"noise {i}" for i in range(MAX_DIAGNOSTICS + 10)]
    result, _ = _parse(*noise, "fatal: not logged in", cwd=tmp_path)

    assert result.error is not None and result.error.code == "auth"
    assert result.error.message.splitlines()[-1] == "fatal: not logged in"
    assert len(result.error.message.splitlines()) == MAX_DIAGNOSTICS + 1


def test_toml_string_quotes_and_escapes_control_characters() -> None:
    assert _toml_string("high") == '"high"'
    assert _toml_string('a"b\\c') == '"a\\"b\\\\c"'
    assert _toml_string("x\ny\x01\x7f") == '"x\\ny\\u0001\\u007F"'


# --- Transport: argv, stdin, cwd, temporary file ---


def test_resume_argv_and_prompt_delivery_are_pinned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, temp_dir: Path) -> None:
    record = tmp_path / "record.json"
    stub_cli(monkeypatch, tmp_path, "codex", [_thread(SESSION), _message("ok")], record=record)

    _run(tmp_path, prompt="the prompt", session_id=OTHER)

    payload = json.loads(record.read_text(encoding="utf-8"))
    argv = payload["argv"]
    assert argv[:4] == TRANSPORT_PREFIX
    assert Path(argv[4]).parent == temp_dir
    assert Path(argv[4]).name.startswith("coding-agent-cli-")
    assert argv[5:] == ["resume", OTHER, "-"]
    assert payload["stdin"] == "the prompt"
    assert Path(payload["cwd"]).resolve() == tmp_path.resolve()


def test_driver_never_passes_a_working_directory_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    record = tmp_path / "record.json"
    stub_cli(monkeypatch, tmp_path, "codex", [_message("ok")], record=record)

    _run(tmp_path, model="m", reasoning="high", args=("--x",))

    argv = _argv(record)
    assert "--cd" not in argv and "-C" not in argv


@pytest.mark.asyncio
async def test_portable_options_map_to_codex_flags_before_caller_args(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, temp_dir: Path) -> None:
    record = tmp_path / "record.json"
    stub_cli(monkeypatch, tmp_path, "codex", [_message("ok")], record=record)

    await run_driver("codex", tmp_path, model="m", reasoning="high", args=("--x",))

    argv = _argv(record)
    assert argv[:4] == TRANSPORT_PREFIX
    assert argv[5:] == ["--model", "m", "-c", 'model_reasoning_effort="high"', "--x", "-"]


def test_reasoning_value_is_toml_quoted_in_the_config_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    record = tmp_path / "record.json"
    stub_cli(monkeypatch, tmp_path, "codex", [_message("ok")], record=record)

    _run(tmp_path, reasoning='h"igh')

    argv = _argv(record)
    assert argv[argv.index("-c") + 1] == 'model_reasoning_effort="h\\"igh"'


def test_caller_args_pass_through_and_lowercase_config_flag_is_allowed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    record = tmp_path / "record.json"
    stub_cli(monkeypatch, tmp_path, "codex", [_message("ok")], record=record)

    result = _run(tmp_path, args=("-c", "profile=fast", "--skip-git-repo-check"))

    assert result.outcome == Outcome.SUCCESS
    assert _argv(record)[5:] == ["-c", "profile=fast", "--skip-git-repo-check", "-"]


@pytest.mark.parametrize(
    "options",
    [
        pytest.param({}, id="none"),
        pytest.param({"model": "m"}, id="model"),
        pytest.param({"reasoning": "high"}, id="reasoning"),
        pytest.param({"session_id": OTHER}, id="resume"),
        pytest.param({"args": ("--x",)}, id="args"),
        pytest.param({"model": "m", "reasoning": "high", "session_id": OTHER, "args": ("--x",)}, id="all"),
    ],
)
def test_no_approval_sandbox_or_bypass_flag_is_ever_added(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, options: dict[str, Any]) -> None:
    record = tmp_path / "record.json"
    stub_cli(monkeypatch, tmp_path, "codex", [_message("ok")], record=record)

    _run(tmp_path, **options)

    argv = _argv(record)
    for flag in FORBIDDEN_FLAGS:
        assert not any(token == flag or token.startswith(f"{flag}=") for token in argv), flag


@pytest.mark.parametrize(
    "args",
    [
        pytest.param(("--cd", "/tmp"), id="cd"),
        pytest.param(("--cd=/tmp",), id="cd-equals"),
        pytest.param(("-C", "/tmp"), id="short-C"),
        pytest.param(("-C/tmp",), id="short-C-attached"),
    ],
)
@pytest.mark.asyncio
async def test_working_directory_args_are_rejected_before_spawn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, args: tuple[str, ...]) -> None:
    record = tmp_path / "record.json"
    stub_cli(monkeypatch, tmp_path, "codex", ["never"], record=record)

    result = await run_driver("codex", tmp_path, args=args)

    assert result.outcome == Outcome.REQUEST_FATAL
    assert result.error is not None and result.error.code == "invalid_args"
    assert not record.exists()


@pytest.mark.parametrize(
    "args",
    [
        pytest.param(("--json",), id="json"),
        pytest.param(("-o", "/x"), id="o"),
        pytest.param(("--output-last-message=/x",), id="output-equals"),
    ],
)
@pytest.mark.asyncio
async def test_raw_args_cannot_override_a_driver_owned_transport_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, args: tuple[str, ...]) -> None:
    record = tmp_path / "record.json"
    stub_cli(monkeypatch, tmp_path, "codex", ["never"], record=record)

    result = await run_driver("codex", tmp_path, args=args)

    assert result.outcome == Outcome.REQUEST_FATAL
    assert result.error is not None and result.error.code == "invalid_args"
    assert not record.exists()


@pytest.mark.parametrize(
    ("options", "args"),
    [
        pytest.param({"model": "m"}, ("--model", "other"), id="model"),
        pytest.param({"model": "m"}, ("--model=other",), id="model-equals"),
        pytest.param({"reasoning": "high"}, ("-c", "model_reasoning_effort=low"), id="config"),
        pytest.param({"reasoning": "high"}, ("--config=model_reasoning_effort=low",), id="config-equals"),
    ],
)
@pytest.mark.asyncio
async def test_raw_args_cannot_conflict_with_a_portable_option(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, options: dict[str, Any], args: tuple[str, ...]) -> None:
    record = tmp_path / "record.json"
    stub_cli(monkeypatch, tmp_path, "codex", ["never"], record=record)

    result = await run_driver("codex", tmp_path, args=args, **options)

    assert result.outcome == Outcome.REQUEST_FATAL
    assert result.error is not None and result.error.code == "invalid_args"
    assert not record.exists()


@pytest.mark.asyncio
async def test_system_prompt_is_unsupported_and_spawns_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    record = tmp_path / "record.json"
    stub_cli(monkeypatch, tmp_path, "codex", ["never"], record=record)

    result = await run_driver("codex", tmp_path, system_prompt="sp")

    assert result.outcome == Outcome.REQUEST_FATAL
    assert result.error == Diagnostic(code="unsupported_option", message="driver 'codex' does not support system_prompt")
    assert not record.exists()


@pytest.mark.asyncio
async def test_bad_cwd_is_rejected_before_spawn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    record = tmp_path / "record.json"
    stub_cli(monkeypatch, tmp_path, "codex", ["never"], record=record)

    result = await run_driver("codex", tmp_path / "absent")

    assert result.outcome == Outcome.REQUEST_FATAL
    assert result.error is not None and result.error.code == "bad_cwd"
    assert not record.exists()


@pytest.mark.parametrize("args", [pytest.param(("--",), id="dashdash"), pytest.param(("a\x00",), id="nul")])
@pytest.mark.asyncio
async def test_invalid_raw_args_are_rejected_before_spawn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, args: tuple[str, ...]) -> None:
    record = tmp_path / "record.json"
    stub_cli(monkeypatch, tmp_path, "codex", ["never"], record=record)

    result = await run_driver("codex", tmp_path, args=args)

    assert result.outcome == Outcome.REQUEST_FATAL
    assert result.error is not None and result.error.code == "invalid_args"
    assert not record.exists()


@pytest.mark.asyncio
async def test_env_values_and_prompt_never_reach_argv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    record = tmp_path / "record.json"
    stub_cli(monkeypatch, tmp_path, "codex", [_message("ok")], record=record)

    await run_driver("codex", tmp_path, prompt="secret prompt", env={"CODEX_API_TOKEN": "s3cr3t"})

    argv = _argv(record)
    assert not any("s3cr3t" in token or "secret prompt" in token for token in argv)


@pytest.mark.asyncio
async def test_cancel_before_start_is_cancelled_and_spawns_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    record = tmp_path / "record.json"
    stub_cli(monkeypatch, tmp_path, "codex", ["never"], record=record)
    cancel = asyncio.Event()
    cancel.set()

    result = await run_driver("codex", tmp_path, cancel=cancel)

    assert result.outcome == Outcome.CANCELLED
    assert not record.exists()


@pytest.mark.asyncio
async def test_temporary_last_message_file_is_removed_on_success_and_on_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, temp_dir: Path) -> None:
    record = tmp_path / "record.json"
    stub_cli(monkeypatch, tmp_path, "codex", [_message("ok")], record=record)
    await run_driver("codex", tmp_path)
    assert not Path(_argv(record)[4]).exists()

    stub_cli(monkeypatch, tmp_path, "codex", [_message("ok")], sleep_s=5)
    timed_out = await run_driver("codex", tmp_path, timeout_s=0.5)
    assert timed_out.error is not None and timed_out.error.code == "timeout"
    assert list(temp_dir.iterdir()) == []


@pytest.mark.asyncio
async def test_last_message_file_is_written_by_the_cli_and_read_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stub_cli(monkeypatch, tmp_path, "codex", [_thread(SESSION)], last_message="final answer")

    result = await run_driver("codex", tmp_path)

    assert result.outcome == Outcome.SUCCESS
    assert result.text == "final answer"
    assert result.session_id == SESSION


def test_driver_registry_returns_the_codex_driver() -> None:
    assert isinstance(DriverRegistry().get("codex"), CodexDriver)
