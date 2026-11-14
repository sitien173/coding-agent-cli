# Phase 1: Recorded and scrubbed CLI fixtures

## Goal & Outcome
- Plan Goal: Rebuild `coding-agent-cli` as 0.2.0 on a public `CliDriver` API, with parsers proven against fixtures recorded from the real CLIs. Full design: `/home/ngosi/projects/coding-agent-cli/docs/plans/coding-agent-cli-v2/DESIGN.md`. Full plan: `/home/ngosi/projects/coding-agent-cli/docs/plans/coding-agent-cli-v2/PLAN.md`, section "Phase 1".
- Phase Outcome: Version-labelled, scrubbed fixtures for `claude`, `codex`, `pi`, and `agy` exist under `tests/fixtures/recorded/`; a privacy gate test guards them; `tests/fixtures/recorded/FINDINGS.md` answers every open question in DESIGN.md "Risks and Open Questions" or marks it "not settled" with the reason.

## Context & Architecture
- Code root: `/home/ngosi/projects/coding-agent-cli/.worktrees/coding-agent-cli-v2-phase-01` | Records root: `/home/ngosi/projects/coding-agent-cli` | Branch: `refract/coding-agent-cli-v2/phase-01` | Base: `refs/plans/coding-agent-cli-v2/phase-01/base`
- Prior State: v1 library at plan base `24b8cca`. Baseline: `ruff format --check` pass, `ruff check` pass, `pyright` 0 errors, `pytest` 505 passed 4 deselected. Phases 2 and 3 run in parallel in other worktrees; they do not touch `scripts/`, `tests/fixtures/recorded/`, `tests/test_fixture_privacy.py`, or `pyproject.toml`.
- Code Context: the v1 drivers in `src/coding_agent_cli/_drivers/{claude,codex,pi,agy}.py` show each CLI's current argv, owned flags, and env handling; read them to build recorder argv. The v2 transport argv per CLI is in DESIGN.md "Drivers": Claude `-p --output-format stream-json --verbose --include-partial-messages` with the prompt on stdin; Codex `exec --json` (no `-o`), prompt on stdin with `-`; Pi per v1 argv, also try without `-p`; Agy per v1 argv, plus an attempt with `--input-format stream-json` on stdin. Installed versions reported earlier: claude 2.1.296, codex 0.169.0, pi 1.5.2, agy 1.5.9; re-read them with `--version`.
- v1 defect 1 (verified earlier): `claude -p --output-format stream-json` without `--verbose` exits with `Error: When using --print, --output-format=stream-json requires --verbose`. Record this as a scenario too (`no_verbose`).

## Invariants & Boundaries
- Preserve: every file under `src/` stays unchanged. Standard library only; no new dependency in `pyproject.toml`.
- Recording runs only in a fresh git repository created with `tempfile.mkdtemp()` holding only synthetic files (for example a `hello.txt` and a tiny `calc.py`). Never use the project checkout or worktree as the CLI `cwd`.
- Raw captures go to a directory created with mode `700` outside the repository (for example under `tempfile.mkdtemp(prefix="cac-raw-")`). Never commit or copy raw captures into the worktree. Only the scrubber writes into `tests/fixtures/recorded/`.
- Permission or approval flags (for example Claude `--permission-mode acceptEdits` or `--allowedTools`, Codex `--full-auto` or `--sandbox workspace-write`, Agy equivalents) may appear only in the recorder's argv for the throwaway repository, never in `src/`.
- Auth-failure scenarios point the CLI config directory at an empty temporary directory (for example `CLAUDE_CONFIG_DIR`, `CODEX_HOME`, and the equivalents you find in `--help` or v1 driver code for pi and agy) and clear API-key variables for that subprocess only. Never read, copy, print, or log real credential files or environment values.
- Spend control: use the cheapest model each CLI offers (for example Claude `--model haiku`), one-line prompts, and Claude `--max-budget-usd 0.05` on every Claude scenario except the tiny-budget one. Run each scenario once; re-run only a failed scenario, at most once.
- The recorder never logs prompts' surrounding environment; argv is stored with the prompt replaced by `<PROMPT>`.
- Out of Scope: any parser or driver code, `src/` changes, Phase 2 to 9 work, `pytest -m live`, editing `PLAN.md`, `DESIGN.md`, `.handover.md`, or `prompt.md`. Do not push, tag, or commit; the Coordinator commits.

## Files
Allowed production and test paths (relative to code root):
- `scripts/scrub_fixtures.py` (create: scrubber library plus CLI entry)
- `scripts/record_fixtures.py` (create: recorder)
- `tests/fixtures/recorded/<cli>/<version>/<scenario>.stdout.jsonl`, `<scenario>.stderr.txt`, `<scenario>.meta.json` or one `meta.json` per version directory keyed by scenario (create; choose one layout and document it in FINDINGS.md)
- `tests/fixtures/recorded/FINDINGS.md` (create)
- `tests/test_fixture_privacy.py` (create)
- `pyproject.toml` (modify: add `"scripts"` to `[tool.pyright] include` only)

Worker-owned phase records:
- `/home/ngosi/projects/coding-agent-cli/docs/plans/coding-agent-cli-v2/phase-01/notes.md` (append `## Task <M>` blocks)
- `/home/ngosi/projects/coding-agent-cli/docs/plans/coding-agent-cli-v2/phase-01/journal.md` (append `# EXTERNAL RESPONSE` under `## Implementation Response`)

Read-only / Forbidden:
- Do not edit `PLAN.md`, `DESIGN.md`, `.handover.md`, `prompt.md`, other phases, dependencies, or secrets. Do not read credential files such as `~/.claude/.credentials.json`, `~/.codex/auth.json`, or similar.

## Tasks
- task-1: Scrubber, test first. In `tests/test_fixture_privacy.py` write failing tests, then implement `scripts/scrub_fixtures.py`:
  - recursive JSON walker that replaces the home directory path, username, hostname, the recording `cwd` (use a stable placeholder such as `/work/repo`), emails, values under secret-named keys (`token`, `api_key`, `apiKey`, `authorization`, `password`, `secret`, case-insensitive substrings), and known secret prefixes (`sk-`, `sk-ant-`, `ghp_`, `gho_`, `AIza`, `Bearer `, `xox`);
  - IDs (session, thread, message, tool-use, item, request, UUIDs) mapped through one consistent table per scenario set so start/end correlations and resume session links survive;
  - the same string replacements for non-JSON stderr text.
  - Gate test: for every committed fixture file, fail if it contains the local username, `$HOME`, the hostname, any email address, or a secret pattern. Compute username, home, and hostname at test time from `getpass`, `Path.home()`, and `socket.gethostname()`; skip a check only when the value is too short or generic to search for (for example length under 3), and say so in the test. Also include planted-value tests showing the scrubber removes a username, home path, hostname, email, and `sk-` secret.
- task-2: Recorder `scripts/record_fixtures.py`: per CLI and scenario, create the throwaway repo, run the CLI with argv lists and `shell=False`, capture stdout and stderr separately, exit code, `--version`, and argv with the prompt replaced by `<PROMPT>`, into the mode-700 raw directory. Then call the scrubber to write `tests/fixtures/recorded/<cli>/<version>/`. Scenarios: `plain` (plain answer), `shell` (run one shell command such as `ls`), `edit` (edit `hello.txt`), `reasoning` (a prompt that triggers reasoning, with the CLI's reasoning flag at its lowest non-off level), `invalid_model`, `auth_failure`, `cancel` (SIGTERM the process group after the first text delta), `resume` (resume the `plain` session). Claude adds `max_turns` (`--max-turns 1` with a tool-requiring prompt), `budget` (a tiny `--max-budget-usd` such as `0.0001`), and `no_verbose`. Agy adds `stdin_stream_json` (an `--input-format stream-json` stdin attempt). Pi adds `no_p` if `-p` turns out to matter. Use per-scenario timeouts (for example 180 s) and always kill the process group on exit.
- task-3: Run the recorder for all four CLIs, scrub, and write expected `outcome`, `code`, and `returncode` per scenario using DESIGN.md "Outcome precedence" and "ErrorCode" (for example `plain` -> `success`/null; `auth_failure` -> `agent_fatal`/`auth`; `invalid_model` -> `request_fatal`/`invalid_model`; `max_turns` -> `limit_reached`/`max_turns`; `budget` -> `limit_reached`/`budget`; `cancel` -> `cancelled`/`cancelled`). A scenario a CLI cannot produce is listed in `FINDINGS.md` with the reason instead of a fixture. Inspect the scrubbed files yourself for leaks before finishing.
- task-4: Write `tests/fixtures/recorded/FINDINGS.md`: fixture layout; CLI versions; observed top-level event types and item/content types per CLI with one example line reference each; answers for Agy stdin `stream-json` message schema, Codex option placement around `resume`, Claude budget subtype name and `--max-turns` acceptance, Pi behavior without `-p`, Pi and Claude reasoning event shapes, Claude failure without `--verbose`, the auth-failure signal each CLI emits (structured or stderr text), and the config-dir variable used per CLI. Mark anything unresolved "not settled" with the reason.

## Done When
- `tests/fixtures/recorded/` holds at least the `plain` scenario for each of the four CLIs, each with expected-outcome metadata.
- `tests/test_fixture_privacy.py` fails on a planted username, home path, hostname, email, or `sk-` secret, and passes on the committed fixtures.
- `FINDINGS.md` answers each open question in DESIGN.md or marks it "not settled" with the reason.
- No raw capture file exists anywhere under the code root (`git status --porcelain` lists only the allowed paths).
- Run from code root, with `export VIRTUAL_ENV=;` first:
  - `uv run ruff format --check` (Baseline: pass)
  - `uv run ruff check` (Baseline: pass)
  - `uv run pyright` (Baseline: 0 errors)
  - `uv run pytest` (Baseline: 505 passed, 4 deselected; new tests add to it)

## Rules
Read and follow these bundled contracts:
- Contract: `/home/ngosi/.claude/plugins/cache/refract/refract/3.0.5/shared/worker-contract.md`
- Response: `/home/ngosi/.claude/plugins/cache/refract/refract/3.0.5/shared/erp.md`
- Notes: `/home/ngosi/.claude/plugins/cache/refract/refract/3.0.5/shared/notes-template.md`
- Journal: `/home/ngosi/.claude/plugins/cache/refract/refract/3.0.5/shared/journal-template.md`

Role boundary: You are the phase implementer, not Coordinator. Edit code only in the code root; write phase records only under the records root. Leave Coordinator skills and Refract job submission to the Coordinator. If the real CLIs cannot reach the network or authenticate from your environment, finish task-1, task-2, and the FINDINGS skeleton, then return `BLOCKED` with the exact error per CLI.

## Response Format
Append the complete `# EXTERNAL RESPONSE` block to `/home/ngosi/projects/coding-agent-cli/docs/plans/coding-agent-cli-v2/phase-01/journal.md`, then return that block and the matching status line from `erp.md`.
