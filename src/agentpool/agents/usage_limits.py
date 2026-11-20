"""Subscription usage limits of an agent backend (e.g. Claude plan rate-limit windows)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal


if TYPE_CHECKING:
    from datetime import datetime


UsagePeriod = Literal["session", "weekly", "monthly", "other"]


@dataclass(frozen=True, kw_only=True)
class UsageWindow:
    """One rate-limit window or meter of a plan."""

    id: str
    """Stable identifier, e.g. ``session`` or ``weekly_scoped:Opus``."""
    label: str
    """Human-readable name, e.g. ``Weekly (Opus)``."""
    period: UsagePeriod
    """Length of the window."""
    percent: float
    """Share of the window used, 0-100."""
    resets_at: datetime | None = None
    """When the window resets."""
    limited: bool = False
    """Whether the limit is reached (requests are rejected or use extra credits)."""


@dataclass(frozen=True, kw_only=True)
class UsageLimits:
    """Usage of the plan the agent backend runs on."""

    windows: list[UsageWindow] = field(default_factory=list)
    """Windows in the backend's order (the most relevant first)."""
    plan: str | None = None
    """Plan name, e.g. ``pro``."""
    usage_url: str | None = None
    """Page where the user can see and manage their usage."""
