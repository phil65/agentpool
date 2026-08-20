"""Agent, command, MCP, LSP, formatter, and logging routes."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, HttpUrl

from wolfharness.capabilities.combined_toolset import CombinedToolsetCapability
from wolfharness.log import get_logger
from wolfharness.mcp_server.manager import MCPManager
from wolfharness_config.mcp_server import (
    SSEMCPServerConfig,
    StdioMCPServerConfig,
    StreamableHTTPMCPServerConfig,
)
from wolfharness_server.opencode_server.converters import to_mcp_status
from wolfharness_server.opencode_server.dependencies import StateDep
from wolfharness_server.opencode_server.models import (
    Agent,
    AuthInfo,
    Command,
    ConnectionStatus,
    FormatterStatus,
    LogRequest,
    LspStatus,
    McpAuthorizationResponse,
    McpResource,
    MCPStatus,
    OpenCodeCapabilities,
    ProviderAuthAuthorization,
    ProviderAuthMethod,
    Session,
    SkillInfo,
    WorkspaceCreateRequest,
    WorkspaceEventConnectionStatus,
    WorkspaceInfo,
    WorktreeCreateRequest,
    WorktreeInfo,
    WorktreeRemoveRequest,
    WorktreeResetRequest,
)


router = APIRouter(tags=["agent"])

# Module-level logger for route-level logging
logger = get_logger(__name__)


def _extract_hints(template: str | None) -> list[str]:
    """Extract input hints from a command template.

    Matches the native OpenCode Command.hints() utility which finds
    $N placeholders (e.g. $1, $2) and $ARGUMENTS.

    Args:
        template: The command template string. None is treated as empty.

    Returns:
        Sorted list of unique hint strings found in the template.
    """
    if not template:
        return []
    hints: list[str] = []
    numbered = re.findall(r"\$\d+", template)
    if numbered:
        hints.extend(sorted(set(numbered), key=lambda x: int(x[1:])))
    if "$ARGUMENTS" in template:
        hints.append("$ARGUMENTS")
    return hints


class AddMCPServerRequest(BaseModel):
    """Request to add an MCP server dynamically."""

    command: str | None = None
    """Command to run (for stdio servers)."""

    args: list[str] | None = None
    """Arguments for the command."""

    url: str | None = None
    """URL for HTTP/SSE servers."""

    env: dict[str, str] | None = None
    """Environment variables for the server."""

    session_id: str | None = None
    """Optional session ID to bind the MCP server to (session-scoped)."""


def _find_mcp_manager(state: Any, session_id: str | None = None) -> MCPManager | None:
    """Find the MCPManager from the agent's capabilities.

    Queries the ``ExtensionRegistry`` at SESSION scope (falling back to
    AGENT scope when no session is available) to find an ``MCPManager``
    instance among the visible capabilities.

    Args:
        state: Server state
        session_id: Optional session ID for scoped capability lookup.
    """
    from wolfharness.capabilities.extension_registry import Scope, ScopeLevel

    agent = state.agent
    host_ctx = agent.host_context
    registry = host_ctx.extension_registry if host_ctx is not None else None

    if registry is not None:
        effective_session_id = session_id or agent.session_id or ""
        if effective_session_id:
            scope = Scope(
                level=ScopeLevel.SESSION,
                agent_name=agent.name,
                session_id=effective_session_id,
            )
        else:
            scope = Scope(level=ScopeLevel.AGENT, agent_name=agent.name)
        caps = registry.get_visible_capabilities(scope)
    else:
        caps = agent._all_capabilities

    for provider in caps:
        match provider:
            case MCPManager():
                return provider
            case CombinedToolsetCapability():
                for nested in provider.capabilities:
                    if isinstance(nested, MCPManager):
                        return nested
    return None


@router.get("/agent")
async def list_agents(state: StateDep) -> list[Agent]:
    """List available agents from the AgentPool.

    Returns all agents with their configurations, suitable for the agent
    switcher UI. All agents are marked as primary (visible in switcher).
    The default agent is always first in the returned list.
    """
    ctx = state.agent.host_context
    assert ctx is not None, "AgentPool is not initialized"
    default_name = ctx.main_agent_name
    agents = [
        Agent(
            name=name,
            display_name=agent.display_name,
            description=agent.description or f"Agent: {name}",
            mode="primary",
            default=(name == default_name),
        )
        for name, agent in ctx.manifest.agents.items()
    ]
    if not agents:
        return [Agent(name="default", description="Default agent", mode="primary", default=True)]
    agents.sort(key=lambda a: (not a.default, a.name))
    return agents


@router.get("/skill")
async def list_skills(state: StateDep) -> list[SkillInfo]:
    """List all available skills.

    Skills are specialized capabilities available to agents.
    Returns skills from:
    1. Local filesystem (via SkillsManager)
    2. MCP providers (via CombinedToolsetCapability)
    """
    ctx = state.agent.host_context
    if ctx is None:
        return []

    skills: list[SkillInfo] = []

    # 1. Get MCP provider skills from skill resolver first
    # These will be overridden by local skills if names conflict
    pool = state.agent._agent_pool
    skill_resolver = pool.skill_resolver if pool is not None else None
    if skill_resolver is not None:
        try:
            for provider_name in skill_resolver.list_providers():
                provider = skill_resolver.get_provider(provider_name)
                if provider is None:
                    continue
                mcp_skills = await provider.list_skills()
                for entry in mcp_skills:
                    try:
                        content = await provider.read_skill(entry.name) or ""
                    except Exception:  # noqa: BLE001
                        content = ""

                    skills.append(
                        SkillInfo(
                            name=entry.name,
                            description=entry.description,
                            location=entry.uri,
                            content=content,
                        )
                    )
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to get MCP skills", error=str(e))

    # 2. Get local filesystem skills from SkillsManager (takes priority)
    # Local skills override MCP skills with the same name
    if ctx.skills_registry is not None:
        for skill in ctx.skills_registry.list_skills():
            # Remove any existing MCP skill with the same name (local takes priority)
            existing = next((s for s in skills if s.name == skill.name), None)
            if existing:
                skills.remove(existing)

            skills.append(
                SkillInfo(
                    name=skill.name,
                    description=skill.description,
                    location=str(skill.skill_path),
                    content=skill.load_instructions(),
                )
            )

    return skills


@router.get("/command")
async def list_commands(state: StateDep) -> list[Command]:
    """List available slash commands.

    Commands include:
    - MCP prompts as commands
    - Skill commands from skill_bridge (if available) or skill_provider
    """
    commands: list[Command] = []

    # Add MCP prompts as commands (source="mcp")
    try:
        prompts = await state.agent.list_prompts()
        commands.extend([
            Command(name=p.name, description=p.description, source="mcp", hints=[]) for p in prompts
        ])
    except Exception:  # noqa: BLE001
        pass

    # Add skill commands from skill_bridge if available
    logger.debug(
        "list_commands debug",
        skill_bridge_exists=state.skill_bridge is not None,
        skill_capabilities_count=len(state.pool.skill_capabilities)
        if isinstance(state.pool.skill_capabilities, list)
        else 0,
    )
    if state.skill_bridge is not None:
        for skill_cmd in state.skill_bridge.get_skill_commands():
            # For virtual skills (from MCP), fetch instructions from resolver
            template = ""
            if state.pool.skill_resolver is not None:
                try:
                    resolved = await state.pool.skill_resolver.resolve(skill_cmd.name)
                    template = resolved.load_instructions()
                except Exception:  # noqa: BLE001
                    # Fall back to local load if resolver fails
                    try:
                        template = skill_cmd.skill.load_instructions()
                    except ValueError:
                        template = ""
            else:
                try:
                    template = skill_cmd.skill.load_instructions()
                except ValueError:
                    template = ""
            commands.append(
                Command(
                    name=skill_cmd.name,
                    description=skill_cmd.description,
                    source="command",
                    template=template,
                    hints=_extract_hints(template),
                )
            )
    # Fallback: get skills directly from skill_resolver if skill_bridge not available
    elif state.pool.skill_resolver is not None:
        try:
            for provider_name in state.pool.skill_resolver.list_providers():
                provider = state.pool.skill_resolver.get_provider(provider_name)
                if provider is None:
                    continue
                provider_skills = await provider.list_skills()
                logger.debug(
                    "Got skills from skill_resolver",
                    skill_count=len(provider_skills),
                )
                for entry in provider_skills:
                    # Use provider's read_skill for content
                    try:
                        template = await provider.read_skill(entry.name) or ""
                    except Exception:  # noqa: BLE001
                        template = ""
                    commands.append(
                        Command(
                            name=entry.name,
                            description=entry.description,
                            source="command",
                            template=template,
                            hints=_extract_hints(template),
                        )
                    )
        except Exception:  # noqa: BLE001
            pass

    return commands


@router.get("/mcp")
async def get_mcp_status(state: StateDep) -> dict[str, MCPStatus]:
    """Get MCP server status."""
    # NOTE: GET /mcp uses state.agent.get_mcp_server_info() (which delegates
    # to self.mcp / host_context.mcp directly) instead of _find_mcp_manager
    # because _find_mcp_manager queries the ExtensionRegistry for
    # MCPManager instances, but the MCPManager lives on self.mcp (MessageNode
    # property), not in capabilities. _find_mcp_manager is kept for sibling
    # /mcp/* routes (POST, connect, disconnect) that need the manager for
    # mutations.
    server_info = await state.agent.get_mcp_server_info()
    return {name: to_mcp_status(status) for name, status in server_info.items()}


@router.post("/mcp")
async def add_mcp_server(request: AddMCPServerRequest, state: StateDep) -> MCPStatus:
    """Add an MCP server dynamically.

    Supports stdio servers (command + args) or HTTP/SSE servers (url).
    If session_id is provided, the server is bound to that session only.
    """
    # Build the config based on request
    # Note: client_id is auto-generated for internal identification;
    # display_name uses configured name if available
    config: SSEMCPServerConfig | StdioMCPServerConfig | StreamableHTTPMCPServerConfig
    if request.url:
        # HTTP-based server
        if request.url.endswith("/sse"):
            config = SSEMCPServerConfig(url=HttpUrl(request.url))
        else:
            config = StreamableHTTPMCPServerConfig(url=HttpUrl(request.url))
    elif request.command:  # Stdio server
        args = request.args or []
        config = StdioMCPServerConfig(command=request.command, args=args, env=request.env)
    else:
        detail = "Must provide either 'command' (for stdio) or 'url' (for HTTP/SSE)"
        raise HTTPException(status_code=400, detail=detail)

    # Find the MCPManager and add the server
    manager = _find_mcp_manager(state, session_id=request.session_id)
    if manager is None:
        raise HTTPException(status_code=400, detail="No MCP manager available")

    try:
        await manager.setup_server(config, add_to_config=True)
        return MCPStatus(
            name=config.client_id, display_name=config.display_name, status="connected"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to add MCP server: {e}") from e


@router.post("/mcp/{name}/connect")
async def connect_mcp_server(
    name: str,
    state: StateDep,
    session_id: str | None = None,
) -> bool:
    """Connect (start) an MCP server by name.

    Finds the server config and sets up the connection via MCPManager.
    If session_id is provided, operates on the session's agent.
    """
    manager = _find_mcp_manager(state, session_id=session_id)
    if manager is None:
        raise HTTPException(status_code=400, detail="No MCP manager available")
    # Find matching server config
    config = next((s for s in manager.servers if s.client_id == name), None)
    if config is None:
        raise HTTPException(status_code=404, detail=f"MCP server not found: {name}")
    try:
        await manager.setup_server(config)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to connect: {e}") from e
    else:
        return True


@router.post("/mcp/{name}/disconnect")
async def disconnect_mcp_server(
    name: str,
    state: StateDep,
    session_id: str | None = None,
) -> bool:
    """Disconnect (stop) an MCP server by name.

    Removes the provider from the manager's active providers.
    If session_id is provided, operates on the session's agent.
    """
    manager = _find_mcp_manager(state, session_id=session_id)
    if manager is None:
        raise HTTPException(status_code=400, detail="No MCP manager available")
    # Find and remove the matching provider
    provider = next((p for p in manager.providers if p.name.endswith(f"_{name}")), None)
    if provider is None:
        raise HTTPException(status_code=404, detail=f"MCP server not found: {name}")
    try:
        await provider.__aexit__(None, None, None)
        manager.providers.remove(provider)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to disconnect: {e}") from e
    else:
        return True


@router.post("/mcp/{name}/auth")
async def start_mcp_auth(name: str, state: StateDep) -> McpAuthorizationResponse:
    """Start OAuth authentication flow for an MCP server.

    Returns the authorization URL to open in a browser.
    """
    _ = state
    # MCP OAuth is not yet supported in AgentPool's MCP implementation
    raise HTTPException(status_code=501, detail=f"MCP OAuth not yet supported for: {name}")


@router.post("/mcp/{name}/auth/callback")
async def mcp_auth_callback(
    name: str,
    state: StateDep,
    code: str | None = None,
) -> MCPStatus:
    """Complete OAuth authentication for an MCP server."""
    _ = state, code
    raise HTTPException(status_code=501, detail=f"MCP OAuth not yet supported for: {name}")


@router.post("/mcp/{name}/auth/authenticate")
async def mcp_auth_authenticate(name: str, state: StateDep) -> MCPStatus:
    """Start OAuth flow and wait for callback (opens browser)."""
    _ = state
    raise HTTPException(status_code=501, detail=f"MCP OAuth not yet supported for: {name}")


@router.delete("/mcp/{name}/auth")
async def remove_mcp_auth(name: str, state: StateDep) -> dict[str, bool]:
    """Remove OAuth credentials for an MCP server."""
    _ = state
    # Stub - no MCP OAuth credential storage yet
    return {"success": True}


@router.post("/log")
async def log(request: LogRequest, state: StateDep) -> bool:
    """Write a log entry."""
    _ = state  # unused for now
    logger = get_logger(request.service)
    extra = request.extra or {}
    match request.level:
        case "debug":
            logger.debug(request.message, **extra)
        case "info":
            logger.info(request.message, **extra)
        case "warn":
            logger.warning(request.message, **extra)
        case "error":
            logger.error(request.message, **extra)
    return True


@router.get("/experimental/console")
async def get_console_state() -> dict[str, Any]:
    """Return console state for OpenCode TUI compatibility.

    Provides an empty ConsoleState so that TUI can bootstrap without
    waiting for proxy catch-all to time out.
    """
    return {
        "consoleManagedProviders": [],
        "activeOrgName": None,
        "switchableOrgCount": 0,
    }


@router.get("/experimental/resource")
async def list_mcp_resources(state: StateDep) -> dict[str, McpResource]:
    """Get all available MCP resources from connected servers.

    Returns a dictionary mapping resource keys to ``McpResource`` objects.
    Keys are formatted as ``{escaped_client}:{uri}`` matching the upstream
    OpenCode catalog key format (percent-encoding ``%`` and ``:`` in the
    client name via :func:`make_catalog_key`).

    Uses the ``ExtensionRegistry`` to discover ``ResourceAccess`` providers
    at SESSION scope (POOL + AGENT + SESSION).  Per-provider failures are
    isolated — a single provider erroring does not prevent other providers'
    resources from being returned.
    """
    import asyncio

    from wolfharness.capabilities.extension_registry import Scope, ScopeLevel
    from wolfharness.capabilities.resource_protocols import (
        NamedResourceAccess,
        ResourceAccess,
        make_catalog_key,
    )

    result: dict[str, McpResource] = {}

    agent = state.agent
    host_ctx = agent.host_context
    registry = host_ctx.extension_registry if host_ctx is not None else None
    if registry is not None:
        session_id = agent.session_id or ""
        if session_id:
            scope = Scope(
                level=ScopeLevel.SESSION,
                agent_name=agent.name,
                session_id=session_id,
            )
<<<<<<< Updated upstream
            for cap, res in zip(resource_caps, results, strict=False):
                if isinstance(res, BaseException):
                    continue
                for resource in res:
                    # ResourceEntry doesn't have a .client field;
                    # use the capability class name as the client identifier.
                    client = type(cap).__name__
                    client_name = client.replace("/", "_")
                    resource_name = resource.name.replace("/", "_")
                    result[f"{client_name}:{resource_name}"] = McpResource(
                        name=resource.uri,
                        uri=resource.uri,
                        description=resource.description,
                        mime_type=resource.mime_type,
                        client=client,
                    )
    except Exception:  # noqa: BLE001
        return {}
=======
        else:
            scope = Scope(level=ScopeLevel.AGENT, agent_name=agent.name)
        resource_caps = registry.get_resource_access(scope)
>>>>>>> Stashed changes
    else:
        caps = agent._all_capabilities
        resource_caps = [cap for cap in caps if isinstance(cap, ResourceAccess)]

    if not resource_caps:
        return result

    results = await asyncio.gather(
        *(cap.list_resources() for cap in resource_caps),
        return_exceptions=True,
    )
    for cap, res in zip(resource_caps, results, strict=False):
        if isinstance(res, BaseException):
            continue
        client_name = (
            cap.client_name if isinstance(cap, NamedResourceAccess) else type(cap).__name__
        )
        for resource in res:
            key = make_catalog_key(client_name, resource.uri)
            result[key] = McpResource(
                name=resource.name,
                uri=resource.uri,
                description=resource.description,
                mime_type=resource.mime_type,
                client=client_name,
            )
    return result


@router.post("/experimental/worktree")
async def create_worktree(request: WorktreeCreateRequest, state: StateDep) -> WorktreeInfo:
    """Create a new git worktree for isolated agent work."""
    from wolfharness.utils.worktree import create_worktree

    repo_dir = state.agent.env.cwd or state.working_dir
    try:
        name, branch, directory = await create_worktree(repo_dir, request.name)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return WorktreeInfo(name=name, branch=branch, directory=directory)


@router.get("/experimental/worktree")
async def list_worktrees(state: StateDep) -> list[str]:
    """List all sandbox worktree directories."""
    from wolfharness.utils.worktree import list_worktrees

    repo_dir = state.agent.env.cwd or state.working_dir
    return await list_worktrees(repo_dir)


@router.delete("/experimental/worktree")
async def remove_worktree(request: WorktreeRemoveRequest, state: StateDep) -> bool:
    """Remove a git worktree and delete its branch."""
    from wolfharness.utils.worktree import remove_worktree

    repo_dir = state.agent.env.cwd or state.working_dir
    try:
        await remove_worktree(repo_dir, request.directory)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return True


@router.post("/experimental/worktree/reset")
async def reset_worktree(request: WorktreeResetRequest, state: StateDep) -> bool:
    """Reset a worktree branch to the primary default branch."""
    from wolfharness.utils.worktree import reset_worktree

    repo_dir = state.agent.env.cwd or state.working_dir
    try:
        await reset_worktree(repo_dir, request.directory)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return True


async def _get_project_id(state: StateDep) -> str:
    """Return the current OpenCode project ID for compatibility responses."""
    from wolfharness_storage.project_store import ProjectStore

    project = await ProjectStore(state.storage).get_or_create(state.working_dir)
    return project.project_id


def _workspace_from_worktree(
    directory: str,
    *,
    branch: str | None,
    project_id: str,
) -> WorkspaceInfo:
    """Map AgentPool worktree data to OpenCode's workspace shape."""
    name = Path(directory).name
    return WorkspaceInfo(
        id=name,
        type="worktree",
        branch=branch,
        name=name,
        directory=directory,
        extra=None,
        project_id=project_id,
    )


@router.get("/experimental/workspace")
async def list_workspaces(state: StateDep) -> list[WorkspaceInfo]:
    """List worktree-backed workspaces for OpenCode TUI compatibility."""
    from wolfharness.utils.worktree import list_worktrees

    repo_dir = state.agent.env.cwd or state.working_dir
    project_id = await _get_project_id(state)
    return [
        _workspace_from_worktree(directory, branch=None, project_id=project_id)
        for directory in await list_worktrees(repo_dir)
    ]


@router.get("/experimental/workspace/status")
async def get_workspace_status(
    state: StateDep,
    directory: str | None = None,
    workspace: str | None = None,
) -> list[WorkspaceEventConnectionStatus]:
    """Return workspace event-stream connection statuses.

    OpenCode SDK expects ``Array<WorkspaceEventConnectionStatus>``. We do not
    maintain per-workspace event-stream connections, so the result is empty.

    Args:
        state: Server state (kept for request context compatibility).
        directory: Optional directory filter (unused, kept for contract).
        workspace: Optional workspace filter (unused, kept for contract).
    """
    _ = directory, workspace, state
    return []


@router.get("/experimental/capabilities")
async def get_capabilities() -> OpenCodeCapabilities:
    """Return experimental server capabilities for the OpenCode client.

    OpenCode requires the ``backgroundSubagents`` field. We do not yet support
    background subagents, so this is reported as ``false``.
    """
    return OpenCodeCapabilities(background_subagents=False)


@router.post("/experimental/workspace")
async def create_workspace(request: WorkspaceCreateRequest, state: StateDep) -> WorkspaceInfo:
    """Create a worktree-backed workspace through OpenCode's workspace API."""
    from wolfharness.utils.worktree import create_worktree

    if request.type != "worktree":
        raise HTTPException(status_code=400, detail="Only worktree workspaces are supported")

    repo_dir = state.agent.env.cwd or state.working_dir
    try:
        _, branch, directory = await create_worktree(repo_dir, request.id)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    return _workspace_from_worktree(
        directory,
        branch=branch,
        project_id=await _get_project_id(state),
    )


@router.delete("/experimental/workspace/{workspace_id}")
async def remove_workspace(workspace_id: str, state: StateDep) -> WorkspaceInfo | None:
    """Remove a worktree-backed workspace by OpenCode workspace ID."""
    from wolfharness.utils.worktree import list_worktrees, remove_worktree

    repo_dir = state.agent.env.cwd or state.working_dir
    project_id = await _get_project_id(state)
    directory = next(
        (item for item in await list_worktrees(repo_dir) if Path(item).name == workspace_id),
        None,
    )
    if directory is None:
        return None

    workspace = _workspace_from_worktree(directory, branch=None, project_id=project_id)
    try:
        await remove_worktree(repo_dir, directory)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return workspace


@router.get("/experimental/session")
async def list_sessions_global(
    state: StateDep,
    directory: str | None = None,
    roots: bool | None = None,
    start: int | None = None,
    cursor: int | None = None,
    search: str | None = None,
    limit: int | None = None,
    archived: bool | None = None,
) -> list[Session]:
    """List sessions globally across all projects.

    Supports pagination via cursor (timestamp-based).
    """
    from wolfharness_server.opencode_server.converters import session_data_to_opencode

    effective_limit = limit or 100
    sessions: list[Session] = []
    for data in await state.agent.list_sessions(
        cwd=directory or state.agent.env.cwd, limit=effective_limit
    ):
        session = session_data_to_opencode(data)
        sessions.append(session)
    # Apply filters
    if roots:
        sessions = [s for s in sessions if s.parent_id is None]
    if start is not None:
        sessions = [s for s in sessions if s.time.updated >= start]
    if cursor is not None:
        sessions = [s for s in sessions if s.time.updated < cursor]
    if search:
        lower_search = search.lower()
        sessions = [s for s in sessions if lower_search in s.title.lower()]
    return sessions


@router.get("/experimental/tool/ids")
async def list_tool_ids(state: StateDep) -> list[str]:
    """List all available tool IDs.

    Returns a list of tool names that are available to the agent.
    OpenCode expects: Array<string>
    """
    try:
        tools = await state.agent._get_all_tools()
        return [tool.name for tool in tools]
    except Exception:  # noqa: BLE001
        return []


class ToolListItem(BaseModel):
    """Tool info matching OpenCode SDK ToolListItem type."""

    id: str
    description: str
    parameters: dict[str, Any]


@router.get("/experimental/tool")
async def list_tools_with_schemas(  # noqa: D417
    state: StateDep,
    provider: str | None = None,
    model: str | None = None,
) -> list[ToolListItem]:
    """List tools with their JSON schemas.

    Args:
        provider: Optional provider filter (not used currently)
        model: Optional model filter (not used currently)

    Returns list of tools matching OpenCode's ToolListItem format:
    - id: string
    - description: string
    - parameters: unknown (JSON schema)
    """
    _ = provider, model  # Currently unused, for future filtering

    try:
        result = []
        for tool in await state.agent._get_all_tools():
            # Extract parameters schema from the OpenAI function schema
            params = tool.schema["function"]["parameters"]
            item = ToolListItem(id=tool.name, description=tool.description or "", parameters=params)
            result.append(item)
    except Exception:  # noqa: BLE001
        return []
    else:
        return result


@router.get("/lsp")
async def get_lsp_status(state: StateDep) -> list[LspStatus]:
    """Get LSP server status.

    Returns status of all running LSP servers.
    """
    servers: list[LspStatus] = []
    for server_id, server_state in state.lsp_manager._servers.items():
        status: ConnectionStatus = "connected" if server_state.initialized else "error"
        servers.append(
            LspStatus(id=server_id, name=server_id, status=status, root=server_state.root_uri or "")
        )
    return servers


@router.get("/formatter")
async def get_formatter_status(state: StateDep) -> list[FormatterStatus]:
    """Get formatter status.

    Returns empty list - formatters not supported yet.
    """
    _ = state
    return []


@router.get("/provider/auth")
async def get_provider_auth(state: StateDep) -> dict[str, list[ProviderAuthMethod]]:
    """Get provider authentication methods.

    Returns available OAuth providers with their auth methods.
    """
    return state.auth_service.methods()


@router.post("/provider/{provider_id}/oauth/authorize")
async def oauth_authorize(provider_id: str, state: StateDep) -> ProviderAuthAuthorization:
    """Start OAuth authorization flow for a provider.

    Returns URL and instructions for the user to complete authorization.
    """
    try:
        return await state.auth_service.authorize(provider_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.post("/provider/{provider_id}/oauth/callback")
async def oauth_callback(
    provider_id: str,
    state: StateDep,
    code: str | None = None,
    device_code: str | None = None,
    verifier: str | None = None,
) -> bool:
    """Handle OAuth callback/code exchange."""
    try:
        return await state.auth_service.callback(
            provider_id, code=code, device_code=device_code, verifier=verifier
        )
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.put("/auth/{provider_id}")
async def set_auth(provider_id: str, info: AuthInfo, state: StateDep) -> bool:
    """Set authentication credentials for a provider."""
    try:
        return await state.auth_service.set_credentials(provider_id, info)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.delete("/auth/{provider_id}")
async def remove_auth(provider_id: str, state: StateDep) -> bool:
    """Remove authentication credentials for a provider."""
    try:
        return await state.auth_service.remove_credentials(provider_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
