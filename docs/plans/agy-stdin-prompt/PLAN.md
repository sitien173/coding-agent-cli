# Agy Stdin Prompt Delivery Plan

**Design:** [DESIGN.md](DESIGN.md)
**Context key:** `agy-stdin-prompt`
**Base:** `refs/plans/agy-stdin-prompt/base` (`7f93f74`)
**Commit:** `fix(agy): send the prompt on stdin to remove the 100 KiB limit`

Commit body, used at consolidation:

```text
The agy driver now sends the prompt as one stream-json line on stdin
instead of in argv, so prompts above 100 KiB run. Agy rejects
CLI-answered slash-command prompts such as /help in this mode.
```

## Outcome & Scope

Goal: the agy driver delivers every prompt on stdin through `--input-format stream-json`, so the 100 KiB pre-spawn cap is gone and the prompt never appears in argv.

Preserved invariants:

- No runtime dependency.
- The driver never adds a permission, approval, or sandbox-bypass flag.
- No prompt, argv, or environment value is logged.
- Every exit path kills the process tree and deletes temp files.
- NUL bytes and invalid UTF-8 still raise `invalid_prompt` before spawn.
- Owned-flag rejection still covers `--input-format`, `--print`, `-p`, and `--prompt`.

Out of scope: the transport, the public API, the claude, codex, and pi drivers, multi-turn stream-json sessions, slash-command handling, a new size cap, `CHANGELOG.md`, and `pytest -m live`.

## Baseline Evidence

Recorded at `7f93f74` on 2026-11-15 with `export VIRTUAL_ENV=;`:

- `uv run ruff format --check`: 108 files already formatted.
- `uv run ruff check`: all checks passed.
- `uv run pyright`: 0 errors, 0 warnings.
- `uv run pytest`: 374 passed, 1 skipped, 4 deselected.

### Phase 1: Driver sends the prompt on stdin

**Phase Request:** Change `AgyDriver.launch` so the prompt travels as one stream-json line on stdin, per `DESIGN.md` "Architecture and Data Flow". Use cases: a prompt above 128 KiB is accepted; a new-conversation launch; a `--conversation` resume launch; a launch with `--model` and `--effort`; a NUL or unpaired-surrogate prompt is rejected.
**Goal:** `launch` returns argv without the prompt and `Launch.stdin` carrying the prompt as one JSON line.
**Depends on:** none
**Invariants & Boundaries:** Do not change `AgyParser`, `_transport.py`, `driver.py`, `_platform.py`, other drivers, `scripts/`, fixtures, or docs. Do not run the `agy` binary or `pytest -m live`. Keep `_validate_command_line` for Windows.
**Files:**
- Modify: `src/coding_agent_cli/_drivers/agy.py`
- Modify: `tests/test_agy_driver.py`
**Tasks:**
1. Write failing tests in `tests/test_agy_driver.py`. A 200 KiB prompt is accepted. `Launch.stdin` is exactly one line ending in `\n` whose `json.loads` equals `{"event": "user", "message": {"content": [{"type": "text", "text": prompt}]}}`, covering multiline text, non-ASCII Unicode, quotes, and backslashes. The prompt is absent from argv. `--output-format stream-json` and `--input-format stream-json` are present. `--print=` is the last argv token. Replace the oversized case in `test_launch_rejects_oversized_and_invalid_prompt` and keep its NUL and surrogate cases; rename the test to match. Update existing argv assertions for the new flags.
2. In `agy.py`, drop the size check and `PROMPT_LIMIT_BYTES`, including from `__all__`. Keep the NUL and UTF-8 checks. Build argv in the order in `DESIGN.md` and set `Launch.stdin` with `json.dumps` and UTF-8 encoding. Update the module, class, and `_validate_prompt` docstrings and the `_validate_command_line` message so neither refers to the prompt being in argv.
**Acceptance Criteria:**
- A 200 KiB prompt launches without `RequestError`.
- `launch.stdin` round-trips every tested prompt through `json.loads`.
- No argv element equals or contains the test prompt.
- `launch.argv[-1] == "--print="`.
- NUL and unpaired-surrogate prompts raise `RequestError` with `ErrorCode.INVALID_PROMPT`.
- `PROMPT_LIMIT_BYTES` no longer exists in `src/` or `tests/`.
**Reviewer Checklist:**
- No permission, approval, or sandbox flag added.
- `--print=` is one token, not `--print` plus an empty string.
- Caller `req.args` still come before `--print=`.
- No prompt text in any error message or log call.
- Diff limited to the two listed files.
**Verification Checks:**
- `export VIRTUAL_ENV=; uv run ruff format && uv run ruff check --fix`
- `export VIRTUAL_ENV=; uv run pyright`
- `export VIRTUAL_ENV=; uv run pytest`
- `grep -rn PROMPT_LIMIT_BYTES src tests` prints nothing.

### Phase 2: Live evidence, fixtures, and docs

**Phase Request:** Make the recording script use the driver's stdin delivery, re-record the agy 1.5.9 fixtures, and update `FINDINGS.md` and `README.md`, per `DESIGN.md` "Testing". This phase spends tokens. The Coordinator submits it only after the user explicitly approves live recording.
**Goal:** Recorded fixtures prove stdin delivery, large input, resume, model error, and slash-command behavior on agy 1.5.9, and the docs match.
**Depends on:** Phase 1
**Invariants & Boundaries:** Do not run `pytest -m live`. Do not change the transport, the public API, or other drivers. Change `src/coding_agent_cli/_drivers/agy.py` only for the session-ID fallback in `DESIGN.md` "Errors and Edge Cases", and only if the recorded log lacks the conversation line. Stop rule: if agy does not exit after end of input, or the `large_prompt` reply lacks the marker, stop with `BLOCKED` and report the evidence without editing docs.
**Files:**
- Modify: `scripts/record_fixtures.py`
- Modify: `tests/test_record_fixtures.py`
- Create: `tests/fixtures/recorded/agy/1.5.9/large_prompt.{meta.json,stdout.jsonl,stderr.txt}`
- Create: `tests/fixtures/recorded/agy/1.5.9/slash_command.{meta.json,stdout.jsonl,stderr.txt}`
- Modify: `tests/fixtures/recorded/agy/1.5.9/*` for every re-recorded default scenario
- Delete: `tests/fixtures/recorded/agy/1.5.9/stdin_stream_json.{meta.json,stdout.jsonl,stderr.txt}`
- Modify: `tests/fixtures/recorded/FINDINGS.md`
- Modify: `README.md`
- Modify, conditional: `src/coding_agent_cli/_drivers/agy.py`, `tests/test_agy_driver.py`
**Tasks:**
1. In `scripts/record_fixtures.py`, make `_build_agy_call` send every agy scenario as `--input-format stream-json` with the same stdin line and `--print=` as the driver. Replace `stdin_stream_json` with `large_prompt` and `slash_command` in `_scenario_specs("agy")` and `_prompt_for_scenario`. The `large_prompt` prompt is deterministic filler above 131,072 UTF-8 bytes ending with an instruction to reply with a fixed marker. The `slash_command` prompt is `/help` and omits `--disable-slash-commands`. Add tests in `tests/test_record_fixtures.py` for the agy argv, the stdin line, and the `large_prompt` size, without launching agy.
2. Run `export VIRTUAL_ENV=; uv run python scripts/record_fixtures.py --cli agy`. Confirm from the captures: `plain` exits 0 with one turn; `large_prompt` exits 0 and its response contains the marker; `resume` succeeds with the session ID taken from the `plain` log; `invalid_model` yields an `ERROR` result and a process exit; `slash_command` yields an `ERROR` result with exit code 2 and zero tokens. Apply the stop rule on contrary evidence. Apply the session-ID fallback with a test only if the log lacks the conversation line.
3. Update `FINDINGS.md` open question 1 to settled, citing the new fixtures and the accepted input schema. Replace the README Known limits line "Agy receives the prompt in argv, which is limited to 100 KiB by the driver." with: agy receives the prompt on stdin through `--input-format stream-json`, and agy rejects CLI-answered slash-command prompts in that mode.
**Acceptance Criteria:**
- `large_prompt` and `slash_command` fixtures exist; `stdin_stream_json` fixtures do not.
- Every agy `*.meta.json` argv contains `--input-format` and `--print=`, and none contains `<PROMPT>`.
- `test_replay_recorded_agy_scenarios` passes for every agy scenario.
- `FINDINGS.md` no longer lists open question 1 as not settled.
- `README.md` no longer contains "limited to 100 KiB".
- Recorded fixtures contain no absolute home path, token, or credential.
**Reviewer Checklist:**
- Fixture sanitization: no user paths, account emails, or secrets in stdout, stderr, or meta.
- `--dangerously-skip-permissions` appears only in the recording script for `shell` and `edit`, never in the driver.
- The session-ID fallback, if added, is backed by a recorded log and a test.
- README Known limits wording matches the recorded behavior.
**Verification Checks:**
- `export VIRTUAL_ENV=; uv run ruff format && uv run ruff check --fix`
- `export VIRTUAL_ENV=; uv run pyright`
- `export VIRTUAL_ENV=; uv run pytest`
- `ls tests/fixtures/recorded/agy/1.5.9/ | grep -c stdin_stream_json` prints `0`.
- `grep -c "limited to 100 KiB" README.md` prints `0`.
