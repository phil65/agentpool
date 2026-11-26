"""An MCP tool's error reaches the model as a failed tool result; it doesn't end the run."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import TYPE_CHECKING, Any

from pydantic_ai import ModelMessage, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

from agentpool import Agent
from agentpool_config.mcp_server import StdioMCPServerConfig


if TYPE_CHECKING:
    from collections.abc import AsyncIterator


SERVER = Path(__file__).parent.parent / "mcp_server" / "failing_server.py"


async def test_tool_error_reaches_the_model() -> None:
    seen: list[str] = []

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[Any]:
        returns = [
            part
            for msg in messages
            for part in msg.parts
            if isinstance(part, ToolReturnPart) and part.tool_name == "read_thing"
        ]
        if not returns:
            yield {0: DeltaToolCall(name="read_thing", json_args='{"name": "x"}')}
            return
        seen.append(returns[0].model_response_str())
        yield "handled"

    config = StdioMCPServerConfig(name="failing", command=sys.executable, args=[str(SERVER)])
    agent = Agent(name="caller", model=FunctionModel(stream_function=model), mcp_servers=[config])
    async with agent:
        result = await agent.run("go")
    assert result.content == "handled"
    assert len(seen) == 1
    assert "no such thing: x" in seen[0]
