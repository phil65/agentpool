"""Plan usage limits of the session agents as OpenCode ``UsageLimitsInfo``.

OpenCode's model is its own console: a workspace with dollar budgets and, for OpenCode Go
subscriptions, three meters (rolling, weekly, monthly). Agent plan windows map onto the
meters: session -> rolling, the first weekly window -> weekly, a monthly window (Claude's
extra-usage credits) -> monthly. A plan without a monthly window reports it as unused.
"""

from typing import TYPE_CHECKING, Any

from agentpool.log import get_logger
from agentpool_server.opencode_v2_server.catalog import leaf_agents
from opencode_v2_sdk.models import (
    UsageLimitsGo,
    UsageLimitsInfo,
    UsageLimitsInfoWorkspace,
    UsageLimitsMeter,
)


if TYPE_CHECKING:
    from agentpool.agents.usage_limits import UsageLimits, UsagePeriod, UsageWindow
    from agentpool.messaging import MessageNode


logger = get_logger(__name__)


def _meter(window: UsageWindow | None) -> UsageLimitsMeter:
    if window is None:
        return UsageLimitsMeter(percent=0, limited=False, resets_at=0)
    resets_at = round(window.resets_at.timestamp() * 1000) if window.resets_at else 0
    return UsageLimitsMeter(percent=window.percent, limited=window.limited, resets_at=resets_at)


def to_usage_info(agent_name: str, limits: UsageLimits) -> UsageLimitsInfo | None:
    """OpenCode usage info (None if the plan has neither a session nor a weekly window)."""

    def first(period: UsagePeriod) -> UsageWindow | None:
        return next((w for w in limits.windows if w.period == period), None)

    session, weekly = first("session"), first("weekly")
    if session is None and weekly is None:
        return None
    name = f"{agent_name} ({limits.plan})" if limits.plan else agent_name
    workspace = UsageLimitsInfoWorkspace(id=agent_name, name=name, usage_url=limits.usage_url or "")
    go = UsageLimitsGo(
        rolling=_meter(session), weekly=_meter(weekly), monthly=_meter(first("monthly"))
    )
    return UsageLimitsInfo(workspace=workspace, go=go, budgets=[])


async def usage_info(node: MessageNode[Any, Any] | None) -> UsageLimitsInfo | None:
    """Usage info of the first agent of a node that reports plan limits."""
    for agent in leaf_agents(node) if node is not None else []:
        try:
            limits = await agent.get_usage_limits()
        except Exception:
            logger.warning("Could not read usage limits", agent=agent.name, exc_info=True)
            continue
        if limits is not None and (info := to_usage_info(agent.name, limits)) is not None:
            return info
    return None
