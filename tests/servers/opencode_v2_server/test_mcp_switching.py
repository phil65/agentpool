"""``POST /api/experimental/mcp/{server}/connect`` and ``/disconnect``."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentpool.agents.exceptions import UnsupportedOperationError
from agentpool.common_types import MCPServerStatus


if TYPE_CHECKING:
    from httpx2 import AsyncClient
    import pytest

    from agentpool import AgentPool


def _with_server(
    pool: AgentPool[Any], monkeypatch: pytest.MonkeyPatch, *, unsupported: bool = False
) -> list[tuple[str, bool]]:
    switched: list[tuple[str, bool]] = []
    agent = pool.all_agents["helper"]

    async def servers() -> dict[str, MCPServerStatus]:
        return {"github": MCPServerStatus(name="github", status="connected")}

    async def switch(name: str, enabled: bool) -> None:
        if unsupported:
            raise UnsupportedOperationError(agent.name, "switch", "not here")
        switched.append((name, enabled))

    monkeypatch.setattr(agent, "get_mcp_server_info", servers)
    monkeypatch.setattr(agent, "set_mcp_server_enabled", switch)
    return switched


async def test_connect_and_disconnect_switch_the_agents_server(
    client: AsyncClient, pool: AgentPool[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    switched = _with_server(pool, monkeypatch)
    response = await client.post("/api/experimental/mcp/github/disconnect")
    assert response.status_code == 204, response.text
    response = await client.post("/api/experimental/mcp/github/connect")
    assert response.status_code == 204, response.text
    assert switched == [("github", False), ("github", True)]


async def test_an_unknown_server_is_not_found(client: AsyncClient) -> None:
    response = await client.post("/api/experimental/mcp/nope/connect")
    assert response.status_code == 404


async def test_an_agent_that_cannot_switch_servers_is_unavailable(
    client: AsyncClient, pool: AgentPool[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    _with_server(pool, monkeypatch, unsupported=True)
    response = await client.post("/api/experimental/mcp/github/connect")
    assert response.status_code == 503
