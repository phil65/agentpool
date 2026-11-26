from typing import TYPE_CHECKING, Any

from pydantic_ai import ToolDefinition

from agentpool.agents.native_agent.agent import Agent
from agentpool.tools.base import FunctionTool


if TYPE_CHECKING:
    from pydantic_ai import Agent as PydanticAgent
    from schemez import OpenAIFunctionDefinition


def my_tool(arg1: str):
    """Original docstring."""
    return f"Hello {arg1}"


async def test_schema_override_propagation():
    """Test that schema overrides are propagated to the PydanticAI agent via prepare."""
    # Define a schema override
    override: OpenAIFunctionDefinition = {
        "name": "my_tool",
        "description": "Overridden description",
        "parameters": {
            "type": "object",
            "properties": {
                "arg1": {"type": "string", "description": "Overridden argument description"}
            },
            "required": ["arg1"],
        },
    }

    tool = FunctionTool.from_callable(my_tool, schema_override=override)
    agent = Agent(name="test-agent", model="test", tools=[tool])
    pydantic_agent: PydanticAgent[Any, Any] = await agent.get_agentlet(None, None)
    found_tool_def = None
    # Inspect _function_toolset or _user_toolsets to find the tool
    # Note: This relies on pydantic-ai internals, which might change.
    # But it's the only way to inspect without running the agent against an LLM.
    toolsets: list[Any] = []
    toolsets.append(pydantic_agent._function_toolset)
    toolsets.extend(pydantic_agent._user_toolsets)
    for ts in toolsets:
        tools = getattr(ts, "tools", {})
        if isinstance(tools, dict):
            if "my_tool" in tools:
                found_tool_def = tools["my_tool"]
                break
        elif isinstance(tools, list):
            for t in tools:
                if getattr(t, "name", "") == "my_tool":
                    found_tool_def = t
                    break
        if found_tool_def:
            break

    assert found_tool_def is not None, "Tool not found in pydantic agent"
    assert found_tool_def.prepare is not None, "prepare function was not set on the tool"

    # Verify prepare function logic
    # Create a mock context required for prepare
    class MockCtx:
        deps = None
        retry = 0
        tool_name = "my_tool"
        model = None

    initial_def = ToolDefinition(
        name=found_tool_def.name,
        description=found_tool_def.description,
        parameters_json_schema=found_tool_def.function_schema.json_schema,
    )

    res = await found_tool_def.prepare(MockCtx(), initial_def)
    assert res is not None
    assert res.description == "Overridden description"
    assert res.parameters_json_schema == override["parameters"]
