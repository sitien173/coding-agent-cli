"""Claude Code CLI driver using print-mode stream-json output."""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .._args import reject_conflicts, reject_transport_flags, reject_unsupported
from .._classify import http_status_code
from ..driver import CliDriver, Facts, Launch, Parser, RequestError
from ..events import (
    Event,
    FileChanged,
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

_OWNED_FLAGS = ("-p", "--print", "--output-format", "--verbose", "--include-partial-messages", "--resume")
_PORTABLE_FLAGS: dict[str, tuple[str, ...]] = {
    "model": ("--model",),
    "reasoning": ("--effort",),
    "system_prompt": ("--system-prompt",),
}
_FILE_TOOL_CHANGES: dict[str, Literal["modified", "unknown"]] = {
    "Edit": "modified",
    "MultiEdit": "modified",
    "NotebookEdit": "modified",
    "Write": "unknown",
}


@dataclass(frozen=True, slots=True)
class _TurnTokens:
    base_input: int
    cache_write: int
    cache_read: int
    output: int


def _parse_usage(raw: Any) -> _TurnTokens | None:
    if not isinstance(raw, dict):
        return None
    inp = as_int(raw.get("input_tokens"))
    out = as_int(raw.get("output_tokens"))
    cache_write = as_int(raw.get("cache_creation_input_tokens"))
    cache_read = as_int(raw.get("cache_read_input_tokens"))
    if inp is None and out is None and cache_write is None and cache_read is None:
        return None
    return _TurnTokens(
        base_input=inp or 0,
        cache_write=cache_write or 0,
        cache_read=cache_read or 0,
        output=out or 0,
    )


def _file_path_for_tool(tool_name: str, tool_input: Any) -> str | None:
    if not isinstance(tool_input, dict):
        return None
    keys = ("notebook_path", "file_path") if tool_name == "NotebookEdit" else ("file_path",)
    for key in keys:
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _dedup_delta(streamed: dict[str, dict[int, str]], msg_key: str, full_text: str) -> str:
    """Return the suffix of ``full_text`` not yet emitted for any content block of the message."""
    if not full_text:
        return ""
    blocks = streamed.setdefault(msg_key, {})
    for index, already in blocks.items():
        if already and full_text.startswith(already):
            blocks[index] = full_text
            return full_text[len(already) :]
    blocks[-len(blocks) - 1] = full_text
    return full_text


def _error_message_from_result(obj: dict[str, Any]) -> str:
    errors = obj.get("errors")
    if isinstance(errors, list):
        parts = [item for item in errors if isinstance(item, str) and item.strip()]
        if parts:
            return "\n".join(parts)
    result = obj.get("result")
    if isinstance(result, str) and result.strip():
        return result.strip()
    return ""


class ClaudeParser:
    """Parser for Claude Code ``--output-format stream-json --verbose --include-partial-messages``."""

    def __init__(self, *, requested_session: str | None = None) -> None:
        self._requested_session = requested_session
        self._session_id: str | None = None
        self._model: str | None = None
        self._active_message_id: str | None = None
        self._message_open = False
        self._streamed_text: dict[str, dict[int, str]] = {}
        self._streamed_thinking: dict[str, dict[int, str]] = {}
        self._pending_tools: dict[str, tuple[str, Any]] = {}
        self._seen_tools: set[str] = set()
        self._message_usage: dict[str, _TurnTokens] = {}
        self._total_usage: _TurnTokens | None = None
        self._latest_context_used: int | None = None
        self._context_window: int | None = None
        self._cost_usd: float | None = None
        self._num_turns: int | None = None
        self._stop_reason: str | None = None
        self._final_text: str | None = None
        self._error: Diagnostic | None = None
        self._error_text: str = ""

    def feed_json(self, obj: dict[str, Any]) -> Iterable[Event]:
        events: list[Event] = []
        session_id = obj.get("session_id")
        if isinstance(session_id, str) and session_id and self._session_id is None:
            self._session_id = session_id
            events.append(SessionIdentified(session_id=session_id))

        kind = obj.get("type")
        if kind == "system" and obj.get("subtype") == "init":
            self._record_model(obj.get("model"))
        elif kind == "stream_event":
            inner = obj.get("event")
            if isinstance(inner, dict):
                self._handle_stream_event(inner, events)
        elif kind == "assistant":
            self._handle_assistant(obj, events)
        elif kind == "user":
            self._handle_user(obj, events)
        elif kind == "result":
            self._handle_result(obj, events)
        return events

    def feed_text(self, line: str, *, stream: Literal["stdout", "stderr"]) -> Iterable[Event]:
        return ()

    def finish(self, read_back: Mapping[Path, bytes]) -> Facts:
        return Facts(
            final_text=self._final_text,
            session_id=self._session_id or self._requested_session,
            model=self._model,
            usage=self._snapshot_usage(),
            cost_usd=self._cost_usd,
            num_turns=self._num_turns,
            stop_reason=self._stop_reason,
            error=self._error,
            error_text=self._error_text,
        )

    def _record_model(self, value: Any) -> None:
        if isinstance(value, str) and value and value != "<synthetic>":
            self._model = value

    def _msg_key(self, msg_id: str | None) -> str:
        return msg_id or "__anon__"

    def _close_open_message(self, events: list[Event]) -> None:
        if self._message_open:
            events.append(MessageCompleted(entity_id=self._active_message_id))
            self._message_open = False
            self._active_message_id = None

    def _record_usage(self, msg_key: str, raw_usage: Any, events: list[Event]) -> None:
        tokens = _parse_usage(raw_usage)
        if tokens is None:
            return
        self._message_usage[msg_key] = tokens
        self._latest_context_used = tokens.base_input + tokens.cache_write + tokens.cache_read + tokens.output
        events.append(UsageUpdated(usage=self._snapshot_usage()))

    def _snapshot_usage(self) -> Usage:
        if self._total_usage is not None:
            t = self._total_usage
            return Usage(
                input=t.base_input + t.cache_write + t.cache_read,
                output=t.output,
                cache_read=t.cache_read,
                cache_write=t.cache_write,
                context_used=self._latest_context_used,
                context_window=self._context_window,
            )
        if not self._message_usage:
            return Usage(context_window=self._context_window)
        base_in = sum(t.base_input for t in self._message_usage.values())
        cache_write = sum(t.cache_write for t in self._message_usage.values())
        cache_read = sum(t.cache_read for t in self._message_usage.values())
        output = sum(t.output for t in self._message_usage.values())
        return Usage(
            input=base_in + cache_write + cache_read,
            output=output,
            cache_read=cache_read,
            cache_write=cache_write,
            context_used=self._latest_context_used,
            context_window=self._context_window,
        )

    def _handle_stream_event(self, event: dict[str, Any], events: list[Event]) -> None:
        kind = event.get("type")
        if kind == "message_start":
            self._close_open_message(events)
            message = event.get("message")
            msg_id = message.get("id") if isinstance(message, dict) else None
            self._active_message_id = msg_id if isinstance(msg_id, str) and msg_id else None
            self._message_open = True
            events.append(MessageStarted(entity_id=self._active_message_id))
            if isinstance(message, dict):
                self._record_model(message.get("model"))
                self._record_usage(self._msg_key(self._active_message_id), message.get("usage"), events)
        elif kind == "content_block_delta":
            delta = event.get("delta")
            if not isinstance(delta, dict):
                return
            raw_index = event.get("index")
            index = raw_index if isinstance(raw_index, int) else 0
            delta_type = delta.get("type")
            if delta_type == "text_delta":
                text = delta.get("text")
                if isinstance(text, str) and text:
                    if not self._message_open:
                        self._message_open = True
                        events.append(MessageStarted(entity_id=self._active_message_id))
                    key = self._msg_key(self._active_message_id)
                    blocks = self._streamed_text.setdefault(key, {})
                    blocks[index] = blocks.get(index, "") + text
                    events.append(TextDelta(text=text, entity_id=self._active_message_id))
            elif delta_type == "thinking_delta":
                thinking = delta.get("thinking")
                if isinstance(thinking, str) and thinking:
                    key = self._msg_key(self._active_message_id)
                    blocks = self._streamed_thinking.setdefault(key, {})
                    blocks[index] = blocks.get(index, "") + thinking
                    events.append(ReasoningDelta(text=thinking, entity_id=self._active_message_id))
        elif kind == "message_delta":
            delta = event.get("delta")
            if isinstance(delta, dict):
                sr = delta.get("stop_reason")
                if isinstance(sr, str) and sr:
                    self._stop_reason = sr
            self._record_usage(self._msg_key(self._active_message_id), event.get("usage"), events)
        elif kind == "message_stop":
            self._close_open_message(events)

    def _handle_assistant(self, obj: dict[str, Any], events: list[Event]) -> None:
        message = obj.get("message")
        if not isinstance(message, dict):
            return
        self._record_model(message.get("model"))
        raw_id = message.get("id")
        msg_id = raw_id if isinstance(raw_id, str) and raw_id else None
        msg_key = self._msg_key(msg_id)
        opened_here = False
        if not self._message_open:
            self._active_message_id = msg_id
            self._message_open = True
            opened_here = True
            events.append(MessageStarted(entity_id=msg_id))

        first_text = ""
        content = message.get("content")
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text":
                    text = block.get("text")
                    if isinstance(text, str):
                        if not first_text:
                            first_text = text
                        delta = _dedup_delta(self._streamed_text, msg_key, text)
                        if delta:
                            events.append(TextDelta(text=delta, entity_id=msg_id))
                elif btype == "thinking":
                    thinking = block.get("thinking")
                    if isinstance(thinking, str):
                        delta = _dedup_delta(self._streamed_thinking, msg_key, thinking)
                        if delta:
                            events.append(ReasoningDelta(text=delta, entity_id=msg_id))
                elif btype == "tool_use":
                    # ToolStarted.input is read from assistant tool_use blocks because
                    # stream_event content_block_start carries an empty input dict.
                    tool_id = block.get("id")
                    tool_name = block.get("name")
                    if isinstance(tool_name, str) and tool_name:
                        tid = tool_id if isinstance(tool_id, str) and tool_id else None
                        if tid is not None and tid in self._seen_tools:
                            continue
                        if tid is not None:
                            self._seen_tools.add(tid)
                            self._pending_tools[tid] = (tool_name, block.get("input"))
                        activity: Literal["command", "tool_call"] = "command" if tool_name == "Bash" else "tool_call"
                        events.append(
                            ToolStarted(tool=tool_name, activity=activity, input=block.get("input"), entity_id=tid)
                        )

        if opened_here:
            self._close_open_message(events)

        # Synthetic API error assistant records (auth_failure.stdout.jsonl:3, invalid_model.stdout.jsonl:3)
        api_error = obj.get("error")
        if api_error == "authentication_failed":
            self._error = Diagnostic(code=ErrorCode.AUTH, message=first_text or "authentication failed")
        elif api_error == "model_not_found":
            self._error = Diagnostic(code=ErrorCode.INVALID_MODEL, message=first_text or "model not found")

    def _handle_user(self, obj: dict[str, Any], events: list[Event]) -> None:
        message = obj.get("message")
        if not isinstance(message, dict):
            return
        content = message.get("content")
        if not isinstance(content, list):
            return
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            tool_use_id = block.get("tool_use_id")
            tid = tool_use_id if isinstance(tool_use_id, str) and tool_use_id else None
            tool_name = "unknown"
            tool_input: Any = None
            if tid is not None and tid in self._pending_tools:
                tool_name, tool_input = self._pending_tools.pop(tid)
            is_error = block.get("is_error") is True
            status: Literal["completed", "error", "unknown"] = "error" if is_error else "completed"
            events.append(ToolCompleted(tool=tool_name, status=status, output=block.get("content"), entity_id=tid))
            if not is_error and tool_name in _FILE_TOOL_CHANGES:
                path = _file_path_for_tool(tool_name, tool_input)
                if path is not None:
                    events.append(
                        FileChanged(
                            path=path,
                            change=_FILE_TOOL_CHANGES[tool_name],
                            evidence="inferred",
                            entity_id=tid,
                        )
                    )

    def _handle_result(self, obj: dict[str, Any], events: list[Event]) -> None:
        cost = obj.get("total_cost_usd")
        if isinstance(cost, (int, float)) and not isinstance(cost, bool):
            self._cost_usd = float(cost)
        turns = as_int(obj.get("num_turns"))
        if turns is not None:
            self._num_turns = turns
        stop_reason = obj.get("stop_reason")
        if isinstance(stop_reason, str) and stop_reason:
            self._stop_reason = stop_reason

        model_usage = obj.get("modelUsage")
        usage_changed = False
        if isinstance(model_usage, dict) and model_usage:
            target = self._model if self._model in model_usage else next(iter(model_usage))
            if isinstance(target, str) and target:
                self._record_model(target)
                stats = model_usage.get(target)
                if isinstance(stats, dict):
                    cap = as_int(stats.get("contextWindow"))
                    if cap is not None and cap > 0 and cap != self._context_window:
                        self._context_window = cap
                        usage_changed = True

        # On budget_exhausted, result.usage is all zeros while stream_event/modelUsage hold totals
        # (claude/2.1.296/budget.stdout.jsonl:46).
        raw_usage = _parse_usage(obj.get("usage"))
        if raw_usage is not None and (
            raw_usage.base_input or raw_usage.cache_write or raw_usage.cache_read or raw_usage.output
        ):
            self._total_usage = raw_usage
            usage_changed = True

        if usage_changed:
            events.append(UsageUpdated(usage=self._snapshot_usage()))

        subtype = obj.get("subtype")
        is_error = bool(obj.get("is_error")) or subtype != "success"
        if not is_error:
            res = obj.get("result")
            if isinstance(res, str):
                self._final_text = res
            return

        msg = _error_message_from_result(obj)
        if subtype == "error_max_turns":
            self._error = Diagnostic(code=ErrorCode.MAX_TURNS, message=msg or "reached maximum turns")
        elif subtype == "error_max_budget_usd":
            self._error = Diagnostic(code=ErrorCode.BUDGET, message=msg or "reached maximum budget")
        else:
            api_status = as_int(obj.get("api_error_status"))
            http_code = http_status_code(api_status)
            if http_code is not None:
                self._error = Diagnostic(code=http_code, message=msg or f"API error {api_status}")
            elif msg:
                self._error_text = msg


class ClaudeDriver(CliDriver):
    """Runs ``claude -p`` with the prompt on stdin. The driver never adds a permission flag."""

    name = "claude"
    executable = "claude"

    def launch(self, req: Request, exe: Path, tmp: Path) -> Launch:
        for check in (
            reject_transport_flags(req.args, _OWNED_FLAGS),
            reject_unsupported(req, _PORTABLE_FLAGS),
            reject_conflicts(req, _PORTABLE_FLAGS),
        ):
            if check is not None:
                raise RequestError(check.code, check.message)

        argv: list[str] = [
            os.fspath(exe),
            "-p",
            "--output-format",
            "stream-json",
            "--verbose",
            "--include-partial-messages",
        ]
        if req.session_id:
            argv.extend(("--resume", req.session_id))
        if req.model is not None:
            argv.extend(("--model", req.model))
        if req.reasoning is not None:
            argv.extend(("--effort", req.reasoning))
        if req.system_prompt is not None:
            argv.extend(("--system-prompt", req.system_prompt))
        argv.extend(req.args)
        return Launch(argv=tuple(argv), stdin=req.prompt.encode("utf-8"))

    def parser(self, req: Request) -> Parser:
        return ClaudeParser(requested_session=req.session_id)


__all__ = ["ClaudeDriver", "ClaudeParser"]
