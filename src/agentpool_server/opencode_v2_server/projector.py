"""Projection of durable session events into messages and inbox state.

Port of OpenCode's ``SessionMessageUpdater`` (``packages/core/src/session/message-updater.ts``)
plus the inbox projection of ``projector.ts``. The message list served by
``GET /api/session/{id}/message`` is always derived from the durable event log, so the live
stream, the replayable log and the message list can never disagree.
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, assert_never

from agentpool_server.opencode_v2_server.events import message_id_from_event
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
    SessionInboxCompaction,
    SessionInboxCompactionTime,
    SessionInboxDelivered,
    SessionInboxDeliveryChanged,
    SessionInboxEnqueued,
    SessionInboxItemCompaction,
    SessionInboxItemMove,
    SessionInboxItemSynthetic,
    SessionInboxItemUser,
    SessionInboxMove,
    SessionInboxMovePayload,
    SessionInboxMoveTime,
    SessionInboxSynthetic,
    SessionInboxSyntheticTime,
    SessionInboxUser,
    SessionInboxUserTime,
    SessionInstructionsUpdated,
    SessionMessageAgentSelected,
    SessionMessageAgentSelectedTime,
    SessionMessageAssistant,
    SessionMessageAssistantReasoning,
    SessionMessageAssistantReasoningTime,
    SessionMessageAssistantRetry,
    SessionMessageAssistantSnapshot,
    SessionMessageAssistantText,
    SessionMessageAssistantTime,
    SessionMessageAssistantTool,
    SessionMessageAssistantToolTime,
    SessionMessageCompactionCompleted,
    SessionMessageCompactionCompletedTime,
    SessionMessageCompactionFailed,
    SessionMessageCompactionFailedTime,
    SessionMessageCompactionRunning,
    SessionMessageCompactionRunningTime,
    SessionMessageContentUpdated,
    SessionMessageIdle,
    SessionMessageIdleTime,
    SessionMessageLocationSwitched,
    SessionMessageLocationSwitchedPreviousVariant0,
    SessionMessageLocationSwitchedTime,
    SessionMessageModelSelected,
    SessionMessageModelSelectedTime,
    SessionMessageShell,
    SessionMessageShellOutput,
    SessionMessageShellTime,
    SessionMessageSkill,
    SessionMessageSkillTime,
    SessionMessageSynthetic,
    SessionMessageSyntheticTime,
    SessionMessageSystem,
    SessionMessageSystemTime,
    SessionMessageToolStateCompleted,
    SessionMessageToolStateError,
    SessionMessageToolStateRunning,
    SessionMessageToolStateStreaming,
    SessionMessageUser,
    SessionMessageUserTime,
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
    from collections.abc import Callable

    from agentpool_server.opencode_v2_server.events import DurableSessionEvent
    from opencode_v2_sdk.models import (
        LocationPublicRef,
        ModelRef,
        SessionInboxInfo,
        SessionMessageInfo,
        SessionStepEndedData,
        SessionStepFailedData,
    )


type AssistantPart = (
    SessionMessageAssistantText | SessionMessageAssistantReasoning | SessionMessageAssistantTool
)
type CompactionMessage = (
    SessionMessageCompactionRunning
    | SessionMessageCompactionCompleted
    | SessionMessageCompactionFailed
)


@dataclass
class SessionProjection:
    """Messages and inbox derived from a session's durable events."""

    session_id: str
    agent: str | None = None
    """Agent the session is currently bound to (as of the last projected event)."""
    model: ModelRef | None = None
    """Model the session is currently bound to (as of the last projected event)."""
    location: LocationPublicRef | None = None
    project_id: str | None = None
    subpath: str | None = None
    messages: list[SessionMessageInfo] = field(default_factory=list)
    inbox: dict[str, SessionInboxInfo] = field(default_factory=dict)
    """Pending (enqueued, not yet delivered or cancelled) inbox items by ID."""
    instructions: dict[str, str] = field(default_factory=dict)
    """Session instruction entries (JSON-encoded values) by key."""

    # --- lookups -------------------------------------------------------------------------

    def message_index(self, message_id: str) -> int | None:
        """Index of a message by ID."""
        for index in range(len(self.messages) - 1, -1, -1):
            if self.messages[index].id == message_id:
                return index
        return None

    def get_message(self, message_id: str) -> SessionMessageInfo | None:
        """Get a message by ID."""
        index = self.message_index(message_id)
        return None if index is None else self.messages[index]

    def current_assistant(self) -> SessionMessageAssistant | None:
        """The most recent assistant message."""
        for message in reversed(self.messages):
            if isinstance(message, SessionMessageAssistant):
                return message
        return None

    def _assistant(self, message_id: str) -> SessionMessageAssistant | None:
        message = self.get_message(message_id)
        return message if isinstance(message, SessionMessageAssistant) else None

    def _current_compaction(self) -> CompactionMessage | None:
        for message in reversed(self.messages):
            match message:
                case (
                    SessionMessageCompactionRunning()
                    | SessionMessageCompactionCompleted()
                    | SessionMessageCompactionFailed()
                ):
                    return message
        return None

    def _shell(self, shell_id: str) -> SessionMessageShell | None:
        for message in reversed(self.messages):
            if isinstance(message, SessionMessageShell) and message.shell_id == shell_id:
                return message
        return None

    # --- mutation helpers ----------------------------------------------------------------

    def _append(self, message: SessionMessageInfo) -> None:
        self.messages.append(message)

    def _replace(self, message: SessionMessageInfo) -> None:
        index = self.message_index(message.id)
        if index is None:
            self.messages.append(message)
        else:
            self.messages[index] = message

    def _update_assistant(
        self,
        message_id: str,
        recipe: Callable[[SessionMessageAssistant], SessionMessageAssistant],
    ) -> None:
        if (assistant := self._assistant(message_id)) is not None:
            self._replace(recipe(assistant))

    def _append_part(self, message_id: str, part: AssistantPart) -> None:
        self._update_assistant(
            message_id, lambda a: a.model_copy(update={"content": [*a.content, part]})
        )

    def _update_last_part[P: AssistantPart](
        self,
        message_id: str,
        part_type: type[P],
        recipe: Callable[[P], AssistantPart],
        *,
        predicate: Callable[[P], bool] | None = None,
    ) -> None:
        assistant = self._assistant(message_id)
        if assistant is None:
            return
        content = list(assistant.content)
        for index in range(len(content) - 1, -1, -1):
            part = content[index]
            if isinstance(part, part_type) and (predicate is None or predicate(part)):
                content[index] = recipe(part)
                self._replace(assistant.model_copy(update={"content": content}))
                return

    def _clear_current_retry(self) -> None:
        assistant = self.current_assistant()
        if assistant is not None and assistant.retry is not None:
            self._replace(assistant.model_copy(update={"retry": None}))

    def _idle(self, event: DurableSessionEvent, outcome: str, error: object = None) -> None:
        self._clear_current_retry()
        self._append(
            SessionMessageIdle.model_validate({
                "id": message_id_from_event(event.id),
                "type": "idle",
                "outcome": outcome,
                "error": error,
                "metadata": event.metadata,
                "time": SessionMessageIdleTime(created=event.created),
            })
        )

    @staticmethod
    def _terminal_snapshot(
        assistant: SessionMessageAssistant,
        data: SessionStepEndedData | SessionStepFailedData,
    ) -> SessionMessageAssistantSnapshot | None:
        if not data.snapshot and not data.files:
            return assistant.snapshot
        base = assistant.snapshot or SessionMessageAssistantSnapshot()
        return base.model_copy(update={"end": data.snapshot, "files": data.files})

    # --- projection ----------------------------------------------------------------------

    def apply(self, event: DurableSessionEvent) -> None:  # noqa: PLR0915
        """Apply a durable event to the projection."""
        created = event.created
        match event:
            case SessionCreated(data=data):
                self.agent = data.agent
                self.model = data.model
                self.project_id = data.project_id
                self.subpath = data.subpath
            case (
                SessionViewed()
                | SessionArchived()
                | SessionUnarchived()
                | SessionUsageRecorded()
                | SessionRenamed()
                | SessionMetadataUpdated()
                | SessionPermissions()
                | SessionDeleted()
                | SessionForked()
                | SessionExecutionStarted()
                | SessionRevertStaged()
                | SessionRevertCleared()
            ):
                pass
            case SessionRevertCommitted(data=data):
                if (index := self.message_index(data.to)) is not None:
                    del self.messages[index:]
            case SessionMessageContentUpdated():
                self._on_content_updated(event)
            case SessionAgentSelected(data=data):
                self._append(
                    SessionMessageAgentSelected(
                        id=message_id_from_event(event.id),
                        metadata=event.metadata,
                        agent=data.agent,
                        previous=data.previous if data.previous is not None else self.agent,
                        time=SessionMessageAgentSelectedTime(created=created),
                    )
                )
                self.agent = data.agent
            case SessionModelSelected(data=data):
                self._append(
                    SessionMessageModelSelected(
                        id=message_id_from_event(event.id),
                        metadata=event.metadata,
                        model=data.model,
                        previous=data.previous if data.previous is not None else self.model,
                        time=SessionMessageModelSelectedTime(created=created),
                    )
                )
                self.model = data.model
            case SessionMoved(data=data):
                previous = (
                    SessionMessageLocationSwitchedPreviousVariant0(
                        location=self.location,
                        project_id=self.project_id,
                        subpath=self.subpath,
                    )
                    if self.location is not None
                    else None
                )
                location = data.location.model_dump(include={"directory"})
                self._append(
                    SessionMessageLocationSwitched.model_validate({
                        "id": message_id_from_event(event.id),
                        "metadata": event.metadata,
                        "location": location,
                        "projectID": data.project_id,
                        "subpath": data.subpath,
                        "previous": previous,
                        "time": SessionMessageLocationSwitchedTime(created=created),
                    })
                )
                self.project_id = data.project_id
                self.subpath = data.subpath
            case SessionInboxEnqueued(data=data):
                self.inbox[data.inbox_id] = _inbox_info(
                    data.inbox_id, self.session_id, data.item, created
                )
            case SessionInboxCancelled(data=data):
                self.inbox.pop(data.inbox_id, None)
            case SessionInboxDeliveryChanged(data=data):
                if (item := self.inbox.get(data.inbox_id)) is not None:
                    self.inbox[data.inbox_id] = item.model_copy(update={"delivery": data.delivery})
            case SessionInboxDelivered(data=data):
                self._deliver(data.inbox_id, created)
            case SessionExecutionSucceeded():
                self._idle(event, "succeeded")
            case SessionExecutionFailed(data=data):
                self._idle(event, "failed", data.error)
            case SessionExecutionInterrupted(data=data):
                if data.reason == "shutdown":
                    self._clear_current_retry()
                else:
                    self._idle(event, "interrupted")
            case SessionInstructionsUpdated(data=data):
                for key, value in data.delta.items():
                    if value == "removed":
                        self.instructions.pop(key, None)
                    else:
                        self.instructions[key] = value
                if data.text is not None:
                    sources = list(data.delta)
                    self._append(
                        SessionMessageSystem(
                            id=message_id_from_event(event.id),
                            text=data.text,
                            description=f"Instructions updated: {', '.join(sources)}",
                            metadata={
                                **(event.metadata or {}),
                                "notice": "instructions",
                                "instructionSources": sources,
                            },
                            time=SessionMessageSystemTime(created=created),
                        )
                    )
            case SessionSynthetic(data=data):
                self._append(
                    SessionMessageSynthetic(
                        id=message_id_from_event(event.id),
                        text=data.text,
                        description=data.description,
                        metadata=data.metadata,
                        time=SessionMessageSyntheticTime(created=created),
                    )
                )
            case SessionSkillActivated(data=data):
                self._append(
                    SessionMessageSkill(
                        id=message_id_from_event(event.id),
                        skill=data.id,
                        name=data.name,
                        text=data.text,
                        metadata=event.metadata,
                        time=SessionMessageSkillTime(created=created),
                    )
                )
            case SessionShellStarted(data=data):
                metadata = event.metadata
                if data.shell.metadata.get("background") is True:
                    metadata = {**(metadata or {}), "background": True}
                self._append(
                    SessionMessageShell(
                        id=message_id_from_event(event.id),
                        metadata=metadata,
                        shell_id=data.shell.id,
                        command=data.shell.command,
                        status=data.shell.status,
                        time=SessionMessageShellTime(created=created),
                    )
                )
            case SessionShellEnded(data=data):
                if (shell := self._shell(data.shell.id)) is not None:
                    self._replace(
                        shell.model_copy(
                            update={
                                "status": data.shell.status,
                                "exit": data.shell.exit,
                                "output": SessionMessageShellOutput.model_validate(
                                    data.output.model_dump()
                                ),
                                "time": shell.time.model_copy(update={"completed": created}),
                            }
                        )
                    )
            case SessionStepStarted(data=data):
                self._step_started(data.assistant_message_id, event, created)
            case SessionStepFirstToken() | SessionStepStreamed():
                self._on_step_timing(event)
            case SessionStepEnded():
                self._on_step_ended(event)
            case SessionStepFailed():
                self._on_step_failed(event)
            case SessionTextStarted(data=data):
                self._append_part(data.assistant_message_id, SessionMessageAssistantText(text=""))
            case SessionTextEnded():
                self._on_text_ended(event)
            case SessionReasoningStarted(data=data):
                self._append_part(
                    data.assistant_message_id,
                    SessionMessageAssistantReasoning(
                        text="",
                        native=data.native,
                        time=SessionMessageAssistantReasoningTime(created=created),
                    ),
                )
            case SessionReasoningEnded():
                self._on_reasoning_ended(event)
            case SessionToolInputStarted(data=data):
                self._append_part(
                    data.assistant_message_id,
                    SessionMessageAssistantTool(
                        id=data.id,
                        name=data.name,
                        time=SessionMessageAssistantToolTime(created=created),
                        state=SessionMessageToolStateStreaming(input=""),
                    ),
                )
            case SessionToolInputEnded():
                self._on_tool_input_ended(event)
            case SessionToolCalled():
                self._on_tool_called(event)
            case SessionToolSuccess():
                self._on_tool_success(event)
            case SessionToolFailed():
                self._on_tool_failed(event)
            case SessionRetryScheduled(data=data):
                retry = SessionMessageAssistantRetry(
                    attempt=data.attempt, at=data.at, error=data.error
                )
                assistant = self._assistant(data.assistant_message_id)
                if assistant is not None:
                    self._replace(assistant.model_copy(update={"retry": retry}))
            case SessionCompactionStarted(data=data):
                self._append(
                    SessionMessageCompactionRunning(
                        id=data.input_id or message_id_from_event(event.id),
                        metadata=event.metadata,
                        reason=data.reason,
                        summary="",
                        time=SessionMessageCompactionRunningTime(created=created),
                    )
                )
            case SessionCompactionEnded(data=data):
                current = self._current_compaction()
                if isinstance(current, SessionMessageCompactionRunning):
                    metadata = (
                        {**(current.metadata or {}), **event.metadata}
                        if event.metadata
                        else current.metadata
                    )
                    message_id = current.id
                    time = SessionMessageCompactionCompletedTime(created=current.time.created)
                else:
                    metadata = event.metadata
                    message_id = message_id_from_event(event.id)
                    time = SessionMessageCompactionCompletedTime(created=created)
                self._replace(
                    SessionMessageCompactionCompleted(
                        id=message_id,
                        metadata=metadata,
                        reason=data.reason,
                        model=data.model,
                        native=data.native,
                        summary=data.text,
                        cost=data.cost,
                        tokens=data.tokens,
                        time=time,
                    )
                )
            case SessionCompactionFailed(data=data):
                current = self._current_compaction()
                running = current if isinstance(current, SessionMessageCompactionRunning) else None
                failed = SessionMessageCompactionFailed(
                    id=(
                        running.id if running else data.input_id or message_id_from_event(event.id)
                    ),
                    metadata=running.metadata if running else event.metadata,
                    reason=data.reason,
                    error=data.error,
                    cost=data.cost,
                    tokens=data.tokens,
                    time=SessionMessageCompactionFailedTime(
                        created=running.time.created if running else created
                    ),
                )
                self._replace(failed)
            case _ as unreachable:
                assert_never(unreachable)

    def _on_content_updated(self, event: SessionMessageContentUpdated) -> None:
        if (assistant := self._assistant(event.data.message_id)) is not None:
            content = list(event.data.content)
            self._replace(assistant.model_copy(update={"content": content}))

    def _on_step_timing(self, event: SessionStepFirstToken | SessionStepStreamed) -> None:
        if (assistant := self._assistant(event.data.assistant_message_id)) is None:
            return
        key = "first_token" if isinstance(event, SessionStepFirstToken) else "streamed"
        time = assistant.time.model_copy(update={key: event.created})
        self._replace(assistant.model_copy(update={"time": time}))

    def _on_step_ended(self, event: SessionStepEnded) -> None:
        data = event.data
        if (assistant := self._assistant(data.assistant_message_id)) is None:
            return
        update: dict[str, object] = {
            "time": assistant.time.model_copy(update={"completed": event.created}),
            "finish": data.finish,
            "raw_finish": data.raw_finish,
            "native": data.native,
            "cost": data.cost,
            "tokens": data.tokens,
            "snapshot": self._terminal_snapshot(assistant, data),
        }
        self._replace(assistant.model_copy(update=update))

    def _on_step_failed(self, event: SessionStepFailed) -> None:
        data = event.data
        if (assistant := self._assistant(data.assistant_message_id)) is None:
            return
        update: dict[str, object] = {
            "time": assistant.time.model_copy(update={"completed": event.created}),
            "finish": data.finish or "error",
            "raw_finish": data.raw_finish,
            "native": data.native,
            "error": data.error,
            "retry": None,
            "snapshot": self._terminal_snapshot(assistant, data),
        }
        if data.cost is not None and data.tokens is not None:
            update |= {"cost": data.cost, "tokens": data.tokens}
        self._replace(assistant.model_copy(update=update))

    def _on_text_ended(self, event: SessionTextEnded) -> None:
        data = event.data

        def recipe(part: SessionMessageAssistantText) -> SessionMessageAssistantText:
            return part.model_copy(update={"text": data.text, "native": data.native})

        self._update_last_part(data.assistant_message_id, SessionMessageAssistantText, recipe)

    def _on_reasoning_ended(self, event: SessionReasoningEnded) -> None:
        data = event.data
        created = event.created

        def recipe(part: SessionMessageAssistantReasoning) -> SessionMessageAssistantReasoning:
            start = part.time.created if part.time is not None else created
            time = SessionMessageAssistantReasoningTime(created=start, completed=created)
            update: dict[str, object] = {"text": data.text, "time": time}
            if data.native is not None:
                update["native"] = data.native
            return part.model_copy(update=update)

        self._update_last_part(
            data.assistant_message_id,
            SessionMessageAssistantReasoning,
            recipe,
            predicate=lambda part: part.time is None or part.time.completed is None,
        )

    def _on_tool_input_ended(self, event: SessionToolInputEnded) -> None:
        text = event.data.text

        def recipe(part: SessionMessageAssistantTool) -> SessionMessageAssistantTool:
            if isinstance(part.state, SessionMessageToolStateStreaming):
                return part.model_copy(
                    update={"state": part.state.model_copy(update={"input": text})}
                )
            return part

        self._update_tool(event.data.assistant_message_id, event.data.id, recipe)

    def _on_tool_called(self, event: SessionToolCalled) -> None:
        data = event.data
        created = event.created

        def recipe(part: SessionMessageAssistantTool) -> SessionMessageAssistantTool:
            return part.model_copy(
                update={
                    "executed": data.executed,
                    "native": data.native,
                    "time": part.time.model_copy(update={"ran": created}),
                    "state": SessionMessageToolStateRunning(input=data.input, metadata={}),
                }
            )

        self._update_tool(data.assistant_message_id, data.id, recipe)

    def _on_tool_success(self, event: SessionToolSuccess) -> None:
        data = event.data
        created = event.created

        def recipe(part: SessionMessageAssistantTool) -> SessionMessageAssistantTool:
            if not isinstance(part.state, SessionMessageToolStateRunning):
                return part
            native = {**(part.native or {}), **data.native} if data.native else part.native
            state = SessionMessageToolStateCompleted(
                input=part.state.input, content=list(data.content), metadata=data.metadata
            )
            return part.model_copy(
                update={
                    "executed": data.executed or part.executed is True,
                    "native": native,
                    "time": part.time.model_copy(update={"completed": created}),
                    "state": state,
                }
            )

        self._update_tool(data.assistant_message_id, data.id, recipe)

    def _on_tool_failed(self, event: SessionToolFailed) -> None:
        data = event.data
        created = event.created

        def recipe(part: SessionMessageAssistantTool) -> SessionMessageAssistantTool:
            match part.state:
                case SessionMessageToolStateStreaming():
                    tool_input: dict[str, Any] = {}
                case SessionMessageToolStateRunning(input=running_input):
                    tool_input = running_input
                case SessionMessageToolStateCompleted() | SessionMessageToolStateError():
                    return part
                case _ as unreachable:
                    assert_never(unreachable)
            native = {**(part.native or {}), **data.native} if data.native else part.native
            state = SessionMessageToolStateError(
                input=tool_input,
                error=data.error,
                content=list(data.content) if data.content is not None else None,
                metadata=data.metadata,
            )
            return part.model_copy(
                update={
                    "executed": data.executed or part.executed is True,
                    "native": native,
                    "time": part.time.model_copy(update={"completed": created}),
                    "state": state,
                }
            )

        self._update_tool(data.assistant_message_id, data.id, recipe)

    def _update_tool(
        self,
        message_id: str,
        call_id: str,
        recipe: Callable[[SessionMessageAssistantTool], SessionMessageAssistantTool],
    ) -> None:
        self._update_last_part(
            message_id,
            SessionMessageAssistantTool,
            recipe,
            predicate=lambda p: p.id == call_id,
        )

    def _step_started(self, message_id: str, event: SessionStepStarted, created: float) -> None:
        data = event.data
        existing = self._assistant(message_id)
        if existing is not None:
            snapshot = existing.snapshot
            if data.snapshot:
                snapshot = (snapshot or SessionMessageAssistantSnapshot()).model_copy(
                    update={"start": data.snapshot}
                )
            self._replace(
                existing.model_copy(
                    update={
                        "agent": data.agent,
                        "model": data.model,
                        "retry": None,
                        "error": None,
                        "finish": None,
                        "raw_finish": None,
                        "native": None,
                        "time": SessionMessageAssistantTime(created=data.started),
                        "snapshot": snapshot,
                    }
                )
            )
            return
        current = self.current_assistant()
        if current is not None:
            self._replace(
                current.model_copy(
                    update={
                        "retry": None,
                        "time": current.time.model_copy(update={"completed": created}),
                    }
                )
            )
        self._append(
            SessionMessageAssistant(
                id=message_id,
                agent=data.agent,
                model=data.model,
                metadata=event.metadata,
                time=SessionMessageAssistantTime(created=data.started),
                content=[],
                snapshot=(
                    SessionMessageAssistantSnapshot(start=data.snapshot) if data.snapshot else None
                ),
            )
        )

    def _deliver(self, inbox_id: str, created: float) -> None:
        item = self.inbox.pop(inbox_id, None)
        match item:
            case SessionInboxUser(payload=payload):
                self._append(
                    SessionMessageUser(
                        id=item.id,
                        metadata=payload.metadata,
                        text=payload.text,
                        files=payload.files,
                        agents=payload.agents,
                        skills=payload.skills,
                        time=SessionMessageUserTime(created=created),
                    )
                )
            case SessionInboxSynthetic(payload=payload):
                self._append(
                    SessionMessageSynthetic(
                        id=item.id,
                        text=payload.text,
                        description=payload.description,
                        metadata=payload.metadata,
                        time=SessionMessageSyntheticTime(created=created),
                    )
                )
            case SessionInboxCompaction() | SessionInboxMove() | None:
                pass
            case _ as unreachable:
                assert_never(unreachable)


def _inbox_info(
    inbox_id: str,
    session_id: str,
    item: SessionInboxItemUser
    | SessionInboxItemSynthetic
    | SessionInboxItemCompaction
    | SessionInboxItemMove,
    created: float,
) -> SessionInboxInfo:
    match item:
        case SessionInboxItemUser(payload=payload, delivery=delivery):
            return SessionInboxUser(
                id=inbox_id,
                session_id=session_id,
                time=SessionInboxUserTime(created=created),
                payload=payload,
                delivery=delivery,
            )
        case SessionInboxItemSynthetic(payload=payload, delivery=delivery):
            return SessionInboxSynthetic(
                id=inbox_id,
                session_id=session_id,
                time=SessionInboxSyntheticTime(created=created),
                payload=payload,
                delivery=delivery,
            )
        case SessionInboxItemCompaction(payload=payload, delivery=delivery):
            return SessionInboxCompaction(
                id=inbox_id,
                session_id=session_id,
                time=SessionInboxCompactionTime(created=created),
                payload=payload,
                delivery=delivery,
            )
        case SessionInboxItemMove(payload=payload, delivery=delivery):
            return SessionInboxMove(
                id=inbox_id,
                session_id=session_id,
                time=SessionInboxMoveTime(created=created),
                payload=SessionInboxMovePayload.model_validate({
                    "projectID": payload.project_id,
                    "subpath": payload.subpath,
                    "location": {"directory": payload.location.directory},
                }),
                delivery=delivery,
            )
        case _ as unreachable:
            assert_never(unreachable)
