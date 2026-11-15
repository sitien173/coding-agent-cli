"""Agy CLI driver: stream-json on stdout, a temp log file for session ID, and prompt on stdin."""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections import deque
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, ClassVar, Literal

from .._args import reject_conflicts, reject_transport_flags, reject_unsupported
from .._classify import keyword_code
from ..driver import CliDriver, Facts, Launch, Parser, RequestError
from ..events import (
    Event,
    JsonValue,
    MessageCompleted,
    MessageStarted,
    TextDelta,
    ToolCompleted,
    ToolStarted,
    UsageUpdated,
)
from ..types import Diagnostic, ErrorCode, Request, Usage
from ._common import as_int

WINDOWS_COMMAND_LINE_LIMIT_CHARS = 32767
_MAX_ERROR_LINES = 50
_WINDOWS = os.name == "nt"
_UUID_PATTERN = r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
_CONVERSATION_ID_RE = re.compile(rf"(?:Created|Streaming) conversation {_UUID_PATTERN}")
_COMMAND_TOOLS = frozenset({"run_command"})
_ERROR_STATUS = "ERROR"

_OWNED_FLAGS = (
    "--output-format",
    "--input-format",
    "--log-file",
    "--conversation",
    "--new-project",
    "--print",
    "-p",
    "--prompt",
    "--continue",
    "-c",
)
_SUPPORTED_PORTABLE = ("model", "reasoning")
_PORTABLE_CONFLICTS = {
    "model": ("--model",),
    "reasoning": ("--effort",),
}


def _validate_prompt(prompt: str) -> None:
    """Reject a prompt that cannot be encoded as a UTF-8 JSON line."""
    if "\x00" in prompt:
        raise RequestError(ErrorCode.INVALID_PROMPT, "prompt cannot contain NUL bytes")
    try:
        prompt.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise RequestError(ErrorCode.INVALID_PROMPT, f"prompt is not valid UTF-8: {exc}") from exc


def _validate_command_line(argv: list[str]) -> None:
    """On Windows, reject an argv whose command line exceeds the CreateProcess limit."""
    if not _WINDOWS:
        return
    chars = len(subprocess.list2cmdline(argv))
    if chars > WINDOWS_COMMAND_LINE_LIMIT_CHARS:
        raise RequestError(
            ErrorCode.INVALID_PROMPT,
            f"agy command line is {chars} characters, above the Windows limit of {WINDOWS_COMMAND_LINE_LIMIT_CHARS}",
        )


def _step_key(update: dict[str, Any]) -> int | None:
    step_index = update.get("step_index")
    if isinstance(step_index, int) and not isinstance(step_index, bool):
        return step_index
    return None


class AgyParser:
    """Parser for ``agy --output-format stream-json`` output."""

    def __init__(self, *, requested_session: str | None = None) -> None:
        self._requested_session = requested_session or ""
        self._model: str | None = None
        self._saw_json = False
        self._plain_started = False
        self._active_message_step: int | None = None
        self._active_tools: dict[int, str] = {}
        self._error_lines: deque[str] = deque(maxlen=_MAX_ERROR_LINES)
        self._status = ""
        self._result_error = ""
        self._final_text: str | None = None
        self._stop_reason: str | None = None
        self._seen_usage = False
        self._input_total = 0
        self._output_total = 0
        self._cache_read_total = 0
        self._usage = Usage()

    def _build_usage(self) -> Usage:
        return Usage(
            input=self._input_total if self._seen_usage else None,
            output=self._output_total if self._seen_usage else None,
            cache_read=self._cache_read_total if self._seen_usage else None,
        )

    def _handle_usage(self, usage: Any, *, cumulative: bool, events: list[Event]) -> None:
        if not isinstance(usage, dict):
            return
        raw_in = as_int(usage.get("input_tokens"))
        raw_out = as_int(usage.get("output_tokens"))
        cache_read = as_int(usage.get("cache_read_tokens"))
        if any(v is not None for v in (raw_in, raw_out, cache_read)):
            self._seen_usage = True
            # Evidenced in agy/1.5.9/shell.stdout.jsonl: total_tokens == input_tokens + output_tokens.
            if cumulative:
                self._input_total = raw_in or 0
                self._output_total = raw_out or 0
                self._cache_read_total = cache_read or 0
            else:
                self._input_total += raw_in or 0
                self._output_total += raw_out or 0
                self._cache_read_total += cache_read or 0
            self._usage = self._build_usage()
            events.append(UsageUpdated(usage=self._usage))

    def _handle_agent_response(self, update: dict[str, Any], events: list[Event]) -> None:
        step_key = _step_key(update)
        delta = update.get("text_delta")
        if isinstance(delta, str) and delta:
            if self._active_message_step is None or self._active_message_step != step_key:
                if self._active_message_step is not None:
                    events.append(MessageCompleted())
                events.append(MessageStarted())
                self._active_message_step = step_key if step_key is not None else -1
            events.append(TextDelta(text=delta))
        if update.get("state") == "DONE" and self._active_message_step is not None:
            if step_key is None or self._active_message_step == step_key:
                events.append(MessageCompleted())
                self._active_message_step = None

    def _handle_tool(self, update: dict[str, Any], events: list[Event]) -> None:
        tool_info_raw = update.get("tool_info")
        tool_info: dict[str, Any] = tool_info_raw if isinstance(tool_info_raw, dict) else {}
        step_key = _step_key(update)
        state = update.get("state")
        if state == "ACTIVE":
            if step_key is not None and step_key in self._active_tools:
                return
            tool_name = str(update.get("tool_name") or tool_info.get("name") or "")
            if step_key is not None:
                self._active_tools[step_key] = tool_name
            activity: Literal["command", "tool_call"] = "command" if tool_name in _COMMAND_TOOLS else "tool_call"
            events.append(
                ToolStarted(
                    tool=tool_name,
                    activity=activity,
                    input=tool_info.get("parameters"),
                )
            )
        elif state == "DONE" or (step_key is not None and step_key in self._active_tools):
            # Any other terminal state closes the started step; its meaning is unproven, so it maps to unknown.
            started_name = self._active_tools.pop(step_key, None) if step_key is not None else None
            tool_name = (
                started_name
                if started_name is not None
                else str(update.get("tool_name") or tool_info.get("name") or "")
            )
            has_unevidenced_status = any(
                key in payload for payload in (update, tool_info) for key in ("error", "status")
            )
            status: Literal["completed", "error", "unknown"] = (
                "unknown" if has_unevidenced_status or state != "DONE" else "completed"
            )
            output: JsonValue | None = tool_info.get("output")
            events.append(ToolCompleted(tool=tool_name, status=status, output=output))

    def feed_json(self, obj: dict[str, Any]) -> Iterable[Event]:
        events: list[Event] = []
        if not self._saw_json and self._plain_started:
            events.append(MessageCompleted())
            self._plain_started = False
        self._saw_json = True

        kind = obj.get("event")
        if kind == "init":
            init = obj.get("init")
            if isinstance(init, dict):
                model = init.get("model")
                if isinstance(model, str) and model:
                    self._model = model
        elif kind == "step_update":
            update = obj.get("step_update")
            if isinstance(update, dict):
                step_type = update.get("step_type")
                if step_type == "agent_response":
                    self._handle_agent_response(update, events)
                elif step_type == "tool":
                    self._handle_tool(update, events)
                if update.get("state") == "DONE":
                    self._handle_usage(update.get("usage"), cumulative=False, events=events)
        elif kind == "result":
            result = obj.get("result")
            if isinstance(result, dict):
                response = result.get("response")
                if isinstance(response, str) and response:
                    self._final_text = response
                status = result.get("status")
                if isinstance(status, str):
                    self._status = status
                error = result.get("error")
                if isinstance(error, str) and error:
                    self._result_error = error
                finished_reason = result.get("finished_reason")
                if isinstance(finished_reason, str) and finished_reason:
                    self._stop_reason = finished_reason
                self._handle_usage(result.get("usage"), cumulative=True, events=events)
        return events

    def feed_text(self, line: str, *, stream: Literal["stdout", "stderr"]) -> Iterable[Event]:
        events: list[Event] = []
        if stream == "stderr":
            stripped = line.strip()
            if stripped:
                self._error_lines.append(stripped)
            return events

        if not self._saw_json:
            if not self._plain_started:
                events.append(MessageStarted())
                self._plain_started = True
                events.append(TextDelta(text=line))
            else:
                events.append(TextDelta(text=f"\n{line}"))
        # Mixed structured and plain stdout is unsupported; structured output takes precedence.
        return events

    def finish(self, read_back: Mapping[Path, bytes]) -> Facts:
        session_id: str | None = None
        for data in read_back.values():
            log_text = data.decode("utf-8", errors="replace")
            match = _CONVERSATION_ID_RE.search(log_text)
            if match:
                session_id = match.group(1)
                break
        if not session_id and self._requested_session:
            session_id = self._requested_session

        error_text = "\n".join(part for part in (self._result_error, *self._error_lines) if part)
        error: Diagnostic | None = None
        # Only SUCCESS and ERROR are evidenced in agy/1.5.9/; leave other statuses uninterpreted.
        if self._status == _ERROR_STATUS:
            code = keyword_code(error_text) or ErrorCode.EXECUTION_ERROR
            error = Diagnostic(code=code, message=self._result_error or f"agy reported status {self._status}")

        return Facts(
            final_text=self._final_text,
            session_id=session_id,
            model=self._model,
            usage=self._usage,
            num_turns=None,
            stop_reason=self._stop_reason,
            error=error,
            error_text=error_text,
        )


class AgyDriver(CliDriver):
    """Runs ``agy --print=`` with the prompt on stdin. Never adds a permission flag."""

    name: ClassVar[str] = "agy"
    executable: ClassVar[str] = "agy"

    def launch(self, req: Request, exe: Path, tmp: Path) -> Launch:
        _validate_prompt(req.prompt)
        for error in (
            reject_unsupported(req, _SUPPORTED_PORTABLE),
            reject_transport_flags(req.args, _OWNED_FLAGS),
            reject_conflicts(req, _PORTABLE_CONFLICTS),
        ):
            if error is not None:
                raise RequestError(error.code, error.message)

        log_path = tmp / "agy.log"
        argv = [
            os.fspath(exe),
            "--output-format",
            "stream-json",
            "--input-format",
            "stream-json",
            "--log-file",
            os.fspath(log_path),
        ]
        if req.session_id:
            argv.extend(["--conversation", req.session_id])
        else:
            argv.append("--new-project")
        if req.model is not None:
            argv.extend(["--model", req.model])
        if req.reasoning is not None:
            argv.extend(["--effort", req.reasoning])
        argv.extend(req.args)
        # Keep --print= last so no caller arg can take it as a flag value.
        argv.append("--print=")
        _validate_command_line(argv)
        payload = json.dumps({"event": "user", "message": {"content": [{"type": "text", "text": req.prompt}]}}) + "\n"
        return Launch(argv=tuple(argv), stdin=payload.encode("utf-8"), read_back=(log_path,))

    def parser(self, req: Request) -> Parser:
        return AgyParser(requested_session=req.session_id)


__all__ = ["AgyDriver", "AgyParser"]
