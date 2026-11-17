"""Mock input provider implementation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from agentpool.ui.base import InputProvider


if TYPE_CHECKING:
    from mcp import types

    from agentpool.agents.context import AgentContext, ConfirmationResult
    from agentpool.messaging.context import NodeContext

InputMethod = Literal["get_input", "get_tool_confirmation", "get_elicitation"]


@dataclass(kw_only=True)
class InputCall:
    """Record of an input provider call."""

    method: InputMethod
    args: tuple[Any, ...]
    kwargs: dict[str, Any]
    result: Any


class MockInputProvider(InputProvider):
    """Provider that records calls and returns pre-configured responses."""

    def __init__(
        self,
        *,
        input_response: str = "mock response",
        tool_confirmation: ConfirmationResult = "allow",
        elicitation_response: dict[str, Any] | None = None,
    ) -> None:
        self.input_response = input_response
        self.tool_confirmation: ConfirmationResult = tool_confirmation
        self.elicitation_response: dict[str, Any] = elicitation_response or {
            "response": "mock response"
        }
        self.calls: list[InputCall] = []

    async def get_input(
        self,
        context: NodeContext,
        prompt: str,
        output_type: type | None = None,
    ) -> Any:
        call = InputCall(
            method="get_input",
            args=(context, prompt),
            kwargs={"output_type": output_type},
            result=self.input_response,
        )
        self.calls.append(call)
        return self.input_response

    async def get_tool_confirmation(
        self,
        context: AgentContext[Any],
        tool_description: str = "",
    ) -> ConfirmationResult:
        call = InputCall(
            method="get_tool_confirmation",
            args=(context, tool_description),
            kwargs={},
            result=self.tool_confirmation,
        )
        self.calls.append(call)
        return self.tool_confirmation

    async def get_elicitation(
        self,
        params: types.ElicitRequestParams,
        *,
        tool_call_id: str | None = None,
    ) -> types.ElicitResult | types.ErrorData:
        from mcp import types

        result = types.ElicitResult(action="accept", content=self.elicitation_response)
        call = InputCall(method="get_elicitation", args=(params,), kwargs={}, result=result)
        self.calls.append(call)
        return result
