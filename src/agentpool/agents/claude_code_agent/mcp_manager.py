"""MCP server config container for Claude Code agents.

Holds server configs in Claude SDK format. Unlike the base MCPManager
(which spawns processes), this just stores configs to pass to the SDK.
"""

from typing import TYPE_CHECKING

from agentpool_config.mcp_server import BaseMCPServerConfig


if TYPE_CHECKING:
    from clawd_code_sdk import ClaudeSDKClient, McpServerConfig

    from agentpool_config.mcp_server import MCPServerConfig


class ClaudeCodeMCPManager:
    """Container for MCP server configurations in Claude SDK format."""

    def __init__(self) -> None:
        self._servers: dict[str, McpServerConfig] = {}
        self._client: ClaudeSDKClient | None = None

    def __repr__(self) -> str:
        return f"ClaudeCodeMCPManager(servers={len(self._servers)})"

    @property
    def servers(self) -> dict[str, McpServerConfig]:
        """All configured servers in SDK format."""
        return dict(self._servers)

    def add_server_config(self, cfg: MCPServerConfig | str) -> str:
        """Add a server config. Accepts MCPServerConfig or string shorthand."""
        from agentpool.agents.claude_code_agent.converters import convert_mcp_servers_to_sdk_format

        resolved = BaseMCPServerConfig.from_string(cfg) if isinstance(cfg, str) else cfg
        sdk_configs = convert_mcp_servers_to_sdk_format([resolved])
        self._servers.update(sdk_configs)
        return next(iter(sdk_configs))

    def remove_server(self, name: str) -> bool:
        """Remove a server by name. Returns True if it existed."""
        if name in self._servers:
            del self._servers[name]
            return True
        return False
