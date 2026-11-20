"""Session operations that rewrite or derive session state: fork, revert, import, stats."""

from __future__ import annotations

from collections import Counter, defaultdict
import dataclasses
from datetime import UTC, datetime
import json
import statistics
from typing import TYPE_CHECKING, Any, Literal
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import TypeAdapter

from agentpool.log import get_logger
from agentpool_server.opencode_v2_server.events import (
    DurableSessionEvent,
    dump_model,
    new_event_id,
)
from agentpool_server.opencode_v2_server.projector import SessionProjection
from agentpool_server.opencode_v2_server.routes.files import parse_unified_diff
from opencode_v2_sdk.models import (
    FileDiffInfo,
    SessionAgentSelected,
    SessionAgentSelectedData,
    SessionArchived,
    SessionCompactionEnded,
    SessionCompactionEndedData,
    SessionCompactionFailed,
    SessionCompactionFailedData,
    SessionCompactionStarted,
    SessionCompactionStartedData,
    SessionCreated,
    SessionDeleted,
    SessionExecutionFailed,
    SessionExecutionFailedData,
    SessionExecutionInterrupted,
    SessionExecutionInterruptedData,
    SessionExecutionStarted,
    SessionExecutionStartedData,
    SessionExecutionSucceeded,
    SessionExecutionSucceededData,
    SessionForked,
    SessionForkedData,
    SessionInboxDelivered,
    SessionInboxDeliveredData,
    SessionInboxEnqueued,
    SessionInboxEnqueuedData,
    SessionInboxItemSynthetic,
    SessionInboxItemUser,
    SessionInboxSyntheticPayload,
    SessionInboxUserPayload,
    SessionInstructionsUpdated,
    SessionInstructionsUpdatedData,
    SessionMessageAgentSelected,
    SessionMessageAssistant,
    SessionMessageAssistantReasoning,
    SessionMessageAssistantText,
    SessionMessageAssistantTool,
    SessionMessageCompactionCompleted,
    SessionMessageCompactionFailed,
    SessionMessageCompactionRunning,
    SessionMessageIdle,
    SessionMessageLocationSwitched,
    SessionMessageModelSelected,
    SessionMessageShell,
    SessionMessageSkill,
    SessionMessageSynthetic,
    SessionMessageSystem,
    SessionMessageToolStateCompleted,
    SessionMessageToolStateError,
    SessionMessageToolStateRunning,
    SessionMessageToolStateStreaming,
    SessionMessageUser,
    SessionModelSelected,
    SessionModelSelectedData,
    SessionMoved,
    SessionMovedData,
    SessionReasoningEnded,
    SessionReasoningEndedData,
    SessionReasoningStarted,
    SessionReasoningStartedData,
    SessionRevertCleared,
    SessionRevertCommitted,
    SessionRevertStaged,
    SessionShellEnded,
    SessionShellEndedData,
    SessionShellEndedDataOutput,
    SessionShellStarted,
    SessionShellStartedData,
    SessionSkillActivated,
    SessionSkillActivatedData,
    SessionStatsActivity,
    SessionStatsInfo,
    SessionStatsInfoRange,
    SessionStatsModelUsage,
    SessionStatsToolsVariant0,
    SessionStatsToolsVariant1,
    SessionStatsToolsVariant2,
    SessionStatsToolTotals,
    SessionStatsToolUsage,
    SessionStepEnded,
    SessionStepEndedData,
    SessionStepFailed,
    SessionStepFailedData,
    SessionStepStarted,
    SessionStepStartedData,
    SessionTextEnded,
    SessionTextEndedData,
    SessionTextStarted,
    SessionTextStartedData,
    SessionToolCalled,
    SessionToolCalledData,
    SessionToolFailed,
    SessionToolFailedData,
    SessionToolInputEnded,
    SessionToolInputEndedData,
    SessionToolInputStarted,
    SessionToolInputStartedData,
    SessionToolSuccess,
    SessionToolSuccessData,
    SessionUnarchived,
    SessionViewed,
    ShellInfo,
    ShellInfoTime,
    TokenUsageInfo,
    TokenUsageInfoCache,
    ToolTextContent,
)


if TYPE_CHECKING:
    from collections.abc import Sequence

    from agentpool.messaging import ChatMessage
    from agentpool.utils.file_ops_tracker import FileChange, FileOpsTracker
    from agentpool_server.opencode_v2_server.session import V2Session
    from opencode_v2_sdk.models import SessionMessageInfo


logger = get_logger(__name__)

_EVENT_ADAPTER: TypeAdapter[DurableSessionEvent] = TypeAdapter(DurableSessionEvent)

NOT_COPIED_ON_FORK = (
    SessionCreated,
    SessionDeleted,
    SessionForked,
    SessionArchived,
    SessionUnarchived,
    SessionViewed,
    SessionRevertStaged,
    SessionRevertCleared,
    SessionRevertCommitted,
)


# --- fork ----------------------------------------------------------------------------------


def rekey_event(event: DurableSessionEvent, session_id: str, seq: int) -> DurableSessionEvent:
    """Copy a durable event into another session (new ID, aggregate and sequence)."""
    raw = dump_model(event)
    raw["id"] = new_event_id()
    raw["durable"] = {**raw["durable"], "aggregateID": session_id, "seq": seq}
    raw["data"] = {**raw["data"], "sessionID": session_id}
    return _EVENT_ADAPTER.validate_python(raw)


def fork_events(
    source: V2Session,
    boundary: Literal["before", "through"] | None,
    message_id: str | None,
) -> list[DurableSessionEvent]:
    """Durable events of ``source`` that reproduce its messages up to a boundary.

    Args:
        source: The session to fork
        boundary: ``before`` (exclude the message) or ``through`` (include it), None for all
        message_id: Boundary message
    """
    probe = SessionProjection(session_id=source.id)
    copied: list[DurableSessionEvent] = []
    for event in source.log:
        if isinstance(event, NOT_COPIED_ON_FORK):
            continue
        probe.apply(event)
        if boundary is not None and message_id is not None:
            index = probe.message_index(message_id)
            if boundary == "before" and index is not None:
                break
            if boundary == "through" and index is not None and index < len(probe.messages) - 1:
                break
        copied.append(event)
    return copied


def user_turns(messages: Sequence[SessionMessageInfo]) -> int:
    """Number of user messages."""
    return sum(1 for m in messages if isinstance(m, SessionMessageUser))


def history_prefix(history: Sequence[ChatMessage[Any]], turns: int) -> list[ChatMessage[Any]]:
    """Stored conversation messages before the ``turns + 1``-th user message."""
    result: list[ChatMessage[Any]] = []
    seen = 0
    for message in history:
        if message.role == "user":
            seen += 1
            if seen > turns:
                break
        result.append(message)
    return result


def copy_history(history: Sequence[ChatMessage[Any]], session_id: str) -> list[ChatMessage[Any]]:
    """Copies of conversation messages, re-assigned to another session."""
    return [
        dataclasses.replace(m, session_id=session_id, message_id=str(uuid.uuid4())) for m in history
    ]


def forked_event_data(
    session_id: str, parent_id: str, boundary_type: Literal["before", "through"], message_id: str
) -> SessionForkedData:
    """Payload of ``session.forked``."""
    return SessionForkedData.model_validate({
        "sessionID": session_id,
        "parentID": parent_id,
        "boundary": {"type": boundary_type, "messageID": message_id},
    })


# --- files (diff / revert) -----------------------------------------------------------------


def message_time(session: V2Session, message_id: str) -> float | None:
    """Creation time (epoch ms) of a message."""
    message = session.projection.get_message(message_id)
    return message.time.created if message is not None else None


def changes_between(
    tracker: FileOpsTracker, start_ms: float | None, end_ms: float | None = None
) -> list[FileChange]:
    """File changes recorded in a time window (epoch ms, end exclusive)."""
    start = (start_ms or 0) / 1000
    end = end_ms / 1000 if end_ms is not None else None
    return [
        change
        for change in tracker.changes
        if change.timestamp >= start and (end is None or change.timestamp < end)
    ]


def changes_to_diffs(changes: Sequence[FileChange]) -> list[FileDiffInfo]:
    """Per-file diffs of a sequence of changes (first old content -> last new content)."""
    from agentpool.utils.diffs import compute_unified_diff

    first: dict[str, str | None] = {}
    last: dict[str, str | None] = {}
    for change in changes:
        first.setdefault(change.path, change.old_content)
        last[change.path] = change.new_content
    diffs: list[FileDiffInfo] = []
    for path, before in first.items():
        after = last[path]
        patch = compute_unified_diff(
            before or "", after or "", fromfile=f"a/{path}", tofile=f"b/{path}"
        )
        header = f"diff --git a/{path} b/{path}\n"
        if before is None:
            header += "new file mode 100644\n"
        elif after is None:
            header += "deleted file mode 100644\n"
        parsed = parse_unified_diff(header + patch)
        if parsed:
            diffs.append(parsed[0])
        else:
            status: Literal["added", "deleted", "modified"] = (
                "added" if before is None else "deleted" if after is None else "modified"
            )
            diffs.append(FileDiffInfo(file=path, patch="", additions=0, deletions=0, status=status))
    return diffs


def revert_operations(changes: Sequence[FileChange]) -> list[tuple[str, str | None]]:
    """``(path, content)`` restoring files to their state before the changes (None = delete)."""
    original: dict[str, str | None] = {}
    for change in changes:
        original.setdefault(change.path, change.old_content)
    return list(original.items())


# --- instructions --------------------------------------------------------------------------


def instruction_update(
    session_id: str, key: str, value: Any | None
) -> tuple[SessionInstructionsUpdatedData, str]:
    """Event payload and model-facing text for setting (or removing, value None) an entry."""
    if value is None:
        text = f"Session instruction {key!r} was removed."
        delta: dict[str, str] = {key: "removed"}
    else:
        encoded = json.dumps(value)
        text = f"Session instruction {key!r}: {encoded}"
        delta = {key: encoded}
    return SessionInstructionsUpdatedData(session_id=session_id, delta=delta, text=text), text


# --- import --------------------------------------------------------------------------------

type EmitArgs = tuple[type[Any], Any, float]


def _zero() -> TokenUsageInfo:
    return TokenUsageInfo(
        input=0, output=0, reasoning=0, cache=TokenUsageInfoCache(read=0, write=0)
    )


def _assistant_events(sid: str, message: SessionMessageAssistant) -> list[EmitArgs]:
    created = message.time.created
    mid = message.id
    events: list[EmitArgs] = [
        (
            SessionStepStarted,
            SessionStepStartedData(
                session_id=sid,
                assistant_message_id=mid,
                agent=message.agent,
                model=message.model,
                started=int(created),
            ),
            created,
        )
    ]
    text_ordinal = reasoning_ordinal = 0
    for part in message.content:
        match part:
            case SessionMessageAssistantText(text=text, native=native):
                events.append((
                    SessionTextStarted,
                    SessionTextStartedData(
                        session_id=sid, assistant_message_id=mid, ordinal=text_ordinal
                    ),
                    created,
                ))
                events.append((
                    SessionTextEnded,
                    SessionTextEndedData(
                        session_id=sid,
                        assistant_message_id=mid,
                        ordinal=text_ordinal,
                        text=text,
                        native=native,
                    ),
                    created,
                ))
                text_ordinal += 1
            case SessionMessageAssistantReasoning(text=text, native=native):
                events.append((
                    SessionReasoningStarted,
                    SessionReasoningStartedData(
                        session_id=sid, assistant_message_id=mid, ordinal=reasoning_ordinal
                    ),
                    created,
                ))
                events.append((
                    SessionReasoningEnded,
                    SessionReasoningEndedData(
                        session_id=sid,
                        assistant_message_id=mid,
                        ordinal=reasoning_ordinal,
                        text=text,
                        native=native,
                    ),
                    created,
                ))
                reasoning_ordinal += 1
            case SessionMessageAssistantTool():
                events.extend(_tool_events(sid, mid, part))
    if message.error is not None:
        events.append((
            SessionStepFailed,
            SessionStepFailedData(
                session_id=sid,
                assistant_message_id=mid,
                error=message.error,
                cost=message.cost,
                tokens=message.tokens,
            ),
            message.time.completed or created,
        ))
    else:
        finish = message.finish or "stop"
        events.append((
            SessionStepEnded,
            SessionStepEndedData(
                session_id=sid,
                assistant_message_id=mid,
                finish=finish,
                cost=message.cost or 0.0,
                tokens=message.tokens or _zero(),
            ),
            message.time.completed or created,
        ))
    return events


def _tool_events(sid: str, mid: str, part: SessionMessageAssistantTool) -> list[EmitArgs]:
    created = part.time.created
    events: list[EmitArgs] = [
        (
            SessionToolInputStarted,
            SessionToolInputStartedData(
                session_id=sid, assistant_message_id=mid, id=part.id, name=part.name
            ),
            created,
        )
    ]
    match part.state:
        case SessionMessageToolStateStreaming(input=text):
            events.append((
                SessionToolInputEnded,
                SessionToolInputEndedData(
                    session_id=sid, assistant_message_id=mid, id=part.id, text=text
                ),
                created,
            ))
            return events
        case (
            SessionMessageToolStateRunning(input=tool_input)
            | SessionMessageToolStateCompleted(input=tool_input)
            | SessionMessageToolStateError(input=tool_input)
        ):
            events.append((
                SessionToolInputEnded,
                SessionToolInputEndedData(
                    session_id=sid,
                    assistant_message_id=mid,
                    id=part.id,
                    text=json.dumps(tool_input),
                ),
                created,
            ))
            events.append((
                SessionToolCalled,
                SessionToolCalledData(
                    session_id=sid,
                    assistant_message_id=mid,
                    id=part.id,
                    input=tool_input,
                    executed=part.executed is not False,
                ),
                part.time.ran or created,
            ))
    completed = part.time.completed or created
    match part.state:
        case SessionMessageToolStateCompleted(content=content, metadata=metadata):
            events.append((
                SessionToolSuccess,
                SessionToolSuccessData(
                    session_id=sid,
                    assistant_message_id=mid,
                    id=part.id,
                    content=content or [ToolTextContent(text="")],
                    metadata=metadata,
                    executed=part.executed is not False,
                ),
                completed,
            ))
        case SessionMessageToolStateError(error=error, content=content, metadata=metadata):
            events.append((
                SessionToolFailed,
                SessionToolFailedData(
                    session_id=sid,
                    assistant_message_id=mid,
                    id=part.id,
                    error=error,
                    content=content,
                    metadata=metadata,
                    executed=part.executed is not False,
                ),
                completed,
            ))
        case SessionMessageToolStateRunning() | SessionMessageToolStateStreaming():
            pass
    return events


def message_events(  # noqa: PLR0911
    sid: str, message: SessionMessageInfo, directory: str, project_id: str
) -> list[EmitArgs]:
    """Durable events (class, data, created) that project into the given message."""
    created = message.time.created
    match message:
        case SessionMessageUser():
            payload = SessionInboxUserPayload(
                text=message.text,
                files=message.files,
                agents=message.agents,
                skills=message.skills,
                metadata=message.metadata,
            )
            item = SessionInboxItemUser(payload=payload, delivery="queue")
            return [
                (
                    SessionInboxEnqueued,
                    SessionInboxEnqueuedData(session_id=sid, inbox_id=message.id, item=item),
                    created,
                ),
                (SessionExecutionStarted, SessionExecutionStartedData(session_id=sid), created),
                (
                    SessionInboxDelivered,
                    SessionInboxDeliveredData(session_id=sid, inbox_id=message.id),
                    created,
                ),
            ]
        case SessionMessageSynthetic():
            synthetic = SessionInboxItemSynthetic(
                payload=SessionInboxSyntheticPayload(
                    text=message.text, description=message.description, metadata=message.metadata
                ),
                delivery="queue",
            )
            return [
                (
                    SessionInboxEnqueued,
                    SessionInboxEnqueuedData(session_id=sid, inbox_id=message.id, item=synthetic),
                    created,
                ),
                (
                    SessionInboxDelivered,
                    SessionInboxDeliveredData(session_id=sid, inbox_id=message.id),
                    created,
                ),
            ]
        case SessionMessageAssistant():
            return _assistant_events(sid, message)
        case SessionMessageIdle(outcome="succeeded"):
            return [
                (SessionExecutionSucceeded, SessionExecutionSucceededData(session_id=sid), created)
            ]
        case SessionMessageIdle(outcome="failed", error=error):
            return [
                (
                    SessionExecutionFailed,
                    SessionExecutionFailedData.model_validate({
                        "sessionID": sid,
                        "error": (
                            error.model_dump() if error else {"type": "unknown", "message": ""}
                        ),
                    }),
                    created,
                )
            ]
        case SessionMessageIdle():
            return [
                (
                    SessionExecutionInterrupted,
                    SessionExecutionInterruptedData(session_id=sid, reason="user"),
                    created,
                )
            ]
        case SessionMessageAgentSelected(agent=agent, previous=previous):
            data = SessionAgentSelectedData(session_id=sid, agent=agent, previous=previous)
            return [(SessionAgentSelected, data, created)]
        case SessionMessageModelSelected(model=model, previous=previous):
            data_m = SessionModelSelectedData(session_id=sid, model=model, previous=previous)
            return [(SessionModelSelected, data_m, created)]
        case SessionMessageLocationSwitched(location=location, project_id=pid, subpath=subpath):
            moved = SessionMovedData.model_validate({
                "sessionID": sid,
                "location": {"directory": location.directory},
                "projectID": pid or project_id,
                "subpath": subpath,
            })
            return [(SessionMoved, moved, created)]
        case SessionMessageSystem(text=text):
            data_s = SessionInstructionsUpdatedData(session_id=sid, delta={}, text=text)
            return [(SessionInstructionsUpdated, data_s, created)]
        case SessionMessageSkill(skill=skill, name=name, text=text):
            data_k = SessionSkillActivatedData(session_id=sid, id=skill, name=name, text=text)
            return [(SessionSkillActivated, data_k, created)]
        case SessionMessageShell():
            info = ShellInfo(
                id=message.shell_id,
                status=message.status,
                command=message.command,
                cwd=directory,
                shell="sh",
                file="sh",
                exit=message.exit if isinstance(message.exit, float | int) else None,
                metadata={},
                time=ShellInfoTime(started=created, completed=message.time.completed),
            )
            events: list[EmitArgs] = [
                (SessionShellStarted, SessionShellStartedData(session_id=sid, shell=info), created)
            ]
            if message.output is not None:
                output = SessionShellEndedDataOutput.model_validate(message.output.model_dump())
                events.append((
                    SessionShellEnded,
                    SessionShellEndedData(session_id=sid, shell=info, output=output),
                    message.time.completed or created,
                ))
            return events
        case SessionMessageCompactionRunning(reason=reason):
            data_c = SessionCompactionStartedData(
                session_id=sid, reason=reason, input_id=message.id
            )
            return [(SessionCompactionStarted, data_c, created)]
        case SessionMessageCompactionCompleted():
            return [
                (
                    SessionCompactionStarted,
                    SessionCompactionStartedData(
                        session_id=sid, reason=message.reason, input_id=message.id
                    ),
                    created,
                ),
                (
                    SessionCompactionEnded,
                    SessionCompactionEndedData(
                        session_id=sid,
                        reason=message.reason,
                        model=message.model,
                        native=message.native,
                        text=message.summary,
                        cost=message.cost,
                        tokens=message.tokens,
                    ),
                    created,
                ),
            ]
        case SessionMessageCompactionFailed():
            return [
                (
                    SessionCompactionStarted,
                    SessionCompactionStartedData(
                        session_id=sid, reason=message.reason, input_id=message.id
                    ),
                    created,
                ),
                (
                    SessionCompactionFailed,
                    SessionCompactionFailedData(
                        session_id=sid,
                        reason=message.reason,
                        error=message.error,
                        input_id=message.id,
                        cost=message.cost,
                        tokens=message.tokens,
                    ),
                    created,
                ),
            ]


# --- stats ---------------------------------------------------------------------------------


def parse_time(value: str | None) -> float | None:
    """Parse an epoch-ms number or ISO date into epoch ms."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.timestamp() * 1000


def _longest_streak(dates: list[str]) -> int:
    days = sorted({datetime.fromisoformat(d).date() for d in dates})
    best = current = 0
    previous = None
    for day in days:
        current = current + 1 if previous is not None and (day - previous).days == 1 else 1
        best = max(best, current)
        previous = day
    return best


def _add_tokens(a: TokenUsageInfo, b: TokenUsageInfo) -> TokenUsageInfo:
    return TokenUsageInfo(
        input=a.input + b.input,
        output=a.output + b.output,
        reasoning=a.reasoning + b.reasoning,
        cache=TokenUsageInfoCache(
            read=a.cache.read + b.cache.read, write=a.cache.write + b.cache.write
        ),
    )


def compute_stats(  # noqa: PLR0915
    sessions: Sequence[V2Session],
    *,
    start: float | None,
    end: float | None,
    project: str | None,
    timezone: str | None,
    tools: Literal["none", "summary", "detail"],
) -> SessionStatsInfo:
    """Aggregate usage statistics over sessions."""
    try:
        zone = ZoneInfo(timezone) if timezone else UTC
    except ZoneInfoNotFoundError:
        zone = UTC
    selected = [s for s in sessions if project is None or s.info.project_id == project]
    lower = start if start is not None else 0.0
    upper = end if end is not None else datetime.now(UTC).timestamp() * 1000
    steps = prompts = 0
    tokens = _zero()
    cost = 0.0
    activity: Counter[str] = Counter()
    models: dict[tuple[str, str], tuple[Any, int, TokenUsageInfo, float]] = {}
    tool_calls: dict[str, list[tuple[str, float | None]]] = defaultdict(list)
    active_sessions: set[str] = set()
    for session in selected:
        for message in session.projection.messages:
            created = message.time.created
            if not lower <= created <= upper:
                continue
            match message:
                case SessionMessageUser():
                    prompts += 1
                    active_sessions.add(session.id)
                case SessionMessageAssistant():
                    steps += 1
                    active_sessions.add(session.id)
                    usage = message.tokens or _zero()
                    tokens = _add_tokens(tokens, usage)
                    cost += message.cost or 0.0
                    date = datetime.fromtimestamp(created / 1000, zone).date().isoformat()
                    activity[date] += 1
                    key = (message.model.provider_id, message.model.id)
                    ref, count, model_tokens, model_cost = models.get(
                        key, (message.model, 0, _zero(), 0.0)
                    )
                    models[key] = (
                        ref,
                        count + 1,
                        _add_tokens(model_tokens, usage),
                        model_cost + (message.cost or 0.0),
                    )
                    for part in message.content:
                        if isinstance(part, SessionMessageAssistantTool):
                            duration = (
                                part.time.completed - part.time.created
                                if part.time.completed is not None
                                else None
                            )
                            tool_calls[part.name].append((part.state.status, duration))
                case _:
                    pass
    totals = SessionStatsToolTotals(calls=0, succeeded=0, failed=0, unfinished=0)
    usage_list: list[SessionStatsToolUsage] = []
    for name, calls in sorted(tool_calls.items()):
        succeeded = sum(1 for status, _ in calls if status == "completed")
        failed = sum(1 for status, _ in calls if status == "error")
        unfinished = len(calls) - succeeded - failed
        durations = [d for _, d in calls if d is not None]
        usage_list.append(
            SessionStatsToolUsage(
                name=name,
                calls=len(calls),
                succeeded=succeeded,
                failed=failed,
                unfinished=unfinished,
                duration_p50=statistics.median(durations) if durations else None,
            )
        )
        totals = SessionStatsToolTotals(
            calls=totals.calls + len(calls),
            succeeded=totals.succeeded + succeeded,
            failed=totals.failed + failed,
            unfinished=totals.unfinished + unfinished,
        )
    match tools:
        case "none":
            tool_stats: (
                SessionStatsToolsVariant0 | SessionStatsToolsVariant1 | SessionStatsToolsVariant2
            ) = SessionStatsToolsVariant0()
        case "summary":
            tool_stats = SessionStatsToolsVariant1(totals=totals)
        case "detail":
            tool_stats = SessionStatsToolsVariant2(totals=totals, usage=usage_list)
    dates = sorted(activity)
    return SessionStatsInfo(
        range=SessionStatsInfoRange(from_=lower, to=upper),
        sessions=len([s for s in selected if s.id in active_sessions and not s.info.parent_id]),
        subagents=len([s for s in selected if s.id in active_sessions and s.info.parent_id]),
        prompts=prompts,
        steps=steps,
        tokens=tokens,
        cost=cost,
        tools=tool_stats,
        active_days=len(dates),
        streak=_longest_streak(dates) if dates else 0,
        activity=[SessionStatsActivity(date=d, steps=activity[d]) for d in dates],
        models=[
            SessionStatsModelUsage(model=ref, steps=count, tokens=model_tokens, cost=model_cost)
            for ref, count, model_tokens, model_cost in models.values()
        ],
    )
