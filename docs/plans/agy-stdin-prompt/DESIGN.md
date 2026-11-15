# Agy Stdin Prompt Delivery Design

**Status:** Confirmed 2026-11-15
**Consultation:** openai consult `32323581` ranked stdin delivery first, then hybrid, raised cap, and pointer file; google consult `f2bd0f83` result unreadable, `response_too_large` on `wait` and `get`.

## Purpose

The agy driver rejects any prompt above 100 KiB, 102,400 bytes, with `invalid_prompt` before it starts `agy`. The cap exists because the prompt travels in argv as `--print <prompt>`, and Linux limits one argv string to 128 KiB. The claude, codex, and pi drivers send the prompt on stdin and have no such cap. This change sends the agy prompt on stdin so large prompts run.

## Users and Success Criteria

Users: library callers that run `agy` through `run`, `stream`, or `run_sync`.

Success criteria:

- A prompt above 128 KiB reaches agy intact and the run completes.
- The prompt never appears in argv.
- NUL bytes and invalid UTF-8 still fail before spawn with `invalid_prompt`.
- New-conversation and `--conversation` resume runs still report a session ID.
- `uv run ruff format`, `uv run ruff check --fix`, `uv run pyright`, and `uv run pytest` pass.

## Constraints, Invariants, and Non-Goals

Constraints and invariants, from `AGENTS.md`:

- No runtime dependency.
- The driver never adds a permission, approval, or sandbox-bypass flag.
- No prompt, argv, or environment value is logged.
- Every exit path kills the process tree and deletes temp files.
- Public signatures and README examples stay in sync.
- Owned-flag rejection still covers `--input-format`, `--print`, `-p`, and `--prompt`.

Non-goals:

- Multi-turn stream-json sessions over one process.
- Changes to the transport, the public API, or the other drivers.
- Special handling of slash-command prompts.
- A new prompt size cap.

## Chosen Approach

Always deliver the agy prompt as one stream-json line on stdin.

Rejected alternatives:

- Hybrid argv below 100 KiB and stdin above it: two code paths, and slash-command behavior would depend on prompt size.
- Raise the argv cap toward 128 KiB: gains about 28 KiB and stays platform-dependent.
- Write the prompt to a temp file and pass a pointer prompt: changes model input, and headless agy auto-denies `read_file` without an allow rule.

## Architecture and Data Flow

Evidence, agy 1.5.9 on Linux, from zero-token probes on 2026-11-15:

- `--input-format stream-json` reads NDJSON from stdin and runs one turn per line. It requires `--output-format stream-json`.
- The accepted input line is `{"event":"user","message":{"content":[{"type":"text","text":"..."}]}}`. Only `text` content blocks are accepted.
- `--print=` with an empty value is accepted in this mode.
- `-p -` is not a stdin sentinel; agy treats `-` as the prompt.
- The earlier fixture `tests/fixtures/recorded/agy/1.5.9/stdin_stream_json` failed because it sent `{"type":"user",...}`, which lacks the `event` field.

Changes, all in `src/coding_agent_cli/_drivers/agy.py`:

1. `AgyDriver.launch` builds argv in this order: executable, `--output-format stream-json`, `--input-format stream-json`, `--log-file <tmp>/agy.log`, then `--conversation <id>` or `--new-project`, then `--model` and `--effort` when set, then `req.args`, then `--print=` last.
2. `Launch.stdin` is `json.dumps({"event": "user", "message": {"content": [{"type": "text", "text": prompt}]}})` encoded as UTF-8 plus `\n`. `_transport._write_stdin` writes, drains, and closes stdin, so agy reads end of input after one turn.
3. `_validate_prompt` keeps the NUL and invalid UTF-8 checks and drops the size check. `PROMPT_LIMIT_BYTES` is removed; it is private to `_drivers.agy`.
4. `_validate_command_line` stays for Windows. It now bounds `req.args` and fixed flags only, and its message no longer refers to the prompt.
5. `AgyParser` is unchanged unless live fixtures show a different event shape in stdin mode.

`--print=` is used instead of `--print ""` because the Windows `.ps1` shim path in `_platform.prepare_command` can drop an empty argument.

## Errors and Edge Cases

- Slash-command prompts: agy rejects CLI-answered slash commands such as `/help` in stdin mode with exit code 2 and a `result.status` of `ERROR`. The driver adds no special handling; README documents the limit. The run surfaces as `execution_error` through the existing parser path.
- NUL bytes and unpaired surrogates: rejected before spawn with `invalid_prompt`, as today.
- Early agy exit while stdin is written: handled by the existing `stdin_failed` path.
- Model error during the turn: the agy changelog says stream-json sessions "warn and continue". With one line and end of input, agy is expected to emit an `ERROR` result and exit. A live fixture confirms this; the parser changes only on contrary evidence.
- Session ID: extraction from the log file stays. If the live fixture shows the log lacks the conversation line in stdin mode, fall back to `conversation_id` from the `result` event, backed by that fixture.
- No automatic argv fallback after spawn, so a prompt is never run or billed twice.

## Migration

No public API change. `PROMPT_LIMIT_BYTES` was never exported from the package. Behavior change for callers: agy prompts above 100 KiB now run, and agy slash-command prompts now fail. The commit message flags the slash-command change. `CHANGELOG.md` is managed by release-please and is not edited by hand.

## Testing

Unit tests in `tests/test_agy_driver.py`:

- Replace `test_launch_rejects_oversized_and_invalid_prompt`: a 200 KiB prompt is accepted; NUL and unpaired surrogate prompts still raise `invalid_prompt`.
- `Launch.stdin` is one line ending in `\n` whose `json.loads` round-trips the prompt, covering multiline text, Unicode, quotes, and backslashes.
- The prompt is absent from argv, `--input-format stream-json` is present, and `--print=` is the last argv token.
- Existing argv tests are updated for the new flags; the permission-flag test is unchanged.
- Recorded replay tests keep passing.

Recording script `scripts/record_fixtures.py`:

- `_build_agy_call` builds the same argv and stdin as the driver.
- Re-record agy 1.5.9 fixtures `plain`, `resume`, and `invalid_model` through stdin.
- Add `large_prompt`, above 128 KiB, with a marker at the end that the reply must echo.
- Add `slash_command`, which spends no tokens.
- Replace the `stdin_stream_json` fixture, which recorded the wrong input schema.
- Recording spends tokens and runs only after explicit user approval during execution. `pytest -m live` is not run.

Docs:

- `tests/fixtures/recorded/FINDINGS.md` open question 1 is marked settled, citing the new fixtures.
- `README.md` Known limits replaces the 100 KiB argv line with stdin delivery and the slash-command limit.

Stop rule: if live fixtures show agy does not exit after end of input, or truncates large input, execution stops and the design returns to the user before shipping.

## Risks and Open Questions

- Unverified until live recording: one turn and clean exit on end of input; integrity above 128 KiB; `--conversation` resume in stdin mode; session-ID line in the log file; exit code and result on model error.
- Agy may enforce its own NDJSON line limit. None is known; any cap added later must cite a measured limit.
- Windows: empty-argument handling and stdin delivery through the `.ps1` shim are reasoned, not tested on Windows.
- The input schema is undocumented by agy and inferred from its validation errors; a future agy version may change it.
