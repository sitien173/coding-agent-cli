from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Literal

from coding_agent_cli import Facts, MessageStarted, Outcome, TextDelta
from coding_agent_cli.events import Event

from .replay import replay, replay_truncated


class _Parser:
    def __init__(self) -> None:
        self.started = False
        self.streams: list[str] = []

    def _event(self, text: str) -> Iterable[Event]:
        events: list[Event] = []
        if not self.started:
            events.append(MessageStarted())
            self.started = True
        events.append(TextDelta(text=text))
        return events

    def feed_json(self, obj: dict[str, Any]) -> Iterable[Event]:
        return self._event(str(obj.get("type", "json")))

    def feed_text(self, line: str, *, stream: Literal["stdout", "stderr"]) -> Iterable[Event]:
        self.streams.append(stream)
        return self._event(line)

    def finish(self, read_back: Mapping[Path, bytes]) -> Facts:
        return Facts(session_id="fixture-session")


def test_replay_recorded_scenario_produces_events_and_classified_result() -> None:
    events, result = replay(_Parser(), "claude", "2.1.296", "plain")
    assert result.outcome == Outcome.SUCCESS
    assert events[-1].kind == "run.completed"
    assert result.text


def test_truncation_stops_after_nth_text_delta_and_keeps_partial_text() -> None:
    parser = _Parser()
    events, result = replay_truncated(parser, "claude", "2.1.296", "plain", 1)
    assert sum(isinstance(event, TextDelta) for event in events) == 1
    assert result.text
    assert result.outcome == Outcome.SUCCESS


def test_replay_classifies_error_present_only_in_stderr(tmp_path: Path) -> None:
    directory = tmp_path / "toy" / "1.0"
    directory.mkdir(parents=True)
    (directory / "auth.meta.json").write_text(
        json.dumps({"returncode": 1, "cancelled": False, "timed_out": False, "code": None}), encoding="utf-8"
    )
    (directory / "auth.stdout.jsonl").write_text('{"type":"output"}\n', encoding="utf-8")
    (directory / "auth.stderr.txt").write_text("Unauthorized API key\n", encoding="utf-8")
    _, result = replay(_Parser(), "toy", "1.0", "auth", root=tmp_path)
    assert result.outcome == Outcome.AGENT_FATAL
    assert result.error is not None and result.error.code.value == "auth"


class _TwoMessageParser(_Parser):
    def feed_json(self, obj: dict[str, Any]) -> Iterable[Event]:
        return [MessageStarted(), TextDelta(text="first"), MessageStarted()]


def test_replay_empty_last_message_matches_driver(tmp_path: Path) -> None:
    directory = tmp_path / "toy" / "1.0"
    directory.mkdir(parents=True)
    (directory / "two.meta.json").write_text(json.dumps({"returncode": 0}), encoding="utf-8")
    (directory / "two.stdout.jsonl").write_text('{"type":"output"}\n', encoding="utf-8")
    _, result = replay(_TwoMessageParser(), "toy", "1.0", "two", root=tmp_path)
    assert result.text == ""


def test_truncated_replay_ignores_unfed_stderr(tmp_path: Path) -> None:
    directory = tmp_path / "toy" / "1.0"
    directory.mkdir(parents=True)
    (directory / "auth.meta.json").write_text(json.dumps({"returncode": 0}), encoding="utf-8")
    (directory / "auth.stdout.jsonl").write_text('{"type":"a"}\n{"type":"b"}\n', encoding="utf-8")
    (directory / "auth.stderr.txt").write_text("Unauthorized API key\n", encoding="utf-8")
    _, result = replay_truncated(_Parser(), "toy", "1.0", "auth", 1, root=tmp_path)
    assert result.outcome == Outcome.SUCCESS
    assert result.text == "a"
