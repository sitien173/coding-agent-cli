from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from coding_agent_cli._classify import RunFacts, classify
from coding_agent_cli._drivers.claude import ClaudeDriver
from coding_agent_cli.driver import RequestError
from coding_agent_cli.events import (
    FileChanged,
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
from tests.launchers import install_launcher
from tests.replay import FIXTURES, replay, replay_truncated

_PERMISSION_FLAGS = (
    "--dangerously-skip-permissions",
    "--permission-mode",
    "--allowedTools",
    "--allowed-tools",
    "--disallowedTools",
    "--disallowed-tools",
)


def _make_request(tmp_path: Path, **overrides: object) -> Request:
    defaults: dict[str, object] = {
        "driver": "claude",
        "prompt": "hello",
        "cwd": tmp_path,
    }
    defaults.update(overrides)
    return Request(**defaults)  # type: ignore[arg-type]


def test_launch_includes_verbose_and_stdin_prompt(tmp_path: Path) -> None:
    driver = ClaudeDriver()
    req = _make_request(tmp_path, prompt="ping")
    launch = driver.launch(req, Path("/usr/bin/claude"), tmp_path)

    assert launch.argv == (
        str(Path("/usr/bin/claude")),
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
        "--include-partial-messages",
    )
    assert launch.stdin == b"ping"
    assert "ping" not in launch.argv


def test_launch_portable_flags_and_resume(tmp_path: Path) -> None:
    driver = ClaudeDriver()
    req = _make_request(
        tmp_path,
        session_id="sess-123",
        model="haiku",
        reasoning="low",
        system_prompt="be brief",
        args=("--max-turns", "2"),
    )
    launch = driver.launch(req, Path("/usr/bin/claude"), tmp_path)

    assert launch.argv == (
        str(Path("/usr/bin/claude")),
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
        "--include-partial-messages",
        "--resume",
        "sess-123",
        "--model",
        "haiku",
        "--effort",
        "low",
        "--system-prompt",
        "be brief",
        "--max-turns",
        "2",
    )


@pytest.mark.parametrize(
    "owned_arg",
    [
        "-p",
        "--print",
        "--output-format",
        "--output-format=text",
        "--verbose",
        "--include-partial-messages",
        "--resume",
        "--resume=abc",
    ],
)
def test_launch_rejects_owned_transport_flags(tmp_path: Path, owned_arg: str) -> None:
    driver = ClaudeDriver()
    req = _make_request(tmp_path, args=(owned_arg,))

    with pytest.raises(RequestError) as exc_info:
        driver.launch(req, Path("/usr/bin/claude"), tmp_path)

    assert exc_info.value.code == ErrorCode.INVALID_ARGS


@pytest.mark.parametrize(
    ("field", "value", "flag"),
    [
        ("model", "haiku", "--model"),
        ("model", "haiku", "--model=sonnet"),
        ("reasoning", "low", "--effort"),
        ("reasoning", "low", "--effort=high"),
        ("system_prompt", "sys", "--system-prompt"),
        ("system_prompt", "sys", "--system-prompt=other"),
    ],
)
def test_launch_rejects_portable_flag_conflicts(tmp_path: Path, field: str, value: str, flag: str) -> None:
    driver = ClaudeDriver()
    req = _make_request(tmp_path, **{field: value, "args": (flag,)})

    with pytest.raises(RequestError) as exc_info:
        driver.launch(req, Path("/usr/bin/claude"), tmp_path)

    assert exc_info.value.code == ErrorCode.INVALID_ARGS


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"session_id": "s1", "model": "haiku", "reasoning": "high", "system_prompt": "sys"},
        {"args": ("--max-turns", "3")},
    ],
)
def test_launch_never_adds_permission_flags(tmp_path: Path, kwargs: dict[str, object]) -> None:
    driver = ClaudeDriver()
    req = _make_request(tmp_path, **kwargs)
    launch = driver.launch(req, Path("/usr/bin/claude"), tmp_path)

    for token in launch.argv:
        flag = token.split("=", 1)[0]
        assert flag not in _PERMISSION_FLAGS


def test_parser_messages_and_text_dedup(tmp_path: Path) -> None:
    parser = ClaudeDriver().parser(_make_request(tmp_path))
    events = []
    records = [
        {"type": "system", "subtype": "init", "session_id": "s-1", "model": "claude-haiku-5-5"},
        {
            "type": "stream_event",
            "session_id": "s-1",
            "event": {"type": "message_start", "message": {"id": "msg-1", "model": "claude-haiku-5-5"}},
        },
        {
            "type": "stream_event",
            "session_id": "s-1",
            "event": {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        },
        {
            "type": "stream_event",
            "session_id": "s-1",
            "event": {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hel"}},
        },
        {
            "type": "stream_event",
            "session_id": "s-1",
            "event": {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "lo"}},
        },
        {
            "type": "assistant",
            "session_id": "s-1",
            "message": {"id": "msg-1", "model": "claude-haiku-5-5", "content": [{"type": "text", "text": "Hello"}]},
        },
        {"type": "stream_event", "session_id": "s-1", "event": {"type": "message_stop"}},
    ]
    for record in records:
        events.extend(parser.feed_json(record))

    assert [type(e) for e in events] == [
        SessionIdentified,
        MessageStarted,
        TextDelta,
        TextDelta,
        MessageCompleted,
    ]
    text_events = [e for e in events if isinstance(e, TextDelta)]
    assert "".join(e.text for e in text_events) == "Hello"
    assert all(e.entity_id == "msg-1" for e in events[1:])

    # Unstreamed assistant message still emits MessageStarted, TextDelta, MessageCompleted.
    unstreamed = list(
        parser.feed_json(
            {
                "type": "assistant",
                "session_id": "s-1",
                "message": {"id": "msg-2", "content": [{"type": "text", "text": "World"}]},
            }
        )
    )
    assert [type(e) for e in unstreamed] == [MessageStarted, TextDelta, MessageCompleted]
    assert isinstance(unstreamed[1], TextDelta) and unstreamed[1].text == "World"


def test_parser_reasoning_deltas_and_dedup(tmp_path: Path) -> None:
    parser = ClaudeDriver().parser(_make_request(tmp_path))
    events = []
    for record in [
        {
            "type": "stream_event",
            "event": {"type": "message_start", "message": {"id": "msg-r1"}},
        },
        {
            "type": "stream_event",
            "event": {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "thinking_delta", "thinking": "Step 1"},
            },
        },
        {
            "type": "assistant",
            "message": {"id": "msg-r1", "content": [{"type": "thinking", "thinking": "Step 1"}]},
        },
        {"type": "stream_event", "event": {"type": "message_stop"}},
    ]:
        events.extend(parser.feed_json(record))

    reasoning = [e for e in events if isinstance(e, ReasoningDelta)]
    assert len(reasoning) == 1
    assert reasoning[0].text == "Step 1"
    assert reasoning[0].entity_id == "msg-r1"


def test_parser_bash_as_command_and_tool_result(tmp_path: Path) -> None:
    parser = ClaudeDriver().parser(_make_request(tmp_path))
    events = []
    for record in [
        {"type": "stream_event", "event": {"type": "message_start", "message": {"id": "msg-t1"}}},
        {
            "type": "stream_event",
            "event": {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "tool_use", "id": "tu-1", "name": "Bash", "input": {}},
            },
        },
        {
            "type": "assistant",
            "message": {
                "id": "msg-t1",
                "content": [{"type": "tool_use", "id": "tu-1", "name": "Bash", "input": {"command": "ls"}}],
            },
        },
        {"type": "stream_event", "event": {"type": "message_stop"}},
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "tu-1", "content": "a.py\n", "is_error": False}],
            },
        },
        {
            "type": "assistant",
            "message": {
                "id": "msg-t2",
                "content": [{"type": "tool_use", "id": "tu-2", "name": "Bash", "input": {"command": "false"}}],
            },
        },
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "tu-2", "content": "boom", "is_error": True}],
            },
        },
    ]:
        events.extend(parser.feed_json(record))

    started = [e for e in events if isinstance(e, ToolStarted)]
    completed = [e for e in events if isinstance(e, ToolCompleted)]
    assert started == [
        ToolStarted(tool="Bash", activity="command", input={"command": "ls"}, entity_id="tu-1"),
        ToolStarted(tool="Bash", activity="command", input={"command": "false"}, entity_id="tu-2"),
    ]
    assert completed == [
        ToolCompleted(tool="Bash", status="completed", output="a.py\n", entity_id="tu-1"),
        ToolCompleted(tool="Bash", status="error", output="boom", entity_id="tu-2"),
    ]


@pytest.mark.parametrize(
    ("tool_name", "tool_input", "is_error", "expected_path"),
    [
        ("Edit", {"file_path": "/work/a.py"}, False, "/work/a.py"),
        ("Write", {"file_path": "/work/b.py"}, False, "/work/b.py"),
        ("MultiEdit", {"file_path": "/work/c.py"}, False, "/work/c.py"),
        ("NotebookEdit", {"notebook_path": "/work/d.ipynb"}, False, "/work/d.ipynb"),
        ("Edit", {"file_path": "/work/e.py"}, True, None),
        ("Read", {"file_path": "/work/f.py"}, False, None),
    ],
)
def test_parser_inferred_file_changed(
    tmp_path: Path,
    tool_name: str,
    tool_input: dict[str, str],
    is_error: bool,
    expected_path: str | None,
) -> None:
    parser = ClaudeDriver().parser(_make_request(tmp_path))
    events = []
    events.extend(
        parser.feed_json(
            {
                "type": "assistant",
                "message": {
                    "id": "msg-f1",
                    "content": [{"type": "tool_use", "id": "tu-f1", "name": tool_name, "input": tool_input}],
                },
            }
        )
    )
    events.extend(
        parser.feed_json(
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "tu-f1", "content": "ok", "is_error": is_error}],
                },
            }
        )
    )

    file_events = [e for e in events if isinstance(e, FileChanged)]
    if expected_path is None:
        assert file_events == []
    else:
        assert len(file_events) == 1
        assert file_events[0].path == expected_path
        assert file_events[0].evidence == "inferred"
        assert file_events[0].entity_id == "tu-f1"


def test_parser_usage_snapshots(tmp_path: Path) -> None:
    parser = ClaudeDriver().parser(_make_request(tmp_path))
    events = []
    events.extend(
        parser.feed_json(
            {
                "type": "stream_event",
                "event": {
                    "type": "message_start",
                    "message": {
                        "id": "msg-u1",
                        "usage": {
                            "input_tokens": 100,
                            "cache_creation_input_tokens": 200,
                            "cache_read_input_tokens": 50,
                            "output_tokens": 10,
                        },
                    },
                },
            }
        )
    )
    events.extend(
        parser.feed_json(
            {
                "type": "stream_event",
                "event": {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn"},
                    "usage": {
                        "input_tokens": 100,
                        "cache_creation_input_tokens": 200,
                        "cache_read_input_tokens": 50,
                        "output_tokens": 25,
                    },
                },
            }
        )
    )
    usage_events = [e for e in events if isinstance(e, UsageUpdated)]
    assert len(usage_events) == 2
    last = usage_events[-1].usage
    assert last.input == 350
    assert last.output == 25
    assert last.cache_write == 200
    assert last.cache_read == 50
    assert last.context_used == 375


def _claude_scenarios() -> list[tuple[str, str]]:
    claude_dir = FIXTURES / "claude"
    scenarios: list[tuple[str, str]] = []
    for version_dir in sorted(claude_dir.iterdir()):
        if not version_dir.is_dir():
            continue
        for meta_path in sorted(version_dir.glob("*.meta.json")):
            scenario = meta_path.name.removesuffix(".meta.json")
            scenarios.append((version_dir.name, scenario))
    return scenarios


@pytest.mark.parametrize(("version", "scenario"), _claude_scenarios())
def test_replay_recorded_claude_scenarios(tmp_path: Path, version: str, scenario: str) -> None:
    meta = json.loads((FIXTURES / "claude" / version / f"{scenario}.meta.json").read_text(encoding="utf-8"))
    parser = ClaudeDriver().parser(_make_request(tmp_path))
    events, result = replay(parser, "claude", version, scenario)

    assert result.outcome.value == meta["outcome"]
    actual_code = result.error.code.value if result.error is not None else None
    assert actual_code == meta["code"]
    assert result.returncode == meta["returncode"]

    if result.outcome == Outcome.SUCCESS:
        started_ids = {e.entity_id for e in events if isinstance(e, ToolStarted)}
        completed_ids = {e.entity_id for e in events if isinstance(e, ToolCompleted)}
        assert started_ids <= completed_ids


def test_partial_text_on_truncation(tmp_path: Path) -> None:
    version = "2.1.296"
    for scenario in ("plain", "edit", "shell", "reasoning", "resume", "cancel", "budget"):
        full_events, _ = replay(ClaudeDriver().parser(_make_request(tmp_path)), "claude", version, scenario)
        total_deltas = sum(1 for e in full_events if isinstance(e, TextDelta))
        assert total_deltas >= 1
        for cut in range(1, total_deltas + 1):
            events, result = replay_truncated(
                ClaudeDriver().parser(_make_request(tmp_path)),
                "claude",
                version,
                scenario,
                after_text_deltas=cut,
            )
            emitted_text = "".join(e.text for e in events if isinstance(e, TextDelta))
            assert emitted_text != ""
            assert result.text == emitted_text


def test_error_max_turns_as_limit_reached(tmp_path: Path) -> None:
    parser = ClaudeDriver().parser(_make_request(tmp_path))
    _, result = replay(parser, "claude", "2.1.296", "max_turns")

    assert result.outcome == Outcome.LIMIT_REACHED
    assert result.error is not None
    assert result.error.code == ErrorCode.MAX_TURNS


def test_facts_from_result_record(tmp_path: Path) -> None:
    parser = ClaudeDriver().parser(_make_request(tmp_path))
    _, result = replay(parser, "claude", "2.1.296", "edit")

    assert result.outcome == Outcome.SUCCESS
    assert result.session_id == "00000000-0000-4000-8000-000000000030"
    assert result.model == "claude-haiku-5-5"
    assert result.cost_usd == pytest.approx(0.00218717)
    assert result.num_turns == 3
    assert result.stop_reason == "end_turn"
    assert result.usage.context_window == 200000
    assert result.usage.output == 605


@pytest.mark.parametrize(
    ("status", "expected_outcome", "expected_code"),
    [
        (401, Outcome.AGENT_FATAL, ErrorCode.AUTH),
        (403, Outcome.AGENT_FATAL, ErrorCode.AUTH),
        (429, Outcome.RETRYABLE, ErrorCode.SERVICE_ERROR),
        (503, Outcome.RETRYABLE, ErrorCode.SERVICE_ERROR),
    ],
)
def test_api_error_status_mapping(
    tmp_path: Path,
    status: int,
    expected_outcome: Outcome,
    expected_code: ErrorCode,
) -> None:
    parser = ClaudeDriver().parser(_make_request(tmp_path))
    parser.feed_json(
        {
            "type": "result",
            "is_error": True,
            "subtype": "success",
            "api_error_status": status,
            "result": f"API error {status}",
        }
    )
    facts = parser.finish({})
    result = classify(RunFacts(returncode=1), facts, text="")

    assert result.outcome == expected_outcome
    assert result.error is not None
    assert result.error.code == expected_code


async def test_claude_driver_end_to_end_with_fake_cli(tmp_path: Path) -> None:
    script = tmp_path / "fake_claude.py"
    lines = [
        {"type": "system", "subtype": "init", "session_id": "s-e2e", "model": "haiku"},
        {"type": "stream_event", "event": {"type": "message_start", "message": {"id": "m1"}}},
        {
            "type": "stream_event",
            "event": {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "OK"}},
        },
        {"type": "stream_event", "event": {"type": "message_stop"}},
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "total_cost_usd": 0.01,
            "num_turns": 1,
            "stop_reason": "end_turn",
            "result": "OK",
        },
    ]
    script.write_text(
        "import sys\n_ = sys.stdin.read()\n" + "\n".join(f"print({json.dumps(item)!r})" for item in lines) + "\n",
        encoding="utf-8",
    )
    wrapper = install_launcher(tmp_path, "claude", script)

    req = _make_request(tmp_path, executable=str(wrapper))
    async with asyncio.timeout(10):
        result = await ClaudeDriver().run(req)

    assert result.outcome == Outcome.SUCCESS
    assert result.text == "OK"
    assert result.session_id == "s-e2e"
    assert result.cost_usd == pytest.approx(0.01)
    assert result.num_turns == 1
    assert result.stop_reason == "end_turn"


def test_parser_dedups_each_text_block_in_one_message(tmp_path: Path) -> None:
    parser = ClaudeDriver().parser(_make_request(tmp_path))

    def stream(event: dict[str, object]) -> dict[str, object]:
        return {"type": "stream_event", "session_id": "s-1", "event": event}

    def assistant(text: str) -> dict[str, object]:
        return {
            "type": "assistant",
            "session_id": "s-1",
            "message": {"id": "msg-1", "content": [{"type": "text", "text": text}]},
        }

    records = [
        stream({"type": "message_start", "message": {"id": "msg-1"}}),
        stream({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "A"}}),
        assistant("A"),
        stream({"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "B"}}),
        assistant("B"),
    ]
    events = [event for record in records for event in parser.feed_json(record)]

    assert [e.text for e in events if isinstance(e, TextDelta)] == ["A", "B"]
