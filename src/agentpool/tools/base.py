"""Base tool classes."""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass, field
import inspect
from typing import TYPE_CHECKING, Any, Literal

import logfire
from pydantic_ai.tools import Tool as PydanticAiTool
import schemez

from agentpool.log import get_logger
from agentpool.utils.inspection import (
    dataclasses_no_defaults_repr,
    execute,
    get_fn_name,
    get_fn_qualname,
)
from agentpool_config.tools import ToolHints


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from mcp.types import Tool as MCPTool, ToolAnnotations
    from pydantic_ai import RunContext, UserContent
    from pydantic_ai.tools import ToolDefinition
    from schemez import FunctionSchema, Property

    from agentpool.agents.context import AgentContext
    from agentpool.common_types import ToolSource
    from agentpool.tools.manager import ToolState

logger = get_logger(__name__)
ToolKind = Literal[
    "read",
    "edit",
    "delete",
    "move",
    "search",
    "execute",
    "think",
    "fetch",
    "switch_mode",
    "other",
]


@dataclass
class ToolResult:
    """Structured tool result with content for LLM and metadata for UI.

    This abstraction allows tools to return rich data that gets converted to
    agent-specific formats (pydantic-ai ToolReturn, FastMCP ToolResult, etc.).

    Attributes:
        content: What the LLM sees - can be string or list of content blocks
        structured_content: Machine-readable JSON data (optional)
        metadata: UI/application data that is NOT sent to the LLM
    """

    content: str | list[UserContent]
    """Content sent to the LLM (text, images, etc.)"""

    structured_content: dict[str, Any] | None = None
    """Structured JSON data for programmatic access (optional)"""

    metadata: dict[str, Any] | None = None
    """Metadata for UI/app use - NOT sent to LLM (diffs, diagnostics, etc.)."""


@dataclass
class Tool[TOutputType = Any]:
    """Base class for tools. Subclass and implement get_callable() or use FunctionTool."""

    name: str
    """The name of the tool."""

    description: str = ""
    """The description of the tool."""

    schema_override: schemez.OpenAIFunctionDefinition | None = None
    """Schema override. If not set, the schema is inferred from the callable."""

    prepare: (
        Callable[[RunContext[AgentContext], ToolDefinition], Awaitable[ToolDefinition | None]]
        | None
    ) = None
    """Prepare function for tool schema customization."""

    function_schema: Any | None = None
    """Function schema override for pydantic-ai tools."""

    hints: ToolHints = field(default_factory=ToolHints)
    """Hints for the tool."""

    import_path: str | None = None
    """The import path for the tool."""

    enabled: bool = True
    """Whether the tool is currently enabled"""

    source: ToolSource | str = "dynamic"
    """Where the tool came from."""

    requires_confirmation: bool = False
    """Whether tool execution needs explicit confirmation"""

    agent_name: str | None = None
    """The agent name as an identifier for agent-as-a-tool."""

    metadata: dict[str, str] = field(default_factory=dict)
    """Additional tool metadata"""

    category: ToolKind | None = None
    """The category of the tool."""

    instructions: str | None = None
    """Instructions for how to use this tool effectively."""

    __repr__ = dataclasses_no_defaults_repr

    @abstractmethod
    def get_callable(self) -> Callable[..., TOutputType | Awaitable[TOutputType]]:
        """Get callable for this tool. Subclasses must implement."""
        ...

    def _get_effective_prepare(
        self,
    ) -> (
        Callable[[RunContext[AgentContext], ToolDefinition], Awaitable[ToolDefinition | None]]
        | None
    ):
        """Get the effective prepare function for this tool.

        Returns self.prepare if set.

        Returns:
            Prepare function or None.
        """
        return self.prepare

    def _detect_takes_ctx(self, func: Callable[..., Any] | None = None) -> bool:
        """Detect if function takes RunContext parameter.

        Args:
            func: The callable to inspect. If None, uses self.get_callable().

        Returns:
            True if function has a RunContext parameter, False otherwise.
        """
        if func is None:
            func = self.get_callable()

        # Check for RunContext in function signature
        sig = inspect.signature(func)
        for param in sig.parameters.values():
            # Check by string type name (works across TYPE_CHECKING)
            if param.annotation == "RunContext" or (
                hasattr(param.annotation, "__name__") and param.annotation.__name__ == "RunContext"
            ):
                return True
        return False

    def _get_json_schema(self, func: Callable[..., Any] | None = None) -> dict[str, Any] | None:
        """Get effective JSON schema for this tool.

        Returns a JSON schema dict if a custom schema is needed
        (from schema_override or fallback to schemez), or None if
        pydantic-ai should infer the schema automatically.

        Args:
            func: The callable to use for schema generation. If None, uses self.get_callable().

        Returns:
            JSON schema dict or None.
        """
        if func is None:
            func = self.get_callable()

        # If no schema_override, let pydantic-ai infer the schema
        if self.schema_override is None:
            return None

        # Try primary path with pydantic_ai.function_schema
        try:
            from pydantic_ai._function_schema import (  # type: ignore[attr-defined]
                GenerateJsonSchema,
                function_schema,
            )

            schema = function_schema(func, schema_generator=GenerateJsonSchema)

            # Apply schema_override to generated schema
            # Merge top-level description
            if "description" in self.schema_override:
                schema.json_schema["description"] = self.schema_override["description"]

            if "parameters" in self.schema_override:
                override_params = self.schema_override["parameters"]
                # Merge custom parameter definitions (which include descriptions)
                if "properties" in override_params:
                    for param_name, param_def in override_params["properties"].items():
                        if param_name in schema.json_schema.get("properties", {}):
                            # Update existing parameter with custom description
                            schema.json_schema["properties"][param_name].update(param_def)
                        else:
                            # Add new parameter
                            schema.json_schema.setdefault("properties", {})[param_name] = param_def
        except Exception as e:
            # Fallback to schemez if pydantic_ai.function_schema fails
            from pydantic.errors import PydanticUndefinedAnnotation

            if isinstance(e, (PydanticUndefinedAnnotation, NameError)):
                logger.warning(
                    "pydantic_ai.function_schema failed for %s, falling back to schemez: %s",
                    self.name,
                    str(e),
                )
            else:
                raise

            # Fallback: use schemez to generate schema
            from pydantic_ai import RunContext

            from agentpool.agents.context import AgentContext

            # Use schema_override description if provided, otherwise use self.description
            desc = (
                self.schema_override.get("description", self.description)
                if self.schema_override
                else self.description
            )

            # Use schemez to generate JSON schema
            schema = schemez.create_schema(  # type: ignore
                func,
                name_override=self.name,
                description_override=desc,
                exclude_types=[AgentContext, RunContext],
            )

            # Return only the parameters part (the "object" schema)
            # Use model_dump - schemez.FunctionSchema has this method (pydantic-compatible)
            schema_dump = getattr(schema, "model_dump")()  # noqa: B009, type: ignore[attr-defined]
            generated_params = schema_dump["parameters"]

            # Apply parameter overrides to maintain consistency with the primary path
            if "parameters" in self.schema_override:
                override_params = self.schema_override["parameters"]
                if "properties" in override_params:
                    for param_name, param_def in override_params["properties"].items():
                        if param_name in generated_params.get("properties", {}):
                            generated_params["properties"][param_name].update(param_def)
                        else:
                            generated_params.setdefault("properties", {})[param_name] = param_def
            return generated_params  # type: ignore[no-any-return]
        else:
            return schema.json_schema

    def to_pydantic_ai(
        self, function_override: Callable[..., TOutputType | Awaitable[TOutputType]] | None = None
    ) -> PydanticAiTool:
        """Convert tool to Pydantic AI tool.

        Args:
            function_override: Optional callable to override self.get_callable().

        Returns:
            PydanticAiTool instance configured for this tool.
        """
        base_metadata = self.metadata or {}
        metadata = {
            **base_metadata,
            "agent_name": self.agent_name,
            "category": self.category,
        }
        function = function_override if function_override is not None else self.get_callable()

        # Check if we have a custom JSON schema that needs to be used
        json_schema = self._get_json_schema(function)

        # If we have a custom schema, use Tool.from_schema
        if json_schema is not None:
            # Detect if function takes RunContext parameter
            takes_ctx = self._detect_takes_ctx(function)

            # Import Tool.from_schema at runtime to avoid circular imports
            from pydantic_ai.tools import Tool as PydanticAiToolClass

            tool_instance = PydanticAiToolClass.from_schema(
                function=function,
                name=self.name,
                description=self.description,
                json_schema=json_schema,
                takes_ctx=takes_ctx,
            )
            # Tool.from_schema doesn't accept prepare parameter, assign it manually
            tool_instance.prepare = self._get_effective_prepare()  # type: ignore[assignment]
            return tool_instance
        # No custom schema, let pydantic-ai infer it automatically
        return PydanticAiTool(
            function=function,
            name=self.name,
            description=self.description,
            requires_approval=self.requires_confirmation,
            metadata=metadata,
            prepare=self._get_effective_prepare(),  # type: ignore[arg-type]
        )

    @property
    def schema_obj(self) -> FunctionSchema:
        """Get the OpenAI function schema for the tool."""
        from pydantic_ai import RunContext

        from agentpool.agents.context import AgentContext

        return schemez.create_schema(
            self.get_callable(),
            name_override=self.name,
            description_override=self.description,
            exclude_types=[AgentContext, RunContext],
        )

    @property
    def schema(self) -> schemez.OpenAIFunctionTool:
        """Get the OpenAI function schema for the tool."""
        schema = self.schema_obj.model_dump_openai()
        if self.schema_override:
            schema["function"] = self.schema_override
        return schema

    def matches_filter(self, state: ToolState) -> bool:
        """Check if tool matches state filter."""
        match state:
            case "all":
                return True
            case "enabled":
                return self.enabled
            case "disabled":
                return not self.enabled

    @property
    def parameters(self) -> list[ToolParameter]:
        """Get information about tool parameters."""
        schema = self.schema["function"]
        properties: dict[str, Property] = schema.get("properties", {})  # type: ignore[assignment]
        required: list[str] = schema.get("required", [])  # type: ignore[assignment]

        return [
            ToolParameter(
                name=name,
                required=name in required,
                type_info=details.get("type"),
                description=details.get("description"),
            )
            for name, details in properties.items()
        ]

    def format_info(self, indent: str = "  ") -> str:
        """Format complete tool information."""
        lines = [f"{indent}→ {self.name}"]
        if self.description:
            lines.append(f"{indent}  {self.description}")
        if self.parameters:
            lines.append(f"{indent}  Parameters:")
            lines.extend(f"{indent}    {param}" for param in self.parameters)
        if self.metadata:
            lines.append(f"{indent}  Metadata:")
            lines.extend(f"{indent}    {k}: {v}" for k, v in self.metadata.items())
        return "\n".join(lines)

    @logfire.instrument("Executing tool {self.name} with args={args}, kwargs={kwargs}")
    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        """Execute tool, handling both sync and async cases."""
        return await execute(self.get_callable(), *args, **kwargs, use_thread=True)

    async def execute_and_unwrap(self, *args: Any, **kwargs: Any) -> Any:
        """Execute tool and unwrap ToolResult if present.

        This is a convenience method for tests and direct tool usage that want
        plain content instead of ToolResult objects.

        Returns:
            If tool returns ToolResult, returns ToolResult.content.
            Otherwise returns the raw result.
        """
        result = await self.execute(*args, **kwargs)
        if isinstance(result, ToolResult):
            return result.content
        return result

    @classmethod
    def from_code(
        cls,
        code: str,
        name: str | None = None,
        description: str | None = None,
    ) -> FunctionTool[Any]:
        """Create a FunctionTool from a code string."""
        namespace: dict[str, Any] = {}
        exec(code, namespace)
        func = next((v for v in namespace.values() if callable(v)), None)
        if not func:
            msg = "No callable found in provided code"
            raise ValueError(msg)
        return FunctionTool.from_callable(
            func, name_override=name, description_override=description
        )

    @classmethod
    def from_callable(
        cls,
        fn: Callable[..., TOutputType | Awaitable[TOutputType]] | str,
        *,
        name_override: str | None = None,
        description_override: str | None = None,
        schema_override: schemez.OpenAIFunctionDefinition | None = None,
        prepare: (
            Callable[[RunContext[AgentContext], ToolDefinition], Awaitable[ToolDefinition | None]]
            | None
        ) = None,
        function_schema: Any | None = None,
        hints: ToolHints | None = None,
        category: ToolKind | None = None,
        enabled: bool = True,
        source: ToolSource | str | None = None,
        **kwargs: Any,
    ) -> FunctionTool[TOutputType]:
        """Create a FunctionTool from a callable or import path."""
        return FunctionTool.from_callable(
            fn,
            name_override=name_override,
            description_override=description_override,
            schema_override=schema_override,
            prepare=prepare,
            function_schema=function_schema,
            hints=hints,
            category=category,
            enabled=enabled,
            source=source,
            **kwargs,
        )

    def get_mcp_tool_annotations(self) -> ToolAnnotations:
        """Convert internal Tool to MCP Tool."""
        from mcp.types import ToolAnnotations

        return ToolAnnotations(
            title=self.name,
            readOnlyHint=self.hints.read_only if self.hints else None,
            destructiveHint=self.hints.destructive if self.hints else None,
            idempotentHint=self.hints.idempotent if self.hints else None,
            openWorldHint=self.hints.open_world if self.hints else None,
        )

    def to_mcp_tool(self) -> MCPTool:
        """Convert internal Tool to MCP Tool."""
        schema = self.schema
        from mcp.types import Tool as MCPTool

        return MCPTool(
            name=schema["function"]["name"],
            description=schema["function"]["description"],
            inputSchema=schema["function"]["parameters"],  # pyright: ignore
            annotations=self.get_mcp_tool_annotations(),
        )


@dataclass
class FunctionTool[TOutputType = Any](Tool[TOutputType]):
    """Tool wrapping a plain callable function."""

    callable: Callable[..., TOutputType | Awaitable[TOutputType]] = field(default=lambda: None)  # type: ignore[assignment]
    """The actual tool implementation."""

    def get_callable(self) -> Callable[..., TOutputType | Awaitable[TOutputType]]:
        """Return the wrapped callable."""
        return self.callable

    @classmethod
    def from_callable(
        cls,
        fn: Callable[..., TOutputType | Awaitable[TOutputType]] | str,
        *,
        name_override: str | None = None,
        description_override: str | None = None,
        schema_override: schemez.OpenAIFunctionDefinition | None = None,
        prepare: (
            Callable[[RunContext[AgentContext], ToolDefinition], Awaitable[ToolDefinition | None]]
            | None
        ) = None,
        function_schema: Any | None = None,
        hints: ToolHints | None = None,
        category: ToolKind | None = None,
        enabled: bool = True,
        source: ToolSource | str | None = None,
        **kwargs: Any,
    ) -> FunctionTool[TOutputType]:
        """Create a FunctionTool from a callable or import path string."""
        if isinstance(fn, str):
            import_path = fn
            from agentpool.utils import importing

            callable_obj = importing.import_callable(fn)
            name = getattr(callable_obj, "__name__", "unknown")
        else:
            callable_obj = fn
            module = fn.__module__
            if hasattr(fn, "__qualname__"):  # Regular function
                name = get_fn_name(fn)
                import_path = f"{module}.{get_fn_qualname(fn)}"
            else:  # Instance with __call__ method
                name = fn.__class__.__name__
                import_path = f"{module}.{fn.__class__.__qualname__}"

        return cls(
            name=name_override or name,
            description=description_override or inspect.getdoc(callable_obj) or "",
            callable=callable_obj,  # pyright: ignore[reportArgumentType]
            import_path=import_path,
            schema_override=schema_override,
            prepare=prepare,
            function_schema=function_schema,
            category=category,
            hints=hints or ToolHints(),
            enabled=enabled,
            source=source or "dynamic",
            **kwargs,
        )


@dataclass
class ToolParameter:
    """Information about a tool parameter."""

    name: str
    required: bool
    type_info: str | None = None
    description: str | None = None

    def __str__(self) -> str:
        """Format parameter info."""
        req = "*" if self.required else ""
        type_str = f": {self.type_info}" if self.type_info else ""
        desc = f" - {self.description}" if self.description else ""
        return f"{self.name}{req}{type_str}{desc}"


if __name__ == "__main__":
    import webbrowser

    t = Tool.from_callable(webbrowser.open)
