"""ACP agents: boolean config options and a failed start (issue #50.3)."""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock

from aclientp.schema import BooleanSessionConfigOption
import pytest

from agentpool.agents.acp_agent import ACPAgent


pytestmark = pytest.mark.timeout(60)


def _agent_with_boolean_option() -> tuple[ACPAgent, AsyncMock]:
    agent = ACPAgent(command="true")
    api = AsyncMock()
    api.set_session_config_option.return_value = None
    agent._api = api
    state = agent._open_state("remote")
    state.config_options = [
        BooleanSessionConfigOption(id="auto_approve", name="Auto approve", current_value=False)
    ]
    return agent, api


async def test_boolean_option_is_offered_as_a_mode_category() -> None:
    agent, _ = _agent_with_boolean_option()
    [category] = await agent.get_modes()
    assert category.id == "auto_approve"
    assert [m.value for m in category.available_modes] == ["true", "false"]
    assert category.current_mode_id == "false"


@pytest.mark.parametrize("value", [True, "true"])
async def test_boolean_option_is_set_as_a_boolean(value: str | bool) -> None:
    agent, api = _agent_with_boolean_option()
    await agent.set_mode(value, category_id="auto_approve")
    api.set_session_config_option.assert_awaited_once_with("remote", "auto_approve", True)


async def test_a_failed_start_stops_the_process() -> None:
    agent = ACPAgent(command=sys.executable, args=["-c", "pass"])  # Exits before initialize
    with pytest.raises(RuntimeError):
        await agent.__aenter__()
    assert agent._process is None
    assert agent._connection is None
