"""Tool management for AgentPool."""

from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

from pydantic import Field
from schemez import Schema

from agentpool.text_templates import get_tool_call_template
from agentpool.utils.time_utils import get_now


if TYPE_CHECKING:
    from agentpool.text_templates import FormatStyle


class ToolCallInfo(Schema):
    """Information about an executed tool call."""

    tool_name: str
    """Name of the tool that was called."""

    args: dict[str, Any]
    """Arguments passed to the tool."""

    result: Any
    """Result returned by the tool."""

    agent_name: str
    """Name of the calling agent."""

    tool_call_id: str = Field(default_factory=lambda: str(uuid4()))
    """ID provided by the model (e.g. OpenAI function call ID)."""

    timestamp: datetime = Field(default_factory=get_now)
    """When the tool was called."""

    message_id: str | None = None
    """ID of the message that triggered this tool call."""

    error: str | None = None
    """Error message if the tool call failed."""

    timing: float | None = None
    """Time taken for this specific tool call in seconds."""

    agent_tool_name: str | None = None
    """If this tool is agent-based, the name of that agent."""

    def format(
        self,
        style: FormatStyle | Literal["custom"] = "simple",
        *,
        template: str | None = None,
        variables: dict[str, Any] | None = None,
        show_timing: bool = True,
    ) -> str:
        """Format tool call information with configurable style.

        Args:
            style: Predefined style to use:
                - simple: Compact single-line format
                - detailed: Multi-line with all details
                - markdown: Formatted markdown with syntax highlighting
            template: Optional custom template (required if style="custom")
            variables: Additional variables for template rendering
            show_timing: Whether to include execution timing

        Raises:
            ValueError: If style is invalid or custom template is missing
        """
        from jinjarope import Environment

        template_str = template if style == "custom" else get_tool_call_template(style)
        if not template_str:
            raise ValueError("Custom template is required for style='custom'")
        env = Environment(trim_blocks=True, lstrip_blocks=True)
        env.filters["repr"] = repr
        template_obj = env.from_string(template_str)
        vars_ = self.model_dump()
        vars_["show_timing"] = show_timing
        if variables:
            vars_.update(variables)
        return template_obj.render(**vars_)
