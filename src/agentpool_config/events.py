"""Event sources for AgentPool."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, Literal

from evented_config import (
    EmailConfig,
    EventSourceConfig,
    FileWatchConfig,
    TimeEventConfig,
    WebhookConfig,
)
from pydantic import ConfigDict, Field
from schemez import Schema


if TYPE_CHECKING:
    from agentpool.messaging.events import ConnectionEventData


TriggerSessionPolicy = Literal["target", "standing", "per_event"]
"""Which session a trigger's events run in.

- ``target``: the node's own session (the default).
- ``standing``: one session for the trigger, kept and resumed (``trigger/<node>/<trigger>``).
- ``per_event``: a new session for every event.

Other than ``target`` only for agents: a team runs its members in sessions of its own.
"""


class _TriggerSession(Schema):
    session: TriggerSessionPolicy = Field(
        default="target",
        examples=["target", "standing", "per_event"],
        title="Session",
    )
    """Which session the trigger's events run in (see ``TriggerSessionPolicy``)."""


class FileWatchTriggerConfig(FileWatchConfig, _TriggerSession):
    """File watch trigger."""


class WebhookTriggerConfig(WebhookConfig, _TriggerSession):
    """Webhook trigger."""


class EmailTriggerConfig(EmailConfig, _TriggerSession):
    """Email trigger."""


class TimeTriggerConfig(TimeEventConfig, _TriggerSession):
    """Scheduled (cron) trigger."""


ConnectionEventType = Literal[
    "message_received",
    "message_processed",
    "message_forwarded",
    "queue_filled",
    "queue_triggered",
]


class ConnectionTriggerConfig(EventSourceConfig, _TriggerSession):
    """Trigger config specifically for connection events."""

    model_config = ConfigDict(title="Connection Trigger")

    type: Literal["connection"] = Field("connection", init=False)
    """Connection event trigger."""

    source: str | None = Field(
        default=None,
        examples=["main_agent", "web_scraper", "data_processor"],
        title="Connection source",
    )
    """Connection source name."""

    target: str | None = Field(
        default=None,
        examples=["output_handler", "notification_agent"],
        title="Connection target",
    )
    """Connection to trigger."""

    event: ConnectionEventType = Field(
        examples=["message_received", "message_processed", "queue_filled"],
        title="Event type",
    )
    """Event type to trigger on."""

    condition: ConnectionEventConditionType | None = Field(default=None, title="Event condition")
    """Condition-based filter for the event."""

    async def matches_event(self, event: ConnectionEventData[Any]) -> bool:
        """Check if this trigger matches the event."""
        # First check event type
        if event.event_type != self.event:
            return False

        # Check source/target filters
        if self.source and event.connection.source.name != self.source:
            return False
        if self.target and not any(t.name == self.target for t in event.connection.targets):
            return False

        # Check condition if any
        if self.condition:
            return await self.condition.check(event)

        return True


EventConfig = Annotated[
    FileWatchTriggerConfig
    | WebhookTriggerConfig
    | EmailTriggerConfig
    | TimeTriggerConfig
    | ConnectionTriggerConfig,
    Field(discriminator="type"),
]


class ConnectionEventCondition(Schema):
    """Base conditions specifically for connection events."""

    type: str = Field(init=False, title="Condition type")

    async def check(self, event: ConnectionEventData[Any]) -> bool:
        raise NotImplementedError


class ConnectionContentCondition(ConnectionEventCondition):
    """Simple content matching for connection events."""

    model_config = ConfigDict(title="Content Condition")

    type: Literal["content"] = Field("content", init=False)
    """Content-based trigger."""

    words: list[str] = Field(
        examples=[["error", "warning"], ["complete", "finished"], ["urgent"]],
        title="Match words",
    )
    """List of words to match."""

    mode: Literal["any", "all"] = Field(
        default="any",
        examples=["any", "all"],
        title="Matching mode",
    )
    """Matching mode."""

    async def check(self, event: ConnectionEventData[Any]) -> bool:
        if not event.message:
            return False
        text = str(event.message.content)
        return any(word in text for word in self.words)


class ConnectionJinja2Condition(ConnectionEventCondition):
    """Flexible Jinja2 condition for connection events."""

    model_config = ConfigDict(title="Jinja2 Condition")

    type: Literal["jinja2"] = Field("jinja2", init=False)
    """Jinja2-based trigger configuration."""

    template: str = Field(
        examples=[
            "{{ event.message.content | length > 100 }}",
            "{{ 'urgent' in event.message.content }}",
        ],
        title="Jinja2 condition template",
    )
    """Jinja2-Template (needs to return a "boolean" string)."""

    async def check(self, event: ConnectionEventData[Any]) -> bool:
        from jinjarope import Environment

        env = Environment(enable_async=True)
        template = env.from_string(self.template)
        result = await template.render_async(event=event)
        return result.strip().lower() == "true"


ConnectionEventConditionType = Annotated[
    ConnectionContentCondition | ConnectionJinja2Condition,
    Field(discriminator="type"),
]
