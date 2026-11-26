"""AskUser tool implementation."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, assert_never

from agentpool.agents.context import AgentContext  # noqa: TC001
from agentpool.tools.base import Tool, ToolResult


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


@dataclass
class QuestionOption:
    """Option for a question in OpenCode format.

    Represents a single choice that can be presented to the user.
    """

    label: str
    """Display text (1-5 words, concise)."""

    description: str
    """Explanation of the choice."""


@dataclass
class OpenCodeQuestionInfo:
    """Question information in OpenCode format.

    This matches OpenCode's QuestionInfo schema used by the TUI.
    """

    question: str
    """Complete question text."""

    header: str
    """Very short label (max 12 chars) - used for tab headers in the TUI."""

    options: list[QuestionOption]
    """Available choices for the user to select from."""

    multiple: bool = False
    """Allow selecting multiple choices."""


@dataclass
class QuestionTool(Tool[ToolResult]):
    """Tool for asking the user clarifying questions.

    Enables agents to ask users for additional information or clarification
    when needed to complete a task effectively.
    """

    def get_callable(self) -> Callable[..., Awaitable[ToolResult]]:
        """Get the tool callable."""
        return self._execute

    async def _execute(
        self,
        ctx: AgentContext,
        prompt: str,
        response_schema: dict[str, Any] | None = None,
    ) -> ToolResult:
        """Ask the user a clarifying question.

        Args:
            ctx: Agent execution context.
            prompt: Question to ask the user.
            response_schema: Optional JSON schema for structured response.

        Returns:
            The user's response as a string.
        """
        from mcp.types import ElicitRequestFormParams, ElicitResult, ErrorData

        schema = response_schema or {"type": "string"}
        params = ElicitRequestFormParams(message=prompt, requested_schema=schema)
        match await ctx.handle_elicitation(params):
            case ElicitResult(action="accept", content={"value": list() as value}):
                # Content is a dict with "value" key per MCP spec
                # Handle list responses (multi-select)
                answer_str = ", ".join(str(v) for v in value)
                # OpenCode expects array of arrays (one per question)
                # Single question, array of selected values
                return ToolResult(content=answer_str)
            case ElicitResult(action="accept", content={"value": value}):
                # Single answer
                answer_str = str(value)
                # Single question, single answer
                return ToolResult(content=answer_str)
            case ElicitResult(action="accept", content=content):
                # Fallback for plain content
                answer_str = str(content)
                return ToolResult(content=answer_str)
            case ElicitResult(action="cancel"):
                return ToolResult(content="User cancelled the request")
            case ElicitResult():
                return ToolResult(content="User declined to answer")
            case ErrorData(message=message):
                return ToolResult(content=f"Error: {message}")
            case _ as unreachable:
                assert_never(unreachable)
