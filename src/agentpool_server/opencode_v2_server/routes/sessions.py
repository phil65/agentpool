"""Session routes: lifecycle, agent/model selection, prompting, messages and the log."""

import asyncio
from typing import TYPE_CHECKING, Literal

from fastapi import APIRouter, Query
from fastapi.responses import Response, StreamingResponse

from agentpool_server.opencode_v2_server.bus import sse_frame
from agentpool_server.opencode_v2_server.deps import (
    DirectoryDep,
    SessionDep,
    StateDep,
    V2Response,
    ok,
)
from agentpool_server.opencode_v2_server.errors import (
    agent_not_found,
    conflict,
    invalid_request,
    message_not_found,
    session_not_found,
    unsupported_model,
)
from agentpool_server.opencode_v2_server.events import new_message_id
from agentpool_server.opencode_v2_server.location import normalize_directory
from agentpool_server.opencode_v2_server.pagination import paginate
from agentpool_server.opencode_v2_server.prompt_input import AttachmentError, admit_prompt
from agentpool_server.opencode_v2_server.state import SessionNotFoundError
from opencode_v2_sdk.models import (
    EventLogSynced,
    SessionActive,
    SessionActiveResponse,
    SessionAgentSelected,
    SessionAgentSelectedData,
    SessionArchived,
    SessionArchivedData,
    SessionContextResponse,
    SessionCreateBody,
    SessionCreateResponse,
    SessionGetResponse,
    SessionInboxCancelled,
    SessionInboxCancelledData,
    SessionInboxDeliveryChanged,
    SessionInboxDeliveryChangedData,
    SessionInboxEnqueued,
    SessionInboxEnqueuedData,
    SessionInboxItemSynthetic,
    SessionInboxItemUser,
    SessionInboxListResponse,
    SessionInboxSynthetic,
    SessionInboxSyntheticPayload,
    SessionInboxUpdateBody,
    SessionInboxUser,
    SessionInterruptResponse,
    SessionMessageGetResponse,
    SessionMessagesResponse,
    SessionMessagesResponseCursor,
    SessionMetadataUpdated,
    SessionMetadataUpdatedData,
    SessionModelSelected,
    SessionModelSelectedData,
    SessionPermissions,
    SessionPermissionsData,
    SessionPromptBody,
    SessionPromptResponse,
    SessionRenamed,
    SessionRenamedData,
    SessionsResponse,
    SessionsResponseCursor,
    SessionSwitchAgentBody,
    SessionSwitchModelBody,
    SessionSyntheticBody,
    SessionSyntheticResponse,
    SessionUnarchived,
    SessionUnarchivedData,
    SessionUpdateBody,
    SessionViewBody,
    SessionViewed,
    SessionViewedData,
)


if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from agentpool_server.opencode_v2_server.session import V2Session
    from agentpool_server.opencode_v2_server.state import ServerState
    from opencode_v2_sdk.models import SessionMessageInfo


router = APIRouter(tags=["session"], default_response_class=V2Response)

DEFAULT_SESSION_LIMIT = 50
DEFAULT_MESSAGE_LIMIT = 50
MAX_MESSAGE_LIMIT = 200
type Delivery = Literal["steer", "queue"]


def _no_content() -> Response:
    return Response(status_code=204)


def _parse_limit(value: str | None, default: int, maximum: int | None = None) -> int:
    if value is None:
        return default
    try:
        limit = int(value)
    except ValueError:
        raise invalid_request("limit must be an integer", field="limit") from None
    if limit < 1 or (maximum is not None and limit > maximum):
        raise invalid_request(f"limit must be between 1 and {maximum or 'inf'}", field="limit")
    return limit


# --- lifecycle -----------------------------------------------------------------------------


@router.get("/api/session")
async def session_list(
    state: StateDep,
    limit: str | None = None,
    order: Literal["asc", "desc"] | None = None,
    search: str | None = None,
    parent_id: str | None = Query(default=None, alias="parentID"),
    archived: Literal["true", "false"] | None = None,
    directory: str | None = None,
    project: str | None = None,
    subpath: str | None = None,
    cursor: str | None = None,
) -> Response:
    """List sessions (newest first by default)."""
    infos = await state.list_sessions()
    if search:
        needle = search.lower()
        infos = [i for i in infos if needle in (i.title or "").lower() or needle in i.id]
    if parent_id == "null":
        infos = [i for i in infos if i.parent_id is None]
    elif parent_id is not None:
        infos = [i for i in infos if i.parent_id == parent_id]
    if archived == "true":
        infos = [i for i in infos if i.time.archived is not None]
    elif archived == "false":
        infos = [i for i in infos if i.time.archived is None]
    if directory:
        wanted = normalize_directory(directory)
        infos = [i for i in infos if i.location.directory == wanted]
    if project:
        infos = [i for i in infos if i.project_id == project]
    if subpath is not None:
        infos = [i for i in infos if (i.subpath or "") == subpath]
    chronological = sorted(infos, key=lambda i: (i.time.updated, i.id))
    page = paginate(
        chronological,
        key=lambda i: i.id,
        limit=_parse_limit(limit, DEFAULT_SESSION_LIMIT),
        order=order,
        cursor=cursor,
    )
    return ok(
        SessionsResponse(
            data=page.items,
            cursor=SessionsResponseCursor(previous=page.previous, next=page.next),
        )
    )


@router.post("/api/session")
async def session_create(
    state: StateDep, directory: DirectoryDep, body: SessionCreateBody | None = None
) -> Response:
    """Create a session bound to an agent (the pool's main agent by default)."""
    body = body or SessionCreateBody()
    try:
        session = await state.create_session(
            directory=body.location.directory if body.location else directory,
            session_id=body.id,
            parent_id=body.parent_id,
            title=body.title,
            agent=body.agent,
            model=body.model,
            metadata=body.metadata,
            permissions=body.permissions,
        )
    except SessionNotFoundError as e:
        raise session_not_found(str(e)) from None
    except ValueError as e:
        raise invalid_request(str(e)) from None
    return ok(SessionCreateResponse(data=session.info))


@router.get("/api/session/active")
async def session_active(state: StateDep) -> Response:
    """Sessions with a running execution."""
    data = {sid: SessionActive() for sid, runner in state.runners.items() if runner.active}
    return ok(SessionActiveResponse(data=data))


@router.get("/api/session/{session_id}")
async def session_get(session: SessionDep) -> Response:
    """Get a session."""
    return ok(SessionGetResponse(data=session.info))


@router.delete("/api/session/{session_id}", status_code=204)
async def session_remove(session_id: str, state: StateDep) -> Response:
    """Delete a session (including subagent sessions, history and log)."""
    try:
        await state.delete_session(session_id)
    except SessionNotFoundError:
        raise session_not_found(session_id) from None
    return _no_content()


@router.patch("/api/session/{session_id}", status_code=204)
async def session_update(session: SessionDep, body: SessionUpdateBody, state: StateDep) -> Response:
    """Rename, annotate, set permissions on or (un)archive a session."""
    sid = session.id
    if body.title is not None and body.title != session.info.title:
        session.emit(SessionRenamed, SessionRenamedData(session_id=sid, title=body.title))
    if body.metadata is not None:
        session.emit(
            SessionMetadataUpdated,
            SessionMetadataUpdatedData(session_id=sid, metadata=body.metadata),
        )
    if body.permissions is not None:
        session.emit(
            SessionPermissions, SessionPermissionsData(session_id=sid, permissions=body.permissions)
        )
    is_archived = session.info.time.archived is not None
    if body.archived is True and not is_archived:
        session.emit(SessionArchived, SessionArchivedData(session_id=sid))
    elif body.archived is False and is_archived:
        session.emit(SessionUnarchived, SessionUnarchivedData(session_id=sid))
    await state.save_session_data(session)
    return _no_content()


@router.post("/api/session/{session_id}/view", status_code=204)
async def session_view(session: SessionDep, body: SessionViewBody) -> Response:
    """Mark the session as viewed up to an idle watermark."""
    session.emit(SessionViewed, SessionViewedData(session_id=session.id, idle=body.idle))
    return _no_content()


# --- agent / model -------------------------------------------------------------------------


@router.post("/api/session/{session_id}/agent", status_code=204)
async def session_switch_agent(
    session: SessionDep, body: SessionSwitchAgentBody, state: StateDep
) -> Response:
    """Bind the session to another pool agent (takes effect with the next turn)."""
    node = state.catalog.get_node(body.agent)
    if node is None:
        raise agent_not_found(body.agent)
    if body.agent == session.info.agent:
        return _no_content()
    previous_agent = session.info.agent
    session.emit(
        SessionAgentSelected,
        SessionAgentSelectedData(session_id=session.id, agent=body.agent, previous=previous_agent),
    )
    model = await state.catalog.current_model_ref(node)
    if await state.catalog.resolve_model(node, session.info.model or model) is None:
        session.emit(
            SessionModelSelected,
            SessionModelSelectedData(
                session_id=session.id, model=model, previous=session.info.model
            ),
        )
    await state.save_session_data(session)
    return _no_content()


@router.post("/api/session/{session_id}/model", status_code=204)
async def session_switch_model(
    session: SessionDep, body: SessionSwitchModelBody, state: StateDep
) -> Response:
    """Select the model for the session's next turns."""
    agent = session.info.agent or state.catalog.default_agent_name()
    node = state.catalog.get_node(agent)
    if node is None:
        raise agent_not_found(agent)
    ref = body.model
    if await state.catalog.resolve_model(node, ref) is None:
        raise unsupported_model(ref.provider_id, ref.id, agent)
    if session.info.model == ref:
        return _no_content()
    session.emit(
        SessionModelSelected,
        SessionModelSelectedData(session_id=session.id, model=ref, previous=session.info.model),
    )
    await state.save_session_data(session)
    return _no_content()


# --- input ---------------------------------------------------------------------------------


async def enqueue(
    state: ServerState,
    session: V2Session,
    inbox_id: str,
    item: SessionInboxItemUser | SessionInboxItemSynthetic,
    *,
    resume: bool,
) -> None:
    if inbox_id in session.projection.inbox or session.projection.get_message(inbox_id):
        raise conflict(f"Input {inbox_id} already exists", resource=inbox_id)
    session.emit(
        SessionInboxEnqueued,
        SessionInboxEnqueuedData(session_id=session.id, inbox_id=inbox_id, item=item),
    )
    runner = state.runner(session)
    if item.delivery == "steer" and await runner.steer(inbox_id):
        return
    if resume:
        runner.schedule()


@router.post("/api/session/{session_id}/prompt")
async def session_prompt(session: SessionDep, body: SessionPromptBody, state: StateDep) -> Response:
    """Admit a user prompt into the session inbox and run it."""
    try:
        payload = await admit_prompt(
            body, fs=state.fs, base_dir=session.directory, skills=state.pool.skills
        )
    except AttachmentError as e:
        raise invalid_request(str(e), field="files") from None
    inbox_id = body.id or new_message_id()
    delivery: Delivery = body.delivery or "steer"
    item = SessionInboxItemUser(payload=payload, delivery=delivery)
    await enqueue(state, session, inbox_id, item, resume=body.resume is not False)
    stored = session.projection.inbox.get(inbox_id)
    if not isinstance(stored, SessionInboxUser):
        stored = SessionInboxUser.model_validate({
            "id": inbox_id,
            "sessionID": session.id,
            "time": {"created": session.info.time.updated},
            "payload": payload,
            "delivery": delivery,
        })
    return ok(SessionPromptResponse(data=stored))


@router.post("/api/session/{session_id}/synthetic")
async def session_synthetic(
    session: SessionDep, body: SessionSyntheticBody, state: StateDep
) -> Response:
    """Admit a synthetic (non-user) input."""
    inbox_id = body.id or new_message_id()
    delivery: Delivery = body.delivery or "steer"
    payload = SessionInboxSyntheticPayload(
        text=body.text, description=body.description, metadata=body.metadata
    )
    item = SessionInboxItemSynthetic(payload=payload, delivery=delivery)
    await enqueue(state, session, inbox_id, item, resume=body.resume is not False)
    stored = session.projection.inbox.get(inbox_id)
    if not isinstance(stored, SessionInboxSynthetic):
        stored = SessionInboxSynthetic.model_validate({
            "id": inbox_id,
            "sessionID": session.id,
            "time": {"created": session.info.time.updated},
            "payload": payload,
            "delivery": delivery,
        })
    return ok(SessionSyntheticResponse(data=stored))


@router.post("/api/session/{session_id}/interrupt")
async def session_interrupt(
    session: SessionDep,
    state: StateDep,
    resume: Literal["true", "false"] | None = None,
) -> Response:
    """Interrupt the running execution."""
    runner = state.runners.get(session.id)
    state.permissions.cancel_session(session.id)
    state.forms.cancel_session(session.id)
    interrupted = False
    if runner is not None:
        interrupted = await runner.interrupt("user", resume=resume == "true")
        if resume == "true":
            runner.schedule()
    return ok(SessionInterruptResponse(interrupted=interrupted))


@router.post("/api/session/{session_id}/background", status_code=204)
async def session_background(session: SessionDep) -> Response:
    """Background blocking tools (AgentPool tools don't block the turn; nothing to do)."""
    return _no_content()


@router.post("/api/experimental/session/{session_id}/wait", status_code=204)
async def session_wait(session: SessionDep, state: StateDep) -> Response:
    """Wait until the session's inbox is drained."""
    if (runner := state.runners.get(session.id)) is not None:
        await runner.wait()
    return _no_content()


# --- inbox ---------------------------------------------------------------------------------


@router.get("/api/session/{session_id}/inbox")
async def session_inbox_list(session: SessionDep) -> Response:
    """Pending (not yet delivered) inputs, in admission order."""
    return ok(SessionInboxListResponse(data=list(session.projection.inbox.values())))


@router.delete("/api/session/{session_id}/inbox/{inbox_id}", status_code=204)
async def session_inbox_cancel(session: SessionDep, inbox_id: str) -> Response:
    """Cancel a pending input."""
    if inbox_id not in session.projection.inbox:
        raise message_not_found(session.id, inbox_id)
    session.emit(
        SessionInboxCancelled, SessionInboxCancelledData(session_id=session.id, inbox_id=inbox_id)
    )
    return _no_content()


@router.patch("/api/session/{session_id}/inbox/{inbox_id}", status_code=204)
async def session_inbox_update(
    session: SessionDep, inbox_id: str, body: SessionInboxUpdateBody, state: StateDep
) -> Response:
    """Change how a pending input is delivered (steer into the running turn or queue)."""
    item = session.projection.inbox.get(inbox_id)
    if item is None:
        raise message_not_found(session.id, inbox_id)
    if item.delivery != body.delivery:
        session.emit(
            SessionInboxDeliveryChanged,
            SessionInboxDeliveryChangedData(
                session_id=session.id, inbox_id=inbox_id, delivery=body.delivery
            ),
        )
        if body.delivery == "steer":
            await state.runner(session).steer(inbox_id)
    return _no_content()


# --- messages ------------------------------------------------------------------------------


@router.get("/api/session/{session_id}/message")
async def session_messages(
    session: SessionDep,
    limit: str | None = None,
    order: Literal["asc", "desc"] | None = None,
    cursor: str | None = None,
    message_type: str | None = Query(default=None, alias="type"),
) -> Response:
    """Projected messages of the session."""
    messages: list[SessionMessageInfo] = list(session.projection.messages)
    if message_type is not None:
        messages = [m for m in messages if m.type == message_type]
    page = paginate(
        messages,
        key=lambda m: m.id,
        limit=_parse_limit(limit, DEFAULT_MESSAGE_LIMIT, MAX_MESSAGE_LIMIT),
        order=order,
        cursor=cursor,
    )
    return ok(
        SessionMessagesResponse(
            data=page.items,
            cursor=SessionMessagesResponseCursor(previous=page.previous, next=page.next),
        )
    )


@router.get("/api/session/{session_id}/message/{message_id}")
async def session_message_get(session: SessionDep, message_id: str) -> Response:
    """One projected message."""
    message = session.projection.get_message(message_id)
    if message is None:
        raise message_not_found(session.id, message_id)
    return ok(SessionMessageGetResponse(data=message))


@router.get("/api/session/{session_id}/context")
async def session_context(session: SessionDep) -> Response:
    """Messages that make up the model context (everything after the last compaction)."""
    messages = list(session.projection.messages)
    start = 0
    for index, message in enumerate(messages):
        if message.type == "compaction":
            start = index
    return ok(SessionContextResponse(data=messages[start:]))


@router.get("/api/experimental/session/{session_id}/log")
async def session_log(
    session: SessionDep,
    after: str | None = None,
    follow: Literal["true", "false"] | None = None,
) -> StreamingResponse:
    """Durable session event log (SSE), optionally continuing with live events."""
    try:
        after_seq = int(after) if after is not None else 0
    except ValueError:
        raise invalid_request("after must be an integer", field="after") from None
    return StreamingResponse(
        _log_stream(session, after_seq, follow=follow == "true"),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


async def _log_stream(session: V2Session, after: int, *, follow: bool) -> AsyncIterator[str]:
    queue = session.follow() if follow else None
    try:
        backlog = session.events_after(after)
        last = after
        for event in backlog:
            last = event.durable.seq
            yield sse_frame(event)
        if queue is None:
            return
        yield sse_frame(
            EventLogSynced(aggregate_id=session.id, seq=session.seq if session.seq else None)
        )
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=15)
            except TimeoutError:
                yield ": heartbeat\n\n"
                continue
            if event is None:
                return
            if event.durable.seq > last:
                last = event.durable.seq
                yield sse_frame(event)
    finally:
        if queue is not None:
            session.unfollow(queue)
