from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from coding_agent_cli import (
    ErrorCode,
    MessageCompleted,
    MessageStarted,
    Outcome,
    Request,
    RequestError,
    RunCompleted,
    SessionIdentified,
    TextDelta,
    ToolCompleted,
    ToolStarted,
    UsageUpdated,
)
from coding_agent_cli._drivers.agy import PROMPT_LIMIT_BYTES, AgyDriver, AgyParser
from tests.replay import FIXTURES, replay, replay_truncated


def _req(tmp_path: Path, **overrides: object) -> Request:
    defaults: dict[str, object] = {
        "driver": "agy",
        "prompt": "hello",
        "cwd": tmp_path,
    }
    defaults.update(overrides)
    return Request(**defaults)  # type: ignore[arg-type]


def test_launch_new_project_and_log_file_read_back(tmp_path: Path) -> None:
    driver = AgyDriver()
    run_tmp = tmp_path / "run_tmp"
    run_tmp.mkdir()
    launch = driver.launch(_req(tmp_path, model="gemini-3.8-flash-low", reasoning="low"), Path("/usr/bin/agy"), run_tmp)

    assert launch.stdin is None
    assert len(launch.read_back) == 1
    log_path = launch.read_back[0]
    assert log_path.parent == run_tmp
    assert "--output-format" in launch.argv
    assert "stream-json" in launch.argv
    assert "--log-file" in launch.argv
    assert str(log_path) in launch.argv
    assert "--new-project" in launch.argv
    assert "--conversation" not in launch.argv
    assert "--model" in launch.argv
    assert "gemini-3.8-flash-low" in launch.argv
    assert "--effort" in launch.argv
    assert "low" in launch.argv
    assert launch.argv[-2:] == ("--print", "hello")


def test_launch_resume_uses_conversation_flag(tmp_path: Path) -> None:
    driver = AgyDriver()
    run_tmp = tmp_path / "run_tmp"
    run_tmp.mkdir()
    launch = driver.launch(_req(tmp_path, session_id="conv-123"), Path("/usr/bin/agy"), run_tmp)

    assert "--conversation" in launch.argv
    idx = launch.argv.index("--conversation")
    assert launch.argv[idx + 1] == "conv-123"
    assert "--new-project" not in launch.argv


def test_launch_never_adds_permission_flags(tmp_path: Path) -> None:
    driver = AgyDriver()
    run_tmp = tmp_path / "run_tmp"
    run_tmp.mkdir()
    for kwargs in (
        {},
        {"session_id": "s1"},
        {"model": "m1", "reasoning": "high"},
    ):
        launch = driver.launch(_req(tmp_path, **kwargs), Path("/usr/bin/agy"), run_tmp)
        assert "--dangerously-skip-permissions" not in launch.argv
        assert not any("skip-permissions" in arg or "approve" in arg for arg in launch.argv)


def test_launch_rejects_oversized_and_invalid_prompt(tmp_path: Path) -> None:
    driver = AgyDriver()
    run_tmp = tmp_path / "run_tmp"
    run_tmp.mkdir()

    with pytest.raises(RequestError) as exc_info:
        driver.launch(_req(tmp_path, prompt="a" * (PROMPT_LIMIT_BYTES + 1)), Path("/usr/bin/agy"), run_tmp)
    assert exc_info.value.code == ErrorCode.INVALID_PROMPT

    with pytest.raises(RequestError) as exc_info:
        driver.launch(_req(tmp_path, prompt="bad\x00prompt"), Path("/usr/bin/agy"), run_tmp)
    assert exc_info.value.code == ErrorCode.INVALID_PROMPT

    with pytest.raises(RequestError) as exc_info:
        driver.launch(_req(tmp_path, prompt="\ud800"), Path("/usr/bin/agy"), run_tmp)
    assert exc_info.value.code == ErrorCode.INVALID_PROMPT


def test_launch_rejects_unsupported_system_prompt(tmp_path: Path) -> None:
    driver = AgyDriver()
    run_tmp = tmp_path / "run_tmp"
    run_tmp.mkdir()
    with pytest.raises(RequestError) as exc_info:
        driver.launch(_req(tmp_path, system_prompt="be concise"), Path("/usr/bin/agy"), run_tmp)
    assert exc_info.value.code == ErrorCode.UNSUPPORTED_OPTION


def test_launch_rejects_owned_and_conflicting_flags(tmp_path: Path) -> None:
    driver = AgyDriver()
    run_tmp = tmp_path / "run_tmp"
    run_tmp.mkdir()
    for flag in ("--output-format", "--input-format", "--log-file", "--conversation", "--new-project", "--print", "-p"):
        with pytest.raises(RequestError) as exc_info:
            driver.launch(_req(tmp_path, args=(flag, "val")), Path("/usr/bin/agy"), run_tmp)
        assert exc_info.value.code == ErrorCode.INVALID_ARGS

    with pytest.raises(RequestError) as exc_info:
        driver.launch(_req(tmp_path, model="m1", args=("--model", "m2")), Path("/usr/bin/agy"), run_tmp)
    assert exc_info.value.code == ErrorCode.INVALID_ARGS

    with pytest.raises(RequestError) as exc_info:
        driver.launch(_req(tmp_path, reasoning="low", args=("--effort=high",)), Path("/usr/bin/agy"), run_tmp)
    assert exc_info.value.code == ErrorCode.INVALID_ARGS


async def test_run_returns_session_id_from_log_file_read_back(tmp_path: Path) -> None:
    script = tmp_path / "fake_agy.py"
    script.write_text(
        """
import json, sys
from pathlib import Path
args = sys.argv[1:]
log_path = Path(args[args.index("--log-file") + 1])
log_path.write_text("Created conversation 11111111-2222-4333-8444-555555555555\\n", encoding="utf-8")
u1 = {"step_index": 1, "state": "ACTIVE", "step_type": "agent_response", "text_delta": "ok"}
u2 = {"step_index": 1, "state": "DONE", "step_type": "agent_response"}
print(json.dumps({"event": "step_update", "step_update": u1}))
print(json.dumps({"event": "step_update", "step_update": u2}))
""",
        encoding="utf-8",
    )

    class _FakeExeAgy(AgyDriver):
        def launch(self, req: Request, exe: Path, tmp: Path):
            base = super().launch(req, exe, tmp)
            return type(base)(
                argv=(sys.executable, str(script), *base.argv[1:]),
                stdin=base.stdin,
                read_back=base.read_back,
            )

    driver = _FakeExeAgy()
    async with asyncio.timeout(1.5):
        result = await driver.run(_req(tmp_path, executable=sys.executable))
    assert result.outcome == Outcome.SUCCESS
    assert result.session_id == "11111111-2222-4333-8444-555555555555"
    assert result.text == "ok"


async def test_plain_text_full_run_preserves_leading_blank_and_completes_message(tmp_path: Path) -> None:
    script = tmp_path / "fake_agy.py"
    script.write_text("print('')\nprint('plain answer')\n", encoding="utf-8")

    class _FakeExeAgy(AgyDriver):
        def launch(self, req: Request, exe: Path, tmp: Path):
            base = super().launch(req, exe, tmp)
            return type(base)(argv=(sys.executable, str(script)), read_back=base.read_back)

    events = []
    async with asyncio.timeout(1.5):
        result = await _FakeExeAgy().run(_req(tmp_path, executable=sys.executable), on_event=events.append)
    completions = [event for event in events if isinstance(event, MessageCompleted)]
    assert result.text == "\nplain answer"
    assert len(completions) == 1
    assert isinstance(events[-2], MessageCompleted)
    assert isinstance(events[-1], RunCompleted)


def test_parser_overlapping_tools_correlated_by_step_index() -> None:
    parser = AgyParser()
    events = []
    events.extend(
        parser.feed_json(
            {
                "event": "step_update",
                "step_update": {
                    "step_index": 2,
                    "state": "ACTIVE",
                    "step_type": "tool",
                    "tool_name": "run_command",
                    "tool_info": {"name": "run_command", "parameters": {"CommandLine": "echo hi"}},
                },
            }
        )
    )
    events.extend(
        parser.feed_json(
            {
                "event": "step_update",
                "step_update": {
                    "step_index": 4,
                    "state": "ACTIVE",
                    "step_type": "tool",
                    "tool_name": "read_file",
                    "tool_info": {"name": "read_file", "parameters": {"Path": "a.txt"}},
                },
            }
        )
    )
    # Step 4 finishes before step 2.
    events.extend(
        parser.feed_json(
            {
                "event": "step_update",
                "step_update": {
                    "step_index": 4,
                    "state": "DONE",
                    "step_type": "tool",
                    "tool_info": {"output": "file contents"},
                },
            }
        )
    )
    events.extend(
        parser.feed_json(
            {
                "event": "step_update",
                "step_update": {
                    "step_index": 2,
                    "state": "DONE",
                    "step_type": "tool",
                    "tool_info": {"output": "hi\n"},
                },
            }
        )
    )

    started = [e for e in events if isinstance(e, ToolStarted)]
    completed = [e for e in events if isinstance(e, ToolCompleted)]
    assert len(started) == 2
    assert len(completed) == 2

    assert started[0].tool == "run_command"
    assert started[0].activity == "command"
    assert started[0].entity_id is None
    assert started[1].tool == "read_file"
    assert started[1].activity == "tool_call"
    assert started[1].entity_id is None

    assert completed[0].tool == "read_file"
    assert completed[0].entity_id is None
    assert completed[0].status == "completed"
    assert completed[0].output == "file contents"

    assert completed[1].tool == "run_command"
    assert completed[1].entity_id is None
    assert completed[1].status == "completed"
    assert completed[1].output == "hi\n"


def test_parser_unevidenced_tool_error_field_yields_unknown_status() -> None:
    parser = AgyParser()
    parser.feed_json(
        {
            "event": "step_update",
            "step_update": {
                "step_index": 2,
                "state": "ACTIVE",
                "step_type": "tool",
                "tool_name": "run_command",
                "tool_info": {"name": "run_command", "parameters": {"CommandLine": "false"}},
            },
        }
    )
    done = list(
        parser.feed_json(
            {
                "event": "step_update",
                "step_update": {
                    "step_index": 2,
                    "state": "DONE",
                    "step_type": "tool",
                    "tool_name": "run_command",
                    "tool_info": {"name": "run_command", "output": "boom", "error": "permission denied"},
                },
            }
        )
    )
    completed = [e for e in done if isinstance(e, ToolCompleted)]
    assert len(completed) == 1
    assert completed[0].tool == "run_command"
    assert completed[0].entity_id is None
    assert completed[0].status == "unknown"
    assert completed[0].output == "boom"


def test_parser_non_done_terminal_tool_state_yields_unknown() -> None:
    parser = AgyParser()
    for index in (3, 4):
        parser.feed_json(
            {
                "event": "step_update",
                "step_update": {
                    "step_index": index,
                    "state": "ACTIVE",
                    "step_type": "tool",
                    "tool_name": f"tool_{index}",
                    "tool_info": {"name": f"tool_{index}"},
                },
            }
        )
    done = list(
        parser.feed_json(
            {
                "event": "step_update",
                "step_update": {"step_index": 3, "state": "ERROR", "step_type": "tool", "tool_info": {}},
            }
        )
    )
    completed = [e for e in done if isinstance(e, ToolCompleted)]
    assert [(c.tool, c.status) for c in completed] == [("tool_3", "unknown")]
    later = list(
        parser.feed_json(
            {
                "event": "step_update",
                "step_update": {"step_index": 4, "state": "DONE", "step_type": "tool", "tool_info": {}},
            }
        )
    )
    assert [(c.tool, c.status) for c in later if isinstance(c, ToolCompleted)] == [("tool_4", "completed")]


def test_parser_plain_text_fallback_through_feed_text() -> None:
    parser = AgyParser()
    # Stdout fallback is model text, even if it resembles CLI log or error text.
    e1 = list(parser.feed_text("Created conversation 11111111-2222-4333-8444-555555555555", stream="stdout"))
    e2 = list(parser.feed_text("", stream="stdout"))
    e3 = list(parser.feed_text("invalid model", stream="stdout"))
    e4 = list(parser.feed_text("warning on stderr", stream="stderr"))
    e5 = list(parser.feed_text("Streaming conversation 22222222-3333-4444-8555-666666666666", stream="stderr"))

    assert not any(isinstance(e, SessionIdentified) for e in e1)
    assert any(isinstance(e, MessageStarted) and e.entity_id is None for e in e1)
    assert any(
        isinstance(e, TextDelta) and e.text == "Created conversation 11111111-2222-4333-8444-555555555555" for e in e1
    )
    assert any(isinstance(e, TextDelta) and e.text == "\n" for e in e2)
    assert any(isinstance(e, TextDelta) and e.text == "\ninvalid model" for e in e3)
    assert not any(isinstance(e, TextDelta) for e in (*e4, *e5))
    assert not any(isinstance(e, SessionIdentified) for e in e5)

    facts = parser.finish({})
    assert facts.session_id is None
    assert "warning on stderr" in facts.error_text
    assert "invalid model" not in facts.error_text
    assert "Created conversation" not in facts.error_text


def test_parser_plain_text_invalid_model_zero_exit_is_success() -> None:
    from coding_agent_cli._classify import RunFacts, classify

    parser = AgyParser()
    events = list(parser.feed_text("invalid model", stream="stdout"))
    facts = parser.finish({})
    text = "".join(e.text for e in events if isinstance(e, TextDelta))
    result = classify(RunFacts(stop_cause="exited", returncode=0), facts, text=text)
    assert result.outcome == Outcome.SUCCESS
    assert result.error is None
    assert result.text == "invalid model"


def test_parser_structured_status_and_usage() -> None:
    parser = AgyParser()
    init_events = list(
        parser.feed_json(
            {
                "event": "init",
                "conversation_id": "00000000-0000-4000-8000-000000000001",
                "init": {"model": "gemini-3.8-flash-low"},
            }
        )
    )
    assert not any(isinstance(e, SessionIdentified) for e in init_events)

    step_events = list(
        parser.feed_json(
            {
                "event": "step_update",
                "step_update": {
                    "step_index": 1,
                    "state": "DONE",
                    "step_type": "agent_response",
                    "text_delta": "hi",
                    "usage": {
                        "input_tokens": 100,
                        "output_tokens": 20,
                        "cache_read_tokens": 30,
                    },
                },
            }
        )
    )
    msg_events = [e for e in step_events if isinstance(e, (MessageStarted, TextDelta, MessageCompleted))]
    assert all(e.entity_id is None for e in msg_events)

    usage_events = [e for e in step_events if isinstance(e, UsageUpdated)]
    assert len(usage_events) == 1
    assert usage_events[0].usage.input == 100
    assert usage_events[0].usage.output == 20
    assert usage_events[0].usage.cache_read == 30
    assert usage_events[0].usage.cache_write is None
    assert usage_events[0].usage.context_used is None
    assert usage_events[0].usage.context_window is None

    parser.feed_json(
        {
            "event": "result",
            "result": {
                "conversation_id": "00000000-0000-4000-8000-000000000001",
                "status": "ERROR",
                "error": "stream input missing event",
                "finished_reason": "error",
                "num_turns": 1,
                "usage": {"input_tokens": 100, "output_tokens": 20, "cache_read_tokens": 30},
            },
        }
    )
    facts = parser.finish({})
    assert facts.model == "gemini-3.8-flash-low"
    assert facts.num_turns is None
    assert facts.stop_reason == "error"
    assert facts.error is not None
    assert facts.error.code == ErrorCode.EXECUTION_ERROR
    assert facts.usage.input == 100
    assert facts.usage.output == 20
    assert facts.usage.cache_read == 30


def test_parser_unknown_result_status_is_not_treated_as_error() -> None:
    from coding_agent_cli._classify import RunFacts, classify

    parser = AgyParser()
    parser.feed_json(
        {
            "event": "result",
            "result": {
                "conversation_id": "00000000-0000-4000-8000-000000000001",
                "status": "PARTIAL",
                "response": "partial answer",
            },
        }
    )
    facts = parser.finish({})
    assert facts.error is None
    result = classify(RunFacts(stop_cause="exited", returncode=0), facts, text="partial answer")
    assert result.outcome == Outcome.SUCCESS
    assert result.error is None


def test_replay_recorded_nonzero_cache_usage() -> None:
    _, result = replay(AgyParser(), "agy", _AGY_VERSION, "shell")
    assert result.usage.input == 12160
    assert result.usage.output == 107
    assert result.usage.cache_read == 7950
    assert result.usage.cache_write is None
    assert result.usage.context_used is None
    assert result.usage.context_window is None


_AGY_VERSION = "1.5.9"
_AGY_DIR = FIXTURES / "agy" / _AGY_VERSION
_AGY_SCENARIOS = sorted(p.name.removesuffix(".meta.json") for p in _AGY_DIR.glob("*.meta.json"))


@pytest.mark.parametrize("scenario", _AGY_SCENARIOS)
def test_replay_recorded_agy_scenarios(scenario: str) -> None:
    meta = json.loads((_AGY_DIR / f"{scenario}.meta.json").read_text(encoding="utf-8"))
    events, result = replay(AgyParser(), "agy", _AGY_VERSION, scenario)

    assert result.outcome.value == meta["outcome"]
    actual_code = result.error.code.value if result.error is not None else None
    assert actual_code == meta["code"]

    if meta["outcome"] == "success":
        started_tools = [e for e in events if isinstance(e, ToolStarted)]
        completed_tools = [e for e in events if isinstance(e, ToolCompleted)]
        assert len(started_tools) == len(completed_tools)
        assert [(e.entity_id, e.tool) for e in started_tools] == [(e.entity_id, e.tool) for e in completed_tools]

        started_msgs = [e for e in events if isinstance(e, MessageStarted)]
        completed_msgs = [e for e in events if isinstance(e, MessageCompleted)]
        assert len(started_msgs) == len(completed_msgs)


@pytest.mark.parametrize("scenario", _AGY_SCENARIOS)
def test_replay_truncation_keeps_partial_text(scenario: str) -> None:
    events, _ = replay(AgyParser(), "agy", _AGY_VERSION, scenario)
    delta_count = sum(1 for e in events if isinstance(e, TextDelta))
    for n in range(1, delta_count + 1):
        _, truncated = replay_truncated(AgyParser(), "agy", _AGY_VERSION, scenario, after_text_deltas=n)
        assert truncated.text != ""
