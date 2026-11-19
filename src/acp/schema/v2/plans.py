"""v2 plans: item-based ``plan_update`` and ``plan_removed`` keyed by plan ID."""

from __future__ import annotations

from collections.abc import Sequence  # noqa: TC003
from typing import Annotated, Literal

from pydantic import MISSING, Discriminator, Field, Tag

from acp.schema.v2.base import OTHER_TAG, AnnotatedObject, Unknown, open_discriminator
from acp.schema.v2.common import AbsolutePath, PlanId  # noqa: TC001


PlanEntryPriority = Literal["high", "medium", "low"] | str
"""Relative importance of a plan entry. Values starting with ``_`` are extensions."""

PlanEntryStatus = Literal["pending", "in_progress", "completed", "cancelled"] | str
"""Status of a plan entry. Values starting with ``_`` are extensions."""


class PlanEntry(AnnotatedObject):
    """A task or goal the agent intends to accomplish."""

    content: str
    """What the task aims to accomplish."""

    priority: PlanEntryPriority
    """Relative importance of the task."""

    status: PlanEntryStatus
    """Current status of the task."""


class PlanItems(AnnotatedObject):
    """A plan as structured entries."""

    type: Literal["items"] = Field(default="items", init=False)

    plan_id: PlanId
    """The plan to update."""

    entries: Sequence[PlanEntry]
    """All tasks of the plan; updates send the complete list, which replaces the old one."""


class PlanMarkdown(AnnotatedObject):
    """**UNSTABLE**: A plan as raw markdown content."""

    type: Literal["markdown"] = Field(default="markdown", init=False)

    plan_id: PlanId
    """The plan to update."""

    content: str
    """Markdown content of the plan."""

    path: AbsolutePath | MISSING | None = MISSING
    """Absolute path of the file holding the plan."""


class UnknownPlanContent(Unknown):
    """Custom or future plan content; ``type`` values starting with ``_`` are extensions."""

    type: str
    """The content kind."""

    plan_id: PlanId
    """The plan to update."""


PlanUpdateContent = Annotated[
    Annotated[PlanItems, Tag("items")]
    | Annotated[PlanMarkdown, Tag("markdown")]
    | Annotated[UnknownPlanContent, Tag(OTHER_TAG)],
    Discriminator(open_discriminator("type", frozenset({"items", "markdown"}))),
]
"""Updated content of a plan."""


class PlanUpdate(AnnotatedObject):
    """New content of the plan identified by its ``plan_id``."""

    session_update: Literal["plan_update"] = Field(default="plan_update", init=False)

    plan: PlanUpdateContent
    """The updated plan content."""


class PlanRemoved(AnnotatedObject):
    """**UNSTABLE**: The plan with this ID was removed."""

    session_update: Literal["plan_removed"] = Field(default="plan_removed", init=False)

    plan_id: PlanId
    """The removed plan."""
