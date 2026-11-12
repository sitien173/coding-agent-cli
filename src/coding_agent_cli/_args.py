"""Shared validation of raw ``args`` and portable options."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from .types import Diagnostic, Request

PORTABLE_FIELDS = ("model", "reasoning", "system_prompt")


def _has_flag(args: Sequence[str], flags: Iterable[str]) -> str | None:
    """Return the first token in ``args`` that is one of ``flags`` or ``flag=value``."""
    flag_set = set(flags)
    for token in args:
        if token in flag_set or token.split("=", 1)[0] in flag_set:
            return token
    return None


def validate_args(args: Sequence[Any]) -> Diagnostic | None:
    """Reject non-string args, NUL bytes, and the reserved bare ``--`` token."""
    if not all(isinstance(value, str) for value in args):
        return Diagnostic(code="invalid_args", message="args must contain only strings")
    if any("\x00" in value for value in args):
        return Diagnostic(code="invalid_args", message="args cannot contain NUL bytes")
    if "--" in args:
        return Diagnostic(code="invalid_args", message="args cannot contain the reserved '--' token")
    return None


def reject_transport_flags(args: Sequence[str], owned: Iterable[str]) -> Diagnostic | None:
    """Reject a raw arg that overrides a flag the driver owns for transport."""
    token = _has_flag(args, owned)
    if token is None:
        return None
    return Diagnostic(code="invalid_args", message=f"args cannot override the driver-owned flag {token.split('=', 1)[0]!r}")


def reject_unsupported(req: Request, supported: Iterable[str]) -> Diagnostic | None:
    """Reject a portable option this driver cannot express."""
    allowed = set(supported)
    for name in PORTABLE_FIELDS:
        if getattr(req, name) is not None and name not in allowed:
            return Diagnostic(code="unsupported_option", message=f"driver {req.driver!r} does not support {name}")
    return None


def reject_conflicts(req: Request, flags: Mapping[str, Iterable[str]]) -> Diagnostic | None:
    """Reject a raw arg that sets a portable field already given. ``flags`` maps field to its flags."""
    for name, field_flags in flags.items():
        if getattr(req, name) is None:
            continue
        token = _has_flag(req.args, field_flags)
        if token is not None:
            return Diagnostic(
                code="invalid_args",
                message=f"args flag {token.split('=', 1)[0]!r} conflicts with the {name} option",
            )
    return None


__all__ = ["PORTABLE_FIELDS", "reject_conflicts", "reject_transport_flags", "reject_unsupported", "validate_args"]
