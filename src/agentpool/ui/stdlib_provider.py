"""Stdlib input provider."""

from __future__ import annotations

import sys
from textwrap import dedent
from typing import TYPE_CHECKING, Any

import anyenv
from mcp import types

from agentpool.log import get_logger
from agentpool.tools.decisions import Approve, Deny
from agentpool.tools.exceptions import ToolError
from agentpool.ui.base import InputProvider


if TYPE_CHECKING:
    from pydantic import BaseModel

    from agentpool.agents.context import AgentContext
    from agentpool.messaging.context import NodeContext
    from agentpool.tools.decisions import ToolDecision


logger = get_logger(__name__)


class StdlibInputProvider(InputProvider):
    """Input provider using only Python stdlib functionality."""

    async def get_text_input(self, context: NodeContext, prompt: str) -> str:
        print(f"{prompt}\n> ", end="", file=sys.stderr, flush=True)
        return input()

    async def get_structured_input(
        self,
        context: NodeContext,
        prompt: str,
        output_type: type[BaseModel],
    ) -> BaseModel:
        """Get structured input, with promptantic and fallback handling."""
        if result := await _get_promptantic_result(output_type):
            return result

        # Fallback: Get raw input and validate
        prompt = f"{prompt}\n(Please provide response as {output_type.__name__})"
        raw_input = await self.get_input(context, prompt)
        try:
            return output_type.model_validate_json(raw_input)
        except Exception as e:
            raise ToolError(f"Invalid response format: {e}") from e

    async def get_tool_confirmation(
        self,
        context: AgentContext[Any],
        tool_description: str = "",
    ) -> ToolDecision:
        agent_name = context.node_name
        tool_name = context.tool_name or "unknown"
        args = context.tool_input
        prompt = dedent(f"""
            Tool Execution Confirmation
            -------------------------
            Tool: {tool_name}
            Description: {tool_description or "No description"}
            Agent: {agent_name}

            Arguments:
            {anyenv.dump_json(args, indent=True)}

            Options:
            - y: allow this call
            - always: allow this tool for the rest of the session
            - n [reason]: don't run it (the agent is told, with your reason, and goes on)
            - never: don't run this tool for the rest of the session
            - stop: don't run it, and end the turn
            """).strip()

        print(f"{prompt}\nChoice [y/always/n/never/stop]: ", end="", file=sys.stderr, flush=True)
        response = input().strip()
        choice, _, reason = response.partition(" ")
        match choice.lower():
            case "y" | "yes":
                return Approve()
            case "always":
                return Approve(scope="session")
            case "never":
                return Deny(scope="session", message=reason.strip() or None)
            case "stop":
                return Deny(stop=True)
            case _:
                return Deny(message=reason.strip() or None)

    async def get_elicitation(
        self,
        params: types.ElicitRequestParams,
        *,
        tool_call_id: str | None = None,
    ) -> types.ElicitResult | types.ErrorData:
        """Get user response to elicitation request using stdlib input."""
        print(f"\n{params.message}", file=sys.stderr)
        try:
            # URL mode: prompt user to open external URL
            match params:
                case types.ElicitRequestURLParams(url=url):
                    print(f"URL: {url}", file=sys.stderr)
                    print("Open this URL? [y/n]: ", end="", file=sys.stderr, flush=True)
                    response = input().strip().lower()
                    action = (
                        "accept"
                        if response in ("y", "yes")
                        else ("decline" if response in ("n", "no") else "cancel")
                    )
                    return types.ElicitResult(action=action)
                case types.ElicitRequestFormParams(requested_schema=schema):
                    # Form mode: a form for the requested schema (validated against it)
                    from promptantic import FormCancelledError, fill

                    try:
                        content = await fill(dict(schema or {"type": "object"}))
                    except FormCancelledError:
                        return types.ElicitResult(action="cancel")
                    return types.ElicitResult(action="accept", content=content)

        except KeyboardInterrupt:
            return types.ElicitResult(action="cancel")
        except Exception as e:  # noqa: BLE001
            return types.ErrorData(code=types.INVALID_REQUEST, message=f"Elicitation failed: {e}")


async def _get_promptantic_result(output_type: type[BaseModel]) -> BaseModel | None:
    """Structured input as a form (promptantic); ``None`` if it can't be shown.

    Raises:
        ToolError: The user cancelled the form.
    """
    from promptantic import FormCancelledError, fill

    try:
        return await fill(output_type)
    except FormCancelledError as e:
        raise ToolError("Input cancelled by the user") from e
    except Exception as e:  # noqa: BLE001
        logger.warning("Promptantic failed", error=e)
        return None
