"""Antigravity links the conversation to the session acting (issue #50.5)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from agentpool.agents.antigravity_agent import AntigravityAgent
from agentpool.sessions import Session


async def test_conversation_is_recorded_under_an_explicit_session() -> None:
    agent = AntigravityAgent(name="agy")
    storage = MagicMock(update_sdk_session_id=AsyncMock())
    agent._storage = storage
    session = Session.create("agy")
    with agent.use_session(session):
        await agent._sync_sdk_session_id("conv-1")
    storage.update_sdk_session_id.assert_awaited_once_with(session.session_id, "conv-1")
