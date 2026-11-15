from __future__ import annotations

import pytest

from coding_agent_cli import Diagnostic, ErrorCode, Outcome, Result, Usage
from coding_agent_cli._classify import RunFacts, classify, http_status_code, keyword_code
from coding_agent_cli.driver import Facts, RequestError

D = Diagnostic


def test_request_error_is_exception() -> None:
    err = RequestError(ErrorCode.INVALID_PROMPT, "too long")
    assert isinstance(err, Exception)
    assert err.code == ErrorCode.INVALID_PROMPT
    assert err.message == "too long"
    assert str(err) == "too long"


def test_row01_cancelled() -> None:
    result = classify(
        RunFacts(
            stop_cause="cancelled",
            request_error=D(code=ErrorCode.BAD_CWD, message="x"),
            spawn_error=D(code=ErrorCode.MISSING_CLI, message="x"),
            duration_s=1.2,
        ),
        Facts(error=D(code=ErrorCode.AUTH, message="x"), session_id="s1"),
        text="partial",
    )
    assert result.outcome == Outcome.CANCELLED
    assert result.error is not None and result.error.code == ErrorCode.CANCELLED
    assert result.text == "partial"
    assert result.duration_s == 1.2


def test_row02_request_fatal() -> None:
    result = classify(
        RunFacts(
            stop_cause="exited",
            request_error=D(code=ErrorCode.BAD_CWD, message="bad"),
            spawn_error=D(code=ErrorCode.MISSING_CLI, message="x"),
        ),
        Facts(),
        text="",
    )
    assert result.outcome == Outcome.REQUEST_FATAL
    assert result.error == D(code=ErrorCode.BAD_CWD, message="bad")


def test_row03_spawn_failed() -> None:
    result = classify(
        RunFacts(
            stop_cause="exited",
            spawn_error=D(code=ErrorCode.MISSING_CLI, message="not found"),
        ),
        Facts(error=D(code=ErrorCode.AUTH, message="x")),
        text="",
    )
    assert result.outcome == Outcome.AGENT_FATAL
    assert result.error == D(code=ErrorCode.MISSING_CLI, message="not found")


def test_row04_auth() -> None:
    result = classify(
        RunFacts(stop_cause="timeout", stderr_tail="Error: not logged in"),
        Facts(),
        text="",
    )
    assert result.outcome == Outcome.AGENT_FATAL
    assert result.error is not None and result.error.code == ErrorCode.AUTH


def test_row05_invalid_model_or_context_overflow() -> None:
    r1 = classify(
        RunFacts(stop_cause="exited", returncode=1, parser_error="boom"),
        Facts(error_text="unknown model foo"),
        text="",
    )
    assert r1.outcome == Outcome.REQUEST_FATAL
    assert r1.error is not None and r1.error.code == ErrorCode.INVALID_MODEL

    r2 = classify(
        RunFacts(stop_cause="exited", stderr_tail="context_length_exceeded"),
        Facts(),
        text="",
    )
    assert r2.outcome == Outcome.REQUEST_FATAL
    assert r2.error is not None and r2.error.code == ErrorCode.CONTEXT_OVERFLOW


def test_row05_cli_invalid_prompt_is_request_fatal() -> None:
    result = classify(
        RunFacts(stop_cause="exited", returncode=2),
        Facts(error=Diagnostic(code=ErrorCode.INVALID_PROMPT, message="/help is unavailable")),
        text="",
    )
    assert result.outcome == Outcome.REQUEST_FATAL
    assert result.error is not None and result.error.code == ErrorCode.INVALID_PROMPT


def test_row06_parser_error() -> None:
    result = classify(
        RunFacts(stop_cause="exited", parser_error="bad json line"),
        Facts(error=D(code=ErrorCode.MAX_TURNS, message="limit")),
        text="partial answer",
    )
    assert result.outcome == Outcome.AGENT_FATAL
    assert result.error == D(code=ErrorCode.PARSER_ERROR, message="bad json line")
    assert result.text == "partial answer"


def test_row07_limit_reached() -> None:
    r1 = classify(
        RunFacts(stop_cause="exited"),
        Facts(error=D(code=ErrorCode.MAX_TURNS, message="hit max turns")),
        text="ok",
    )
    assert r1.outcome == Outcome.LIMIT_REACHED
    assert r1.error is not None and r1.error.code == ErrorCode.MAX_TURNS

    r2 = classify(
        RunFacts(stop_cause="exited"),
        Facts(error=D(code=ErrorCode.BUDGET, message="hit budget")),
        text="ok",
    )
    assert r2.outcome == Outcome.LIMIT_REACHED
    assert r2.error is not None and r2.error.code == ErrorCode.BUDGET


def test_row08_deadline() -> None:
    r1 = classify(
        RunFacts(stop_cause="timeout", stderr_tail="overloaded"),
        Facts(),
        text="partial",
    )
    assert r1.outcome == Outcome.RETRYABLE
    assert r1.error is not None and r1.error.code == ErrorCode.TIMEOUT

    r2 = classify(
        RunFacts(stop_cause="idle_timeout"),
        Facts(),
        text="partial",
    )
    assert r2.outcome == Outcome.RETRYABLE
    assert r2.error is not None and r2.error.code == ErrorCode.IDLE_TIMEOUT


def test_row09_service_error() -> None:
    result = classify(
        RunFacts(stop_cause="exited", stderr_tail="429 rate limit"),
        Facts(session_id="s1"),
        text="partial",
    )
    assert result.outcome == Outcome.RETRYABLE
    assert result.error is not None and result.error.code == ErrorCode.SERVICE_ERROR


def test_row10_execution_or_stdin_failure() -> None:
    r_stdin = classify(RunFacts(stop_cause="exited", stdin_failed="broken pipe"), Facts(), text="")
    assert r_stdin.outcome == Outcome.RETRYABLE
    assert r_stdin.error is not None and r_stdin.error.code == ErrorCode.STDIN_FAILED

    r_rc = classify(RunFacts(stop_cause="exited", returncode=2), Facts(), text="t")
    assert r_rc.outcome == Outcome.RETRYABLE
    assert r_rc.error is not None and r_rc.error.code == ErrorCode.EXECUTION_ERROR

    r_struct = classify(
        RunFacts(stop_cause="exited", returncode=0),
        Facts(error=D(code=ErrorCode.EXECUTION_ERROR, message="failed")),
        text="t",
    )
    assert r_struct.outcome == Outcome.RETRYABLE
    assert r_struct.error == D(code=ErrorCode.EXECUTION_ERROR, message="failed")


def test_row11_no_output() -> None:
    result = classify(RunFacts(stop_cause="exited", returncode=0), Facts(session_id="s1"), text="  \n ")
    assert result.outcome == Outcome.RETRYABLE
    assert result.error is not None and result.error.code == ErrorCode.NO_OUTPUT


def test_row12_success() -> None:
    result = classify(
        RunFacts(stop_cause="exited", returncode=0, duration_s=2.5),
        Facts(
            final_text="final",
            session_id="s1",
            model="m1",
            usage=Usage(input=10, output=5),
            cost_usd=0.01,
            num_turns=1,
            stop_reason="end_turn",
        ),
        text="ignored",
    )
    assert result == Result(
        outcome=Outcome.SUCCESS,
        text="final",
        session_id="s1",
        model="m1",
        usage=Usage(input=10, output=5),
        cost_usd=0.01,
        duration_s=2.5,
        num_turns=1,
        stop_reason="end_turn",
        returncode=0,
        error=None,
        warnings=(),
    )


def test_latched_exited_ignores_later_cancel() -> None:
    # stop_cause was latched as "exited", so classify returns success regardless of external state
    result = classify(RunFacts(stop_cause="exited", returncode=0), Facts(session_id="s1"), text="done")
    assert result.outcome == Outcome.SUCCESS


def test_max_turns_limit_beats_timeout() -> None:
    result = classify(
        RunFacts(stop_cause="timeout"),
        Facts(error=D(code=ErrorCode.MAX_TURNS, message="max turns reached")),
        text="partial",
    )
    assert result.outcome == Outcome.LIMIT_REACHED
    assert result.error is not None and result.error.code == ErrorCode.MAX_TURNS


def test_warnings_include_no_session_id_line_too_long_cleanup_incomplete() -> None:
    result = classify(
        RunFacts(stop_cause="exited", returncode=0, line_too_long=True, cleanup_incomplete=True),
        Facts(),
        text="done",
    )
    assert result.outcome == Outcome.SUCCESS
    assert [w.code for w in result.warnings] == [
        ErrorCode.LINE_TOO_LONG,
        ErrorCode.CLEANUP_INCOMPLETE,
        ErrorCode.NO_SESSION_ID,
    ]


def test_structured_error_skips_keyword_matching() -> None:
    result = classify(
        RunFacts(stop_cause="exited", stderr_tail="unauthorized"),
        Facts(error=D(code=ErrorCode.SERVICE_ERROR, message="500")),
        text="partial",
    )
    assert result.outcome == Outcome.RETRYABLE
    assert result.error == D(code=ErrorCode.SERVICE_ERROR, message="500")


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (401, ErrorCode.AUTH),
        (403, ErrorCode.AUTH),
        (429, ErrorCode.SERVICE_ERROR),
        (500, ErrorCode.SERVICE_ERROR),
        (529, ErrorCode.SERVICE_ERROR),
        (400, None),
        (None, None),
    ],
)
def test_http_status_code(status, code) -> None:
    assert http_status_code(status) == code


def test_clean_exit_classifies_success() -> None:
    result = classify(RunFacts(returncode=0), Facts(session_id="s1"), text="done")
    assert result.outcome == Outcome.SUCCESS


def test_run_request_error_maps_to_request_fatal() -> None:
    facts = RunFacts(request_error=D(code=ErrorCode.INVALID_PROMPT, message="prompt is not valid UTF-8"))
    result = classify(facts, Facts(), text="")
    assert result.outcome == Outcome.REQUEST_FATAL
    assert result.error == D(code=ErrorCode.INVALID_PROMPT, message="prompt is not valid UTF-8")


@pytest.mark.parametrize(
    "snippet",
    [
        "HTTP 504",
        "HTTP error 504: Gateway Timeout",
        "status 503",
        '"status": 502',
    ],
)
def test_unstructured_http_5xx_classifies_as_service_error_over_nonzero_exit(snippet: str) -> None:
    r_stderr = classify(
        RunFacts(stop_cause="exited", returncode=1, stderr_tail=snippet),
        Facts(session_id="s1"),
        text="",
    )
    assert r_stderr.outcome == Outcome.RETRYABLE
    assert r_stderr.error is not None and r_stderr.error.code == ErrorCode.SERVICE_ERROR

    r_errtext = classify(
        RunFacts(stop_cause="exited", returncode=1),
        Facts(session_id="s1", error_text=snippet),
        text="",
    )
    assert r_errtext.outcome == Outcome.RETRYABLE
    assert r_errtext.error is not None and r_errtext.error.code == ErrorCode.SERVICE_ERROR


@pytest.mark.parametrize(
    "snippet",
    [
        "HTTP 404 Not Found",
        "status 400",
        '"status": 422',
    ],
)
def test_unstructured_http_4xx_does_not_match_service_error(snippet: str) -> None:
    result = classify(
        RunFacts(stop_cause="exited", returncode=1, stderr_tail=snippet),
        Facts(session_id="s1"),
        text="",
    )
    assert result.outcome == Outcome.RETRYABLE
    assert result.error is not None and result.error.code == ErrorCode.EXECUTION_ERROR


def test_mixed_http_statuses_follow_precedence_not_text_order() -> None:
    result = classify(
        RunFacts(stop_cause="exited", returncode=1, stderr_tail="HTTP 503 then HTTP 401"),
        Facts(session_id="s1"),
        text="",
    )
    assert result.outcome == Outcome.AGENT_FATAL
    assert result.error is not None and result.error.code == ErrorCode.AUTH


def test_keyword_matches_quoted_model_not_found() -> None:
    # Pi 1.5.2 reports an unknown model only on stderr (fixture pi/1.5.2/invalid_model).
    text = 'Error: Model "nonexistent-model-xyz-9999" not found. Use --list-models to see available models.'
    assert keyword_code(text) == ErrorCode.INVALID_MODEL
    assert keyword_code("file not found") is None
