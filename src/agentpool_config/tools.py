"""Models for tools."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Literal, Self

from pydantic import ConfigDict, Field, ImportString
from schemez import Schema


if TYPE_CHECKING:
    from mcp.types import ToolAnnotations

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

    @classmethod
    def from_mcp(cls, annotations: ToolAnnotations) -> Self:
        """Create a ToolHints instance from MCP tool annotations."""
        return cls(
            read_only=annotations.read_only_hint,
            destructive=annotations.destructive_hint,
            idempotent=annotations.idempotent_hint,
            open_world=annotations.open_world_hint,
        )

    def to_mcp(self) -> ToolAnnotations:
        """Convert to MCP tool annotations."""
        from mcp.types import ToolAnnotations

        return ToolAnnotations(
            read_only_hint=self.read_only,
            destructive_hint=self.destructive,
            idempotent_hint=self.idempotent,
            open_world_hint=self.open_world,
        )


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
        from agentpool.tools.base import FunctionTool

        return FunctionTool.from_callable(
            self.import_path,
            name_override=self.name,
            description_override=self.description,
            enabled=self.enabled,
            requires_confirmation=self.requires_confirmation,
            meta=self.metadata,
        )
