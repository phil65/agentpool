"""Session routes that derive or rewrite session state.

Fork, move, compact, revert, diff, generate, environment, instructions, stats and
export/import.
"""

import json
from typing import TYPE_CHECKING, Any, Literal

import anyio
from fastapi import APIRouter, Query
from fastapi.responses import Response

from agentpool.agents.base_agent import BaseAgent
from agentpool.agents.events import StreamCompleteEvent
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
    session_busy,
    unavailable,
)
from agentpool_server.opencode_v2_server.events import new_message_id
from agentpool_server.opencode_v2_server.location import normalize_directory
from agentpool_server.opencode_v2_server.run_converter import usage_to_tokens
from agentpool_server.opencode_v2_server.session_ops import (
    changes_of_turns,
    changes_to_diffs,
    compute_stats,
    copy_history,
    fork_events,
    forked_event_data,
    history_before,
    instruction_update,
    message_events,
    message_time,
    parse_time,
    prompt_ids,
    rekey_event,
    revert_operations,
)
from agentpool_server.opencode_v2_server.state import SessionNotFoundError
from opencode_v2_sdk.models import (
    ExperimentalSessionExportResponse,
    ExperimentalSessionImportBody,
    ExperimentalSessionImportResponse,
    ExperimentalSessionInstructionsEntryListResponse,
    ExperimentalSessionInstructionsEntryPutBody,
    ExperimentalSessionStatsResponse,
    InstructionEntryInfo,
    SessionCompactBody,
    SessionCompactResponse,
    SessionDiffResponse,
    SessionEnvironmentBody,
    SessionForkBody,
    SessionForked,
    SessionForkResponse,
    SessionGenerateBody,
    SessionGenerateResponse,
    SessionGenerateResponseData,
    SessionInboxCompaction,
    SessionInboxEnqueued,
    SessionInboxEnqueuedData,
    SessionInboxItemCompaction,
    SessionInstructionsUpdated,
    SessionMessageUser,
    SessionMoveBody,
    SessionMoved,
    SessionMovedData,
    SessionRevert,
    SessionRevertCleared,
    SessionRevertClearedData,
    SessionRevertCommitted,
    SessionRevertCommittedData,
    SessionRevertStageBody,
    SessionRevertStaged,
    SessionRevertStagedData,
    SessionRevertStageResponse,
    SessionTransferData,
)


if TYPE_CHECKING:
    from agentpool_server.opencode_v2_server.session import V2Session
    from agentpool_server.opencode_v2_server.state import ServerState


router = APIRouter(tags=["session"], default_response_class=V2Response)


def _no_content() -> Response:
    return Response(status_code=204)


def _require_idle(state: ServerState, session: V2Session) -> None:
    if state.is_running(session.id):
        raise session_busy(session.id)


def _session_agent(state: ServerState, session: V2Session) -> BaseAgent[Any, Any]:
    name = session.info.agent or state.catalog.default_agent_name()
    node = state.catalog.get_node(name)
    if not isinstance(node, BaseAgent):
        raise agent_not_found(name)
    return node


def _known_agent(state: ServerState, name: str | None) -> str | None:
    return name if name is not None and state.catalog.get_node(name) is not None else None


# --- fork / move ---------------------------------------------------------------------------


@router.post("/api/session/{session_id}/fork")
async def session_fork(session: SessionDep, body: SessionForkBody, state: StateDep) -> Response:
    """Fork the session (optionally only the history before a message)."""
    if body.before is not None and session.projection.get_message(body.before) is None:
        raise message_not_found(session.id, body.before)
    title = f"{session.info.title or 'Session'} (fork)"
    child = await state.create_session(
        directory=session.directory,
        title=title,
        agent=session.info.agent,
        model=session.info.model,
        metadata=session.info.metadata,
        permissions=session.info.permissions,
    )
    boundary: Literal["before", "through"] | None = "before" if body.before else None
    copied = fork_events(session, boundary, body.before)
    child.ingest([
        rekey_event(event, child.id, child.seq + i + 1) for i, event in enumerate(copied)
    ])
    await child.flush()
    if body.before is not None:
        child.emit(SessionForked, forked_event_data(child.id, session.id, "before", body.before))
    elif session.projection.messages:
        last = session.projection.messages[-1].id
        child.emit(SessionForked, forked_event_data(child.id, session.id, "through", last))
    history = await state.storage.get_session_messages(session.id)
    kept = len(prompt_ids(child.projection.messages))
    prompts = prompt_ids(session.projection.messages)
    for message in copy_history(history_before(history, prompts, kept), child.id):
        await state.storage.log_message(message)
    await state.save_session_data(child)
    return ok(SessionForkResponse(data=child.info))


@router.post("/api/session/{session_id}/move", status_code=204)
async def session_move(session: SessionDep, body: SessionMoveBody, state: StateDep) -> Response:
    """Move the session to another directory (takes effect for the next turn)."""
    directory = normalize_directory(body.directory)
    if not await anyio.Path(directory).is_dir():
        raise invalid_request(f"Directory does not exist: {directory}", field="directory")
    project = await state.projects.resolve(directory)
    session.emit(
        SessionMoved,
        SessionMovedData.model_validate({
            "sessionID": session.id,
            "location": {"directory": directory},
            "projectID": project.id,
        }),
    )
    if (runner := state.runners.get(session.id)) is not None:
        runner.input_provider.directory = directory
        runner.input_provider.project_id = project.id
    await state.save_session_data(session)
    return _no_content()


# --- compaction ----------------------------------------------------------------------------


@router.post("/api/session/{session_id}/compact")
async def session_compact(
    session: SessionDep, body: SessionCompactBody, state: StateDep
) -> Response:
    """Queue a manual compaction of the session history."""
    inbox_id = body.id or new_message_id()
    if inbox_id in session.projection.inbox:
        raise conflict(f"Input {inbox_id} already exists", resource=inbox_id)
    item = SessionInboxItemCompaction(payload={}, delivery=body.delivery or "queue")
    session.emit(
        SessionInboxEnqueued,
        SessionInboxEnqueuedData(session_id=session.id, inbox_id=inbox_id, item=item),
    )
    state.runner(session).schedule()
    stored = session.projection.inbox.get(inbox_id)
    if not isinstance(stored, SessionInboxCompaction):
        stored = SessionInboxCompaction.model_validate({
            "id": inbox_id,
            "sessionID": session.id,
            "time": {"created": session.info.time.updated},
            "payload": {},
            "delivery": item.delivery,
        })
    return ok(SessionCompactResponse(data=stored))


# --- revert --------------------------------------------------------------------------------


@router.post("/api/session/{session_id}/revert/stage")
async def session_revert_stage(
    session: SessionDep, body: SessionRevertStageBody, state: StateDep
) -> Response:
    """Stage a revert to just before a message (optionally including file changes)."""
    _require_idle(state, session)
    created = message_time(session, body.message_id)
    if created is None:
        raise message_not_found(session.id, body.message_id)
    if body.files:
        dropped = _prompts_from(session, body.message_id)
        files = changes_to_diffs(
            changes_of_turns(state.pool.file_ops, session.id, dropped, created)
        )
    else:
        files = None
    revert = SessionRevert(message_id=body.message_id, files=files)
    session.emit(SessionRevertStaged, SessionRevertStagedData(session_id=session.id, revert=revert))
    return ok(SessionRevertStageResponse(data=revert))


@router.delete("/api/session/{session_id}/revert", status_code=204)
async def session_revert_clear(session: SessionDep) -> Response:
    """Discard a staged revert."""
    if session.info.revert is not None:
        session.emit(SessionRevertCleared, SessionRevertClearedData(session_id=session.id))
    return _no_content()


@router.post("/api/session/{session_id}/revert/commit", status_code=204)
async def session_revert_commit(session: SessionDep, state: StateDep) -> Response:
    """Apply the staged revert: drop later messages, history and (staged) file changes."""
    revert = session.info.revert
    if revert is None:
        raise conflict("No revert is staged", resource=session.id)
    _require_idle(state, session)
    created = message_time(session, revert.message_id)
    if created is None:
        raise message_not_found(session.id, revert.message_id)
    if revert.files:
        tracker = state.pool.file_ops
        dropped = _prompts_from(session, revert.message_id)
        changes = changes_of_turns(tracker, session.id, dropped, created)
        for path, content in revert_operations(changes):
            target = anyio.Path(path)
            if content is None:
                if await target.exists():
                    await target.unlink()
            else:
                await target.write_text(content)
        tracker.changes = [c for c in tracker.changes if c not in changes]
    index = session.projection.message_index(revert.message_id) or 0
    kept = len(prompt_ids(session.projection.messages[:index]))
    prompts = prompt_ids(session.projection.messages)
    history = await state.storage.get_session_messages(session.id)
    await state.storage.replace_conversation_messages(
        session.id, history_before(history, prompts, kept)
    )
    state.pool.sessions.remove(session.id)
    for agent in state.pool.all_agents.values():
        if agent.session_id == session.id:
            agent.session_id = None  # Reload the truncated history on the next turn.
    session.emit(
        SessionRevertCommitted,
        SessionRevertCommittedData(session_id=session.id, to=revert.message_id),
    )
    return _no_content()


# --- diff / generate -----------------------------------------------------------------------


@router.get("/api/session/{session_id}/diff")
async def session_diff(
    session: SessionDep,
    state: StateDep,
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = None,
    context: str | None = None,
) -> Response:
    """File changes made between two user messages (default: the whole session)."""
    users = [m for m in session.projection.messages if isinstance(m, SessionMessageUser)]
    start_id = from_ or (users[0].id if users else None)
    start = message_time(session, start_id) if start_id else session.info.time.created
    end = message_time(session, to) if to else None
    if start_id and start is None:
        raise message_not_found(session.id, start_id)
    if to and end is None:
        raise message_not_found(session.id, to)
    messages = session.projection.messages
    first = session.projection.message_index(start_id) if start_id else 0
    last = session.projection.message_index(to) if to else None
    turns = prompt_ids(messages[first or 0 : last])
    diffs = changes_to_diffs(changes_of_turns(state.pool.file_ops, session.id, turns, start, end))
    return ok(SessionDiffResponse(data=diffs))


def _prompts_from(session: V2Session, message_id: str) -> list[str]:
    """Prompts of the turns from ``message_id`` on (what a revert to it drops)."""
    index = session.projection.message_index(message_id) or 0
    return prompt_ids(session.projection.messages[index:])


@router.post("/api/session/{session_id}/generate")
async def session_generate(
    session: SessionDep, body: SessionGenerateBody, state: StateDep
) -> Response:
    """Generate text from the session context without changing its history."""
    agent = _session_agent(state, session)
    has_history = any(isinstance(m, SessionMessageUser) for m in session.projection.messages)
    text = ""
    tokens = usage_to_tokens(None)
    cost = 0.0
    try:
        async with state.binder.hold(agent, session, session.info.model, has_history=has_history):
            stream = agent.run_stream(body.prompt, session_id=session.id, store_history=False)
            async for event in stream:
                if isinstance(event, StreamCompleteEvent):
                    text = str(event.message.content)
                    tokens = usage_to_tokens(event.message.usage)
                    if event.message.cost_info:
                        cost = float(event.message.cost_info.total_cost)
    except Exception as e:
        raise unavailable("session generation", str(e)) from e
    data = SessionGenerateResponseData(text=text, tokens=tokens, cost=cost)
    return ok(SessionGenerateResponse(data=data))


@router.put("/api/session/{session_id}/environment", status_code=204)
async def session_environment(
    session: SessionDep, body: SessionEnvironmentBody, state: StateDep
) -> Response:
    """Set environment variables for the session's shell commands."""
    state.runner(session).environment = dict(body.variables)
    return _no_content()


# --- instructions --------------------------------------------------------------------------


@router.get("/api/experimental/session/{session_id}/instructions/entries")
async def instruction_entries(session: SessionDep) -> Response:
    """Session instruction entries."""
    data = [
        InstructionEntryInfo(key=key, value=json.loads(value))
        for key, value in sorted(session.projection.instructions.items())
    ]
    return ok(ExperimentalSessionInstructionsEntryListResponse(data=data))


@router.put("/api/experimental/session/{session_id}/instructions/entries/{key}", status_code=204)
async def instruction_put(
    session: SessionDep,
    key: str,
    body: ExperimentalSessionInstructionsEntryPutBody,
    state: StateDep,
) -> Response:
    """Set an instruction entry (shown to the agent with its next turn)."""
    data, text = instruction_update(session.id, key, body.value)
    session.emit(SessionInstructionsUpdated, data)
    state.runner(session).pending_context.append(text)
    return _no_content()


@router.delete("/api/experimental/session/{session_id}/instructions/entries/{key}", status_code=204)
async def instruction_remove(session: SessionDep, key: str, state: StateDep) -> Response:
    """Remove an instruction entry."""
    if key not in session.projection.instructions:
        return _no_content()
    data, text = instruction_update(session.id, key, None)
    session.emit(SessionInstructionsUpdated, data)
    state.runner(session).pending_context.append(text)
    return _no_content()


# --- stats / export / import ---------------------------------------------------------------


@router.get("/api/experimental/session/stats")
async def session_stats(
    state: StateDep,
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = None,
    project: str | None = None,
    timezone: str | None = None,
    tools: Literal["none", "summary", "detail"] = "none",
) -> Response:
    """Usage statistics over all sessions."""
    try:
        start, end = parse_time(from_), parse_time(to)
    except ValueError:
        raise invalid_request("from/to must be epoch milliseconds or ISO dates") from None
    sessions = []
    for info in await state.list_sessions():
        try:
            sessions.append(await state.get_session(info.id))
        except SessionNotFoundError:
            continue
    stats = compute_stats(
        sessions, start=start, end=end, project=project, timezone=timezone, tools=tools
    )
    return ok(ExperimentalSessionStatsResponse(data=stats))


@router.get("/api/experimental/session/{session_id}/export")
async def session_export(
    session: SessionDep, sanitize: Literal["true", "false"] | None = None
) -> Response:
    """Export the session info and messages."""
    info = session.info
    if sanitize == "true":
        info = info.model_copy(update={"metadata": None, "permissions": None})
    data = SessionTransferData(info=info, messages=list(session.projection.messages))
    return ok(ExperimentalSessionExportResponse(data=data))


@router.post("/api/experimental/session/import")
async def session_import(
    body: ExperimentalSessionImportBody, state: StateDep, directory: DirectoryDep
) -> Response:
    """Import an exported session (as a new session with the same messages)."""
    transfer = SessionTransferData.model_validate({
        "info": body.info.model_dump(by_alias=True),
        "messages": body.messages,
    })
    target = body.location.directory if body.location else directory
    session = await state.create_session(
        directory=target,
        title=transfer.info.title,
        agent=_known_agent(state, transfer.info.agent),
        model=transfer.info.model,
        metadata=transfer.info.metadata,
        permissions=transfer.info.permissions,
    )
    for message in transfer.messages:
        for event_cls, data, created in message_events(
            session.id, message, session.directory, session.info.project_id
        ):
            session.emit(event_cls, data, created=int(created))
    await session.flush()
    await state.save_session_data(session)
    return ok(ExperimentalSessionImportResponse(data=session.info))
