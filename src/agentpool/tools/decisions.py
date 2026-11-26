"""The user's decision on a tool call: approve it or deny it, once or for the session.

One vocabulary for every agent type and protocol; each backend maps it to its own values:

- ``Approve()``: ACP ``allow_once``, Claude Code ``Allow``, Codex ``allow``, OpenCode
  ``once``, T3 ``accept``, MCP elicitation ``accept``, pydantic-ai ``ToolApproved``.
- ``Approve(args=...)``: Claude Code ``Allow(updated_input)``, pydantic-ai
  ``ToolApproved(override_args)``.
- ``Deny(message=...)``: ACP ``reject_once``, Claude Code ``Deny(message)``, Codex
  ``deny``, OpenCode ``reject`` (with its message), T3 ``decline``, MCP ``decline``,
  pydantic-ai ``ToolDenied(message)``.
- ``Deny(stop=True)``: ACP ``cancelled``, Claude Code ``Deny(interrupt=True)``, T3 and
  MCP ``cancel``; elsewhere a denial and an interrupt of the turn.

A denied call is reported back to the model (``Deny.reason``) and the turn goes on, unless
the denial stops it. A decision with ``scope="session"`` (ACP's ``allow_always`` and
``reject_always``) answers that tool's later calls in the same session without asking:
the session remembers it (``SessionOverrides.tool_decisions``), for every agent type alike.
"""

from dataclasses import dataclass
from typing import Any, Literal


type DecisionScope = Literal["once", "session"]
"""What a decision covers: this call, or every call of the tool in the session."""

type RememberedDecision = Literal["approve", "deny"]
"""A decision a session keeps for a tool (``scope="session"``)."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Approve:
    """Run the tool call."""

    scope: DecisionScope = "once"
    """This call, or every call of the tool in the session."""

    args: dict[str, Any] | None = None
    """Arguments to run the call with instead of the model's (the user edited them)."""

    def remembered(self) -> RememberedDecision:
        """How a session keeps this decision."""
        return "approve"


@dataclass(frozen=True, slots=True, kw_only=True)
class Deny:
    """Don't run the tool call: the model is told, and the turn goes on unless it stops."""

    scope: DecisionScope = "once"
    """This call, or every call of the tool in the session."""

    message: str | None = None
    """The user's word to the model, e.g. what to do instead."""

    stop: bool = False
    """End the turn too, as if the user had cancelled it."""

    def remembered(self) -> RememberedDecision:
        """How a session keeps this decision."""
        return "deny"

    def reason(self, tool_name: str | None) -> str:
        """What the model is told instead of the call's result."""
        name = repr(tool_name) if tool_name else "the tool"
        if self.stop:
            return f"The user stopped the turn instead of running {name}."
        if self.message:
            return f"The user declined to run {name}: {self.message}"
        return f"The user declined to run {name}."


type ToolDecision = Approve | Deny
"""The user's decision on a tool call."""


def from_remembered(remembered: RememberedDecision) -> ToolDecision:
    """The decision a session kept for a tool, applied to one more call."""
    match remembered:
        case "approve":
            return Approve()
        case "deny":
            return Deny()
