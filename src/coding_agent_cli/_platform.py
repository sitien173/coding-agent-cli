"""Cross-platform launch and process-tree kill helpers.

Agent CLIs often start child processes (an npm ``.cmd`` launcher starts Node on
Windows). Each CLI runs in its own process group so the whole tree can be killed.
"""

from __future__ import annotations

import asyncio
import ctypes
import os
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

TERM_GRACE_S = 1.0
_POLL_S = 0.05
_TASKKILL_TIMEOUT_S = 5.0
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000

if sys.platform == "win32":
    from ctypes import WinDLL, c_void_p, wintypes

    _PROCESS_TERMINATE = 0x0001
    _PROCESS_SET_QUOTA = 0x0100
    _kernel32 = WinDLL("kernel32", use_last_error=True)
    _kernel32.CreateJobObjectW.argtypes = (c_void_p, wintypes.LPCWSTR)
    _kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    _kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    _kernel32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
    _kernel32.TerminateJobObject.restype = wintypes.BOOL
    _kernel32.SetInformationJobObject.argtypes = (wintypes.HANDLE, wintypes.INT, c_void_p, wintypes.DWORD)
    _kernel32.SetInformationJobObject.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _kernel32.CloseHandle.restype = wintypes.BOOL

    class _JobObjectBasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class _JobObjectExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _JobObjectBasicLimitInformation),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]


# Windows job object handle per tracked process ID.
_jobs: dict[int, int] = {}


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


def track_process_tree(process_id: int) -> None:
    """Put a Windows process in a job object so descendants it orphans can still be killed.

    ``taskkill /T`` finds descendants through their parent, so it misses a grandchild
    whose parent already exited. A job holds every descendant started after this call.
    No-op on POSIX, where the process group covers this.
    """
    if sys.platform != "win32":
        return
    job = _kernel32.CreateJobObjectW(None, None)
    if not job:
        return
    limits = _JobObjectExtendedLimitInformation()
    limits.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    configured = bool(
        _kernel32.SetInformationJobObject(
            job,
            9,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        )
    )
    if not configured:
        _kernel32.CloseHandle(job)
        return
    process = _kernel32.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, process_id)
    assigned = bool(process) and bool(_kernel32.AssignProcessToJobObject(job, process))
    if process:
        _kernel32.CloseHandle(process)
    if assigned:
        _jobs[process_id] = job
    else:
        _kernel32.CloseHandle(job)


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
    assert sys.platform != "win32"
    try:
        os.killpg(group_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _signal_group(group_id: int, sig: int) -> bool:
    """Send ``sig`` to the group. Return False when the group is gone."""
    assert sys.platform != "win32"
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
    SIGKILL. Windows terminates the tracked job, or runs ``taskkill /T /F`` in a thread.
    """
    if sys.platform == "win32":
        job = _jobs.pop(process_id, None)
        if job is None:
            await asyncio.to_thread(_taskkill, process_id)
            return
        _kernel32.TerminateJobObject(job, 1)
        _kernel32.CloseHandle(job)
        return
    if not _signal_group(process_id, signal.SIGTERM):
        return
    deadline = time.monotonic() + max(0.0, grace_s)
    while _group_exists(process_id) and time.monotonic() < deadline:  # noqa: ASYNC110 - no event signals group exit
        await asyncio.sleep(_POLL_S)
    if _group_exists(process_id):
        _signal_group(process_id, signal.SIGKILL)


async def force_kill_process_tree(process_id: int) -> None:
    """Immediately kill the tracked process tree after a cleanup deadline expires."""
    if sys.platform == "win32":
        job = _jobs.pop(process_id, None)
        if job is not None:
            _kernel32.TerminateJobObject(job, 1)
            _kernel32.CloseHandle(job)
        else:
            await asyncio.to_thread(_taskkill, process_id)
        return
    _signal_group(process_id, signal.SIGKILL)


__all__ = [
    "force_kill_process_tree",
    "kill_process_tree",
    "prepare_command",
    "process_group_kwargs",
    "track_process_tree",
]
