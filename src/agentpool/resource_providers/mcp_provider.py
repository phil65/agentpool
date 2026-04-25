"""Tool management for AgentPool."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from typing import TYPE_CHECKING, Any, Self, assert_never

from agentpool.common_types import MCPServerStatus
from agentpool.log import get_logger
from agentpool.resource_providers import ResourceProvider
from agentpool.resource_providers.resource_info import ResourceInfo


if TYPE_CHECKING:
    from collections.abc import Sequence
    from types import TracebackType
    from typing import Literal

    from fastmcp.client.sampling import SamplingHandler
    from mcp.types import ResourceTemplate

    from agentpool.prompts.prompts import MCPClientPrompt
    from agentpool.tools.base import FunctionTool, Tool
    from agentpool_config.mcp_server import MCPServerConfig


logger = get_logger(__name__)


class MCPResourceProvider(ResourceProvider):
    """Resource provider for a single MCP server."""

    kind = "mcp"

    def __init__(
        self,
        server: MCPServerConfig | str,
        name: str = "mcp",
        owner: str | None = None,
        source: Literal["pool", "node"] = "node",
        sampling_callback: SamplingHandler[Any, Any] | None = None,
        accessible_roots: list[str] | None = None,
    ) -> None:
        from agentpool.mcp_server import MCPClient
        from agentpool_config.mcp_server import BaseMCPServerConfig

        super().__init__(name, owner=owner)
        self.server = BaseMCPServerConfig.from_string(server) if isinstance(server, str) else server
        self.source = source
        self.exit_stack = AsyncExitStack()

        self._accessible_roots = accessible_roots
        self._sampling_callback = sampling_callback

        self._saved_enabled_states: dict[str, bool] = {}
        self._tools_cache: list[FunctionTool] | None = None
        self._prompts_cache: list[MCPClientPrompt] | None = None
        self._resources_cache: list[ResourceInfo] | None = None
        self.client = MCPClient(
            config=self.server,
            sampling_callback=self._sampling_callback,
            accessible_roots=self._accessible_roots,
            tool_change_callback=self._on_tools_changed,
            prompt_change_callback=self._on_prompts_changed,
            resource_change_callback=self._on_resources_changed,
        )

    def __repr__(self) -> str:
        return f"MCPResourceProvider({self.server!r}, source={self.source!r})"

    @property
    def transport_type(self) -> Literal["stdio", "http", "sse"]:
        """Return the type of connection used by the MCP server."""
        from agentpool_config import (
            SSEMCPServerConfig,
            StdioMCPServerConfig,
            StreamableHTTPMCPServerConfig,
        )

        match self.client.config:
            case StdioMCPServerConfig():
                return "stdio"
            case StreamableHTTPMCPServerConfig():
                return "http"
            case SSEMCPServerConfig():
                return "sse"
            case _ as unreachable:
                assert_never(unreachable)

    async def __aenter__(self) -> Self:
        try:
            await self.exit_stack.enter_async_context(self.client)
        except Exception as e:
            # Clean up in case of error
            await self.__aexit__(type(e), e, e.__traceback__)
            raise RuntimeError("Failed to initialize MCP manager") from e

        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        try:
            try:
                # Clean up exit stack (which includes MCP clients)
                await self.exit_stack.aclose()
            except RuntimeError as e:
                if "different task" in str(e):
                    # Handle task context mismatch
                    if asyncio.current_task():
                        loop = asyncio.get_running_loop()
                        await loop.create_task(self.exit_stack.aclose())
                else:
                    raise

        except Exception as e:
            msg = "Error during MCP manager cleanup"
            logger.exception(msg, exc_info=e)
            raise RuntimeError(msg) from e

    async def _on_tools_changed(self, _meta: dict[str, Any]) -> None:
        """Callback when tools change on the MCP server."""
        logger.info("MCP tool list changed, refreshing provider cache")
        self._saved_enabled_states = {t.name: t.enabled for t in self._tools_cache or []}
        self._tools_cache = None
        # Notify subscribers via signal
        await self.tools_changed.emit(self.create_change_event("tools"))

    async def _on_prompts_changed(self, _meta: dict[str, Any]) -> None:
        """Callback when prompts change on the MCP server."""
        logger.info("MCP prompt list changed, refreshing provider cache")
        self._prompts_cache = None
        # Notify subscribers via signal
        await self.prompts_changed.emit(self.create_change_event("prompts"))

    async def _on_resources_changed(self, _meta: dict[str, Any]) -> None:
        """Callback when resources change on the MCP server."""
        logger.info("MCP resource list changed, refreshing provider cache")
        self._resources_cache = None
        # Notify subscribers via signal
        await self.resources_changed.emit(self.create_change_event("resources"))

    async def refresh_tools_cache(self) -> None:
        """Refresh the tools cache by fetching from client."""
        all_tools: list[FunctionTool] = []
        try:
            for tool in await self.client.list_tools():
                try:
                    tool_info = self.client.convert_tool(tool)
                    all_tools.append(tool_info)
                except Exception:
                    logger.exception("Failed to create MCP tool", name=tool.name)
                    continue

            # Restore enabled states from saved states
            for tool_info in all_tools:
                if tool_info.name in self._saved_enabled_states:
                    tool_info.enabled = self._saved_enabled_states[tool_info.name]

            self._tools_cache = all_tools
            logger.debug("Refreshed MCP tools cache", num_tools=len(all_tools))
        except Exception:
            logger.exception("Failed to refresh MCP tools cache")
            self._tools_cache = []

    async def get_tools(self) -> Sequence[Tool]:
        """Get cached tools, refreshing if necessary."""
        if self._tools_cache is None:
            await self.refresh_tools_cache()

        return self._tools_cache or []

    async def refresh_prompts_cache(self) -> None:
        """Refresh the prompts cache by fetching from client."""
        from agentpool.prompts.prompts import MCPClientPrompt

        all_prompts: list[MCPClientPrompt] = []
        try:
            for prompt in await self.client.list_prompts():
                try:
                    converted = MCPClientPrompt.from_fastmcp(self.client, prompt)
                    all_prompts.append(converted)
                except Exception:
                    logger.exception("Failed to convert prompt", name=prompt.name)
                    continue

            self._prompts_cache = all_prompts
            logger.debug("Refreshed MCP prompts cache", num_prompts=len(all_prompts))
        except Exception:
            logger.exception("Failed to refresh MCP prompts cache")
            self._prompts_cache = []

    async def get_prompts(self) -> list[MCPClientPrompt]:  # type: ignore
        """Get cached prompts, refreshing if necessary."""
        if self._prompts_cache is None:
            await self.refresh_prompts_cache()

        return self._prompts_cache or []

    async def refresh_resources_cache(self) -> None:
        """Refresh the resources cache by fetching from client."""
        all_resources: list[ResourceInfo] = []
        try:
            for resource in await self.client.list_resources():
                try:
                    converted = await ResourceInfo.from_mcp_resource(
                        resource,
                        client_name=self.name,
                        reader=self.read_resource,
                    )
                    all_resources.append(converted)
                except Exception:
                    logger.exception("Failed to convert resource", name=resource.name)
                    continue

            self._resources_cache = all_resources
            logger.debug("Refreshed MCP resources cache", num_resources=len(all_resources))
        except Exception:
            logger.exception("Failed to refresh MCP resources cache")
            self._resources_cache = []

    async def get_resources(self) -> list[ResourceInfo]:
        """Get cached resources, refreshing if necessary."""
        if self._resources_cache is None:
            await self.refresh_resources_cache()

        return self._resources_cache or []

    async def read_resource(self, uri: str) -> list[str]:
        """Read resource content by URI.

        Args:
            uri: URI of the resource to read

        Returns:
            List of text contents from the resource

        Raises:
            RuntimeError: If resource cannot be read
        """
        from mcp.types import BlobResourceContents, TextResourceContents

        result: list[str] = []
        for content in await self.client.read_resource(uri):
            match content:
                case TextResourceContents(text=text):
                    result.append(text)
                case BlobResourceContents(blob=blob_data):
                    result.append(f"[Binary data: {len(blob_data)} bytes]")
        return result

    async def list_resource_templates(self) -> list[ResourceTemplate]:
        """Get available resource templates from the MCP server.

        Resource templates define URI patterns with placeholders that can be
        expanded into concrete resource URIs. For example:
        - Template: "file:///{path}" with path="config.json"
        - Expands to: "file:///config.json"

        TODO: Decide on integration strategy:
        - Option 1: Templates as separate concept with expand() -> ResourceInfo
        - Option 2: Unified ResourceInfo with is_template flag and read(**kwargs)
        - Option 3: ResourceTemplateInfo class that produces ResourceInfo

        Returns:
            List of ResourceTemplate objects from the server
        """
        try:
            return await self.client.list_resource_templates()
        except Exception:
            logger.exception("Failed to list resource templates")
            return []

    def get_status(self) -> MCPServerStatus:
        """Get connection status for this MCP server.

        Returns:
            Status dict with 'status' key and optionally 'error' key.
            Status can be: 'connected', 'disabled', or 'failed'.
        """
        try:
            if self.client.connected:
                return MCPServerStatus(
                    name=self.name, status="connected", server_type=self.transport_type
                )
        except Exception as e:  # noqa: BLE001
            return MCPServerStatus(
                name=self.name,
                status="failed",
                error=str(e),
                server_type=self.transport_type,
            )
        else:
            return MCPServerStatus(
                name=self.name, status="disabled", server_type=self.transport_type
            )


if __name__ == "__main__":
    import anyio

    cfg = "uv run /home/phil65/dev/oss/agentpool/tests/mcp_server/server.py"

    async def main() -> None:
        manager = MCPResourceProvider(cfg)
        async with manager:
            prompts = await manager.get_prompts()
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

            # Test dynamic prompt (with arguments)
            dynamic_prompt = next(p for p in prompts if p.name == "dynamic_prompt")
            print(f"\n--- Testing dynamic prompt: {dynamic_prompt} ---")
            components = await dynamic_prompt.get_components(
                arguments={"some_arg": "Hello, world!"}
            )
            assert components, "No prompt components found"
            print(f"Found {len(components)} prompt components:")
            for i, component in enumerate(components):
                comp_type = type(component).__name__
                print(f"  {i + 1}. {comp_type}: {component.content}")

    anyio.run(main)
