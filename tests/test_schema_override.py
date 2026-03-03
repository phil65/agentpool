from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from agentpool.agents.native_agent.agent import Agent
from agentpool.tools.base import Tool


if TYPE_CHECKING:
    from pydantic_ai import Agent as PydanticAgent
    from schemez import OpenAIFunctionDefinition


def my_tool(arg1: str):
    """Original docstring."""
    return f"Hello {arg1}"


@pytest.mark.asyncio
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

    tool = Tool.from_callable(my_tool, schema_override=override)

    agent = Agent(name="test-agent", model="openai:gpt-4o", tools=[tool])

    pydantic_agent: PydanticAgent[Any, Any] = await agent.get_agentlet(None, None)

    found_tool_def = None

    # Inspect _function_toolset or _user_toolsets to find the tool
    # Note: This relies on pydantic-ai internals, which might change.
    # But it's the only way to inspect without running the agent against an LLM.

    toolsets: list[Any] = []
    if hasattr(pydantic_agent, "_function_toolset"):
        toolsets.append(pydantic_agent._function_toolset)
    if hasattr(pydantic_agent, "_user_toolsets"):
        toolsets.extend(pydantic_agent._user_toolsets)  # type: ignore

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

    # Verify that schema_override is baked into function_schema
    # In RFC-0002, schema_override is handled in Tool.to_pydantic_ai()
    # and merged into function_schema, not applied via prepare()
    assert found_tool_def.function_schema is not None, "function_schema was not set on the tool"

    # Check that description and parameter descriptions from override are in the schema
    json_schema = found_tool_def.function_schema.json_schema
    assert json_schema is not None
    # The tool description itself is NOT overridden (stays as docstring)
    # But the json_schema's description IS overridden
    assert json_schema["description"] == "Overridden description"

    # Verify parameter descriptions are overridden
    if "properties" in json_schema and "arg1" in json_schema["properties"]:
        arg1_desc = json_schema["properties"]["arg1"]
        # Check that description matches the override
        if isinstance(arg1_desc, dict):
            assert arg1_desc.get("description") == "Overridden argument description"
