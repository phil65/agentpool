"""The private ``opencode serve`` runs in its own process group (killing it spares us)."""

from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING, Any

import pytest

from agentpool.agents.opencode_v2_agent.opencode_v2_agent import OpenCodeV2Agent


if TYPE_CHECKING:
    from pathlib import Path


pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="process groups are POSIX")


async def test_server_runs_in_its_own_process_group(tmp_path: Path) -> None:
    fake = tmp_path / "opencode"
    fake.write_text("#!/bin/sh\nexec sleep 60\n")
    fake.chmod(0o755)
    agent = OpenCodeV2Agent(name="oc", command=str(fake))

    async def ready(*args: Any) -> None:
        pass

    agent._wait_for_server = ready  # type: ignore[method-assign]  # ty:ignore[invalid-assignment]
    await agent._spawn_server()
    proc = agent._process
    assert proc is not None
    try:
        assert os.getpgid(proc.pid) != os.getpgrp()  # hard_kill SIGKILLs the whole group
    finally:
        await agent._stop_process()
    assert proc.returncode is not None
