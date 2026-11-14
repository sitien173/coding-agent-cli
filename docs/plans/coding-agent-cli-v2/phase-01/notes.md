<!-- refract-shared-version: 3.0.5 -->

# Phase 1 Decision Notes

<!-- Created by the worker per worker-contract.md. Append one `## Task <M>` block per task. -->

## Task 1

### Decisions made
- Added `scripts` to `[tool.pyright] include` in `pyproject.toml` and imported `scripts.scrub_fixtures.Scrubber` in `tests/test_fixture_privacy.py`.
- `Scrubber.map_id` preserves UUID format (`00000000-0000-4000-8000-<12hex>`) so UUID regexes in drivers continue to match scrubbed fixtures; non-UUID IDs map to `id-XXXX`.
- `_is_secret_key` matches `api_key`, `apikey`, `authorization`, `password`, `secret`, and `token` while excluding keys ending in `tokens` so integer usage counters (`input_tokens`, `output_tokens`, etc.) are preserved.
- `test_committed_recorded_fixtures_have_no_privacy_leaks` skips checks for username/hostname/home shorter than 3 chars or generic names (`user`, `root`, `admin`, `test`, `localhost`), and currently skips when `tests/fixtures/recorded` does not exist yet (4 passed, 1 skipped before Task 3).

### Spec deviations
- none

### Tradeoffs accepted
- Replacing `home`, `username`, and `hostname` with fixed placeholders (`/home/scrubbed`, `scrubbed_user`, `scrubbed-host`) instead of hashed tokens keeps fixtures human-readable.

### Assumptions
- `tests/fixtures/recorded/` will be created in Task 3 and covered by the gate test.

### Follow-ups for human
- none

### Test evidence
- RED -> GREEN:
  - RED: `export VIRTUAL_ENV=; uv run pytest tests/test_fixture_privacy.py` -> `3 failed, 1 passed, 1 skipped` (`AssertionError` on `alice` not removed from JSON, `alice` not removed from stderr, and `session_id` not mapped).
  - GREEN: `export VIRTUAL_ENV=; uv run pytest tests/test_fixture_privacy.py` -> `4 passed, 1 skipped in 0.05s` (`test_committed_recorded_fixtures_have_no_privacy_leaks` skipped until Task 3 creates `tests/fixtures/recorded/`).
- Root cause (bugfix only): none

## Task 2

### Decisions made
- Implemented `scripts/record_fixtures.py` using `tempfile.mkdtemp(prefix=f"cac-raw-{cli}-")` with `0o700` permissions for raw captures and `tempfile.mkdtemp(prefix=f"cac-work-{cli}-")` for isolated throwaway git repositories.
- Used `start_new_session=True` and `os.killpg(..., signal.SIGKILL)` on cleanup/timeout, and `SIGTERM` on the first `text_delta` (or `turn.started` for `codex`) for `cancel`.
- Reused the `plain` throwaway repo for `resume` so `pi --session <id>` finds the session in the matching project directory.
- Used cheapest models (`claude`: `haiku` with `--max-budget-usd 0.05` except `budget` at `0.0001`; `pi`: `openai-codex/gpt-5.6-luna`; `agy`: `gemini-3.8-flash-low`; `codex`: default model).

### Spec deviations
- none

### Tradeoffs accepted
- none

### Assumptions
- none

### Follow-ups for human
- none

### Test evidence
- RED -> GREEN:
  - RED: `export VIRTUAL_ENV=; uv run pytest tests/test_fixture_privacy.py` (before `scripts/record_fixtures.py` existed, `tests/fixtures/recorded/` did not exist and `test_committed_recorded_fixtures_have_no_privacy_leaks` skipped).
  - GREEN: `export VIRTUAL_ENV=; uv run ruff format --check scripts/record_fixtures.py && uv run ruff check scripts/record_fixtures.py && uv run pyright scripts/record_fixtures.py` -> `1 file formatted`, `All checks passed!`, `0 errors, 0 warnings, 0 informations`.
- Root cause (bugfix only): none

## Task 3

### Decisions made
- Recorded all four CLIs (`claude 2.1.296`, `codex 0.169.0`, `pi 1.5.2`, `agy 1.5.9`) and scrubbed all captures through `Scrubber`.
- Fixed two recorder issues discovered on initial run: changed `codex` `--full-auto` to `--sandbox workspace-write` and re-recorded `shell`/`edit`; reused `plain` repo `cwd` for `resume` and re-recorded `pi` `plain`/`resume`.
- Removed `agy/1.5.9/auth_failure.*` because isolating `HOME` and `XDG_CONFIG_HOME` did not cause `agy` to fail authentication (`status: "SUCCESS"`), and documented it in `FINDINGS.md`.

### Spec deviations
- none

### Tradeoffs accepted
- none

### Assumptions
- none

### Follow-ups for human
- none

### Test evidence
- RED -> GREEN:
  - RED: `tests/test_fixture_privacy.py::test_privacy_gate_catches_planted_leaks` verified the gate catches planted username, home path, hostname, email, and `sk-` secret.
  - GREEN: `export VIRTUAL_ENV=; uv run pytest tests/test_fixture_privacy.py` -> `5 passed in 0.16s` across all committed fixtures under `tests/fixtures/recorded/`.
- Root cause (bugfix only): none

## Task 4

### Decisions made
- Documented fixture layout, CLI versions, observed event/item types with line references, and answers to all `DESIGN.md` open questions in `tests/fixtures/recorded/FINDINGS.md`, marking `agy --input-format stream-json` schema and `agy` `auth_failure` as "not settled" with exact reasons.

### Spec deviations
- none

### Tradeoffs accepted
- none

### Assumptions
- none

### Follow-ups for human
- none

### Test evidence
- RED -> GREEN:
  - RED: none (documentation task).
  - GREEN: verified all referenced fixture paths and line numbers exist under `tests/fixtures/recorded/`.
- Root cause (bugfix only): none

## FIX: User Config Isolation & Privacy Hardening

### Decisions made
- Isolated user configuration in `scripts/record_fixtures.py`:
  - `claude`: `--setting-sources "" --strict-mcp-config --disable-slash-commands`
  - `pi`: `--no-extensions --no-skills --no-prompt-templates --no-themes --no-context-files --no-mcp`
  - `agy`: `--disable-slash-commands`
  - `codex`: explicit prompt instruction not to read files outside the throwaway repo.
- Hardened `scripts/scrub_fixtures.py` to strip `/home/scrubbed/.<dir>` config paths, `.nvm/` paths, `plugins/cache` paths, `<available_skills>`, `<location>`, `SKILL.md`, `hook_response` `output`/`stdout` payloads, `systemPrompt`/`sections`/`skills`/`slash_commands`/`plugins`/`mcp_servers`/`agents`/`memory_paths` fields, and any JSON string over 8 KiB (`8192` bytes).
- Extended `tests/test_fixture_privacy.py` with gate checks and planted-value tests for all new config-leak rules.
- Re-recorded all four CLIs and documented config isolation in `tests/fixtures/recorded/FINDINGS.md`. Largest fixture file size is now `54K` (`tests/fixtures/recorded/pi/1.5.2/edit.stdout.jsonl`).

### Spec deviations
- none

### Tradeoffs accepted
- none

### Assumptions
- none

### Follow-ups for human
- none

### Test evidence
- RED -> GREEN:
  - RED: `export VIRTUAL_ENV=; uv run pytest tests/test_fixture_privacy.py` -> `4 failed, 2 passed` (failing on `/home/scrubbed/.`, `<location>`, `<available_skills`, `SKILL.md`, `plugins/cache`, `.nvm/`, `hook_response.output > 64 chars`, and `sections.skills > 8192 bytes`).
  - GREEN: `export VIRTUAL_ENV=; uv run pytest tests/test_fixture_privacy.py` -> `6 passed in 1.22s`.
  - Grep check: `grep -rIl -e '/home/scrubbed/\.' -e '<location>' -e 'plugins/cache' tests/fixtures/recorded` -> returned nothing.
  - Full suite: `uv run ruff format --check` (78 files formatted), `uv run ruff check` (passed), `uv run pyright` (0 errors), `uv run pytest` (511 passed, 4 deselected).
- Root cause (bugfix only): Personal config paths, skills/plugins/MCP lists, hook outputs, and oversized system prompt sections leaked into recorded fixtures because the recorder did not pass CLI config-isolation flags and the scrubber did not redact config-bearing fields or `/home/scrubbed/.<dir>` paths.


## FIX: Gate 3 Review Findings (1-6)
- Goal: Resolve all 6 Gate 3 review findings on `scripts/record_fixtures.py`, `scripts/scrub_fixtures.py`, `tests/test_fixture_privacy.py`, `tests/fixtures/recorded/codex/0.169.0/cancel.*`, and `tests/fixtures/recorded/FINDINGS.md`.
- Decisions:
  1. Agy `auth_failure`: Verified via `agy --help` that no config-dir flag or credential-isolation env var exists; removed `auth_failure` from `_scenario_specs("agy")` and `_auth_failure_env`, and recorded it in `FINDINGS.md` as `not producible: credentials not isolatable`.
  2. Secret-key nested redaction: Checked `_is_secret_key(key)` at the top of `Scrubber.scrub_value()` before dict/list recursion so any non-empty JSON type (`dict`, `list`, `str`, `int`, etc.) under a secret-named key is replaced with `"<REDACTED_SECRET>"`.
  3. Evidence-based outcome/code: Replaced `_default_outcome_code` with `derive_outcome_code(...)` following `DESIGN.md` Outcome precedence (`cancelled` -> `auth` -> `invalid_model`/`context_overflow` -> `max_turns`/`budget` -> `timeout` -> `service_error` -> `execution_error` -> `no_output` -> `success`), inspecting error events and stderr rather than scenario names. Audited all 36 committed `meta.json` files via `test_committed_meta_json_matches_derived_outcome_code`.
  4. Codex `cancel`: Updated `_should_cancel_on_line` for `codex` to wait for `item.completed` with `item.type == "agent_message"`, used a short multi-step prompt with `--sandbox workspace-write`, and re-recorded ONLY `codex` `cancel` once (`Part 1.` captured before SIGTERM, `returncode: -15`).
  5. Temp parent check: Added `_verified_temp_parent()` to resolve `tempfile.gettempdir()` and raise `RuntimeError` if it is inside `REPO_ROOT`.
  6. Bytecode prevention: Added `sys.dont_write_bytecode = True` before `from scripts.scrub_fixtures import Scrubber` in `scripts/record_fixtures.py` and verified `scripts/__pycache__` is not created.
- RED: `export VIRTUAL_ENV=; uv run pytest tests/test_fixture_privacy.py tests/test_record_fixtures.py` -> 7 failed, 6 passed (`test_scrubber_redacts_nested_objects_and_arrays_under_secret_keys`, `test_agy_default_scenarios_exclude_auth_failure`, `test_record_fixtures_sets_dont_write_bytecode_before_scrubber_import`, `test_verified_temp_parent_rejects_path_inside_repo`, `test_should_cancel_on_line_for_codex_waits_for_agent_message`, `test_derive_outcome_code_follows_design_precedence`, `test_record_fixtures_import_does_not_create_pycache`).
- GREEN: `export VIRTUAL_ENV=; uv run pytest tests/test_fixture_privacy.py tests/test_record_fixtures.py` -> 14 passed.
- Checks: `ruff format --check` (79 files already formatted), `ruff check` (All checks passed!), `pyright` (0 errors, 0 warnings, 0 informations), `pytest` (519 passed, 4 deselected).


## FIX (cycle 2, final): Gate 3 Re-Review Findings (1-5)
- Goal: Resolve all 5 remaining findings from Gate 3 re-review without changing anything outside scope.
- Decisions:
  1. Explicit `agy` `auth_failure`: Added an upfront check in `record_cli` (before `_verified_temp_parent` and `_get_cli_version`) that raises `ValueError` if `cli == "agy"` and `"auth_failure"` is in `selected`.
  2. Actual non-empty assistant text check: Replaced `_has_assistant_output` with `_has_assistant_text` checking non-empty text per CLI (Claude `text_delta`/assistant text block/`result.result`; Codex `agent_message` text; Pi `text_delta`/`message_end` assistant text content; Agy `step_update` `text_delta`/`text`/`result.response`; and Agy plain-text stdout fallback). Re-ran the meta audit over all 36 committed `meta.json` files: 0 label changes needed.
  3. HTTP 5xx regex: Added `_HTTP_5XX_RE` matching any 500–599 status near `HTTP`/`status` or standard 5xx reason phrases, plus `rate limit`/`rate_limit`/`overloaded`.
  4. Agy cancel condition: Updated `_should_cancel_on_line("agy", ...)` to require a non-empty `text_delta` string on `step_update` (`agent_response`). Confirmed `tests/fixtures/recorded/agy/1.5.9/cancel.stdout.jsonl` already has `"text_delta": "The"` before cancel, so kept it unchanged.
  5. Isolated pycache test: Rewrote `test_record_fixtures_script_does_not_create_pycache` to copy the two scripts into `tmp_path / "scripts"` and run `python -I scripts/record_fixtures.py --help` with `cwd=tmp_path`, never touching `scripts/__pycache__` in the checkout.
- RED: `export VIRTUAL_ENV=; uv run pytest tests/test_record_fixtures.py` -> 10 failed, 11 passed.
- GREEN: `export VIRTUAL_ENV=; uv run pytest tests/test_record_fixtures.py` -> 21 passed.
- Checks: `ruff format --check` (79 files already formatted), `ruff check` (All checks passed!), `pyright` (0 errors, 0 warnings, 0 informations), `pytest` (pending final run).
