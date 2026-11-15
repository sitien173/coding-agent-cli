# Phase 2: Live evidence, fixtures, and docs

## Goal & Outcome
- Plan Goal: The agy driver delivers every prompt on stdin through `--input-format stream-json`, so the 100 KiB pre-spawn cap is gone and the prompt never appears in argv.
- Phase Outcome: Recorded agy 1.5.9 fixtures prove stdin delivery, large input, resume, model error, and slash-command behavior, and `FINDINGS.md` and `README.md` match.

## Context & Architecture
- Code root: `/home/ngosi/projects/coding-agent-cli` | Records root: `/home/ngosi/projects/coding-agent-cli` | Branch: `main` | Base: `refs/plans/agy-stdin-prompt/phase-02/base`
- Prior State: Phase 1 done. `AgyDriver.launch` in `src/coding_agent_cli/_drivers/agy.py` sends stdin `json.dumps({"event": "user", "message": {"content": [{"type": "text", "text": prompt}]}}) + "\n"` encoded UTF-8, with argv `agy --output-format stream-json --input-format stream-json --log-file <log> [--conversation <id> | --new-project] [--model m] [--effort e] <args> --print=`. Baseline at phase base: pytest 380 passed, 1 skipped, 4 deselected; pyright 0 errors; ruff clean.
- Code Context:
  - `scripts/record_fixtures.py`: `_scenario_specs` near line 300 returns, for agy, the base scenarios minus `auth_failure` plus `stdin_stream_json`. `_build_agy_call` near line 423 builds the `stdin_stream_json` case with the wrong schema `{"type": "user", ...}` and `--print ""`, and builds every other agy scenario with `--disable-slash-commands`, `--model gemini-3.8-flash-low` or the invalid model, `--dangerously-skip-permissions` for `shell` and `edit`, `--effort low` for `reasoning`, and `--print <prompt>` last, returning `(argv, None, env)`. `_prompt_for_scenario` near line 467 maps scenarios to prompts. `_redact_prompt_in_argv` replaces the prompt in argv with `<PROMPT>`. `record_cli` near line 719 runs scenarios, extracts the session ID from the `plain` capture and agy log for `resume`, and writes fixtures under `tests/fixtures/recorded/<cli>/<version>/`.
  - `tests/test_agy_driver.py`: `test_replay_recorded_agy_scenarios` globs every `*.meta.json` in `tests/fixtures/recorded/agy/1.5.9/` and checks `outcome` and `code` from the meta against the replayed parser result. Replay passes an empty `read_back`.
  - Zero-token probe evidence on agy 1.5.9: the input schema above is accepted; `--print=` is accepted; a `/help` text message under stdin mode fails with exit 2, result status `ERROR`, num_turns 0, error "/help is answered by the CLI itself and is unavailable with --input-format stream-json".
  - `tests/fixtures/recorded/FINDINGS.md` open question 1 says the agy stream-json input schema is not settled and v1 argv delivery stays.
  - `README.md` Known limits contains the line "Agy receives the prompt in argv, which is limited to 100 KiB by the driver."

## Invariants & Boundaries
- Preserve: no runtime dependency; the driver never adds a permission, approval, or sandbox flag; no prompt, argv, or environment value logged; fixtures sanitized by the existing script redaction.
- `--dangerously-skip-permissions` stays only in the recording script for `shell` and `edit`, never in the driver.
- Edit `src/coding_agent_cli/_drivers/agy.py` and `tests/test_agy_driver.py` only for this conditional fix: if the recorded agy log in stdin mode lacks the "Created conversation <uuid>" or "Streaming conversation <uuid>" line, make `AgyParser.finish` fall back to the `conversation_id` from the `result` or `init` event, with a test.
- Out of Scope: `_transport.py`, `driver.py`, `_platform.py`, other drivers, other CLIs' fixtures, `CHANGELOG.md`. Do not run `pytest -m live`. Do not record claude, codex, or pi.
- Stop rule: if agy does not exit after end of input, or the `large_prompt` response lacks the marker, stop: return `BLOCKED` with the evidence and do not edit docs.

## Files
Allowed production and test paths:
- `scripts/record_fixtures.py` (modify: agy stdin delivery, `large_prompt` and `slash_command` scenarios, drop `stdin_stream_json`)
- `tests/test_record_fixtures.py` (modify: tests for agy argv, stdin line, `large_prompt` size, without launching agy)
- `tests/fixtures/recorded/agy/1.5.9/` (re-record default agy scenarios; create `large_prompt.*` and `slash_command.*`; delete `stdin_stream_json.*`)
- `tests/fixtures/recorded/FINDINGS.md` (modify: open question 1 settled; agy argv rows if any)
- `README.md` (modify: Known limits agy line only)
- Conditional: `src/coding_agent_cli/_drivers/agy.py`, `tests/test_agy_driver.py`

Worker-owned phase records:
- `/home/ngosi/projects/coding-agent-cli/docs/plans/agy-stdin-prompt/phase-02/notes.md` (append `## Task <M>` blocks)
- `/home/ngosi/projects/coding-agent-cli/docs/plans/agy-stdin-prompt/phase-02/journal.md` (append `# EXTERNAL RESPONSE` under `## Implementation Response`)

Read-only / Forbidden:
- Do not edit `PLAN.md`, `DESIGN.md`, `.handover.md`, `prompt.md`, other phases, dependencies, or secrets.

## Tasks
- task-1: RED then GREEN, no live calls. In `scripts/record_fixtures.py`:
  - Every agy scenario uses `--output-format stream-json --input-format stream-json` and ends argv with `--print=`, and returns stdin text `json.dumps({"event": "user", "message": {"content": [{"type": "text", "text": prompt}]}}) + "\n"`. Keep existing per-scenario flags.
  - Replace `stdin_stream_json` with `large_prompt` and `slash_command` in `_scenario_specs("agy")` and `_prompt_for_scenario`.
  - `large_prompt`: deterministic filler above 131,072 UTF-8 bytes, followed by an instruction to reply with exactly a fixed marker such as `LARGE-PROMPT-MARKER-7F3A`. Keep the marker text out of the filler.
  - `slash_command`: prompt `/help`; omit `--disable-slash-commands` for this scenario only.
  - Add tests in `tests/test_record_fixtures.py` for the agy argv, the stdin line round-trip, the `large_prompt` size and marker, and `slash_command` omitting `--disable-slash-commands`. Record RED then GREEN.
- task-2: Live recording, pre-approved by the user. Run `export VIRTUAL_ENV=; uv run python scripts/record_fixtures.py --cli agy`. Confirm from the captures and record in notes: `plain` exits 0 and its response contains PONG; `large_prompt` exits 0 and its response contains the marker; `resume` succeeds using the session ID from the `plain` log; `invalid_model` yields a `result` with status `ERROR` and the process exits; `slash_command` yields status `ERROR`, exit code 2, and zero tokens. Check whether the agy log contains the conversation line; apply the conditional fix only if it does not. Apply the stop rule on contrary evidence. Confirm `stdin_stream_json.*` fixtures are gone and every agy meta argv contains `--input-format` and `--print=`.
- task-3: Update `FINDINGS.md` open question 1 to settled, citing the new fixtures and the accepted schema `{"event":"user","message":{"content":[{"type":"text","text":"..."}]}}`. Replace the README Known limits agy argv line with one sentence: Agy receives the prompt on stdin through `--input-format stream-json`, and agy rejects CLI-answered slash-command prompts such as `/help` in that mode.

## Done When
- `large_prompt` and `slash_command` fixtures exist; `stdin_stream_json` fixtures do not.
- Every agy `*.meta.json` argv contains `--input-format` and `--print=`.
- `test_replay_recorded_agy_scenarios` passes for every agy scenario.
- `FINDINGS.md` no longer lists open question 1 as not settled.
- Fixtures contain no absolute home path, account email, token, or credential.
- Run from `/home/ngosi/projects/coding-agent-cli`:
  - `export VIRTUAL_ENV=; uv run ruff format && uv run ruff check --fix` (Baseline: clean)
  - `export VIRTUAL_ENV=; uv run pyright` (Baseline: 0 errors)
  - `export VIRTUAL_ENV=; uv run pytest` (Baseline: 380 passed, 1 skipped, 4 deselected)
  - `ls tests/fixtures/recorded/agy/1.5.9/ | grep -c stdin_stream_json` prints `0`
  - `grep -c "limited to 100 KiB" README.md` prints `0`

## Rules
Read and follow these bundled contracts:
- Contract: `/home/ngosi/.claude/plugins/cache/refract/refract/3.0.6/shared/worker-contract.md`
- Response: `/home/ngosi/.claude/plugins/cache/refract/refract/3.0.6/shared/erp.md`
- Notes: `/home/ngosi/.claude/plugins/cache/refract/refract/3.0.6/shared/notes-template.md`
- Journal: `/home/ngosi/.claude/plugins/cache/refract/refract/3.0.6/shared/journal-template.md`

Also follow `/home/ngosi/projects/coding-agent-cli/AGENTS.md` and `CONTRIBUTING.md`. Match surrounding code; keep the diff minimal.

Role boundary: You are the phase implementer, not Coordinator. Edit code only in `/home/ngosi/projects/coding-agent-cli`; write phase records only under `/home/ngosi/projects/coding-agent-cli/docs/plans/agy-stdin-prompt/phase-02/`. Leave Coordinator skills and Refract job submission to the Coordinator. Do not commit.

## Response Format
Append the complete `# EXTERNAL RESPONSE` block to `/home/ngosi/projects/coding-agent-cli/docs/plans/agy-stdin-prompt/phase-02/journal.md`, then return that block and the matching status line from `erp.md`.
