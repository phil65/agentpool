"""Construction and serialization helpers for OpenCode V2 events."""

from typing import TYPE_CHECKING, Any, TypeIs, get_args

from agentpool.utils import identifiers
from agentpool.utils.time_utils import now_ms
from opencode_v2_sdk.models import (
    SessionAgentSelected,
    SessionArchived,
    SessionCompactionEnded,
    SessionCompactionFailed,
    SessionCompactionStarted,
    SessionCreated,
    SessionDeleted,
    SessionExecutionFailed,
    SessionExecutionInterrupted,
    SessionExecutionStarted,
    SessionExecutionSucceeded,
    SessionForked,
    SessionInboxCancelled,
    SessionInboxDelivered,
    SessionInboxDeliveryChanged,
    SessionInboxEnqueued,
    SessionInstructionsUpdated,
    SessionMessageContentUpdated,
    SessionMetadataUpdated,
    SessionModelSelected,
    SessionMoved,
    SessionPermissions,
    SessionReasoningEnded,
    SessionReasoningStarted,
    SessionRenamed,
    SessionRetryScheduled,
    SessionRevertCleared,
    SessionRevertCommitted,
    SessionRevertStaged,
    SessionShellEnded,
    SessionShellStarted,
    SessionSkillActivated,
    SessionStepEnded,
    SessionStepFailed,
    SessionStepFirstToken,
    SessionStepStarted,
    SessionStepStreamed,
    SessionSynthetic,
    SessionTextEnded,
    SessionTextStarted,
    SessionToolCalled,
    SessionToolFailed,
    SessionToolInputEnded,
    SessionToolInputStarted,
    SessionToolSuccess,
    SessionUnarchived,
    SessionUsageRecorded,
    SessionViewed,
)


if TYPE_CHECKING:
    from pydantic import BaseModel


type DurableSessionEvent = (
    SessionCreated
    | SessionAgentSelected
    | SessionModelSelected
    | SessionMoved
    | SessionRenamed
    | SessionMetadataUpdated
    | SessionPermissions
    | SessionViewed
    | SessionArchived
    | SessionUnarchived
    | SessionDeleted
    | SessionForked
    | SessionInboxDelivered
    | SessionInboxEnqueued
    | SessionInboxCancelled
    | SessionInboxDeliveryChanged
    | SessionExecutionStarted
    | SessionExecutionSucceeded
    | SessionExecutionFailed
    | SessionExecutionInterrupted
    | SessionInstructionsUpdated
    | SessionSynthetic
    | SessionSkillActivated
    | SessionShellStarted
    | SessionShellEnded
    | SessionStepStarted
    | SessionStepFirstToken
    | SessionStepStreamed
    | SessionStepEnded
    | SessionStepFailed
    | SessionTextStarted
    | SessionTextEnded
    | SessionReasoningStarted
    | SessionReasoningEnded
    | SessionToolInputStarted
    | SessionToolInputEnded
    | SessionToolCalled
    | SessionToolSuccess
    | SessionToolFailed
    | SessionRetryScheduled
    | SessionCompactionStarted
    | SessionCompactionEnded
    | SessionCompactionFailed
    | SessionRevertStaged
    | SessionRevertCleared
    | SessionRevertCommitted
    | SessionUsageRecorded
    | SessionMessageContentUpdated
)
"""Durable session events: persisted in the session log and projected into messages."""

DURABLE_EVENT_TYPES: tuple[type[BaseModel], ...] = get_args(DurableSessionEvent.__value__)
"""Runtime tuple of the durable event classes (for isinstance checks)."""


def is_durable_event(event: object) -> TypeIs[DurableSessionEvent]:
    """Whether an event model is a durable session event."""
    return isinstance(event, DURABLE_EVENT_TYPES)


def new_event_id() -> str:
    """Create a new, chronologically sortable event ID (``evt_...``)."""
    return identifiers.ascending("event")


def new_message_id() -> str:
    """Create a new, chronologically sortable message ID (``msg_...``)."""
    return identifiers.ascending("message")


def new_session_id() -> str:
    """Create a new session ID (``ses_...``)."""
    return identifiers.ascending("session")


def message_id_from_event(event_id: str) -> str:
    """Derive the message ID OpenCode uses for messages projected from an event."""
    return "msg_" + event_id.removeprefix("evt_")


def is_durable(event_cls: type[BaseModel]) -> bool:
    """Whether events of this class carry a durable ``seq`` (i.e. go into the session log)."""
    return "durable" in event_cls.model_fields


def build_event[E: BaseModel](
    event_cls: type[E],
    data: BaseModel,
    *,
    aggregate_id: str | None = None,
    seq: int | None = None,
    created: int | None = None,
    metadata: dict[str, Any] | None = None,
    directory: str | None = None,
) -> E:
    """Build an event model of the given class.

    Args:
        event_cls: The generated event class (e.g. ``SessionTextDelta``)
        data: The event's ``data`` payload model
        aggregate_id: Aggregate (session) ID, required for durable events
        seq: Sequence number within the aggregate, required for durable events
        created: Creation time in epoch milliseconds (defaults to now)
        metadata: Optional event metadata
        directory: Location directory attached to the event envelope

    Returns:
        The validated event model.
    """
    payload: dict[str, Any] = {"id": new_event_id(), "data": data}
    if "created" in event_cls.model_fields:
        payload["created"] = created if created is not None else now_ms()
    if metadata is not None:
        payload["metadata"] = metadata
    if directory is not None and "location" in event_cls.model_fields:
        payload["location"] = {"directory": directory}
    if is_durable(event_cls):
        if aggregate_id is None or seq is None:
            msg = f"{event_cls.__name__} is durable and needs aggregate_id and seq"
            raise ValueError(msg)
        payload["durable"] = {"aggregateID": aggregate_id, "seq": seq}
    return event_cls.model_validate(payload)


def dump_model(model: BaseModel) -> dict[str, Any]:
    """Serialize a protocol model to its JSON wire form."""
    return model.model_dump(mode="json", by_alias=True, exclude_none=True)
