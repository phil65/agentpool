"""Base resource provider interface."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Self

from anyenv.signals import Signal

from agentpool.log import get_logger
from agentpool.tools.base import FunctionTool
from agentpool_config.tools import ToolHints


if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from types import TracebackType

    from pydantic_ai import ModelRequestPart
    from schemez import OpenAIFunctionDefinition

    from agentpool.agents.base_agent import BaseAgent
    from agentpool.prompts.prompts import BasePrompt
    from agentpool.resource_providers.resource_info import ResourceInfo
    from agentpool.skills.skill import Skill
    from agentpool.tools.base import Tool, ToolKind


logger = get_logger(__name__)


ResourceType = Literal["tools", "prompts", "resources", "skills"]
ProviderKind = Literal[
    "base", "mcp", "mcp_run", "tools", "prompts", "skills", "aggregating", "custom"
]


@dataclass(frozen=True, slots=True)
class ResourceChangeEvent:
    """Event emitted when resources change in a provider.

    Attributes:
        provider_name: Name of the provider instance
        provider_kind: Kind/type of the provider (e.g., "mcp", "tools")
        resource_type: Type of resource that changed
        owner: Optional owner of the provider (e.g., agent name)
    """

    provider_name: str
    provider_kind: ProviderKind
    resource_type: ResourceType
    owner: str | None = None


class ResourceProvider:
    """Base class for resource providers.

    Provides tools, prompts, and other resources to agents.
    Default implementations return empty lists - override as needed.

    Class Attributes:
        kind: Short slug identifying the provider type (e.g., "mcp", "tools")

    Change signals (using anyenv.signals.Signal):
        - tools_changed: Emitted when tools change
        - prompts_changed: Emitted when prompts change
        - resources_changed: Emitted when resources change
        - skills_changed: Emitted when skills change

    Example:
        provider.tools_changed.connect(my_handler)
        await provider.tools_changed.emit(provider.create_change_event("tools"))
    """

    kind: ProviderKind = "base"

    # Change signals - emit ResourceChangeEvent when resources change
    tools_changed: Signal[ResourceChangeEvent] = Signal()
    prompts_changed: Signal[ResourceChangeEvent] = Signal()
    resources_changed: Signal[ResourceChangeEvent] = Signal()
    skills_changed: Signal[ResourceChangeEvent] = Signal()

    def __init__(self, name: str, owner: str | None = None) -> None:
        """Initialize the resource provider."""
        self.name = name
        self.owner = owner
        self.log = logger.bind(name=self.name, owner=self.owner)

    def create_change_event(self, resource_type: ResourceType) -> ResourceChangeEvent:
        """Create a ResourceChangeEvent for this provider."""
        return ResourceChangeEvent(
            provider_name=self.name,
            provider_kind=self.kind,
            resource_type=resource_type,
            owner=self.owner,
        )

    def attach_agent(self, agent: BaseAgent[Any, Any]) -> None:
        """Called by the agent this provider was configured for (default: nothing).

        For providers that act on their own, outside a tool call (e.g. scheduled jobs).
        """

    async def __aenter__(self) -> Self:
        """Async context entry if required."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Async context cleanup if required."""

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name!r})"

    async def get_tools(self) -> Sequence[Tool]:
        """Get available tools. Override to provide tools."""
        return []

    async def get_tool(self, tool_name: str) -> Tool:
        """Get specific tool."""
        tools = await self.get_tools()
        for tool in tools:
            if tool.name == tool_name:
                return tool

        raise ValueError(f"Tool {tool_name!r} not found")

    async def get_prompts(self) -> list[BasePrompt]:
        """Get available prompts. Override to provide prompts."""
        return []

    async def get_resources(self) -> list[ResourceInfo]:
        """Get available resources. Override to provide resources."""
        return []

    async def get_skills(self) -> list[Skill]:
        """Get available skills. Override to provide skills."""
        return []

    async def get_skill_instructions(self, skill_name: str) -> str:
        """Get full instructions for a specific skill.

        Args:
            skill_name: Name of the skill to get instructions for

        Returns:
            The full skill instructions for execution

        Raises:
            KeyError: If skill not found
        """
        raise KeyError(f"Skill {skill_name!r} not found")

    async def get_request_parts(
        self, name: str, arguments: dict[str, str] | None = None
    ) -> list[ModelRequestPart]:
        """Get a prompt formatted with arguments.

        Args:
            name: Name of the prompt to format
            arguments: Optional arguments for prompt formatting

        Returns:
            Single chat message with merged content

        Raises:
            KeyError: If prompt not found
            ValueError: If formatting fails
        """
        prompts = await self.get_prompts()
        prompt = next((p for p in prompts if p.name == name), None)
        if not prompt:
            raise KeyError(f"Prompt {name!r} not found")

        messages = await prompt.format(arguments or {})
        if not messages:
            raise ValueError(f"Prompt {name!r} produced no messages")

        return [p for prompt_msg in messages for p in prompt_msg.to_pydantic_parts()]

    def create_tool(
        self,
        fn: Callable[..., Any],
        read_only: bool | None = None,
        destructive: bool | None = None,
        idempotent: bool | None = None,
        open_world: bool | None = None,
        requires_confirmation: bool = False,
        metadata: dict[str, Any] | None = None,
        category: ToolKind | None = None,
        name_override: str | None = None,
        description_override: str | None = None,
        schema_override: OpenAIFunctionDefinition | None = None,
    ) -> FunctionTool:
        """Create a tool from a function.

        Args:
            fn: Function to create a tool from
            read_only: Whether the tool is read-only
            destructive: Whether the tool is destructive
            idempotent: Whether the tool is idempotent
            open_world: Whether the tool is open-world
            requires_confirmation: Whether the tool requires confirmation
            metadata: Metadata for the tool
            category: Category of the tool
            name_override: Override the name of the tool
            description_override: Override the description of the tool
            schema_override: Override the schema of the tool

        Returns:
            Tool created from the function
        """
        return FunctionTool.from_callable(
            fn=fn,
            category=category,
            source=self.name,
            requires_confirmation=requires_confirmation,
            meta=metadata,
            name_override=name_override,
            description_override=description_override,
            schema_override=schema_override,
            hints=ToolHints(
                read_only=read_only,
                destructive=destructive,
                idempotent=idempotent,
                open_world=open_world,
            ),
        )
