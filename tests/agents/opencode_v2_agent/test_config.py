"""Tests for OpenCodeV2AgentConfig and manifest integration."""

from typing import TYPE_CHECKING

from agentpool.agents.opencode_v2_agent import OpenCodeV2Agent
from agentpool.models.manifest import AgentsManifest
from agentpool.models.opencode_v2_agents import (
    OpenCodePermissionRuleConfig,
    OpenCodeV2AgentConfig,
)


if TYPE_CHECKING:
    import pytest


def test_manifest_loads_opencode_v2_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OC_PW", "top-secret")
    monkeypatch.setenv("OC_HDR", "custom-value")
    monkeypatch.setenv("OC_ENV", "1")
    manifest = AgentsManifest.model_validate({
        "agents": {
            "oc": {
                "type": "opencode_v2",
                "url": "http://127.0.0.1:4096",
                "password": "${OC_PW}",
                "headers": {"X-Custom": "${OC_HDR}"},
                "env_vars": {"DEBUG": "${OC_ENV}"},
                "agent": "build",
                "model": "anthropic/claude-sonnet-4-5",
                "variant": "high",
                "permission_mode": "ask",
                "permissions": [{"action": "bash", "resource": "*", "effect": "ask"}],
            }
        }
    })
    config = manifest.agents["oc"]
    assert isinstance(config, OpenCodeV2AgentConfig)
    assert config.get_password() == "top-secret"
    assert config.get_headers() == {"X-Custom": "custom-value"}
    assert config.get_env_vars() == {"DEBUG": "1"}
    assert config.permissions == [
        OpenCodePermissionRuleConfig(action="bash", resource="*", effect="ask")
    ]

    agent = config.get_agent()
    assert isinstance(agent, OpenCodeV2Agent)
    assert agent.AGENT_TYPE == "opencode_v2"
    assert agent.model_name == "anthropic/claude-sonnet-4-5"
    assert agent._opencode_agent == "build"
    assert agent._variant == "high"
    assert agent._permission_mode == "ask"
