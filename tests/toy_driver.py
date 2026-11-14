from __future__ import annotations

import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Literal

from coding_agent_cli import CliDriver, Facts, Launch, MessageStarted, Request, TextDelta

FAKE_CLI = Path(__file__).parent / "fixtures" / "fake_cli"


class _ToyParser:
    def __init__(self) -> None:
        self.started = False

    def feed_json(self, obj: dict[str, Any]) -> Iterable:
        return ()

    def feed_text(self, line: str, *, stream: Literal["stdout", "stderr"]) -> Iterable:
        if stream == "stderr":
            return ()
        prefix = () if self.started else (MessageStarted(entity_id="msg-1"),)
        self.started = True
        return (*prefix, TextDelta(entity_id="msg-1", text=line))

    def finish(self, read_back: Mapping[Path, bytes]) -> Facts:
        return Facts(session_id="toy-session")


class ToyDriver(CliDriver):
    name = "toy"
    executable = sys.executable

    def __init__(self) -> None:
        self.temp_files: list[Path] = []

    def launch(self, req: Request, exe: Path, tmp: Path) -> Launch:
        self.temp_files.append(tmp)
        script, *script_args = req.args
        return Launch(argv=(str(exe), str(FAKE_CLI / script), *script_args), stdin=req.prompt.encode())

    def parser(self, req: Request) -> _ToyParser:
        return _ToyParser()


__all__ = ["Facts", "ToyDriver"]
