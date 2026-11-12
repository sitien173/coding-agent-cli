"""Pi CLI driver using Pi's non-interactive JSON event stream."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from .._classify import keyword_code
from .._process import ProcessOutcome, run_process
from ..events import SessionIdentified, TextDelta, ToolCompleted, UsageUpdated
from ..registry import EventHandler, SpawnHandler
from ..types import Diagnostic, Request, Result
from ._common import (
    CliDriver,
    Diagnostics,
    EventBuffer,
    build_result,
    deadline_for,
    parse_json_object,
    process_facts,
    read_usage,
    tool_started,
    tool_status,
)
from ._usage import TokenCounters

MAX_DIAGNOSTICS = 50
_PI_USAGE_KEYS = {"input_key": "input", "output_key": "output", "cache_read_key": "cacheRead", "cache_write_key": "cacheWrite"}


def _message_text(message: Any) -> str:
    """Extract text from Pi's AssistantMessage content representation."""
    if not isinstance(message, dict) or message.get("role") != "assistant":
        return ""
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text", "")
            if isinstance(text, str):
                parts.append(text)
    return "".join(parts)


def _assistant_model(message: Any) -> str | None:
    if isinstance(message, dict) and message.get("role") == "assistant":
        model = message.get("model")
        if isinstance(model, str) and model:
            return model
    return None


def _event_model(event: dict[str, Any]) -> str | None:
    """Return the model named by one event: its assistant message, else its last assistant message."""
    model = _assistant_model(event.get("message"))
    if model:
        return model
    messages = event.get("messages")
    if isinstance(messages, list):
        for m in reversed(messages):
            model = _assistant_model(m)
            if model:
                return model
    return None


def _tool_call_id(event: dict[str, Any]) -> str:
    return str(event.get("toolCallId") or event.get("tool_call_id") or event.get("id") or "")


class PiRun:
    """Parse one Pi run line by line. Raw lines are never retained.

    Diagnostics keep the first ``MAX_DIAGNOSTICS`` lines, plus the first line for each CLI error code
    beyond that bound, so the classification matches an unbounded run. Response IDs are kept only for
    usage de-duplication.
    """

    def __init__(self, sink: EventHandler) -> None:
        self.events = EventBuffer(sink)
        self.diagnostics = Diagnostics(MAX_DIAGNOSTICS)
        self.counters = TokenCounters()
        self.session_id = ""
        self.agent_message = ""
        self.structured_error = ""
        self.model: str | None = None
        self.tool_counter = 0
        self.raw_to_entity: dict[str, str] = {}
        self.recorded_responses: set[str] = set()
        self.turn_counter = 0
        self.turn_completed_in_current_turn = False

    async def feed(self, line: str) -> None:
        stripped = line.strip()
        event = parse_json_object(stripped)
        if event is None:
            if stripped:
                self._keep_diagnostic(stripped)
        else:
            model = _event_model(event)
            if model:
                self.model = model
            self._output(event)
            self._telemetry(event)
        await self.events.flush()

    def finish(self, process: ProcessOutcome, cancel: asyncio.Event, requested_session: str | None) -> Result:
        """Map the parsed stream and the transport outcome to a result."""
        facts = process_facts(process, cancel)
        facts.text = self.agent_message
        if self.structured_error:
            facts.cli_error = Diagnostic(code="execution_error", message=self.structured_error)
        facts.session_id = self.session_id or requested_session or ""
        facts.diagnostics = self.diagnostics.text()
        return build_result(facts, model=self.model, usage=self.counters.usage())

    def _keep_diagnostic(self, text: str) -> None:
        code = keyword_code(text)
        self.diagnostics.add(text, marks=(code,) if code else ())

    def _identify(self, value: str) -> None:
        if value and value != self.session_id:
            self.session_id = value
            self.events.emit(SessionIdentified(session_id=value))

    def _record_message_error(self, message: Any) -> None:
        if not isinstance(message, dict) or message.get("stopReason") != "error":
            return
        error = message.get("errorMessage")
        if isinstance(error, str) and error:
            self._keep_diagnostic(error)
            self.structured_error = error
        else:
            self.structured_error = "error"

    def _message_end(self, message: Any) -> None:
        text = _message_text(message)
        if text:
            self.agent_message = text
        self._record_message_error(message)

    def _agent_end(self, messages: Any) -> None:
        if not isinstance(messages, list):
            return
        for message in reversed(messages):
            if not self.structured_error:
                self._record_message_error(message)
            text = _message_text(message)
            if text:
                self.agent_message = text
                break

    def _output(self, event: dict[str, Any]) -> None:
        """Collect the final reply, session ID, and error diagnostics from one event."""
        event_type = event.get("type")
        if event_type == "session" and isinstance(event.get("id"), str):
            self._identify(event["id"])
        if event_type in {"message_end", "message"}:
            self._message_end(event.get("message"))
        if event_type == "agent_end":
            self._agent_end(event.get("messages"))
        if event_type in {"error", "server_error"}:
            error = event.get("error") or event.get("message")
            if isinstance(error, str) and error:
                self._keep_diagnostic(error)

    def _telemetry(self, event: dict[str, Any]) -> None:
        event_type = event.get("type", "")
        if event_type == "message_update":
            self._message_update(event)
        elif event_type == "text_delta":
            text = event.get("text")
            self._emit_text(text if isinstance(text, str) else event.get("delta"))
        elif event_type in {"tool_execution_start", "tool_call"}:
            self._start_tool(event)
        elif event_type in {"tool_execution_end", "tool_result"}:
            self._complete_tool(event)

        if event_type == "turn_start":
            self.turn_counter += 1
            self.turn_completed_in_current_turn = False
        elif event_type in ("message_end", "turn_end"):
            self._message_usage(event.get("message"))
        elif event_type in ("agent_end", "summary"):
            self._summary_usage(event.get("usage") or event.get("totalUsage"))

    def _emit_text(self, text: Any) -> None:
        if isinstance(text, str) and text:
            self.events.emit(TextDelta(text=text, entity_id=f"msg-{self.turn_counter or 1}"))

    def _message_update(self, event: dict[str, Any]) -> None:
        update = event.get("assistantMessageEvent")
        if not isinstance(update, dict) or update.get("type") != "text_delta":
            return
        delta = update.get("delta")
        self._emit_text(delta if isinstance(delta, str) else update.get("text"))

    def _start_tool(self, event: dict[str, Any]) -> None:
        self.tool_counter += 1
        entity_id = f"tool-{self.tool_counter}"
        raw_id = _tool_call_id(event)
        if raw_id:
            self.raw_to_entity[raw_id] = entity_id
        tool_name = str(event.get("toolName") or event.get("tool_name") or event.get("tool") or event.get("name") or "")
        self.events.emit(tool_started(entity_id, "pi", tool_name, tool_input=event.get("args")))

    def _complete_tool(self, event: dict[str, Any]) -> None:
        entity_id = self.raw_to_entity.pop(_tool_call_id(event), None)
        if entity_id is None:
            return
        is_error = event.get("isError")
        status = "error" if is_error is True else "completed" if is_error is False else tool_status(event.get("status"))
        self.events.emit(ToolCompleted(status=status, entity_id=entity_id))

    def _response_id(self, msg: dict[str, Any]) -> str | None:
        """Return the ID to record this assistant message under, or None when it was already counted."""
        resp_id = str(msg.get("responseId") or msg.get("id") or "")
        if resp_id:
            return None if resp_id in self.recorded_responses else resp_id
        if self.turn_counter == 0:
            self.turn_counter = 1
            self.turn_completed_in_current_turn = False
        elif self.turn_completed_in_current_turn:
            return None
        assigned_id = f"turn_{self.turn_counter}"
        return None if assigned_id in self.recorded_responses else assigned_id

    def _message_usage(self, msg: Any) -> None:
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            return
        assigned_id = self._response_id(msg)
        if assigned_id is None:
            return

        self.recorded_responses.add(assigned_id)
        self.turn_completed_in_current_turn = True

        u = msg.get("usage")
        if not isinstance(u, dict):
            return
        tok = read_usage(u, **_PI_USAGE_KEYS)
        if not tok.observed:
            return
        self.counters.record_response(
            assigned_id,
            input=tok.input,
            output=tok.output,
            cache_read=tok.cache_read,
            cache_write=tok.cache_write,
        )
        if tok.input is not None:
            self.counters.record_context(used=tok.input + (tok.output or 0))
        self.events.emit(UsageUpdated(usage=self.counters.usage()))

    def _summary_usage(self, u: Any) -> None:
        if not isinstance(u, dict):
            return
        tok = read_usage(u, **_PI_USAGE_KEYS)
        if not tok.observed:
            return
        self.counters.record_totals(
            input=tok.input,
            output=tok.output,
            cache_read=tok.cache_read,
            cache_write=tok.cache_write,
        )
        self.events.emit(UsageUpdated(usage=self.counters.usage()))


class PiDriver(CliDriver):
    """Runs ``pi --mode json`` with the prompt on stdin. The driver never adds an approval flag."""

    name = "pi"
    executable = "pi"
    owned_flags = ("--mode", "--session")
    portable_flags = {
        "system_prompt": ("--system-prompt",),
        "model": ("--model",),
        "reasoning": ("--thinking",),
    }
    help_markers = ("--mode", "--session", "--thinking")

    def _argv(self, executable: Path, req: Request) -> list[str]:
        argv = [os.fspath(executable), "--mode", "json"]
        if req.session_id:
            argv.extend(["--session", req.session_id])
        argv.extend(self.portable_argv(req))
        argv.extend(req.args)
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
        self.log.info("run start resume=%s prompt_len=%d", bool(req.session_id), len(req.prompt))
        run = PiRun(on_event)
        process = await run_process(
            self._argv(executable, req),
            req.prompt,
            dict(req.env),
            os.fspath(cwd),
            deadline_for(req),
            cancel,
            run.feed,
            on_spawn=on_spawn,
        )
        result = run.finish(process, cancel, req.session_id)
        self.log.info("run done outcome=%s", result.outcome)
        return result


__all__ = ["MAX_DIAGNOSTICS", "PiDriver", "PiRun"]
