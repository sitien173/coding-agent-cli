"""Privacy gate and unit tests for recorded CLI fixtures and the scrubber."""

from __future__ import annotations

import getpass
import json
import re
import socket
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
RECORDED_DIR = REPO_ROOT / "tests" / "fixtures" / "recorded"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.scrub_fixtures import Scrubber  # noqa: E402

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
SECRET_PATTERNS = (
    re.compile(r"sk-ant-[A-Za-z0-9_-]{8,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"ghp_[A-Za-z0-9]{8,}"),
    re.compile(r"gho_[A-Za-z0-9]{8,}"),
    re.compile(r"AIza[A-Za-z0-9_-]{8,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{8,}"),
)
CONFIG_LEAK_PATTERNS = (
    "/home/scrubbed/.",
    "<location>",
    "<available_skills",
    "SKILL.md",
    "plugins/cache",
    ".nvm/",
)
MAX_JSON_STRING_BYTES = 8192
MAX_HOOK_OUTPUT_CHARS = 64
GENERIC_NAMES = frozenset({"user", "root", "admin", "test", "localhost"})


def _check_text_for_leaks(
    text: str,
    *,
    username: str,
    home: str,
    hostname: str,
    label: str,
) -> list[str]:
    """Return a list of privacy and user-config leak descriptions found in ``text``."""
    leaks: list[str] = []
    # Skip values shorter than 3 chars or generic system names to avoid false positives.
    if len(home) >= 3 and home in text:
        leaks.append(f"{label}: contains home path")
    if len(username) >= 3 and username.lower() not in GENERIC_NAMES and username in text:
        leaks.append(f"{label}: contains username")
    if len(hostname) >= 3 and hostname.lower() not in GENERIC_NAMES and hostname in text:
        leaks.append(f"{label}: contains hostname")
    for match in EMAIL_RE.finditer(text):
        email = match.group(0)
        if not email.endswith("@example.invalid"):
            leaks.append(f"{label}: contains email {email!r}")
    for pattern in SECRET_PATTERNS:
        if pattern.search(text):
            leaks.append(f"{label}: matches secret pattern {pattern.pattern}")
    for marker in CONFIG_LEAK_PATTERNS:
        if marker in text:
            leaks.append(f"{label}: contains config marker {marker!r}")
    return leaks


def _walk_json_strings(value: Any, *, path: str = "$") -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for k, v in value.items():
            found.extend(_walk_json_strings(v, path=f"{path}.{k}"))
    elif isinstance(value, list):
        for idx, item in enumerate(value):
            found.extend(_walk_json_strings(item, path=f"{path}[{idx}]"))
    elif isinstance(value, str):
        found.append((path, value))
    return found


def _check_jsonl_for_config_leaks(text: str, *, label: str) -> list[str]:
    """Check JSONL records for oversized strings and un-scrubbed hook_response payloads."""
    leaks: list[str] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("subtype") == "hook_response":
            for field in ("output", "stdout"):
                val = obj.get(field)
                if isinstance(val, str) and len(val) > MAX_HOOK_OUTPUT_CHARS:
                    leaks.append(f"{label}:{lineno}: hook_response.{field} exceeds {MAX_HOOK_OUTPUT_CHARS} chars")
        for json_path, s in _walk_json_strings(obj):
            if len(s.encode("utf-8")) > MAX_JSON_STRING_BYTES:
                leaks.append(f"{label}:{lineno}: string at {json_path} exceeds {MAX_JSON_STRING_BYTES} bytes")
    return leaks


def test_scrubber_removes_planted_values_from_json() -> None:
    scrubber = Scrubber(
        home="/home/alice",
        username="alice",
        hostname="workstation-99",
        cwds=("/tmp/cac-repo-1234",),
    )
    payload = {
        "type": "assistant",
        "cwd": "/tmp/cac-repo-1234",
        "path": "/home/alice/.config/tool.json",
        "host": "workstation-99",
        "author": "alice",
        "email": "alice@corp.example.com",
        "api_key": "super-secret-value",
        "apiKey": "another-secret",
        "token": "tok-1234567890",
        "authorization": "Bearer abcdefghijklmnop",
        "password": "pw",
        "secret": "sec",
        "note": "key sk-proj-1234567890abcdef and ghp_1234567890abcdef",
        "usage": {"input_tokens": 42, "output_tokens": 7},
    }
    scrubbed = scrubber.scrub_value(payload)
    serialized = json.dumps(scrubbed)

    assert "alice" not in serialized
    assert "/home/alice" not in serialized
    assert "/home/scrubbed/." not in serialized
    assert "workstation-99" not in serialized
    assert "alice@corp.example.com" not in serialized
    assert "sk-proj-" not in serialized
    assert "ghp_" not in serialized
    assert "super-secret-value" not in serialized
    assert "another-secret" not in serialized
    assert "tok-1234567890" not in serialized
    assert "abcdefghijklmnop" not in serialized
    assert scrubbed["cwd"] == "/work/repo"
    assert scrubbed["usage"] == {"input_tokens": 42, "output_tokens": 7}


def test_scrubber_redacts_nested_objects_and_arrays_under_secret_keys() -> None:
    scrubber = Scrubber(home="/home/alice", username="alice", hostname="workstation-99")
    payload = {
        "secret": {"value": "private-credential"},
        "api_key": ["private-key-1", {"nested": "private-key-2"}],
        "authorization": {"scheme": "Bearer", "credentials": "raw-cred"},
        "password": 987654,
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }
    scrubbed = scrubber.scrub_value(payload)
    serialized = json.dumps(scrubbed)

    assert "private-credential" not in serialized
    assert "private-key-1" not in serialized
    assert "private-key-2" not in serialized
    assert "raw-cred" not in serialized
    assert "987654" not in serialized
    assert scrubbed["secret"] == "<REDACTED_SECRET>"
    assert scrubbed["api_key"] == "<REDACTED_SECRET>"
    assert scrubbed["authorization"] == "<REDACTED_SECRET>"
    assert scrubbed["password"] == "<REDACTED_SECRET>"
    assert scrubbed["usage"] == {"input_tokens": 10, "output_tokens": 5}


def test_scrubber_removes_user_config_fields_and_paths() -> None:
    scrubber = Scrubber(home="/home/alice", username="alice", hostname="workstation-99")
    init_event = {
        "type": "system",
        "subtype": "init",
        "skills": ["personal-skill", "refract:brainstorming"],
        "slash_commands": ["personal-cmd"],
        "plugins": [{"name": "my-plugin", "path": "/home/alice/.claude/plugins/cache/org/my-plugin/1.0"}],
        "mcp_servers": [{"name": "private-mcp", "status": "connected"}],
        "agents": ["custom-agent"],
        "memory_paths": {"auto": "/home/alice/.claude/projects/foo/memory/"},
    }
    scrubbed_init = scrubber.scrub_value(init_event)
    assert scrubbed_init["skills"] == []
    assert scrubbed_init["slash_commands"] == []
    assert scrubbed_init["plugins"] == []
    assert scrubbed_init["mcp_servers"] == []
    assert scrubbed_init["agents"] == []
    assert "/home/scrubbed/." not in json.dumps(scrubbed_init)
    assert "plugins/cache" not in json.dumps(scrubbed_init)

    hook_event = {
        "type": "system",
        "subtype": "hook_response",
        "output": "long private hook instructions " * 20,
        "stdout": "long private hook stdout " * 20,
    }
    scrubbed_hook = scrubber.scrub_value(hook_event)
    assert len(scrubbed_hook["output"]) <= MAX_HOOK_OUTPUT_CHARS
    assert len(scrubbed_hook["stdout"]) <= MAX_HOOK_OUTPUT_CHARS

    pi_msg = {
        "type": "message_start",
        "message": {
            "role": "developer",
            "systemPrompt": (
                "You are a helpful assistant.\n"
                "<available_skills><location>/home/alice/.pi/SKILL.md</location></available_skills>"
            ),
            "sections": [{"title": "Skills", "content": "<location>/home/alice/.agents/skills/x/SKILL.md</location>"}],
            "tools": [{"name": "codemode", "description": "Read /home/alice/.nvm/versions/node/v24/docs/codemode.md"}],
        },
    }
    scrubbed_pi = scrubber.scrub_value(pi_msg)
    pi_text = json.dumps(scrubbed_pi)
    assert "<location>" not in pi_text
    assert "<available_skills" not in pi_text
    assert "SKILL.md" not in pi_text
    assert ".nvm/" not in pi_text
    assert "/home/scrubbed/." not in pi_text
    assert scrubbed_pi["message"]["systemPrompt"] == "<scrubbed:system-prompt>"
    assert scrubbed_pi["message"]["sections"] == []

    oversized = {"type": "other", "blob": "x" * (MAX_JSON_STRING_BYTES + 500)}
    scrubbed_big = scrubber.scrub_value(oversized)
    assert len(scrubbed_big["blob"].encode("utf-8")) <= MAX_JSON_STRING_BYTES


def test_scrubber_removes_planted_values_from_stderr() -> None:
    scrubber = Scrubber(
        home="/home/alice",
        username="alice",
        hostname="workstation-99",
        cwds=("/tmp/cac-repo-1234",),
    )
    stderr = (
        "Error in /tmp/cac-repo-1234 for alice@workstation-99 "
        "(/home/alice/.local/bin/cli) email bob@acme.org key sk-ant-1234567890ab"
    )
    scrubbed = scrubber.scrub_text(stderr)

    assert "alice" not in scrubbed
    assert "/home/alice" not in scrubbed
    assert "/home/scrubbed/." not in scrubbed
    assert "workstation-99" not in scrubbed
    assert "/tmp/cac-repo-1234" not in scrubbed
    assert "bob@acme.org" not in scrubbed
    assert "sk-ant-" not in scrubbed
    assert "/work/repo" in scrubbed


def test_scrubber_preserves_consistent_id_mapping_across_events() -> None:
    scrubber = Scrubber(home="/home/alice", username="alice", hostname="workstation-99")
    uuid_1 = "123e4567-e89b-12d3-a456-426614174000"
    uuid_2 = "987fcdeb-51a2-43d1-b789-426614174999"

    ev1 = scrubber.scrub_value({"type": "session", "session_id": uuid_1, "id": "toolu_abc123"})
    ev2 = scrubber.scrub_value({"type": "tool_result", "session_id": uuid_1, "tool_use_id": "toolu_abc123"})
    ev3 = scrubber.scrub_value({"type": "other", "thread_id": uuid_2})

    assert ev1["session_id"] != uuid_1
    assert ev1["session_id"] == ev2["session_id"]
    assert ev1["id"] != "toolu_abc123"
    assert ev1["id"] == ev2["tool_use_id"]
    assert ev3["thread_id"] != uuid_2
    assert ev3["thread_id"] != ev1["session_id"]


def test_privacy_gate_catches_planted_leaks_and_config_markers() -> None:
    leaks = _check_text_for_leaks(
        (
            "user alice at /home/alice on myhost.local bob@company.com sk-1234567890abcdef "
            "/home/scrubbed/.claude <location>x</location> <available_skills> SKILL.md "
            "plugins/cache/foo /usr/.nvm/node"
        ),
        username="alice",
        home="/home/alice",
        hostname="myhost.local",
        label="planted",
    )
    assert len(leaks) == 11

    jsonl_leaks = _check_jsonl_for_config_leaks(
        json.dumps({"type": "system", "subtype": "hook_response", "output": "x" * 200})
        + "\n"
        + json.dumps({"type": "other", "big": "y" * (MAX_JSON_STRING_BYTES + 1)}),
        label="planted.jsonl",
    )
    assert len(jsonl_leaks) == 2


def test_committed_recorded_fixtures_have_no_privacy_leaks() -> None:
    if not RECORDED_DIR.exists():
        pytest.skip("tests/fixtures/recorded does not exist yet")

    username = getpass.getuser()
    home = str(Path.home())
    hostname = socket.gethostname()

    fixture_files = sorted(p for p in RECORDED_DIR.rglob("*") if p.is_file() and p.name != "FINDINGS.md")
    all_leaks: list[str] = []
    for path in fixture_files:
        text = path.read_text(encoding="utf-8", errors="replace")
        rel = path.relative_to(REPO_ROOT).as_posix()
        all_leaks.extend(_check_text_for_leaks(text, username=username, home=home, hostname=hostname, label=rel))
        if path.suffix == ".jsonl":
            all_leaks.extend(_check_jsonl_for_config_leaks(text, label=rel))

    assert not all_leaks, "Privacy leaks detected:\n" + "\n".join(all_leaks)
