"""Tests for agent lifecycle hooks."""

from typing import TYPE_CHECKING, Any

import pytest

from agentpool import Agent
from agentpool.hooks import AgentHooks, CallableHook


if TYPE_CHECKING:
    from agentpool.hooks import HookResult


# Hook state for testing
hook_state: dict[str, list[Any]] = {"calls": [], "results": []}


def reset_hook_state():
    """Reset hook state between tests."""
    hook_state["calls"] = []
    hook_state["results"] = []


# Hook functions


def allow_hook(**kwargs) -> HookResult:
    """Hook that allows the action."""
    hook_state["calls"].append(("allow", kwargs.get("event")))
    return {"decision": "allow"}


def deny_hook(**kwargs) -> HookResult:
    """Hook that denies the action."""
    hook_state["calls"].append(("deny", kwargs.get("event")))
    return {"decision": "deny", "reason": "Denied by test hook"}


def record_result_hook(**kwargs) -> HookResult:
    """Hook that records data passed to it."""
    hook_state["results"].append(kwargs)
    return {"decision": "allow"}


def modify_input_hook(**kwargs) -> HookResult:
    """Hook that modifies tool input."""
    hook_state["calls"].append(("modify", kwargs.get("tool_input")))
    return {"decision": "allow", "modified_input": {"modified": True}}


# Tests for pre_run hooks


async def test_pre_run_hook_allow():
    """Test pre-run hook that allows execution."""
    reset_hook_state()

    hooks = AgentHooks(pre_run=[CallableHook(event="pre_run", fn=allow_hook)])
    agent = Agent(model="test", hooks=hooks)

    async with agent:
        result = await agent.run("Hello")

    assert len(hook_state["calls"]) == 1
    assert hook_state["calls"][0] == ("allow", "pre_run")
    assert result.content is not None  # Test model returns some output


async def test_pre_run_hook_deny():
    """Test pre-run hook that blocks execution."""
    reset_hook_state()

    hooks = AgentHooks(pre_run=[CallableHook(event="pre_run", fn=deny_hook)])
    agent = Agent(model="test", hooks=hooks)

    async with agent:
        with pytest.raises(RuntimeError, match="Run blocked"):
            await agent.run("Hello")

    assert len(hook_state["calls"]) == 1
    assert hook_state["calls"][0] == ("deny", "pre_run")


# Tests for post_run hooks


async def test_post_run_hook():
    """Test post-run hook receives result."""
    reset_hook_state()

    hooks = AgentHooks(post_run=[CallableHook(event="post_run", fn=record_result_hook)])
    agent = Agent(model="test", hooks=hooks)

    async with agent:
        await agent.run("Hello")

    assert len(hook_state["results"]) == 1
    result = hook_state["results"][0]
    assert "Hello" in str(result["prompt"])
    assert result["result"] is not None
    assert result["event"] == "post_run"


# Tests for pre_tool_use hooks


async def test_pre_tool_hook_allow():
    """Test pre-tool hook that allows tool execution."""
    reset_hook_state()

    def simple_tool() -> str:
        """A simple test tool."""
        return "tool_result"

    hooks = AgentHooks(pre_tool_use=[CallableHook(event="pre_tool_use", fn=allow_hook)])
    async with Agent(model="test", hooks=hooks, tools=[simple_tool]) as agent:
        # Just verify the hooks are set up correctly
        assert agent._hook_manager.has_hooks()
        assert agent._hook_manager.agent_hooks
        assert len(agent._hook_manager.agent_hooks.pre_tool_use) == 1


async def test_pre_tool_hook_with_matcher():
    """Test that matcher filters which tools trigger hooks."""
    reset_hook_state()

    # Hook that only matches "other_tool", not our actual tool
    hooks = AgentHooks(
        pre_tool_use=[CallableHook(event="pre_tool_use", fn=deny_hook, matcher="other_tool")]
    )
    async with Agent(model="test", hooks=hooks) as agent:
        # Run should succeed because matcher doesn't match any tool
        result = await agent.run("Hello")
        assert result.content is not None


# Tests for post_tool_use hooks


async def test_post_tool_hook():
    """Test post-tool hook setup."""
    reset_hook_state()
    hooks = AgentHooks(post_tool_use=[CallableHook(event="post_tool_use", fn=record_result_hook)])
    async with Agent(model="test", hooks=hooks) as agent:
        assert agent._hook_manager.has_hooks()
        assert agent._hook_manager.agent_hooks
        assert len(agent._hook_manager.agent_hooks.post_tool_use) == 1


# Tests for AgentHooks class


def test_agent_hooks_has_hooks():
    """Test has_hooks method."""
    empty = AgentHooks()
    assert not empty.has_hooks()
    with_pre_run = AgentHooks(pre_run=[CallableHook(event="pre_run", fn=allow_hook)])
    assert with_pre_run.has_hooks()


def test_agent_hooks_repr():
    """Test AgentHooks string representation."""
    empty = AgentHooks()
    assert repr(empty) == "AgentHooks(empty)"

    with_hooks = AgentHooks(
        pre_run=[CallableHook(event="pre_run", fn=allow_hook)],
        post_tool_use=[CallableHook(event="post_tool_use", fn=allow_hook)],
    )
    assert "pre_run=1" in repr(with_hooks)
    assert "post_tool_use=1" in repr(with_hooks)


# Tests for multiple hooks


async def test_multiple_hooks_all_allow():
    """Test multiple hooks all allowing."""
    reset_hook_state()

    hooks = AgentHooks(
        pre_run=[
            CallableHook(event="pre_run", fn=allow_hook),
            CallableHook(event="pre_run", fn=allow_hook),
        ]
    )
    async with Agent(model="test", hooks=hooks) as agent:
        result = await agent.run("Hello")

    assert len(hook_state["calls"]) == 2
    assert result.content is not None


async def test_multiple_hooks_one_denies():
    """Test that one denying hook blocks execution."""
    reset_hook_state()

    hooks = AgentHooks(
        pre_run=[
            CallableHook(event="pre_run", fn=allow_hook),
            CallableHook(event="pre_run", fn=deny_hook),
        ]
    )
    async with Agent(model="test", hooks=hooks) as agent:
        with pytest.raises(RuntimeError, match="Run blocked"):
            await agent.run("Hello")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
