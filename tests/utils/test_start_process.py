"""start_process waits for the server instead of trusting a fixed delay."""

from __future__ import annotations

import socket
import sys

from anyenv.processes import hard_kill
import anyio
import pytest

from agentpool.utils.subprocess_utils import start_process


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def test_waits_until_the_server_listens() -> None:
    port = _free_port()
    # Listens only after a delay longer than any fixed startup_delay would have been.
    code = (
        "import socket, time; time.sleep(1.5); s = socket.socket();"
        f" s.bind(('127.0.0.1', {port})); s.listen(); time.sleep(60)"
    )
    process = await start_process(
        f'"{sys.executable}" -c "{code}"', ready_address=("127.0.0.1", port), startup_timeout=20
    )
    try:
        stream = await anyio.connect_tcp("127.0.0.1", port)
        await stream.aclose()
    finally:
        await hard_kill(process)


async def test_a_process_exiting_during_startup_is_reported() -> None:
    port = _free_port()
    with pytest.raises(RuntimeError, match="exited with code 3"):
        await start_process(
            f'"{sys.executable}" -c "raise SystemExit(3)"',
            ready_address=("127.0.0.1", port),
            startup_timeout=20,
        )


async def test_a_server_that_never_listens_times_out() -> None:
    port = _free_port()
    process_cmd = f'"{sys.executable}" -c "import time; time.sleep(60)"'
    with pytest.raises(TimeoutError, match=f"127.0.0.1:{port}"):
        await start_process(process_cmd, ready_address=("127.0.0.1", port), startup_timeout=0.5)
