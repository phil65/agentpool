"""serve-mcp takes the manifest's pool_server settings, with the command line's on top."""

from __future__ import annotations

from datetime import timedelta

from agentpool.models.manifest import AgentsManifest
from agentpool_cli.serve_mcp import resolve_server_config


def _manifest() -> AgentsManifest:
    return AgentsManifest.model_validate({
        "pool_server": {
            "type": "mcp",
            "transport": "streamable-http",
            "port": 4000,
            "background_tasks": True,
            "tasks_ttl": "PT2H",
        }
    })


def test_manifest_settings_apply() -> None:
    config = resolve_server_config(_manifest().pool_server)
    assert config.enabled
    assert (config.transport, config.port) == ("streamable-http", 4000)
    assert config.background_tasks
    assert config.tasks_ttl == timedelta(hours=2)


def test_command_line_overrides_the_manifest() -> None:
    config = resolve_server_config(
        _manifest().pool_server, port=5000, background_tasks=False, tasks_ttl=60
    )
    assert (config.transport, config.port) == ("streamable-http", 5000)
    assert not config.background_tasks
    assert config.tasks_ttl == timedelta(seconds=60)


def test_defaults_without_a_pool_server_section() -> None:
    config = resolve_server_config(AgentsManifest().pool_server)
    assert (config.transport, config.host, config.port) == ("stdio", "localhost", 3001)
    assert not config.background_tasks
