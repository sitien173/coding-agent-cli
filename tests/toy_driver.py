"""A minimal custom driver that runs a fake CLI script and emits one TextDelta per line.

``req.args[0]`` names a script in ``tests/fixtures/fake_cli``; the rest are its arguments.
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

from coding_agent_cli import Capabilities, Request, Result, SessionIdentified, TextDelta
from coding_agent_cli._classify import Facts
from coding_agent_cli._drivers._common import build_result, deadline_for, process_facts
from coding_agent_cli._drivers._usage import TokenCounters
from coding_agent_cli._process import run_process
from coding_agent_cli.registry import EventHandler, SpawnHandler

FAKE_CLI = Path(__file__).parent / "fixtures" / "fake_cli"


class ToyDriver:
    def __init__(self) -> None:
        self.temp_files: list[Path] = []

    async def check(self, req: Request, *, refresh: bool = False) -> Capabilities:
        return Capabilities(available=True, executable=Path(sys.executable), structured_streaming=True)

    async def run(
        self,
        req: Request,
        *,
        on_event: EventHandler,
        cancel: asyncio.Event,
        on_spawn: SpawnHandler | None,
    ) -> Result:
        script, *script_args = req.args
        with tempfile.NamedTemporaryFile(prefix="coding-agent-cli-toy-", delete=False) as tmp:
            temp = Path(tmp.name)
        self.temp_files.append(temp)
        text: list[str] = []

        async def on_line(line: str) -> None:
            text.append(line)
            await on_event(TextDelta(entity_id="msg-1", text=line))

        try:
            await on_event(SessionIdentified(session_id="toy-session"))
            process = await run_process(
                [sys.executable, str(FAKE_CLI / script), *script_args],
                req.prompt,
                dict(req.env),
                str(req.cwd),
                deadline_for(req),
                cancel,
                on_line,
                on_spawn=on_spawn,
            )
        finally:
            temp.unlink(missing_ok=True)
        facts = process_facts(process, cancel)
        facts.text = "\n".join(text)
        facts.session_id = "toy-session"
        return build_result(facts, model=None, usage=TokenCounters().usage())


__all__ = ["Facts", "ToyDriver"]
