"""Base input provider class."""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from collections.abc import Coroutine

    from mcp import types
    from pydantic import BaseModel

    from agentpool.agents.context import AgentContext
    from agentpool.messaging.context import NodeContext
    from agentpool.tools.decisions import ToolDecision


class InputProvider(ABC):
    """Base class for handling all UI interactions."""

    async def get_input(
        self,
        context: NodeContext,
        prompt: str,
        output_type: type[BaseModel] | None = None,
    ) -> Any:
        """Get normal input (used by HumanProvider).

        Args:
            context: Current agent context
            prompt: The prompt to show to the user
            output_type: Optional type for structured responses
            message_history: Optional conversation history
        """
        if output_type:
            return await self.get_structured_input(context, prompt, output_type)
        return await self.get_text_input(context, prompt)

    async def get_text_input(self, context: NodeContext[Any], prompt: str) -> str:
        """Get normal text input."""
        raise NotImplementedError

    async def get_structured_input(
        self,
        context: NodeContext[Any],
        prompt: str,
        output_type: type[BaseModel],
    ) -> BaseModel:
        """Get structured input."""
        raise NotImplementedError

    @abstractmethod
    def get_tool_confirmation(
        self,
        context: AgentContext[Any],
        tool_description: str = "",
    ) -> Coroutine[Any, Any, ToolDecision]:
        """Ask the user whether to run a tool call: approve or deny it, once or for the session.

        Tool name and arguments are read from context.tool_name and context.tool_input.

        Args:
            context: Current node context with tool_name, tool_call_id, tool_input set
            tool_description: Human-readable description of the tool
        """

    @abstractmethod
    def get_elicitation(
        self,
        params: types.ElicitRequestParams,
        *,
        tool_call_id: str | None = None,
    ) -> Coroutine[Any, Any, types.ElicitResult | types.ErrorData]:
        """Get user response to elicitation request.

        Args:
            params: MCP elicit request parameters
            tool_call_id: ID of the tool call requesting input, if any
        """

    async def elicitation_complete(self, elicitation_id: str) -> None:  # noqa: B027
        """Handle completion of a URL elicitation this provider handled.

        Sent by MCP servers once the out-of-band URL flow finished. No-op by default.

        Args:
            elicitation_id: ID of the completed URL elicitation
        """
