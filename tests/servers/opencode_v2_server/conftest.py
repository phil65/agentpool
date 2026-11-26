"""Fixtures for the OpenCode V2 server tests."""

import asyncio
from typing import TYPE_CHECKING, Any

from httpx2 import ASGITransport, AsyncClient
import pytest

from agentpool import AgentPool
from agentpool.models.manifest import AgentsManifest
from agentpool_server.opencode_v2_server.event_store import EventStore
from agentpool_server.opencode_v2_server.server import create_app


if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from fastapi import FastAPI

    from agentpool_server.opencode_v2_server.state import ServerState


def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b


MANIFEST: dict[str, Any] = {
    "agents": {
        "helper": {"type": "native", "model": "test", "description": "Test helper"},
        "other": {"type": "native", "model": "test"},
        "guarded": {"type": "native", "model": "test", "requires_tool_confirmation": "always"},
    },
    "storage": {"providers": [{"type": "memory"}]},
}


@pytest.fixture
async def pool() -> AsyncIterator[AgentPool[Any]]:
    """Pool with test-model agents (``helper`` has an ``add`` tool)."""
    async with AgentPool(AgentsManifest.model_validate(MANIFEST)) as agent_pool:
        agent_pool.all_agents["helper"].tools.register_tool(add)
        agent_pool.all_agents["guarded"].tools.register_tool(add)
        yield agent_pool


@pytest.fixture
async def app(
    pool: AgentPool[Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[FastAPI]:
    """The V2 app (no auth) with a temporary event store, running its lifespan."""
    monkeypatch.delenv("OPENCODE_PASSWORD", raising=False)
    monkeypatch.delenv("OPENCODE_SERVER_PASSWORD", raising=False)
    application = create_app(pool, working_dir=str(tmp_path), store=EventStore(tmp_path / "events"))
    application.state.v2_state.permissions.saved_file = tmp_path / "permissions.json"
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
def state(app: FastAPI) -> ServerState:
    """The server state of the app."""
    server_state: ServerState = app.state.v2_state
    return server_state


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """HTTP client talking to the app in-process."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client


async def create_session(client: AsyncClient, **body: Any) -> str:
    """Create a session and return its ID."""
    response = await client.post("/api/session", json=body)
    assert response.status_code == 200, response.text
    session_id: str = response.json()["data"]["id"]
    return session_id


async def prompt_and_wait(client: AsyncClient, session_id: str, text: str) -> None:
    """Send a prompt and wait until the session's inbox is drained."""
    response = await client.post(f"/api/session/{session_id}/prompt", json={"text": text})
    assert response.status_code == 200, response.text
    response = await client.post(f"/api/experimental/session/{session_id}/wait")
    assert response.status_code == 204, response.text


async def messages(client: AsyncClient, session_id: str) -> list[dict[str, Any]]:
    """Messages of a session, oldest first."""
    response = await client.get(
        f"/api/session/{session_id}/message", params={"order": "asc", "limit": "200"}
    )
    assert response.status_code == 200, response.text
    data: list[dict[str, Any]] = response.json()["data"]
    return data


async def wait_for(predicate: Any, timeout: float = 5.0) -> None:
    """Poll an async predicate until it returns truthy."""
    async with asyncio.timeout(timeout):
        while not await predicate():
            await asyncio.sleep(0.02)
