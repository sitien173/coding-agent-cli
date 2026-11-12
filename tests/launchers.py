"""Install fake CLI launchers that work on POSIX and Windows.

On Windows the launcher is an npm-style ``<name>.cmd`` plus ``<name>.ps1`` pair, so
tests run through the library's ``prepare_command`` path.
"""

from __future__ import annotations

import shlex
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path


def _ps_literal(token: str) -> str:
    return "'" + token.replace("'", "''") + "'"


def install_launcher(bin_dir: Path, name: str, script: Path, args: Sequence[str] = ()) -> Path:
    """Write a launcher that runs ``sys.executable script *args`` plus caller arguments.

    Returns the path ``shutil.which(name, path=str(bin_dir))`` resolves to.
    """
    if sys.platform != "win32":
        launcher = bin_dir / name
        fixed = " ".join(shlex.quote(token) for token in (sys.executable, str(script), *args))
        launcher.write_text(f'#!/bin/sh\nexec {fixed} "$@"\n', encoding="utf-8")
        launcher.chmod(0o755)
        return launcher

    if not (shutil.which("pwsh") or shutil.which("powershell")):
        raise RuntimeError("PowerShell is required to install a Windows test launcher")
    fixed = " ".join(_ps_literal(token) for token in (sys.executable, str(script), *args))
    (bin_dir / f"{name}.ps1").write_text(
        f"& {fixed} @args\nexit $LASTEXITCODE\n",
        encoding="utf-8",
    )
    batch = " ".join(f'"{token}"' for token in (sys.executable, str(script), *args))
    launcher = bin_dir / f"{name}.cmd"
    launcher.write_text(f"@{batch} %*\r\n", encoding="utf-8")
    return launcher
