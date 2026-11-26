"""MCP server management for AgentPool."""

import asyncio
from contextlib import AsyncExitStack
from typing import TYPE_CHECKING, Self, cast

import anyio

from agentpool.log import get_logger
from agentpool.resource_providers import AggregatingResourceProvider, ResourceProvider
from agentpool.resource_providers.mcp_provider import MCPResourceProvider
from agentpool_config.mcp_server import BaseMCPServerConfig


if TYPE_CHECKING:
    from collections.abc import Sequence
    from types import TracebackType

    from agentpool_config.mcp_server import MCPServerConfig


logger = get_logger(__name__)


class MCPManager:
    """Manages MCP server connections and distributes resource providers."""

    def __init__(
        self,
        name: str = "mcp",
        owner: str | None = None,
        servers: Sequence[MCPServerConfig | str] | None = None,
    ) -> None:
        self.name = name
        self.owner = owner
        self.servers: list[MCPServerConfig] = []
        for server in servers or []:
            self.add_server_config(server)
        self.providers: list[MCPResourceProvider] = []
        self.aggregating_provider = AggregatingResourceProvider(
            providers=cast(list[ResourceProvider], self.providers),
            name=f"{name}_aggregated",
        )
        self.exit_stack = AsyncExitStack()

    def add_server_config(self, cfg: MCPServerConfig | str) -> None:
        """Add a new MCP server to the manager."""
        resolved = BaseMCPServerConfig.from_string(cfg) if isinstance(cfg, str) else cfg
        self.servers.append(resolved)

    def __repr__(self) -> str:
        return f"MCPManager(name={self.name!r}, servers={len(self.servers)})"

    async def __aenter__(self) -> Self:
        """Connect all enabled servers concurrently.

        A server that fails to connect (or to start within its ``init_timeout``)
        doesn't fail the manager: it is logged and kept with status ``failed``, so
        it can be reconnected later. Cancellation stops every pending connect and
        closes what was connected.
        """
        try:
            async with asyncio.TaskGroup() as group:
                for server in self.servers:
                    group.create_task(self._setup_isolated(server))
        except BaseException:
            await self.cleanup()
            raise
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self.cleanup()

    async def _setup_isolated(self, config: MCPServerConfig) -> None:
        """Set up a server; a failure leaves it registered as ``failed`` instead of raising."""
        if not config.enabled:
            return
        provider = self._create_provider(config)
        try:
            await self.exit_stack.enter_async_context(provider)
        except Exception as e:  # noqa: BLE001
            logger.error(  # noqa: TRY400
                "MCP server failed to start, continuing without it",
                server=config.client_id,
                manager=self.name,
                error=str(e.__cause__ or e),
            )
            # Kept for its status and a later reconnect, which it may also close
            self.exit_stack.push_async_exit(provider.__aexit__)
        self.aggregating_provider.add_provider(provider)

    def _create_provider(self, config: MCPServerConfig) -> MCPResourceProvider:
        return MCPResourceProvider(
            server=config,
            name=f"{self.name}_{config.name or config.client_id}",
            owner=self.owner,
            source="pool" if self.owner == "pool" else "node",
        )

    async def setup_server(
        self, config: MCPServerConfig, *, add_to_config: bool = False
    ) -> MCPResourceProvider | None:
        """Set up a single MCP server resource provider.

        Args:
            config: MCP server configuration
            add_to_config: If True, also add config to self.servers list and
                          raise ValueError if config is disabled

        Returns:
            The provider if created, None if config is disabled (only when add_to_config=False)

        Raises:
            ValueError: If add_to_config=True and config is disabled
            RuntimeError: If the server can't be connected
        """
        if not config.enabled:
            if add_to_config:
                raise ValueError(f"Server config {config.client_id} is disabled")
            return None

        if add_to_config:
            self.add_server_config(config)

        provider = self._create_provider(config)
        provider = await self.exit_stack.enter_async_context(provider)
        self.aggregating_provider.add_provider(provider)
        return provider

    def get_mcp_providers(self) -> list[MCPResourceProvider]:
        """Get all MCP resource providers managed by this manager."""
        return list(self.providers)

    def get_aggregating_provider(self) -> AggregatingResourceProvider:
        """Get the aggregating provider that contains all MCP providers."""
        return self.aggregating_provider

    async def cleanup(self) -> None:
        """Clean up all MCP connections and providers (stops stdio server processes)."""
        try:
            await self.exit_stack.aclose()
        except Exception as e:
            msg = "Error during MCP manager cleanup"
            logger.exception(msg, exc_info=e)
            raise RuntimeError(msg) from e
        finally:
            self.exit_stack = AsyncExitStack()
            self.aggregating_provider.clear_providers()


if __name__ == "__main__":
    from agentpool_config.mcp_server import StdioMCPServerConfig

    cfg = StdioMCPServerConfig(
        command="uv",
        args=["run", "/home/phil65/dev/oss/agentpool/tests/mcp_server/server.py"],
    )

    async def main() -> None:
        manager = MCPManager(servers=[cfg])
        async with manager:
            providers = manager.get_mcp_providers()
            print(f"Found {len(providers)} providers")
            provider = providers[0]
            prompts = await provider.get_prompts()
            print(f"Found prompts: {prompts}")
            # Test static prompt (no arguments)
            static_prompt = next(p for p in prompts if p.name == "static_prompt")
            print(f"\n--- Testing static prompt: {static_prompt} ---")
            components = await static_prompt.get_components()
            assert components, "No prompt components found"
            print(f"Found {len(components)} prompt components:")
            for i, component in enumerate(components):
                comp_type = type(component).__name__
                print(f"  {i + 1}. {comp_type}: {component.content}")

    anyio.run(main)
