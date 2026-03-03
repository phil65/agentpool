from __future__ import annotations

from pydantic_ai import RunContext
from pydantic_ai.models.test import TestModel
import pytest

from agentpool import Agent, AgentContext, AgentPool
from agentpool_config.toolsets import SubagentToolsetConfig


async def run_ctx_tool(ctx: RunContext) -> str:
    """Tool expecting RunContext."""
    assert isinstance(ctx, RunContext)
    return "RunContext tool"


async def agent_ctx_tool(ctx: AgentContext) -> str:
    """Tool expecting AgentContext."""
    assert isinstance(ctx, AgentContext)
    return "AgentContext tool"


async def data_with_run_ctx(ctx: RunContext[AgentContext[dict[str, str]]]) -> str:
    """Tool accessing data through RunContext."""
    return f"Data from RunContext: {ctx.deps.data}"


async def data_with_agent_ctx(ctx: AgentContext) -> str:
    """Tool accessing data through AgentContext."""
    return f"Data from AgentContext: {ctx.data}"


async def no_ctx_tool() -> str:
    """Tool without any context."""
    return "No context tool"


async def dual_ctx_tool(run_ctx: RunContext, agent_ctx: AgentContext) -> str:
    """Tool expecting both RunContext and AgentContext."""
    assert isinstance(run_ctx, RunContext)
    assert isinstance(agent_ctx, AgentContext)
    return f"Dual context tool (agent: {agent_ctx.node_name})"


async def test_tool_context_injection():
    """Test that tools receive correct context."""
    context_received = None
    deps_received = None

    async def test_tool(ctx: RunContext[AgentContext]) -> str:
        """Test tool that captures its context."""
        nonlocal context_received, deps_received
        context_received = ctx
        deps_received = ctx.deps
        return "Called"

    async with Agent(model=TestModel(call_tools=["test_tool"]), deps_type=bool) as agent:
        # Register our test tool
        agent.tools.register_tool(test_tool, enabled=True)
        # Run agent which should trigger tool
        await agent.run("Test", deps=True)
        assert context_received is not None, "Tool did not receive context"
        assert isinstance(context_received, RunContext), "Wrong context type"

        # Verify dependencies
        assert deps_received is not None, "Tool did not receive dependencies"


async def test_plain_tool_no_context():
    """Test that plain tools work without context."""
    count = 0

    async def plain_tool() -> str:
        """Tool without context parameter."""
        nonlocal count
        count += 1
        return "Got arg"

    async with Agent(model=TestModel(call_tools=["plain_tool"])) as agent:
        agent.tools.register_tool(plain_tool, enabled=True)
        # Should work without error
        await agent.run("Test")
        assert count == 1


@pytest.mark.integration
@pytest.mark.flaky(reruns=2)
async def test_capability_tools(default_model: str):
    """Test that capability tools work with AgentContext."""
    async with AgentPool() as pool:
        subagent = SubagentToolsetConfig()
        providers = [subagent.get_provider()]
        agent = Agent(name="test", model=default_model, toolsets=providers)
        await pool.add_agent(agent)
        prompt = "Get available agents using the list_available_nodes tool and return all names."
        result = await agent.run(prompt)
        assert agent.name in str(result.content)
        agent_2 = Agent(name="test_2", model=default_model, toolsets=providers)
        await pool.add_agent(agent_2)
        agent_3 = Agent(name="helper", system_prompt="You help with tasks", model=default_model)
        await pool.add_agent(agent_3)
        result = await agent_2.run("Execute task 'say hello' on agent with name `helper`")
        assert result.get_tool_calls()
        assert result.get_tool_calls()[0].tool_name == "task"


@pytest.mark.flaky(reruns=2)
async def test_team_creation(default_model: str):
    """Test that an agent can create other agents and form them into a team via commands."""
    # default_model = "openrouter:anthropic/claude-haiku-4.5"
    async with AgentPool() as pool:
        # Create creator agent with agent_cli tool (provides run_command-like functionality)
        from agentpool_config.agentpool_tools import AgentCliToolConfig

        tools = [AgentCliToolConfig()]
        tool_instances = [config.get_tool() for config in tools]
        creator = Agent(name="creator", model=default_model, tools=tool_instances)
        await pool.add_agent(creator)
        # Ask it to create agents and form a team
        result = await creator.run("""
            Use the run_agent_cli_command tool to:
            1. Create two agents named "alice" and "bob"
            2. Then create a team called "crew" with those agents
        """)

        # Debug
        print(f"Tool calls: {result.get_tool_calls()}")
        print(f"Content: {result.content}")

        # Verify agents were created
        assert "alice" in pool.get_agents()
        assert "bob" in pool.get_agents()
        assert "crew" in pool.teams
        # Verify team creation message
        assert "alice" in str(result.content.lower())
        assert "bob" in str(result.content.lower())


async def test_context_compatibility():
    """Test that both context types work in tools."""
    model = TestModel(call_tools=["run_ctx_tool", "agent_ctx_tool", "no_ctx_tool"])
    async with Agent(model=model) as agent:
        agent.tools.register_tool(run_ctx_tool)
        agent.tools.register_tool(agent_ctx_tool)
        agent.tools.register_tool(no_ctx_tool)

        # All should work
        result = await agent.run("Test")
        assert any(call.result == "RunContext tool" for call in result.get_tool_calls())
        assert any(call.result == "AgentContext tool" for call in result.get_tool_calls())
        assert any(call.result == "No context tool" for call in result.get_tool_calls())


async def test_context_sharing():
    """Test that both context types access same data."""
    shared_data = {"key": "value"}
    model = TestModel(call_tools=["data_with_run_ctx", "data_with_agent_ctx"])
    agent = Agent[dict[str, str]](name="test", model=model, deps_type=dict)
    agent.tools.register_tool(data_with_run_ctx)
    agent.tools.register_tool(data_with_agent_ctx)

    async with agent:
        result = await agent.run("Test", deps=shared_data)

        assert any(
            call.result == "Data from RunContext: {'key': 'value'}"
            for call in result.get_tool_calls()
        )
        assert any(
            call.result == "Data from AgentContext: {'key': 'value'}"
            for call in result.get_tool_calls()
        )


async def test_dual_context_tool():
    """Test tool that requires both RunContext and AgentContext."""
    async with Agent(model=TestModel(call_tools=["dual_ctx_tool"]), name="dual-agent") as agent:
        agent.tools.register_tool(dual_ctx_tool)
        # This should work if dual context injection is implemented
        result = await agent.run("Test")
        # Should successfully call the tool with both contexts
        tool_calls = result.get_tool_calls()
        assert len(tool_calls) > 0
        expected_result = "Dual context tool (agent: dual-agent)"
        assert any(call.result == expected_result for call in tool_calls)


if __name__ == "__main__":
    pytest.main([__file__, "-vv", "--log-level", "debug"])
