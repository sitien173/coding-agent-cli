"""Codex rollout collector tests: exact-session verification, discovery bounds, incremental reads, and invalidation.

The collector is driven directly through ``CodexRolloutCollector``. The end-to-end ``CODEX_HOME`` checks run the
real driver against the fake ``codex`` launcher.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from coding_agent_cli import Outcome, Usage
from coding_agent_cli._drivers._usage import TokenCounters
from coding_agent_cli._drivers.codex_rollout import CodexRolloutCollector

from .cli_stub import run_driver, stub_cli

SID = "00000000-0000-0000-0000-111111111111"
OTHER_SID = "00000000-0000-0000-0000-222222222222"


def _header(session_id: str, cwd: Path) -> dict[str, Any]:
    return {"type": "session_meta", "payload": {"id": session_id, "cwd": str(cwd)}}


def _token(
    input_tokens: int = 500, output_tokens: int = 20, reasoning: int = 5, window: int | None = 8000
) -> dict[str, Any]:
    info: dict[str, Any] = {
        "last_token_usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "reasoning_output_tokens": reasoning,
        }
    }
    if window is not None:
        info["model_context_window"] = window
    return {"type": "event_msg", "payload": {"type": "token_count", "info": info}}


def _model(model: str) -> dict[str, Any]:
    return {"type": "event_msg", "payload": {"type": "session_configured", "model": model}}


def _write(path: Path, *records: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    return path


def _append(path: Path, text: str) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(text)


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    return workspace


@pytest.fixture
def home(tmp_path: Path) -> Path:
    codex_home = tmp_path / "codex-home"
    (codex_home / "sessions").mkdir(parents=True)
    return codex_home


def _rollout_path(home: Path, session_id: str = SID, subdir: str = "") -> Path:
    directory = home / "sessions" / subdir if subdir else home / "sessions"
    return directory / f"rollout_{session_id}.jsonl"


class Probe:
    """One collector over one session, recording how many usage changes it reported."""

    def __init__(self, ws: Path, home: Path, session_id: str = SID, env: dict[str, str] | None = None) -> None:
        self.changes = 0
        self.counters = TokenCounters()
        self.collector = CodexRolloutCollector(
            cwd=ws,
            env=env if env is not None else {"CODEX_HOME": str(home)},
            session_id=session_id,
            counters=self.counters,
            on_change=self._changed,
        )

    def _changed(self) -> None:
        self.changes += 1

    def poll(self) -> Usage:
        self.collector.poll()
        return self.counters.usage()


# --- Exact-session enrichment through the driver ---


@pytest.mark.asyncio
async def test_codex_rollout_exact_session_enrichment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ws: Path, home: Path
) -> None:
    _write(_rollout_path(home), _header(SID, ws))
    stub_cli(
        monkeypatch,
        tmp_path,
        "codex",
        [
            json.dumps({"type": "thread.started", "thread_id": SID}),
            json.dumps({"type": "item.completed", "item": {"id": "item_1", "type": "agent_message", "text": "OK"}}),
            json.dumps(
                {
                    "type": "turn.completed",
                    "usage": {"input_tokens": 1000, "output_tokens": 50, "cached_input_tokens": 200},
                }
            ),
        ],
    )

    result = await run_driver("codex", ws, env={"CODEX_HOME": str(home)})

    assert result.outcome == Outcome.SUCCESS
    assert result.usage.context_window is None
    assert result.usage.context_used == 1050


@pytest.mark.asyncio
async def test_rollout_token_count_wins_over_turn_consumption_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ws: Path, home: Path
) -> None:
    _write(_rollout_path(home), _header(SID, ws), _token(500, 20, 5, 8000))
    stub_cli(
        monkeypatch,
        tmp_path,
        "codex",
        [
            json.dumps({"type": "thread.started", "thread_id": SID}),
            json.dumps({"type": "item.completed", "item": {"id": "i", "type": "agent_message", "text": "OK"}}),
            json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1000, "output_tokens": 50}}),
        ],
    )

    result = await run_driver("codex", ws, env={"CODEX_HOME": str(home)})

    assert result.usage.context_used == 515
    assert result.usage.context_window == 8000


@pytest.mark.asyncio
async def test_rollout_supplies_the_model_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ws: Path, home: Path
) -> None:
    _write(_rollout_path(home), _header(SID, ws), _model("gpt-test"))
    stub_cli(
        monkeypatch,
        tmp_path,
        "codex",
        [
            json.dumps({"type": "thread.started", "thread_id": SID}),
            json.dumps({"type": "item.completed", "item": {"id": "i", "type": "agent_message", "text": "OK"}}),
        ],
    )

    result = await run_driver("codex", ws, env={"CODEX_HOME": str(home)})

    assert result.model == "gpt-test"


@pytest.mark.asyncio
async def test_codex_home_from_request_env_wins_over_os_environ(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ws: Path, home: Path
) -> None:
    # os.environ points at an empty home. The rollout exists only under the request's CODEX_HOME.
    empty_home = tmp_path / "empty-home"
    (empty_home / "sessions").mkdir(parents=True)
    monkeypatch.setenv("CODEX_HOME", str(empty_home))
    _write(_rollout_path(home), _header(SID, ws), _token(500, 20, 5, 8000))
    stub_cli(
        monkeypatch,
        tmp_path,
        "codex",
        [
            json.dumps({"type": "thread.started", "thread_id": SID}),
            json.dumps({"type": "item.completed", "item": {"id": "i", "type": "agent_message", "text": "OK"}}),
        ],
    )

    result = await run_driver("codex", ws, env={"CODEX_HOME": str(home)})

    assert result.usage.context_used == 515


@pytest.mark.asyncio
async def test_os_environ_codex_home_is_ignored_when_request_env_overrides_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ws: Path, home: Path
) -> None:
    _write(_rollout_path(home), _header(SID, ws), _token(500, 20, 5, 8000))
    monkeypatch.setenv("CODEX_HOME", str(home))
    empty_home = tmp_path / "empty-home"
    (empty_home / "sessions").mkdir(parents=True)
    stub_cli(
        monkeypatch,
        tmp_path,
        "codex",
        [
            json.dumps({"type": "thread.started", "thread_id": SID}),
            json.dumps({"type": "item.completed", "item": {"id": "i", "type": "agent_message", "text": "OK"}}),
            json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1000, "output_tokens": 50}}),
        ],
    )

    result = await run_driver("codex", ws, env={"CODEX_HOME": str(empty_home)})

    assert result.usage.context_used == 1050


# --- Exact session verification ---


def test_codex_rollout_refuses_mismatched_session_identity(ws: Path, home: Path) -> None:
    _write(_rollout_path(home), _header(OTHER_SID, ws), _token())

    probe = Probe(ws, home)

    assert probe.poll().context_used is None


def test_codex_rollout_refuses_a_file_with_the_wrong_cwd(tmp_path: Path, ws: Path, home: Path) -> None:
    _write(_rollout_path(home), _header(SID, tmp_path / "elsewhere"), _token())

    assert Probe(ws, home).poll().context_used is None


def test_codex_rollout_never_selects_unrelated_latest_session(ws: Path, home: Path) -> None:
    _write(_rollout_path(home), _header(SID, ws), _token(100, 10, 0, 1000))
    _write(_rollout_path(home, OTHER_SID), _header(OTHER_SID, ws), _token(9000, 900, 0, 9999))

    usage = Probe(ws, home).poll()

    assert usage.context_used == 110
    assert usage.context_window == 1000


def test_codex_rollout_session_meta_mid_file_for_another_session_stops_verification(ws: Path, home: Path) -> None:
    path = _write(_rollout_path(home), _header(SID, ws), _token(100, 10, 0, 1000))
    _append(path, json.dumps(_header(OTHER_SID, ws)) + "\n" + json.dumps(_token(900, 90, 0, 9000)) + "\n")

    usage = Probe(ws, home).poll()

    assert usage.context_used == 110


@pytest.mark.parametrize(
    "content",
    [
        pytest.param("not json at all\n", id="non-json-header"),
        pytest.param(
            json.dumps({"type": "event_msg", "payload": {"id": SID, "cwd": "x"}}) + "\n", id="not-session-meta"
        ),
        pytest.param("", id="empty"),
    ],
)
def test_codex_rollout_discovery_rejects_unverifiable_files(ws: Path, home: Path, content: str) -> None:
    path = _rollout_path(home)
    path.write_text(content, encoding="utf-8")

    assert Probe(ws, home).poll().context_used is None


def test_codex_rollout_records_are_ignored_while_file_is_unverified(ws: Path, home: Path) -> None:
    _write(_rollout_path(home), _header(OTHER_SID, ws), _token())

    probe = Probe(ws, home)
    probe.poll()

    assert probe.changes == 0
    assert probe.counters.usage().context_used is None


# --- Discovery bounds ---


def test_codex_rollout_discovery_finds_nested_exact_file_and_records_identity(ws: Path, home: Path) -> None:
    _write(_rollout_path(home, subdir="2026/11/12"), _header(SID, ws), _token(500, 20, 5, 8000))

    assert Probe(ws, home).poll().context_used == 515


def test_codex_rollout_discovery_skips_mismatched_file_in_sibling_directory(ws: Path, home: Path) -> None:
    _write(_rollout_path(home, OTHER_SID, subdir="a"), _header(OTHER_SID, ws), _token(900, 90, 0, 9000))
    _write(_rollout_path(home, SID, subdir="b"), _header(SID, ws), _token(100, 10, 0, 1000))

    assert Probe(ws, home).poll().context_used == 110


def test_codex_rollout_discovery_honors_depth_limit(ws: Path, home: Path) -> None:
    deep = _write(_rollout_path(home, subdir="a/b/c/d"), _header(SID, ws), _token())
    assert deep.exists()

    assert Probe(ws, home).poll().context_used is None


def test_codex_rollout_discovery_finds_a_file_at_the_depth_limit(ws: Path, home: Path) -> None:
    _write(_rollout_path(home, subdir="a/b/c"), _header(SID, ws), _token())

    assert Probe(ws, home).poll().context_used == 515


@pytest.mark.parametrize("session_id", ["", "not-a-uuid", "../escape"])
def test_codex_rollout_discovery_rejects_invalid_session_id_without_retry(
    ws: Path, home: Path, session_id: str
) -> None:
    probe = Probe(ws, home, session_id=session_id)

    probe.poll()

    assert probe.collector.missing_discovery_retries == 0


def test_codex_rollout_discovery_stops_after_retry_budget(ws: Path, home: Path) -> None:
    probe = Probe(ws, home)
    for _ in range(probe.collector.MAX_DISCOVERY_RETRIES):
        probe.poll()
    _write(_rollout_path(home), _header(SID, ws), _token())

    probe.poll()

    assert probe.collector.missing_discovery_retries == probe.collector.MAX_DISCOVERY_RETRIES
    assert probe.counters.usage().context_used is None


def test_codex_rollout_discovery_counts_missing_sessions_dir(tmp_path: Path, ws: Path) -> None:
    codex_home = tmp_path / "no-sessions"
    codex_home.mkdir()

    probe = Probe(ws, codex_home)
    probe.poll()

    assert probe.collector.missing_discovery_retries == 1


def test_codex_rollout_discovery_refuses_symlinked_sessions_dir(tmp_path: Path, ws: Path) -> None:
    real = tmp_path / "real-sessions"
    _write(real / "rollout_x.jsonl", _header(SID, ws), _token())
    codex_home = tmp_path / "linked-home"
    codex_home.mkdir()
    (codex_home / "sessions").symlink_to(real, target_is_directory=True)

    probe = Probe(ws, codex_home)

    assert probe.poll().context_used is None
    assert probe.collector.missing_discovery_retries == 1


def test_codex_rollout_discovery_ignores_symlinked_rollout_file(tmp_path: Path, ws: Path, home: Path) -> None:
    target = _write(tmp_path / "outside.jsonl", _header(SID, ws), _token())
    (home / "sessions" / f"rollout_{SID}.jsonl").symlink_to(target)

    assert Probe(ws, home).poll().context_used is None


def test_codex_rollout_discovery_stops_at_entry_limit(monkeypatch: pytest.MonkeyPatch, ws: Path, home: Path) -> None:
    monkeypatch.setattr(CodexRolloutCollector, "MAX_DISCOVERY_ENTRIES", 2)
    for index in range(5):
        _write(home / "sessions" / f"noise-{index}.jsonl", _header(OTHER_SID, ws))
    _write(_rollout_path(home, subdir="deep"), _header(SID, ws), _token())

    assert Probe(ws, home).poll().context_used is None


# --- Incremental reads and invalidation ---


def test_codex_rollout_poll_without_session_id_does_nothing(ws: Path, home: Path) -> None:
    _write(_rollout_path(home), _header(SID, ws), _token())

    probe = Probe(ws, home, session_id="")
    probe.poll()

    assert probe.collector.missing_discovery_retries == 0
    assert probe.changes == 0


def test_codex_rollout_poll_first_read_verifies_file_and_records_context(ws: Path, home: Path) -> None:
    _write(_rollout_path(home), _header(SID, ws), _token(500, 20, 5, 8000))

    probe = Probe(ws, home)
    usage = probe.poll()

    assert probe.collector.file_verified is True
    assert usage == Usage(context_used=515, context_window=8000)
    assert probe.changes == 1


def test_codex_rollout_poll_discards_state_when_file_disappears(ws: Path, home: Path) -> None:
    path = _write(_rollout_path(home), _header(SID, ws), _token())
    probe = Probe(ws, home)
    assert probe.poll().context_used == 515

    path.unlink()

    assert probe.poll().context_used is None
    assert probe.collector.session_file is None


def test_codex_rollout_growth_invalidates_verified_context_until_next_snapshot(ws: Path, home: Path) -> None:
    path = _write(_rollout_path(home), _header(SID, ws), _token(500, 20, 5, 8000))
    probe = Probe(ws, home)
    assert probe.poll().context_used == 515

    _append(path, json.dumps({"type": "event_msg", "payload": {"type": "agent_message"}}) + "\n")

    assert probe.poll().context_used is None

    _append(path, json.dumps(_token(600, 30, 10, 8000)) + "\n")

    assert probe.poll().context_used == 620


def test_codex_rollout_poll_without_new_bytes_does_not_emit(ws: Path, home: Path) -> None:
    _write(_rollout_path(home), _header(SID, ws), _token())
    probe = Probe(ws, home)
    probe.poll()
    changes = probe.changes

    probe.poll()

    assert probe.changes == changes


def test_codex_rollout_poll_keeps_partial_line_until_it_completes(ws: Path, home: Path) -> None:
    path = _rollout_path(home)
    _write(path, _header(SID, ws))
    _append(path, json.dumps(_token(500, 20, 5, 8000)))
    probe = Probe(ws, home)

    assert probe.poll().context_used is None

    _append(path, "\n")

    assert probe.poll().context_used == 515


def test_codex_rollout_poll_skips_blank_and_non_object_lines(ws: Path, home: Path) -> None:
    path = _rollout_path(home)
    _write(path, _header(SID, ws))
    _append(path, "\n[]\n42\nnull\n" + json.dumps(_token(500, 20, 5, 8000)) + "\n")

    assert Probe(ws, home).poll().context_used == 515


def test_codex_rollout_poll_defers_context_until_file_is_fully_read(ws: Path, home: Path) -> None:
    path = _write(_rollout_path(home), _header(SID, ws), _token(500, 20, 5, 8000), _token(700, 30, 10, 8000))
    probe = Probe(ws, home)
    probe.collector.max_bytes_per_poll = 120

    first = probe.poll()
    assert first.context_used is None

    usage = first
    for _ in range(20):
        if usage.context_used is not None:
            break
        usage = probe.poll()

    assert path.stat().st_size > 120
    assert usage.context_used == 720


def test_codex_rollout_budget_exhaustion_invalidates_once(ws: Path, home: Path) -> None:
    _write(_rollout_path(home), _header(SID, ws), *[_token() for _ in range(20)])
    probe = Probe(ws, home)
    probe.collector.max_total_bytes = 200
    probe.collector.max_bytes_per_poll = 64

    for _ in range(10):
        probe.poll()
    changes = probe.changes
    for _ in range(5):
        probe.poll()

    assert probe.collector.budget_exhausted is True
    assert probe.changes == changes


def test_codex_rollout_truncation_requires_the_session_header_again(ws: Path, home: Path) -> None:
    path = _write(_rollout_path(home), _header(SID, ws), _token(500, 20, 5, 8000))
    probe = Probe(ws, home)
    assert probe.poll().context_used == 515

    path.write_text(json.dumps(_header(OTHER_SID, ws)) + "\n", encoding="utf-8")

    assert probe.poll().context_used is None
    assert probe.collector.header_verified is False


def test_codex_rollout_rotation_replaces_the_inode_and_rediscovers(ws: Path, home: Path) -> None:
    path = _write(_rollout_path(home), _header(SID, ws), _token(500, 20, 5, 8000))
    probe = Probe(ws, home)
    assert probe.poll().context_used == 515

    replacement = path.with_suffix(".new")
    _write(replacement, _header(SID, ws), _token(800, 40, 0, 8000))
    os.replace(replacement, path)

    assert probe.poll().context_used is None
    assert probe.poll().context_used == 840


def test_codex_rollout_compaction_record_clears_context_and_marks_compacted(ws: Path, home: Path) -> None:
    path = _write(_rollout_path(home), _header(SID, ws), _token(500, 20, 5, 8000))
    probe = Probe(ws, home)
    assert probe.poll().context_used == 515

    _append(path, json.dumps({"type": "context_compacted"}) + "\n")

    assert probe.poll().context_used is None
    assert probe.collector.is_compacted is True


def test_codex_rollout_compacted_event_msg_clears_context(ws: Path, home: Path) -> None:
    path = _write(_rollout_path(home), _header(SID, ws), _token(500, 20, 5, 8000))
    probe = Probe(ws, home)
    probe.poll()

    _append(path, json.dumps({"type": "event_msg", "payload": {"type": "context_compacted"}}) + "\n")

    assert probe.poll().context_used is None
    assert probe.collector.is_compacted is True


def test_codex_rollout_session_switch_invalidates_context(ws: Path, home: Path) -> None:
    _write(_rollout_path(home), _header(SID, ws), _token(500, 20, 5, 8000))
    _write(_rollout_path(home, OTHER_SID), _header(OTHER_SID, ws), _token(100, 10, 0, 1000))
    probe = Probe(ws, home)
    assert probe.poll().context_used == 515

    probe.collector.set_session_id(OTHER_SID)

    assert probe.poll().context_used == 110


def test_codex_rollout_concurrent_sessions_are_isolated(ws: Path, home: Path) -> None:
    _write(_rollout_path(home), _header(SID, ws), _token(500, 20, 5, 8000))
    _write(_rollout_path(home, OTHER_SID), _header(OTHER_SID, ws), _token(100, 10, 0, 1000))

    first = Probe(ws, home, SID)
    second = Probe(ws, home, OTHER_SID)

    assert first.poll().context_used == 515
    assert second.poll().context_used == 110


def test_codex_rollout_model_records_update_model_name(ws: Path, home: Path) -> None:
    path = _write(_rollout_path(home), _header(SID, ws), _model("model-a"))
    probe = Probe(ws, home)
    probe.poll()
    assert probe.collector.model_name == "model-a"

    _append(path, json.dumps({"type": "event_msg", "payload": {"type": "model_reroute", "to_model": "model-b"}}) + "\n")
    probe.poll()

    assert probe.collector.model_name == "model-b"


def test_codex_rollout_unrecognized_records_change_nothing(ws: Path, home: Path) -> None:
    _write(
        _rollout_path(home), _header(SID, ws), {"type": "unknown"}, {"type": "event_msg", "payload": {"type": "other"}}
    )

    probe = Probe(ws, home)
    usage = probe.poll()

    assert usage == Usage()
    assert probe.changes == 0


def test_codex_rollout_token_count_without_usable_fields_is_ignored(ws: Path, home: Path) -> None:
    _write(
        _rollout_path(home),
        _header(SID, ws),
        {"type": "event_msg", "payload": {"type": "token_count", "info": {"last_token_usage": {"input_tokens": 5}}}},
        {"type": "event_msg", "payload": {"type": "token_count", "info": "x"}},
    )

    assert Probe(ws, home).poll().context_used is None


def test_codex_rollout_stat_failure_after_discovery_is_not_fatal(
    ws: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(_rollout_path(home), _header(SID, ws), _token())
    probe = Probe(ws, home)
    probe.poll()

    real_stat = Path.stat

    def failing_stat(self: Path, *args: Any, **kwargs: Any) -> os.stat_result:
        if self.name == f"rollout_{SID}.jsonl":
            raise OSError("stat failed")
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", failing_stat)

    assert probe.poll().context_used is None
