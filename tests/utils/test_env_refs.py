"""``${VAR}`` expansion in configuration values (HTTP headers of remote agents)."""

from __future__ import annotations

import pytest

from agentpool.models.agui_agents import AGUIAgentConfig
from agentpool.utils.env_refs import expand_env_refs


def test_expand_env_refs() -> None:
    env = {"KEY": "secret"}
    headers = {"X-API-Key": "${KEY}", "Authorization": "Bearer $KEY", "Price": "$$5"}
    assert expand_env_refs(headers, env) == {
        "X-API-Key": "secret",
        "Authorization": "Bearer secret",
        "Price": "$5",
    }
    with pytest.raises(ValueError, match="'X' references unset environment variable MISSING"):
        expand_env_refs({"X": "${MISSING}"}, env)


def test_agui_headers_expand_env_refs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_AGUI_TOKEN", "abc")
    config = AGUIAgentConfig(
        endpoint="http://localhost:8000/agent/run",
        headers={"Authorization": "Bearer ${TEST_AGUI_TOKEN}"},
    )
    assert config.get_headers() == {"Authorization": "Bearer abc"}
