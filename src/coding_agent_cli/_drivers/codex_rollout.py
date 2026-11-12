"""Exact-session Codex rollout collector: bounded reads of one verified rollout file for telemetry.

The collector reads ``CODEX_HOME`` from the request's effective environment. It reports a usage change through
``on_change`` and never emits events itself.
"""

from __future__ import annotations

import json
import logging
import os
import re
import stat as stat_module
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from ._common import as_int
from ._usage import TokenCounters

log = logging.getLogger("coding_agent_cli.drivers.codex")


class CodexRolloutCollector:
    """Bounded exact-session Codex rollout collector."""

    MAX_DISCOVERY_ENTRIES = 50
    MAX_DISCOVERY_RETRIES = 3
    MAX_DISCOVERY_DEPTH = 3

    def __init__(
        self,
        *,
        cwd: Path,
        env: Mapping[str, str],
        session_id: str = "",
        counters: TokenCounters,
        on_change: Callable[[], None] | None = None,
    ) -> None:
        self.cwd = cwd
        self.env = env
        self.session_id = session_id.strip()
        self.counters = counters
        self.on_change = on_change
        self.session_file: Path | None = None
        self.file_verified = False
        self.file_dev: int | None = None
        self.file_ino: int | None = None
        self.header_verified = False
        self.has_verified_context = False
        self.pending_context: tuple[int, int | None] | None = None
        self.budget_exhausted = False
        self.offset = 0
        self.partial_buffer = ""
        self.model_name: str | None = None
        self.is_compacted = False
        self.max_bytes_per_poll = 65536
        self.max_total_bytes = 1048576
        self.total_bytes_read = 0
        self.missing_discovery_retries = 0

    def set_session_id(self, session_id: str) -> None:
        sid = session_id.strip()
        if sid and sid != self.session_id:
            if self.session_id:
                self._emit_invalidation()
            self.session_id = sid
            self._reset_file_state()
            self.total_bytes_read = 0
            self.missing_discovery_retries = 0
            self.is_compacted = False

    def _reset_file_state(self) -> None:
        self.session_file = None
        self.file_verified = False
        self.file_dev = None
        self.file_ino = None
        self.header_verified = False
        self.has_verified_context = False
        self.pending_context = None
        self.budget_exhausted = False
        self.offset = 0
        self.partial_buffer = ""

    def _changed(self) -> None:
        if self.on_change is not None:
            self.on_change()

    def _emit_invalidation(self, is_compaction: bool = False) -> None:
        self.pending_context = None
        self.has_verified_context = False
        self.counters.clear_context()
        if is_compaction:
            self.is_compacted = True
        self._changed()

    def _find_exact_file(self) -> Path | None:
        if not self.session_id or not _SESSION_ID_RE.fullmatch(self.session_id):
            return None
        if self.missing_discovery_retries >= self.MAX_DISCOVERY_RETRIES:
            return None

        sessions_dir = Path(self.env.get("CODEX_HOME") or Path.home() / ".codex") / "sessions"
        if not sessions_dir.is_dir() or sessions_dir.is_symlink():
            self.missing_discovery_retries += 1
            return None

        try:
            resolved_sessions_dir = sessions_dir.resolve()
        except OSError:
            self.missing_discovery_retries += 1
            return None

        found_file = self._scan_sessions(sessions_dir, resolved_sessions_dir)
        if found_file is None:
            self.missing_discovery_retries += 1
        return found_file

    def _scan_sessions(self, sessions_dir: Path, resolved_sessions_dir: Path) -> Path | None:
        entries_inspected = 0
        dirs_to_visit = [(sessions_dir, 0)]

        while dirs_to_visit and entries_inspected < self.MAX_DISCOVERY_ENTRIES:
            current_dir, depth = dirs_to_visit.pop(0)
            inspected, files, subdirs = self._read_dir(current_dir, self.MAX_DISCOVERY_ENTRIES - entries_inspected)
            entries_inspected += inspected
            for file_path in files:
                if self._verify_rollout_file(file_path, resolved_sessions_dir):
                    return file_path
            if depth < self.MAX_DISCOVERY_DEPTH:
                dirs_to_visit.extend((subdir, depth + 1) for subdir in subdirs)
        return None

    def _read_dir(self, directory: Path, budget: int) -> tuple[int, list[Path], list[Path]]:
        """Inspect up to ``budget`` entries. Return the count and the candidate rollout files and subdirectories."""
        inspected = 0
        files: list[Path] = []
        subdirs: list[Path] = []
        try:
            with os.scandir(directory) as it:
                for entry in it:
                    inspected += 1
                    if inspected >= budget:
                        break
                    kind = _entry_kind(entry)
                    if kind == "dir":
                        subdirs.append(Path(entry.path))
                    elif kind == "file" and entry.name.endswith(".jsonl") and self.session_id in entry.name:
                        files.append(Path(entry.path))
        except OSError:
            pass
        return inspected, files, subdirs

    def _verify_rollout_file(self, file_path: Path, resolved_sessions_dir: Path) -> bool:
        """Accept ``file_path`` only when its header names this session and cwd. Record its identity."""
        try:
            if file_path.is_symlink() or not file_path.resolve().is_relative_to(resolved_sessions_dir):
                return False
            stat = file_path.stat()
            if stat.st_size < 10:
                return False
            first = _read_first_line(file_path, stat)
            if first is None or not first[1]:
                return False
            opened, first_line = first
            if not self._header_matches(json.loads(first_line)):
                return False
        except (OSError, json.JSONDecodeError):
            return False
        self.file_dev = opened.st_dev
        self.file_ino = opened.st_ino
        return True

    def _identity_matches(self, payload: dict[str, Any]) -> bool:
        return str(payload.get("id") or "").strip() == self.session_id and _same_path(payload.get("cwd"), self.cwd)

    def _header_matches(self, header: Any) -> bool:
        if not isinstance(header, dict) or header.get("type") != "session_meta":
            return False
        payload = header.get("payload")
        return isinstance(payload, dict) and self._identity_matches(payload)

    def _invalidate_and_reset(self) -> None:
        self._emit_invalidation()
        self._reset_file_state()

    def poll(self) -> None:
        try:
            self._poll_internal()
        except Exception:
            log.warning("codex: rollout poll error", exc_info=True)

    def _poll_internal(self) -> None:
        if not self.session_id:
            return
        if self.session_file is None and not self._discover_session_file():
            return
        if not self.file_verified:
            return

        stat = self._current_stat()
        if stat is None:
            return
        self._apply_size_change(stat)

        if stat.st_size == self.offset and not self.partial_buffer:
            return

        if self.total_bytes_read >= self.max_total_bytes:
            self._exhaust_budget()
            return

        read_size = min(self.max_bytes_per_poll, self.max_total_bytes - self.total_bytes_read)
        if read_size <= 0:
            return

        chunk = self._read_chunk(read_size)
        if not chunk:
            return

        self.offset += len(chunk)
        self.total_bytes_read += len(chunk)
        self._process_chunk(chunk)
        self._finish_read(stat)

    def _discover_session_file(self) -> bool:
        self.session_file = self._find_exact_file()
        if self.session_file is None:
            return False
        try:
            st = self.session_file.stat()
            if self.file_dev is None or self.file_ino is None:
                self.file_dev = st.st_dev
                self.file_ino = st.st_ino
            self.file_verified = True
        except OSError:
            self._reset_file_state()
            return False
        return True

    def _current_stat(self) -> os.stat_result | None:
        """Stat the session file. A missing or replaced file invalidates context and resets state."""
        if self.session_file is None:
            return None
        try:
            stat = self.session_file.stat()
        except OSError:
            self._invalidate_and_reset()
            return None
        if (
            self.file_dev is not None
            and self.file_ino is not None
            and (stat.st_dev, stat.st_ino) != (self.file_dev, self.file_ino)
        ):
            self._invalidate_and_reset()
            return None
        return stat

    def _apply_size_change(self, stat: os.stat_result) -> None:
        if stat.st_size < self.offset:
            self._emit_invalidation()
            self.header_verified = False
            self.offset = 0
            self.partial_buffer = ""
        elif stat.st_size > self.offset and self.has_verified_context:
            self._emit_invalidation()

    def _exhaust_budget(self) -> None:
        if not self.budget_exhausted:
            self._emit_invalidation()
            self.budget_exhausted = True

    def _read_chunk(self, read_size: int) -> bytes:
        """Read the next chunk. An empty result means nothing to process, with state reset on failure."""
        if self.session_file is None:
            return b""
        try:
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
            fd = os.open(self.session_file, flags)
            try:
                opened = os.fstat(fd)
                if not stat_module.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (
                    self.file_dev,
                    self.file_ino,
                ):
                    self._invalidate_and_reset()
                    return b""
                with os.fdopen(fd, "rb") as f:
                    fd = -1
                    if not self.header_verified and not self._verify_header(f):
                        return b""
                    f.seek(self.offset)
                    return f.read(read_size)
            finally:
                if fd >= 0:
                    os.close(fd)
        except OSError:
            self._invalidate_and_reset()
            return b""

    def _verify_header(self, f: Any) -> bool:
        try:
            self.header_verified = self._header_matches(json.loads(f.readline(4096)))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self.header_verified = False
        if not self.header_verified:
            self._invalidate_and_reset()
        return self.header_verified

    def _process_chunk(self, chunk: bytes) -> None:
        text = self.partial_buffer + chunk.decode("utf-8", errors="replace")
        lines = text.split("\n")
        self.partial_buffer = lines.pop()

        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                self._process_record(record)

    def _finish_read(self, stat: os.stat_result) -> None:
        if self.offset >= stat.st_size and not self.partial_buffer and self.pending_context is not None:
            used, window = self.pending_context
            self.counters.record_context(used=used, window=window)
            self.has_verified_context = True
            self._changed()
            self.pending_context = None

        if self.total_bytes_read >= self.max_total_bytes and stat.st_size > self.offset:
            self._exhaust_budget()

    def _process_record(self, record: dict[str, Any]) -> None:
        rec_type = record.get("type", "")
        payload = record.get("payload")

        if rec_type == "session_meta":
            if isinstance(payload, dict) and not self._identity_matches(payload):
                self.file_verified = False
            return

        if not self.file_verified:
            return

        if rec_type == "event_msg" and isinstance(payload, dict):
            self._process_event_msg(payload)
        elif rec_type == "context_compacted":
            self._emit_invalidation(is_compaction=True)

    def _process_event_msg(self, payload: dict[str, Any]) -> None:
        p_type = payload.get("type", "")
        if p_type == "token_count":
            self._record_token_count(payload)
        elif p_type == "context_compacted":
            self._emit_invalidation(is_compaction=True)
        elif p_type in ("session_configured", "model_reroute"):
            self._record_model(payload)

    def _record_token_count(self, payload: dict[str, Any]) -> None:
        info = payload.get("info")
        if not isinstance(info, dict):
            return
        usage = info.get("last_token_usage")
        window = as_int(info.get("model_context_window"))
        if not isinstance(usage, dict):
            return
        input_tokens = as_int(usage.get("input_tokens"))
        output_tokens = as_int(usage.get("output_tokens"))
        reasoning_tokens = as_int(usage.get("reasoning_output_tokens"))
        if input_tokens is None or output_tokens is None or reasoning_tokens is None:
            return
        used = input_tokens + output_tokens - reasoning_tokens
        if used >= 0:
            self.pending_context = (used, window)

    def _record_model(self, payload: dict[str, Any]) -> None:
        model = payload.get("model") or payload.get("to_model")
        if not model:
            return
        model_str = str(model)
        if self.model_name and self.model_name != model_str:
            self._emit_invalidation(is_compaction=False)
        self.model_name = model_str


def _entry_kind(entry: os.DirEntry[str]) -> str:
    """Return "dir" or "file" for a plain entry, "" for a symlink, other entry, or stat failure."""
    try:
        if entry.is_symlink():
            return ""
        if entry.is_dir(follow_symlinks=False):
            return "dir"
        return "file" if entry.is_file(follow_symlinks=False) else ""
    except OSError:
        return ""


def _read_first_line(path: Path, expected: os.stat_result) -> tuple[os.stat_result, str] | None:
    """Open ``path`` without following links and return its stat and first line, or None on an identity mismatch."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(path, flags)
    try:
        opened = os.fstat(fd)
        if not stat_module.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (
            expected.st_dev,
            expected.st_ino,
        ):
            return None
        with os.fdopen(fd, "r", encoding="utf-8", errors="replace") as f:
            fd = -1
            return opened, f.readline(4096)
    finally:
        if fd >= 0:
            os.close(fd)


_SESSION_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _same_path(left: Any, right: Path) -> bool:
    if not isinstance(left, str) or not left.strip():
        return False
    try:
        return Path(left).resolve() == right.resolve()
    except (OSError, ValueError, TypeError):
        try:
            return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(os.fspath(right)))
        except (OSError, ValueError, TypeError):
            return False


__all__ = ["CodexRolloutCollector"]
