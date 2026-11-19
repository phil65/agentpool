"""MCP server management for AgentPool."""

from __future__ import annotations

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
        try:
            if tasks := [self.setup_server(server) for server in self.servers]:
                await asyncio.gather(*tasks)
        except Exception as e:
            await self.__aexit__(type(e), e, e.__traceback__)
            raise RuntimeError("Failed to initialize MCP manager") from e

        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self.cleanup()

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
        """
        if not config.enabled:
            if add_to_config:
                raise ValueError(f"Server config {config.client_id} is disabled")
            return None

        if add_to_config:
            self.add_server_config(config)

        provider = MCPResourceProvider(
            server=config,
            name=f"{self.name}_{config.client_id}",
            owner=self.owner,
            source="pool" if self.owner == "pool" else "node",
        )
        provider = await self.exit_stack.enter_async_context(provider)
        self.providers.append(provider)
        return provider

    def get_mcp_providers(self) -> list[MCPResourceProvider]:
        """Get all MCP resource providers managed by this manager."""
        return list(self.providers)

    def get_aggregating_provider(self) -> AggregatingResourceProvider:
        """Get the aggregating provider that contains all MCP providers."""
        return self.aggregating_provider

    async def cleanup(self) -> None:
        """Clean up all MCP connections and providers."""
        try:
            try:
                # Clean up exit stack (which includes MCP providers)
                await self.exit_stack.aclose()
            except RuntimeError as e:
                if "different task" in str(e):
                    # Handle task context mismatch
                    if asyncio.current_task():
                        loop = asyncio.get_running_loop()
                        await loop.create_task(self.exit_stack.aclose())
                else:
                    raise

            self.providers.clear()

        except Exception as e:
            msg = "Error during MCP manager cleanup"
            logger.exception(msg, exc_info=e)
            raise RuntimeError(msg) from e


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
