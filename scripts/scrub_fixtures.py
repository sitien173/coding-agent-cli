"""Scrub personal data, secrets, user config, and unstable IDs from recorded CLI fixtures."""

from __future__ import annotations

import argparse
import getpass
import json
import re
import socket
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

MAX_JSON_STRING_BYTES = 8192

UUID_RE = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
AVAILABLE_SKILLS_RE = re.compile(r"<available_skills\b[^>]*>.*?</available_skills>", re.DOTALL)
LOCATION_TAG_RE = re.compile(r"<location>.*?</location>", re.DOTALL)
HOME_DOT_PATH_RE = re.compile(r"/home/scrubbed/\.[^\s\"',;:\)\]>\\]*")
NVM_PATH_RE = re.compile(r"[^\s\"',;:\)\]>\\]*\.nvm/[^\s\"',;:\)\]>\\]*")
PLUGINS_CACHE_RE = re.compile(r"plugins/cache[^\s\"',;:\)\]>\\]*")

SECRET_VALUE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"sk-ant-[A-Za-z0-9_-]{8,}"), "<REDACTED_SECRET>"),
    (re.compile(r"sk-[A-Za-z0-9_-]{8,}"), "<REDACTED_SECRET>"),
    (re.compile(r"ghp_[A-Za-z0-9]{8,}"), "<REDACTED_SECRET>"),
    (re.compile(r"gho_[A-Za-z0-9]{8,}"), "<REDACTED_SECRET>"),
    (re.compile(r"AIza[A-Za-z0-9_-]{8,}"), "<REDACTED_SECRET>"),
    (re.compile(r"xox[baprs]-[A-Za-z0-9-]{8,}"), "<REDACTED_SECRET>"),
    (re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]{8,}"), "Bearer <REDACTED_SECRET>"),
)

SECRET_KEY_SUBSTRINGS = ("api_key", "apikey", "authorization", "password", "secret")
CONFIG_COLLECTION_KEYS = frozenset(
    {
        "skills",
        "slash_commands",
        "terminal_slash_commands",
        "plugins",
        "mcp_servers",
        "agents",
        "sections",
        "memory_paths",
    }
)
SYSTEM_PROMPT_KEYS = frozenset({"systemPrompt", "system_prompt"})
ID_KEY_EXACT = frozenset(
    {
        "id",
        "uuid",
        "parentUuid",
        "session_id",
        "sessionId",
        "thread_id",
        "threadId",
        "conversation_id",
        "conversationId",
        "message_id",
        "messageId",
        "tool_use_id",
        "toolUseId",
        "tool_call_id",
        "toolCallId",
        "call_id",
        "callId",
        "request_id",
        "requestId",
        "response_id",
        "responseId",
        "item_id",
        "itemId",
        "parent_id",
        "parentId",
    }
)


def _is_secret_key(key: str | None) -> bool:
    if not key:
        return False
    lower = key.lower()
    if any(sub in lower for sub in SECRET_KEY_SUBSTRINGS):
        return True
    # Avoid clobbering integer token usage counts (input_tokens, output_tokens, etc.).
    return "token" in lower and not lower.endswith("tokens")


def _is_id_key(key: str | None) -> bool:
    if not key:
        return False
    return key in ID_KEY_EXACT


def _dedupe_longest_first(values: Sequence[str]) -> list[str]:
    unique = {v for v in values if v}
    return sorted(unique, key=len, reverse=True)


class Scrubber:
    """Deterministic, stateful scrubber for one scenario set.

    Maintains a consistent ID mapping across multiple events and files so
    session, thread, message, tool, and request correlations survive.
    """

    def __init__(
        self,
        *,
        home: str | None = None,
        username: str | None = None,
        hostname: str | None = None,
        cwds: Sequence[str] = (),
        extra_literals: Sequence[str] = (),
    ) -> None:
        raw_home = str(Path.home()) if home is None else home
        resolved_home = str(Path(raw_home).resolve()) if raw_home else ""
        self.homes = _dedupe_longest_first([raw_home, resolved_home])
        self.username = getpass.getuser() if username is None else username
        self.hostname = socket.gethostname() if hostname is None else hostname

        cwd_variants: list[str] = []
        for cwd in cwds:
            if cwd:
                cwd_variants.append(cwd)
                cwd_variants.append(str(Path(cwd).resolve()))
        self.cwds = _dedupe_longest_first(cwd_variants)
        self.extra_literals = _dedupe_longest_first(list(extra_literals))

        self.id_map: dict[str, str] = {}
        self._uuid_counter = 0
        self._id_counter = 0

    def map_id(self, raw_id: str) -> str:
        """Map ``raw_id`` to a deterministic placeholder, preserving UUID shape."""
        if not raw_id:
            return raw_id
        existing = self.id_map.get(raw_id)
        if existing is not None:
            return existing
        if UUID_RE.fullmatch(raw_id):
            self._uuid_counter += 1
            mapped = f"00000000-0000-4000-8000-{self._uuid_counter:012x}"
        else:
            self._id_counter += 1
            mapped = f"id-{self._id_counter:04d}"
        self.id_map[raw_id] = mapped
        return mapped

    def scrub_text(self, text: str) -> str:
        """Replace paths, secrets, emails, config markers, IDs, hostname, and username in plain text."""
        out = text
        for cwd in self.cwds:
            if len(cwd) >= 3:
                out = out.replace(cwd, "/work/repo")
        for extra in self.extra_literals:
            if len(extra) >= 3:
                out = out.replace(extra, "<REDACTED_PATH>")
        for home in self.homes:
            if len(home) >= 3:
                out = out.replace(home, "/home/scrubbed")
        for pattern, replacement in SECRET_VALUE_PATTERNS:
            out = pattern.sub(replacement, out)
        out = EMAIL_RE.sub("scrubbed@example.invalid", out)
        out = AVAILABLE_SKILLS_RE.sub("<scrubbed:skills>", out)
        out = LOCATION_TAG_RE.sub("<scrubbed:location>", out)
        out = out.replace("<available_skills", "<scrubbed:available_skills")
        out = out.replace("<location>", "<scrubbed:location>")
        out = out.replace("SKILL.md", "<scrubbed:skill-doc>")
        out = HOME_DOT_PATH_RE.sub("<scrubbed:config-path>", out)
        out = NVM_PATH_RE.sub("<scrubbed:nvm-path>", out)
        out = PLUGINS_CACHE_RE.sub("<scrubbed:plugin-path>", out)
        out = UUID_RE.sub(lambda m: self.map_id(m.group(0)), out)
        for raw_id in sorted((k for k in self.id_map if len(k) >= 6), key=len, reverse=True):
            out = out.replace(raw_id, self.id_map[raw_id])
        if len(self.hostname) >= 3:
            out = out.replace(self.hostname, "scrubbed-host")
        if len(self.username) >= 3:
            out = out.replace(self.username, "scrubbed_user")
        return out

    def scrub_value(self, value: Any, *, key: str | None = None) -> Any:
        """Recursively scrub a parsed JSON value."""
        if _is_secret_key(key):
            if value is None:
                return None
            if isinstance(value, str) and not value:
                return ""
            return "<REDACTED_SECRET>"
        if key in CONFIG_COLLECTION_KEYS:
            if isinstance(value, list):
                return []
            if isinstance(value, dict):
                return {}
            if isinstance(value, str):
                return "<scrubbed:config>"
        if key in SYSTEM_PROMPT_KEYS and isinstance(value, str):
            return "<scrubbed:system-prompt>" if value else ""

        if isinstance(value, dict):
            is_hook_response = value.get("subtype") == "hook_response"
            result: dict[str, Any] = {}
            for k, v in value.items():
                k_str = str(k)
                if is_hook_response and k_str in ("output", "stdout") and isinstance(v, str):
                    result[k] = "<scrubbed:hook-output>" if v else ""
                else:
                    result[k] = self.scrub_value(v, key=k_str)
            return result
        if isinstance(value, list):
            return [self.scrub_value(item, key=key) for item in value]
        if isinstance(value, str):
            if _is_secret_key(key):
                return "<REDACTED_SECRET>" if value else ""
            if _is_id_key(key) and value:
                return self.map_id(value)
            scrubbed = self.scrub_text(value)
            if len(scrubbed.encode("utf-8")) > MAX_JSON_STRING_BYTES:
                return "<scrubbed:oversized-string>"
            return scrubbed
        return value

    def scrub_jsonl_line(self, line: str) -> str:
        """Scrub a single stdout line, parsing JSON when possible."""
        trailing = "\n" if line.endswith("\n") else ""
        stripped = line.rstrip("\r\n")
        if not stripped.strip():
            return line
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            return self.scrub_text(stripped) + trailing
        scrubbed = self.scrub_value(parsed)
        return json.dumps(scrubbed, ensure_ascii=False) + trailing


def scrub_file(src: Path, dst: Path, scrubber: Scrubber, *, is_jsonl: bool) -> None:
    """Scrub ``src`` into ``dst`` line-by-line (for JSONL) or as plain text."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    text = src.read_text(encoding="utf-8", errors="replace")
    if is_jsonl:
        lines = [scrubber.scrub_jsonl_line(line) for line in text.splitlines(keepends=True)]
        dst.write_text("".join(lines), encoding="utf-8")
    else:
        dst.write_text(scrubber.scrub_text(text), encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point to scrub a raw text or JSONL file."""
    parser = argparse.ArgumentParser(description="Scrub personal data and secrets from a raw fixture file.")
    parser.add_argument("src", type=Path, help="Input raw file path")
    parser.add_argument("dst", type=Path, help="Output scrubbed file path")
    parser.add_argument("--jsonl", action="store_true", help="Parse and scrub each line as JSONL")
    parser.add_argument("--cwd", action="append", default=[], help="Recording cwd to replace with /work/repo")
    args = parser.parse_args(argv)

    scrubber = Scrubber(cwds=args.cwd)
    scrub_file(args.src, args.dst, scrubber, is_jsonl=args.jsonl)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
