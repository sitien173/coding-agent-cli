from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Literal

import pytest

from coding_agent_cli import (
    CliDriver,
    DriverRegistry,
    ErrorCode,
    Facts,
    Launch,
    MessageCompleted,
    MessageStarted,
    Outcome,
    Parser,
    RawEvent,
    Request,
    RequestError,
    Result,
    RunCompleted,
    TextDelta,
    run,
)
from coding_agent_cli.events import Event

FAKE = Path(__file__).parent / "fixtures" / "fake_cli"


class _Parser:
    def __init__(self, fail: bool = False, mode: str = "") -> None:
        self.fail = fail
        self.mode = mode
        self.started = False
        self.read_back: Mapping[Path, bytes] = {}

    def feed_json(self, obj: dict[str, Any]) -> Iterable[Event]:
        if self.fail:
            raise ValueError("parser drift")
        return (MessageStarted(), TextDelta(text=obj["text"]))

    def feed_text(self, line: str, *, stream: Literal["stdout", "stderr"]) -> Iterable[Event]:
        if self.fail:
            raise ValueError("parser drift")
        if stream == "stderr":
            return ()
        if self.mode == "two_messages":
            if line == "first":
                return (MessageStarted(), TextDelta(text="first"))
            if line == "empty":
                return (MessageStarted(),)
        if self.mode == "open_message":
            return (MessageStarted(entity_id="open-1"), TextDelta(text=line))
        if self.mode == "completed_message":
            return (MessageStarted(entity_id="done-1"), TextDelta(text=line), MessageCompleted(entity_id="done-1"))
        if self.mode == "generator_failure":

            def partial_then_fail() -> Iterable[Event]:
                yield MessageStarted()
                yield TextDelta(text="partial")
                raise ValueError("iterator drift")

            return partial_then_fail()
        if self.mode == "run_completed":
            return (RunCompleted(result=Result(outcome=Outcome.SUCCESS)),)
        events: list[Event] = []
        if not self.started:
            events.append(MessageStarted())
            self.started = True
        events.append(TextDelta(text=line))
        return events

    def finish(self, read_back: Mapping[Path, bytes]) -> Facts:
        self.read_back = read_back
        return Facts(session_id="toy")


class _Driver(CliDriver):
    name = "toy"
    executable = sys.executable

    def __init__(self) -> None:
        self.tmp_paths: list[Path] = []
        self.fail_parser = False
        self.request_error = False
        self.parsers: list[_Parser] = []

    def launch(self, req: Request, exe: Path, tmp: Path) -> Launch:
        self.tmp_paths.append(tmp)
        if self.request_error:
            raise RequestError(ErrorCode.INVALID_PROMPT, "bad prompt")
        if req.env.get("TEST_MODE") == "read_back":
            path = tmp / "recorded.pid"
            self.tmp_paths.append(tmp)
            return Launch(argv=(str(exe), str(FAKE / "stubborn_descendant.py"), str(path)), read_back=(path,))
        script = req.args[0]
        return Launch(argv=(str(exe), str(FAKE / script), *req.args[1:]))

    def parser(self, req: Request) -> Parser:
        parser = _Parser(self.fail_parser, req.env.get("TEST_MODE", ""))
        self.parsers.append(parser)
        return parser


def _setup() -> tuple[DriverRegistry, _Driver]:
    driver = _Driver()
    registry = DriverRegistry(builtins=False)
    registry.register("toy", driver)
    return registry, driver


def _req(*args: str, **kwargs: Any) -> Request:
    return Request(driver="toy", prompt="hello", cwd=Path.cwd(), args=args, **kwargs)


@pytest.mark.parametrize("script,args", [("print_then_exit.py", ()), ("many_lines.py", ("2",))])
async def test_driver_success_and_temp_cleanup(script: str, args: tuple[str, ...]) -> None:
    registry, driver = _setup()
    async with asyncio.timeout(1.5):
        result = await run(_req(script, *args), registry=registry)
    assert result.outcome == Outcome.SUCCESS
    assert driver.tmp_paths and all(not path.exists() for path in driver.tmp_paths)


async def test_cancel_keeps_partial_text_and_removes_tmp() -> None:
    registry, driver = _setup()
    cancel = asyncio.Event()
    events: list[Event] = []

    async def on_event(event: Event) -> None:
        events.append(event)
        if isinstance(event, TextDelta):
            cancel.set()

    async with asyncio.timeout(1.5):
        result = await run(_req("sleeper.py"), on_event=on_event, cancel=cancel, registry=registry)
    assert result.outcome == Outcome.CANCELLED and result.text == "tick"
    assert not driver.tmp_paths[-1].exists()


async def test_timeout_and_idle_timeout() -> None:
    registry, _ = _setup()
    async with asyncio.timeout(1.5):
        wall = await run(_req("sleeper.py", timeout_s=0.15), registry=registry)
    async with asyncio.timeout(1.5):
        idle = await run(_req("slow_trickle.py", "0.5", idle_timeout_s=0.15), registry=registry)
    assert wall.error is not None and wall.error.code == ErrorCode.TIMEOUT
    assert idle.error is not None and idle.error.code == ErrorCode.IDLE_TIMEOUT


async def test_request_error_from_launch_and_parser_exception() -> None:
    registry, driver = _setup()
    driver.request_error = True
    async with asyncio.timeout(1.5):
        request_error = await run(_req("print_then_exit.py"), registry=registry)
    assert request_error.outcome == Outcome.REQUEST_FATAL
    assert not driver.tmp_paths[-1].exists()
    driver.request_error = False
    driver.fail_parser = True
    async with asyncio.timeout(1.5):
        parser_error = await run(_req("print_then_exit.py"), registry=registry)
    assert parser_error.outcome == Outcome.AGENT_FATAL
    assert parser_error.error is not None and parser_error.error.code == ErrorCode.PARSER_ERROR


async def test_raw_event_precedes_normalized_events_and_completion_is_last(tmp_path: Path) -> None:
    registry, _ = _setup()
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"lines": ['{"text":"hello"}']}), encoding="utf-8")
    events: list[Event] = []

    async def on_event(event: Event) -> None:
        events.append(event)

    async with asyncio.timeout(1.5):
        result = await run(_req("fake_cli.py", str(config), raw_events=True), on_event=on_event, registry=registry)
    assert result.text == "hello"
    assert isinstance(events[0], RawEvent)
    assert isinstance(events[-1], RunCompleted)
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))


async def test_on_event_exception_removes_temp_and_propagates() -> None:
    registry, driver = _setup()

    async def fail(event: Event) -> None:
        if isinstance(event, TextDelta):
            raise ValueError("callback")

    with pytest.raises(ValueError, match="callback"):
        async with asyncio.timeout(1.5):
            await run(_req("print_then_exit.py"), on_event=fail, registry=registry)
    assert not driver.tmp_paths[-1].exists()


async def test_future_returned_by_callback_is_awaited_and_failure_propagates() -> None:
    registry, driver = _setup()
    loop = asyncio.get_running_loop()

    def on_event(event: Event):
        if not isinstance(event, TextDelta):
            return None
        future: asyncio.Future[None] = loop.create_future()
        loop.call_later(0.02, future.set_exception, ValueError("future callback"))
        return future

    with pytest.raises(ValueError, match="future callback"):
        async with asyncio.timeout(1.5):
            await run(_req("print_then_exit.py"), on_event=on_event, registry=registry)
    assert not driver.tmp_paths[-1].exists()


async def test_empty_last_message_does_not_reuse_previous_text() -> None:
    registry, _ = _setup()
    req = _req("two_messages.py", env={"TEST_MODE": "two_messages"})
    async with asyncio.timeout(1.5):
        result = await run(req, registry=registry)
    assert result.text == ""


@pytest.mark.parametrize(
    ("mode", "expected_entity_ids"),
    [("open_message", ["open-1"]), ("completed_message", ["done-1"])],
)
async def test_open_message_is_completed_at_transport_end(mode: str, expected_entity_ids: list[str]) -> None:
    registry, _ = _setup()
    events: list[Event] = []

    async def on_event(event: Event) -> None:
        events.append(event)

    req = _req("print_then_exit.py", env={"TEST_MODE": mode})
    async with asyncio.timeout(1.5):
        await run(req, on_event=on_event, registry=registry)

    completions = [event for event in events if isinstance(event, MessageCompleted)]
    assert [event.entity_id for event in completions] == expected_entity_ids
    assert isinstance(events[-1], RunCompleted)
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))


async def test_generator_failure_keeps_already_emitted_partial_text() -> None:
    registry, _ = _setup()
    req = _req("print_then_exit.py", env={"TEST_MODE": "generator_failure"})
    async with asyncio.timeout(1.5):
        result = await run(req, registry=registry)
    assert result.outcome == Outcome.AGENT_FATAL
    assert result.error is not None and result.error.code == ErrorCode.PARSER_ERROR
    assert result.text == "partial"


async def test_stubborn_descendant_cleanup_preserves_read_back_for_finish() -> None:
    registry, driver = _setup()
    req = _req("unused.py", env={"TEST_MODE": "read_back"})
    async with asyncio.timeout(4):
        result = await run(req, registry=registry)
    assert result.outcome == Outcome.SUCCESS
    assert driver.parsers[-1].read_back
    assert next(iter(driver.parsers[-1].read_back.values())).isdigit()
    assert result.warnings == ()


async def test_parser_cannot_emit_run_completed() -> None:
    registry, _ = _setup()
    events: list[Event] = []

    async def on_event(event: Event) -> None:
        events.append(event)

    req = _req("print_then_exit.py", env={"TEST_MODE": "run_completed"})
    async with asyncio.timeout(1.5):
        result = await run(req, on_event=on_event, registry=registry)
    completions = [event for event in events if isinstance(event, RunCompleted)]
    assert len(completions) == 1 and events[-1] is completions[0]
    assert result.error is not None and result.error.code == ErrorCode.PARSER_ERROR


async def test_temp_removal_error_adds_cleanup_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    import shutil

    registry, driver = _setup()

    def fail_removal(path, ignore_errors=False, *, onexc=None):
        if onexc is not None:
            onexc(lambda: None, str(path), PermissionError("simulated removal failure"))

    monkeypatch.setattr("coding_agent_cli.driver.shutil.rmtree", fail_removal)
    async with asyncio.timeout(1.5):
        result = await run(_req("print_then_exit.py"), registry=registry)
    monkeypatch.undo()
    shutil.rmtree(driver.tmp_paths[-1], ignore_errors=True)
    assert any(warning.code == ErrorCode.CLEANUP_INCOMPLETE for warning in result.warnings)


async def test_on_event_raising_parser_failure_propagates_not_classified() -> None:
    from coding_agent_cli._transport import ParserFailure

    registry, driver = _setup()

    async def fail(event: Event) -> None:
        if isinstance(event, TextDelta):
            raise ParserFailure("caller")

    with pytest.raises(ParserFailure, match="caller"):
        async with asyncio.timeout(1.5):
            await run(_req("print_then_exit.py"), on_event=fail, registry=registry)
    assert not driver.tmp_paths[-1].exists()
