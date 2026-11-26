"""Regression tests for manifest and config handling (issues #57, #63.6c)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import yaml

from agentpool import AgentsManifest
from agentpool.models.agents import NativeAgentConfig
from agentpool_config.mcp_server import (
    SSEMCPServerConfig,
    StdioMCPServerConfig,
    StreamableHTTPMCPServerConfig,
)
from agentpool_config.resolution import resolve_config


if TYPE_CHECKING:
    from pathlib import Path


def _manifest() -> AgentsManifest:
    return AgentsManifest.model_validate({"agents": {"a": {"type": "native", "model": "test"}}})


def test_clone_agent_config_applies_overrides():
    manifest = _manifest()
    name = manifest.clone_agent_config("a", "b", description="cloned")
    assert name == "b"
    assert manifest.agents["b"].description == "cloned"
    assert manifest.agents["a"].description is None


def test_clone_agent_config_template_name():
    manifest = _manifest()
    manifest.clone_agent_config("a", "b", template_context={"name": "zzz"})
    assert manifest.agents["b"].name == "zzz"


def test_clone_agent_config_rejects_unknown_override():
    with pytest.raises(ValueError, match="Invalid override"):
        _manifest().clone_agent_config("a", "b", no_such_field=1)


def test_empty_agents_section():
    manifest = AgentsManifest.model_validate(yaml.safe_load("agents:\n"))
    assert manifest.agents == {}


def test_mcp_server_strings_with_urls():
    config = NativeAgentConfig(
        name="x",
        model="test",
        mcp_servers=["https://mcp.example.com/mcp", "http://h/sse", "uvx some-server --flag"],
    )
    http, sse, stdio = config.get_mcp_servers()
    assert isinstance(http, StreamableHTTPMCPServerConfig)
    assert isinstance(sse, SSEMCPServerConfig)
    assert isinstance(stdio, StdioMCPServerConfig)
    assert (stdio.command, stdio.args) == ("uvx", ["some-server", "--flag"])


def test_config_store_creates_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import platformdirs

    from agentpool_cli.store import ConfigStore

    config_dir = tmp_path / "missing" / "agentpool"
    monkeypatch.setattr(platformdirs, "user_config_dir", lambda *_a, **_kw: str(config_dir))
    config = tmp_path / "pool.yml"
    config.write_text("agents: {}\n")
    store = ConfigStore()
    store.add_config("mine", config)
    assert store.config_file.exists()
    assert store.list_configs() == [("mine", str(config.resolve()))]


def test_empty_project_config(tmp_path: Path):
    (tmp_path / "agentpool.yml").write_text("")
    resolved = resolve_config(project_dir=tmp_path, include_global=False)
    assert resolved.data == {}
