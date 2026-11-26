"""Base tool classes."""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass, field
import inspect
from typing import TYPE_CHECKING, Any, Literal, cast, get_type_hints

import logfire

# UserContent at runtime (not under TYPE_CHECKING): pydantic-ai builds a JSON schema
# from the ToolResult return annotation of every tool and needs its field types.
from pydantic_ai import RunContext, Tool as PydanticAiTool, ToolReturn, UserContent
import schemez

from agentpool.log import get_logger
from agentpool.utils.inspection import (
    dataclasses_no_defaults_repr,
    execute,
    get_fn_name,
    get_fn_qualname,
)
from agentpool.utils.signatures import (
    filter_schema_params,
    get_params_matching_predicate,
    is_context_type,
)
from agentpool_config.tools import ToolHints


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from mcp.types import Tool as MCPTool
    from schemez import FunctionSchema, Property

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
    """Structured tool result: content for the LLM, optionally structured data.

    Converted to agent-specific formats (pydantic-ai ToolReturn, FastMCP ToolResult, etc.).
    What a tool shows beyond its result (diffs, terminals, the plan) goes out as events
    while it runs (``ctx.events``).

    Attributes:
        content: What the LLM sees - can be string or list of content blocks
        structured_content: Machine-readable JSON data (optional)
    """

    content: str | list[UserContent]
    """Content sent to the LLM (text, images, etc.)"""

    structured_content: dict[str, Any] | None = None
    """Structured JSON data for programmatic access (optional)"""

    def to_pydantic_ai(self) -> ToolReturn:
        """Convert this ToolResult to a pydantic-ai ToolReturn."""
        val = self.structured_content or self.content
        return ToolReturn(return_value=val, content=self.content)


@dataclass
class Tool[TOutputType = Any]:
    """Base class for tools. Subclass and implement get_callable() or use FunctionTool."""

    name: str
    """The name of the tool."""

    description: str = ""
    """The description of the tool."""

    schema_override: schemez.OpenAIFunctionDefinition | None = None
    """Schema override. If not set, the schema is inferred from the callable."""

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

    meta: dict[str, Any] = field(default_factory=dict)
    """Additional tool metadata"""

    category: ToolKind | None = None
    """The category of the tool."""

    __repr__ = dataclasses_no_defaults_repr

    @abstractmethod
    def get_callable(self) -> Callable[..., TOutputType | Awaitable[TOutputType]]:
        """Get the callable for this tool. Subclasses must implement."""
        ...

    def to_pydantic_ai(self) -> PydanticAiTool:
        """Convert tool to Pydantic AI tool."""
        metadata = {**self.meta, "agent_name": self.agent_name, "category": self.category}
        return PydanticAiTool(
            function=self.get_callable(),
            name=self.name,
            description=self.description,
            requires_approval=self.requires_confirmation,
            metadata=metadata,
        )

    @property
    def schema_obj(self) -> FunctionSchema:
        """Get the OpenAI function schema for the tool."""
        from agentpool.agents.context import AgentContext

        fn = self.get_callable()
        schema = schemez.create_schema(
            fn,
            name_override=self.name,
            description_override=self.description,
            exclude_types=[AgentContext, RunContext],
        )
        # Context parameters typed with names unavailable at runtime (TYPE_CHECKING
        # imports) escape exclude_types: drop them by name
        try:
            context_params = get_params_matching_predicate(
                fn, lambda p: is_context_type(p.annotation)
            )
        except TypeError, ValueError:  # no inspectable signature
            return schema
        if context_params & schema.parameters.get("properties", {}).keys():
            params = filter_schema_params(schema.parameters, context_params)  # type: ignore[arg-type]
            schema = schema.model_copy(
                update={
                    "parameters": params,
                    "required": [r for r in schema.required if r not in context_params],
                }
            )
        return schema

    @property
    def output_schema(self) -> dict[str, Any] | None:
        """Get the MCP-facing output schema, unwrapping internal wrapper types.

        Returns None for tools returning ToolResult (internal transport wrapper)
        or generic object types where no meaningful schema can be advertised.
        Returns the JSON schema dict for tools with concrete return types.
        """
        fn = self.get_callable()
        try:
            hints = get_type_hints(fn)
        except Exception:  # noqa: BLE001
            return None
        ret = hints.get("return")
        if ret is None or ret is ToolResult:
            return None
        returns = self.schema_obj.returns
        if returns == {"type": "object"}:
            return None
        return returns

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
        params = self.schema["function"]["parameters"]
        properties: dict[str, Property] = params["properties"]
        required: list[str] = params.get("required", [])

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
        if self.meta:
            lines.append(f"{indent}  Metadata:")
            lines.extend(f"{indent}    {k}: {v}" for k, v in self.meta.items())
        return "\n".join(lines)

    @logfire.instrument("Executing tool {self.name} kwargs={kwargs}")
    async def run(self, **kwargs: Any) -> Any:
        """Execute tool, handling both sync and async cases."""
        return await execute(self.get_callable(), **kwargs, use_thread=True)

    async def execute_and_unwrap(self, **kwargs: Any) -> Any:
        """Execute tool and unwrap ToolResult if present."""
        result = await self.run(**kwargs)
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
            raise ValueError("No callable found in provided code")
        return FunctionTool.from_callable(
            func, name_override=name, description_override=description
        )

    def to_mcp_tool(self) -> MCPTool:
        """Convert internal Tool to MCP Tool."""
        from mcp.types import Tool as MCPTool

        return MCPTool(
            name=self.schema["function"]["name"],
            description=self.schema["function"]["description"],
            input_schema=cast(dict[str, Any], self.schema["function"]["parameters"]),
            annotations=self.hints.to_mcp(),
        )


@dataclass
class FunctionTool[TOutputType = Any](Tool[TOutputType]):
    """Tool wrapping a plain callable function."""

    fn: Callable[..., TOutputType | Awaitable[TOutputType]] = field(kw_only=True)
    """The actual tool implementation."""

    def get_callable(self) -> Callable[..., TOutputType | Awaitable[TOutputType]]:
        """Return the wrapped callable."""
        return self.fn

    @classmethod
    def from_callable(
        cls,
        fn: Callable[..., TOutputType | Awaitable[TOutputType]] | str,
        *,
        name_override: str | None = None,
        description_override: str | None = None,
        schema_override: schemez.OpenAIFunctionDefinition | None = None,
        hints: ToolHints | None = None,
        category: ToolKind | None = None,
        enabled: bool = True,
        source: ToolSource | str | None = None,
        requires_confirmation: bool = False,
        meta: dict[str, Any] | None = None,
    ) -> FunctionTool[TOutputType]:
        """Create a FunctionTool from a callable or import path string."""
        from agentpool.utils import importing

        if isinstance(fn, str):
            import_path = fn
            callable_obj = importing.import_callable(fn)
            name = getattr(callable_obj, "__name__", "unknown")
        else:
            callable_obj = fn
            if hasattr(fn, "__qualname__"):  # Regular function
                name = get_fn_name(fn)
                import_path = f"{fn.__module__}.{get_fn_qualname(fn)}"
            else:  # Instance with __call__ method
                name = fn.__class__.__name__
                import_path = f"{fn.__module__}.{fn.__class__.__qualname__}"
        return cls(
            name=name_override or name,
            description=description_override or inspect.getdoc(callable_obj) or "",
            fn=callable_obj,
            import_path=import_path,
            schema_override=schema_override,
            category=category,
            hints=hints or ToolHints(),
            enabled=enabled,
            source=source or "dynamic",
            requires_confirmation=requires_confirmation,
            meta=meta or {},
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

    t = FunctionTool.from_callable(webbrowser.open)
