"""Tests for CodexDriver launch policy, parser, and recorded fixture replay."""

from __future__ import annotations

from pathlib import Path

import pytest

from coding_agent_cli._drivers.codex import CodexDriver
from coding_agent_cli.driver import RequestError
from coding_agent_cli.types import ErrorCode, Request

_FORBIDDEN_PERMISSION_FLAGS = (
    "--dangerously-bypass-approvals-and-sandbox",
    "--full-auto",
    "-s",
    "--sandbox",
    "-a",
    "--ask-for-approval",
)


def _req(tmp_path: Path, **overrides: object) -> Request:
    defaults: dict[str, object] = {
        "driver": "codex",
        "prompt": "hello",
        "cwd": tmp_path,
    }
    defaults.update(overrides)
    return Request(**defaults)  # type: ignore[arg-type]


def test_launch_plain_argv_and_stdin(tmp_path: Path) -> None:
    driver = CodexDriver()
    launch = driver.launch(_req(tmp_path), Path("/usr/bin/codex"), tmp_path)
    assert launch.argv == ("/usr/bin/codex", "exec", "--json", "-")
    assert launch.stdin == b"hello"
    assert launch.read_back == ()


def test_launch_resume_placement(tmp_path: Path) -> None:
    driver = CodexDriver()
    launch = driver.launch(
        _req(tmp_path, session_id="00000000-0000-4000-8000-000000000001", model="gpt-5", reasoning="low"),
        Path("/usr/bin/codex"),
        tmp_path,
    )
    assert launch.argv == (
        "/usr/bin/codex",
        "exec",
        "resume",
        "--json",
        "--model",
        "gpt-5",
        "-c",
        'model_reasoning_effort="low"',
        "00000000-0000-4000-8000-000000000001",
        "-",
    )


def test_launch_rejects_system_prompt(tmp_path: Path) -> None:
    driver = CodexDriver()
    with pytest.raises(RequestError) as exc_info:
        driver.launch(_req(tmp_path, system_prompt="be brief"), Path("/usr/bin/codex"), tmp_path)
    assert exc_info.value.code == ErrorCode.UNSUPPORTED_OPTION


def test_launch_rejects_owned_json_flag(tmp_path: Path) -> None:
    driver = CodexDriver()
    with pytest.raises(RequestError) as exc_info:
        driver.launch(_req(tmp_path, args=("--json",)), Path("/usr/bin/codex"), tmp_path)
    assert exc_info.value.code == ErrorCode.INVALID_ARGS


@pytest.mark.parametrize("cd_args", [("--cd", "/tmp"), ("--cd=/tmp",), ("-C", "/tmp"), ("-C/tmp",)])
def test_launch_rejects_cd_flags(tmp_path: Path, cd_args: tuple[str, ...]) -> None:
    driver = CodexDriver()
    with pytest.raises(RequestError) as exc_info:
        driver.launch(_req(tmp_path, args=cd_args), Path("/usr/bin/codex"), tmp_path)
    assert exc_info.value.code == ErrorCode.INVALID_ARGS


@pytest.mark.parametrize(
    "conflict_args",
    [
        ("-c", "model_reasoning_effort=high"),
        ("--config", "model_reasoning_effort=high"),
        ("-c=model_reasoning_effort=high",),
        ("--config=model_reasoning_effort=high",),
    ],
)
def test_launch_rejects_conflicting_reasoning_config(tmp_path: Path, conflict_args: tuple[str, ...]) -> None:
    driver = CodexDriver()
    with pytest.raises(RequestError) as exc_info:
        driver.launch(_req(tmp_path, reasoning="low", args=conflict_args), Path("/usr/bin/codex"), tmp_path)
    assert exc_info.value.code == ErrorCode.INVALID_ARGS


def test_launch_accepts_unrelated_c_args_with_reasoning(tmp_path: Path) -> None:
    driver = CodexDriver()
    launch = driver.launch(
        _req(tmp_path, reasoning="low", args=("-c", "foo=bar", "--config", 'sandbox_mode="read-only"')),
        Path("/usr/bin/codex"),
        tmp_path,
    )
    assert launch.argv == (
        "/usr/bin/codex",
        "exec",
        "--json",
        "-c",
        'model_reasoning_effort="low"',
        "-c",
        "foo=bar",
        "--config",
        'sandbox_mode="read-only"',
        "-",
    )


def test_launch_attached_reasoning_conflict_and_unrelated_config(tmp_path: Path) -> None:
    driver = CodexDriver()
    with pytest.raises(RequestError) as exc_info:
        driver.launch(
            _req(tmp_path, reasoning="low", args=("-cmodel_reasoning_effort=high",)),
            Path("/usr/bin/codex"),
            tmp_path,
        )
    assert exc_info.value.code == ErrorCode.INVALID_ARGS
    launch = driver.launch(
        _req(tmp_path, reasoning="low", args=("-cmodel=custom",)),
        Path("/usr/bin/codex"),
        tmp_path,
    )
    assert "-cmodel=custom" in launch.argv


def test_launch_rejects_conflicting_model_flag(tmp_path: Path) -> None:
    driver = CodexDriver()
    with pytest.raises(RequestError) as exc_info:
        driver.launch(_req(tmp_path, model="gpt-5", args=("-m", "other")), Path("/usr/bin/codex"), tmp_path)
    assert exc_info.value.code == ErrorCode.INVALID_ARGS


def test_launch_rejects_attached_model_flag_but_accepts_unrelated_short_option(tmp_path: Path) -> None:
    driver = CodexDriver()
    with pytest.raises(RequestError) as exc_info:
        driver.launch(_req(tmp_path, model="gpt-5", args=("-mother",)), Path("/usr/bin/codex"), tmp_path)
    assert exc_info.value.code == ErrorCode.INVALID_ARGS
    launch = driver.launch(_req(tmp_path, args=("-mystery",)), Path("/usr/bin/codex"), tmp_path)
    assert "-mystery" in launch.argv


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"model": "gpt-5"},
        {"reasoning": "high"},
        {"session_id": "sess-1", "model": "gpt-5", "reasoning": "low"},
    ],
)
def test_launch_never_adds_permission_flags(tmp_path: Path, kwargs: dict[str, object]) -> None:
    driver = CodexDriver()
    launch = driver.launch(_req(tmp_path, **kwargs), Path("/usr/bin/codex"), tmp_path)
    for token in launch.argv:
        flag = token.split("=", 1)[0]
        assert flag not in _FORBIDDEN_PERMISSION_FLAGS
    assert "-o" not in launch.argv
    assert "--output-last-message" not in launch.argv


def test_parser_thread_started_agent_message_and_usage(tmp_path: Path) -> None:
    parser = CodexDriver().parser(_req(tmp_path))
    events = [
        *parser.feed_json({"type": "thread.started", "thread_id": "thread-123"}),
        *parser.feed_json({"type": "turn.started"}),
        *parser.feed_json(
            {
                "type": "item.completed",
                "item": {
                    "id": "id-0003",
                    "type": "agent_message",
                    "text": "session id: 11111111-1111-4111-8111-111111111111\nPONG",
                },
            }
        ),
        *parser.feed_json(
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 15374,
                    "cached_input_tokens": 7808,
                    "cache_write_input_tokens": 0,
                    "output_tokens": 6,
                    "reasoning_output_tokens": 0,
                },
            }
        ),
    ]
    from coding_agent_cli.events import MessageCompleted, MessageStarted, SessionIdentified, TextDelta, UsageUpdated
    from coding_agent_cli.types import Usage

    assert events == [
        SessionIdentified(session_id="thread-123", entity_id=None),
        MessageStarted(entity_id="id-0003"),
        TextDelta(text="session id: 11111111-1111-4111-8111-111111111111\nPONG", entity_id="id-0003"),
        MessageCompleted(entity_id="id-0003"),
        UsageUpdated(usage=Usage(input=15374, output=6, cache_read=7808, cache_write=0)),
    ]
    facts = parser.finish({})
    assert facts.session_id == "thread-123"
    assert facts.model is None
    assert facts.num_turns is None


def test_parser_no_session_id_regex_over_model_text(tmp_path: Path) -> None:
    parser = CodexDriver().parser(_req(tmp_path))
    list(
        parser.feed_json(
            {
                "type": "item.completed",
                "item": {
                    "id": "id-0001",
                    "type": "agent_message",
                    "text": "session id: 11111111-1111-4111-8111-111111111111",
                },
            }
        )
    )
    facts = parser.finish({})
    assert facts.session_id is None


def test_command_execution_produces_tool_started_with_activity_command(tmp_path: Path) -> None:
    from coding_agent_cli.events import ToolCompleted, ToolStarted

    parser = CodexDriver().parser(_req(tmp_path))
    started = list(
        parser.feed_json(
            {
                "type": "item.started",
                "item": {
                    "id": "id-0004",
                    "type": "command_execution",
                    "command": "/usr/bin/zsh -lc ls",
                    "aggregated_output": "",
                    "exit_code": None,
                    "status": "in_progress",
                },
            }
        )
    )
    completed = list(
        parser.feed_json(
            {
                "type": "item.completed",
                "item": {
                    "id": "id-0004",
                    "type": "command_execution",
                    "command": "/usr/bin/zsh -lc ls",
                    "aggregated_output": "calc.py\nhello.txt\n",
                    "exit_code": 0,
                    "status": "completed",
                },
            }
        )
    )
    failed = list(
        parser.feed_json(
            {
                "type": "item.completed",
                "item": {
                    "id": "id-0005",
                    "type": "command_execution",
                    "command": "/usr/bin/zsh -lc false",
                    "aggregated_output": "boom\n",
                    "exit_code": 1,
                    "status": "failed",
                },
            }
        )
    )
    assert started == [
        ToolStarted(
            tool="command_execution",
            activity="command",
            input={"command": "/usr/bin/zsh -lc ls"},
            entity_id="id-0004",
        )
    ]
    assert completed == [
        ToolCompleted(
            tool="command_execution",
            status="completed",
            output="calc.py\nhello.txt\n",
            entity_id="id-0004",
        )
    ]
    assert failed[-1] == ToolCompleted(
        tool="command_execution",
        status="error",
        output="boom\n",
        entity_id="id-0005",
    )


def test_file_change_produces_file_changed(tmp_path: Path) -> None:
    from coding_agent_cli.events import FileChanged, ToolCompleted, ToolStarted

    parser = CodexDriver().parser(_req(tmp_path))
    started = list(
        parser.feed_json(
            {
                "type": "item.started",
                "item": {
                    "id": "id-0005",
                    "type": "file_change",
                    "changes": [{"path": "/work/repo/hello.txt", "kind": "update"}],
                    "status": "in_progress",
                },
            }
        )
    )
    completed = list(
        parser.feed_json(
            {
                "type": "item.completed",
                "item": {
                    "id": "id-0005",
                    "type": "file_change",
                    "changes": [
                        {"path": "/work/repo/hello.txt", "kind": "update"},
                        {"path": "/work/repo/new.txt", "kind": "add"},
                        {"path": "/work/repo/old.txt", "kind": "delete"},
                    ],
                    "status": "completed",
                },
            }
        )
    )
    assert started == [
        ToolStarted(
            tool="file_change",
            activity="tool_call",
            input={"changes": [{"path": "/work/repo/hello.txt", "kind": "update"}]},
            entity_id="id-0005",
        )
    ]
    assert completed == [
        ToolCompleted(tool="file_change", status="completed", output=None, entity_id="id-0005"),
        FileChanged(path="/work/repo/hello.txt", change="modified", evidence="reported", entity_id="id-0005"),
        FileChanged(path="/work/repo/new.txt", change="added", evidence="reported", entity_id="id-0005"),
        FileChanged(path="/work/repo/old.txt", change="deleted", evidence="reported", entity_id="id-0005"),
    ]


@pytest.mark.parametrize("kind", ["create", "modify", "remove"])
def test_file_change_uses_only_codex_patch_kinds(tmp_path: Path, kind: str) -> None:
    from coding_agent_cli.events import FileChanged

    parser = CodexDriver().parser(_req(tmp_path))
    events = list(
        parser.feed_json(
            {"type": "item.completed", "item": {"type": "file_change", "changes": [{"path": "a", "kind": kind}]}}
        )
    )
    assert isinstance(events[-1], FileChanged)
    assert events[-1].change == "unknown"


def test_mcp_tool_name_uses_schema_field_consistently(tmp_path: Path) -> None:
    parser = CodexDriver().parser(_req(tmp_path))
    started = list(
        parser.feed_json({"type": "item.started", "item": {"type": "mcp_tool_call", "name": "unsupported-alias"}})
    )
    completed = list(
        parser.feed_json(
            {
                "type": "item.completed",
                "item": {"type": "mcp_tool_call", "name": "unsupported-alias", "status": "completed"},
            }
        )
    )
    assert started == []
    assert completed == []


def test_parser_mcp_web_search_reasoning_error_item_and_todo_list(tmp_path: Path) -> None:
    from coding_agent_cli.events import Notice, ReasoningDelta, ToolCompleted, ToolStarted

    parser = CodexDriver().parser(_req(tmp_path))
    events = [
        *parser.feed_json({"type": "item.started", "item": {"id": "id-1", "type": "mcp_tool_call", "tool": "fetch"}}),
        *parser.feed_json(
            {
                "type": "item.completed",
                "item": {"id": "id-1", "type": "mcp_tool_call", "tool": "fetch", "status": "completed", "result": "ok"},
            }
        ),
        *parser.feed_json({"type": "item.started", "item": {"id": "id-2", "type": "web_search", "query": "python"}}),
        *parser.feed_json(
            {"type": "item.completed", "item": {"id": "id-2", "type": "web_search", "status": "completed"}}
        ),
        *parser.feed_json({"type": "item.completed", "item": {"id": "id-3", "type": "reasoning", "text": "thinking"}}),
        *parser.feed_json({"type": "item.completed", "item": {"id": "id-4", "type": "error", "message": "warn"}}),
        *parser.feed_json({"type": "item.completed", "item": {"id": "id-5", "type": "todo_list", "items": []}}),
    ]
    assert events == [
        ToolStarted(tool="fetch", activity="tool_call", input=None, entity_id="id-1"),
        ToolCompleted(tool="fetch", status="completed", output="ok", entity_id="id-1"),
        ToolStarted(tool="web_search", activity="tool_call", input=None, entity_id="id-2"),
        ToolCompleted(tool="web_search", status="completed", output=None, entity_id="id-2"),
        ReasoningDelta(text="thinking", entity_id="id-3"),
        Notice(code="error", message="warn", entity_id="id-4"),
    ]


@pytest.mark.parametrize("with_id", [False, True])
@pytest.mark.parametrize("with_start", [False, True])
def test_tool_completion_is_always_paired(with_id: bool, with_start: bool, tmp_path: Path) -> None:
    from coding_agent_cli.events import ToolCompleted, ToolStarted

    parser = CodexDriver().parser(_req(tmp_path))
    item = {"type": "web_search", "query": "python"}
    if with_id:
        item["id"] = "search-1"
    events = []
    if with_start:
        events.extend(parser.feed_json({"type": "item.started", "item": item}))
    events.extend(parser.feed_json({"type": "item.completed", "item": item}))
    assert isinstance(events[0], ToolStarted)
    assert isinstance(events[-1], ToolCompleted)
    assert [type(event) for event in events] == [ToolStarted, ToolCompleted]
    assert events[0].entity_id == ("search-1" if with_id else None)
    assert events[-1].entity_id == ("search-1" if with_id else None)


@pytest.mark.parametrize("with_id", [False, True])
def test_consecutive_completed_only_tools_are_each_paired(with_id: bool, tmp_path: Path) -> None:
    from coding_agent_cli.events import ToolCompleted, ToolStarted

    parser = CodexDriver().parser(_req(tmp_path))
    events = []
    for index in range(2):
        item = {"type": "file_change", "status": "completed", "changes": []}
        if with_id:
            item["id"] = f"change-{index}"
        events.extend(parser.feed_json({"type": "item.completed", "item": item}))
    assert [type(event) for event in events] == [ToolStarted, ToolCompleted, ToolStarted, ToolCompleted]


def test_tool_status_error_alias_is_not_a_failure(tmp_path: Path) -> None:
    from coding_agent_cli.events import ToolCompleted

    parser = CodexDriver().parser(_req(tmp_path))
    item = {"id": "call-1", "type": "mcp_tool_call", "tool": "fetch", "status": "error"}
    events = list(parser.feed_json({"type": "item.completed", "item": item}))
    assert isinstance(events[-1], ToolCompleted)
    assert events[-1].status == "unknown"


def test_empty_agent_message_emits_empty_delta(tmp_path: Path) -> None:
    from coding_agent_cli.events import MessageCompleted, MessageStarted, TextDelta

    parser = CodexDriver().parser(_req(tmp_path))
    assert list(parser.feed_json({"type": "item.completed", "item": {"type": "agent_message", "text": ""}})) == [
        MessageStarted(entity_id=None),
        TextDelta(text="", entity_id=None),
        MessageCompleted(entity_id=None),
    ]


def test_specific_codex_error_survives_later_generic_and_success_clears(tmp_path: Path) -> None:
    parser = CodexDriver().parser(_req(tmp_path))
    list(parser.feed_json({"type": "error", "message": "401 Unauthorized: missing authentication"}))
    list(parser.feed_json({"type": "turn.failed", "error": {"message": "stream closed"}}))
    facts = parser.finish({})
    assert facts.error is not None
    assert facts.error.code == ErrorCode.AUTH
    list(parser.feed_json({"type": "turn.completed"}))
    assert parser.finish({}).error is None


def test_command_execution_without_integer_exit_code_is_unknown(tmp_path: Path) -> None:
    from coding_agent_cli.events import ToolCompleted

    parser = CodexDriver().parser(_req(tmp_path))
    events = list(
        parser.feed_json({"type": "item.completed", "item": {"type": "command_execution", "status": "completed"}})
    )
    assert events[-1] == ToolCompleted(tool="command_execution", status="unknown", output=None, entity_id=None)


def test_turn_failed_auth_produces_agent_fatal(tmp_path: Path) -> None:
    from coding_agent_cli._classify import RunFacts, classify
    from coding_agent_cli.types import Outcome

    parser = CodexDriver().parser(_req(tmp_path))
    list(parser.feed_json({"type": "error", "message": "Reconnecting... 1/5"}))
    list(
        parser.feed_json(
            {
                "type": "turn.failed",
                "error": {"message": "unexpected status 401 Unauthorized: Missing bearer or basic authentication"},
            }
        )
    )
    facts = parser.finish({})
    assert facts.error is not None
    assert facts.error.code == ErrorCode.AUTH
    result = classify(RunFacts(returncode=1), facts, text="")
    assert result.outcome == Outcome.AGENT_FATAL
    assert result.error is not None
    assert result.error.code == ErrorCode.AUTH
    assert result.model is None


def test_parser_unstructured_stdout_goes_to_error_text(tmp_path: Path) -> None:
    parser = CodexDriver().parser(_req(tmp_path))
    assert list(parser.feed_text("fatal panic on stdout", stream="stdout")) == []
    facts = parser.finish({})
    assert facts.error_text == "fatal panic on stdout"


def _codex_scenarios() -> list[str]:
    directory = Path(__file__).parent / "fixtures" / "recorded" / "codex" / "0.169.0"
    return sorted(p.name.removesuffix(".meta.json") for p in directory.glob("*.meta.json"))


@pytest.mark.parametrize("scenario", _codex_scenarios())
def test_recorded_codex_replay_and_truncation(tmp_path: Path, scenario: str) -> None:
    import json

    from coding_agent_cli.events import RunCompleted, TextDelta
    from coding_agent_cli.types import Outcome
    from tests.replay import FIXTURES, replay, replay_truncated

    meta = json.loads((FIXTURES / "codex" / "0.169.0" / f"{scenario}.meta.json").read_text(encoding="utf-8"))
    events, result = replay(CodexDriver().parser(_req(tmp_path)), "codex", "0.169.0", scenario)

    assert result.outcome == Outcome(meta["outcome"])
    actual_code = result.error.code.value if result.error is not None else None
    assert actual_code == meta["code"]
    assert result.model is None
    assert isinstance(events[-1], RunCompleted)
    assert [e.sequence for e in events] == list(range(1, len(events) + 1))

    text_deltas = [e for e in events if isinstance(e, TextDelta)]
    for index, delta in enumerate(text_deltas, 1):
        _, truncated = replay_truncated(
            CodexDriver().parser(_req(tmp_path)),
            "codex",
            "0.169.0",
            scenario,
            after_text_deltas=index,
        )
        assert truncated.text == delta.text
        assert truncated.model is None


async def test_codex_driver_run_end_to_end(tmp_path: Path) -> None:
    import asyncio
    import sys

    from coding_agent_cli.events import Event, RunCompleted, TextDelta
    from coding_agent_cli.types import Outcome

    script = tmp_path / "fake-codex"
    script.write_text(
        "\n".join(
            [
                f"#!{sys.executable}",
                "import sys",
                "sys.stdin.read()",
                'print(\'{"type": "thread.started", "thread_id": "sess-e2e"}\', flush=True)',
                'print(\'{"type": "item.completed", "item": '
                '{"id": "m1", "type": "agent_message", "text": "ok"}}\', flush=True)',
                'print(\'{"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 2}}\', flush=True)',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    script.chmod(0o755)

    seen: list[Event] = []
    async with asyncio.timeout(1.5):
        result = await CodexDriver().run(
            _req(tmp_path, executable=str(script)),
            on_event=seen.append,
        )

    assert result.outcome == Outcome.SUCCESS
    assert result.text == "ok"
    assert result.session_id == "sess-e2e"
    assert result.model is None
    assert any(isinstance(e, TextDelta) and e.text == "ok" for e in seen)
    assert isinstance(seen[-1], RunCompleted)
