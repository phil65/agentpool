"""Type definitions."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
import os
from typing import (
    TYPE_CHECKING,
    Any,
    Literal,
    Protocol,
    runtime_checkable,
)

from pydantic import BaseModel, ConfigDict, field_validator
from pydantic_ai import AgentStreamEvent
from pydantic_ai.models import Model
from tokonomics.model_names import ModelId
from toprompt.to_prompt import AnyPromptType
from upathtools import JoinablePathLike


if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from uuid import UUID

    from fsspec.asyn import AsyncFileSystem

    from wolfharness.agents.context import AgentContext
    from wolfharness.agents.events import RichAgentStreamEvent
    from wolfharness.tools.base import Tool

    type AnyTransformFn[T] = Callable[[T], T | Awaitable[T]]
    type OptionalAwaitable[T] = T | Awaitable[T]
    # Import path string for dynamic tool loading (e.g., "mymodule:my_tool")
    type ImportPathString = str
    type ToolType = ImportPathString | AnyCallable | Tool
    type SessionIdType = str | UUID | None
    type ProcessorCallback[TResult] = Callable[..., TResult | Awaitable[TResult]]

# In reflex for example, the complex ones create issues..
SimpleJsonType = dict[
    str, bool | int | float | str | list[str] | dict[str, bool | int | float | str]
]
type JsonPrimitive = bool | int | float | str | None
type JsonValue = JsonPrimitive | JsonArray | JsonObject
type JsonObject = dict[str, JsonValue]
type JsonArray = list[JsonValue]


MCPConnectionStatus = Literal[
    "connected", "disconnected", "error", "pending", "failed", "needs-auth", "disabled"
]


@dataclass(frozen=True, slots=True)
class MCPServerStatus:
    """Status information for an MCP server."""

    name: str
    """Server name/identifier (client_id)."""

    status: MCPConnectionStatus
    """Connection status."""

    display_name: str | None = None
    """Human-readable display name for the server."""

    server_type: str = "unknown"
    """Transport type (stdio, sse, http)."""

    error: str | None = None
    """Error message if status is 'error'."""

    server_name: str | None = None
    """Self-reported server name."""

    server_version: str | None = None
    """Self-reported server version."""

    tools: list[str] = field(default_factory=list)
    """List of tool names exposed by this server (empty if not connected)."""


NodeName = str
TeamName = str
AgentName = str
StrPath = str | os.PathLike[str]
MessageRole = Literal["user", "assistant"]
PartType = Literal["text", "image", "audio", "video"]
ModelType = Model | ModelId | str
EnvironmentType = Literal["file", "inline"]
ToolSource = Literal["agent", "builtin", "dynamic", "task", "mcp", "toolset"]
AnyCallable = Callable[..., Any]
AsyncFilterFn = Callable[..., Awaitable[bool]]
SyncFilterFn = Callable[..., bool]
AnyFilterFn = Callable[..., bool | Awaitable[bool]]
# Event handler types for composable event processing
# Individual event handler for composability - takes single events
type IndividualEventHandler = Callable[[AgentContext[Any], AgentStreamEvent], Awaitable[None]]
BuiltinEventHandlerType = Literal["simple", "detailed"]
AnyEventHandlerType = IndividualEventHandler | BuiltinEventHandlerType


@dataclass(frozen=True, slots=True)
class PathReference:
    """A reference to a file or directory that should be resolved to context.

    Used to defer file/directory context resolution to the prompt conversion layer.
    The ACP layer (and other protocol adapters) emit these instead of eagerly
    reading files. Resolution happens in convert_prompts().
    """

    path: str
    """Filesystem path string."""
    fs: AsyncFileSystem | None = None
    """Optional async filesystem for reading (None = local filesystem)."""
    mime_type: str | None = None
    """Optional MIME type hint from the protocol layer."""
    display_name: str | None = None
    """Optional display name (e.g. "@converters.py")."""


PromptCompatible = AnyPromptType | JoinablePathLike | PathReference
# P = ParamSpec("P")
# SyncAsync = Callable[P, OptionalAwaitable[T]]
EndStrategy = Literal["early", "exhaustive"]
QueueStrategy = Literal["concat", "latest", "buffer"]
"""The strategy for handling multiple tool calls when a final result is found.

- `'early'`: Stop processing other tool calls once a final result is found
- `'exhaustive'`: Process all tool calls even after finding a final result
"""


@runtime_checkable
class SupportsRunStream[TResult](Protocol):
    """Protocol for nodes that support streaming via run_stream().

    Used by Team and TeamRun to check if a node can be streamed.
    All streaming nodes return RichAgentStreamEvent, with subagent/team
    activity wrapped in SubAgentEvent.
    """

    def run_stream(
        self, *prompts: Any, **kwargs: Any
    ) -> AsyncIterator[RichAgentStreamEvent[TResult]]: ...


@runtime_checkable
class SupportsStructuredOutput(Protocol):
    """Protocol for agents that support structured output via to_structured().

    Used by Interactions class for pick/extract operations that require
    structured output from agents.
    """

    @property
    def _output_type(self) -> type[Any]: ...

    def to_structured[T](self, output_type: type[T]) -> SupportsStructuredOutput:
        """Create a copy of this agent configured for structured output.

        Args:
            output_type: The type to structure output as

        Returns:
            New agent instance configured for structured output
        """
        ...

    async def run(self, *prompts: Any, **kwargs: Any) -> Any:
        """Run the agent with the given prompts.

        Returns:
            ChatMessage with content typed according to output_type from to_structured
        """
        ...


class BaseCode(BaseModel):
    """Base class for syntax-validated code."""

    code: str
    """The source code."""

    @field_validator("code")
    @classmethod
    def validate_syntax(cls, code: str) -> str:
        """Override in subclasses."""
        return code

    model_config = ConfigDict(use_attribute_docstrings=True)


def _validate_type_args(data: Any, args: tuple[Any, ...]) -> None:
    """Validate data against type arguments."""
    match data:
        case dict() if len(args) == 2:  # noqa: PLR2004
            key_type, value_type = args
            for k, v in data.items():
                if not isinstance(k, key_type):
                    raise ValueError(f"Invalid key type: {type(k)}, expected {key_type}")  # noqa: TRY004
                if not isinstance(v, value_type):
                    raise ValueError(f"Invalid value type: {type(v)}, expected {value_type}")  # noqa: TRY004
        case list() if len(args) == 1:
            item_type = args[0]
            for item in data:
                if not isinstance(item, item_type):
                    raise ValueError(f"Invalid item type: {type(item)}, expected {item_type}")  # noqa: TRY004


# class ConfigCode[T](BaseCode):
#     """Base class for configuration code that validates against a specific type.

#     Generic type T specifies the type to validate against.
#     """

#     validator_type: ClassVar[type]

#     @field_validator("code")
#     @classmethod
#     def validate_syntax(cls, code: str) -> str:
#         """Validate both YAML syntax and type constraints."""
#         import yamling

#         try:
#             # First validate YAML syntax
#             data = yamling.load(code, mode="yaml")

#             # Then validate against target type
#             match cls.validator_type:
#                 case type() as model_cls if issubclass(model_cls, BaseModel):
#                     model_cls.model_validate(data)
#                 case _ if origin := get_origin(cls.validator_type):
#                     # Handle generics like dict[str, int]
#                     if not isinstance(data, origin):
#                         raise ValueError(f"Expected {origin.__name__}, got {type(data).__name__}")
#                     # Validate type arguments if present
#                     if args := get_args(cls.validator_type):
#                         _validate_type_args(data, args)
#                 case _:
#                     raise TypeError(f"Unsupported validation type: {cls.validator_type}")

#         except Exception as e:
#             raise ValueError(f"Invalid YAML for {cls.validator_type.__name__}: {e}") from e

#         return code

#     @classmethod
#     def for_config[TConfig](
#         cls,
#         base_type: type[TConfig],
#         *,
#         name: str | None = None,
#         error_msg: str | None = None,
#     ) -> type[ConfigCode[TConfig]]:
#         """Create a new ConfigCode class for a specific type.

#         Args:
#             base_type: The type to validate against
#             name: Optional name for the new class
#             error_msg: Optional custom error message

#         Returns:
#             New ConfigCode subclass with type-specific validation
#         """

#         class TypedConfigCode(ConfigCode[TConfig]):
#             validator_type = base_type

#             @field_validator("code")
#             @classmethod
#             def validate_syntax(cls, code: str) -> str:
#                 try:
#                     return super().validate_syntax(code)
#                 except ValueError as e:
#                     msg = error_msg or str(e)
#                     raise ValueError(msg) from e

#         if name:
#             TypedConfigCode.__name__ = name

#         return TypedConfigCode


# if __name__ == "__main__":
#     from wolfharness.models.manifest import AgentsManifest

#     AgentsManifestCode = ConfigCode.for_config(
#         AgentsManifest,
#         name="AgentsManifestCode",
#         error_msg="Invalid agents manifest YAML",
#     )
