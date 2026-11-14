"""Runs each real CLI once. Excluded by default; run with ``uv run pytest -m live -rP``."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from coding_agent_cli import Outcome, Request, check, run

PROMPT = "Reply with exactly the word PONG and nothing else."

# Permission and approval flags are never added by the drivers; unattended runs pass them here.
UNATTENDED_ARGS: dict[str, tuple[str, ...]] = {
    "claude": ("--permission-mode", "bypassPermissions"),
    "codex": ("--dangerously-bypass-approvals-and-sandbox",),
    "pi": (),
    "agy": ("--dangerously-skip-permissions",),
}


@pytest.mark.live
@pytest.mark.parametrize("driver", sorted(UNATTENDED_ARGS))
async def test_live_pong(driver: str, tmp_path: Path, record_property) -> None:
    if shutil.which(driver) is None:
        pytest.skip(f"{driver}: missing_cli")
    capabilities = await check(driver)
    assert capabilities.available
    version = capabilities.version or ""
    print(f"{driver} --version: {version}")
    record_property(f"{driver}_version", version)

    result = await run(Request(driver=driver, prompt=PROMPT, cwd=tmp_path, args=UNATTENDED_ARGS[driver], timeout_s=300))

    if result.outcome == Outcome.AGENT_FATAL:
        pytest.skip(f"{driver}: {result.error}")
    assert result.outcome == Outcome.SUCCESS, result.error
    assert "PONG" in result.text.upper()
    assert result.session_id
