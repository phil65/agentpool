"""AG-UI agents: explicit sessions and local tool round-trips (issue #50.4)."""

import json
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import httpx2
import pytest

from agentpool.agents.agui_agent import AGUIAgent
from agentpool.sessions import Session
from agentpool.tools import FunctionTool


if TYPE_CHECKING:
    from collections.abc import AsyncIterator


pytestmark = pytest.mark.timeout(30)


def _sse(*events: dict[str, Any]) -> list[str]:
    return [f"data: {json.dumps(e)}\n\n" for e in events]


def _text(message_id: str, text: str) -> list[str]:
    return _sse(
        {"type": "TEXT_MESSAGE_START", "messageId": message_id, "role": "assistant"},
        {"type": "TEXT_MESSAGE_CONTENT", "messageId": message_id, "delta": text},
        {"type": "TEXT_MESSAGE_END", "messageId": message_id},
    )


class _Server:
    """A stateless AG-UI endpoint: answers each request with the next scripted reply."""

    def __init__(self, *replies: list[str]) -> None:
        self.replies = list(replies)
        self.requests: list[dict[str, Any]] = []

    def stream(self, method: str, url: str, *, json: dict[str, Any]) -> Any:
        self.requests.append(json)
        events = self.replies.pop(0)
        response = AsyncMock(spec=httpx2.Response)
        response.raise_for_status = MagicMock()

        async def aiter_text() -> AsyncIterator[str]:
            for event in events:
                yield event

        response.aiter_text = aiter_text
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=response)
        context.__aexit__ = AsyncMock(return_value=None)
        return context


def _agent(server: _Server) -> AGUIAgent:
    agent = AGUIAgent(endpoint="http://agui.test/run", name="remote")
    agent._client = server  # type: ignore[assignment]  # ty:ignore[invalid-assignment]
    return agent


async def test_turn_of_an_explicit_session_on_a_fresh_agent() -> None:
    server = _Server(_text("m1", "hi"))
    agent = _agent(server)
    session = Session.create("remote")
    message = await agent.run("hello", session=session)
    assert message.content == "hi"
    assert message.session_id == session.session_id
    assert server.requests[0]["threadId"] == session.session_id


async def test_tool_round_trip_sends_the_whole_conversation() -> None:
    def lookup(city: str) -> str:
        """Look up the weather."""
        return f"sunny in {city}"

    call = {"type": "TOOL_CALL_START", "toolCallId": "tc1", "toolCallName": "lookup"}
    args = {"type": "TOOL_CALL_ARGS", "toolCallId": "tc1", "delta": '{"city": "Rome"}'}
    end = {"type": "TOOL_CALL_END", "toolCallId": "tc1"}
    server = _Server(_sse(call, args, end), _text("m2", "It is sunny."))
    agent = _agent(server)
    agent.tools.register_tool(FunctionTool.from_callable(lookup))
    message = await agent.run("weather in Rome?")
    assert message.content == "It is sunny."
    second = server.requests[1]["messages"]
    assert [m["role"] for m in second] == ["user", "assistant", "tool"]
    assert second[0]["content"] == [{"type": "text", "text": "weather in Rome?"}]
    [tool_call] = second[1]["toolCalls"]
    assert tool_call["id"] == "tc1"
    assert json.loads(tool_call["function"]["arguments"]) == {"city": "Rome"}
    assert second[2]["toolCallId"] == "tc1"
    assert second[2]["content"] == "sunny in Rome"
