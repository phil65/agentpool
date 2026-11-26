"""Tool management for AgentPool."""

import asyncio
from collections.abc import Callable, Sequence
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Literal, assert_never

from agentpool.log import get_logger
from agentpool.resource_providers import StaticResourceProvider
from agentpool.utils.baseregistry import AgentPoolError


if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from agentpool.common_types import MCPServerStatus, ToolType
    from agentpool.prompts.prompts import MCPClientPrompt
    from agentpool.resource_providers import ResourceProvider
    from agentpool.resource_providers.codemode.provider import CodeModeResourceProvider
    from agentpool.resource_providers.mcp_provider import MCPResourceProvider
    from agentpool.resource_providers.resource_info import ResourceInfo
    from agentpool.tools.base import Tool


logger = get_logger(__name__)

MAX_LEN_DESCRIPTION = 2000
ToolState = Literal["all", "enabled", "disabled"]
ProviderName = str
OwnerType = Literal["pool", "team", "node"]
ToolMode = Literal["codemode"]


class ToolError(AgentPoolError):
    """Base exception for tool-related errors."""


class ToolManager:
    """Manages tool registration, enabling/disabling and access."""

    def __init__(
        self,
        tools: Sequence[ToolType] | None = None,
        tool_mode: ToolMode | None = None,
    ) -> None:
        """Initialize tool manager.

        Args:
            tools: Initial tools to register
            tool_mode: Tool execution mode (None or "codemode")
        """
        from agentpool.resource_providers.codemode.provider import CodeModeResourceProvider

        super().__init__()
        self.external_providers: list[ResourceProvider] = []
        self.worker_provider = StaticResourceProvider(name="workers")
        self.builtin_provider = StaticResourceProvider(name="builtin")
        self.tool_mode = tool_mode
        self._codemode_provider: CodeModeResourceProvider = CodeModeResourceProvider([])
        # Forward to provider methods
        self.tool = self.builtin_provider.tool
        self.register_tool = self.builtin_provider.register_tool
        self.register_worker = self.worker_provider.register_worker
        for tool in tools or []:
            self.builtin_provider.add_tool(tool)

    @property
    def scoped_external_providers(self) -> list[ResourceProvider]:
        """External providers plus those of the session acting in this context.

        Agents are shared by sessions: a session's own resources (its client's MCP
        servers, its workspace skills) only show up in its own turns.
        """
        from agentpool.sessions.environment import current_session_providers

        return [*self.external_providers, *current_session_providers()]

    @property
    def providers(self) -> list[ResourceProvider]:
        """Get all providers: external (+ the acting session's) + worker + builtin providers."""
        external = self.scoped_external_providers
        if self.tool_mode == "codemode":
            # Update the providers list with current providers
            self._codemode_provider.providers[:] = [
                *external,
                self.worker_provider,
                self.builtin_provider,
            ]
            return [self._codemode_provider]

        return [*external, self.worker_provider, self.builtin_provider]

    async def __prompt__(self) -> str:
        enabled_tools = [t.name for t in await self.get_tools() if t.enabled]
        if not enabled_tools:
            return "No tools available"
        return f"Available tools: {', '.join(enabled_tools)}"

    def add_provider(self, provider: ResourceProvider, owner: str | None = None) -> None:
        """Add an external resource provider.

        Args:
            provider: ResourceProvider instance (e.g., MCP server, custom provider)
            owner: Optional owner for the provider
        """
        if owner:
            provider.owner = owner
        self.external_providers.append(provider)

    def remove_provider(self, provider: ResourceProvider | ProviderName) -> None:
        """Remove an external resource provider."""
        from agentpool.resource_providers import ResourceProvider

        match provider:
            case ResourceProvider():
                self.external_providers.remove(provider)
            case str():
                for p in self.external_providers:
                    if p.name == provider:
                        self.external_providers.remove(p)
            case _ as unreachable:
                assert_never(unreachable)

    async def reset_states(self) -> None:
        """Reset all tools to their default enabled states."""
        for info in await self.get_tools():
            info.enabled = True

    async def enable_tool(self, tool_name: str) -> None:
        """Enable a previously disabled tool."""
        tool_info = await self.get_tool(tool_name)
        tool_info.enabled = True
        logger.debug("Enabled tool", tool_name=tool_name)

    async def disable_tool(self, tool_name: str) -> None:
        """Disable a tool."""
        tool_info = await self.get_tool(tool_name)
        tool_info.enabled = False
        logger.debug("Disabled tool", tool_name=tool_name)

    async def get_tools(
        self,
        state: ToolState = "all",
        names: str | list[str] | None = None,
    ) -> list[Tool]:
        """Get tool objects based on filters."""
        tools: list[Tool] = []
        # Get tools from providers concurrently
        provider_coroutines = [provider.get_tools() for provider in self.providers]
        results = await asyncio.gather(*provider_coroutines, return_exceptions=True)
        for provider, result in zip(self.providers, results, strict=False):
            if isinstance(result, BaseException):
                msg = "Failed to get tools from provider"
                logger.warning(msg, provider=provider, result=result)
                continue
            tools.extend(t for t in result if t.matches_filter(state))

        match names:
            case str():
                tools = [t for t in tools if t.name == names]
            case list():
                tools = [t for t in tools if t.name in names]
        return tools

    async def get_tool(self, name: str) -> Tool:
        """Get a specific tool by name.

        First checks local tools, then uses concurrent provider fetching.
        """
        tool = next((tool for tool in await self.get_tools() if tool.name == name), None)
        if not tool:
            raise ToolError(f"Tool not found: {tool}")
        return tool

    async def list_prompts(self) -> list[MCPClientPrompt]:
        """Get all prompts from all providers."""
        from agentpool.mcp_server.manager import MCPManager
        from agentpool.prompts.prompts import MCPClientPrompt as MCPPrompt
        from agentpool.resource_providers import AggregatingResourceProvider
        from agentpool.resource_providers.mcp_provider import MCPResourceProvider

        all_prompts: list[MCPClientPrompt] = []
        # Get prompts from all external providers (check if they're MCP providers)
        for provider in self.scoped_external_providers:
            if isinstance(provider, MCPResourceProvider):  # A session's client server
                try:
                    all_prompts.extend(await provider.get_prompts())
                except Exception:
                    logger.exception("Failed to get prompts from provider", provider=provider)
            elif isinstance(provider, MCPManager):
                agg_provider = provider.get_aggregating_provider()
                try:
                    prompts = await agg_provider.get_prompts()
                    mcp_prompts = [p for p in prompts if isinstance(p, MCPPrompt)]
                    all_prompts.extend(mcp_prompts)
                except Exception:
                    logger.exception("Failed to get prompts from provider", provider=provider)
            elif isinstance(provider, AggregatingResourceProvider):
                try:
                    # AggregatingResourceProvider can directly provide prompts
                    prompts = await provider.get_prompts()
                    # Filter to only MCPClientPrompt instances
                    mcp_prompts = [p for p in prompts if isinstance(p, MCPPrompt)]
                    all_prompts.extend(mcp_prompts)
                except Exception:
                    logger.exception("Failed to get prompts from provider", provider=provider)

        return all_prompts

    async def list_resources(self) -> list[ResourceInfo]:
        """Get all resources from all providers.

        Returns:
            List of ResourceInfo objects from all providers
        """
        all_resources: list[ResourceInfo] = []
        # Get resources from all providers concurrently
        provider_coroutines = [provider.get_resources() for provider in self.providers]
        results = await asyncio.gather(*provider_coroutines, return_exceptions=True)

        for provider, result in zip(self.providers, results, strict=False):
            if isinstance(result, BaseException):
                logger.warning(
                    "Failed to get resources from provider",
                    provider=provider.name,
                    error=str(result),
                )
                continue
            all_resources.extend(result)

        return all_resources

    async def get_resource(self, name: str) -> ResourceInfo:
        """Get a specific resource by name or URI.

        Args:
            name: Name or URI of the resource to find

        Returns:
            ResourceInfo for the requested resource

        Raises:
            ToolError: If resource not found
        """
        resource = next(
            (r for r in await self.list_resources() if name in (r.name, r.uri)),
            None,
        )
        if not resource:
            raise ToolError(f"Resource not found: {name}")
        return resource

    @asynccontextmanager
    async def temporary_tools(
        self,
        tools: ToolType | Sequence[ToolType],
        *,
        exclusive: bool = False,
    ) -> AsyncGenerator[list[Tool]]:
        """Temporarily register tools.

        Args:
            tools: Tool(s) to register
            exclusive: Whether to temporarily disable all other tools

        Yields:
            List of registered tool infos
        """
        from agentpool.tools.base import Tool

        match tools:
            case str() | Callable() | Tool():
                tools_list = [tools]
            case Sequence():
                tools_list = list(tools)
            case _ as unreachable:
                assert_never(unreachable)
        # Store original tool states if exclusive
        tools = await self.get_tools()
        original_states: dict[str, bool] = {}
        if exclusive:
            original_states = {t.name: t.enabled for t in tools}
            # Disable all existing tools
            for t in tools:
                t.enabled = False
        # Register all tools
        registered_tools: list[Tool] = []
        try:
            for tool in tools_list:
                tool_info = self.register_tool(tool)
                registered_tools.append(tool_info)
            yield registered_tools

        finally:
            # Remove temporary tools
            for tool_info in registered_tools:
                self.builtin_provider.remove_tool(tool_info.name)
            # Restore original tool states if exclusive
            if exclusive:
                for name_, was_enabled in original_states.items():
                    t_ = await self.get_tool(name_)
                    t_.enabled = was_enabled

    def mcp_providers(self) -> dict[str, MCPResourceProvider]:
        """The MCP servers this manager's tools come from, by provider name."""
        from agentpool.mcp_server.manager import MCPManager
        from agentpool.resource_providers import AggregatingResourceProvider
        from agentpool.resource_providers.mcp_provider import MCPResourceProvider

        found: list[MCPResourceProvider] = []
        for provider in self.external_providers:
            match provider:
                case MCPResourceProvider():
                    found.append(provider)
                case AggregatingResourceProvider():
                    found.extend(
                        p for p in provider.providers if isinstance(p, MCPResourceProvider)
                    )
                case MCPManager():
                    found.extend(provider.get_mcp_providers())
        return {provider.name: provider for provider in found}

    async def get_mcp_server_info(self) -> dict[str, MCPServerStatus]:
        """Get information about configured MCP servers."""
        return {name: p.get_status() for name, p in self.mcp_providers().items()}


if __name__ == "__main__":
    manager = ToolManager()

    @manager.tool(name="custom_name", description="Custom description")
    def with_params(query: str) -> str:
        """With parameters."""
        return query.upper()
