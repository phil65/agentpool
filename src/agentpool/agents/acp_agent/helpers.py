"""Helper functions for ACP agent."""

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from collections.abc import Sequence

    from aclientp.schema import AgentCapabilities
    from aclientp.schema.mcp import McpServer
    import structlog


def filter_servers_by_capabilities(
    servers: Sequence[McpServer],
    agent_capabilities: AgentCapabilities | None,
    *,
    logger: structlog.stdlib.BoundLogger | None = None,
) -> list[McpServer]:
    """Filter MCP servers based on agent's supported transports.

    Args:
        servers: ACP-schema MCP servers to filter (not agentpool_config types)
        agent_capabilities: Agent's capabilities (None = no HTTP/SSE support)
        logger: Optional logger for warnings about unsupported servers

    Returns:
        Servers compatible with the agent's capabilities
    """
    from aclientp.schema.mcp import AcpMcpServer, HttpMcpServer, SseMcpServer

    mcp_caps = i.mcp_capabilities if (i := agent_capabilities) else None
    # Check what transports are supported
    supports_http = mcp_caps and mcp_caps.http
    supports_sse = mcp_caps and mcp_caps.sse
    supports_acp = mcp_caps and mcp_caps.acp
    supported_servers: list[McpServer] = []
    unsupported_servers: list[tuple[McpServer, str]] = []

    for server in servers:
        match server:
            case HttpMcpServer() if not supports_http:
                unsupported_servers.append((server, "HTTP"))
            case SseMcpServer() if not supports_sse:
                unsupported_servers.append((server, "SSE"))
            case AcpMcpServer() if not supports_acp:
                unsupported_servers.append((server, "ACP"))
            case _:
                # Stdio servers or supported transport types
                supported_servers.append(server)

    # Log warning if some servers were filtered out
    if unsupported_servers and logger:
        transports = ", ".join(sorted({t for _, t in unsupported_servers}))
        server_names = ", ".join(s.name for s, _ in unsupported_servers)
        logger.warning(
            "Agent does not support some MCP transports, skipping servers",
            unsupported_transports=transports,
            skipped_servers=server_names,
            unsupported_count=len(unsupported_servers),
            supported_http=supports_http,
            supported_sse=supports_sse,
        )

    return supported_servers
