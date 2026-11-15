"""Codex CLI driver and JSONL stream parser."""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, ClassVar, Literal

from .._args import reject_conflicts, reject_transport_flags, reject_unsupported
from .._classify import keyword_code
from ..driver import CliDriver, Facts, Launch, Parser, RequestError
from ..events import (
    Event,
    FileChanged,
    MessageCompleted,
    MessageStarted,
    Notice,
    ReasoningDelta,
    SessionIdentified,
    TextDelta,
    ToolCompleted,
    ToolStarted,
    UsageUpdated,
)
from ..types import Diagnostic, ErrorCode, Request, Usage
from ._common import as_int

_OWNED_FLAGS = ("--json",)
_SUPPORTED_OPTIONS = ("model", "reasoning")
_TOML_ESCAPES = {'"': '\\"', "\\": "\\\\", "\b": "\\b", "\t": "\\t", "\n": "\\n", "\f": "\\f", "\r": "\\r"}
_FILE_CHANGE_MAP: dict[str, Literal["added", "modified", "deleted"]] = {
    "add": "added",
    "update": "modified",
    "delete": "deleted",
}


def _toml_string(value: str) -> str:
    """Return ``value`` as a TOML basic string with control characters escaped."""
    parts: list[str] = []
    for char in value:
        if char in _TOML_ESCAPES:
            parts.append(_TOML_ESCAPES[char])
        elif ord(char) < 0x20 or ord(char) == 0x7F:
            parts.append(f"\\u{ord(char):04X}")
        else:
            parts.append(char)
    return f'"{"".join(parts)}"'


def _cd_flag(args: tuple[str, ...]) -> str | None:
    """Return the first raw arg setting the working directory, or ``None``."""
    for token in args:
        if token == "--cd" or token.startswith("--cd="):
            return token
        if token.startswith("-C") and not token.startswith("--"):
            return token
    return None


def _is_reasoning_override(assignment: str) -> bool:
    key, sep, _ = assignment.partition("=")
    return bool(sep) and key.strip() == "model_reasoning_effort"


def _has_reasoning_conflict(args: tuple[str, ...]) -> bool:
    """Return ``True`` when ``args`` sets ``model_reasoning_effort=...`` via ``-c`` or ``--config``."""
    for index, token in enumerate(args):
        if token in ("-c", "--config"):
            if index + 1 < len(args) and _is_reasoning_override(args[index + 1]):
                return True
        elif token.startswith(("-c=", "--config=")):
            _, _, rhs = token.partition("=")
            if _is_reasoning_override(rhs):
                return True
        elif token.startswith("-c") and _is_reasoning_override(token[2:]):
            return True
    return False


def _raise_if_diagnostic(error: Diagnostic | None) -> None:
    if error is not None:
        raise RequestError(error.code, error.message)


def _entity_id(item: Mapping[str, Any]) -> str | None:
    raw = item.get("id")
    return raw if isinstance(raw, str) and raw else None


def _classify_error_message(message: str) -> Diagnostic:
    code = keyword_code(message)
    if code is None:
        lowered = message.lower()
        # codex/0.169.0/invalid_model.stdout.jsonl:6-7: "The '...' model is not supported..."
        if "model" in lowered and "not supported" in lowered:
            code = ErrorCode.INVALID_MODEL
        else:
            code = ErrorCode.EXECUTION_ERROR
    return Diagnostic(code=code, message=message)


def _error_precedence(error: Diagnostic) -> int:
    return {
        ErrorCode.AUTH: 5,
        ErrorCode.INVALID_MODEL: 4,
        ErrorCode.CONTEXT_OVERFLOW: 4,
        ErrorCode.SERVICE_ERROR: 3,
    }.get(error.code, 1)


def _tool_status(item: Mapping[str, Any], *, exit_code: int | None = None) -> Literal["completed", "error", "unknown"]:
    if exit_code is not None:
        return "completed" if exit_code == 0 else "error"
    if item.get("type") == "command_execution":
        return "unknown"
    status = item.get("status")
    if status == "completed":
        return "completed"
    if status == "failed":
        return "error"
    return "unknown"


def _file_changes(item: Mapping[str, Any], entity_id: str | None) -> list[Event]:
    changes = item.get("changes")
    if not isinstance(changes, list):
        return []
    events: list[Event] = []
    for entry in changes:
        if not isinstance(entry, dict):
            continue
        path = entry.get("path")
        if not isinstance(path, str) or not path:
            continue
        kind = entry.get("kind")
        # https://github.com/openai/codex/blob/main/sdk/typescript/src/items.ts defines add/delete/update.
        change: Literal["added", "modified", "deleted", "unknown"] = (
            _FILE_CHANGE_MAP[kind] if isinstance(kind, str) and kind in _FILE_CHANGE_MAP else "unknown"
        )
        events.append(FileChanged(path=path, change=change, evidence="reported", entity_id=entity_id))
    return events


class _CodexParser:
    """Line-by-line parser for ``codex exec --json`` JSONL output."""

    def __init__(self) -> None:
        self._session_id: str | None = None
        self._usage = Usage()
        self._error: Diagnostic | None = None
        self._error_lines: list[str] = []
        self._started_tools: set[str] = set()
        self._unkeyed_started_tools: dict[str, int] = {}

    def feed_json(self, obj: dict[str, Any]) -> Iterable[Event]:
        event_type = obj.get("type")
        if event_type == "thread.started":
            thread_id = obj.get("thread_id")
            if isinstance(thread_id, str) and thread_id:
                self._session_id = thread_id
                return (SessionIdentified(session_id=thread_id, entity_id=None),)
            return ()

        if event_type == "turn.completed":
            self._error = None
            return self._handle_usage(obj.get("usage"))

        if event_type == "error":
            message = obj.get("message")
            if isinstance(message, str) and message:
                self._record_error(_classify_error_message(message))
            return ()

        if event_type == "turn.failed":
            err = obj.get("error")
            if isinstance(err, dict):
                message = err.get("message")
                if isinstance(message, str) and message:
                    self._record_error(_classify_error_message(message))
            return ()

        if event_type == "item.started":
            item = obj.get("item")
            return self._handle_item_started(item) if isinstance(item, dict) else ()

        if event_type == "item.completed":
            item = obj.get("item")
            return self._handle_item_completed(item) if isinstance(item, dict) else ()

        return ()

    def _record_error(self, error: Diagnostic) -> None:
        if self._error is None or _error_precedence(error) > _error_precedence(self._error):
            self._error = error

    def feed_text(self, line: str, *, stream: Literal["stdout", "stderr"]) -> Iterable[Event]:
        if stream == "stdout" and line.strip():
            self._error_lines.append(line.strip())
        return ()

    def finish(self, read_back: Mapping[Path, bytes]) -> Facts:
        return Facts(
            session_id=self._session_id,
            model=None,
            usage=self._usage,
            num_turns=None,
            error=self._error,
            error_text="\n".join(self._error_lines),
        )

    def _handle_usage(self, raw: Any) -> Iterable[Event]:
        if not isinstance(raw, dict):
            return ()
        input_tokens = as_int(raw.get("input_tokens"))
        output_tokens = as_int(raw.get("output_tokens"))
        cache_read = as_int(raw.get("cached_input_tokens"))
        cache_write = as_int(raw.get("cache_write_input_tokens"))
        if all(value is None for value in (input_tokens, output_tokens, cache_read, cache_write)):
            return ()
        self._usage = Usage(
            input=input_tokens,
            output=output_tokens,
            cache_read=cache_read,
            cache_write=cache_write,
        )
        return (UsageUpdated(usage=self._usage),)

    def _handle_item_started(self, item: dict[str, Any]) -> Iterable[Event]:
        item_type = item.get("type")
        entity_id = _entity_id(item)
        if item_type == "command_execution":
            self._track_tool_start(item_type, entity_id)
            command = item.get("command")
            return (
                ToolStarted(
                    tool="command_execution",
                    activity="command",
                    input={"command": command} if isinstance(command, str) else None,
                    entity_id=entity_id,
                ),
            )
        if item_type == "file_change":
            self._track_tool_start(item_type, entity_id)
            changes = item.get("changes")
            return (
                ToolStarted(
                    tool="file_change",
                    activity="tool_call",
                    input={"changes": changes} if isinstance(changes, list) else None,
                    entity_id=entity_id,
                ),
            )
        if item_type in ("mcp_tool_call", "web_search"):
            tool = self._tool_name(item, item_type)
            if tool is None:
                return ()
            self._track_tool_start(item_type, entity_id)
            return (ToolStarted(tool=str(tool), activity="tool_call", input=None, entity_id=entity_id),)
        return ()

    def _track_tool_start(self, item_type: str, entity_id: str | None) -> None:
        if entity_id is not None:
            self._started_tools.add(entity_id)
        else:
            self._unkeyed_started_tools[item_type] = self._unkeyed_started_tools.get(item_type, 0) + 1

    def _take_start(self, item_type: str, entity_id: str | None) -> bool:
        if entity_id is not None:
            if entity_id in self._started_tools:
                self._started_tools.remove(entity_id)
                return True
            return False
        count = self._unkeyed_started_tools.get(item_type, 0)
        if not count:
            return False
        if count == 1:
            del self._unkeyed_started_tools[item_type]
        else:
            self._unkeyed_started_tools[item_type] = count - 1
        return True

    def _take_or_synthesize_start(self, item: dict[str, Any], item_type: str, entity_id: str | None) -> list[Event]:
        if self._take_start(item_type, entity_id):
            return []
        events = list(self._handle_item_started(item))
        self._take_start(item_type, entity_id)
        return events

    @staticmethod
    def _tool_name(item: Mapping[str, Any], item_type: str) -> str | None:
        if item_type == "mcp_tool_call":
            # https://github.com/openai/codex/blob/main/codex-rs/exec/src/exec_events.rs defines McpToolCallItem.tool.
            tool = item.get("tool")
            return tool if isinstance(tool, str) and tool else None
        return item_type

    def _handle_item_completed(self, item: dict[str, Any]) -> Iterable[Event]:
        item_type = item.get("type")
        entity_id = _entity_id(item)

        if item_type == "agent_message":
            text = item.get("text")
            if not isinstance(text, str):
                return ()
            events: list[Event] = [MessageStarted(entity_id=entity_id)]
            events.append(TextDelta(text=text, entity_id=entity_id))
            events.append(MessageCompleted(entity_id=entity_id))
            return events

        if item_type == "reasoning":
            text = item.get("text")
            return (ReasoningDelta(text=text, entity_id=entity_id),) if isinstance(text, str) and text else ()

        if item_type == "error":
            message = item.get("message")
            return (Notice(code="error", message=message, entity_id=entity_id),) if isinstance(message, str) else ()

        if item_type == "command_execution":
            events = self._take_or_synthesize_start(item, item_type, entity_id)
            exit_code = as_int(item.get("exit_code"))
            output = item.get("aggregated_output")
            events.append(
                ToolCompleted(
                    tool="command_execution",
                    status=_tool_status(item, exit_code=exit_code),
                    output=output if isinstance(output, str) else None,
                    entity_id=entity_id,
                )
            )
            return events

        if item_type == "file_change":
            events = self._take_or_synthesize_start(item, item_type, entity_id)
            events.append(
                ToolCompleted(
                    tool="file_change",
                    status=_tool_status(item),
                    output=None,
                    entity_id=entity_id,
                )
            )
            events.extend(_file_changes(item, entity_id))
            return events

        if item_type in ("mcp_tool_call", "web_search"):
            tool = self._tool_name(item, item_type)
            if tool is None:
                return ()
            events = self._take_or_synthesize_start(item, item_type, entity_id)
            output = item.get("result")
            events.append(
                ToolCompleted(
                    tool=str(tool),
                    status=_tool_status(item),
                    output=output,
                    entity_id=entity_id,
                )
            )
            return events

        return ()


class CodexDriver(CliDriver):
    """Runs ``codex exec`` with the prompt on stdin, in ``Request.cwd``.

    The driver never adds an approval or sandbox flag.
    """

    name: ClassVar[str] = "codex"
    executable: ClassVar[str] = "codex"

    def launch(self, req: Request, exe: Path, tmp: Path) -> Launch:
        _raise_if_diagnostic(reject_unsupported(req, _SUPPORTED_OPTIONS))
        _raise_if_diagnostic(reject_transport_flags(req.args, _OWNED_FLAGS))
        _raise_if_diagnostic(reject_conflicts(req, {"model": ("--model", "-m")}))
        if req.model is not None and any(arg.startswith("-m") and len(arg) > 2 for arg in req.args):
            raise RequestError(ErrorCode.INVALID_ARGS, "args flag '-m' conflicts with the model option")
        cd_token = _cd_flag(req.args)
        if cd_token is not None:
            raise RequestError(
                ErrorCode.INVALID_ARGS,
                f"args cannot set the working directory ({cd_token!r}); use Request.cwd",
            )
        if req.reasoning is not None and _has_reasoning_conflict(req.args):
            raise RequestError(
                ErrorCode.INVALID_ARGS,
                "args config 'model_reasoning_effort' conflicts with the reasoning option",
            )

        argv: list[str] = [os.fspath(exe), "exec", "--json"]
        if req.model is not None:
            argv.extend(["--model", req.model])
        if req.reasoning is not None:
            argv.extend(["-c", f"model_reasoning_effort={_toml_string(req.reasoning)}"])
        argv.extend(req.args)
        if req.session_id:
            # Options go before `resume`: the subcommand rejects exec options such as --profile and --sandbox.
            argv.extend(["resume", req.session_id])
        argv.append("-")
        return Launch(argv=tuple(argv), stdin=req.prompt.encode("utf-8"))

    def parser(self, req: Request) -> Parser:
        return _CodexParser()


__all__ = ["CodexDriver"]
