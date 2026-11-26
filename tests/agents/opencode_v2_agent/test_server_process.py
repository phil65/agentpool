"""The private ``opencode serve`` runs in its own process group (killing it spares us)."""

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


async def test_server_stderr_is_drained_while_it_runs(tmp_path: Path) -> None:
    """A chatty ``opencode serve`` must not block on a full stderr pipe."""
    marker = tmp_path / "wrote-everything"
    fake = tmp_path / "opencode"
    # 1 MB of stderr (a pipe holds 64 KB), then a marker, then an exit with a last line
    fake.write_text(
        "#!/bin/sh\n"
        "head -c 1000000 /dev/zero | tr '\\\\0' 'x' >&2\n"
        f"echo >&2; touch {marker}\n"
        "echo 'fatal: no config' >&2\n"
        "exit 3\n"
    )
    fake.chmod(0o755)
    agent = OpenCodeV2Agent(name="oc", command=str(fake), startup_timeout=10.0)
    with pytest.raises(RuntimeError, match="exited with code 3") as info:
        await agent._spawn_server()
    assert marker.exists()
    assert "fatal: no config" in str(info.value)
    assert len(str(info.value)) < 200_000  # Only the tail goes into the message
