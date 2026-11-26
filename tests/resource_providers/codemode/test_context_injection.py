"""Integration tests for context injection in code execution environment."""

import re

from pydantic_ai import RunContext  # noqa: TC002
import pytest

from agentpool import AgentContext  # noqa: TC001
from agentpool.resource_providers import StaticResourceProvider
from agentpool.resource_providers.codemode.provider import CodeModeResourceProvider
from agentpool.tools.base import FunctionTool


async def tool_with_run_context(ctx: RunContext[None], message: str) -> str:
    """Tool that requires RunContext to function."""
    return f"RunContext tool received: {message}"


async def tool_with_agent_context(ctx: AgentContext, message: str) -> str:
    """Tool that requires AgentContext to function."""
    return f"AgentContext tool received: {message} (agent: {ctx.node_name})"


def simple_tool(x: int) -> str:
    """Simple tool without context requirements."""
    return f"Simple result: {x}"


async def dual_context_tool(run_ctx: RunContext[None], agent_ctx: AgentContext, value: int) -> str:
    """Tool that requires both RunContext and AgentContext."""
    return f"Dual context tool: {value}"


async def test_direct_context_call_fails():
    """Test that calling context-dependent tools directly fails."""
    # Create tool that needs RunContext
    tool = FunctionTool.from_callable(tool_with_run_context)
    # Create provider with the context-dependent tool
    static_provider = StaticResourceProvider(tools=[tool])
    code_provider = CodeModeResourceProvider([static_provider])
    # Get the execution namespace directly
    generator = await code_provider._get_code_generator()
    namespace = generator.generate_execution_namespace()
    # Try to call the tool directly without context - this should fail
    tool_func = namespace["tool_with_run_context"]
    # This should fail because we're not providing RunContext (schemez >= 2.5 raises)
    with pytest.raises(TypeError, match="missing 1 required positional argument"):
        await tool_func("test message")


async def test_simple_tool_works():
    """Test that simple tools without context work fine."""
    # Create tool without context requirements
    tool = FunctionTool.from_callable(simple_tool)
    # Create provider
    static_provider = StaticResourceProvider(tools=[tool])
    code_provider = CodeModeResourceProvider([static_provider])
    # Get the execution namespace
    generator = await code_provider._get_code_generator()
    namespace = generator.generate_execution_namespace()
    # Call the simple tool - this should work
    tool_func = namespace["simple_tool"]
    result = await tool_func(42)
    # Should work fine
    assert result == "Simple result: 42"


async def test_context_signature_hiding():
    """Test that context parameters are hidden from user-visible signatures."""
    # Create tool with context
    tool = FunctionTool.from_callable(tool_with_run_context)
    static_provider = StaticResourceProvider(tools=[tool])
    code_provider = CodeModeResourceProvider([static_provider])
    # Get tool description (what user sees)
    tools = await code_provider.get_tools()
    assert len(tools) == 1
    description = tools[0].description
    # The description should show the function signature but without RunContext param
    # User should see: tool_with_run_context(message: str) -> str
    # NOT: tool_with_run_context(ctx: RunContext[None], message: str) -> str
    assert "message: str" in description
    # Check that the RunContext param is specifically hidden from the signature
    # (RunContext may still appear in return types or docstrings, but not as a param)
    assert "ctx: RunContext" not in description
    assert "(ctx:" not in description
    # Verify the signature shows only the message parameter
    # Find the function signature line
    signature_match = re.search(r"async def tool_with_run_context\([^)]*\)", description)
    assert signature_match is not None, "Could not find function signature"
    signature = signature_match.group(0)
    assert "message:" in signature
    assert "ctx:" not in signature


async def test_agent_context_signature_hiding():
    """Test that AgentContext parameters are hidden from user-visible signatures."""
    # Create tool with AgentContext
    tool = FunctionTool.from_callable(tool_with_agent_context)
    static_provider = StaticResourceProvider(tools=[tool])
    code_provider = CodeModeResourceProvider([static_provider])
    # Get tool description (what user sees)
    tools = await code_provider.get_tools()
    assert len(tools) == 1
    description = tools[0].description
    # The description should show the function sig but without AgentContext param
    # User should see: tool_with_agent_context(message: str) -> str
    # NOT: tool_with_agent_context(ctx: AgentContext, message: str) -> str
    assert "message: str" in description
    # Check that the AgentContext parameter is specifically hidden from the signature
    assert "ctx: AgentContext" not in description
    assert "(ctx:" not in description
    # Verify the signature shows only the message parameter
    # Find the function signature line
    signature_match = re.search(r"async def tool_with_agent_context\([^)]*\)", description)
    assert signature_match is not None, "Could not find function signature"
    signature = signature_match.group(0)
    assert "message:" in signature
    assert "ctx:" not in signature


async def test_dual_context_signature_hiding():
    """Test that both RunContext and AgentContext parameters are hidden from tools."""
    # Create tool with both contexts
    tool = FunctionTool.from_callable(dual_context_tool)
    static_provider = StaticResourceProvider(tools=[tool])
    code_provider = CodeModeResourceProvider([static_provider])
    # Get tool description (what user sees)
    tools = await code_provider.get_tools()
    assert len(tools) == 1
    description = tools[0].description
    # The description should show the function signature with only the business param
    # User should see: dual_context_tool(value: int) -> str
    # NOT: dual_context_tool(run_ctx: RunContext[None], agent_ctx:
    # AgentContext, value: int) -> str
    assert "value: int" in description
    # Check that both context parameters are hidden from the signature
    assert "run_ctx: RunContext" not in description
    assert "agent_ctx: AgentContext" not in description
    assert "(run_ctx:" not in description
    assert "(agent_ctx:" not in description
    # Verify the signature shows only the value parameter
    # Find the function signature line
    signature_match = re.search(r"async def dual_context_tool\([^)]*\)", description)
    assert signature_match is not None, "Could not find function signature"
    signature = signature_match.group(0)
    assert "value:" in signature
    assert "run_ctx:" not in signature
    assert "agent_ctx:" not in signature


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
