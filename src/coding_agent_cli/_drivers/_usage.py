"""Flat token counters: six nullable integers, null when unknown, never zero-filled."""

from __future__ import annotations

from typing import Any

from ..types import Usage

CONSUMPTION_KEYS = ("input", "output", "cache_read", "cache_write")


def _count(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


class TokenCounters:
    """Responses sum across ids; CLI totals and context override them per field."""

    def __init__(self) -> None:
        self._responses: dict[str, dict[str, int]] = {}
        self._totals: dict[str, int] = {}
        self._context: dict[str, int] = {}

    def record_response(
        self,
        response_id: str,
        *,
        input: Any = None,
        output: Any = None,
        cache_read: Any = None,
        cache_write: Any = None,
    ) -> None:
        values = {"input": input, "output": output, "cache_read": cache_read, "cache_write": cache_write}
        counted = {key: _count(value) for key, value in values.items()}
        self._responses[response_id] = {key: value for key, value in counted.items() if value is not None}

    def record_totals(
        self,
        *,
        input: Any = None,
        output: Any = None,
        cache_read: Any = None,
        cache_write: Any = None,
    ) -> None:
        values = {"input": input, "output": output, "cache_read": cache_read, "cache_write": cache_write}
        for key, value in values.items():
            count = _count(value)
            if count is not None:
                self._totals[key] = count

    def record_context(self, *, used: Any = None, window: Any = None) -> None:
        for key, value in (("context_used", used), ("context_window", window)):
            count = _count(value)
            if count is not None:
                self._context[key] = count

    def clear_context(self) -> None:
        self._context.pop("context_used", None)

    def usage(self) -> Usage:
        counters: dict[str, int | None] = {}
        for key in CONSUMPTION_KEYS:
            if key in self._totals:
                counters[key] = self._totals[key]
            else:
                values = [response[key] for response in self._responses.values() if key in response]
                counters[key] = sum(values) if values else None
        return Usage(
            context_used=self._context.get("context_used"),
            context_window=self._context.get("context_window"),
            **counters,
        )


__all__ = ["TokenCounters"]
