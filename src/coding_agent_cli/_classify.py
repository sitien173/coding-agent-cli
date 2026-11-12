"""Map facts a driver collected about one run to an ``Outcome`` and ``Diagnostic``.

Rows follow the design's outcome precedence; the first matching row wins.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .types import Diagnostic, Outcome

REQUEST_CODES = frozenset({"bad_cwd", "invalid_args", "unsupported_option", "invalid_prompt"})
SPAWN_CODES = frozenset({"missing_cli", "spawn_failed"})
CLI_CODES = ("auth", "invalid_model", "context_overflow", "service_error")

_KEYWORDS: dict[str, tuple[str, ...]] = {
    "auth": (
        "authentication",
        "unauthorized",
        "forbidden",
        "api key",
        "not logged in",
        "not logged",
        "login required",
        "auth_unavailable",
    ),
    "invalid_model": ("invalid model", "unknown model", "not a valid model"),
    "context_overflow": ("context_length_exceeded",),
    "service_error": (
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


def _normalize(text: str) -> str:
    return text.lower().replace("node_tls_reject_unauthorized", "")


def keyword_code(text: str) -> str:
    """Return the first CLI code whose keywords appear in ``text``, in precedence order, or ""."""
    lowered = _normalize(text)
    if not lowered.strip():
        return ""
    for code in CLI_CODES:
        if code == "context_overflow":
            if _CONTEXT_OVERFLOW_RE.search(lowered):
                return code
        elif any(token in lowered for token in _KEYWORDS[code]):
            return code
    return ""


def http_status_code(status: int | None) -> str:
    """Map a CLI-reported HTTP status to a CLI code, or ""."""
    if status in (401, 403):
        return "auth"
    if status == 429 or (status is not None and 500 <= status <= 599):
        return "service_error"
    return ""


@dataclass(slots=True, kw_only=True)
class Facts:
    """What a driver observed. ``cli_error`` is structured CLI error data; ``diagnostics`` is free text."""

    text: str = ""
    session_id: str | None = None
    cancelled: bool = False
    request_error: Diagnostic | None = None
    process_error: str = ""
    process_message: str = ""
    cli_error: Diagnostic | None = None
    diagnostics: str = ""
    returncode: int | None = None


@dataclass(frozen=True, slots=True)
class Classification:
    outcome: Outcome
    error: Diagnostic | None
    warnings: tuple[Diagnostic, ...] = ()


_CLI_OUTCOMES = {
    "auth": Outcome.AGENT_FATAL,
    "invalid_model": Outcome.REQUEST_FATAL,
    "context_overflow": Outcome.REQUEST_FATAL,
    "service_error": Outcome.RETRYABLE,
}


def _cli_diagnostic(facts: Facts) -> Diagnostic | None:
    """Structured CLI error data first, then keyword matching on diagnostics."""
    if facts.cli_error is not None and facts.cli_error.code in _CLI_OUTCOMES:
        return facts.cli_error
    code = keyword_code(facts.diagnostics)
    if code:
        return Diagnostic(code=code, message=facts.diagnostics.strip())
    if facts.cli_error is not None:
        code = keyword_code(facts.cli_error.message)
        if code:
            return Diagnostic(code=code, message=facts.cli_error.message)
    return None


def _message(*parts: str) -> str:
    return "\n".join(part.strip() for part in parts if part and part.strip())


def classify(facts: Facts) -> Classification:
    # Row 1
    if facts.cancelled or facts.process_error == "cancelled":
        return Classification(Outcome.CANCELLED, Diagnostic(code="cancelled", message="run cancelled"))
    # Rows 2 and 3
    if facts.request_error is not None:
        return Classification(Outcome.REQUEST_FATAL, facts.request_error)
    if facts.process_error in REQUEST_CODES:
        return Classification(Outcome.REQUEST_FATAL, Diagnostic(code=facts.process_error, message=facts.process_message))
    # Row 4
    if facts.process_error in SPAWN_CODES:
        return Classification(Outcome.AGENT_FATAL, Diagnostic(code=facts.process_error, message=facts.process_message))
    # Rows 5 to 7, and 9 after the timeout row
    cli = _cli_diagnostic(facts)
    if cli is not None and cli.code != "service_error":
        return Classification(_CLI_OUTCOMES[cli.code], cli)
    # Row 8
    if facts.process_error == "timeout":
        return Classification(Outcome.RETRYABLE, Diagnostic(code="timeout", message=facts.process_message or "run timed out"))
    if cli is not None:
        return Classification(Outcome.RETRYABLE, cli)
    # Row 10
    if facts.process_error in {"execution_error", "stdin_failed"}:
        message = _message(facts.process_message, facts.diagnostics if not facts.text.strip() else "")
        return Classification(Outcome.RETRYABLE, Diagnostic(code=facts.process_error, message=message))
    if facts.cli_error is not None:
        return Classification(Outcome.RETRYABLE, Diagnostic(code="execution_error", message=facts.cli_error.message))
    if facts.returncode:
        return Classification(
            Outcome.RETRYABLE,
            Diagnostic(code="execution_error", message=_message(f"command exited with status {facts.returncode}", facts.diagnostics)),
        )
    if not facts.text.strip():
        return Classification(
            Outcome.RETRYABLE,
            Diagnostic(code="no_output", message=_message("the CLI produced no assistant text", facts.diagnostics)),
        )
    # Row 11
    if not facts.session_id:
        return Classification(Outcome.SUCCESS, None, (Diagnostic(code="no_session_id", message="the CLI reported no session ID"),))
    return Classification(Outcome.SUCCESS, None)


__all__ = ["Classification", "Facts", "classify", "http_status_code", "keyword_code"]
