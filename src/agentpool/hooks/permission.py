"""Static permission hooks (used to map agent-file permission rules onto hooks)."""

from typing import TYPE_CHECKING, Any, Literal


if TYPE_CHECKING:
    from agentpool.hooks.base import HookResult


async def fixed_decision(
    *,
    decision: Literal["allow", "deny", "ask"],
    reason: str = "",
    **_: Any,
) -> HookResult:
    """Return the same decision for every matching tool call.

    Meant for a ``callable`` pre_tool_use hook with a ``matcher``:
    ``{"type": "callable", "import_path": "agentpool.hooks.permission.fixed_decision",
    "matcher": "^(write|edit)$", "arguments": {"decision": "deny"}}``.

    Args:
        decision: The decision for every call the hook matches
        reason: Explanation shown for the decision
    """
    return {"decision": decision, "reason": reason or f"Permission rule: {decision}"}
