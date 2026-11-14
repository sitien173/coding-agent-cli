"""Pi CLI driver using Pi's non-interactive JSON event stream."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Literal

from .._args import reject_conflicts, reject_transport_flags, reject_unsupported
from .._classify import keyword_code
from ..driver import CliDriver, Facts, Launch, Parser, RequestError
from ..events import (
    Event,
    MessageCompleted,
    MessageStarted,
    ReasoningDelta,
    SessionIdentified,
    TextDelta,
    ToolCompleted,
    ToolStarted,
    UsageUpdated,
)
from ..types import Diagnostic, ErrorCode, Request, Usage
from ._common import as_int

_OWNED_FLAGS = ("--mode", "--session", "-p", "--print")
_PORTABLE_FLAGS: dict[str, tuple[str, ...]] = {
    "model": ("--model",),
    "reasoning": ("--thinking",),
    "system_prompt": ("--system-prompt",),
}


def _add_opt(total: int | None, value: int | None) -> int | None:
    if value is None:
        return total
    return (total or 0) + value


class PiParser:
    """Parse Pi's ``--mode json`` JSONL stream into v2 events and facts."""

    def __init__(self) -> None:
        self._session_id: str | None = None
        self._model: str | None = None
        self._message_open = False
        self._message_id: str | None = None
        self._input: int | None = None
        self._output: int | None = None
        self._cache_read: int | None = None
        self._cache_write: int | None = None
        self._context_used: int | None = None
        self._cost_usd: float | None = None
        self._num_turns = 0
        self._stop_reason: str | None = None
        self._error: Diagnostic | None = None
        self._error_lines: list[str] = []

    def _record_model(self, model: Any) -> None:
        if isinstance(model, str) and model:
            self._model = model

    def _usage(self) -> Usage:
        return Usage(
            input=self._input,
            output=self._output,
            cache_read=self._cache_read,
            cache_write=self._cache_write,
            context_used=self._context_used,
        )

    def _handle_message_end(self, msg: dict[str, Any]) -> list[Event]:
        events: list[Event] = []
        self._record_model(msg.get("model"))
        stop_reason = msg.get("stopReason")
        if isinstance(stop_reason, str) and stop_reason:
            self._stop_reason = stop_reason

        # Pi 1.5.2 docs/message-types.md, AssistantMessage, defines optional errorMessage.
        err_msg = msg.get("errorMessage")
        if stop_reason == "error" or (isinstance(err_msg, str) and err_msg):
            text = err_msg if isinstance(err_msg, str) and err_msg else "assistant message failed"
            self._error = Diagnostic(code=keyword_code(text) or ErrorCode.EXECUTION_ERROR, message=text)
        elif stop_reason in ("stop", "toolUse"):
            # Only a confirmed successful assistant message clears an earlier transient error.
            self._error = None

        usage = msg.get("usage")
        if isinstance(usage, dict):
            raw_input = as_int(usage.get("input"))
            out = as_int(usage.get("output"))
            cache_read = as_int(usage.get("cacheRead"))
            cache_write = as_int(usage.get("cacheWrite"))
            if any(v is not None for v in (raw_input, out, cache_read, cache_write)):
                # Pi reports uncached input separately from cacheRead/cacheWrite (shell.stdout.jsonl:37).
                turn_input = (
                    (raw_input or 0) + (cache_read or 0) + (cache_write or 0) if raw_input is not None else None
                )
                self._input = _add_opt(self._input, turn_input)
                self._output = _add_opt(self._output, out)
                self._cache_read = _add_opt(self._cache_read, cache_read)
                self._cache_write = _add_opt(self._cache_write, cache_write)
                if turn_input is not None:
                    self._context_used = turn_input + (out or 0)
                events.append(UsageUpdated(usage=self._usage()))
            cost = usage.get("cost")
            if isinstance(cost, dict):
                total_cost = cost.get("total")
                if isinstance(total_cost, (int, float)) and not isinstance(total_cost, bool):
                    self._cost_usd = (self._cost_usd or 0.0) + float(total_cost)

        if self._message_open:
            self._message_open = False
            events.append(MessageCompleted(entity_id=self._message_id))
            self._message_id = None
        return events

    def feed_json(self, obj: dict[str, Any]) -> Iterable[Event]:
        kind = obj.get("type")
        if kind == "session":
            sid = obj.get("id")
            if isinstance(sid, str) and sid and sid != self._session_id:
                self._session_id = sid
                return (SessionIdentified(session_id=sid),)
            return ()

        if kind == "message_start":
            msg = obj.get("message")
            if isinstance(msg, dict) and msg.get("role") == "assistant":
                self._record_model(msg.get("model"))
                self._message_open = True
                # Pi 1.5.2 docs/message-types.md, AssistantMessage, defines optional responseId.
                raw_id = msg.get("responseId")
                self._message_id = raw_id if isinstance(raw_id, str) and raw_id else None
                return (MessageStarted(entity_id=self._message_id),)
            return ()

        if kind == "message_update":
            update = obj.get("assistantMessageEvent")
            if not isinstance(update, dict):
                return ()
            utype = update.get("type")
            delta = update.get("delta")
            if not isinstance(delta, str) or not delta:
                return ()
            if utype == "text_delta":
                return (TextDelta(text=delta, entity_id=self._message_id),)
            if utype == "thinking_delta":
                return (ReasoningDelta(text=delta, entity_id=self._message_id),)
            return ()

        if kind == "message_end":
            msg = obj.get("message")
            if isinstance(msg, dict) and msg.get("role") == "assistant":
                return self._handle_message_end(msg)
            return ()

        if kind == "tool_execution_start":
            tool = obj.get("toolName")
            tool_name = tool if isinstance(tool, str) else ""
            call_id = obj.get("toolCallId")
            entity_id = call_id if isinstance(call_id, str) and call_id else None
            activity = "command" if tool_name == "bash" else "tool_call"
            return (ToolStarted(tool=tool_name, activity=activity, input=obj.get("args"), entity_id=entity_id),)

        if kind == "tool_execution_end":
            tool = obj.get("toolName")
            tool_name = tool if isinstance(tool, str) else ""
            call_id = obj.get("toolCallId")
            entity_id = call_id if isinstance(call_id, str) and call_id else None
            is_error = obj.get("isError")
            status = "error" if is_error is True else "completed" if is_error is False else "unknown"
            return (ToolCompleted(tool=tool_name, status=status, output=obj.get("result"), entity_id=entity_id),)

        if kind == "turn_end":
            self._num_turns += 1
            return ()

        return ()

    def feed_text(self, line: str, *, stream: Literal["stdout", "stderr"]) -> Iterable[Event]:
        stripped = line.strip()
        if stripped:
            self._error_lines.append(stripped)
        return ()

    def finish(self, read_back: Mapping[Path, bytes]) -> Facts:
        return Facts(
            session_id=self._session_id,
            model=self._model,
            usage=self._usage(),
            cost_usd=self._cost_usd,
            num_turns=self._num_turns or None,
            stop_reason=self._stop_reason,
            error=self._error,
            error_text="\n".join(self._error_lines),
        )


class PiDriver(CliDriver):
    """Runs ``pi --mode json`` with the prompt on stdin. Never adds an approval flag."""

    name = "pi"
    executable = "pi"

    def launch(self, req: Request, exe: Path, tmp: Path) -> Launch:
        for error in (
            reject_transport_flags(req.args, _OWNED_FLAGS),
            reject_unsupported(req, _PORTABLE_FLAGS),
            reject_conflicts(req, _PORTABLE_FLAGS),
        ):
            if error is not None:
                raise RequestError(error.code, error.message)
        argv = [str(exe), "--mode", "json"]
        if req.session_id:
            argv.extend(["--session", req.session_id])
        if req.model is not None:
            argv.extend(["--model", req.model])
        if req.reasoning is not None:
            argv.extend(["--thinking", req.reasoning])
        if req.system_prompt is not None:
            argv.extend(["--system-prompt", req.system_prompt])
        argv.extend(req.args)
        return Launch(argv=tuple(argv), stdin=req.prompt.encode("utf-8"))

    def parser(self, req: Request) -> Parser:
        return PiParser()


__all__ = ["PiDriver"]
