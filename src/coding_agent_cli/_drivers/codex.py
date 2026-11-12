"""Codex CLI driver: JSONL on stdout, the final message in a temporary file, and an exact session ID."""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .._classify import Facts, keyword_code
from .._process import ProcessOutcome, run_process
from ..events import MessageCompleted, MessageStarted, SessionIdentified, TextDelta, ToolCompleted, UsageUpdated
from ..registry import EventHandler, SpawnHandler
from ..types import Diagnostic, Request, Result
from ._common import (
    CliDriver,
    Diagnostics,
    EventBuffer,
    as_int,
    build_result,
    deadline_for,
    effective_env,
    parse_json_object,
    process_facts,
    read_and_unlink,
    tool_started,
    tool_status,
)
from ._usage import TokenCounters
from .codex_rollout import CodexRolloutCollector

MAX_DIAGNOSTICS = 50
_SESSION_ID_STDOUT_RE = re.compile(
    r"^\s*session id:\s*([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_TOML_ESCAPES = {'"': '\\"', "\\": "\\\\", "\b": "\\b", "\t": "\\t", "\n": "\\n", "\f": "\\f", "\r": "\\r"}


def _toml_string(value: str) -> str:
    """Return ``value`` as a TOML basic string. Control characters and DEL use ``\\uXXXX`` escapes."""
    parts: list[str] = []
    for char in value:
        if char in _TOML_ESCAPES:
            parts.append(_TOML_ESCAPES[char])
        elif ord(char) < 0x20 or ord(char) == 0x7F:
            parts.append(f"\\u{ord(char):04X}")
        else:
            parts.append(char)
    return f'"{"".join(parts)}"'


def _session_id_line(text: str) -> str:
    """Return the ID of the first ``session id: <uuid>`` line in ``text``, or ""."""
    match = _SESSION_ID_STDOUT_RE.search(text)
    return match.group(1) if match else ""


def _resolve_session_id(thread_session_id: str, last_message: str, stdout_session_id: str) -> str:
    """Return the first exact session ID from the thread event, the last message, or stdout. No source returns ""."""
    for value in (thread_session_id.strip(), _session_id_line(last_message), stdout_session_id):
        if value:
            return value
    return ""


def _cd_flag(args: tuple[str, ...]) -> str | None:
    """Return the first raw arg that sets the working directory: ``--cd``, ``--cd=...``, ``-C``, or ``-C...``."""
    for token in args:
        if token == "--cd" or token.startswith("--cd="):
            return token
        if token.startswith("-C") and not token.startswith("--"):
            return token
    return None


class CodexRun:
    """Parse one Codex run line by line with bounded state. Raw lines are never retained.

    The answer is the last-message file when it has text, else the concatenated ``agent_message``
    items. Diagnostics keep the first ``MAX_DIAGNOSTICS`` non-JSON lines plus the first fatal-token line.
    """

    def __init__(
        self,
        sink: EventHandler,
        *,
        cwd: Path,
        env: Mapping[str, str],
        requested_session: str | None,
    ) -> None:
        self.events = EventBuffer(sink)
        self.diagnostics = Diagnostics(MAX_DIAGNOSTICS)
        self.counters = TokenCounters()
        self.requested_session = requested_session or ""
        self.thread_session_id = ""
        self.stdout_session_id = ""
        self.agent_messages: list[str] = []
        self.entity_counter = 0
        self.raw_to_entity: dict[str, str] = {}
        self.turn_counter = 0
        self.collector = CodexRolloutCollector(
            cwd=cwd,
            env=env,
            session_id=self.requested_session,
            counters=self.counters,
            on_change=self._usage_updated,
        )

    async def feed(self, line: str) -> None:
        stripped = line.strip()
        if not self.stdout_session_id:
            self.stdout_session_id = _session_id_line(stripped)
        event = parse_json_object(stripped)
        if event is None:
            if stripped:
                self._keep_diagnostic(stripped)
        else:
            self._on_event(event)
        await self.events.flush()

    def _keep_diagnostic(self, line: str) -> None:
        code = keyword_code(line)
        self.diagnostics.add(line, marks=(code,) if code else ())

    def _usage_updated(self) -> None:
        self.events.emit(UsageUpdated(usage=self.counters.usage()))

    def _start_item(self, item: dict[str, Any], raw_id: str) -> None:
        item_type = item.get("type", "")
        if item_type == "agent_message":
            self.entity_counter += 1
            entity_id = f"msg-{self.entity_counter}"
            if raw_id:
                self.raw_to_entity[raw_id] = entity_id
            self.events.emit(MessageStarted(entity_id=entity_id))
        elif item_type == "tool_call":
            self.entity_counter += 1
            entity_id = f"tool-{self.entity_counter}"
            if raw_id:
                self.raw_to_entity[raw_id] = entity_id
            self.events.emit(tool_started(entity_id, "codex", str(item.get("name", "")), tool_input=item.get("input")))

    def _complete_item(self, item: dict[str, Any], raw_id: str) -> None:
        item_type = item.get("type", "")
        if item_type == "agent_message":
            text = item.get("text")
            if isinstance(text, str) and text:
                self.agent_messages.append(text)
            entity_id = self.raw_to_entity.get(raw_id) or f"msg-{self.entity_counter or 1}"
            if isinstance(text, str) and text:
                self.events.emit(TextDelta(text=text, entity_id=entity_id))
            self.events.emit(MessageCompleted(entity_id=entity_id))
        elif item_type == "tool_call":
            entity_id = self.raw_to_entity.get(raw_id) or f"tool-{self.entity_counter or 1}"
            self.events.emit(ToolCompleted(status=tool_status(item.get("status")), entity_id=entity_id))

    def _on_event(self, parsed: dict[str, Any]) -> None:
        event_type = parsed.get("type", "")
        if event_type == "thread.started" and parsed.get("thread_id"):
            thread_id = str(parsed.get("thread_id"))
            if thread_id != self.thread_session_id:
                self.thread_session_id = thread_id
                self.events.emit(SessionIdentified(session_id=thread_id))
            self.collector.set_session_id(thread_id)
        self.collector.poll()

        item = parsed.get("item")
        if isinstance(item, dict):
            raw_id = str(item.get("id", ""))
            if event_type == "item.started":
                self._start_item(item, raw_id)
            elif event_type == "item.completed":
                self._complete_item(item, raw_id)

        if event_type == "turn.completed":
            self.collector.poll()
            self._record_turn_usage(parsed.get("usage"))
            self.collector.poll()
        elif event_type in ("thread.completed", "summary"):
            self.collector.poll()
            self._record_final_usage(parsed.get("usage"))
            self.collector.poll()

    def _record_turn_usage(self, u: Any) -> None:
        if not isinstance(u, dict):
            return
        raw_in = as_int(u.get("input_tokens"))
        cached_in = as_int(u.get("cached_input_tokens"))
        cw = as_int(u.get("cache_write_input_tokens"))
        raw_out = as_int(u.get("output_tokens"))
        reasoning_out = as_int(u.get("reasoning_output_tokens"))
        if not any(v is not None for v in (raw_in, cached_in, cw, raw_out, reasoning_out)):
            return
        # Codex input_tokens already includes cached_input_tokens.
        self.turn_counter += 1
        self.counters.record_response(
            f"turn_{self.turn_counter}",
            input=raw_in,
            output=raw_out,
            cache_read=cached_in,
            cache_write=cw,
        )
        self._record_turn_context(raw_in, raw_out, reasoning_out)
        self._usage_updated()

    def _record_turn_context(self, norm_in: int | None, norm_out: int | None, reasoning_out: int | None) -> None:
        """Derive a context snapshot from turn usage unless the rollout already owns the context."""
        if norm_in is None or norm_out is None:
            return
        used = norm_in + norm_out - (reasoning_out or 0)
        if used >= 0 and not self.collector.is_compacted and not self.collector.has_verified_context and not self.collector.budget_exhausted:
            self.counters.record_context(used=used)

    def _record_final_usage(self, u: Any) -> None:
        if not isinstance(u, dict):
            return
        raw_in = as_int(u.get("input_tokens"))
        cached_in = as_int(u.get("cached_input_tokens"))
        cw = as_int(u.get("cache_write_input_tokens"))
        raw_out = as_int(u.get("output_tokens"))
        if any(v is not None for v in (raw_in, cached_in, cw, raw_out)) and not self.requested_session:
            self.counters.record_totals(input=raw_in, output=raw_out, cache_read=cached_in, cache_write=cw)
            self._usage_updated()

    def finish(self, process: ProcessOutcome, cancel: asyncio.Event, last_message: str) -> Result:
        """Map the parsed stream, the last-message file text, and the transport outcome to a result."""
        last_message_text = last_message.strip()
        text = last_message_text or "".join(self.agent_messages).strip()

        session_id = _resolve_session_id(self.thread_session_id, last_message, self.stdout_session_id)
        if session_id:
            self.collector.set_session_id(session_id)
            self.collector.poll()

        facts: Facts = process_facts(process, cancel)
        facts.text = text
        facts.session_id = session_id or None
        # Diagnostics classify the run only when there is no agent output, or when the process exited nonzero.
        if not text or facts.returncode:
            facts.diagnostics = self.diagnostics.text()
        return build_result(facts, model=self.collector.model_name, usage=self.counters.usage())


class CodexDriver(CliDriver):
    """Runs ``codex exec`` with the prompt on stdin, in ``Request.cwd``. The driver never adds an approval or sandbox flag."""

    name = "codex"
    executable = "codex"
    owned_flags = ("--json", "-o", "--output-last-message")
    portable_flags = {
        "model": ("--model", "-m"),
        "reasoning": ("-c", "--config"),
    }
    help_args = ("exec", "--help")
    help_markers = ("--json", "--output-last-message")

    def validate(self, req: Request) -> Diagnostic | None:
        token = _cd_flag(req.args)
        if token is not None:
            return Diagnostic(code="invalid_args", message=f"args cannot set the working directory ({token!r}); use Request.cwd")
        return None

    def portable_argv(self, req: Request) -> list[str]:
        argv: list[str] = []
        if req.model is not None:
            argv.extend(["--model", req.model])
        if req.reasoning is not None:
            argv.extend(["-c", f"model_reasoning_effort={_toml_string(req.reasoning)}"])
        return argv

    def _argv(self, executable: Path, req: Request, last_message_path: Path) -> list[str]:
        argv = [os.fspath(executable), "exec", "--json", "-o", os.fspath(last_message_path)]
        argv.extend(self.portable_argv(req))
        argv.extend(req.args)
        if req.session_id:
            argv.extend(["resume", req.session_id])
        # "-" makes codex exec read the prompt from stdin.
        argv.append("-")
        return argv

    async def run(
        self,
        req: Request,
        *,
        on_event: EventHandler,
        cancel: asyncio.Event,
        on_spawn: SpawnHandler | None,
    ) -> Result:
        prepared = self.preflight(req, cancel)
        if isinstance(prepared, Result):
            return prepared
        cwd, executable = prepared
        fd, name = tempfile.mkstemp(prefix="coding-agent-cli-", suffix=".txt")
        os.close(fd)
        last_message_path = Path(name)
        self.log.info("run start resume=%s prompt_len=%d", bool(req.session_id), len(req.prompt))
        run = CodexRun(on_event, cwd=cwd, env=effective_env(req), requested_session=req.session_id)
        try:
            process = await run_process(
                self._argv(executable, req, last_message_path),
                req.prompt,
                dict(req.env),
                os.fspath(cwd),
                deadline_for(req),
                cancel,
                run.feed,
                on_spawn=on_spawn,
            )
            last_message = read_and_unlink(last_message_path, errors="replace")
        finally:
            with contextlib.suppress(OSError):
                last_message_path.unlink()
        result = run.finish(process, cancel, last_message)
        self.log.info("run done outcome=%s", result.outcome)
        return result


__all__ = ["MAX_DIAGNOSTICS", "CodexDriver", "CodexRun"]
