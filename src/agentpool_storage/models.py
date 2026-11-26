"""Data classes for storing agent data."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, TypedDict


if TYPE_CHECKING:
    from datetime import datetime

    from pydantic_ai import RunUsage

    from agentpool.messaging import ChatMessage


GroupBy = Literal["agent", "model", "hour", "day"]


class ConversationData(TypedDict):
    """Formatted conversation data."""

    id: str
    """Unique identifier for the conversation"""

    agent: str
    """Name of the agent that handled this conversation"""

    title: str | None
    """AI-generated or user-provided conversation title"""

    start_time: str
    """When the conversation started (ISO format)"""

    messages: list[ChatMessage[Any]]
    """List of messages in this conversation"""

    token_usage: RunUsage | None
    """Aggregated token usage for the entire conversation"""


@dataclass
class QueryFilters:
    """Filters for conversation queries."""

    agent_name: str | None = None
    """Filter by specific agent name"""

    since: datetime | None = None
    """Only include conversations after this time"""

    query: str | None = None
    """Search term to filter message content"""

    model: str | None = None
    """Filter by model name"""

    limit: int | None = None
    """Maximum number of conversations to return"""

    cwd: str | None = None
    """Filter by working directory (project path)"""


@dataclass
class StatsFilters:
    """Filters for statistics queries."""

    cutoff: datetime
    """Only include data after this time"""

    group_by: GroupBy
    """How to group the statistics (agent/model/hour/day)"""

    agent_name: str | None = None
    """Filter statistics to specific agent"""
