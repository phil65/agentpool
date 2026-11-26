"""Configured `triggers:` reach the node for every agent type and for teams."""

from typing import Any

import pytest

from agentpool import AgentPool
from agentpool.models.manifest import AgentsManifest


TRIGGER: dict[str, Any] = {"type": "time", "name": "daily", "schedule": "0 9 * * *", "prompt": "Hi"}

AGENT_CONFIGS: dict[str, dict[str, Any]] = {
    "native": {"type": "native", "model": "test"},
    "claude_code": {"type": "claude_code"},
    "acp": {"type": "acp", "provider": "custom", "command": "goose", "args": ["acp"]},
    "agui": {"type": "agui", "endpoint": "http://localhost:8000/agent/run"},
    "antigravity": {"type": "antigravity"},
    "codex": {"type": "codex"},
    "opencode_v2": {"type": "opencode_v2", "url": "http://127.0.0.1:4096"},
    "t3": {"type": "t3", "login": "home", "project": "proj_1"},
}


@pytest.mark.parametrize("agent_type", sorted(AGENT_CONFIGS))
def test_agent_from_config_keeps_triggers(agent_type: str) -> None:
    manifest = AgentsManifest.model_validate({
        "agents": {"node": {**AGENT_CONFIGS[agent_type], "triggers": [TRIGGER]}}
    })
    agent = manifest.agents["node"].get_agent()
    assert [c.name for c in agent.router.triggers.of(agent)] == ["daily"]


@pytest.mark.parametrize("mode", ["parallel", "sequential"])
def test_pool_team_keeps_triggers_and_description(mode: str) -> None:
    manifest = AgentsManifest.model_validate({
        "agents": {"a": AGENT_CONFIGS["native"], "b": AGENT_CONFIGS["native"]},
        "teams": {
            "crew": {
                "mode": mode,
                "members": ["a", "b"],
                "description": "Does things",
                "triggers": [TRIGGER],
            }
        },
    })
    pool = AgentPool(manifest)
    team = pool.nodes["crew"]
    assert [c.name for c in team.router.triggers.of(team)] == ["daily"]
    assert team.description == "Does things"
