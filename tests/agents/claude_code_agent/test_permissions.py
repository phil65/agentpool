"""Permission requests of the Claude Code agent, and the mode changes they cause."""

from typing import Any

from clawd_code_sdk import PermissionResultAllow, PermissionResultDeny
from clawd_code_sdk.models.permissions import ModePermissionUpdate, ToolPermissionContext

from agentpool.agents.claude_code_agent import ClaudeCodeAgent
from agentpool.agents.modes import ConfigOptionChanged
from agentpool.tools.decisions import Approve, Deny
from agentpool.ui.mock_provider import MockInputProvider


async def _request(
    agent: ClaudeCodeAgent[Any, Any], tool_name: str, provider: MockInputProvider
) -> PermissionResultAllow | PermissionResultDeny:
    async with agent._tool_bridge.set_run_context(agent.get_context(input_provider=provider)):
        return await agent._on_permission(tool_name, {}, ToolPermissionContext(tool_use_id="t1"))


def _record_changes(agent: ClaudeCodeAgent[Any, Any]) -> list[ConfigOptionChanged]:
    changes: list[ConfigOptionChanged] = []

    async def on_change(change: Any) -> None:
        changes.append(change)

    agent.state_updated.connect(on_change)
    return changes


async def test_approving_a_plan_leaves_plan_mode() -> None:
    agent = ClaudeCodeAgent(name="cc", permission_mode="acceptEdits")
    await agent.set_permission_mode("plan")
    changes = _record_changes(agent)
    provider = MockInputProvider(tool_confirmation=Approve())

    result = await _request(agent, "ExitPlanMode", provider)

    # The user was asked, and the CLI is told to return to the mode before planning
    assert len(provider.calls) == 1
    update = ModePermissionUpdate(type="setMode", mode="acceptEdits", destination="session")
    assert result == PermissionResultAllow(updated_permissions=[update])
    assert agent._permission_mode == "acceptEdits"
    assert changes == [ConfigOptionChanged(config_id="mode", value_id="acceptEdits")]


async def test_rejecting_a_plan_keeps_plan_mode() -> None:
    agent = ClaudeCodeAgent(name="cc", permission_mode="plan")
    changes = _record_changes(agent)

    result = await _request(agent, "ExitPlanMode", MockInputProvider(tool_confirmation=Deny()))

    assert isinstance(result, PermissionResultDeny)
    assert agent._permission_mode == "plan"
    assert changes == []


async def test_entering_plan_mode_is_followed() -> None:
    agent = ClaudeCodeAgent(name="cc")
    changes = _record_changes(agent)

    result = await _request(agent, "EnterPlanMode", MockInputProvider(tool_confirmation=Approve()))

    assert result == PermissionResultAllow()
    assert agent._permission_mode == "plan"
    assert agent._pre_plan_mode == "default"
    assert changes == [ConfigOptionChanged(config_id="mode", value_id="plan")]


async def test_accept_edits_allows_edits_without_asking() -> None:
    agent = ClaudeCodeAgent(name="cc", permission_mode="acceptEdits")
    provider = MockInputProvider(tool_confirmation=Deny())

    assert await _request(agent, "Edit", provider) == PermissionResultAllow()
    assert isinstance(await _request(agent, "Bash", provider), PermissionResultDeny)
    assert len(provider.calls) == 1


async def test_a_denial_tells_the_model_and_a_stop_interrupts() -> None:
    agent = ClaudeCodeAgent(name="cc")
    denied = await _request(agent, "Bash", MockInputProvider(tool_confirmation=Deny(message="no")))
    assert denied == PermissionResultDeny(message="The user declined to run 'Bash': no")
    stopped = await _request(agent, "Bash", MockInputProvider(tool_confirmation=Deny(stop=True)))
    assert isinstance(stopped, PermissionResultDeny)
    assert stopped.interrupt


async def test_edited_arguments_reach_the_cli() -> None:
    agent = ClaudeCodeAgent(name="cc")
    provider = MockInputProvider(tool_confirmation=Approve(args={"command": "ls"}))
    assert await _request(agent, "Bash", provider) == PermissionResultAllow(
        updated_input={"command": "ls"}
    )
