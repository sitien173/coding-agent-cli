"""Public extension API for coding-agent CLI drivers."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, ClassVar, Literal, Protocol, cast, final

from ._args import validate_request
from ._transport import ParserFailure, TransportOutcome, run_transport
from .events import Event, MessageCompleted, MessageStarted, RawEvent, RunCompleted, TextDelta
from .types import Capabilities, Diagnostic, ErrorCode, Request, Result, Usage

_check_cache: dict[tuple[Path, int], Capabilities] = {}


@dataclass(frozen=True, slots=True, kw_only=True)
class Launch:
    argv: tuple[str, ...]
    stdin: bytes | None = None
    read_back: tuple[Path, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class Facts:
    final_text: str | None = None
    session_id: str | None = None
    model: str | None = None
    usage: Usage = Usage()
    cost_usd: float | None = None
    num_turns: int | None = None
    stop_reason: str | None = None
    error: Diagnostic | None = None
    error_text: str = ""


class Parser(Protocol):
    def feed_json(self, obj: dict[str, Any]) -> Iterable[Event]: ...

    def feed_text(self, line: str, *, stream: Literal["stdout", "stderr"]) -> Iterable[Event]: ...

    def finish(self, read_back: Mapping[Path, bytes]) -> Facts: ...


class RequestError(Exception):
    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class _CallerFailure(Exception):
    def __init__(self, original: ParserFailure) -> None:
        super().__init__(original)
        self.original = original


class CliDriver(ABC):
    name: ClassVar[str]
    executable: ClassVar[str]
    max_line_bytes: ClassVar[int] = 16 * 1024 * 1024

    @abstractmethod
    def launch(self, req: Request, exe: Path, tmp: Path) -> Launch: ...

    @abstractmethod
    def parser(self, req: Request) -> Parser: ...

    @final
    async def check(
        self,
        *,
        executable: str | None = None,
        env: Mapping[str, str] | None = None,
        refresh: bool = False,
    ) -> Capabilities:
        name = self.executable if executable is None else executable
        resolved = shutil.which(name, path=(env or {}).get("PATH", os.environ.get("PATH", "")))
        if resolved is None:
            return Capabilities(available=False)
        path = Path(resolved).resolve()
        try:
            mtime = path.stat().st_mtime_ns
        except OSError:
            return Capabilities(available=False)
        key = (path, mtime)
        if not refresh and key in _check_cache:
            return _check_cache[key]
        version = ""
        success = True
        for arg in ("--version", "--help"):
            output: list[str] = []
            errors: list[str] = []

            async def collect(
                stream: str, line: str, *, stdout: list[str] = output, stderr: list[str] = errors
            ) -> None:
                (stdout if stream == "stdout" else stderr).append(line)

            async with asyncio.timeout(16):
                outcome = await run_transport([str(path), arg], collect, env=env, timeout_s=5)
            if outcome.stop_cause != "exited" or outcome.returncode != 0 or outcome.spawn_failure is not None:
                success = False
                break
            if arg == "--version":
                version = output[0] if output else errors[0] if errors else ""
        capabilities = Capabilities(
            available=success,
            executable=path,
            version=version or None,
            structured_streaming=True if success else None,
        )
        if success:
            _check_cache[key] = capabilities
        return capabilities

    @final
    async def run(
        self,
        req: Request,
        *,
        on_event: Callable[[Event], Awaitable[None] | None] | None = None,
        cancel: asyncio.Event | None = None,
        on_spawn: Callable[[int], Awaitable[None] | None] | None = None,
    ) -> Result:
        """Run one request with supervised process and temporary-file cleanup."""
        from ._classify import RunFacts, classify

        error = validate_request(req)
        if error is not None:
            return await self._complete(classify(RunFacts(request_error=error), Facts(), text=""), on_event)
        if cancel is not None and cancel.is_set():
            return await self._complete(classify(RunFacts(stop_cause="cancelled"), Facts(), text=""), on_event)
        exe_name = self.executable if req.executable is None else req.executable
        executable = shutil.which(exe_name, path=req.env.get("PATH", os.environ.get("PATH", "")))
        if executable is None:
            spawn = Diagnostic(code=ErrorCode.MISSING_CLI, message=f"executable {exe_name!r} was not found")
            return await self._complete(classify(RunFacts(spawn_error=spawn), Facts(), text=""), on_event)

        tmp = Path(tempfile.mkdtemp(prefix="coding-agent-cli-"))
        outcome: TransportOutcome | None = None
        parser: Parser | None = None
        launch: Launch | None = None
        facts = Facts()
        current: list[str] = []
        open_messages: list[str | None] = []
        sequence = 0
        parser_error: str | None = None
        request_error: Diagnostic | None = None
        cleanup_failed = False
        read_back: dict[Path, bytes] = {}

        def record_cleanup_error(function: Callable[..., Any], path: str, error: BaseException) -> None:
            nonlocal cleanup_failed
            cleanup_failed = True

        def remove_tmp() -> None:
            if tmp.exists():
                shutil.rmtree(tmp, onexc=record_cleanup_error)

        def cleanup() -> None:
            if launch is not None:
                for path in launch.read_back:
                    try:
                        read_back[path] = path.read_bytes()
                    except OSError:
                        continue
            remove_tmp()

        async def emit(event: Event) -> None:
            nonlocal sequence
            sequence += 1
            if on_event is not None:
                result = on_event(replace(event, sequence=sequence))
                if result is not None:
                    await result

        try:
            try:
                launch = self.launch(req, Path(executable), tmp)
            except RequestError as exc:
                request_error = Diagnostic(code=exc.code, message=exc.message)
                outcome = TransportOutcome(stop_cause="exited")
            else:
                assert launch is not None
                try:
                    parser = self.parser(req)
                except Exception as exc:
                    parser_error = f"{type(exc).__name__}: {exc}"
                    outcome = TransportOutcome(stop_cause="exited")
                else:

                    async def deliver(event: Event) -> None:
                        # The transport treats ParserFailure as parser-origin, so hide caller ones from it.
                        try:
                            await emit(event)
                        except ParserFailure as exc:
                            raise _CallerFailure(exc) from exc

                    async def on_line(stream: Literal["stdout", "stderr"], line: str) -> None:
                        nonlocal current, parser_error
                        obj: Any = None
                        is_json = False
                        if stream == "stdout":
                            try:
                                obj = json.loads(line)
                                is_json = isinstance(obj, dict)
                            except json.JSONDecodeError:
                                pass
                        if req.raw_events and is_json:
                            await deliver(RawEvent(stream="stdout", data=obj))
                        try:
                            produced = parser.feed_json(obj) if is_json else parser.feed_text(line, stream=stream)
                            iterator = iter(produced)
                        except Exception as exc:
                            parser_error = f"{type(exc).__name__}: {exc}"
                            raise ParserFailure(parser_error) from exc
                        while True:
                            try:
                                event = next(iterator)
                            except StopIteration:
                                break
                            except Exception as exc:
                                parser_error = f"{type(exc).__name__}: {exc}"
                                raise ParserFailure(parser_error) from exc
                            if isinstance(event, RunCompleted):
                                parser_error = "parser must not emit RunCompleted"
                                raise ParserFailure(parser_error)
                            if isinstance(event, MessageStarted):
                                current = []
                                open_messages.append(event.entity_id)
                            elif isinstance(event, MessageCompleted):
                                try:
                                    open_messages.remove(event.entity_id)
                                except ValueError:
                                    pass
                            elif isinstance(event, TextDelta):
                                current.append(event.text)
                            await deliver(event)

                    try:
                        outcome = await run_transport(
                            list(launch.argv),
                            on_line,
                            stdin=launch.stdin,
                            env=req.env,
                            cwd=str(req.cwd),
                            max_line_bytes=self.max_line_bytes,
                            timeout_s=req.timeout_s,
                            idle_timeout_s=req.idle_timeout_s,
                            cancel=cancel,
                            on_spawn=on_spawn,
                            cleanup_timeout_s=3.0,
                            cleanup_hook=cleanup,
                        )
                    except _CallerFailure as exc:
                        raise exc.original from None
                    try:
                        facts = parser.finish(read_back)
                    except Exception as exc:
                        parser_error = f"{type(exc).__name__}: {exc}"
                    for entity_id in open_messages:
                        await emit(MessageCompleted(entity_id=entity_id))
        finally:
            remove_tmp()

        text = "".join(current)
        assert outcome is not None
        run_facts = RunFacts(
            stop_cause=cast(Literal["exited", "cancelled", "timeout", "idle_timeout"], outcome.stop_cause or "exited"),
            returncode=outcome.returncode,
            stderr_tail=outcome.stderr_tail,
            request_error=request_error,
            spawn_error=(
                Diagnostic(code=ErrorCode(outcome.spawn_failure), message=outcome.spawn_failure_message)
                if outcome.spawn_failure
                else None
            ),
            stdin_failed=outcome.stdin_failure_message if outcome.stdin_failure else None,
            parser_error=parser_error or outcome.parser_failure_message or None,
            cleanup_incomplete=cleanup_failed or outcome.cleanup_incomplete,
            line_too_long=outcome.line_too_long,
            duration_s=outcome.duration_s,
        )
        result = classify(run_facts, facts, text=text)
        return await self._complete(result, on_event, sequence)

    @staticmethod
    async def _complete(
        result: Result,
        on_event: Callable[[Event], Awaitable[None] | None] | None,
        sequence: int = 0,
    ) -> Result:
        if on_event is not None:
            emitted = on_event(RunCompleted(result=result, sequence=sequence + 1))
            if emitted is not None:
                await emitted
        return result


__all__ = ["CliDriver", "Facts", "Launch", "Parser", "RequestError"]
