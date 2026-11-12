"""Agy CLI driver: stream-json on stdout, a temporary log file for the session ID, and the prompt in argv."""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .._classify import Facts, keyword_code
from .._platform import prepare_command
from .._process import ProcessOutcome, run_process
from ..events import SessionIdentified, TextDelta, ToolCompleted, UsageUpdated
from ..registry import EventHandler, SpawnHandler
from ..types import Diagnostic, Request, Result
from ._common import (
    CliDriver,
    Diagnostics,
    EventBuffer,
    as_int,
    build_result,
    deadline_for,
    parse_json_object,
    process_facts,
    read_and_unlink,
    request_failure,
    tool_started,
)
from ._usage import TokenCounters

MAX_DIAGNOSTICS = 50
PROMPT_LIMIT_BYTES = 100 * 1024
WINDOWS_COMMAND_LINE_LIMIT_CHARS = 32767
_WINDOWS = os.name == "nt"
_UUID_PATTERN = r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
_CONVERSATION_ID_RE = re.compile(rf"(?:Created|Streaming) conversation {_UUID_PATTERN}")
_SUCCESS_STATUS = "SUCCESS"


def _event_model(event: dict[str, Any]) -> str | None:
    """Return the model named by one event: its top-level ``model``, else its ``result.model``."""
    model = event.get("model")
    if isinstance(model, str) and model:
        return model
    result = event.get("result")
    if isinstance(result, dict):
        model = result.get("model")
        if isinstance(model, str) and model:
            return model
    return None


def _prompt_error(prompt: str) -> Diagnostic | None:
    """Reject a prompt that cannot be passed in argv: NUL bytes, invalid UTF-8, or more than the size limit."""
    if "\x00" in prompt:
        return Diagnostic(code="invalid_prompt", message="prompt cannot contain NUL bytes")
    try:
        size = len(prompt.encode("utf-8"))
    except UnicodeEncodeError as exc:
        return Diagnostic(code="invalid_prompt", message=f"prompt is not valid UTF-8: {exc}")
    if size > PROMPT_LIMIT_BYTES:
        return Diagnostic(
            code="invalid_prompt",
            message=(
                f"agy prompt is {size} bytes, "
                f"above the 100 KiB ({PROMPT_LIMIT_BYTES} bytes) limit for the prompt argument"
            ),
        )
    return None


def _command_line_error(argv: list[str]) -> Diagnostic | None:
    """On Windows, reject an argv whose command line exceeds the CreateProcess limit."""
    if not _WINDOWS:
        return None
    chars = len(subprocess.list2cmdline(prepare_command(list(argv))))
    if chars <= WINDOWS_COMMAND_LINE_LIMIT_CHARS:
        return None
    return Diagnostic(
        code="invalid_prompt",
        message=(
            f"agy command line is {chars} characters, "
            f"above the Windows limit of {WINDOWS_COMMAND_LINE_LIMIT_CHARS} characters"
        ),
    )


class AgyRun:
    """Parse one Agy run line by line with bounded state. Raw lines are never retained after the first JSON event.

    Plain-text lines are the reply only for a run that emits no JSON events, so they are kept until the first event.
    """

    def __init__(self, sink: EventHandler, *, requested_session: str | None) -> None:
        self.events = EventBuffer(sink)
        self.diagnostics = Diagnostics(MAX_DIAGNOSTICS)
        self.counters = TokenCounters()
        self.requested_session = requested_session or ""
        self.stream_session_id = ""
        self.conversation_id = ""
        self.tool_counter = 0
        self.active_tool_id = ""
        self.step_counter = 0
        self.deltas: list[str] = []
        self.final_response = ""
        self.status = ""
        self.model: str | None = None
        self.saw_event = False
        self.plain_lines: list[str] = []

    async def feed(self, line: str) -> None:
        stripped = line.strip()
        match = _CONVERSATION_ID_RE.search(stripped)
        if match and not self.stream_session_id:
            self.stream_session_id = match.group(1)
            self._identify(self.stream_session_id)
        event = parse_json_object(stripped)
        if event is None:
            if stripped:
                code = keyword_code(stripped)
                self.diagnostics.add(stripped, marks=(code,) if code else ())
                if match is None and not self.saw_event:
                    self.plain_lines.append(stripped)
        else:
            self.saw_event = True
            self.plain_lines.clear()
            model = _event_model(event)
            if model:
                self.model = model
            self._dispatch(event)
        await self.events.flush()

    def _identify(self, value: Any) -> None:
        if isinstance(value, str) and value and value != self.conversation_id:
            self.conversation_id = value
            self.events.emit(SessionIdentified(session_id=value))

    def _usage_updated(self) -> None:
        self.events.emit(UsageUpdated(usage=self.counters.usage()))

    def _dispatch(self, event: dict[str, Any]) -> None:
        self._identify(event.get("conversation_id"))
        kind = event.get("event", "")
        if kind == "step_update":
            self._step_update(event.get("step_update"))
        elif kind == "result":
            self._result(event.get("result"))

    def _step_update(self, update: Any) -> None:
        if not isinstance(update, dict):
            return
        self._identify(update.get("conversation_id"))
        step_type = update.get("step_type", "")
        if step_type == "agent_response":
            self._append_delta(update.get("text_delta"))
        elif step_type == "tool":
            self._tool_update(update)

        usage = update.get("usage")
        if update.get("state", "") == "DONE" and isinstance(usage, dict):
            self._step_usage(update, usage)

    def _append_delta(self, value: Any) -> None:
        if isinstance(value, str) and value:
            self.deltas.append(value)
            self.events.emit(TextDelta(text=value, entity_id="msg-1"))

    def _tool_update(self, update: dict[str, Any]) -> None:
        tool_info = update.get("tool_info")
        if not isinstance(tool_info, dict):
            tool_info = {}
        state = update.get("state", "")
        if state == "ACTIVE":
            self.tool_counter += 1
            self.active_tool_id = f"tool-{self.tool_counter}"
            tool_name = str(update.get("tool_name", "") or tool_info.get("name", ""))
            self.events.emit(
                tool_started(self.active_tool_id, "agy", tool_name, tool_input=tool_info.get("parameters"))
            )
        elif state == "DONE":
            self.events.emit(
                ToolCompleted(status="completed", entity_id=self.active_tool_id or f"tool-{self.tool_counter or 1}")
            )
            self.active_tool_id = ""

    def _step_usage(self, update: dict[str, Any], usage: dict[str, Any]) -> None:
        raw_in = as_int(usage.get("input_tokens"))
        raw_out = as_int(usage.get("output_tokens"))
        cache_read = as_int(usage.get("cache_read_tokens"))
        cache_write = as_int(usage.get("cache_write_tokens"))
        if not any(v is not None for v in (raw_in, raw_out, cache_read, cache_write)):
            return
        self.step_counter += 1
        step_index = update.get("step_index")
        step_id = f"step_{step_index}" if step_index is not None else f"step_{self.step_counter}"
        self.counters.record_response(
            step_id, input=raw_in, output=raw_out, cache_read=cache_read, cache_write=cache_write
        )
        window = as_int(
            usage.get("model_context_window")
            or usage.get("context_window_size")
            or update.get("model_context_window")
            or update.get("context_window_size")
        )
        if raw_in is not None and raw_out is not None:
            used = raw_in + raw_out - (as_int(usage.get("thinking_tokens")) or 0)
            if used >= 0:
                self.counters.record_context(used=used)
        if window is not None:
            self.counters.record_context(window=window)
        self._usage_updated()

    def _result(self, result: Any) -> None:
        if not isinstance(result, dict):
            return
        self._identify(result.get("conversation_id"))
        response = result.get("response")
        if isinstance(response, str) and response:
            self.final_response = response
        status = result.get("status")
        if isinstance(status, str):
            self.status = status
        usage = result.get("usage")
        if not isinstance(usage, dict):
            return
        raw_in = as_int(usage.get("input_tokens"))
        raw_out = as_int(usage.get("output_tokens"))
        cache_read = as_int(usage.get("cache_read_tokens"))
        cache_write = as_int(usage.get("cache_write_tokens"))
        totals = any(v is not None for v in (raw_in, raw_out, cache_read, cache_write))
        if totals:
            self.counters.record_totals(input=raw_in, output=raw_out, cache_read=cache_read, cache_write=cache_write)
        window = as_int(usage.get("model_context_window") or usage.get("context_window_size"))
        if window is not None:
            self.counters.record_context(window=window)
        if totals or window is not None:
            self._usage_updated()

    def finish(self, process: ProcessOutcome, cancel: asyncio.Event, log_text: str) -> Result:
        """Map the parsed stream, the log file text, and the transport outcome to a result."""
        match = _CONVERSATION_ID_RE.search(log_text)
        session_id = (
            match.group(1) if match else (self.stream_session_id or self.conversation_id or self.requested_session)
        )
        if not self.saw_event:
            text = "\n".join(self.plain_lines).strip()
        else:
            text = (self.final_response or "".join(self.deltas)).strip()

        facts: Facts = process_facts(process, cancel)
        facts.text = text
        facts.session_id = session_id or None
        if self.status and self.status != _SUCCESS_STATUS:
            facts.cli_error = Diagnostic(code="execution_error", message=f"agy reported status {self.status}")
        # Diagnostics classify the run only when there is no agent output, or when the process exited nonzero.
        if not text or facts.returncode:
            facts.diagnostics = self.diagnostics.text()
        return build_result(facts, model=self.model, usage=self.counters.usage())


class AgyDriver(CliDriver):
    """Runs ``agy --print`` with the prompt in argv, in ``Request.cwd``. The driver never adds a permission flag."""

    name = "agy"
    executable = "agy"
    owned_flags = (
        "--output-format",
        "--log-file",
        "--conversation",
        "--new-project",
        "--print",
        "-p",
        "--prompt",
        "--continue",
        "-c",
    )
    portable_flags = {"model": ("--model",)}
    help_markers = ("--output-format", "stream-json", "--log-file")

    def validate(self, req: Request) -> Diagnostic | None:
        return _prompt_error(req.prompt)

    def _argv(self, executable: Path, req: Request, log_path: Path) -> list[str]:
        argv = [os.fspath(executable), "--output-format", "stream-json", "--log-file", os.fspath(log_path)]
        if req.session_id:
            argv.extend(["--conversation", req.session_id])
        else:
            argv.append("--new-project")
        argv.extend(self.portable_argv(req))
        argv.extend(req.args)
        # The prompt comes last so no caller arg can take it as a flag value.
        argv.extend(["--print", req.prompt])
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
        fd, name = tempfile.mkstemp(prefix="coding-agent-cli-", suffix=".log")
        os.close(fd)
        log_path = Path(name)
        self.log.info("run start resume=%s prompt_len=%d", bool(req.session_id), len(req.prompt))
        run = AgyRun(on_event, requested_session=req.session_id)
        try:
            argv = self._argv(executable, req, log_path)
            error = _command_line_error(argv)
            if error is not None:
                return request_failure(req, error, cancel)
            process = await run_process(
                argv,
                None,
                dict(req.env),
                os.fspath(cwd),
                deadline_for(req),
                cancel,
                run.feed,
                on_spawn=on_spawn,
            )
            log_text = read_and_unlink(log_path, errors="ignore")
        finally:
            with contextlib.suppress(OSError):
                log_path.unlink()
        result = run.finish(process, cancel, log_text)
        self.log.info("run done outcome=%s", result.outcome)
        return result


__all__ = ["MAX_DIAGNOSTICS", "PROMPT_LIMIT_BYTES", "AgyDriver", "AgyRun"]
