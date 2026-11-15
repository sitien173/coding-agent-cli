# Phase 1: Driver sends the prompt on stdin

## Goal & Outcome
- Plan Goal: The agy driver delivers every prompt on stdin through `--input-format stream-json`, so the 100 KiB pre-spawn cap is gone and the prompt never appears in argv.
- Phase Outcome: `AgyDriver.launch` returns argv without the prompt and `Launch.stdin` carrying the prompt as one JSON line. A 200 KiB prompt launches without `RequestError`.

## Context & Architecture
- Code root: `/home/ngosi/projects/coding-agent-cli` | Records root: `/home/ngosi/projects/coding-agent-cli` | Branch: `main` | Base: `refs/plans/agy-stdin-prompt/phase-01/base`
- Prior State: Baseline at the plan base is green: `ruff format --check` 108 files formatted, `ruff check` passed, `pyright` 0 errors, `pytest` 374 passed, 1 skipped, 4 deselected.
- Code Context:
  - `src/coding_agent_cli/_drivers/agy.py`:
    - Line 1 module docstring says "prompt in argv".
    - Line 29 `PROMPT_LIMIT_BYTES = 100 * 1024`; line 316 `__all__ = ["PROMPT_LIMIT_BYTES", "AgyDriver", "AgyParser"]`.
    - Line 57 `_validate_prompt` rejects NUL, invalid UTF-8, and size above `PROMPT_LIMIT_BYTES`, all with `RequestError(ErrorCode.INVALID_PROMPT, ...)`. Its docstring says "cannot be passed in argv".
    - Line 72 `_validate_command_line` enforces the Windows 32767-character limit and its message says "agy command line is ...". It runs only when `os.name == "nt"`.
    - Line 280 `AgyDriver` docstring says "with the prompt in argv". Line 286 `launch` validates, builds argv, appends `["--print", req.prompt]` last, calls `_validate_command_line(argv)`, and returns `Launch(argv=tuple(argv), read_back=(log_path,))`.
  - `src/coding_agent_cli/driver.py:25` `Launch` has `argv`, `stdin: bytes | None = None`, `read_back`.
  - `src/coding_agent_cli/_transport.py:61` `_write_stdin` writes, drains, and closes stdin.
  - Reuse pattern: `src/coding_agent_cli/_drivers/claude.py:443`, `codex.py:403`, and `pi.py:224` return `Launch(argv=tuple(argv), stdin=req.prompt.encode("utf-8"))`.
  - Agy 1.5.9 evidence from zero-token probes: `--input-format stream-json` requires `--output-format stream-json`, reads one NDJSON line per turn, and accepts `{"event": "user", "message": {"content": [{"type": "text", "text": "..."}]}}`. Only `text` content blocks are accepted. `--print=` as one token is accepted.
  - `tests/test_agy_driver.py`: `_req` helper at line 28; `test_launch_new_project_and_log_file_read_back` at line 38 asserts `launch.stdin is None` and `launch.argv[-2:] == ("--print", "hello")`; `test_launch_rejects_oversized_and_invalid_prompt` at line 87 imports and uses `PROMPT_LIMIT_BYTES` from line 24.

## Invariants & Boundaries
- Preserve:
  - No runtime dependency; stdlib only.
  - The driver never adds a permission, approval, or sandbox-bypass flag.
  - No prompt, argv, or environment value in any log call or error message.
  - NUL bytes and unpaired surrogates still raise `RequestError` with `ErrorCode.INVALID_PROMPT` before spawn.
  - `_OWNED_FLAGS` unchanged; caller `req.args` with `--input-format`, `--print`, `-p`, or `--prompt` still raise `INVALID_ARGS`.
  - `_validate_command_line` kept for Windows, applied to the final argv.
  - `AgyParser` unchanged.
- Out of Scope: `_transport.py`, `driver.py`, `_platform.py`, other drivers, `scripts/`, `tests/fixtures/`, `README.md`, `FINDINGS.md`, `CHANGELOG.md`, Phase 2 work. Do not run the `agy` binary. Do not run `pytest -m live`. No new size cap.

## Files
Allowed production and test paths:
- `src/coding_agent_cli/_drivers/agy.py` (modify: stdin delivery, argv order, drop size check and `PROMPT_LIMIT_BYTES`, docstrings and Windows message)
- `tests/test_agy_driver.py` (modify: launch tests for stdin delivery and residual validation)

Worker-owned phase records:
- `/home/ngosi/projects/coding-agent-cli/docs/plans/agy-stdin-prompt/phase-01/notes.md` (append `## Task <M>` blocks)
- `/home/ngosi/projects/coding-agent-cli/docs/plans/agy-stdin-prompt/phase-01/journal.md` (append `# EXTERNAL RESPONSE` under `## Implementation Response`)

Read-only / Forbidden:
- Do not edit `PLAN.md`, `DESIGN.md`, `.handover.md`, `prompt.md`, other phases, dependencies, or secrets.

## Tasks
- task-1: RED. In `tests/test_agy_driver.py`:
  - A 200 KiB ASCII prompt launches without `RequestError`.
  - For prompts covering multiline text, non-ASCII Unicode, double quotes, and backslashes: `launch.stdin` is bytes, ends with exactly one `b"\n"`, contains no other newline byte, and `json.loads(launch.stdin)` equals `{"event": "user", "message": {"content": [{"type": "text", "text": prompt}]}}`.
  - No argv element contains the test prompt.
  - `--output-format`, `--input-format`, and `stream-json` are in argv, with `--input-format` immediately followed by `stream-json`.
  - `launch.argv[-1] == "--print="` and `"--print"` is not a separate argv token.
  - Replace `test_launch_rejects_oversized_and_invalid_prompt` with a test named for invalid prompts that keeps the NUL and `"\ud800"` cases. Remove the `PROMPT_LIMIT_BYTES` import.
  - Update `test_launch_new_project_and_log_file_read_back` and any other argv assertions to the new flags and stdin.
  - Run and record RED with a behavioral assertion failure.
- task-2: GREEN. In `agy.py`:
  - Drop the size check and `PROMPT_LIMIT_BYTES`, including from `__all__`.
  - Keep the NUL and UTF-8 checks.
  - Build argv in this order: executable, `--output-format stream-json`, `--input-format stream-json`, `--log-file <tmp>/agy.log`, then `--conversation <id>` or `--new-project`, then `--model` and `--effort` when set, then `req.args`, then `--print=`.
  - Set `stdin=(json.dumps({"event": "user", "message": {"content": [{"type": "text", "text": req.prompt}]}}) + "\n").encode("utf-8")`. Default `json.dumps` escapes newlines and non-ASCII, so the payload is one line.
  - Update the module, `_validate_prompt`, and `AgyDriver` docstrings and the `_validate_command_line` message so none says the prompt is in argv. Keep the comment explaining why the last token comes last, reworded for `--print=`.
  - Run all verification commands and record GREEN.

## Done When
- A 200 KiB prompt launches without `RequestError`.
- `launch.stdin` round-trips every tested prompt through `json.loads`.
- No argv element equals or contains the test prompt.
- `launch.argv[-1] == "--print="`.
- NUL and unpaired-surrogate prompts raise `RequestError` with `ErrorCode.INVALID_PROMPT`.
- `grep -rn PROMPT_LIMIT_BYTES src tests` prints nothing.
- Run from `/home/ngosi/projects/coding-agent-cli`:
  - `export VIRTUAL_ENV=; uv run ruff format && uv run ruff check --fix` (Baseline: 108 files formatted, all checks passed)
  - `export VIRTUAL_ENV=; uv run pyright` (Baseline: 0 errors)
  - `export VIRTUAL_ENV=; uv run pytest` (Baseline: 374 passed, 1 skipped, 4 deselected)

## Rules
Read and follow these bundled contracts:
- Contract: `/home/ngosi/.claude/plugins/cache/refract/refract/3.0.6/shared/worker-contract.md`
- Response: `/home/ngosi/.claude/plugins/cache/refract/refract/3.0.6/shared/erp.md`
- Notes: `/home/ngosi/.claude/plugins/cache/refract/refract/3.0.6/shared/notes-template.md`
- Journal: `/home/ngosi/.claude/plugins/cache/refract/refract/3.0.6/shared/journal-template.md`

Also follow `/home/ngosi/projects/coding-agent-cli/AGENTS.md` and the coding standard in `CONTRIBUTING.md`. Match surrounding code; keep the diff minimal.

Role boundary: You are the phase implementer, not Coordinator. Edit code only in `/home/ngosi/projects/coding-agent-cli`; write phase records only under `/home/ngosi/projects/coding-agent-cli/docs/plans/agy-stdin-prompt/phase-01/`. Leave Coordinator skills and Refract job submission to the Coordinator. Do not commit.

## Response Format
Append the complete `# EXTERNAL RESPONSE` block to `/home/ngosi/projects/coding-agent-cli/docs/plans/agy-stdin-prompt/phase-01/journal.md`, then return that block and the matching status line from `erp.md`.
