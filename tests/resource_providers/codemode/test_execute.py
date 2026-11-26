"""CodeMode actually runs the wrapped tools (with the AgentContext injected)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Self

from pydantic_ai import ToolReturnPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

from agentpool import Agent, AgentContext
from agentpool.resource_providers import StaticResourceProvider
from agentpool.resource_providers.codemode import (
    CodeModeResourceProvider,
    RemoteCodeModeResourceProvider,
    remote_provider,
)
from agentpool.tool_impls.list_directory import create_list_directory_tool
from agentpool.tools.base import FunctionTool


if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from pydantic_ai import ModelMessage
    import pytest


async def greet(ctx: AgentContext, name: str) -> str:
    """Greet someone, naming the agent (context parameter called ``ctx``)."""
    return f"{ctx.node_name} greets {name}"


async def tag(agent_ctx: AgentContext, value: int) -> str:
    """Tag a value with the agent name (context parameter called ``agent_ctx``)."""
    return f"{agent_ctx.node_name}:{value}"


def double(x: int) -> int:
    """Double a number."""
    return x * 2


def _provider() -> CodeModeResourceProvider:
    tools = [FunctionTool.from_callable(f) for f in (greet, tag, double)]
    return CodeModeResourceProvider([StaticResourceProvider(tools=tools)])


CODE = """
async def main():
    a = await greet(name="Bob")
    b = await tag(value=await double(x=21))
    return f"{a} | {b}"
"""


async def test_execute_runs_wrapped_tools_with_context() -> None:
    provider = _provider()
    async with Agent(name="coder", model="test") as agent:
        result = await provider.execute(agent.get_context(), CODE, "greet")
    assert result == "coder greets Bob | coder:42"


async def test_codemode_tool_called_by_the_model() -> None:
    """The model calls the CodeMode meta tool, which calls a context-taking tool."""
    returned: list[Any] = []

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[Any]:
        returns = [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)]
        if returns:
            returned.extend(r.content for r in returns)
            yield "done"
            return
        [tool] = info.function_tools
        args = json.dumps({"python_code": CODE, "title": "greet"})
        yield {0: DeltaToolCall(name=tool.name, json_args=args)}

    agent = Agent(name="coder", model=FunctionModel(stream_function=model), toolsets=[_provider()])
    async with agent:
        await agent.run("go")
    assert returned == ["coder greets Bob | coder:42"]


async def test_remote_codemode_exposes_the_wrapped_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#53.2: the remote executor gets the wrapped tools, not the meta tool."""
    seen: list[str] = []

    class FakeExecutor:
        async def __aenter__(self) -> Self:
            return self

    def from_tools(tools: list[Any], *args: Any, **kwargs: Any) -> FakeExecutor:
        seen.extend(t.name for t in tools)
        return FakeExecutor()

    monkeypatch.setattr(remote_provider.RemoteCodeExecutor, "from_tools", from_tools)
    tools = [FunctionTool.from_callable(double)]
    provider = RemoteCodeModeResourceProvider([StaticResourceProvider(tools=tools)])
    await provider._get_code_executor()
    assert seen == ["double"]


async def test_execute_runs_a_builtin_tool(tmp_path: Path) -> None:
    """A built-in tool taking ``ctx: AgentContext`` works through CodeMode (#53.3)."""
    (tmp_path / "hello.txt").write_text("x")
    tool = create_list_directory_tool()
    provider = CodeModeResourceProvider([StaticResourceProvider(tools=[tool])])
    code = f"async def main():\n    return await {tool.name}(path={str(tmp_path)!r})\n"
    async with Agent(name="coder", model="test") as agent:
        result = await provider.execute(agent.get_context(), code, "ls")
    assert "hello.txt" in str(result)
