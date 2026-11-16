"""Aggregating resource provider."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from agentpool.resource_providers.base import ResourceChangeEvent, ResourceProvider


if TYPE_CHECKING:
    from collections.abc import Sequence

    from pydantic_ai import ModelRequestPart

    from agentpool.prompts.prompts import BasePrompt
    from agentpool.resource_providers.resource_info import ResourceInfo
    from agentpool.tools.base import Tool

ToolMode = Literal["codemode"]

_ = ResourceChangeEvent  # Used at runtime in method signatures


class AggregatingResourceProvider(ResourceProvider):
    """Provider that combines resources from multiple providers.

    Automatically forwards change signals from child providers.
    When a child emits tools_changed, this provider re-emits it.
    """

    kind = "aggregating"

    def __init__(
        self,
        providers: list[ResourceProvider],
        name: str = "aggregating",
        tool_mode: ToolMode | None = None,
    ) -> None:
        """Initialize provider with list of providers to aggregate.

        Args:
            providers: Resource providers to aggregate (stores reference to list)
            name: Name for this provider
            tool_mode: Optional tool execution mode ("codemode" wraps all tools)
        """
        super().__init__(name=name)
        self._providers: list[ResourceProvider] = []
        self.tool_mode = tool_mode
        self._codemode_provider: ResourceProvider | None = None
        # Use property setter to set up signal forwarding
        self.providers = providers

    @property
    def providers(self) -> list[ResourceProvider]:
        """Get the list of child providers."""
        return self._providers

    @providers.setter
    def providers(self, value: list[ResourceProvider]) -> None:
        """Set the list of child providers and set up signal forwarding."""
        # Disconnect from old providers
        for provider in self._providers:
            provider.tools_changed.disconnect(self._forward_tools_changed)
            provider.prompts_changed.disconnect(self._forward_prompts_changed)
            provider.resources_changed.disconnect(self._forward_resources_changed)
            provider.skills_changed.disconnect(self._forward_skills_changed)

        self._providers = value

        # Connect to new providers
        for provider in self._providers:
            provider.tools_changed.connect(self._forward_tools_changed)
            provider.prompts_changed.connect(self._forward_prompts_changed)
            provider.resources_changed.connect(self._forward_resources_changed)
            provider.skills_changed.connect(self._forward_skills_changed)

    async def _forward_tools_changed(self, event: ResourceChangeEvent) -> None:
        """Forward tools_changed signal from child provider."""
        await self.tools_changed.emit(event)

    async def _forward_prompts_changed(self, event: ResourceChangeEvent) -> None:
        """Forward prompts_changed signal from child provider."""
        await self.prompts_changed.emit(event)

    async def _forward_resources_changed(self, event: ResourceChangeEvent) -> None:
        """Forward resources_changed signal from child provider."""
        await self.resources_changed.emit(event)

    async def _forward_skills_changed(self, event: ResourceChangeEvent) -> None:
        """Forward skills_changed signal from child provider."""
        await self.skills_changed.emit(event)

    async def get_tools(self) -> Sequence[Tool]:
        """Get tools from all providers.

        If tool_mode="codemode", wraps all tools in a single Python execution tool.
        """
        from agentpool.resource_providers.codemode.provider import CodeModeResourceProvider
        from agentpool.resource_providers.static import StaticResourceProvider

        # Get all tools from child providers
        all_tools = [t for provider in self.providers for t in await provider.get_tools()]

        # If codemode, wrap all tools in a single codemode tool
        if self.tool_mode == "codemode":
            # Always create fresh static provider with current tools
            static = StaticResourceProvider("codemode_static", tools=all_tools)

            if self._codemode_provider is None:
                self._codemode_provider = CodeModeResourceProvider([static])
            else:
                # Update the providers list on existing codemode provider
                # Type narrowing: we know it's CodeModeResourceProvider at this point
                codemode = self._codemode_provider
                assert isinstance(codemode, CodeModeResourceProvider)
                codemode.providers = [static]

            return list(await self._codemode_provider.get_tools())

        return all_tools

    async def get_prompts(self) -> list[BasePrompt]:
        """Get prompts from all providers."""
        return [p for provider in self.providers for p in await provider.get_prompts()]

    async def get_resources(self) -> list[ResourceInfo]:
        """Get resources from all providers."""
        return [r for provider in self.providers for r in await provider.get_resources()]

    async def get_request_parts(
        self, name: str, arguments: dict[str, str] | None = None
    ) -> list[ModelRequestPart]:
        """Try to get prompt from first provider that has it."""
        for provider in self.providers:
            try:
                return await provider.get_request_parts(name, arguments)
            except KeyError:
                continue

        raise KeyError(f"Prompt {name!r} not found in any provider")
