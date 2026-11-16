"""Session routes."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from anyenv.text_sharing.opencode import Message, MessagePart, OpenCodeSharer
from fastapi import APIRouter, HTTPException
from pydantic_ai import (
    FileUrl,
    PartDeltaEvent,
    PartStartEvent,
    TextPart as PydanticTextPart,
    TextPartDelta,
)

from agentpool.repomap import RepoMap, find_src_files
from agentpool.utils import identifiers as identifier
from agentpool.utils.time_utils import now_ms
from agentpool_server.opencode_server.command_validation import validate_command
from agentpool_server.opencode_server.converters import (
    opencode_to_session_data,
    session_data_to_opencode,
)
from agentpool_server.opencode_server.dependencies import StateDep
from agentpool_server.opencode_server.input_provider import OpenCodeInputProvider
from opencode_sdk.models import (
    AnyMessageWithParts,
    AssistantMessage,
    CommandExecutedEvent,
    CommandRequest,
    FileDiff,
    MessagePath,
    MessageRemovedEvent,
    MessageTime,
    MessageUpdatedEvent,
    MessageWithParts,
    OpenCodeBaseModel,
    PartRemovedEvent,
    PartUpdatedEvent,
    PermissionAskedProperties,
    PermissionReplyRequest,
    PermissionResolvedEvent,
    Session,
    SessionCreatedEvent,
    SessionCreateRequest,
    SessionDeletedEvent,
    SessionDiffEvent,
    SessionForkRequest,
    SessionInitRequest,
    SessionRevert,
    SessionShare,
    SessionStatus,
    SessionStatusEvent,
    SessionUpdatedEvent,
    SessionUpdateRequest,
    ShellRequest,
    SummarizeRequest,
    TextPart,
    TimeCreatedUpdated,
    Todo,
    Tokens,
)


router = APIRouter(prefix="/session", tags=["session"])


class RevertRequest(OpenCodeBaseModel):
    """Request body for reverting a message."""

    message_id: str
    part_id: str | None = None


@router.get("")
async def list_sessions(
    state: StateDep,
    directory: str | None = None,
    roots: bool | None = None,
    start: int | None = None,
    search: str | None = None,
    limit: int | None = None,
) -> list[Session]:
    """List all sessions from the agent.

    Delegates to agent.list_sessions() which handles fetching sessions
    from the appropriate storage (pool storage, Claude storage, ACP server, etc.).

    Query params:
        directory: Filter sessions by project directory
        roots: Only return root sessions (no parentID)
        start: Filter sessions updated on or after this timestamp (ms since epoch)
        search: Filter sessions by title (case-insensitive)
        limit: Maximum number of sessions to return
    """
    # Convert to OpenCode Session format and cache
    sessions: list[Session] = []
    cwd = directory or state.agent.env.cwd
    for data in await state.agent.list_sessions(cwd=cwd):
        session = session_data_to_opencode(data)
        # Cache in state for later use
        state.sessions[data.session_id] = session
        sessions.append(session)
    # Apply filters
    if roots:
        sessions = [s for s in sessions if s.parent_id is None]
    if start is not None:
        sessions = [s for s in sessions if s.time.updated >= start]
    if search:
        lower_search = search.lower()
        sessions = [s for s in sessions if lower_search in s.title.lower()]
    if limit is not None:
        sessions = sessions[:limit]
    return sessions


@router.post("")
async def create_session(state: StateDep, request: SessionCreateRequest | None = None) -> Session:
    """Create a new session and persist to storage."""
    now = now_ms()
    session_id = identifier.ascending("session")
    session = Session(
        id=session_id,
        project_id="default",  # TODO: Get from config/request
        directory=state.working_dir,
        title=request.title if request and request.title else "New Session",
        version="1",
        time=TimeCreatedUpdated(created=now, updated=now),
        parent_id=request.parent_id if request else None,
    )

    # Persist to storage
    id_ = state.pool.manifest.config_file_path
    session_data = opencode_to_session_data(session, agent_name=state.agent.name, pool_id=id_)
    await state.storage.save_session(session_data)
    # Cache in memory
    state.sessions[session_id] = session
    state.messages[session_id] = []
    state.session_status[session_id] = SessionStatus(type="idle")
    state.todos[session_id] = []
    # Create input provider for this session
    input_provider = OpenCodeInputProvider(state, session_id)
    state.input_providers[session_id] = input_provider
    # Set input provider on agent
    state.agent._input_provider = input_provider
    await state.broadcast_event(SessionCreatedEvent.create(session))
    return session


@router.get("/status")
async def get_session_status(state: StateDep) -> dict[str, SessionStatus]:
    """Get status for all sessions.

    Returns only non-idle sessions. If all sessions are idle, returns empty dict.
    """
    return {sid: status for sid, status in state.session_status.items() if status.type != "idle"}


@router.get("/{session_id}")
async def get_session(session_id: str, state: StateDep) -> Session:
    """Get session details.

    Loads from storage if not in memory cache.
    """
    session = await state.get_or_load_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@router.patch("/{session_id}")
async def update_session(
    session_id: str,
    request: SessionUpdateRequest,
    state: StateDep,
) -> Session:
    """Update session properties and persist changes.

    Supports updating title and archiving via time.archived.
    """
    session = await state.get_or_load_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    updates: dict[str, Any] = {}
    if request.title is not None:
        updates["title"] = request.title
    # Handle archiving via time.archived
    if request.time is not None and request.time.archived is not None:
        current_time = session.time
        updates["time"] = TimeCreatedUpdated(
            created=current_time.created,
            updated=now_ms(),
            archived=request.time.archived if request.time.archived > 0 else None,
        )
    else:
        # Always update the 'updated' timestamp
        updates["time"] = TimeCreatedUpdated(created=session.time.created, updated=now_ms())
    session = session.model_copy(update=updates)
    state.sessions[session_id] = session  # Update cache
    id_ = state.pool.manifest.config_file_path
    session_data = opencode_to_session_data(session, agent_name=state.agent.name, pool_id=id_)
    await state.storage.save_session(session_data)
    await state.broadcast_event(SessionUpdatedEvent.create(session))
    return session


@router.delete("/{session_id}")
async def delete_session(session_id: str, state: StateDep) -> bool:
    """Delete a session from both cache and storage."""
    # Check if session exists (in cache or storage)
    session = await state.get_or_load_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    # Cancel any pending permissions and clean up input provider
    if input_provider := state.input_providers.pop(session_id, None):
        input_provider.permission_manager.cancel_all_pending()

    # Remove from cache
    state.sessions.pop(session_id, None)
    state.messages.pop(session_id, None)
    state.session_status.pop(session_id, None)
    state.todos.pop(session_id, None)
    # Delete from storage
    await state.storage.delete_session(session_id)
    await state.broadcast_event(SessionDeletedEvent.create(session_id, info=session))
    return True


@router.get("/{session_id}/children")
async def get_session_children(session_id: str, state: StateDep) -> list[Session]:
    """Get all child sessions that were forked from the specified parent session."""
    session = await state.get_or_load_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    # Search all cached sessions for children
    return [sess for sess in state.sessions.values() if sess.parent_id == session_id]


@router.post("/{session_id}/abort")
async def abort_session(session_id: str, state: StateDep) -> bool:
    """Abort a running session by interrupting the agent."""
    session = await state.get_or_load_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    # Interrupt the agent to cancel any ongoing stream
    try:
        await state.agent.interrupt()
        # Give a moment for the cancellation to propagate
        await asyncio.sleep(0.1)
    except Exception:  # noqa: BLE001
        pass

    # Update and broadcast session status to notify clients
    state.session_status[session_id] = SessionStatus(type="idle")
    await state.broadcast_event(SessionStatusEvent.create(session_id, SessionStatus(type="idle")))
    return True


@router.post("/{session_id}/fork")
async def fork_session(  # noqa: D417
    session_id: str,
    state: StateDep,
    request: SessionForkRequest | None = None,
    directory: str | None = None,
) -> Session:
    """Fork a session, optionally at a specific message.

    Creates a new session with:
    - parent_id pointing to the original session
    - Copies all messages (or up to message_id if specified)
    - Independent conversation history from that point forward

    Args:
        session_id: The session to fork from
        request: Optional fork parameters (message_id to fork from)
        directory: Optional directory for the forked session

    Returns:
        The newly created forked session
    """
    # Get the original session
    original_session = await state.get_or_load_session(session_id)
    if original_session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    # Get messages from the original session
    original_messages = state.messages.get(session_id, [])
    # Filter messages if message_id is specified
    messages_to_copy: list[AnyMessageWithParts] = []
    if request and request.message_id:
        # Copy messages up to and including the specified message_id
        for msg in original_messages:
            messages_to_copy.append(msg)
            if msg.info.id == request.message_id:
                break
        else:
            # message_id not found in messages
            detail = f"Message {request.message_id} not found in session"
            raise HTTPException(status_code=404, detail=detail)
    else:
        # Copy all messages
        messages_to_copy = list(original_messages)

    # Create the new forked session
    now = now_ms()
    new_session_id = identifier.ascending("session")
    # Use provided directory or inherit from original session
    fork_directory = directory if directory else original_session.directory
    forked_session = Session(
        id=new_session_id,
        project_id=original_session.project_id,
        directory=fork_directory,
        title=f"{original_session.title} (fork)",
        version="1",
        time=TimeCreatedUpdated(created=now, updated=now),
        parent_id=session_id,  # Link to original session
    )

    # Persist the forked session to storage
    session_data = opencode_to_session_data(
        forked_session,
        agent_name=state.agent.name,
        pool_id=state.pool.manifest.config_file_path,
    )
    await state.storage.save_session(session_data)
    # Cache in memory
    state.sessions[new_session_id] = forked_session
    state.session_status[new_session_id] = SessionStatus(type="idle")
    state.todos[new_session_id] = []
    # Copy messages to the new session (with updated session_id references)
    copied_messages: list[AnyMessageWithParts] = []
    for msg_with_parts in messages_to_copy:
        new_info = msg_with_parts.info.model_copy(update={"session_id": new_session_id})
        new_parts = [
            part.model_copy(update={"session_id": new_session_id}) for part in msg_with_parts.parts
        ]
        copied_messages.append(
            msg_with_parts.model_copy(update={"info": new_info, "parts": new_parts})
        )

    state.messages[new_session_id] = copied_messages
    input_provider = OpenCodeInputProvider(state, new_session_id)
    state.input_providers[new_session_id] = input_provider
    # Broadcast session created event
    await state.broadcast_event(SessionCreatedEvent.create(forked_session))
    return forked_session


@router.post("/{session_id}/init")
async def init_session(  # noqa: D417
    session_id: str,
    state: StateDep,
    request: SessionInitRequest | None = None,
) -> bool:
    """Initialize a session by analyzing the codebase and creating AGENTS.md.

    Generates a repository map, reads README if present, and runs the agent
    with a prompt to create an AGENTS.md file with project-specific context.

    Args:
        session_id: The session to initialize
        request: Optional model/provider override for the init task

    Returns:
        True when the init task has been started (runs async)
    """
    session = await state.get_or_load_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    # Get the agent and filesystem
    agent = state.agent
    fs = state.fs
    working_dir = state.working_dir
    try:
        all_files = await find_src_files(fs, working_dir)
        repo_map = RepoMap(fs=fs, root_path=working_dir, max_tokens=4000)
        repomap_content = await repo_map.get_map(all_files) or "No repository map generated."
    except Exception as e:  # noqa: BLE001
        repomap_content = f"Error generating repository map: {e}"

    # Try to read README.md
    readme_content = ""
    for readme_name in ["README.md", "readme.md", "README", "readme.txt"]:
        try:
            readme_path = f"{working_dir}/{readme_name}".replace("//", "/")
            content = await fs._cat_file(readme_path)
            readme_content = content.decode("utf-8") if isinstance(content, bytes) else content
            break
        except Exception:  # noqa: BLE001
            continue

    # Build the init prompt
    prompt_parts = [
        "Please analyze this codebase and create an AGENTS.md file in the project root.",
        "",
        "<repository-structure>",
        repomap_content,
        "</repository-structure>",
    ]
    if readme_content:
        prompt_parts.extend(["", "<readme>", readme_content, "</readme>"])
    prompt_parts.extend([
        "",
        "Include:",
        "1. Build/lint/test commands - especially for running a single test",
        "2. Code style guidelines (imports, formatting, types, naming conventions, error handling)",
        "",
        (
            "The file will be given to AI coding agents working in this repository. "
            "Keep it around 150 lines."
        ),
        "",
        (
            "If there are existing rules (.cursor/rules/, .cursorrules, "
            ".github/copilot-instructions.md), incorporate them."
        ),
    ])

    init_prompt = "\n".join(prompt_parts)

    # Handle model selection if requested
    original_model: str | None = None
    if request and request.model_id and request.provider_id:
        requested_model = f"{request.provider_id}:{request.model_id}"
        try:
            available_models = await agent.get_available_models()
            if available_models:
                valid_ids = [m.id_override if m.id_override else m.id for m in available_models]
                if requested_model in valid_ids:
                    # Store original model to restore later
                    original_model = agent.model_name
                    await agent.set_model(requested_model)
        except Exception:  # noqa: BLE001
            # Agent doesn't support model selection, ignore
            pass

    # Run the agent in the background
    async def run_init() -> None:
        try:
            await agent.run(init_prompt)
        finally:
            # Restore original model if we changed it
            if original_model is not None:
                with contextlib.suppress(Exception):
                    await agent.set_model(original_model)

    state.create_background_task(run_init(), name=f"init_{session_id}")

    return True


@router.get("/{session_id}/todo")
async def get_session_todos(session_id: str, state: StateDep) -> list[Todo]:
    """Get todos for a session.

    Returns todos from the agent pool's TodoTracker.
    """
    session = await state.get_or_load_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    # Get todos from pool's TodoTracker
    tracker = state.pool.todos
    return [Todo(content=e.content, status=e.status, priority=e.priority) for e in tracker.entries]


@router.get("/{session_id}/diff")
async def get_session_diff(
    session_id: str,
    state: StateDep,
    message_id: str | None = None,
) -> list[FileDiff]:
    """Get file diffs for a session.

    Returns a list of file changes with unified diffs.
    Optionally filter to changes since a specific message.
    """
    session = await state.get_or_load_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    file_ops = state.pool.file_ops
    if not file_ops.changes:
        return []
    # Optionally filter by message_id
    changes = file_ops.get_changes_since(message_id) if message_id else file_ops.changes
    return [FileDiff.from_file_change(change) for change in changes]


@router.post("/{session_id}/shell")
async def run_shell_command(
    session_id: str,
    request: ShellRequest,
    state: StateDep,
) -> MessageWithParts[AssistantMessage]:
    """Run a shell command directly."""
    session = await state.get_or_load_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    # Validate command for security issues
    validate_command(request.command, state.working_dir)
    now = now_ms()
    # Create assistant message for the shell output
    assistant_msg_id = identifier.ascending("message")
    assistant_message = AssistantMessage(
        id=assistant_msg_id,
        session_id=session_id,
        parent_id="",  # Shell commands don't have a parent user message
        model_id=request.model.model_id if request.model else "shell",
        provider_id=request.model.provider_id if request.model else "local",
        mode="shell",
        agent=request.agent,
        path=MessagePath(cwd=state.working_dir, root=state.working_dir),
        time=MessageTime(created=now),
    )

    # Initialize message with empty parts
    assistant_msg_with_parts = MessageWithParts[AssistantMessage](info=assistant_message, parts=[])
    state.messages[session_id].append(assistant_msg_with_parts)
    # Broadcast message created
    await state.broadcast_event(MessageUpdatedEvent.create(assistant_message))
    # Mark session as busy
    state.session_status[session_id] = SessionStatus(type="busy")
    await state.broadcast_event(SessionStatusEvent.create(session_id, SessionStatus(type="busy")))
    # Add step-start part
    step_start = assistant_msg_with_parts.add_step_start_part()
    await state.broadcast_event(PartUpdatedEvent.create(step_start))
    # Execute the command
    output_text = ""
    success = False
    try:
        result = await state.agent.env.execute_command(request.command)
        success = result.success
        if success:
            output_text = str(result.result) if result.result else ""
        else:
            output_text = f"Error: {result.error}" if result.error else "Command failed"
    except Exception as e:  # noqa: BLE001
        output_text = f"Error executing command: {e}"

    response_time = now_ms()
    # Create text part with output
    text_part = assistant_msg_with_parts.add_text_part(text=f"$ {request.command}\n{output_text}")
    await state.broadcast_event(PartUpdatedEvent.create(text_part))
    step_finish = assistant_msg_with_parts.add_step_finish_part()
    await state.broadcast_event(PartUpdatedEvent.create(step_finish))
    # Update message with completion time
    time_ = MessageTime(created=now, completed=response_time)
    updated_assistant = assistant_message.model_copy(update={"time": time_})
    assistant_msg_with_parts.info = updated_assistant
    await state.broadcast_event(MessageUpdatedEvent.create(updated_assistant))
    # Mark session as idle
    state.session_status[session_id] = SessionStatus(type="idle")
    await state.broadcast_event(SessionStatusEvent.create(session_id, SessionStatus(type="idle")))
    return assistant_msg_with_parts


@router.get("/{session_id}/permissions")
async def get_pending_permissions(
    session_id: str, state: StateDep
) -> list[PermissionAskedProperties]:
    """Get all pending permission requests for a session.

    Returns a list of pending permissions awaiting user response.
    """
    session = await state.get_or_load_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    # Get the input provider for this session
    input_provider = state.input_providers.get(session_id)
    if input_provider is None:
        return []

    return input_provider.permission_manager.get_pending_permissions(session_id)


@router.post("/{session_id}/permissions/{permission_id}")
async def respond_to_permission(
    session_id: str,
    permission_id: str,
    body: PermissionReplyRequest,
    state: StateDep,
) -> bool:
    """Respond to a pending permission request.

    The response can be:
    - "once": Allow this tool execution once
    - "always": Always allow this tool (remembered for session)
    - "reject": Reject this tool execution
    """
    session = await state.get_or_load_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    # Get the input provider for this session
    input_provider = state.input_providers.get(session_id)
    if input_provider is None:
        raise HTTPException(status_code=404, detail="No input provider for session")

    # Resolve the permission
    resolved = input_provider.permission_manager.resolve_permission(permission_id, body.reply)
    if not resolved:
        raise HTTPException(status_code=404, detail="Permission not found or already resolved")
    event = PermissionResolvedEvent.create(
        session_id=session_id,
        request_id=permission_id,
        reply=body.reply,
    )
    await state.broadcast_event(event)
    return True


# OpenCode-style continuation prompt for summarization
SUMMARIZE_PROMPT = """Provide a detailed prompt for continuing our conversation above. Focus on information that would be helpful for continuing the conversation, including what we did, what we're doing, which files we're working on, and what we're going to do next considering new session will not have access to our conversation."""  # noqa: E501


@router.post("/{session_id}/summarize")
async def summarize_session(  # noqa: PLR0915
    session_id: str,
    state: StateDep,
    request: SummarizeRequest | None = None,
) -> MessageWithParts[AssistantMessage]:
    """Summarize the session conversation.

    First runs the compaction pipeline to condense older messages,
    then streams an LLM-generated summary/continuation prompt to the user.
    The summary message is marked with summary=true for UI display.
    """
    from agentpool.agents.events import StreamCompleteEvent
    from agentpool.messaging.compaction import compact_conversation, summarizing_context

    session = await state.get_or_load_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if not state.messages.get(session_id):
        raise HTTPException(status_code=400, detail="No messages to summarize")

    # Determine model to use
    model_id = request.model_id if request and request.model_id else "default"
    provider_id = request.provider_id if request and request.provider_id else "agentpool"

    now = now_ms()
    # Create assistant message for the summary (marked with summary=true)
    assistant_msg_id = identifier.ascending("message")
    assistant_message = AssistantMessage(
        id=assistant_msg_id,
        session_id=session_id,
        parent_id="",
        model_id=model_id,
        provider_id=provider_id,
        mode="summarize",
        agent="summarizer",
        path=MessagePath(cwd=state.working_dir, root=state.working_dir),
        time=MessageTime(created=now),
        summary=True,  # Mark as summary message
    )

    assistant_msg_with_parts = MessageWithParts[AssistantMessage](info=assistant_message, parts=[])
    state.messages[session_id].append(assistant_msg_with_parts)
    # Broadcast message created
    await state.broadcast_event(MessageUpdatedEvent.create(assistant_message))
    # Mark session as busy
    state.session_status[session_id] = SessionStatus(type="busy")
    await state.broadcast_event(SessionStatusEvent.create(session_id, SessionStatus(type="busy")))
    # Add step-start part
    step_start = assistant_msg_with_parts.add_step_start_part()
    await state.broadcast_event(PartUpdatedEvent.create(step_start))
    # Step 1: Stream LLM summary generation FIRST (while we have full history)
    # The LLM sees the complete conversation and generates a continuation prompt.
    response_text = ""
    usage = None
    cost = 0.0
    text_part: TextPart | None = None
    try:
        # Stream events from the agent with the summarization prompt
        # This runs with FULL history - the summary is based on complete context
        async for event in state.agent.run_stream(SUMMARIZE_PROMPT):
            match event:
                # Text streaming start
                case PartStartEvent(part=PydanticTextPart(content=delta)):
                    response_text = delta
                    text_part = assistant_msg_with_parts.add_text_part(delta)
                    await state.broadcast_event(PartUpdatedEvent.create(text_part, delta=delta))

                # Text streaming delta
                case PartDeltaEvent(delta=TextPartDelta(content_delta=delta)) if delta:
                    response_text += delta
                    if text_part is not None:
                        text_part = TextPart(
                            id=text_part.id,
                            message_id=assistant_msg_id,
                            session_id=session_id,
                            text=response_text,
                        )
                        assistant_msg_with_parts.update_part(text_part)
                        await state.broadcast_event(PartUpdatedEvent.create(text_part, delta=delta))

                # Stream complete - extract token usage
                case StreamCompleteEvent(message=msg) if msg and msg.usage:
                    usage = msg.usage
                    cost = float(msg.cost_info.total_cost) if msg.cost_info else 0

    except Exception as e:  # noqa: BLE001
        response_text = f"Error generating summary: {e}"

    response_time = now_ms()
    # Create/update text part with final response
    if text_part is None:
        text_part = assistant_msg_with_parts.add_text_part(response_text)
        await state.broadcast_event(PartUpdatedEvent.create(text_part))

    # Step 2: Run compaction pipeline AFTER summary is generated
    # The summary was generated with full context. Now we compact the history.
    # Final state will be: [compacted history] + [summary message]
    # The compacted history becomes the cached prefix for future LLM calls.
    try:
        # Get the compaction pipeline from the agent pool configuration
        pipeline = None
        if state.agent.agent_pool is not None:
            pipeline = state.agent.agent_pool.compaction_pipeline
        if pipeline is None:
            # Fall back to a default summarizing pipeline
            pipeline = summarizing_context()

        # Apply the compaction pipeline (modifies agent.conversation in place)
        await compact_conversation(pipeline, state.agent.conversation)
        # Persist compacted messages to storage, replacing the old ones
        if state.storage is not None:
            compacted_history = state.agent.conversation.get_history()
            await state.storage.replace_conversation_messages(session_id, compacted_history)
        # Update in-memory OpenCode messages list with compacted versions
        # Keep only the summary message we just created
        state.messages[session_id] = [assistant_msg_with_parts]

    except Exception:  # noqa: BLE001
        # Compaction failure is not fatal - we still have the summary
        pass
    tokens = Tokens.from_pydantic_ai(usage) if usage else Tokens()
    # Add step-finish part
    step_finish = assistant_msg_with_parts.add_step_finish_part(tokens=tokens, cost=cost)
    await state.broadcast_event(PartUpdatedEvent.create(step_finish))
    # Update message with completion time and tokens
    assistant_msg_with_parts.info.time = MessageTime(created=now, completed=response_time)
    assistant_msg_with_parts.info.tokens = tokens
    assistant_msg_with_parts.info.cost = cost
    await state.broadcast_event(MessageUpdatedEvent.create(assistant_msg_with_parts.info))
    # Mark session as idle
    state.session_status[session_id] = SessionStatus(type="idle")
    await state.broadcast_event(SessionStatusEvent.create(session_id, SessionStatus(type="idle")))
    # Broadcast session.diff event after summarization
    file_ops = state.pool.file_ops
    diffs = [FileDiff.from_file_change(change) for change in file_ops.changes]
    await state.broadcast_event(SessionDiffEvent.create(session_id, diffs))
    return assistant_msg_with_parts


@router.post("/{session_id}/share")
async def share_session(
    session_id: str,
    state: StateDep,
    num_messages: int | None = None,
) -> Session:
    """Share session conversation history via OpenCode's sharing service.

    Uses the OpenCode share API to create a shareable link with the full
    conversation including messages and parts.

    Returns the updated session with the share URL.
    """
    session = await state.get_or_load_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    messages = state.messages.get(session_id, [])

    if not messages:
        raise HTTPException(status_code=400, detail="No messages to share")

    # Apply message limit if specified
    if num_messages is not None and num_messages > 0:
        messages = messages[-num_messages:]
    # Convert our messages to OpenCode Message format
    opencode_messages: list[Message] = []
    for msg_with_parts in messages:
        # Extract text parts
        parts = [
            MessagePart(type="text", text=part.text)
            for part in msg_with_parts.parts
            if isinstance(part, TextPart) and part.text
        ]
        if parts:
            opencode_messages.append(Message(role=msg_with_parts.info.role, parts=parts))
    if not opencode_messages:
        raise HTTPException(status_code=400, detail="No content to share")

    # Share via OpenCode API
    async with OpenCodeSharer() as sharer:
        result = await sharer.share_conversation(opencode_messages, title=session.title)
    # Store the share URL in the session
    share_info = SessionShare(url=result.url)
    updated_session = session.model_copy(update={"share": share_info})
    state.sessions[session_id] = updated_session
    # Broadcast session update
    await state.broadcast_event(SessionUpdatedEvent.create(updated_session))
    return updated_session


@router.post("/{session_id}/revert")
async def revert_session(session_id: str, request: RevertRequest, state: StateDep) -> Session:
    """Revert file changes and messages from a specific message.

    Removes messages from the revert point onwards and restores files to their
    state before the specified message's changes.
    """
    session = await state.get_or_load_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    # Get messages for this session
    messages = state.messages.get(session_id, [])
    if not messages:
        raise HTTPException(status_code=400, detail="No messages to revert")

    # Find the revert message index
    revert_idx = next((i for i, m in enumerate(messages) if m.info.id == request.message_id), None)
    if revert_idx is None:
        raise HTTPException(status_code=404, detail=f"Message {request.message_id} not found")

    # Split messages: keep messages before revert point, remove from revert point onwards
    messages_to_keep = messages[:revert_idx]
    messages_to_remove = messages[revert_idx:]

    if not messages_to_remove:
        raise HTTPException(status_code=400, detail="No messages to revert")

    # Store removed messages for unrevert
    state.reverted_messages[session_id] = messages_to_remove
    # Update message list - keep only messages before revert point
    state.messages[session_id] = messages_to_keep
    # Emit message.removed and part.removed events for all removed messages
    for msg in messages_to_remove:
        # Emit message.removed event
        await state.broadcast_event(MessageRemovedEvent.create(session_id, msg.info.id))
        # Emit part.removed events for all parts
        for part in msg.parts:
            await state.broadcast_event(PartRemovedEvent.create(session_id, msg.info.id, part.id))

    # Also revert file changes if any
    file_ops = state.pool.file_ops
    if file_ops.changes and (
        revert_ops := file_ops.get_revert_operations(since_message_id=request.message_id)
    ):
        for path, content in revert_ops:
            try:
                if content is None:
                    await state.fs._rm_file(path)
                else:
                    content_bytes = content.encode("utf-8")
                    await state.fs._pipe_file(path, content_bytes)
            except Exception as e:
                detail = f"Failed to revert {path}: {e}"
                raise HTTPException(status_code=500, detail=detail) from e
        file_ops.remove_changes_since(request.message_id)

    # Update session with revert info
    session = state.sessions[session_id]
    revert_info = SessionRevert(message_id=request.message_id, part_id=request.part_id)
    updated_session = session.model_copy(update={"revert": revert_info})
    state.sessions[session_id] = updated_session
    # Broadcast session update
    await state.broadcast_event(SessionUpdatedEvent.create(updated_session))
    # Broadcast session.diff event with current file diffs
    file_ops = state.pool.file_ops
    diffs = [FileDiff.from_file_change(change) for change in file_ops.changes]
    await state.broadcast_event(SessionDiffEvent.create(session_id, diffs))

    return updated_session


@router.post("/{session_id}/unrevert")
async def unrevert_session(session_id: str, state: StateDep) -> Session:
    """Restore all reverted messages and file changes.

    Re-applies the messages and changes that were previously reverted.
    """
    session = await state.get_or_load_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    # Restore reverted messages
    reverted_messages = state.reverted_messages.get(session_id, [])
    if not reverted_messages:
        raise HTTPException(status_code=400, detail="No reverted messages to restore")

    # Restore messages to conversation
    if session_id not in state.messages:
        state.messages[session_id] = []
    state.messages[session_id].extend(reverted_messages)

    # Emit message.updated and part.updated events for restored messages
    for msg in reverted_messages:
        # Emit message.updated event
        await state.broadcast_event(MessageUpdatedEvent.create(msg.info))
        # Emit part.updated events for all parts
        for part in msg.parts:
            await state.broadcast_event(PartUpdatedEvent.create(part))

    # Clear reverted messages
    state.reverted_messages.pop(session_id, None)

    # Also unrevert file changes if any
    file_ops = state.pool.file_ops
    if file_ops.reverted_changes:
        unrevert_ops = file_ops.get_unrevert_operations()
        for path, content in unrevert_ops:
            try:
                if content is None:
                    await state.fs._rm_file(path)
                else:
                    content_bytes = content.encode("utf-8")
                    await state.fs._pipe_file(path, content_bytes)
            except Exception as e:
                detail = f"Failed to unrevert {path}: {e}"
                raise HTTPException(status_code=500, detail=detail) from e
        file_ops.restore_reverted_changes()

    # Clear revert info from session
    updated_session = session.model_copy(update={"revert": None})
    state.sessions[session_id] = updated_session
    # Broadcast session update
    await state.broadcast_event(SessionUpdatedEvent.create(updated_session))
    return updated_session


@router.delete("/{session_id}/share")
async def unshare_session(session_id: str, state: StateDep) -> Session:
    """Remove share link from a session.

    Note: This only removes the link from the session metadata.
    The shared content may still exist on the provider's servers.
    """
    session = await state.get_or_load_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.share is None:
        raise HTTPException(status_code=400, detail="Session is not shared")
    # Remove share info from session
    updated_session = session.model_copy(update={"share": None})
    state.sessions[session_id] = updated_session
    # Broadcast session update
    await state.broadcast_event(SessionUpdatedEvent.create(updated_session))
    return updated_session


@router.post("/{session_id}/command")
async def execute_command(  # noqa: PLR0915
    session_id: str,
    request: CommandRequest,
    state: StateDep,
) -> MessageWithParts[AssistantMessage]:
    """Execute a slash command (MCP prompt).

    Commands are mapped to MCP prompts. The command name is used to find
    the matching prompt, and arguments are parsed and passed to it.
    """
    session = await state.get_or_load_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    prompts = await state.agent.tools.list_prompts()
    # Find matching prompt by name
    prompt = next((p for p in prompts if p.name == request.command), None)
    if prompt is None:
        detail = f"Command not found: {request.command}"
        raise HTTPException(status_code=404, detail=detail)

    # Parse arguments - OpenCode uses $1, $2 style, MCP uses named arguments
    # For simplicity, we'll pass the raw arguments string to the first argument
    # or parse space-separated args into a dict
    arguments: dict[str, str] = {}
    if request.arguments and prompt.arguments:
        # Split arguments and map to prompt argument names
        arg_values = request.arguments.split()
        for i, arg_def in enumerate(prompt.arguments):
            if i < len(arg_values):
                arguments[arg_def["name"]] = arg_values[i]

    now = now_ms()
    # Create assistant message
    assistant_message = AssistantMessage(
        id=identifier.ascending("message"),
        session_id=session_id,
        parent_id="",
        model_id=request.model or "default",
        provider_id="mcp",
        mode="command",
        agent=request.agent or "default",
        path=MessagePath(cwd=state.working_dir, root=state.working_dir),
        time=MessageTime(created=now),
    )
    assistant_msg_with_parts = MessageWithParts[AssistantMessage](info=assistant_message, parts=[])
    state.messages[session_id].append(assistant_msg_with_parts)
    await state.broadcast_event(MessageUpdatedEvent.create(assistant_message))
    # Mark session as busy
    state.session_status[session_id] = SessionStatus(type="busy")
    await state.broadcast_event(SessionStatusEvent.create(session_id, SessionStatus(type="busy")))
    step_start = assistant_msg_with_parts.add_step_start_part()
    await state.broadcast_event(PartUpdatedEvent.create(step_start))
    # Get prompt content and execute through the agent
    try:
        # Extract text content from parts
        prompt_texts = []
        for part in await prompt.get_components(arguments):
            match part.content:
                case str():
                    prompt_texts.append(part.content)
                case list() as content_list:
                    # Handle Sequence[UserContent]
                    for item in content_list:
                        if isinstance(item, FileUrl):
                            prompt_texts.append(item.url)
                        elif isinstance(item, str):
                            prompt_texts.append(item)
        prompt_text = "\n".join(prompt_texts)
        # Run the expanded prompt through the agent
        result = await state.agent.run(prompt_text)
        output_text = str(result.data)

    except Exception as e:  # noqa: BLE001
        output_text = f"Error executing command: {e}"

    response_time = now_ms()
    # Create text part with output
    text_part = assistant_msg_with_parts.add_text_part(text=output_text)
    await state.broadcast_event(PartUpdatedEvent.create(text_part))
    step_finish = assistant_msg_with_parts.add_step_finish_part()
    await state.broadcast_event(PartUpdatedEvent.create(step_finish))
    # Update message with completion time
    time_ = MessageTime(created=now, completed=response_time)
    updated_assistant = assistant_message.model_copy(update={"time": time_})
    assistant_msg_with_parts.info = updated_assistant
    await state.broadcast_event(MessageUpdatedEvent.create(updated_assistant))
    # Mark session as idle
    state.session_status[session_id] = SessionStatus(type="idle")
    await state.broadcast_event(SessionStatusEvent.create(session_id, SessionStatus(type="idle")))

    # Broadcast command.executed event
    executed_event = CommandExecutedEvent.create(
        name=request.command,
        session_id=session_id,
        arguments=request.arguments or "",
        message_id=assistant_message.id,
    )
    await state.broadcast_event(executed_event)
    return assistant_msg_with_parts
