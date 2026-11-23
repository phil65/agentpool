"""Sessions of one ACP agent: an ACP session each, over one connection."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

from aclientp.schema import (
    AgentMessageChunk,
    AllowedOutcome,
    ConfigOptionUpdate,
    PermissionOption,
    RequestPermissionRequest,
    RequestPermissionResponse,
    SessionNotification,
    TextContentBlock,
    ToolCallUpdate,
)
import pytest

from agentpool.agents.acp_agent import ACPAgent
from agentpool.agents.acp_agent.client_handler import ACPClientHandler
from agentpool.agents.modes import ConfigOptionChanged
from agentpool.sessions import Session
from agentpool.tools.decisions import Approve, Deny
from agentpool.ui.mock_provider import MockInputProvider


if TYPE_CHECKING:
    from agentpool.tools.decisions import ToolDecision


def _agent_with_two_sessions() -> tuple[ACPAgent[None], Session, Session]:
    agent = ACPAgent(command="true", name="nested")
    first, second = Session.create("nested"), Session.create("nested")
    for session, acp_id in ((first, "acp-1"), (second, "acp-2")):
        with agent.use_session(session):
            agent._open_state(acp_id)
    return agent, first, second


def _chunk(acp_id: str, text: str) -> SessionNotification:
    update = AgentMessageChunk(content=TextContentBlock(text=text))
    return SessionNotification(session_id=acp_id, update=update)


async def test_updates_go_to_the_state_of_their_session() -> None:
    agent, first, second = _agent_with_two_sessions()
    handler = ACPClientHandler(agent)
    await handler.session_update(_chunk("acp-2", "for second"))
    await handler.session_update(_chunk("acp-unknown", "dropped"))
    assert not agent._states["acp-1"].has_pending_updates()
    assert agent._states["acp-2"].update_event.is_set()
    assert len(agent._states["acp-2"].updates) == 1
    with agent.use_session(second):
        assert agent._sdk_session_id == "acp-2"
    assert (first.sdk_session_id, second.sdk_session_id) == ("acp-1", "acp-2")


async def test_config_changes_carry_their_session() -> None:
    agent, first, _second = _agent_with_two_sessions()
    changes: list[Any] = []
    agent.state_updated.connect(changes.append)
    handler = ACPClientHandler(agent)
    option = {
        "type": "select",
        "id": "model",
        "name": "Model",
        "currentValue": "b",
        "options": [{"value": "a", "name": "A"}, {"value": "b", "name": "B"}],
    }
    update = ConfigOptionUpdate.model_validate({"configOptions": [option]})
    await handler.session_update(SessionNotification(session_id="acp-1", update=update))
    assert changes == [ConfigOptionChanged("model", "b", session_id=first.session_id)]


def _permission(acp_id: str) -> RequestPermissionRequest:
    return RequestPermissionRequest(
        session_id=acp_id,
        tool_call=ToolCallUpdate(tool_call_id="t1", title="write"),
        options=[PermissionOption(option_id="ok", name="Allow", kind="allow_once")],
    )


async def test_permission_requests_go_to_their_sessions_callback() -> None:
    agent, first, second = _agent_with_two_sessions()
    forwarded = {
        first.session_id: AsyncMock(return_value=RequestPermissionResponse.denied()),
        second.session_id: AsyncMock(return_value=RequestPermissionResponse.denied()),
    }
    for session_id, callback in forwarded.items():
        agent.set_permission_callback(callback, session_id=session_id)
    await ACPClientHandler(agent).request_permission(_permission("acp-2"))
    forwarded[first.session_id].assert_not_awaited()
    forwarded[second.session_id].assert_awaited_once()


async def test_permission_requests_use_their_turns_input_provider() -> None:
    agent, _first, _second = _agent_with_two_sessions()
    allowing, denying = MockInputProvider(), MockInputProvider(tool_confirmation=Deny())
    agent._states["acp-1"].input_provider = denying
    agent._states["acp-2"].input_provider = allowing
    response = await ACPClientHandler(agent).request_permission(_permission("acp-2"))
    assert response.outcome.outcome == "selected"
    assert [c.method for c in allowing.calls] == ["get_tool_confirmation"]
    assert denying.calls == []


_OPTIONS = [
    PermissionOption(option_id=kind, name=kind, kind=kind)
    for kind in ("allow_once", "allow_always", "reject_once", "reject_always")
]


@pytest.mark.parametrize(
    ("decision", "option_id"),
    [
        (Approve(), "allow_once"),
        (Approve(scope="session"), "allow_always"),
        (Deny(), "reject_once"),  # A denial, not a cancelled turn
        (Deny(scope="session"), "reject_always"),
        (Deny(stop=True), None),  # Cancelled
    ],
)
async def test_decisions_pick_the_agents_option_of_their_kind(
    decision: ToolDecision, option_id: str | None
) -> None:
    agent, _first, _second = _agent_with_two_sessions()
    agent._states["acp-2"].input_provider = MockInputProvider(tool_confirmation=decision)
    request = RequestPermissionRequest(
        session_id="acp-2",
        tool_call=ToolCallUpdate(tool_call_id="t1", title="write"),
        options=_OPTIONS,
    )
    response = await ACPClientHandler(agent).request_permission(request)
    match response.outcome:
        case AllowedOutcome(option_id=selected):
            assert selected == option_id
        case _:
            assert option_id is None
