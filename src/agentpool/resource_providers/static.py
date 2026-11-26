"""Static resource provider implementation."""

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, assert_never, overload

from agentpool.resource_providers import ResourceProvider
from agentpool.tools.base import FunctionTool, Tool


if TYPE_CHECKING:
    from collections.abc import Sequence
    from typing import Any

    from agentpool import Agent, MessageNode
    from agentpool.common_types import ToolSource, ToolType
    from agentpool.prompts.prompts import BasePrompt
    from agentpool.resource_providers.resource_info import ResourceInfo


class StaticResourceProvider(ResourceProvider):
    """Provider for pre-configured tools, prompts and resources.

    Allows creating a provider that serves a fixed set of resources
    passed during initialization. Useful for converting static configurations
    to the common ResourceProvider interface.
    """

    kind = "tools"

    def __init__(
        self,
        name: str = "static",
        tools: Sequence[Tool] | None = None,
        prompts: Sequence[BasePrompt] | None = None,
        resources: Sequence[ResourceInfo] | None = None,
    ) -> None:
        """Initialize provider with static resources.

        Args:
            name: Name of the provider
            tools: Optional list of tools to serve
            prompts: Optional list of prompts to serve
            resources: Optional list of resources to serve
        """
        super().__init__(name=name)
        self._tools = list(tools) if tools else []
        self._prompts = list(prompts) if prompts else []
        self._resources = list(resources) if resources else []

    async def get_tools(self) -> Sequence[Tool]:
        """Get pre-configured tools."""
        return self._tools

    async def get_prompts(self) -> list[BasePrompt]:
        """Get pre-configured prompts."""
        return self._prompts

    async def get_resources(self) -> list[ResourceInfo]:
        """Get pre-configured resources."""
        return self._resources

    def add_tool(self, tool: ToolType) -> None:
        """Add a tool to this provider."""
        match tool:
            case Tool():
                instance = tool
            case Callable() | str():
                instance = FunctionTool.from_callable(tool)
            case _ as unreachable:
                assert_never(unreachable)
        self._tools.append(instance)

    def remove_tool(self, name: str) -> bool:
        """Remove a tool by name.

        Args:
            name: Name of tool to remove

        Returns:
            True if tool was found and removed, False otherwise
        """
        for i, tool in enumerate(self._tools):
            if tool.name == name:
                self._tools.pop(i)
                return True
        return False

    def add_prompt(self, prompt: BasePrompt) -> None:
        """Add a prompt to this provider."""
        self._prompts.append(prompt)

    def remove_prompt(self, name: str) -> bool:
        """Remove a prompt by name."""
        for i, prompt in enumerate(self._prompts):
            if prompt.name == name:
                self._prompts.pop(i)
                return True
        return False

    def add_resource(self, resource: ResourceInfo) -> None:
        """Add a resource to this provider."""
        self._resources.append(resource)

    def remove_resource(self, name: str) -> bool:
        """Remove a resource by name."""
        for i, resource in enumerate(self._resources):
            if resource.name == name:
                self._resources.pop(i)
                return True
        return False

    def register_tool(
        self,
        tool: ToolType,
        *,
        name_override: str | None = None,
        description_override: str | None = None,
        enabled: bool = True,
        source: ToolSource = "dynamic",
        requires_confirmation: bool = False,
        metadata: dict[str, str] | None = None,
    ) -> Tool:
        """Register a new tool with custom settings.

        Args:
            tool: Tool to register (callable, Tool instance, or import path)
            name_override: Optional name override for the tool
            description_override: Optional description override for the tool
            enabled: Whether tool is initially enabled
            source: Tool source (runtime/agent/builtin/dynamic)
            requires_confirmation: Whether tool needs confirmation
            metadata: Additional tool metadata

        Returns:
            Created Tool instance
        """
        match tool:
            case Tool(description=description, meta=meta, name=name):
                tool.description = description_override or description
                tool.name = name_override or name
                tool.source = source
                tool.meta = meta | (metadata or {})
                tool.enabled = enabled

            case str() | Callable():
                tool = FunctionTool.from_callable(
                    tool,
                    enabled=enabled,
                    source=source,
                    name_override=name_override,
                    description_override=description_override,
                    requires_confirmation=requires_confirmation,
                    meta=metadata or {},
                )
            case _ as unreachable:
                assert_never(unreachable)

        self.add_tool(tool)
        return tool

    def register_worker(
        self,
        worker: MessageNode[Any, Any],
        *,
        name: str | None = None,
        reset_history_on_run: bool = True,
        pass_message_history: bool = False,
        parent: Agent[Any, Any] | None = None,
    ) -> Tool:
        """Register an agent as a worker tool.

        Args:
            worker: Agent to register as worker
            name: Optional name override for the worker tool
            reset_history_on_run: Whether to clear history before each run
            pass_message_history: Whether to pass parent's message history
            parent: Optional parent agent for history/context sharing
        """
        from agentpool import Agent, BaseTeam
        from agentpool.agents.base_agent import BaseAgent

        match worker:
            case Agent():
                tool = worker.to_tool(
                    parent=parent,
                    name=name,
                    reset_history_on_run=reset_history_on_run,
                    pass_message_history=pass_message_history,
                )
            case BaseTeam() | BaseAgent():
                tool = worker.to_tool(name=name, description=worker.description)
            case _:
                raise ValueError(f"Unsupported worker type: {type(worker)}")

        self.add_tool(tool)
        return tool

    @overload
    def tool(self, func: Callable[..., Any]) -> Callable[..., Any]: ...

    @overload
    def tool(
        self,
        *,
        name: str | None = None,
        description: str | None = None,
        enabled: bool = True,
        requires_confirmation: bool = False,
        metadata: dict[str, str] | None = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]: ...

    def tool(
        self,
        func: Callable[..., Any] | None = None,
        *,
        name: str | None = None,
        description: str | None = None,
        enabled: bool = True,
        requires_confirmation: bool = False,
        metadata: dict[str, str] | None = None,
    ) -> Callable[..., Any] | Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Decorator to register a function as a tool.

        Can be used with or without parameters:

        ```python
        # Without parameters
        @provider.tool
        def my_function(x: int) -> str:
            return str(x)

        # With parameters
        @provider.tool(name="custom_name", description="Custom description")
        def another_function(y: str) -> str:
            return y.upper()
        ```

        Args:
            func: Function to register (when used without parentheses)
            name: Override for tool name
            description: Override for tool description
            enabled: Whether tool is initially enabled
            requires_confirmation: Whether execution needs confirmation
            metadata: Additional tool metadata
        """
        from agentpool.tools.base import FunctionTool

        def decorator(f: Callable[..., Any]) -> Callable[..., Any]:
            tool = FunctionTool.from_callable(
                f,
                name_override=name,
                description_override=description,
                enabled=enabled,
                requires_confirmation=requires_confirmation,
                meta=metadata or {},
            )
            self.add_tool(tool)
            return f

        return decorator if func is None else decorator(func)
