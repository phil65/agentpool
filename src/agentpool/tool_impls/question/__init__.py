"""User interaction tool for asking clarifying questions."""

from typing import Literal

from agentpool.tool_impls.question.tool import QuestionTool
from agentpool_config.tools import ToolHints


__all__ = ["QuestionTool", "create_question_tool"]

# Tool metadata defaults
NAME = "question"
DESCRIPTION = "Ask the user a clarifying question during processing."
CATEGORY: Literal["other"] = "other"
HINTS = ToolHints(open_world=True)


def create_question_tool(
    *,
    name: str = NAME,
    description: str = DESCRIPTION,
    requires_confirmation: bool = False,
) -> QuestionTool:
    """Create a configured QuestionTool instance.

    Args:
        name: Tool name override.
        description: Tool description override.
        requires_confirmation: Whether tool execution needs confirmation.

    Returns:
        Configured QuestionTool instance.
    """
    return QuestionTool(
        name=name,
        description=description,
        category=CATEGORY,
        hints=HINTS,
        requires_confirmation=requires_confirmation,
    )
