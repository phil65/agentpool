"""serve-mcp serves agent tools without caching their results."""

from typing import TYPE_CHECKING
from uuid import uuid4

from fastmcp import Client
from mcp import types
from pydantic_ai.models.function import AgentInfo, FunctionModel
import pytest

from agentpool import Agent, AgentPool
from agentpool_config.pool_server import MCPPoolServerConfig
from agentpool_server.mcp_server.server import MCPServer


if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastmcp.client.client import CallToolResult
    from pydantic_ai import ModelMessage


def _text(result: CallToolResult) -> str:
    match result.content:
        case [types.TextContent(text=text), *_]:
            return text
        case _:
            raise AssertionError(f"no text content: {result}")


async def test_repeated_calls_run_the_agent_again() -> None:
    calls = 0

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        nonlocal calls
        calls += 1
        yield f"answer {calls}"

    async with AgentPool() as pool:
        await pool.add_agent(Agent(name="helper", model=FunctionModel(stream_function=stream)))
        server = MCPServer(pool, MCPPoolServerConfig())
        await server._register_pool_tools()
        prompt = f"hi {uuid4()}"
        async with Client(server.fastmcp) as client:
            first = await client.call_tool("ask_helper", {"prompt": prompt})
            second = await client.call_tool("ask_helper", {"prompt": prompt})
    assert calls == 2
    assert [_text(first), _text(second)] == ["answer 1", "answer 2"]


async def _tool_names(serve_nodes: list[str] | bool) -> list[str]:
    async with AgentPool() as pool:
        for name in ("a", "b"):
            await pool.add_agent(Agent(name, model=FunctionModel(stream_function=_echo)))
        server = MCPServer(pool, MCPPoolServerConfig(serve_nodes=serve_nodes))
        await server._register_pool_tools()
        async with Client(server.fastmcp) as client:
            return sorted(tool.name for tool in await client.list_tools())


async def _echo(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
    yield "echo"


async def test_serve_nodes_picks_the_served_agents() -> None:
    assert await _tool_names(True) == ["ask_a", "ask_b"]
    assert await _tool_names(["b"]) == ["ask_b"]
    assert await _tool_names(False) == []


async def _prompt_server(serve_prompts: list[str] | bool) -> tuple[AgentPool, MCPServer]:
    from agentpool import AgentsManifest

    manifest = AgentsManifest.model_validate({
        "prompts": {
            "system_prompts": {
                "reviewer": {"content": "Review the code carefully.", "category": "role"},
                "tester": {"content": "Write the tests first.", "category": "methodology"},
            }
        }
    })
    pool = AgentPool(manifest)
    return pool, MCPServer(pool, MCPPoolServerConfig(serve_prompts=serve_prompts))


async def test_prompt_library_is_served_as_mcp_prompts() -> None:
    pool, server = await _prompt_server(True)
    async with pool, Client(server.fastmcp) as client:
        names = sorted(prompt.name for prompt in await client.list_prompts())
        result = await client.get_prompt("reviewer")
    assert names == ["reviewer", "tester"]
    match result.messages:
        case [message]:
            assert isinstance(message.content, types.TextContent)
            assert message.content.text == "Review the code carefully."
        case _:
            raise AssertionError(result.messages)


@pytest.mark.parametrize(("serve_prompts", "expected"), [(["tester"], ["tester"]), (False, [])])
async def test_serve_prompts_picks_the_served_prompts(
    serve_prompts: list[str] | bool, expected: list[str]
) -> None:
    pool, server = await _prompt_server(serve_prompts)
    async with pool, Client(server.fastmcp) as client:
        assert sorted(prompt.name for prompt in await client.list_prompts()) == expected


@pytest.mark.parametrize(
    ("cors_origins", "origin", "allowed"),
    [
        (["http://localhost:6274"], "http://localhost:6274", True),
        (["http://localhost:6274"], "https://evil.example", False),
        ([], "http://localhost:6274", False),
    ],
)
async def test_cors_origins_answer_browser_preflights(
    cors_origins: list[str], origin: str, allowed: bool
) -> None:
    import httpx

    async with AgentPool() as pool:
        server = MCPServer(pool, MCPPoolServerConfig(cors_origins=cors_origins))
        app = server.fastmcp.http_app(**server.http_options())
        transport = httpx.ASGITransport(app=app)
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=transport, base_url="http://localhost") as http,
        ):
            response = await http.options(
                "/mcp",
                headers={"Origin": origin, "Access-Control-Request-Method": "POST"},
            )
    assert (response.headers.get("access-control-allow-origin") == origin) is allowed
