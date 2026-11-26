"""Native agents act in the session of the running turn (issue #59.1b, #59.2c-e, #59.4e)."""

from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from pydantic import BaseModel
from pydantic_ai.messages import PartDeltaEvent, PartStartEvent, ToolCallPart, ToolCallPartDelta
import pytest

from agentpool import Agent
from agentpool.agents.events.reconstructor import MessageReconstructor
from agentpool.hooks import AgentHooks, CallableHook
from agentpool.messaging import ChatMessage
from agentpool.sessions import Session


pytestmark = pytest.mark.timeout(30)


async def test_tool_hooks_get_the_turns_session_id() -> None:
    seen: list[tuple[str, str | None]] = []

    def record(**kwargs: Any) -> Any:
        seen.append((kwargs["event"], kwargs.get("session_id")))
        return {"decision": "allow"}

    def simple_tool() -> str:
        """A simple tool."""
        return "result"

    hooks = AgentHooks(
        pre_tool_use=[CallableHook(event="pre_tool_use", fn=record)],
        post_tool_use=[CallableHook(event="post_tool_use", fn=record)],
    )
    session = Session.create("a")
    async with Agent(name="a", model="test", hooks=hooks, tools=[simple_tool]) as agent:
        await agent.run("hi", session=session)
    assert seen
    assert {session_id for _, session_id in seen} == {session.session_id}


async def test_agent_tool_forks_the_calling_turns_session(monkeypatch: pytest.MonkeyPatch) -> None:
    parent = Agent(name="parent", model="test")
    child = Agent(name="child", model="test")
    sessions: list[Session] = []

    async def run(prompt: str, *, session: Session) -> Any:
        sessions.append(session)
        return MagicMock(data="ok")

    monkeypatch.setattr(child, "run", run)
    tool = child.to_tool(parent=parent, reset_history_on_run=True)
    explicit = Session.create("parent")
    with parent.use_session(explicit):
        await tool.fn("question")
    assert sessions[0].data.parent_id == explicit.session_id


class _Answer(BaseModel):
    value: int


async def test_temporary_state_restores_the_output_type_when_setup_fails() -> None:
    agent = Agent(name="a", model="test")
    original = agent._output_type
    with pytest.raises(Exception):  # noqa: B017, PT011  # The model doesn't resolve
        async with agent.temporary_state(output_type=_Answer, model="bogusprovider:none"):
            pass
    assert agent._output_type is original


async def test_load_session_reads_history_from_the_history_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = Agent(name="a", model="test")
    message = ChatMessage(content="hi", role="user", session_id="s1")
    storage = MagicMock()
    storage.load_session = AsyncMock(return_value=MagicMock())
    storage.providers = [MagicMock(can_load_history=False), MagicMock(can_load_history=True)]
    storage.get_session_messages = AsyncMock(return_value=[message])
    monkeypatch.setattr(type(agent), "storage", property(lambda self: storage))
    assert await agent.load_session("s1") is not None
    assert agent.conversation.chat_messages == [message]


async def test_session_cost_survives_its_runtime() -> None:
    session = Session.create("a")
    agent = Agent(name="a", model="test")
    runtime = agent.runtime(session)
    session.cost += Decimal("0.5")
    agent._runtimes.pop(session.session_id, None)  # Dropped when idle
    assert agent.runtime(session) is not runtime
    assert agent.runtime(session).session.cost == Decimal("0.5")


@pytest.mark.parametrize(
    ("start", "delta", "expected"),
    [
        (None, '{"a": 1}', '{"a": 1}'),
        ("", '{"a": 1}', '{"a": 1}'),
        ({}, {"a": 1}, {"a": 1}),
        ({"a": 1}, {"b": 2}, {"a": 1, "b": 2}),
        ('{"a": ', "1}", '{"a": 1}'),
    ],
)
def test_tool_call_deltas_of_any_shape_merge(start: Any, delta: Any, expected: Any) -> None:
    reconstructor = MessageReconstructor()
    part = ToolCallPart(tool_name="t", args=start, tool_call_id="c1")
    reconstructor.observe(PartStartEvent(index=0, part=part))
    reconstructor.observe(
        PartDeltaEvent(index=0, delta=ToolCallPartDelta(args_delta=delta, tool_call_id="c1"))
    )
    [merged] = [p for p in reconstructor.current_response_parts if isinstance(p, ToolCallPart)]
    assert merged.args == expected
