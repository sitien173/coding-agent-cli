"""Helpers shared by the built-in drivers: preflight, capability probes, event buffering, and results."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .._args import reject_conflicts, reject_transport_flags, reject_unsupported, validate_args
from .._classify import Facts, classify
from .._process import ProcessOutcome, run_process
from ..events import Event, ToolStarted
from ..registry import EventHandler
from ..types import Capabilities, Diagnostic, Request, Result, Usage

PROBE_TIMEOUT_S = 10.0

_VERIFIED_COMMANDS: dict[str, frozenset[str]] = {
    "claude": frozenset({"bash"}),
    "codex": frozenset({"bash"}),
}


def classify_tool_activity(provider: str, tool_name: str) -> Literal["command", "tool_call"]:
    """Classify tool activity by exact, case-sensitive provider and tool-name allow-lists."""
    commands = _VERIFIED_COMMANDS.get(provider)
    if commands is not None and tool_name in commands:
        return "command"
    return "tool_call"


def tool_started(entity_id: str, provider: str, tool_name: str, tool_input: Any = None) -> ToolStarted:
    return ToolStarted(
        entity_id=entity_id,
        tool=tool_name,
        activity=classify_tool_activity(provider, tool_name),
        input=tool_input,
    )


def tool_status(value: Any) -> Literal["completed", "error", "unknown"]:
    if value in ("completed", "error"):
        return value
    if value in ("failed", "failure"):
        return "error"
    return "unknown"


def parse_json_object(line: str) -> dict[str, Any] | None:
    """Return the line's JSON object, or None for non-JSON or non-object lines."""
    try:
        value = json.loads(line)
    except (json.JSONDecodeError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def as_int(val: Any) -> int | None:
    if isinstance(val, bool) or not isinstance(val, int) or val < 0:
        return None
    return val


def read_and_unlink(path: Path, *, errors: str) -> str:
    """Return the file's UTF-8 text, or "" when it cannot be read. The file is removed either way."""
    try:
        return path.read_text(encoding="utf-8", errors=errors)
    except OSError:
        return ""
    finally:
        try:
            path.unlink()
        except OSError:
            pass


def parse_token_component(usage: dict[str, Any], key: str) -> tuple[int | None, bool]:
    """Return (value, malformed). A missing or null key is absent, not malformed."""
    if key not in usage:
        return None, False
    val = usage[key]
    if val is None:
        return None, False
    if isinstance(val, bool) or not isinstance(val, int) or val < 0:
        return None, True
    return val, False


@dataclass(slots=True, frozen=True)
class TokenUsage:
    """Normalized usage.

    ``input`` is raw input plus cache reads and writes, or None when the raw input is absent or malformed.

    ``observed`` is True when any component was present or malformed.
    """

    input: int | None
    output: int | None
    cache_read: int | None
    cache_write: int | None
    observed: bool


NO_USAGE = TokenUsage(input=None, output=None, cache_read=None, cache_write=None, observed=False)


def read_usage(
    usage: dict[str, Any],
    *,
    input_key: str = "input_tokens",
    output_key: str = "output_tokens",
    cache_read_key: str = "cache_read_input_tokens",
    cache_write_key: str = "cache_creation_input_tokens",
) -> TokenUsage:
    raw_in, raw_in_bad = parse_token_component(usage, input_key)
    cr, cr_bad = parse_token_component(usage, cache_read_key)
    cw, cw_bad = parse_token_component(usage, cache_write_key)
    raw_out, raw_out_bad = parse_token_component(usage, output_key)
    if raw_in_bad or cr_bad or cw_bad or raw_in is None:
        norm_in = None
    else:
        norm_in = raw_in + (cr or 0) + (cw or 0)
    observed = any(v is not None for v in (norm_in, cr, cw, raw_out)) or raw_in_bad or cr_bad or cw_bad or raw_out_bad
    return TokenUsage(input=norm_in, output=raw_out, cache_read=cr, cache_write=cw, observed=observed)


class EventBuffer:
    """Collect the events one parsed line produces, then await the sink once per event in order.

    The parser stays synchronous. ``flush`` runs after each line, so a failing sink fails the run at that line.
    """

    def __init__(self, sink: EventHandler) -> None:
        self._sink = sink
        self._pending: list[Event] = []

    def emit(self, event: Event) -> None:
        self._pending.append(event)

    async def flush(self) -> None:
        while self._pending:
            await self._sink(self._pending.pop(0))


class Diagnostics:
    """Keep the first ``limit`` diagnostic lines, plus the first line that matches ``keep`` beyond it."""

    def __init__(self, limit: int = 50) -> None:
        self.limit = limit
        self.lines: list[str] = []
        self._kept: set[str] = set()

    def add(self, line: str, *, marks: Sequence[str] = ()) -> None:
        extra = [mark for mark in marks if mark not in self._kept]
        if len(self.lines) < self.limit or extra:
            self.lines.append(line)
            self._kept.update(marks)

    def text(self) -> str:
        return "\n".join(self.lines).strip()


def effective_env(req: Request) -> dict[str, str]:
    return {**os.environ, **req.env}


def resolve_executable(req: Request, default: str) -> Path | None:
    """Resolve ``req.executable`` or ``default`` on the request's effective ``PATH``."""
    found = shutil.which(req.executable or default, path=effective_env(req).get("PATH"))
    return None if found is None else Path(found)


def request_failure(req: Request, error: Diagnostic, cancel: asyncio.Event | None = None) -> Result:
    """A result for a run that never spawned."""
    verdict = classify(Facts(cancelled=cancel is not None and cancel.is_set(), request_error=error))
    return Result(outcome=verdict.outcome, session_id=req.session_id, error=verdict.error)


def build_result(facts: Facts, *, model: str | None, usage: Usage) -> Result:
    verdict = classify(facts)
    return Result(
        outcome=verdict.outcome,
        text=facts.text,
        session_id=facts.session_id or None,
        model=model,
        usage=usage,
        returncode=facts.returncode,
        error=verdict.error,
        warnings=verdict.warnings,
    )


def process_facts(process: ProcessOutcome, cancel: asyncio.Event) -> Facts:
    return Facts(
        cancelled=cancel.is_set(),
        process_error=process.error_class,
        process_message=process.error,
        returncode=process.returncode,
    )


def deadline_for(req: Request) -> float | None:
    return None if req.timeout_s is None else time.monotonic() + req.timeout_s


class CliDriver:
    """Shared preflight and capability probe for the built-in drivers.

    Subclasses set ``name``, ``executable``, ``owned_flags``, ``portable_flags`` (supported
    portable field to the flags that set it), ``help_args``, and ``help_markers``.
    """

    name = ""
    executable = ""
    owned_flags: tuple[str, ...] = ()
    portable_flags: Mapping[str, tuple[str, ...]] = {}
    help_args: tuple[str, ...] = ("--help",)
    help_markers: tuple[str, ...] = ()

    def __init__(self) -> None:
        self._probe_cache: set[tuple[str, int]] = set()
        self.log = logging.getLogger(f"coding_agent_cli.drivers.{self.name}")

    def validate(self, req: Request) -> Diagnostic | None:
        """Driver-specific argument checks. Return a diagnostic to reject the request."""
        return None

    def preflight(self, req: Request, cancel: asyncio.Event) -> tuple[Path, Path] | Result:
        """Validate the request and resolve ``(cwd, executable)``, or return the failure result."""
        error = (
            validate_args(req.args)
            or reject_unsupported(req, self.portable_flags)
            or reject_conflicts(req, self.portable_flags)
            or reject_transport_flags(req.args, self.owned_flags)
            or self.validate(req)
        )
        cwd = req.cwd.expanduser().absolute()
        if error is None:
            if not cwd.is_dir():
                error = Diagnostic(code="bad_cwd", message=f"cwd {os.fspath(cwd)!r} is not a directory")
        if error is not None:
            return request_failure(req, error, cancel)
        executable = resolve_executable(req, self.executable)
        if executable is None:
            name = req.executable or self.executable
            verdict = classify(
                Facts(
                    cancelled=cancel.is_set(),
                    process_error="missing_cli",
                    process_message=f"{name!r} was not found on PATH",
                )
            )
            return Result(outcome=verdict.outcome, session_id=req.session_id, error=verdict.error)
        return cwd, executable

    def portable_argv(self, req: Request) -> list[str]:
        """Flags for the portable options the request sets, in ``portable_flags`` order."""
        argv: list[str] = []
        for name, flags in self.portable_flags.items():
            value = getattr(req, name)
            if value is not None:
                argv.extend([flags[0], value])
        return argv

    async def check(self, req: Request, *, refresh: bool = False) -> Capabilities:
        executable = resolve_executable(req, self.executable)
        if executable is None:
            return Capabilities(available=False)
        try:
            key = (os.fspath(executable), executable.stat().st_mtime_ns)
        except OSError:
            return Capabilities(available=False)
        if not refresh and key in self._probe_cache:
            return Capabilities(available=True, executable=executable, structured_streaming=True)
        supported = await self._probe(req, executable)
        if supported:
            self._probe_cache.add(key)
        return Capabilities(available=True, executable=executable, structured_streaming=supported or None)

    async def _probe(self, req: Request, executable: Path) -> bool:
        lines: list[str] = []

        async def collect(line: str) -> None:
            lines.append(line)

        try:
            outcome = await run_process(
                [os.fspath(executable), *self.help_args],
                None,
                dict(req.env),
                None,
                time.monotonic() + PROBE_TIMEOUT_S,
                None,
                collect,
            )
        except OSError:
            return False
        if outcome.error_class:
            return False
        text = "\n".join(lines)
        return all(marker in text for marker in self.help_markers)


__all__ = [
    "NO_USAGE",
    "CliDriver",
    "Diagnostics",
    "EventBuffer",
    "TokenUsage",
    "as_int",
    "build_result",
    "classify_tool_activity",
    "deadline_for",
    "effective_env",
    "parse_json_object",
    "process_facts",
    "read_and_unlink",
    "read_usage",
    "request_failure",
    "resolve_executable",
    "tool_started",
]
