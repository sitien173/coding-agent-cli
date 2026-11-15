"""Record and scrub real CLI fixtures in an isolated throwaway git repository.

Raw captures are written to a mode-700 directory outside the repository and are
never copied into the worktree. Only scrubbed output is written to
``tests/fixtures/recorded/<cli>/<version>/``.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
sys.dont_write_bytecode = True

from scripts.scrub_fixtures import Scrubber  # noqa: E402

RECORDED_ROOT = REPO_ROOT / "tests" / "fixtures" / "recorded"
DEFAULT_TIMEOUT_S = 180.0

_API_KEY_ENV_PREFIXES = (
    "ANTHROPIC_",
    "OPENAI_",
    "CODEX_",
    "GEMINI_",
    "GOOGLE_",
    "CLAUDE_CODE_",
    "AZURE_OPENAI_",
    "DEEPSEEK_",
    "NVIDIA_",
    "GROQ_",
    "CEREBRAS_",
    "XAI_",
    "FIREWORKS_",
    "TOGETHER_",
    "BASETEN_",
    "OPENROUTER_",
    "AI_GATEWAY_",
    "ZAI_",
    "MISTRAL_",
    "MINIMAX_",
    "MOONSHOT_",
    "OPENCODE_",
    "KIMI_",
    "META_",
    "CLOUDFLARE_",
    "QWEN_",
    "XIAOMI_",
    "AWS_",
    "AGY_",
)

_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)
_VERSION_NUM_RE = re.compile(r"(\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.]+)?)")
_HTTP_5XX_RE = re.compile(
    r"(?:\bhttp(?:/\d(?:\.\d)?)?(?:\s+error)?|\bstatus(?:[\s_]?code)?\"?)\s*[:=]?\s*5\d\d\b"
    r"|\b5\d\d\s+(?:service\s+unavailable|bad\s+gateway|gateway\s+time-?out|internal\s+server\s+error)\b",
    re.IGNORECASE,
)


@dataclass(slots=True)
class RawCapture:
    cli: str
    version: str
    version_output: str
    scenario: str
    argv_redacted: list[str]
    cwd: str
    raw_dir: Path
    stdout_path: Path
    stderr_path: Path
    returncode: int | None
    timed_out: bool
    cancelled: bool
    extra_literals: list[str]


def _make_throwaway_repo(parent: Path, name: str) -> Path:
    repo = parent / name
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "hello.txt").write_text("hello\n", encoding="utf-8")
    (repo / "calc.py").write_text("def add(a: int, b: int) -> int:\n    return a + b\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Fixture Bot"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "fixture@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo


def _get_cli_version(cli: str) -> tuple[str, str]:
    proc = subprocess.run([cli, "--version"], capture_output=True, text=True, timeout=15, check=False)
    out = (proc.stdout or proc.stderr or "").strip()
    match = _VERSION_NUM_RE.search(out)
    version = match.group(1) if match else (out.replace(" ", "_") or "unknown")
    return version, out


def _auth_failure_env(cli: str, empty_dir: Path) -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(_API_KEY_ENV_PREFIXES) and not k.endswith("_API_KEY") and not k.endswith("_TOKEN")
    }
    if cli == "claude":
        env["CLAUDE_CONFIG_DIR"] = str(empty_dir)
    elif cli == "codex":
        env["CODEX_HOME"] = str(empty_dir)
    elif cli == "pi":
        env["PI_CODING_AGENT_DIR"] = str(empty_dir)
        env["PI_CODING_AGENT_SESSION_DIR"] = str(empty_dir / "sessions")
    return env


def _should_cancel_on_line(cli: str, line: str) -> bool:
    """Return True when ``line`` indicates the CLI has emitted its first partial text."""
    stripped = line.strip()
    if not stripped:
        return False
    if cli == "codex":
        with contextlib.suppress(json.JSONDecodeError):
            obj = json.loads(stripped)
            if isinstance(obj, dict) and obj.get("type") == "item.completed":
                item = obj.get("item")
                return isinstance(item, dict) and item.get("type") == "agent_message"
        return False
    if cli == "agy":
        with contextlib.suppress(json.JSONDecodeError):
            obj = json.loads(stripped)
            if isinstance(obj, dict) and obj.get("event") == "step_update":
                step = obj.get("step_update")
                return (
                    isinstance(step, dict)
                    and step.get("step_type") == "agent_response"
                    and isinstance(step.get("text_delta"), str)
                    and bool(step["text_delta"])
                )
        return False
    return '"text_delta"' in stripped


def _kill_process_group(proc: subprocess.Popen[bytes], *, force: bool) -> None:
    with contextlib.suppress(ProcessLookupError, OSError):
        if sys.platform == "win32":
            proc.kill()
        else:
            os.killpg(proc.pid, signal.SIGKILL if force else signal.SIGTERM)


def _run_capture(
    *,
    cli: str,
    version: str,
    version_output: str,
    scenario: str,
    argv: list[str],
    argv_redacted: list[str],
    stdin_text: str | None,
    cwd: Path,
    env: dict[str, str] | None,
    raw_dir: Path,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    cancel_on_delta: bool = False,
    extra_literals: list[str] | None = None,
) -> RawCapture:
    stdout_path = raw_dir / f"{scenario}.stdout.jsonl"
    stderr_path = raw_dir / f"{scenario}.stderr.txt"

    proc = subprocess.Popen(
        argv,
        cwd=cwd,
        env=env,
        stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
        shell=False,
    )

    if stdin_text is not None and proc.stdin is not None:
        with contextlib.suppress(BrokenPipeError, OSError):
            proc.stdin.write(stdin_text.encode("utf-8"))
            proc.stdin.close()

    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
    cancelled = False
    timed_out = False

    def _read_stdout() -> None:
        nonlocal cancelled
        assert proc.stdout is not None
        for line in proc.stdout:
            stdout_chunks.append(line)
            if cancel_on_delta and not cancelled:
                decoded = line.decode("utf-8", errors="replace")
                if _should_cancel_on_line(cli, decoded):
                    cancelled = True
                    _kill_process_group(proc, force=False)

    def _read_stderr() -> None:
        assert proc.stderr is not None
        stderr_chunks.append(proc.stderr.read())

    t_out = threading.Thread(target=_read_stdout, daemon=True)
    t_err = threading.Thread(target=_read_stderr, daemon=True)
    t_out.start()
    t_err.start()

    try:
        returncode: int | None = proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_process_group(proc, force=False)
        try:
            returncode = proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _kill_process_group(proc, force=True)
            returncode = proc.wait(timeout=5)
    finally:
        _kill_process_group(proc, force=True)
        t_out.join(timeout=5)
        t_err.join(timeout=5)

    stdout_path.write_bytes(b"".join(stdout_chunks))
    stderr_path.write_bytes(b"".join(stderr_chunks))

    return RawCapture(
        cli=cli,
        version=version,
        version_output=version_output,
        scenario=scenario,
        argv_redacted=argv_redacted,
        cwd=str(cwd),
        raw_dir=raw_dir,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        returncode=returncode,
        timed_out=timed_out,
        cancelled=cancelled,
        extra_literals=extra_literals or [],
    )


def _extract_session_id(cli: str, stdout_text: str, log_text: str = "") -> str:
    if cli == "claude":
        for line in stdout_text.splitlines():
            with contextlib.suppress(json.JSONDecodeError):
                obj = json.loads(line)
                if isinstance(obj, dict) and isinstance(obj.get("session_id"), str) and obj["session_id"]:
                    return obj["session_id"]
    elif cli == "codex":
        for line in stdout_text.splitlines():
            with contextlib.suppress(json.JSONDecodeError):
                obj = json.loads(line)
                if isinstance(obj, dict) and obj.get("type") == "thread.started" and obj.get("thread_id"):
                    return str(obj["thread_id"])
    elif cli == "pi":
        for line in stdout_text.splitlines():
            with contextlib.suppress(json.JSONDecodeError):
                obj = json.loads(line)
                if isinstance(obj, dict) and obj.get("type") == "session" and obj.get("id"):
                    return str(obj["id"])
    elif cli == "agy":
        for text in (stdout_text, log_text):
            match = _UUID_RE.search(text)
            if match:
                return match.group(0)
        for line in stdout_text.splitlines():
            with contextlib.suppress(json.JSONDecodeError):
                obj = json.loads(line)
                if isinstance(obj, dict) and obj.get("conversation_id"):
                    return str(obj["conversation_id"])
    return ""


def _redact_prompt_in_argv(argv: list[str], prompt: str | None) -> list[str]:
    if prompt is None:
        return list(argv)
    return ["<PROMPT>" if arg == prompt else arg for arg in argv]


def _scenario_specs(cli: str) -> list[str]:
    base = ["plain", "shell", "edit", "reasoning", "invalid_model", "auth_failure", "cancel", "resume"]
    if cli == "claude":
        return [*base, "max_turns", "budget", "no_verbose"]
    if cli == "agy":
        return [s for s in base if s != "auth_failure"] + ["large_prompt", "slash_command"]
    if cli == "pi":
        return [*base, "no_p"]
    return base


def _build_claude_call(
    scenario: str, prompt: str, session_id: str, empty_cfg: Path
) -> tuple[list[str], str | None, dict[str, str] | None]:
    isolation = ["--setting-sources", "", "--strict-mcp-config", "--disable-slash-commands"]
    base = [
        "claude",
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
        "--include-partial-messages",
        *isolation,
        "--model",
        "haiku",
    ]
    env: dict[str, str] | None = None
    if scenario == "no_verbose":
        argv = [
            "claude",
            "-p",
            "--output-format",
            "stream-json",
            *isolation,
            "--model",
            "haiku",
            "--max-budget-usd",
            "0.05",
        ]
        return argv, prompt, None
    if scenario == "budget":
        return [*base, "--max-budget-usd", "0.0001"], prompt, None
    base = [*base, "--max-budget-usd", "0.05"]
    if scenario in ("shell", "edit"):
        argv = [*base, "--permission-mode", "acceptEdits", "--allowedTools", "Bash,Edit,Write,Read"]
    elif scenario == "reasoning":
        argv = [*base, "--effort", "low"]
    elif scenario == "invalid_model":
        argv = [
            "claude",
            "-p",
            "--output-format",
            "stream-json",
            "--verbose",
            "--include-partial-messages",
            *isolation,
            "--model",
            "nonexistent-model-xyz-9999",
            "--max-budget-usd",
            "0.05",
        ]
    elif scenario == "auth_failure":
        argv = base
        env = _auth_failure_env("claude", empty_cfg)
    elif scenario == "resume":
        argv = [*base, "--resume", session_id]
    elif scenario == "max_turns":
        argv = [*base, "--permission-mode", "acceptEdits", "--allowedTools", "Bash,Edit,Write,Read", "--max-turns", "1"]
    else:
        argv = base
    return argv, prompt, env


def _build_codex_call(
    scenario: str, prompt: str, session_id: str, empty_cfg: Path
) -> tuple[list[str], str | None, dict[str, str] | None]:
    env: dict[str, str] | None = None
    if scenario == "resume":
        argv = ["codex", "exec", "resume", "--json", session_id, "-"]
        return argv, prompt, None
    argv = ["codex", "exec", "--json"]
    if scenario in ("shell", "edit", "cancel"):
        argv.extend(["--sandbox", "workspace-write"])
    elif scenario == "reasoning":
        argv.extend(["-c", 'model_reasoning_effort="low"'])
    elif scenario == "invalid_model":
        argv.extend(["--model", "nonexistent-model-xyz-9999"])
    elif scenario == "auth_failure":
        env = _auth_failure_env("codex", empty_cfg)
    argv.append("-")
    return argv, prompt, env


def _build_pi_call(
    scenario: str, prompt: str, session_dir: Path, session_id: str, empty_cfg: Path
) -> tuple[list[str], str | None, dict[str, str] | None]:
    env: dict[str, str] | None = None
    model = "openai-codex/gpt-5.6-luna"
    isolation = [
        "--no-extensions",
        "--no-skills",
        "--no-prompt-templates",
        "--no-themes",
        "--no-context-files",
        "--no-mcp",
    ]
    if scenario == "no_p":
        argv = ["pi", "--mode", "json", *isolation, "--model", model, "--session-dir", str(session_dir)]
        return argv, prompt, None
    argv = ["pi", "-p", "--mode", "json", *isolation, "--session-dir", str(session_dir)]
    if scenario == "invalid_model":
        argv.extend(["--model", "nonexistent-model-xyz-9999"])
    else:
        argv.extend(["--model", model])
    if scenario == "reasoning":
        argv.extend(["--thinking", "low"])
    elif scenario == "auth_failure":
        env = _auth_failure_env("pi", empty_cfg)
    elif scenario == "resume" and session_id:
        argv.extend(["--session", session_id])
    return argv, prompt, env


_LARGE_PROMPT_MARKER = "LARGE-PROMPT-MARKER-7F3A"
_LARGE_PROMPT = (
    "Ignore this filler line for context padding.\n" * 3200
    + f"\nIgnore all previous filler lines. Reply with exactly: {_LARGE_PROMPT_MARKER}"
)


def _build_agy_call(
    scenario: str, prompt: str, log_path: Path, session_id: str, empty_cfg: Path
) -> tuple[list[str], str | None, dict[str, str] | None]:
    env: dict[str, str] | None = None
    argv = [
        "agy",
        "--output-format",
        "stream-json",
        "--input-format",
        "stream-json",
    ]
    if scenario != "slash_command":
        argv.append("--disable-slash-commands")
    argv.extend(["--log-file", str(log_path)])
    if scenario == "resume" and session_id:
        argv.extend(["--conversation", session_id])
    else:
        argv.append("--new-project")

    if scenario == "invalid_model":
        argv.extend(["--model", "nonexistent-model-xyz-9999"])
    else:
        argv.extend(["--model", "gemini-3.8-flash-low"])

    if scenario in ("shell", "edit"):
        argv.append("--dangerously-skip-permissions")
    elif scenario == "reasoning":
        argv.extend(["--effort", "low"])

    argv.append("--print=")
    stdin_payload = json.dumps({"event": "user", "message": {"content": [{"type": "text", "text": prompt}]}})
    return argv, stdin_payload + "\n", env


def _prompt_for_scenario(scenario: str, *, cli: str = "") -> str:
    if cli == "codex" and scenario == "cancel":
        return (
            "First reply with a short sentence saying 'Part 1', then run `sleep 5` in shell, "
            "then reply with 'Part 2'. Do not read files outside the current directory."
        )
    prompts = {
        "plain": "Reply with exactly the word: PONG",
        "shell": "Run the shell command `ls` in the current directory and list the file names.",
        "edit": (
            "Edit hello.txt in the current directory to add a second line containing 'world'. "
            "Do not read files outside the current directory."
        ),
        "reasoning": "What is 17 * 23? Reply with the number.",
        "invalid_model": "Reply with hello",
        "auth_failure": "Reply with hello",
        "cancel": "Write a 200-word paragraph about the ocean, one sentence at a time.",
        "resume": "What single word did you just reply with?",
        "max_turns": "First run `ls` with Bash, then edit hello.txt to add 'done'.",
        "budget": "Write a short poem about trees.",
        "no_verbose": "Reply with hello",
        "large_prompt": _LARGE_PROMPT,
        "slash_command": "/help",
        "no_p": "Reply with exactly the word: PONG",
    }
    return prompts[scenario]


def _parsed_json_lines(stdout_text: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in stdout_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        with contextlib.suppress(json.JSONDecodeError):
            obj = json.loads(stripped)
            if isinstance(obj, dict):
                events.append(obj)
    return events


def _has_assistant_text_block(content: Any) -> bool:
    if not isinstance(content, list):
        return False
    return any(
        isinstance(block, dict)
        and block.get("type") == "text"
        and isinstance(block.get("text"), str)
        and bool(block["text"])
        for block in content
    )


def _has_assistant_text(stdout_text: str, events: list[dict[str, Any]]) -> bool:
    for obj in events:
        typ = obj.get("type")
        if typ == "stream_event":
            ev = obj.get("event")
            if isinstance(ev, dict) and ev.get("type") == "content_block_delta":
                delta = ev.get("delta")
                if (
                    isinstance(delta, dict)
                    and delta.get("type") == "text_delta"
                    and isinstance(delta.get("text"), str)
                    and delta["text"]
                ):
                    return True
        if typ == "assistant" and not obj.get("error"):
            msg = obj.get("message")
            if isinstance(msg, dict) and _has_assistant_text_block(msg.get("content")):
                return True
        if typ == "result" and not obj.get("is_error"):
            res_text = obj.get("result")
            if isinstance(res_text, str) and res_text:
                return True
        if typ == "item.completed":
            item = obj.get("item")
            if (
                isinstance(item, dict)
                and item.get("type") == "agent_message"
                and isinstance(item.get("text"), str)
                and item["text"]
            ):
                return True
        if typ == "message_update":
            ame = obj.get("assistantMessageEvent")
            if (
                isinstance(ame, dict)
                and ame.get("type") == "text_delta"
                and isinstance(ame.get("delta"), str)
                and ame["delta"]
            ):
                return True
        if typ == "message_end":
            msg = obj.get("message")
            if (
                isinstance(msg, dict)
                and msg.get("role") == "assistant"
                and _has_assistant_text_block(msg.get("content"))
            ):
                return True
        if obj.get("event") == "step_update":
            step = obj.get("step_update")
            if isinstance(step, dict) and step.get("step_type") == "agent_response":
                td = step.get("text_delta")
                txt = step.get("text")
                if (isinstance(td, str) and td) or (isinstance(txt, str) and txt):
                    return True
        if obj.get("event") == "result":
            res = obj.get("result")
            if (
                isinstance(res, dict)
                and res.get("status") == "SUCCESS"
                and isinstance(res.get("response"), str)
                and res["response"]
            ):
                return True

    # Plain-text stdout fallback (e.g. Agy non-JSON stdout).
    for line in stdout_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            json.loads(stripped)
        except json.JSONDecodeError:
            return True
    return False


def derive_outcome_code(
    *,
    cancelled: bool,
    timed_out: bool,
    returncode: int | None,
    stdout_text: str,
    stderr_text: str,
) -> tuple[str, str | None]:
    """Derive ``(outcome, code)`` from captured evidence per DESIGN.md precedence."""
    # 1. Cancelled
    if cancelled:
        return "cancelled", "cancelled"

    events = _parsed_json_lines(stdout_text)
    error_texts: list[str] = [stderr_text]
    for obj in events:
        if obj.get("type") in ("error", "turn.failed"):
            error_texts.append(json.dumps(obj))
        elif obj.get("type") == "assistant" and obj.get("error"):
            error_texts.append(json.dumps(obj))
        elif obj.get("type") == "result" and obj.get("is_error"):
            error_texts.append(json.dumps(obj))
        elif (
            obj.get("event") == "result"
            and isinstance(obj.get("result"), dict)
            and obj["result"].get("status") == "ERROR"
        ):
            error_texts.append(json.dumps(obj))
    error_lower = "\n".join(error_texts).lower()

    # 4. Auth failure (precedes timeout)
    for obj in events:
        if obj.get("error") == "authentication_failed":
            return "agent_fatal", "auth"
    if any(
        marker in error_lower
        for marker in (
            "authentication_failed",
            "not logged in",
            "401 unauthorized",
            "missing bearer or basic authentication",
            "no api key found",
            "invalid api key",
            "authentication error",
        )
    ):
        return "agent_fatal", "auth"

    # 5. Invalid model or context overflow (precedes timeout)
    if any(
        marker in error_lower
        for marker in (
            "unrecognized_model",
            "invalid model",
            "model is not supported",
            "not supported when using codex",
            "unknown model",
        )
    ) or ("model" in error_lower and "not found" in error_lower):
        return "request_fatal", "invalid_model"
    if any(
        marker in error_lower
        for marker in (
            "context_length_exceeded",
            "context length exceeded",
            "maximum context length",
            "prompt is too long",
        )
    ):
        return "request_fatal", "context_overflow"

    # 7. Caller-set limit (precedes timeout)
    for obj in events:
        if obj.get("type") == "result":
            subtype = obj.get("subtype")
            reason = obj.get("terminal_reason")
            if subtype == "error_max_turns" or reason == "max_turns":
                return "limit_reached", "max_turns"
            if subtype == "error_max_budget_usd" or reason == "budget_exhausted":
                return "limit_reached", "budget"

    # 8. Deadline
    if timed_out:
        return "retryable", "timeout"

    # 9. Service error (HTTP 5xx, overload, rate limit)
    if _HTTP_5XX_RE.search(error_lower) or any(
        marker in error_lower for marker in ("rate limit", "rate_limit", "overloaded")
    ):
        return "retryable", "service_error"

    # 10. Nonzero exit or structured failure
    structured_failure = any(
        obj.get("type") == "turn.failed"
        or (obj.get("type") == "result" and bool(obj.get("is_error")))
        or (
            obj.get("event") == "result"
            and isinstance(obj.get("result"), dict)
            and obj["result"].get("status") == "ERROR"
        )
        for obj in events
    )
    if returncode not in (0, None) or structured_failure:
        return "retryable", "execution_error"

    # 11. No text / output
    if not _has_assistant_text(stdout_text, events):
        return "retryable", "no_output"

    # 12. Otherwise
    return "success", None


def _verified_temp_parent(repo_root: Path = REPO_ROOT) -> Path:
    temp_parent = Path(tempfile.gettempdir()).resolve()
    resolved_root = repo_root.resolve()
    if temp_parent == resolved_root or temp_parent.is_relative_to(resolved_root):
        raise RuntimeError(
            f"Refusing to record: temporary directory parent {temp_parent} is inside repository root {resolved_root}"
        )
    return temp_parent


def record_cli(cli: str, scenarios: Sequence[str] | None = None, *, timeout_s: float = DEFAULT_TIMEOUT_S) -> Path:
    selected = list(scenarios) if scenarios else _scenario_specs(cli)
    if cli == "agy" and "auth_failure" in selected:
        raise ValueError("Agy auth_failure scenario is not supported: credentials are not isolatable")

    temp_parent = _verified_temp_parent()
    version, version_output = _get_cli_version(cli)
    raw_root = Path(tempfile.mkdtemp(prefix=f"cac-raw-{cli}-", dir=temp_parent))
    os.chmod(raw_root, 0o700)
    work_root = Path(tempfile.mkdtemp(prefix=f"cac-work-{cli}-", dir=temp_parent))

    captures: list[RawCapture] = []
    cwds: list[str] = []
    extra_literals: list[str] = [str(raw_root), str(work_root)]
    session_id = ""

    pi_session_dir = raw_root / "pi-sessions"
    pi_session_dir.mkdir(exist_ok=True)
    plain_repo: Path | None = None

    try:
        for scenario in selected:
            if scenario == "resume" and plain_repo is not None:
                repo = plain_repo
            else:
                repo = _make_throwaway_repo(work_root, scenario)
                if scenario == "plain":
                    plain_repo = repo
            cwds.append(str(repo))
            empty_cfg = raw_root / f"empty-cfg-{scenario}"
            empty_cfg.mkdir(exist_ok=True)
            extra_literals.append(str(empty_cfg))
            prompt = _prompt_for_scenario(scenario, cli=cli)
            log_path = raw_root / f"{scenario}.agy.log"
            extra_literals.append(str(log_path))

            if cli == "claude":
                argv, stdin_text, env = _build_claude_call(scenario, prompt, session_id, empty_cfg)
            elif cli == "codex":
                argv, stdin_text, env = _build_codex_call(scenario, prompt, session_id, empty_cfg)
            elif cli == "pi":
                argv, stdin_text, env = _build_pi_call(scenario, prompt, pi_session_dir, session_id, empty_cfg)
            elif cli == "agy":
                argv, stdin_text, env = _build_agy_call(scenario, prompt, log_path, session_id, empty_cfg)
            else:
                raise ValueError(f"Unsupported CLI: {cli}")

            argv_redacted = _redact_prompt_in_argv(argv, prompt)
            capture = _run_capture(
                cli=cli,
                version=version,
                version_output=version_output,
                scenario=scenario,
                argv=argv,
                argv_redacted=argv_redacted,
                stdin_text=stdin_text,
                cwd=repo,
                env=env,
                raw_dir=raw_root,
                timeout_s=timeout_s,
                cancel_on_delta=(scenario == "cancel"),
                extra_literals=extra_literals,
            )
            captures.append(capture)

            if scenario == "plain":
                stdout_text = capture.stdout_path.read_text(encoding="utf-8", errors="replace")
                log_text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
                session_id = _extract_session_id(cli, stdout_text, log_text)

        out_dir = RECORDED_ROOT / cli / version
        out_dir.mkdir(parents=True, exist_ok=True)
        scrubber = Scrubber(cwds=cwds, extra_literals=extra_literals)

        for cap in captures:
            raw_stdout_text = cap.stdout_path.read_text(encoding="utf-8", errors="replace")
            stdout_lines = raw_stdout_text.splitlines(keepends=True)
            scrubbed_stdout = "".join(scrubber.scrub_jsonl_line(line) for line in stdout_lines)
            (out_dir / f"{cap.scenario}.stdout.jsonl").write_text(scrubbed_stdout, encoding="utf-8")

            stderr_text = cap.stderr_path.read_text(encoding="utf-8", errors="replace")
            (out_dir / f"{cap.scenario}.stderr.txt").write_text(scrubber.scrub_text(stderr_text), encoding="utf-8")

            outcome, code = derive_outcome_code(
                cancelled=cap.cancelled,
                timed_out=cap.timed_out,
                returncode=cap.returncode,
                stdout_text=raw_stdout_text,
                stderr_text=stderr_text,
            )
            meta: dict[str, Any] = {
                "cli": cap.cli,
                "version": cap.version,
                "version_output": scrubber.scrub_text(cap.version_output),
                "scenario": cap.scenario,
                "argv": [scrubber.scrub_text(a) for a in cap.argv_redacted],
                "returncode": cap.returncode,
                "timed_out": cap.timed_out,
                "cancelled": cap.cancelled,
                "outcome": outcome,
                "code": code,
            }
            (out_dir / f"{cap.scenario}.meta.json").write_text(
                json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
        return out_dir
    finally:
        shutil.rmtree(work_root, ignore_errors=True)
        shutil.rmtree(raw_root, ignore_errors=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Record and scrub real coding-agent CLI fixtures.")
    parser.add_argument("--cli", action="append", choices=("claude", "codex", "pi", "agy"), help="CLI(s) to record")
    parser.add_argument("--scenario", action="append", help="Specific scenario(s) to record")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S, help="Per-scenario timeout in seconds")
    args = parser.parse_args(argv)

    clis = args.cli or ["claude", "codex", "pi", "agy"]
    for cli in clis:
        start = time.monotonic()
        out_dir = record_cli(cli, args.scenario, timeout_s=args.timeout)
        elapsed = time.monotonic() - start
        print(f"Recorded {cli} -> {out_dir.relative_to(REPO_ROOT)} ({elapsed:.1f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
