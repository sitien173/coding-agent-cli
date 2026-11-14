"""Unit tests for scripts/record_fixtures.py."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import scripts.record_fixtures as rf  # noqa: E402


def test_agy_default_scenarios_exclude_auth_failure() -> None:
    assert "auth_failure" not in rf._scenario_specs("agy")


def test_agy_explicit_auth_failure_rejected_before_launch(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fail_launch(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("CLI must not be launched for unsupported agy auth_failure")

    monkeypatch.setattr(subprocess, "run", _fail_launch)
    monkeypatch.setattr(subprocess, "Popen", _fail_launch)
    with pytest.raises(ValueError, match="not supported"):
        rf.record_cli("agy", ["auth_failure"])


def test_record_fixtures_sets_dont_write_bytecode_before_scrubber_import() -> None:
    source = (REPO_ROOT / "scripts" / "record_fixtures.py").read_text(encoding="utf-8")
    idx_flag = source.find("sys.dont_write_bytecode = True")
    idx_import = source.find("from scripts.scrub_fixtures import")
    assert idx_flag != -1
    assert idx_import != -1
    assert idx_flag < idx_import


def test_verified_temp_parent_rejects_path_inside_repo(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    inside = REPO_ROOT / ".tmp-inside-test"
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(inside))
    with pytest.raises(RuntimeError, match="inside repository root"):
        rf._verified_temp_parent(REPO_ROOT)

    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    assert rf._verified_temp_parent(REPO_ROOT) == tmp_path.resolve()


def test_should_cancel_on_line_for_codex_waits_for_agent_message() -> None:
    turn_started = json.dumps({"type": "turn.started"})
    error_item = json.dumps({"type": "item.completed", "item": {"id": "1", "type": "error", "message": "warn"}})
    cmd_item = json.dumps(
        {"type": "item.completed", "item": {"id": "2", "type": "command_execution", "status": "completed"}}
    )
    agent_msg = json.dumps({"type": "item.completed", "item": {"id": "3", "type": "agent_message", "text": "Part 1"}})

    assert not rf._should_cancel_on_line("codex", turn_started)
    assert not rf._should_cancel_on_line("codex", error_item)
    assert not rf._should_cancel_on_line("codex", cmd_item)
    assert rf._should_cancel_on_line("codex", agent_msg)
    assert not rf._should_cancel_on_line(
        "agy", json.dumps({"event": "step_update", "step_update": {"step_type": "agent_response", "text_delta": ""}})
    )
    assert not rf._should_cancel_on_line(
        "agy", json.dumps({"event": "step_update", "step_update": {"step_type": "agent_response"}})
    )
    assert rf._should_cancel_on_line(
        "agy", json.dumps({"event": "step_update", "step_update": {"step_type": "agent_response", "text_delta": "Hi"}})
    )
    assert rf._should_cancel_on_line(
        "claude", json.dumps({"type": "stream_event", "event": {"delta": {"type": "text_delta"}}})
    )


def test_derive_outcome_code_follows_design_precedence() -> None:
    # 1. Cancelled beats everything, including auth and timeout.
    assert rf.derive_outcome_code(
        cancelled=True,
        timed_out=True,
        returncode=-15,
        stdout_text='{"type":"assistant","error":"authentication_failed"}\n',
        stderr_text="",
    ) == ("cancelled", "cancelled")

    # 4. Auth beats timeout (row 4 < row 8).
    assert rf.derive_outcome_code(
        cancelled=False,
        timed_out=True,
        returncode=1,
        stdout_text='{"type":"assistant","error":"authentication_failed"}\n',
        stderr_text="",
    ) == ("agent_fatal", "auth")

    # 5. Invalid model beats timeout (row 5 < row 8).
    assert rf.derive_outcome_code(
        cancelled=False,
        timed_out=True,
        returncode=1,
        stdout_text='{"type":"turn.failed","error":{"message":"The model is not supported"}}\n',
        stderr_text="",
    ) == ("request_fatal", "invalid_model")

    # 7. Caller-set limit (max_turns / budget) beats timeout (row 7 < row 8).
    assert rf.derive_outcome_code(
        cancelled=False,
        timed_out=True,
        returncode=1,
        stdout_text='{"type":"result","is_error":true,"subtype":"error_max_turns"}\n',
        stderr_text="",
    ) == ("limit_reached", "max_turns")

    assert rf.derive_outcome_code(
        cancelled=False,
        timed_out=False,
        returncode=1,
        stdout_text='{"type":"result","is_error":true,"subtype":"error_max_budget_usd"}\n',
        stderr_text="",
    ) == ("limit_reached", "budget")

    # 8. Timeout when no higher-precedence condition matches.
    assert rf.derive_outcome_code(
        cancelled=False,
        timed_out=True,
        returncode=-9,
        stdout_text="",
        stderr_text="",
    ) == ("retryable", "timeout")

    # 9. Service error (e.g. 503 / 504 / rate limit).
    assert rf.derive_outcome_code(
        cancelled=False,
        timed_out=False,
        returncode=1,
        stdout_text="",
        stderr_text="HTTP 503 Service Unavailable",
    ) == ("retryable", "service_error")
    assert rf.derive_outcome_code(
        cancelled=False,
        timed_out=False,
        returncode=1,
        stdout_text="",
        stderr_text="HTTP 504 Gateway Timeout",
    ) == ("retryable", "service_error")
    assert rf.derive_outcome_code(
        cancelled=False,
        timed_out=False,
        returncode=1,
        stdout_text="",
        stderr_text="unexpected status 504 from upstream",
    ) == ("retryable", "service_error")
    assert rf.derive_outcome_code(
        cancelled=False,
        timed_out=False,
        returncode=1,
        stdout_text="",
        stderr_text="HTTP error 504: Gateway Timeout",
    ) == ("retryable", "service_error")
    assert rf.derive_outcome_code(
        cancelled=False,
        timed_out=False,
        returncode=1,
        stdout_text='{"type": "error", "error": {"status": 504}}\n',
        stderr_text="",
    ) == ("retryable", "service_error")
    assert rf.derive_outcome_code(
        cancelled=False,
        timed_out=False,
        returncode=1,
        stdout_text="",
        stderr_text="unexpected status 401 Unauthorized, see https://example.com:503/x",
    ) != ("retryable", "service_error")

    # 10. Nonzero exit or structured error without more specific code.
    assert rf.derive_outcome_code(
        cancelled=False,
        timed_out=False,
        returncode=1,
        stdout_text="",
        stderr_text="Error: When using --print, --output-format=stream-json requires --verbose",
    ) == ("retryable", "execution_error")

    # 11. Zero exit with no text/output.
    assert rf.derive_outcome_code(
        cancelled=False,
        timed_out=False,
        returncode=0,
        stdout_text="",
        stderr_text="",
    ) == ("retryable", "no_output")

    # 12. Success with output.
    assert rf.derive_outcome_code(
        cancelled=False,
        timed_out=False,
        returncode=0,
        stdout_text='{"type":"result","subtype":"success","result":"PONG"}\n',
        stderr_text="",
    ) == ("success", None)


@pytest.mark.parametrize(
    ("stdout_text", "expected"),
    [
        # Claude: message_start only or empty result -> no_output
        ('{"type":"stream_event","event":{"type":"message_start"}}\n', ("retryable", "no_output")),
        ('{"type":"result","subtype":"success","result":""}\n', ("retryable", "no_output")),
        # Claude: text_delta, assistant text block, or non-empty result -> success
        (
            '{"type":"stream_event","event":{"type":"content_block_delta","delta":{"type":"text_delta","text":"Hi"}}}\n',
            ("success", None),
        ),
        (
            '{"type":"assistant","message":{"content":[{"type":"text","text":"Hi"}]}}\n',
            ("success", None),
        ),
        ('{"type":"result","subtype":"success","result":"PONG"}\n', ("success", None)),
        # Codex: empty agent_message vs non-empty agent_message
        ('{"type":"item.completed","item":{"type":"agent_message","text":""}}\n', ("retryable", "no_output")),
        ('{"type":"item.completed","item":{"type":"agent_message","text":"Part 1"}}\n', ("success", None)),
        # Pi: non-text message_update vs text_delta / assistant text content
        (
            '{"type":"message_update","assistantMessageEvent":{"type":"text_start"}}\n',
            ("retryable", "no_output"),
        ),
        (
            '{"type":"message_update","assistantMessageEvent":{"type":"text_delta","delta":"Hi"}}\n',
            ("success", None),
        ),
        (
            '{"type":"message_end","message":{"role":"assistant","content":[{"type":"text","text":"Hi"}]}}\n',
            ("success", None),
        ),
        # Agy: empty result/step vs text_delta / plain-text fallback
        (
            '{"event":"result","result":{"status":"SUCCESS","response":""}}\n',
            ("retryable", "no_output"),
        ),
        (
            '{"event":"step_update","step_update":{"step_type":"agent_response","text_delta":"Hi"}}\n',
            ("success", None),
        ),
        ("Plain text fallback answer\n", ("success", None)),
    ],
)
def test_derive_outcome_code_checks_non_empty_assistant_text(
    stdout_text: str, expected: tuple[str, str | None]
) -> None:
    assert (
        rf.derive_outcome_code(
            cancelled=False,
            timed_out=False,
            returncode=0,
            stdout_text=stdout_text,
            stderr_text="",
        )
        == expected
    )


def test_record_fixtures_script_does_not_create_pycache(tmp_path: Path) -> None:
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    shutil.copy2(REPO_ROOT / "scripts" / "record_fixtures.py", scripts_dir / "record_fixtures.py")
    shutil.copy2(REPO_ROOT / "scripts" / "scrub_fixtures.py", scripts_dir / "scrub_fixtures.py")
    proc = subprocess.run(
        [sys.executable, "-I", "scripts/record_fixtures.py", "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    assert proc.returncode == 0
    assert not (scripts_dir / "__pycache__").exists()


def test_committed_meta_json_matches_derived_outcome_code() -> None:
    recorded_dir = REPO_ROOT / "tests" / "fixtures" / "recorded"
    for meta_path in sorted(recorded_dir.rglob("*.meta.json")):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        stdout_text = meta_path.with_name(meta_path.name.replace(".meta.json", ".stdout.jsonl")).read_text(
            encoding="utf-8"
        )
        stderr_text = meta_path.with_name(meta_path.name.replace(".meta.json", ".stderr.txt")).read_text(
            encoding="utf-8"
        )
        derived_outcome, derived_code = rf.derive_outcome_code(
            cancelled=bool(meta["cancelled"]),
            timed_out=bool(meta["timed_out"]),
            returncode=meta["returncode"],
            stdout_text=stdout_text,
            stderr_text=stderr_text,
        )
        assert (meta["outcome"], meta["code"]) == (derived_outcome, derived_code), (
            f"{meta_path}: meta has {(meta['outcome'], meta['code'])}, derived {(derived_outcome, derived_code)}"
        )
