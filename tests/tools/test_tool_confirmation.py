"""The user's decision on a native agent's tool calls (``Approve`` / ``Deny``)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic_ai.messages import ToolReturnPart
from pydantic_ai.models.test import TestModel

from agentpool import Agent, FunctionTool
from agentpool.tools.decisions import Approve, Deny
from agentpool.ui.mock_provider import MockInputProvider


if TYPE_CHECKING:
    from agentpool.messaging import ChatMessage
    from agentpool.tools.decisions import ToolDecision


def _tool(calls: list[str], *, confirm: bool = True) -> FunctionTool:
    def delete_files(pattern: str) -> str:
        """Delete the files matching a pattern."""
        calls.append(pattern)
        return f"deleted {pattern}"

    return FunctionTool.from_callable(delete_files, requires_confirmation=confirm)


def _agent(tool: FunctionTool, decision: ToolDecision) -> tuple[Agent, MockInputProvider]:
    provider = MockInputProvider(tool_confirmation=decision)
    model = TestModel(call_tools=["delete_files"], custom_output_text="done")
    agent = Agent("cleaner", model=model, input_provider=provider)
    agent.tools.register_tool(tool)
    return agent, provider


def _returns(message: ChatMessage[str]) -> list[ToolReturnPart]:
    return [
        part
        for msg in message.to_pydantic_ai()
        for part in msg.parts
        if isinstance(part, ToolReturnPart)
    ]


async def test_approved_call_runs() -> None:
    calls: list[str] = []
    agent, provider = _agent(_tool(calls), Approve())
    result = await agent.run("clean up")
    assert [c.method for c in provider.calls] == ["get_tool_confirmation"]
    assert provider.calls[0].args[0].tool_name == "delete_files"
    assert provider.calls[0].args[0].tool_call_id  # Matches the call the client was shown
    assert calls == ["a"]
    assert result.content == "done"


async def test_a_call_without_confirmation_is_not_asked() -> None:
    calls: list[str] = []
    agent, provider = _agent(_tool(calls, confirm=False), Deny())
    await agent.run("clean up")
    assert provider.calls == []
    assert calls == ["a"]


async def test_denied_call_is_reported_and_the_turn_goes_on() -> None:
    calls: list[str] = []
    agent, _ = _agent(_tool(calls), Deny(message="use the trash instead"))
    result = await agent.run("clean up")
    assert calls == []
    assert result.content == "done"  # The turn went on
    [denied] = _returns(result)
    assert denied.outcome == "denied"
    assert denied.content == "The user declined to run 'delete_files': use the trash instead"


async def test_approved_call_runs_with_the_users_arguments() -> None:
    calls: list[str] = []
    agent, _ = _agent(_tool(calls), Approve(args={"pattern": "*.tmp"}))
    await agent.run("clean up")
    assert calls == ["*.tmp"]


async def test_a_denial_that_stops_ends_the_turn() -> None:
    calls: list[str] = []
    agent, _ = _agent(_tool(calls), Deny(stop=True))
    result = await agent.run("clean up")
    assert calls == []
    assert agent.is_cancelled()
    assert result.content != "done"  # Ended before the model's final answer


async def test_a_decision_for_the_session_answers_later_calls() -> None:
    calls: list[str] = []
    agent, provider = _agent(_tool(calls), Approve(scope="session"))
    async with agent:
        # Not stored: TestModel calls no tool again once the history has its result
        await agent.run("clean up", store_history=False)
        await agent.run("and again", store_history=False)
    assert len(provider.calls) == 1  # Asked once, the session answered the second call
    assert calls == ["a", "a"]
    assert agent.session.overrides.tool_decisions == {"delete_files": "approve"}


async def test_a_pre_tool_hook_denial_is_reported_and_the_turn_goes_on() -> None:
    from agentpool.hooks import AgentHooks, CallableHook, HookResult

    def block(**_: object) -> HookResult:
        return {"decision": "deny", "reason": "not on Fridays"}

    calls: list[str] = []
    hooks = AgentHooks(pre_tool_use=[CallableHook(event="pre_tool_use", fn=block)])
    model = TestModel(call_tools=["delete_files"], custom_output_text="done")
    agent = Agent("cleaner", model=model, hooks=hooks)
    agent.tools.register_tool(_tool(calls, confirm=False))
    result = await agent.run("clean up")
    assert calls == []
    assert result.content == "done"
    [denied] = _returns(result)
    assert denied.outcome == "denied"
    assert "not on Fridays" in str(denied.content)
