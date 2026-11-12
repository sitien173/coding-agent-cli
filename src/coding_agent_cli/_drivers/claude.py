"""Claude Code CLI driver using print-mode stream-json output."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from .._classify import http_status_code, keyword_code
from .._process import ProcessOutcome, run_process
from ..events import SessionIdentified, TextDelta, ToolCompleted, UsageUpdated
from ..registry import EventHandler, SpawnHandler
from ..types import Diagnostic, Request, Result
from ._common import (
    NO_USAGE,
    CliDriver,
    Diagnostics,
    EventBuffer,
    as_int,
    build_result,
    deadline_for,
    parse_json_object,
    process_facts,
    read_usage,
    tool_started,
)
from ._usage import TokenCounters

MAX_DIAGNOSTICS = 50
_RESULT_FIELDS = ("result", "session_id", "is_error", "subtype", "api_error_status", "modelUsage")


def _value_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return str(value)


def _unwrap(event: dict[str, Any]) -> dict[str, Any]:
    inner = event.get("event")
    return inner if event.get("type") == "stream_event" and isinstance(inner, dict) else event


def _http_status(value: Any) -> int | None:
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _result_failed(final: dict[str, Any]) -> bool:
    return bool(final.get("is_error")) or final.get("subtype") != "success"


def _result_error_text(final: dict[str, Any]) -> str:
    parts = [_value_text(final.get("result"))]
    status = _value_text(final.get("api_error_status"))
    if status:
        parts.append(f"api_error_status={status}")
    return "\n".join(part for part in parts if part).strip()


class ClaudeRun:
    """Parse one Claude run line by line. Raw lines are never retained.

    Diagnostics keep the first ``MAX_DIAGNOSTICS`` lines, plus the first line for each CLI error code
    beyond that bound, so the classification matches an unbounded run.
    """

    def __init__(self, sink: EventHandler) -> None:
        self.events = EventBuffer(sink)
        self.diagnostics = Diagnostics(MAX_DIAGNOSTICS)
        self.counters = TokenCounters()
        self.final: dict[str, Any] | None = None
        self.session_id = ""
        self.active_model = ""
        self.response_counter = 0
        self.message_counter = 0
        self.tool_counter = 0
        self.tool_entities: dict[str, str] = {}

    async def feed(self, line: str) -> None:
        stripped = line.strip()
        event = parse_json_object(stripped)
        if event is None:
            if stripped:
                self._keep_diagnostic(stripped)
        else:
            self._record(event)
        await self.events.flush()

    def finish(self, process: ProcessOutcome, cancel: asyncio.Event, requested_session: str | None) -> Result:
        """Map the parsed stream and the transport outcome to a result."""
        facts = process_facts(process, cancel)
        final = self.final
        if final is not None and _result_failed(final):
            code = http_status_code(_http_status(final.get("api_error_status"))) or "execution_error"
            facts.cli_error = Diagnostic(code=code, message=_result_error_text(final))
        elif final is not None:
            facts.text = _value_text(final.get("result"))
        facts.session_id = self.session_id or requested_session or ""
        facts.diagnostics = self.diagnostics.text()
        return build_result(facts, model=self._model(), usage=self.counters.usage())

    def _keep_diagnostic(self, line: str) -> None:
        code = keyword_code(line)
        self.diagnostics.add(line, marks=(code,) if code else ())

    def _record(self, raw: dict[str, Any]) -> None:
        event = _unwrap(raw)
        if event.get("type") == "result":
            self.final = {key: event.get(key) for key in _RESULT_FIELDS}
        self._identify(raw.get("session_id"))

        kind = event.get("type")
        if kind == "message_start":
            self.message_counter += 1
        elif kind == "content_block_delta":
            self._text_delta(event)
        elif kind == "content_block_start":
            self._start_tool(event)
        elif kind == "user":
            self._tool_results(event)
        elif kind == "assistant":
            self._assistant(event)
        elif kind == "result":
            self._result(event)

    def _identify(self, value: Any) -> None:
        if isinstance(value, str) and value and value != self.session_id:
            self.session_id = value
            self.events.emit(SessionIdentified(session_id=value))

    def _message_entity(self) -> str:
        return f"msg-{self.message_counter or 1}"

    def _usage_updated(self) -> None:
        self.events.emit(UsageUpdated(usage=self.counters.usage()))

    def _text_delta(self, event: dict[str, Any]) -> None:
        delta = event.get("delta")
        if not isinstance(delta, dict) or delta.get("type") != "text_delta":
            return
        text = delta.get("text")
        if isinstance(text, str) and text:
            self.events.emit(TextDelta(text=text, entity_id=self._message_entity()))

    def _start_tool(self, event: dict[str, Any]) -> None:
        block = event.get("content_block")
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            return
        self.tool_counter += 1
        entity_id = f"tool-{self.tool_counter}"
        tool_use_id = block.get("id")
        if isinstance(tool_use_id, str) and tool_use_id:
            self.tool_entities[tool_use_id] = entity_id
        self.events.emit(tool_started(entity_id, "claude", str(block.get("name", "")), tool_input=block.get("input")))

    def _tool_results(self, event: dict[str, Any]) -> None:
        message = event.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            return
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            tool_use_id = block.get("tool_use_id")
            entity_id = self.tool_entities.pop(tool_use_id, None) if isinstance(tool_use_id, str) else None
            if entity_id is None:
                continue
            status = "error" if block.get("is_error") is True else "completed"
            self.events.emit(ToolCompleted(status=status, entity_id=entity_id))

    def _assistant(self, event: dict[str, Any]) -> None:
        message = event.get("message")
        if not isinstance(message, dict):
            return
        model = message.get("model")
        if isinstance(model, str) and model:
            self.active_model = model
        usage = message.get("usage")
        if not isinstance(usage, dict):
            return
        tok = read_usage(usage)
        if not tok.observed:
            return

        msg_id = message.get("id")
        response_id = str(msg_id) if msg_id else f"msg-{self.response_counter + 1}"
        self.response_counter += 1
        self.counters.record_response(
            response_id,
            input=tok.input,
            output=tok.output,
            cache_read=tok.cache_read,
            cache_write=tok.cache_write,
        )
        self._usage_updated()
        if tok.input is not None:
            self.counters.record_context(used=tok.input + (tok.output or 0))
            self._usage_updated()

    def _context_capacity(self, event: dict[str, Any]) -> int | None:
        model_usage = event.get("modelUsage")
        if not isinstance(model_usage, dict):
            return None
        target_model = self.active_model
        if not target_model and len(model_usage) == 1:
            target_model = next(iter(model_usage))
        if not target_model or target_model not in model_usage:
            return None
        stats = model_usage[target_model]
        if not isinstance(stats, dict):
            return None
        cap = as_int(stats.get("contextWindow"))
        return cap if cap and cap > 0 else None

    def _result(self, event: dict[str, Any]) -> None:
        capacity = self._context_capacity(event)

        usage = event.get("usage")
        tok = read_usage(usage) if isinstance(usage, dict) else NO_USAGE
        if tok.observed:
            self.counters.record_totals(
                input=tok.input,
                output=tok.output,
                cache_read=tok.cache_read,
                cache_write=tok.cache_write,
            )
            self._usage_updated()
        if capacity is not None:
            self.counters.record_context(window=capacity)
            self._usage_updated()

    def _model(self) -> str | None:
        final = self.final
        if final is not None:
            model_usage = final.get("modelUsage")
            if isinstance(model_usage, dict) and model_usage:
                if self.active_model and self.active_model in model_usage:
                    return self.active_model
                first = next(iter(model_usage))
                if isinstance(first, str) and first:
                    return first
        return self.active_model or None


class ClaudeDriver(CliDriver):
    """Runs ``claude -p`` with the prompt on stdin. The driver never adds a permission flag."""

    name = "claude"
    executable = "claude"
    owned_flags = ("-p", "--output-format", "--include-partial-messages", "--resume")
    portable_flags = {
        "system_prompt": ("--system-prompt",),
        "model": ("--model",),
        "reasoning": ("--effort",),
    }
    help_markers = ("--output-format", "stream-json", "--include-partial-messages")

    def _argv(self, executable: Path, req: Request) -> list[str]:
        argv = [os.fspath(executable), "-p", "--output-format", "stream-json", "--include-partial-messages"]
        if req.session_id:
            argv.extend(["--resume", req.session_id])
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
        run = ClaudeRun(on_event)
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


__all__ = ["MAX_DIAGNOSTICS", "ClaudeDriver", "ClaudeRun"]
