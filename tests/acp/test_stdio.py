"""Running agents with `run_agent`."""

from __future__ import annotations

import asyncio

import pytest

from acp.agent.implementations.testing import TestAgent as EchoAgent
from acp.stdio import run_agent
from tests.acp.streams import TIMEOUT, make_streams


async def test_run_agent_returns_when_client_disconnects() -> None:
    writer, reader, peer = make_streams()
    runner = asyncio.create_task(run_agent(EchoAgent(), writer, reader))
    await peer.send({
        "jsonrpc": "2.0",
        "id": 0,
        "method": "initialize",
        "params": {"protocolVersion": 1},
    })
    assert (await peer.receive())["result"]["protocolVersion"] == 1
    await peer.hang_up()
    await asyncio.wait_for(runner, TIMEOUT)


async def test_run_agent_requires_both_or_nomake_streams() -> None:
    writer, _reader, _peer = make_streams()
    with pytest.raises(ValueError, match="both"):
        await run_agent(EchoAgent(), writer, None)
