# Recorded CLI Fixture Findings

## Fixture Layout

Each recorded scenario is stored under:

```
tests/fixtures/recorded/<cli>/<version>/<scenario>.stdout.jsonl
tests/fixtures/recorded/<cli>/<version>/<scenario>.stderr.txt
tests/fixtures/recorded/<cli>/<version>/<scenario>.meta.json
```

Each `<scenario>.meta.json` holds `cli`, `version`, `version_output`, `scenario`, `argv` (prompt replaced with `<PROMPT>`), `returncode`, `timed_out`, `cancelled`, `outcome`, and `code` (per `DESIGN.md` Outcome precedence and `ErrorCode`).

## CLI Versions Recorded

| CLI | Version | `--version` output |
|---|---|---|
| `claude` | `2.1.296` | `2.1.296 (Claude Code)` |
| `codex` | `0.169.0` | `codex-cli 0.169.0` |
| `pi` | `1.5.2` | `1.5.2` |
| `agy` | `1.5.9` | `1.5.9` |

## Observed Event Types and Item/Content Types

### `claude` (`2.1.296`)

Top-level `type` values observed:
- `system` (`subtype`: `init`, `status`, `hook_started`, `hook_response`) — e.g. `claude/2.1.296/plain.stdout.jsonl:1`
- `stream_event` wrapping `event.type`:
  - `message_start` — `claude/2.1.296/plain.stdout.jsonl:2`
  - `content_block_start` (`content_block.type`: `text`, `thinking`, `tool_use`) — `claude/2.1.296/plain.stdout.jsonl:3`, `claude/2.1.296/reasoning.stdout.jsonl:8`, `claude/2.1.296/edit.stdout.jsonl:13`
  - `content_block_delta` (`delta.type`: `text_delta`, `thinking_delta`, `input_json_delta`) — `claude/2.1.296/plain.stdout.jsonl:4`, `claude/2.1.296/reasoning.stdout.jsonl:9`
  - `content_block_stop` — `claude/2.1.296/plain.stdout.jsonl:6`
  - `message_delta` — `claude/2.1.296/plain.stdout.jsonl:7`
  - `message_stop` — `claude/2.1.296/plain.stdout.jsonl:8`
- `assistant` (`message.content[].type`: `text`, `thinking`, `tool_use` with populated `input`) — `claude/2.1.296/plain.stdout.jsonl:5`, `claude/2.1.296/reasoning.stdout.jsonl:11`, `claude/2.1.296/edit.stdout.jsonl:24`
- `user` (`message.content[].type`: `tool_result` with `tool_use_id`, `content`, `is_error`) — `claude/2.1.296/edit.stdout.jsonl`
- `rate_limit_event` — `claude/2.1.296/plain.stdout.jsonl`
- `result` (`subtype`: `success`, `error_max_turns`, `error_max_budget_usd`) — `claude/2.1.296/plain.stdout.jsonl:15`, `claude/2.1.296/max_turns.stdout.jsonl:32`, `claude/2.1.296/budget.stdout.jsonl:31`

### `codex` (`0.169.0`)

Top-level `type` values observed:
- `thread.started` (`thread_id`) — `codex/0.169.0/plain.stdout.jsonl:1`
- `turn.started` — `codex/0.169.0/plain.stdout.jsonl:4`
- `item.started` (`item.type`: `command_execution`) — `codex/0.169.0/shell.stdout.jsonl:6`
- `item.completed` (`item.type`: `agent_message`, `command_execution`, `error`) — `codex/0.169.0/plain.stdout.jsonl:5`, `codex/0.169.0/shell.stdout.jsonl:7`, `codex/0.169.0/plain.stdout.jsonl:2`
- `turn.completed` (`usage`) — `codex/0.169.0/plain.stdout.jsonl:6`
- `error` (`message`) — `codex/0.169.0/invalid_model.stdout.jsonl:6`
- `turn.failed` (`error.message`) — `codex/0.169.0/invalid_model.stdout.jsonl:7`

### `pi` (`1.5.2`)

Top-level `type` values observed:
- `session` (`id`, `version`, `cwd`) — `pi/1.5.2/plain.stdout.jsonl:1`
- `agent_start` — `pi/1.5.2/plain.stdout.jsonl:2`
- `turn_start` — `pi/1.5.2/plain.stdout.jsonl:3`
- `message_start` — `pi/1.5.2/plain.stdout.jsonl:4`
- `message_update` (`assistantMessageEvent.type`: `text_start`, `text_delta`, `text_end`, `thinking_start`, `thinking_delta`, `thinking_end`, `toolcall_start`, `toolcall_delta`, `toolcall_end`) — `pi/1.5.2/plain.stdout.jsonl`, `pi/1.5.2/edit.stdout.jsonl:10`
- `message_end` — `pi/1.5.2/plain.stdout.jsonl`
- `tool_execution_start`, `tool_execution_update`, `tool_execution_end` — `pi/1.5.2/shell.stdout.jsonl`
- `turn_end` — `pi/1.5.2/plain.stdout.jsonl`
- `agent_end` — `pi/1.5.2/plain.stdout.jsonl`
- `agent_settled` — `pi/1.5.2/plain.stdout.jsonl`

### `agy` (`1.5.9`)

Top-level `event` values observed:
- `init` (`conversation_id`, `init.model`, `init.cwd`, `init.tools`) — `agy/1.5.9/plain.stdout.jsonl:1`
- `step_update` (`step_update.step_type`: `user_input`, `system_message`, `agent_response`, `tool`; `state`: `ACTIVE`, `DONE`) — `agy/1.5.9/plain.stdout.jsonl:2-4`, `agy/1.5.9/resume.stdout.jsonl`, `agy/1.5.9/edit.stdout.jsonl:4-9`
- `result` (`result.status`: `SUCCESS` or `ERROR`, `result.response`, `result.error`, `result.usage`) — `agy/1.5.9/plain.stdout.jsonl:5`, `agy/1.5.9/stdin_stream_json.stdout.jsonl:2`

## Open Questions from `DESIGN.md`

1. **Agy `--input-format stream-json` message schema**: **not settled**.
   - Sending Anthropic-style `{"type":"user","message":{"role":"user","content":[{"type":"text","text":"..."}]}}` on stdin with `--input-format stream-json --output-format stream-json` exited with code `1`, emitting `error: stream input message is missing the "event" field` on stderr (`agy/1.5.9/stdin_stream_json.stderr.txt:1`) and `status: "ERROR"` in `result` (`agy/1.5.9/stdin_stream_json.stdout.jsonl:2`). Because the exact `event` input schema is not documented in `agy --help`, v1 argv prompt delivery stays.

2. **Codex option placement around `resume`**: **settled**.
   - `codex exec resume --json <session_id> -` succeeded with exit code `0` (`codex/0.169.0/resume.meta.json`, `codex/0.169.0/resume.stdout.jsonl`). `codex exec resume --help` also accepts `-c`, `-m`/`--model`, and `--dangerously-bypass-approvals-and-sandbox` as options on the `resume` subcommand.

3. **Claude budget-exceeded subtype name and `--max-turns` acceptance**: **settled**.
   - Budget exceeded emits `"type": "result"`, `"is_error": true`, `"subtype": "error_max_budget_usd"`, `"terminal_reason": "budget_exhausted"` (`claude/2.1.296/budget.stdout.jsonl:31`).
   - `--max-turns 1` is accepted by `claude` and emits `"type": "result"`, `"is_error": true`, `"subtype": "error_max_turns"`, `"terminal_reason": "max_turns"` (`claude/2.1.296/max_turns.stdout.jsonl:32`).

4. **Pi non-interactive behavior without `-p`**: **settled**.
   - `pi --mode json` without `-p` (`pi/1.5.2/no_p.stdout.jsonl`) reads the prompt from stdin, runs non-interactively, emits the same JSON event stream (`session` .. `agent_settled`), and exits with code `0` once stdin closes.

5. **Pi and Claude reasoning event shapes**: **settled**.
   - **Claude**: `stream_event` with `event.type == "content_block_start"` (`content_block.type == "thinking"`) and `event.type == "content_block_delta"` (`delta.type == "thinking_delta"`, `delta.thinking`), plus an `assistant` message containing a `{"type": "thinking", ...}` content block (`claude/2.1.296/reasoning.stdout.jsonl:8-11`).
   - **Pi**: `message_update` events where `assistantMessageEvent.type` is `"thinking_start"`, `"thinking_delta"` (with `delta` string), and `"thinking_end"` (`pi/1.5.2/edit.stdout.jsonl:10-11`).

6. **Claude failure without `--verbose`**: **settled**.
   - `claude -p --output-format stream-json` without `--verbose` exits with code `1`, empty stdout, and stderr: `Error: When using --print, --output-format=stream-json requires --verbose` (`claude/2.1.296/no_verbose.stderr.txt:1`).

7. **Auth-failure signal and config-dir variable per CLI**:
   - **Claude** (`CLAUDE_CONFIG_DIR`): Exits `1`; emits structured `assistant` event with `"error": "authentication_failed"`, `"is_api_error_message": true`, and `result` event with `"is_error": true`, `"subtype": "success"`, `"result": "Not logged in · Please run /login"` (`claude/2.1.296/auth_failure.stdout.jsonl:3-4`).
   - **Codex** (`CODEX_HOME`): Exits `1`; emits `error` and `turn.failed` JSON events on stdout and WebSocket connection errors on stderr (`codex/0.169.0/auth_failure.stdout.jsonl`, `codex/0.169.0/auth_failure.stderr.txt`).
   - **Pi** (`PI_CODING_AGENT_DIR`): Exits `1`; emits `session` on stdout and `No API key found for openai-codex.` on stderr (`pi/1.5.2/auth_failure.stderr.txt:1`).
   - **Agy** (`auth_failure` **not producible: credentials not isolatable**): Neither `agy --help` nor its documentation exposes a config-dir flag or credential-isolation environment variable, and overriding `HOME` and `XDG_CONFIG_HOME` to an empty directory still authenticated (`agy` succeeded with `status: "SUCCESS"`) against credentials outside those variables. Removed `auth_failure` from `agy` default scenarios; no `agy` `auth_failure` fixture is committed.

## Config Isolation During Recording

To prevent personal agent configuration, skills, plugins, hooks, MCP servers, and memory files from appearing in fixtures:

- **`claude`**: Recorded with `--setting-sources "" --strict-mcp-config --disable-slash-commands`, which disables user/project/local settings, hooks, plugins, skills, and MCP servers while preserving OAuth/keychain authentication.
- **`pi`**: Recorded with `--no-extensions --no-skills --no-prompt-templates --no-themes --no-context-files --no-mcp`.
- **`agy`**: Recorded with `--disable-slash-commands`.
- **`codex`**: Recorded in a fresh temporary git repository with an explicit instruction not to read files outside the repository.
- **Scrubber fallback**: `scripts/scrub_fixtures.py` also strips `/home/scrubbed/.<dir>` paths, `.nvm/` paths, `plugins/cache` paths, `<available_skills>`/`<location>`/`SKILL.md` markers, `hook_response` output/stdout payloads, `systemPrompt`/`sections`/`skills`/`slash_commands`/`plugins`/`mcp_servers`/`agents`/`memory_paths` fields, and any JSON string over 8 KiB.
