"""Functions to discover available MCP servers from PulseMCP."""

from typing import Any, Literal

import anyenv
from pydantic import Field
from schemez import Schema


class Remote(Schema):
    """Information about a remote MCP server endpoint."""

    url_direct: str | None = None
    url_setup: str | None = None
    transport: Literal["streamable_http", "sse"] | None = None
    authentication_method: str | None = None
    cost: str | None = None


class ServerInfo(Schema):
    """Information about an MCP server."""

    name: str
    url: str
    external_url: str | None = None
    short_description: str
    source_code_url: str | None = None
    github_stars: int | None = Field(default=None)
    package_registry: str | None = None
    package_name: str | None = None
    package_download_count: int | None = Field(default=None)
    ai_generated_description: str | None = Field(
        default=None, alias="EXPERIMENTAL_ai_generated_description"
    )
    remotes: list[Remote] = Field(default_factory=list)


class ServerListResponse(Schema):
    """Response from the MCP server list endpoint."""

    servers: list[ServerInfo]
    next: str | None = None
    total_count: int


async def get_mcp_servers(
    query: str | None = None,
    count_per_page: int | None = None,
    offset: int | None = None,
) -> list[ServerInfo]:
    """Fetch all available MCP servers.

    Args:
        query: Optional query string to filter the results.
        count_per_page: Optional number of results to return per page.
        offset: Optional offset for pagination.

    Returns:
        List of ServerInfo objects representing available MCP servers.

    Raises:
        HTTPError: If the API request fails.
    """
    params: dict[str, Any] = {}
    if query:
        params["query"] = query
    if count_per_page is not None:
        params["count_per_page"] = count_per_page
    if offset is not None:
        params["offset"] = offset
    result = await anyenv.get_json(
        "https://api.pulsemcp.com/v0beta/servers",
        headers={"User-Agent": "MCPToolDiscovery/1.0"},
        params=params,
        return_type=ServerListResponse,
    )
    return result.servers


if __name__ == "__main__":
    import asyncio

    servers = asyncio.run(get_mcp_servers(count_per_page=100))
    print(f"Found {len(servers)} MCP servers")
    for server in servers:
        print(server)
