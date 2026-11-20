"""Remote ACP agents (Streamable HTTP): configuration and construction."""

from __future__ import annotations

import pytest

from agentpool import AgentsManifest
from agentpool.agents.acp_agent import ACPAgent
from agentpool.models.acp_agents import RemoteACPAgentConfig


MANIFEST = """
agents:
  remote:
    type: acp
    provider: remote
    url: https://agent.test/api/acp
    headers:
      X-API-Key: ${TEST_ACP_KEY}
      X-Client: agentpool
"""


def test_remote_config_from_yaml(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_ACP_KEY", "secret")
    config = AgentsManifest.from_yaml(MANIFEST).agents["remote"]
    assert isinstance(config, RemoteACPAgentConfig)
    assert config.get_url() == "https://agent.test/api/acp"
    assert config.get_command() is None
    assert config.get_headers() == {"X-API-Key": "secret", "X-Client": "agentpool"}


def test_unset_header_variable_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TEST_ACP_KEY", raising=False)
    config = RemoteACPAgentConfig(
        url="https://agent.test/api/acp", headers={"K": "${TEST_ACP_KEY}"}
    )
    with pytest.raises(ValueError, match="unset environment variable TEST_ACP_KEY"):
        config.get_headers()


def test_agent_from_remote_config() -> None:
    config = RemoteACPAgentConfig(name="remote", url="https://agent.test/api/acp")
    agent = ACPAgent.from_config(config)
    assert agent.name == "remote"


def test_exactly_one_source() -> None:
    with pytest.raises(ValueError, match="Exactly one"):
        ACPAgent(command="x", url="https://agent.test/api/acp")
    with pytest.raises(ValueError, match="Exactly one"):
        ACPAgent()
