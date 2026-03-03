"""Models for tools."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Literal

from pydantic import ConfigDict, Field, ImportString
from schemez import Schema

from agentpool.log import get_logger


logger = get_logger(__name__)


if TYPE_CHECKING:
    from agentpool.tools.base import Tool


class ToolHints(Schema):
    """Configuration for tool execution hints."""

    read_only: bool | None = Field(default=None, title="Read-only operation")
    """Hints that this tool only reads data without modifying anything"""

    destructive: bool | None = Field(default=None, title="Destructive operation")
    """Hints that this tool performs destructive operations that cannot be undone"""

    idempotent: bool | None = Field(default=None, title="Idempotent operation")
    """Hints that this tool has idempotent behaviour"""

    open_world: bool | None = Field(default=None, title="External resource access")
    """Hints that this tool can access / interact with external resources beyond the
    current system"""


class BaseToolConfig(Schema):
    """Base configuration for agent tools."""

    type: str = Field(init=False)
    """Type discriminator for tool configs."""

    name: str | None = Field(
        default=None,
        examples=["search_web", "file_reader", "calculator"],
        title="Tool name override",
    )
    """Optional override for the tool name."""

    description: str | None = Field(
        default=None,
        examples=["Search the web for information", "Read file contents"],
        title="Tool description override",
    )
    """Optional override for the tool description."""

    enabled: bool = Field(default=True, title="Tool enabled")
    """Whether this tool is initially enabled."""

    requires_confirmation: bool = Field(default=False, title="Requires confirmation")
    """Whether tool execution needs confirmation."""

    metadata: dict[str, str] = Field(default_factory=dict, title="Tool metadata")
    """Additional tool metadata."""

    instructions: str | None = Field(default=None, title="Tool instructions")
    """Instructions for how to use this tool effectively."""

    prepare: ImportString[str] | None = Field(
        default=None,
        examples=["mymodule:my_prepare_function"],
        title="Prepare function",
    )
    """Prepare function for tool schema customization (pydantic-ai style)."""

    function_schema: Any | None = Field(
        default=None,
        title="Function schema override",
    )
    """Function schema override for pydantic-ai tools."""

    schema_override: Any | None = Field(
        default=None,
        title="Schema override",
    )
    """Schema override for tool function definition."""

    model_config = ConfigDict(frozen=True)

    def get_tool(self) -> Tool:
        """Convert config to Tool instance."""
        raise NotImplementedError


class ImportToolConfig(BaseToolConfig):
    """Configuration for importing tools from Python modules."""

    type: Literal["import"] = Field("import", init=False)
    """Import path based tool."""

    import_path: ImportString[Callable[..., Any]] = Field(
        examples=["webbrowser:open", "builtins:print"],
        title="Import path",
    )
    """Import path to the tool function."""

    hints: ToolHints | None = Field(
        default=None,
        title="Execution hints",
        examples=[
            {"read_only": True, "destructive": False, "open_world": True, "idempotent": False},
        ],
    )
    """Hints for tool execution."""

    def get_tool(self) -> Tool:
        """Import and create tool from configuration."""
        from agentpool.tools.base import Tool

        # Load prepare callable from import string if provided
        prepare_callable = None
        if self.prepare:
            # ImportString is like "mymodule:my_function"
            # Load it as a callable
            try:
                module_path, func_name = str(self.prepare).split(":")
                module = __import__(module_path, fromlist=[func_name])
                prepare_callable = getattr(module, func_name)
            except (ValueError, ImportError, AttributeError) as e:
                # If import fails, pass None (prepare is optional)
                logger.warning("Failed to import prepare function %s: %s", self.prepare, e)

        return Tool.from_callable(
            self.import_path,
            name_override=self.name,
            description_override=self.description,
            enabled=self.enabled,
            requires_confirmation=self.requires_confirmation,
            metadata=self.metadata,
            instructions=self.instructions,
            prepare=prepare_callable,
            function_schema=self.function_schema,
            schema_override=self.schema_override,
        )
