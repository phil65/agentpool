"""Filtering resource provider implementation."""

from typing import TYPE_CHECKING, Any, Self

from agentpool.resource_providers import ResourceProvider


if TYPE_CHECKING:
    from collections.abc import Sequence
    from types import TracebackType

    from pydantic_ai import ModelRequestPart

    from agentpool.agents.base_agent import BaseAgent
    from agentpool.prompts.prompts import BasePrompt
    from agentpool.resource_providers.base import ResourceChangeEvent
    from agentpool.resource_providers.resource_info import ResourceInfo
    from agentpool.skills.skill import Skill
    from agentpool.tools.base import Tool


class FilteringResourceProvider(ResourceProvider):
    """Wrapper that filters tools from a ResourceProvider based on tool name filter.

    Everything except the tool list is delegated to the wrapped provider: its
    lifecycle, prompts, resources, skills and change signals.
    """

    def __init__(self, provider: ResourceProvider, tool_filter: dict[str, bool]) -> None:
        """Initialize filtering wrapper.

        Args:
            provider: The provider to wrap
            tool_filter: Dict mapping tool names to enabled state (defaults to True)
        """
        super().__init__(name=provider.name, owner=provider.owner)
        self.kind = provider.kind
        self._provider = provider
        self._tool_filter = tool_filter
        provider.tools_changed.connect(self._forward_tools_changed)
        provider.prompts_changed.connect(self._forward_prompts_changed)
        provider.resources_changed.connect(self._forward_resources_changed)
        provider.skills_changed.connect(self._forward_skills_changed)

    def __getattr__(self, name: str) -> Any:
        """Delegate other attribute access to the wrapped provider."""
        if name == "_provider":  # not set yet
            raise AttributeError(name)
        return getattr(self._provider, name)

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self._provider!r})"

    def attach_agent(self, agent: BaseAgent[Any, Any]) -> None:
        self._provider.attach_agent(agent)

    async def __aenter__(self) -> Self:
        await self._provider.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self._provider.__aexit__(exc_type, exc_val, exc_tb)

    async def _forward_tools_changed(self, event: ResourceChangeEvent) -> None:
        await self.tools_changed.emit(event)

    async def _forward_prompts_changed(self, event: ResourceChangeEvent) -> None:
        await self.prompts_changed.emit(event)

    async def _forward_resources_changed(self, event: ResourceChangeEvent) -> None:
        await self.resources_changed.emit(event)

    async def _forward_skills_changed(self, event: ResourceChangeEvent) -> None:
        await self.skills_changed.emit(event)

    async def get_tools(self) -> Sequence[Tool]:
        """Get filtered tools from wrapped provider.

        Returns only tools where the filter value is True. Tools not in the filter
        default to enabled (True).
        """
        tools = await self._provider.get_tools()
        return [t for t in tools if self._tool_filter.get(t.name, True)]

    async def get_prompts(self) -> list[BasePrompt]:
        return await self._provider.get_prompts()

    async def get_resources(self) -> list[ResourceInfo]:
        return await self._provider.get_resources()

    async def get_skills(self) -> list[Skill]:
        return await self._provider.get_skills()

    async def get_skill_instructions(self, skill_name: str) -> str:
        return await self._provider.get_skill_instructions(skill_name)

    async def get_request_parts(
        self, name: str, arguments: dict[str, str] | None = None
    ) -> list[ModelRequestPart]:
        return await self._provider.get_request_parts(name, arguments)
