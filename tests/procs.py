"""Process liveness probe that works on POSIX and Windows.

On Windows ``os.kill(pid, 0)`` calls ``TerminateProcess``, so it cannot be used as a probe.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

if sys.platform == "win32":
    from ctypes import WinDLL, byref, wintypes

    _kernel32 = WinDLL("kernel32", use_last_error=True)
    _kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, wintypes.LPDWORD)
    _kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _kernel32.CloseHandle.restype = wintypes.BOOL

    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _STILL_ACTIVE = 259

    def pid_running(pid: int) -> bool:
        handle = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            if not _kernel32.GetExitCodeProcess(handle, byref(code)):
                return False
            return code.value == _STILL_ACTIVE
        finally:
            _kernel32.CloseHandle(handle)

else:

    def pid_running(pid: int) -> bool:
        stat_path = Path(f"/proc/{pid}/stat")
        if stat_path.exists():
            try:
                return stat_path.read_text(encoding="utf-8").split(") ", 1)[1][0] != "Z"
            except (IndexError, OSError):
                return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True
