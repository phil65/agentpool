"""Approval requests of the Codex agent follow the user's decision."""

from typing import TYPE_CHECKING

from codexed.models import CommandExecutionRequestApprovalParams
import pytest

from agentpool.agents.codex_agent import CodexAgent
from agentpool.tools.decisions import Approve, Deny
from agentpool.ui.mock_provider import MockInputProvider


if TYPE_CHECKING:
    from agentpool.tools.decisions import ToolDecision


def _params() -> CommandExecutionRequestApprovalParams:
    return CommandExecutionRequestApprovalParams(
        thread_id="t", turn_id="u", item_id="i", command="rm -rf build"
    )


@pytest.mark.parametrize(
    ("confirmation", "decision"),
    [(Approve(), "allow"), (Deny(), "deny"), (Deny(stop=True), "deny")],
)
async def test_command_approval(confirmation: ToolDecision, decision: str) -> None:
    agent = CodexAgent(name="x")
    provider = MockInputProvider(tool_confirmation=confirmation)
    async with agent._tool_bridge.set_run_context(agent.get_context(input_provider=provider)):
        response = await agent._on_approval(_params())
    assert response.decision == decision
    context = provider.calls[0].args[0]
    assert (context.tool_name, context.tool_call_id) == ("command_execution", "i")
    assert context.tool_input["command"] == "rm -rf build"
