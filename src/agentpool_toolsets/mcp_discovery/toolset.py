# mypy: disable-error-code="import-not-found,unused-ignore"
"""MCP Discovery Toolset - dynamic MCP server exploration and tool execution.

This toolset provides a stable interface for agents to discover and use MCP servers
on-demand without needing to preload all tools upfront. This preserves prompt cache
stability while enabling access to the entire MCP ecosystem.

The toolset exposes three main capabilities:
1. search_mcp_servers - Semantic search over 1000+ servers (uses pre-built index)
2. list_mcp_tools - Get tools from a specific server (connects on-demand)
3. call_mcp_tool - Execute a tool on any server (reuses connections)
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import HttpUrl
from pydantic_ai import RunContext  # noqa: TC002

from agentpool.agents.context import AgentContext  # noqa: TC001
from agentpool.log import get_logger
from agentpool.mcp_server.client import MCPClient
from agentpool.mcp_server.registries.official_registry_client import (
    MCPRegistryClient,
    MCPRegistryError,
)
from agentpool.resource_providers import ResourceProvider


if TYPE_CHECKING:
    from collections.abc import Sequence

    from fastmcp.client.sampling import SamplingHandler

    from agentpool.mcp_server.registries.official_registry_client import RegistryServer
    from agentpool.tools.base import Tool
    from agentpool_config.mcp_server import MCPServerConfig


logger = get_logger(__name__)

# Path to the pre-built semantic search index (parquet file loaded into LanceDB at runtime)
PARQUET_PATH = Path(__file__).parent / "data" / "mcp_servers.parquet"


class MCPDiscoveryToolset(ResourceProvider):
    """Toolset for dynamic MCP server discovery and tool execution.

    This toolset allows agents to:
    - Search the MCP registry for servers by keyword
    - List tools available on a specific server
    - Call tools on any server without preloading

    Connections are managed lazily - servers are only connected when needed,
    and connections are kept alive for the session duration.
    """

    def __init__(
        self,
        name: str = "mcp_discovery",
        registry_url: str = "https://registry.modelcontextprotocol.io",
        allowed_servers: list[str] | None = None,
        blocked_servers: list[str] | None = None,
        sampling_callback: SamplingHandler[Any, Any] | None = None,
    ) -> None:
        """Initialize the MCP Discovery toolset.

        Args:
            name: Name for this toolset provider
            registry_url: Base URL for the MCP registry API
            allowed_servers: If set, only these server names can be used
            blocked_servers: Server names that cannot be used
            sampling_callback: Callback for MCP sampling requests
        """
        super().__init__(name=name)
        self._registry_url = registry_url
        self._registry: MCPRegistryClient | None = None
        self._connections: dict[str, MCPClient] = {}
        self._server_cache: dict[str, RegistryServer] = {}
        self._tools_cache: dict[str, list[dict[str, Any]]] = {}
        self._allowed_servers = set(allowed_servers) if allowed_servers else None
        self._blocked_servers = set(blocked_servers) if blocked_servers else set()
        self._sampling_callback = sampling_callback
        self._tools: list[Tool] | None = None
        # Lazy-loaded semantic search components
        self._db: Any = None
        self._table: Any = None
        self._embed_model: Any = None
        self._tmpdir: str | None = None

    def _get_registry(self) -> MCPRegistryClient:
        """Get or create the registry client."""
        if self._registry is None:
            self._registry = MCPRegistryClient(base_url=self._registry_url)
        return self._registry

    def _get_search_index(self) -> Any:
        """Get or create the LanceDB search index from parquet file."""
        if self._table is not None:
            return self._table

        import tempfile

        import lancedb  # type: ignore[import-untyped]
        import pyarrow as pa  # type: ignore[import-untyped]
        import pyarrow.parquet as pq  # type: ignore[import-untyped]

        if not PARQUET_PATH.exists():
            msg = f"MCP registry index not found at {PARQUET_PATH}. Run build_mcp_registry_index.py"
            raise FileNotFoundError(msg)

        # Load parquet
        arrow_table = pq.read_table(PARQUET_PATH)

        # Convert vector column to fixed-size list for LanceDB vector search
        # LanceDB requires fixed-size vectors, not variable-length lists
        vectors = arrow_table.column("vector").to_pylist()
        if vectors:
            vec_dim = len(vectors[0])
            fixed_vectors = pa.FixedSizeListArray.from_arrays(
                pa.array([v for vec in vectors for v in vec], type=pa.float32()),
                list_size=vec_dim,
            )
            # Replace the vector column
            col_idx = arrow_table.schema.get_field_index("vector")
            arrow_table = arrow_table.set_column(col_idx, "vector", fixed_vectors)

        # Use a temp directory for LanceDB (it needs a path but we're loading from parquet)
        if self._db is None:
            self._tmpdir = tempfile.mkdtemp(prefix="mcp_discovery_")
            self._db = lancedb.connect(self._tmpdir)

        self._table = self._db.create_table("servers", arrow_table, mode="overwrite")
        return self._table

    def _get_embed_model(self) -> Any:
        """Get or create the FastEmbed model for query embedding."""
        if self._embed_model is not None:
            return self._embed_model

        from fastembed import TextEmbedding

        self._embed_model = TextEmbedding("BAAI/bge-small-en-v1.5")
        return self._embed_model

    def _is_server_allowed(self, server_name: str) -> bool:
        """Check if a server is allowed to be used."""
        if server_name in self._blocked_servers:
            return False
        if self._allowed_servers is not None:
            return server_name in self._allowed_servers
        return True

    async def _get_server_config(self, server_name: str) -> MCPServerConfig:
        """Get connection config for a server from the registry."""
        from agentpool_config.mcp_server import (
            SSEMCPServerConfig,
            StreamableHTTPMCPServerConfig,
        )

        # Check cache first
        server: RegistryServer
        if server_name in self._server_cache:
            server = self._server_cache[server_name]
        else:
            # Use list_servers and find by name - more reliable than get_server
            registry = self._get_registry()
            all_servers = await registry.list_servers()
            found_server: RegistryServer | None = None
            for s in all_servers:
                if s.name == server_name:
                    found_server = s
                    break
            if found_server is None:
                msg = f"Server {server_name!r} not found in registry"
                raise MCPRegistryError(msg)
            server = found_server
            self._server_cache[server_name] = server

        # Find a usable remote endpoint
        for remote in server.remotes:
            if remote.type == "sse":
                return SSEMCPServerConfig(url=HttpUrl(remote.url))
            if remote.type in ("streamable-http", "http"):
                return StreamableHTTPMCPServerConfig(url=HttpUrl(remote.url))

        msg = f"No supported remote transport for server {server_name!r}"
        raise MCPRegistryError(msg)

    async def _get_connection(self, server_name: str) -> MCPClient:
        """Get or create a connection to a server."""
        if server_name in self._connections:
            client = self._connections[server_name]
            if client.connected:
                return client
            # Connection dropped, remove and reconnect
            del self._connections[server_name]

        config = await self._get_server_config(server_name)
        client = MCPClient(config=config, sampling_callback=self._sampling_callback)
        await client.__aenter__()
        self._connections[server_name] = client
        logger.info("Connected to MCP server", server=server_name)
        return client

    async def _close_connections(self) -> None:
        """Close all active connections."""
        for name, client in list(self._connections.items()):
            try:
                await client.__aexit__(None, None, None)
                logger.debug("Closed connection", server=name)
            except Exception as e:  # noqa: BLE001
                logger.warning("Error closing connection", server=name, error=e)
        self._connections.clear()

    async def get_tools(self) -> Sequence[Tool]:
        """Get the discovery tools."""
        if self._tools is not None:
            return self._tools

        self._tools = [
            self.create_tool(
                self.search_mcp_servers,
                category="search",
                read_only=True,
                idempotent=True,
            ),
            self.create_tool(
                self.list_mcp_tools,
                category="search",
                read_only=True,
                idempotent=True,
            ),
            self.create_tool(
                self.call_mcp_tool,
                category="execute",
                open_world=True,
            ),
        ]
        return self._tools

    async def search_mcp_servers(  # noqa: D417
        self,
        agent_ctx: AgentContext,
        query: str,
        max_results: int = 10,
    ) -> str:
        """Search the MCP registry for servers matching a query.

        Uses semantic search over 1000+ indexed MCP servers. The search understands
        meaning, not just keywords - e.g., "web scraping" finds crawlers too.

        Args:
            query: Search term (e.g., "github issues", "database sql", "file system")
            max_results: Maximum number of results to return

        Returns:
            List of matching servers with names and descriptions
        """
        await agent_ctx.events.tool_call_start(
            title=f"Searching MCP servers: {query}",
            kind="search",
        )

        try:
            # Get embedding for query
            model = self._get_embed_model()
            query_embedding = next(iter(model.embed([query]))).tolist()

            # Search the index
            table = self._get_search_index()
            results = table.search(query_embedding).limit(max_results * 2).to_arrow()

            if len(results) == 0:
                return f"No MCP servers found matching '{query}'"

            # Format results, filtering by allowed/blocked
            lines = [f"Found MCP servers matching '{query}':\n"]
            count = 0
            for i, results in enumerate(results):
                name = results["name"][i].as_py()

                # Filter by allowed/blocked
                if not self._is_server_allowed(name):
                    continue

                desc = results["description"][i].as_py()
                version = results["version"][i].as_py()
                has_remote = results["has_remote"][i].as_py()
                remote_types = results["remote_types"][i].as_py()

                lines.append(f"**{name}** (v{version})")
                lines.append(f"  {desc}")
                if has_remote and remote_types:
                    lines.append(f"  Transports: {remote_types}")
                lines.append("")

                count += 1
                if count >= max_results:
                    break

            if count == 0:
                return f"No MCP servers found matching '{query}'"

            lines[0] = f"Found {count} MCP servers matching '{query}':\n"
            return "\n".join(lines)

        except FileNotFoundError as e:
            return f"Error: {e}"
        except Exception as e:
            logger.exception("Error searching MCP servers")
            return f"Error searching MCP servers: {e}"

    async def list_mcp_tools(  # noqa: D417
        self,
        agent_ctx: AgentContext,
        server_name: str,
    ) -> str:
        """List all tools available on a specific MCP server.

        This connects to the server (if not already connected) and retrieves
        the list of available tools with their descriptions and parameters.

        Args:
            server_name: Name of the MCP server (e.g., "com.github/github")

        Returns:
            List of tools with names, descriptions, and parameter schemas
        """
        await agent_ctx.events.tool_call_start(
            title=f"Listing tools from: {server_name}",
            kind="search",
        )

        if not self._is_server_allowed(server_name):
            return f"Error: Server '{server_name}' is not allowed"

        try:
            # Check tools cache first
            if server_name in self._tools_cache:
                tools_data = self._tools_cache[server_name]
            else:
                client = await self._get_connection(server_name)
                mcp_tools = await client.list_tools()

                # Convert to serializable format and cache
                tools_data = []
                for tool in mcp_tools:
                    tool_info: dict[str, Any] = {
                        "name": tool.name,
                        "description": tool.description or "No description",
                    }
                    # Include parameter info
                    if tool.inputSchema:
                        props = tool.inputSchema.get("properties", {})
                        required = set(tool.inputSchema.get("required", []))
                        params = []
                        for pname, pschema in props.items():
                            param_str = pname
                            if pname in required:
                                param_str += " (required)"
                            if "type" in pschema:
                                param_str += f": {pschema['type']}"
                            if "description" in pschema:
                                param_str += f" - {pschema['description']}"
                            params.append(param_str)
                        if params:
                            tool_info["parameters"] = params
                    tools_data.append(tool_info)

                self._tools_cache[server_name] = tools_data

            if not tools_data:
                return f"No tools found on server '{server_name}'"

            # Format output
            lines = [f"Tools available on **{server_name}** ({len(tools_data)} tools):\n"]
            for tool_info in tools_data:
                lines.append(f"### {tool_info['name']}")
                lines.append(f"{tool_info['description']}")
                if "parameters" in tool_info:
                    lines.append("Parameters:")
                    lines.extend(f"  - {param}" for param in tool_info["parameters"])
                lines.append("")

            return "\n".join(lines)

        except MCPRegistryError as e:
            return f"Error: {e}"
        except Exception as e:
            logger.exception("Error listing MCP tools", server=server_name)
            return f"Error listing tools from '{server_name}': {e}"

    async def call_mcp_tool(  # noqa: D417
        self,
        ctx: RunContext,
        agent_ctx: AgentContext,
        server_name: str,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
    ) -> str | Any:
        """Call a tool on an MCP server.

        Use this to execute a specific tool on an MCP server. The server
        connection is reused if already established.

        This properly supports progress reporting, elicitation, and sampling
        through the AgentContext integration.

        Args:
            server_name: Name of the MCP server (e.g., "com.github/github")
            tool_name: Name of the tool to call
            arguments: Arguments to pass to the tool

        Returns:
            The result from the tool execution
        """
        await agent_ctx.events.tool_call_start(
            title=f"Calling {tool_name} on {server_name}",
            kind="execute",
        )

        if not self._is_server_allowed(server_name):
            return f"Error: Server '{server_name}' is not allowed"

        try:
            client = await self._get_connection(server_name)

            # Use MCPClient.call_tool which handles progress, elicitation, and sampling
            return await client.call_tool(
                name=tool_name,
                run_context=ctx,
                arguments=arguments or {},
                agent_ctx=agent_ctx,
            )

            # Result is already processed by MCPClient (ToolReturn, str, or structured data)

        except MCPRegistryError as e:
            return f"Error: {e}"
        except Exception as e:
            logger.exception("Error calling MCP tool", server=server_name, tool=tool_name)
            return f"Error calling '{tool_name}' on '{server_name}': {e}"

    async def cleanup(self) -> None:
        """Clean up resources."""
        await self._close_connections()
        if self._registry:
            await self._registry.close()
            self._registry = None
        # Clean up temp directory used for LanceDB
        if self._tmpdir:
            import shutil

            shutil.rmtree(self._tmpdir, ignore_errors=True)
            self._tmpdir = None
        self._db = None
        self._table = None


if __name__ == "__main__":
    import asyncio

    from agentpool.agents.native_agent import Agent

    async def main() -> None:
        """End-to-end example: Add MCP discovery toolset to an agent and call a tool."""
        # Create the discovery toolset
        toolset = MCPDiscoveryToolset(
            name="mcp_discovery",
            # Optionally restrict to specific servers for safety
            # allowed_servers=["@modelcontextprotocol/server-everything"],
        )

        # Create an AgentPool agent with the toolset
        agent = Agent(
            model="openai:gpt-4o",
            system_prompt="""
            You are a helpful assistant with access to MCP servers.
            Use the MCP discovery tools to search for and use tools from the MCP ecosystem.
            """,
            toolsets=[toolset],
        )

        async with agent:
            # Example 1: Search for servers
            print("\n=== Example 1: Search for file system servers ===")
            result = await agent.run(
                "Search for MCP servers related to file systems and show me the top 3 results"
            )
            print(result.data)

            # Example 2: List tools from a specific server
            print("\n=== Example 2: List tools from a server ===")
            result = await agent.run(
                "List the tools available on the '@modelcontextprotocol/server-everything' server"
            )
            print(result.data)

            # Example 3: Call a tool (using a safe, read-only tool for demo)
            print("\n=== Example 3: Call a tool ===")
            result = await agent.run(
                """Use the MCP discovery to call the 'echo' tool from
                '@modelcontextprotocol/server-everything' with the argument
                message='Hello from MCP Discovery!'"""
            )
            print(result.data)

    asyncio.run(main())
