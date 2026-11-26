"""Unit tests for A2AServer."""

import asyncio
from typing import TYPE_CHECKING, Any

from pydantic_ai import ModelRequest, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
import pytest

from agentpool import Agent, AgentPool
from agentpool_server.a2a_server import A2AServer


if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fasta2a.client import A2AClient
    from pydantic_ai import ModelMessage


# Test constants
TEST_PORT_BASE = 8001
AGENT_COUNT = 2
EXPECTED_ROUTES = 5  # 2 agents * (POST endpoint + mount) + 1 root endpoint


@pytest.fixture
def simple_agent_pool():
    """Create a simple agent pool for testing."""

    def callback1(message: str) -> str:
        return f"Agent1: {message}"

    def callback2(message: str) -> str:
        return f"Agent2: {message}"

    agent1 = Agent.from_callback(name="agent1", callback=callback1)
    agent2 = Agent.from_callback(name="agent2", callback=callback2)

    pool = AgentPool()
    pool.register("agent1", agent1)
    pool.register("agent2", agent2)
    return pool


async def test_a2a_server_creation(simple_agent_pool: AgentPool):
    """Test A2AServer can be created with agent pool."""
    server = A2AServer(simple_agent_pool, host="localhost", port=TEST_PORT_BASE)
    assert server.pool is simple_agent_pool
    assert server.host == "localhost"
    assert server.port == TEST_PORT_BASE
    assert len(server.pool.get_agents()) == AGENT_COUNT


async def test_a2a_server_initialization(simple_agent_pool: AgentPool):
    """Test A2AServer initialization with pool context."""
    server = A2AServer(simple_agent_pool, host="localhost", port=TEST_PORT_BASE + 1)

    async with server:
        # Pool should be initialized
        assert server.pool is not None
        assert len(server.pool.get_agents()) == AGENT_COUNT


async def test_a2a_server_base_url(simple_agent_pool: AgentPool):
    """Test A2AServer URL generation."""
    port = TEST_PORT_BASE + 2
    server = A2AServer(simple_agent_pool, host="localhost", port=port)
    assert server.base_url == f"http://localhost:{port}"


async def test_a2a_server_agent_url(simple_agent_pool: AgentPool):
    """Test A2AServer agent URL generation."""
    port = TEST_PORT_BASE + 3
    server = A2AServer(simple_agent_pool, host="localhost", port=port)
    url = server.get_agent_url("agent1")
    assert url == f"http://localhost:{port}/agent1"


async def test_a2a_server_agent_card_url(simple_agent_pool: AgentPool):
    """Test A2AServer agent card URL generation."""
    port = TEST_PORT_BASE + 4
    server = A2AServer(simple_agent_pool, host="localhost", port=port)
    url = server.get_agent_card_url("agent1")
    assert url == f"http://localhost:{port}/agent1/.well-known/agent-card.json"


async def test_a2a_server_list_routes(simple_agent_pool: AgentPool):
    """Test A2AServer route listing."""
    port = TEST_PORT_BASE + 5
    server = A2AServer(simple_agent_pool, host="localhost", port=port)
    routes = server.list_agent_routes()

    assert isinstance(routes, dict)
    assert len(routes) == AGENT_COUNT
    assert "agent1" in routes
    assert "agent2" in routes
    assert routes["agent1"]["endpoint"] == f"http://localhost:{port}/agent1"
    assert (
        routes["agent1"]["agent_card"]
        == f"http://localhost:{port}/agent1/.well-known/agent-card.json"
    )
    assert routes["agent1"]["docs"] == f"http://localhost:{port}/agent1/docs"


async def test_a2a_server_app_creation(simple_agent_pool: AgentPool):
    """Test A2AServer Starlette app creation."""
    pytest.importorskip("starlette")

    server = A2AServer(simple_agent_pool, host="localhost", port=TEST_PORT_BASE + 6)

    async with server:
        routes = await server.get_routes()
        assert routes is not None
        # Per agent: the POST endpoint and the mounted FastA2A app; plus the root listing
        assert len(routes) == EXPECTED_ROUTES


@pytest.mark.skip(reason="Requires specific YAML schema - integration test")
async def test_a2a_server_from_config(tmp_path):
    """Test A2AServer creation from config file."""
    pytest.importorskip("starlette")

    # Create a minimal config file
    config_content = """
    environments:
      test:
        type: inline
        module_path: agentpool
        class_name: Agent

    agents:
      test_agent:
        name: test_agent
        environment: test
        model: test
        system_prompt: You are a test agent
    """

    config_path = tmp_path / "test_config.yml"
    config_path.write_text(config_content)

    port = TEST_PORT_BASE + 7
    server = A2AServer.from_config(
        config_path,
        host="localhost",
        port=port,
    )

    assert server.host == "localhost"
    assert server.port == port
    assert len(server.pool.get_agents()) >= 1


async def test_a2a_server_name_generation(simple_agent_pool: AgentPool):
    """Test A2AServer auto-generates name when not provided."""
    server = A2AServer(simple_agent_pool)
    assert server.name is not None
    assert "A2AServer" in server.name


async def test_a2a_server_custom_name(simple_agent_pool: AgentPool):
    """Test A2AServer uses custom name when provided."""
    server = A2AServer(simple_agent_pool, name="custom-a2a-server")
    assert server.name == "custom-a2a-server"


def _echo_model() -> FunctionModel:
    """Answers with the user prompts it has seen so far (shows the session history)."""

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        prompts = [
            part.content if isinstance(part.content, str) else "".join(map(str, part.content))
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, UserPromptPart)
        ]
        yield " | ".join(prompts)

    return FunctionModel(stream_function=stream)


async def _wait_done(client: A2AClient, task_id: str) -> dict[str, Any]:
    for _ in range(200):
        response = await client.get_task(task_id)
        task = response["result"]
        if task["status"]["state"] in {"completed", "failed", "canceled"}:
            return task
        await asyncio.sleep(0.02)
    raise AssertionError("task did not finish")


async def test_a2a_end_to_end_over_asgi():
    """Agent card, message/send, task polling and a second turn in the same context (#54)."""
    from fasta2a.client import A2AClient
    import httpx

    async with AgentPool() as pool:
        await pool.add_agent(Agent(name="bot", model=_echo_model()))
        server = A2AServer(pool, port=TEST_PORT_BASE + 9)
        app = await server.create_app()
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
                card = (await http.get("/bot/.well-known/agent-card.json")).json()
                assert card["name"] == "bot"
                assert (await http.get("/bot/docs")).status_code == 200
                assert (await http.get("/")).json()["count"] == 1

                client = A2AClient("http://test/bot", http_client=http)
                message = {"role": "user", "parts": [{"text": "hello"}], "message_id": "m1"}
                sent = await client.send_message(message)  # type: ignore[arg-type]
                task = await _wait_done(client, sent["result"]["task"]["id"])
                assert task["status"]["state"] == "completed"
                assert task["artifacts"][0]["parts"][0]["text"] == "hello"
                context_id = task["context_id"]

                follow_up = {
                    "role": "user",
                    "parts": [{"text": "again"}],
                    "message_id": "m2",
                    "context_id": context_id,
                }
                sent = await client.send_message(follow_up)  # type: ignore[arg-type]
                task = await _wait_done(client, sent["result"]["task"]["id"])
                assert task["status"]["state"] == "completed"
                # The second turn sees the first one: same session, history kept
                assert task["artifacts"][0]["parts"][0]["text"] == "hello | again"
                assert pool.sessions.get(context_id) is not None


if __name__ == "__main__":
    pytest.main(["-v", __file__])
