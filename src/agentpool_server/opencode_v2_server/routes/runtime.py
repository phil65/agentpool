"""Commands, skills, MCP servers, shells and jobs."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from fastapi import APIRouter, Query
from fastapi.responses import Response

from agentpool.agents.base_agent import BaseAgent
from agentpool_commands.base import NodeCommand
from agentpool_server.opencode_v2_server.deps import (
    DirectoryDep,
    SessionDep,
    StateDep,
    V2Response,
    ok,
)
from agentpool_server.opencode_v2_server.errors import (
    agent_not_found,
    command_not_found,
    invalid_request,
    shell_not_found,
    skill_not_found,
)
from agentpool_server.opencode_v2_server.events import new_message_id
from agentpool_server.opencode_v2_server.location import public_ref
from agentpool_server.opencode_v2_server.prompt_input import AttachmentError, admit_prompt
from agentpool_server.opencode_v2_server.routes.sessions import enqueue
from opencode_v2_sdk.models import (
    CommandInfo,
    CommandListResponse,
    ExperimentalJobListResponse,
    ExperimentalSessionSkillBody,
    JobShell,
    JobSubagent,
    JobTime,
    McpListResponse,
    McpResource,
    McpResourceCatalog,
    McpResourceCatalogResponse,
    McpServer,
    McpStatusConnected,
    McpStatusDisabled,
    McpStatusFailed,
    McpStatusNeedsAuth,
    McpStatusPending,
    SessionCommandBody,
    SessionInboxItemUser,
    SessionPromptBody,
    SessionShellBody,
    SessionShellEnded,
    SessionShellEndedData,
    SessionShellEndedDataOutput,
    SessionShellStarted,
    SessionShellStartedData,
    SessionSkillActivated,
    SessionSkillActivatedData,
    ShellCreateBody,
    ShellCreateResponse,
    ShellGetResponse,
    ShellListResponse,
    ShellOutputResponse,
    SkillInfo,
    SkillListResponse,
)


if TYPE_CHECKING:
    from agentpool.common_types import MCPServerStatus
    from agentpool_server.opencode_v2_server.shells import ShellProcess
    from agentpool_server.opencode_v2_server.state import ServerState


router = APIRouter(tags=["runtime"], default_response_class=V2Response)

type McpStatus = (
    McpStatusConnected | McpStatusPending | McpStatusDisabled | McpStatusFailed | McpStatusNeedsAuth
)


# --- commands ------------------------------------------------------------------------------


def _command_infos(agent: BaseAgent[Any, Any]) -> list[CommandInfo]:
    infos = []
    for command in agent.command_store.list_commands():
        if isinstance(command, NodeCommand) and not command.supports_node(agent):
            continue
        infos.append(CommandInfo(name=command.name, description=command.description))
    return infos


@router.get("/api/command")
async def command_list(state: StateDep, directory: DirectoryDep) -> Response:
    """Slash commands of the default agent."""
    data = _command_infos(state.pool.main_agent)
    return ok(CommandListResponse(location=public_ref(directory), data=data))


@router.post("/api/session/{session_id}/command", status_code=204)
async def session_command(
    session: SessionDep, body: SessionCommandBody, state: StateDep
) -> Response:
    """Run a slash command of the session's agent as the next turn."""
    agent_name = session.info.agent or state.catalog.default_agent_name()
    node = state.catalog.get_node(agent_name)
    if not isinstance(node, BaseAgent):
        raise agent_not_found(agent_name)
    if node.command_store.get_command(body.name) is None:
        raise command_not_found(body.name)
    prompt = SessionPromptBody(
        text=f"/{body.name} {body.text}".strip(),
        files=body.files,
        agents=body.agents,
        skills=body.skills,
        metadata={"command": body.name},
    )
    try:
        payload = await admit_prompt(
            prompt, fs=state.fs, base_dir=session.directory, skills=state.pool.skills
        )
    except AttachmentError as e:
        raise invalid_request(str(e), field="files") from None
    item = SessionInboxItemUser(payload=payload, delivery=body.delivery or "queue")
    enqueue(state, session, new_message_id(), item, resume=True)
    return Response(status_code=204)


# --- skills --------------------------------------------------------------------------------


@router.get("/api/skill")
async def skill_list(state: StateDep, directory: DirectoryDep) -> Response:
    """Skills available to the pool."""
    data = []
    for skill in state.pool.skills.list_skills():
        content = await state.pool.skills.get_skill_instructions(skill.name)
        data.append(
            SkillInfo(
                id=skill.name,
                name=skill.name,
                description=skill.description,
                autoinvoke=not skill.disable_model_invocation,
                path=str(skill.skill_path),
                content=content,
            )
        )
    return ok(SkillListResponse(location=public_ref(directory), data=data))


@router.post("/api/experimental/session/{session_id}/skill", status_code=204)
async def session_skill(
    session: SessionDep, body: ExperimentalSessionSkillBody, state: StateDep
) -> Response:
    """Activate a skill: shown in the session and added to the next turn's context."""
    skills = state.pool.skills
    if body.id not in skills.registry:
        raise skill_not_found(body.id)
    skill = skills.get_skill(body.id)
    text = await skills.get_skill_instructions(body.id)
    session.emit(
        SessionSkillActivated,
        SessionSkillActivatedData(session_id=session.id, id=body.id, name=skill.name, text=text),
    )
    runner = state.runner(session)
    runner.pending_context.append(f"<skill name={skill.name!r}>\n{text}\n</skill>")
    if body.resume is not False:
        runner.schedule()
    return Response(status_code=204)


# --- MCP -----------------------------------------------------------------------------------


def _mcp_status(info: MCPServerStatus) -> McpStatus:
    match info.status:
        case "connected":
            return McpStatusConnected()
        case "pending":
            return McpStatusPending()
        case "disabled" | "disconnected":
            return McpStatusDisabled()
        case "needs-auth":
            return McpStatusNeedsAuth(error=info.error or "Authentication required")
        case "error" | "failed":
            return McpStatusFailed(error=info.error or "Connection failed")


@router.get("/api/mcp")
async def mcp_list(state: StateDep, directory: DirectoryDep) -> Response:
    """MCP servers of all pool agents."""
    servers: dict[str, McpServer] = {}
    for agent in state.pool.all_agents.values():
        for name, info in (await agent.get_mcp_server_info()).items():
            servers.setdefault(
                name,
                McpServer(name=name, status=_mcp_status(info), title=info.server_name),
            )
    return ok(McpListResponse(location=public_ref(directory), data=list(servers.values())))


@router.get("/api/mcp/resource")
async def mcp_resources(state: StateDep, directory: DirectoryDep) -> Response:
    """MCP resources of the default agent."""
    resources = [
        McpResource(
            server=resource.client or "agentpool",
            name=resource.name,
            uri=resource.uri,
            description=resource.description,
            mime_type=resource.mime_type,
        )
        for resource in await state.pool.main_agent.tools.list_resources()
    ]
    catalog = McpResourceCatalog(resources=resources, templates=[])
    return ok(McpResourceCatalogResponse(location=public_ref(directory), data=catalog))


# --- shells --------------------------------------------------------------------------------


@router.get("/api/shell")
async def shell_list(state: StateDep, directory: DirectoryDep) -> Response:
    """Shell commands started at this location."""
    data = [s.info for s in state.shells.shells.values() if s.directory == directory]
    return ok(ShellListResponse(location=public_ref(directory), data=data))


@router.post("/api/shell")
async def shell_create(body: ShellCreateBody, state: StateDep, directory: DirectoryDep) -> Response:
    """Run a shell command in the background."""
    entry = await state.shells.start(
        body.command,
        directory=directory,
        cwd=body.cwd,
        timeout=body.timeout,
        metadata=body.metadata,
    )
    return ok(ShellCreateResponse(location=public_ref(directory), data=entry.info))


def _shell(state: ServerState, shell_id: str) -> ShellProcess:
    entry = state.shells.get(shell_id)
    if entry is None:
        raise shell_not_found(shell_id)
    return entry


@router.get("/api/shell/{shell_id}")
async def shell_get(shell_id: str, state: StateDep, directory: DirectoryDep) -> Response:
    """One shell command."""
    entry = _shell(state, shell_id)
    return ok(ShellGetResponse(location=public_ref(directory), data=entry.info))


@router.delete("/api/shell/{shell_id}", status_code=204)
async def shell_remove(shell_id: str, state: StateDep) -> Response:
    """Kill and forget a shell command."""
    if not await state.shells.remove(shell_id):
        raise shell_not_found(shell_id)
    return Response(status_code=204)


@router.get("/api/shell/{shell_id}/output")
async def shell_output(
    shell_id: str,
    state: StateDep,
    directory: DirectoryDep,
    cursor: str | None = None,
    limit: str | None = None,
) -> Response:
    """Output of a shell command from a byte cursor."""
    entry = _shell(state, shell_id)
    try:
        start = int(float(cursor)) if cursor else 0
        count = int(float(limit)) if limit else None
    except ValueError:
        raise invalid_request("cursor and limit must be numbers") from None
    return ok(ShellOutputResponse(location=public_ref(directory), data=entry.read(start, count)))


@router.post("/api/session/{session_id}/shell", status_code=204)
async def session_shell(session: SessionDep, body: SessionShellBody, state: StateDep) -> Response:
    """Run a user shell command inside the session (shown as a shell message)."""

    def on_exit(entry: ShellProcess) -> None:
        output = entry.read(0)
        session.emit(
            SessionShellEnded,
            SessionShellEndedData(
                session_id=session.id,
                shell=entry.info,
                output=SessionShellEndedDataOutput.model_validate(output.model_dump()),
            ),
        )

    entry = await state.shells.start(
        body.command,
        directory=session.directory,
        shell_id=body.id,
        metadata={"sessionID": session.id},
        env=state.runner(session).environment,
        on_exit=on_exit,
    )
    session.emit(
        SessionShellStarted, SessionShellStartedData(session_id=session.id, shell=entry.info)
    )
    return Response(status_code=204)


# --- jobs ----------------------------------------------------------------------------------


def _shell_jobs(state: ServerState, sessions: set[str]) -> list[JobShell]:
    jobs = []
    for entry in state.shells.shells.values():
        owner = entry.info.metadata.get("sessionID")
        if not isinstance(owner, str) or owner not in sessions:
            continue
        status: Literal["running", "completed", "error", "cancelled"]
        match entry.info.status:
            case "running":
                status = "running"
            case "exited":
                status = "completed" if entry.info.exit == 0 else "error"
            case "timeout":
                status = "error"
            case "killed":
                status = "cancelled"
        jobs.append(
            JobShell(
                id=entry.info.id,
                session_id=owner,
                status=status,
                title=entry.info.command,
                time=JobTime(started=entry.info.time.started, completed=entry.info.time.completed),
                shell_id=entry.info.id,
                command=entry.info.command,
            )
        )
    return jobs


async def _descendants(state: ServerState, session_id: str) -> set[str]:
    infos = await state.list_sessions()
    result = {session_id}
    changed = True
    while changed:
        changed = False
        for info in infos:
            if info.parent_id in result and info.id not in result:
                result.add(info.id)
                changed = True
    return result


@router.get("/api/experimental/job")
async def job_list(
    state: StateDep,
    session_id: str | None = Query(default=None, alias="sessionID"),
    kind: str | None = None,
    status: str | None = None,
    descendants: Literal["true", "false"] | None = None,
) -> Response:
    """Background jobs: shell commands started in sessions and agent tasks."""
    if session_id is None:
        sessions = set(state.sessions)
    elif descendants == "true":
        sessions = await _descendants(state, session_id)
    else:
        sessions = {session_id}
    jobs: list[JobShell | JobSubagent] = [*_shell_jobs(state, sessions)]
    for sid in sessions:
        if (runner := state.runners.get(sid)) is not None:
            jobs.extend(runner.tasks.jobs())
    if kind is not None:
        jobs = [j for j in jobs if j.kind == kind]
    if status is not None:
        jobs = [j for j in jobs if j.status == status]
    return ok(ExperimentalJobListResponse(data=list(jobs)))
