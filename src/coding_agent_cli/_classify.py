"""Map transport ``RunFacts`` and driver ``Facts`` to a classified ``Result``.

Rows follow the design's outcome precedence; the first matching row wins.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from .driver import Facts
from .types import Diagnostic, ErrorCode, Outcome, Result

StopCause = Literal["exited", "cancelled", "timeout", "idle_timeout"]

_CLI_KEYWORD_ORDER: tuple[ErrorCode, ...] = (
    ErrorCode.AUTH,
    ErrorCode.INVALID_MODEL,
    ErrorCode.CONTEXT_OVERFLOW,
    ErrorCode.SERVICE_ERROR,
)

_KEYWORDS: dict[ErrorCode, tuple[str, ...]] = {
    ErrorCode.AUTH: (
        "authentication",
        "unauthorized",
        "forbidden",
        "api key",
        "not logged in",
        "not logged",
        "login required",
        "auth_unavailable",
    ),
    ErrorCode.INVALID_MODEL: ("invalid model", "unknown model", "not a valid model"),
    ErrorCode.CONTEXT_OVERFLOW: ("context_length_exceeded",),
    ErrorCode.SERVICE_ERROR: (
        "overloaded",
        "rate limit",
        "rate_limit",
        "too many requests",
        "internal server error",
        "service unavailable",
        "bad gateway",
    ),
}
_CONTEXT_OVERFLOW_RE = re.compile(r"\bcontext_length_exceeded\b")
_MODEL_NOT_FOUND_RE = re.compile(r'\bmodel\s+"[^"]+"\s+not\s+found\b')
_HTTP_STATUS_RE = re.compile(
    r"""\b(?:http(?:/\d(?:\.\d)?)?(?:\s+error)?|status(?:\s+code)?)\s*[:=]?\s*"?([1-5]\d{2})\b"""
    r"""|"(?:status|status_code|statusCode)"\s*:\s*(\d{3})\b""",
    re.IGNORECASE,
)


def _normalize(text: str) -> str:
    return text.lower().replace("node_tls_reject_unauthorized", "")


def _http_codes_from_text(text: str) -> set[ErrorCode]:
    codes = (http_status_code(int(m.group(1) or m.group(2))) for m in _HTTP_STATUS_RE.finditer(text))
    return {code for code in codes if code is not None}


def keyword_code(text: str) -> ErrorCode | None:
    """Return the first CLI ``ErrorCode`` whose keywords appear in ``text``, in precedence order, or ``None``."""
    lowered = _normalize(text)
    if not lowered.strip():
        return None
    http_codes = _http_codes_from_text(lowered)
    for code in _CLI_KEYWORD_ORDER:
        if code == ErrorCode.CONTEXT_OVERFLOW:
            if _CONTEXT_OVERFLOW_RE.search(lowered):
                return code
        elif code == ErrorCode.INVALID_MODEL and _MODEL_NOT_FOUND_RE.search(lowered):
            return code
        elif any(token in lowered for token in _KEYWORDS[code]) or code in http_codes:
            return code
    return None


def http_status_code(status: int | None) -> ErrorCode | None:
    """Map a CLI-reported HTTP status to an ``ErrorCode``, or ``None``."""
    if status in (401, 403):
        return ErrorCode.AUTH
    if status == 429 or (status is not None and 500 <= status <= 599):
        return ErrorCode.SERVICE_ERROR
    return None


@dataclass(frozen=True, slots=True, kw_only=True)
class RunFacts:
    """Transport and supervision facts collected for one run."""

    stop_cause: StopCause = "exited"
    returncode: int | None = None
    stderr_tail: str = ""
    request_error: Diagnostic | None = None
    spawn_error: Diagnostic | None = None
    stdin_failed: str | None = None
    parser_error: str | None = None
    cleanup_incomplete: bool = False
    line_too_long: bool = False
    duration_s: float = 0.0


def _message(*parts: str) -> str:
    return "\n".join(part.strip() for part in parts if part and part.strip())


def _cli_diagnostic(run: RunFacts, facts: Facts) -> Diagnostic | None:
    """Return structured ``Facts.error`` if set, else keyword-match ``Facts.error_text`` and ``stderr_tail``."""
    if facts.error is not None:
        return facts.error
    combined = _message(facts.error_text, run.stderr_tail)
    code = keyword_code(combined)
    if code is not None:
        return Diagnostic(code=code, message=combined)
    return None


def _build_warnings(run: RunFacts, extra: tuple[Diagnostic, ...] = ()) -> tuple[Diagnostic, ...]:
    warnings: list[Diagnostic] = []
    if run.line_too_long:
        warnings.append(
            Diagnostic(code=ErrorCode.LINE_TOO_LONG, message="a line exceeded max_line_bytes and was discarded")
        )
    if run.cleanup_incomplete:
        warnings.append(
            Diagnostic(
                code=ErrorCode.CLEANUP_INCOMPLETE,
                message="process cleanup did not complete before deadline",
            )
        )
    warnings.extend(extra)
    return tuple(warnings)


def _result(
    run: RunFacts,
    facts: Facts,
    *,
    text: str,
    outcome: Outcome,
    error: Diagnostic | None,
    extra_warnings: tuple[Diagnostic, ...] = (),
) -> Result:
    return Result(
        outcome=outcome,
        text=text,
        session_id=facts.session_id or None,
        model=facts.model,
        usage=facts.usage,
        cost_usd=facts.cost_usd,
        duration_s=run.duration_s,
        num_turns=facts.num_turns,
        stop_reason=facts.stop_reason,
        returncode=run.returncode,
        error=error,
        warnings=_build_warnings(run, extra_warnings),
    )


def classify(run: RunFacts, facts: Facts, *, text: str) -> Result:
    """Classify one run into a ``Result`` according to the design's 12-row precedence table."""
    result_text = facts.final_text if facts.final_text is not None else text

    # Row 1: Latched stop cause is cancelled
    if run.stop_cause == "cancelled":
        return _result(
            run,
            facts,
            text=result_text,
            outcome=Outcome.CANCELLED,
            error=Diagnostic(code=ErrorCode.CANCELLED, message="run cancelled"),
        )
    # Row 2: Validation failure or RequestError
    if run.request_error is not None:
        return _result(run, facts, text=result_text, outcome=Outcome.REQUEST_FATAL, error=run.request_error)
    # Row 3: Executable missing or spawn denied
    if run.spawn_error is not None:
        return _result(run, facts, text=result_text, outcome=Outcome.AGENT_FATAL, error=run.spawn_error)

    cli = _cli_diagnostic(run, facts)

    # Row 4: Auth failure
    if cli is not None and cli.code == ErrorCode.AUTH:
        return _result(run, facts, text=result_text, outcome=Outcome.AGENT_FATAL, error=cli)
    # Row 5: Invalid model or context overflow
    if cli is not None and cli.code in (ErrorCode.INVALID_MODEL, ErrorCode.CONTEXT_OVERFLOW):
        return _result(run, facts, text=result_text, outcome=Outcome.REQUEST_FATAL, error=cli)
    # Row 6: Parser raised
    if run.parser_error is not None:
        return _result(
            run,
            facts,
            text=result_text,
            outcome=Outcome.AGENT_FATAL,
            error=Diagnostic(code=ErrorCode.PARSER_ERROR, message=run.parser_error),
        )
    # Row 7: CLI reports a caller-set limit
    if cli is not None and cli.code in (ErrorCode.MAX_TURNS, ErrorCode.BUDGET):
        return _result(run, facts, text=result_text, outcome=Outcome.LIMIT_REACHED, error=cli)
    # Row 8: Latched stop cause is a deadline
    if run.stop_cause in ("timeout", "idle_timeout"):
        code = ErrorCode.TIMEOUT if run.stop_cause == "timeout" else ErrorCode.IDLE_TIMEOUT
        msg = "run timed out" if code == ErrorCode.TIMEOUT else "run idle-timed out"
        return _result(
            run,
            facts,
            text=result_text,
            outcome=Outcome.RETRYABLE,
            error=Diagnostic(code=code, message=msg),
        )
    # Row 9: HTTP 5xx, overload, or rate limit
    if cli is not None and cli.code == ErrorCode.SERVICE_ERROR:
        return _result(run, facts, text=result_text, outcome=Outcome.RETRYABLE, error=cli)
    # Row 10: Stdin failure, nonzero exit, or structured failure
    if run.stdin_failed is not None:
        return _result(
            run,
            facts,
            text=result_text,
            outcome=Outcome.RETRYABLE,
            error=Diagnostic(
                code=ErrorCode.STDIN_FAILED, message=run.stdin_failed or "failed to write prompt to stdin"
            ),
        )
    if facts.error is not None:
        return _result(run, facts, text=result_text, outcome=Outcome.RETRYABLE, error=facts.error)
    if run.returncode:
        return _result(
            run,
            facts,
            text=result_text,
            outcome=Outcome.RETRYABLE,
            error=Diagnostic(
                code=ErrorCode.EXECUTION_ERROR,
                message=_message(f"command exited with status {run.returncode}", facts.error_text, run.stderr_tail),
            ),
        )
    # Row 11: No text
    if not result_text.strip():
        return _result(
            run,
            facts,
            text=result_text,
            outcome=Outcome.RETRYABLE,
            error=Diagnostic(
                code=ErrorCode.NO_OUTPUT,
                message=_message("the CLI produced no assistant text", facts.error_text, run.stderr_tail),
            ),
        )
    # Row 12: Otherwise
    extra: tuple[Diagnostic, ...] = ()
    if not facts.session_id:
        extra = (Diagnostic(code=ErrorCode.NO_SESSION_ID, message="the CLI reported no session ID"),)
    return _result(run, facts, text=result_text, outcome=Outcome.SUCCESS, error=None, extra_warnings=extra)


__all__ = ["RunFacts", "StopCause", "classify", "http_status_code", "keyword_code"]
