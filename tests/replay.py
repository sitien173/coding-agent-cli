"""Replay scrubbed CLI fixtures through a parser without starting a process."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from coding_agent_cli._classify import RunFacts, classify
from coding_agent_cli.driver import Parser
from coding_agent_cli.events import Event, MessageStarted, RunCompleted, TextDelta
from coding_agent_cli.types import Result

FIXTURES = Path(__file__).parent / "fixtures" / "recorded"


def _run_facts(meta: dict[str, Any], stderr_tail: str) -> RunFacts:
    stop = "cancelled" if meta.get("cancelled") else "timeout" if meta.get("timed_out") else "exited"
    return RunFacts(
        stop_cause=stop,
        returncode=meta.get("returncode"),
        stderr_tail=stderr_tail,
    )


def replay(
    parser: Parser,
    cli: str,
    version: str,
    scenario: str,
    *,
    root: Path = FIXTURES,
    text_delta_limit: int | None = None,
) -> tuple[tuple[Event, ...], Result]:
    directory = root / cli / version
    meta = json.loads((directory / f"{scenario}.meta.json").read_text(encoding="utf-8"))
    generated: list[Event] = []
    read_back: dict[Path, bytes] = {}
    delta_count = 0
    stopped = False

    def accept(events) -> None:
        nonlocal delta_count, stopped
        for event in events:
            if isinstance(event, TextDelta):
                delta_count += 1
                if text_delta_limit is not None and delta_count > text_delta_limit:
                    stopped = True
                    return
            generated.append(event)
            if text_delta_limit is not None and delta_count == text_delta_limit:
                stopped = True
                return

    stdout = (directory / f"{scenario}.stdout.jsonl").read_text(encoding="utf-8").splitlines()
    stderr_path = directory / f"{scenario}.stderr.jsonl"
    if not stderr_path.exists():
        stderr_path = directory / f"{scenario}.stderr.txt"
    stderr = stderr_path.read_bytes().decode("utf-8", errors="replace").splitlines() if stderr_path.exists() else []
    for line in stdout:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            value = None
        if isinstance(value, dict):
            accept(parser.feed_json(value))
        else:
            accept(parser.feed_text(line, stream="stdout"))
        if stopped:
            break
    # Separate captures carry no cross-stream timing, so a truncated replay sees no stderr.
    fed_stderr: list[str] = []
    if not stopped:
        for line in stderr:
            fed_stderr.append(line)
            accept(parser.feed_text(line, stream="stderr"))
            if stopped:
                break
    facts = parser.finish(read_back)
    # Same rule as CliDriver.run: the last started message wins, even when empty.
    current: list[str] = []
    for event in generated:
        if isinstance(event, MessageStarted):
            current = []
        elif isinstance(event, TextDelta):
            current.append(event.text)
    tail_bytes = "\n".join(fed_stderr).encode("utf-8") if fed_stderr else b""
    stderr_tail = tail_bytes[-(64 * 1024) :].decode("utf-8", errors="replace")
    result = classify(_run_facts(meta, stderr_tail), facts, text="".join(current))
    sequenced = tuple(replace(event, sequence=index) for index, event in enumerate(generated, 1))
    completed = RunCompleted(result=result, sequence=len(sequenced) + 1)
    return (*sequenced, completed), result


def replay_truncated(
    parser: Parser,
    cli: str,
    version: str,
    scenario: str,
    after_text_deltas: int,
    *,
    root: Path = FIXTURES,
) -> tuple[tuple[Event, ...], Result]:
    if after_text_deltas < 0:
        raise ValueError("after_text_deltas must be non-negative")
    return replay(parser, cli, version, scenario, root=root, text_delta_limit=after_text_deltas)


__all__ = ["replay", "replay_truncated"]
