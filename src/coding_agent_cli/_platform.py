"""Cross-platform launch and process-tree kill helpers.

Agent CLIs often start child processes (an npm ``.cmd`` launcher starts Node on
Windows). Each CLI runs in its own process group so the whole tree can be killed.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import signal
import subprocess
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

TERM_GRACE_S = 1.0
_POLL_S = 0.05
_TASKKILL_TIMEOUT_S = 5.0


def prepare_command(command: list[str]) -> list[str]:
    """Use a safe native launcher for a resolved command.

    npm exposes both ``.cmd`` and ``.ps1`` shims on Windows. Passing untrusted
    prompt text through a batch shim lets ``cmd.exe`` interpret characters such
    as ``&`` and ``%``. Prefer the companion PowerShell shim, whose ``-File``
    arguments retain their literal values.
    """
    if os.name != "nt" or not command:
        return command
    executable = Path(command[0])
    if executable.suffix.lower() not in {".bat", ".cmd"}:
        return command
    powershell_shim = executable.with_suffix(".ps1")
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if not powershell_shim.is_file() or powershell is None:
        raise OSError(
            f"Windows batch launcher {executable} requires a matching .ps1 shim "
            "and PowerShell so arguments can be passed safely"
        )
    return [
        powershell,
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        os.fspath(powershell_shim),
        *command[1:],
    ]


def process_group_kwargs() -> Mapping[str, Any]:
    """Return subprocess options for a new, independently killable group."""
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    return {"start_new_session": True}


def _taskkill(process_id: int) -> None:
    taskkill = shutil.which("taskkill")
    if taskkill is None:
        return
    try:
        subprocess.run(
            [taskkill, "/PID", str(process_id), "/T", "/F"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            shell=False,
            timeout=_TASKKILL_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        return


def _group_exists(group_id: int) -> bool:
    try:
        os.killpg(group_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _signal_group(group_id: int, sig: int) -> bool:
    """Send ``sig`` to the group. Return False when the group is gone."""
    try:
        os.killpg(group_id, sig)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


async def kill_process_tree(process_id: int, *, grace_s: float = TERM_GRACE_S) -> None:
    """Kill a POSIX process group or a Windows process tree without blocking the loop.

    POSIX sends SIGTERM, waits up to ``grace_s`` for the group to go away, then sends
    SIGKILL. Windows runs ``taskkill /T /F`` in a thread.
    """
    if os.name == "nt":
        await asyncio.to_thread(_taskkill, process_id)
        return
    if not _signal_group(process_id, signal.SIGTERM):
        return
    deadline = time.monotonic() + max(0.0, grace_s)
    while _group_exists(process_id) and time.monotonic() < deadline:
        await asyncio.sleep(_POLL_S)
    if _group_exists(process_id):
        _signal_group(process_id, signal.SIGKILL)


__all__ = ["kill_process_tree", "prepare_command", "process_group_kwargs"]
