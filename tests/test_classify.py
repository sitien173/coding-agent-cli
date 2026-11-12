from __future__ import annotations

import pytest

from coding_agent_cli import Diagnostic, Outcome
from coding_agent_cli._classify import Facts, classify, http_status_code

D = Diagnostic

ROWS = [
    # (facts, outcome, code)
    (Facts(cancelled=True, request_error=D(code="bad_cwd", message="x"), process_error="timeout"), Outcome.CANCELLED, "cancelled"),
    (Facts(process_error="cancelled"), Outcome.CANCELLED, "cancelled"),
    (Facts(request_error=D(code="bad_cwd", message="x"), process_error="missing_cli"), Outcome.REQUEST_FATAL, "bad_cwd"),
    (Facts(request_error=D(code="invalid_args", message="x")), Outcome.REQUEST_FATAL, "invalid_args"),
    (Facts(request_error=D(code="unsupported_option", message="x")), Outcome.REQUEST_FATAL, "unsupported_option"),
    (Facts(process_error="invalid_prompt"), Outcome.REQUEST_FATAL, "invalid_prompt"),
    (Facts(process_error="missing_cli"), Outcome.AGENT_FATAL, "missing_cli"),
    (Facts(process_error="spawn_failed"), Outcome.AGENT_FATAL, "spawn_failed"),
    (Facts(diagnostics="Error: not logged in", process_error="timeout"), Outcome.AGENT_FATAL, "auth"),
    (Facts(diagnostics="unknown model foo", returncode=1, process_error="execution_error"), Outcome.REQUEST_FATAL, "invalid_model"),
    (Facts(diagnostics="error context_length_exceeded"), Outcome.REQUEST_FATAL, "context_overflow"),
    (Facts(process_error="timeout", diagnostics="overloaded"), Outcome.RETRYABLE, "timeout"),
    (Facts(diagnostics="429 rate limit", text="partial", session_id="s"), Outcome.RETRYABLE, "service_error"),
    (Facts(cli_error=D(code="service_error", message="api_error_status=500")), Outcome.RETRYABLE, "service_error"),
    (Facts(process_error="execution_error", returncode=2, text="t"), Outcome.RETRYABLE, "execution_error"),
    (Facts(process_error="stdin_failed"), Outcome.RETRYABLE, "stdin_failed"),
    (Facts(returncode=1, text="t", session_id="s"), Outcome.RETRYABLE, "execution_error"),
    (Facts(cli_error=D(code="", message="something odd"), text="t", session_id="s"), Outcome.RETRYABLE, "execution_error"),
    (Facts(text="  "), Outcome.RETRYABLE, "no_output"),
]


@pytest.mark.parametrize(("facts", "outcome", "code"), ROWS)
def test_precedence_rows(facts: Facts, outcome: Outcome, code: str) -> None:
    verdict = classify(facts)
    assert verdict.outcome == outcome
    assert verdict.error is not None and verdict.error.code == code


def test_structured_error_beats_keywords() -> None:
    verdict = classify(Facts(cli_error=D(code="service_error", message="500"), diagnostics="unauthorized"))
    assert verdict.error is not None and verdict.error.code == "service_error"


def test_specific_diagnostic_beats_nonzero_exit() -> None:
    verdict = classify(Facts(returncode=1, process_error="execution_error", cli_error=D(code="auth", message="login")))
    assert verdict.outcome == Outcome.AGENT_FATAL


def test_success_and_missing_session_warning() -> None:
    ok = classify(Facts(text="done", session_id="s"))
    assert (ok.outcome, ok.error, ok.warnings) == (Outcome.SUCCESS, None, ())
    warned = classify(Facts(text="done"))
    assert warned.outcome == Outcome.SUCCESS
    assert [w.code for w in warned.warnings] == ["no_session_id"]


@pytest.mark.parametrize(("status", "code"), [(401, "auth"), (403, "auth"), (429, "service_error"), (500, "service_error"), (529, "service_error"), (400, ""), (None, "")])
def test_http_status_code(status, code) -> None:
    assert http_status_code(status) == code
