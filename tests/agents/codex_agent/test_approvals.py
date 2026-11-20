"""Approval requests of the Codex agent follow the user's decision."""

from __future__ import annotations

from typing import TYPE_CHECKING

from codexed.models import CommandExecutionRequestApprovalParams
import pytest

from agentpool.agents.codex_agent import CodexAgent
from agentpool.ui.mock_provider import MockInputProvider


if TYPE_CHECKING:
    from agentpool.agents.context import ConfirmationResult


@pytest.mark.parametrize(
    ("confirmation", "decision"),
    [("allow", "allow"), ("skip", "deny"), ("abort_run", "deny"), ("abort_chain", "deny")],
)
async def test_command_approval(confirmation: ConfirmationResult, decision: str) -> None:
    agent = CodexAgent(name="x")
    provider = MockInputProvider(tool_confirmation=confirmation)
    params = CommandExecutionRequestApprovalParams(
        thread_id="t", turn_id="u", item_id="i", command="rm -rf build"
    )
    async with agent._tool_bridge.set_run_context(agent.get_context(input_provider=provider)):
        response = await agent._on_approval(params)
    assert response.decision == decision
