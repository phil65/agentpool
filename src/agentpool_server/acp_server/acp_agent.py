"""ACP (Agent Client Protocol) Agent implementation."""

from __future__ import annotations

from dataclasses import KW_ONLY, dataclass, field
from importlib.metadata import version as _version
from typing import TYPE_CHECKING, Any, ClassVar, assert_never

from aclientp import Agent as ACPAgent
from aclientp.exceptions import RequestError
from aclientp.schema import (
    AuthStatusCapabilities,
    CloseSessionResponse,
    DeleteSessionResponse,
    ForkSessionResponse,
    InitializeResponse,
    ListSessionsResponse,
    LoadSessionResponse,
    NewSessionResponse,
    PromptResponse,
    ResumeSessionResponse,
    SessionAdditionalDirectoriesCapabilities,
    SessionDeleteCapabilities,
    SessionForkCapabilities,
    SessionInfoUpdate,
    SessionMode,
    SessionModeState,
    SessionNotification,
    SessionOptionsCapabilities,
    SessionOptionsResponse,
    SessionStatusCapabilities,
    SessionStatusResponse,
    SessionSystemPromptCapabilities,
    SessionTitleCapabilities,
    SetSessionConfigOptionResponse,
    SetSessionModeRequest,
    SetSessionModeResponse,
    SetSessionTitleResponse,
)

from agentpool.agents.exceptions import UnknownCategoryError, UnknownModeError
from agentpool.log import get_logger
from agentpool.utils.tasks import TaskManager
from agentpool_server.acp_server.converters import (
    system_prompt_text,
    to_auth_status_response,
    to_session_config_option,
    to_session_info,
)
from agentpool_server.acp_server.session_manager import ACPSessionManager


if TYPE_CHECKING:
    from collections.abc import Callable

    from aclientp import Client
    from aclientp.schema import (
        AuthenticateRequest,
        AuthStatusRequest,
        AuthStatusResponse,
        CancelNotification,
        ClientCapabilities,
        CloseSessionRequest,
        DeleteSessionRequest,
        ForkSessionRequest,
        Implementation,
        InitializeRequest,
        ListSessionsRequest,
        LoadSessionRequest,
        LogoutRequest,
        NewSessionRequest,
        PromptRequest,
        ResumeSessionRequest,
        SessionConfigOption,
        SessionOptionsRequest,
        SessionStatusRequest,
        SetSessionConfigOptionRequest,
        SetSessionModeRequest,
        SetSessionTitleRequest,
    )
    from pydantic_ai import ModelMessage

    from agentpool import AgentPool
    from agentpool.agents.base_agent import BaseAgent
    from agentpool.messaging import MessageHistory
    from agentpool.storage.manager import SessionMetadataGeneratedEvent
    from agentpool_server.acp_server.output import SessionOutput
    from agentpool_server.acp_server.server import ACPServer
    from agentpool_server.acp_server.session import ACPSession

logger = get_logger(__name__)


async def get_session_mode_state(agent: BaseAgent) -> SessionModeState | None:
    """Get SessionModeState from an agent using its get_modes() method.

    Converts agentpool ModeCategory to ACP SessionModeState format.
    Uses the first category that looks like permissions (not model).

    Args:
        agent: Any agent with get_modes() method

    Returns:
        SessionModeState from agent's modes, None if no modes available
    """
    try:
        mode_categories = await agent.get_modes()
    except Exception:
        logger.exception("Failed to get modes from agent")
        return None
    if not mode_categories:
        return None
    # Find the permissions category (not model)
    category = next((c for c in mode_categories if c.id == "mode"), None)
    if not category:
        return None
    acp_modes = [  # Convert ModeInfo to ACP SessionMode
        # TODO: remove legacy stuff
        SessionMode(id=str(mode.value), name=mode.name, description=mode.description)
        for mode in category.available_modes
    ]
    return SessionModeState(available_modes=acp_modes, current_mode_id=category.current_mode_id)


async def get_session_config_options(agent: BaseAgent) -> list[SessionConfigOption]:
    """Get SessionConfigOptions from an agent using its get_modes() method."""
    try:
        mode_categories = await agent.get_modes()
    except Exception:
        logger.exception("Failed to get modes from agent")
        return []
    return [to_session_config_option(category) for category in mode_categories]
    # for opt in options:
    #     if opt.id == config_id:
    #         opt.current_value = value_id
    #         break


async def _set_mode(session: ACPSession, mode_id: str, *, category_id: str) -> None:
    """Switch a mode for ``session``, reporting unknown modes/categories as invalid params."""
    try:
        await session.set_mode(mode_id, category_id=category_id)
    except (UnknownModeError, UnknownCategoryError) as exc:
        raise RequestError.invalid_params({"details": str(exc)}) from exc


@dataclass
class AgentPoolACPAgent(ACPAgent):
    """Implementation of ACP Agent protocol interface for AgentPool.

    This class implements the external library's Agent protocol interface,
    bridging AgentPool with the standard ACP JSON-RPC protocol.
    """

    PROTOCOL_VERSION: ClassVar = 1

    client: Client
    """ACP connection for client communication."""

    default_agent: BaseAgent[Any, Any]
    """Default agent instance to use for new sessions.

    The agent carries its own pool reference via agent.agent_pool,
    which is used for pool-level operations and agent switching.
    """

    _: KW_ONLY

    debug_commands: bool = False
    """Whether to enable debug slash commands for testing."""

    load_skills: bool = True
    """Whether to load client-side skills from .claude/skills and .agents/skills."""

    server: ACPServer | None = field(default=None)
    """Reference to the ACPServer for pool hot-switching."""

    session_output: Callable[[ACPSession], SessionOutput] | None = None
    """Builds each session's output; v1 when None. The v2 handler serves v2 through this one."""

    def __post_init__(self) -> None:
        """Initialize derived attributes and setup after field assignment."""
        self.client_capabilities: ClientCapabilities | None = None
        self.client_info: Implementation | None = None
        self.session_manager = ACPSessionManager(pool=self.agent_pool)
        self.tasks = TaskManager()
        self._initialized = False
        # Connect to title generation signal to notify clients of session updates
        self.agent_pool.storage.metadata_generated.connect(self._on_metadata_generated)

    async def _on_metadata_generated(self, event: SessionMetadataGeneratedEvent) -> None:
        """Handle metadata generation - notify active sessions of the update."""
        session = self.session_manager.get_session(event.session_id)
        if session is None:
            logger.debug("Metadata generated for inactive session", session_id=event.session_id)
            return

        # Send session info update to client
        update = SessionInfoUpdate(title=event.metadata.title)
        notification = SessionNotification(session_id=event.session_id, update=update)
        try:
            await session.client.session_update(notification)
            logger.info(
                "Sent session info update",
                session_id=event.session_id,
                title=event.metadata.title,
            )
        except Exception:
            logger.exception("Failed to send session info update", session_id=event.session_id)

    @property
    def agent_pool(self) -> AgentPool[Any]:
        """Get the agent pool from the default agent."""
        assert self.default_agent.agent_pool
        return self.default_agent.agent_pool

        # Note: Tool registration happens after initialize() when we know client caps

    async def initialize(self, params: InitializeRequest) -> InitializeResponse:
        """Initialize the agent and negotiate capabilities."""
        version = min(params.protocol_version, self.PROTOCOL_VERSION)
        self.client_capabilities = params.client_capabilities
        self.client_info = params.client_info
        logger.info("Client info", request=params.model_dump_json())
        self._initialized = True
        response = InitializeResponse.create(
            protocol_version=version,
            name="agentpool",
            title="AgentPool",
            version=_version("agentpool"),
            load_session=True,
            list_sessions=True,
            resume_session=True,
            close_session=True,
            http_mcp_servers=True,
            sse_mcp_servers=True,
            audio_prompts=True,
            embedded_context_prompts=True,
            image_prompts=True,
        )
        assert response.agent_capabilities
        session_caps = response.agent_capabilities.session_capabilities
        assert session_caps
        session_caps.delete = SessionDeleteCapabilities()
        session_caps.additional_directories = SessionAdditionalDirectoriesCapabilities()
        # Only `append` mode: agentpool always keeps its own agent instructions
        session_caps.system_prompt = SessionSystemPromptCapabilities()
        session_caps.fork = SessionForkCapabilities()
        session_caps.status = SessionStatusCapabilities()
        session_caps.options = SessionOptionsCapabilities()
        session_caps.title = SessionTitleCapabilities()
        assert response.agent_capabilities.auth
        response.agent_capabilities.auth.status = AuthStatusCapabilities()
        return response

    def _require_initialized(self) -> None:
        if not self._initialized:
            raise RequestError.invalid_request({"details": "Connection not initialized"})

    def _require_session(self, session_id: str) -> ACPSession:
        if session := self.session_manager.get_session(session_id):
            return session
        raise RequestError.resource_not_found(session_id)

    async def _create_session(
        self,
        params: NewSessionRequest | LoadSessionRequest | ResumeSessionRequest | ForkSessionRequest,
        session_id: str | None = None,
    ) -> ACPSession:
        """Create an ACP session from a session setup request."""
        session = await self.session_manager.create_session(
            agent=self.default_agent,
            cwd=params.cwd or "",
            client=self.client,
            acp_agent=self,
            mcp_servers=params.mcp_servers,
            client_capabilities=self.client_capabilities,
            client_info=self.client_info,
            session_id=session_id,
            additional_directories=params.additional_directories,
            output_factory=self.session_output,
            system_prompt=system_prompt_text(params.system_prompt),
        )
        if params.config_options:
            await session.apply_initial_config_options(params.config_options)
        return session

    async def _attach_stored_session(
        self, params: LoadSessionRequest | ResumeSessionRequest
    ) -> ACPSession:
        """Return the active session, or recreate it from storage.

        Raises:
            RequestError: The session exists neither in memory nor in storage.
        """
        if session := self.session_manager.get_session(params.session_id):
            return session
        if not await self.session_manager.storage.load_session(params.session_id):
            raise RequestError.resource_not_found(params.session_id)
        return await self._create_session(params, session_id=params.session_id)

    async def _restore_session(self, session: ACPSession, action: str) -> MessageHistory:
        """Have the agent restore ``session`` (its history, its backend session).

        Agents that run sessions concurrently restore it on the session's own pool
        session; the others on their own, re-bound to it. Returns its conversation.

        Raises:
            RequestError: The agent could not restore it.
        """
        agent = session.agent
        pool_session = await session.pool_session()
        with agent.use_session(pool_session):
            loaded = await agent.load_session(session.session_id)
        if not loaded:
            details = f"Agent {agent.name!r} failed to {action} the session"
            raise RequestError.internal_error({"details": details})
        return pool_session.history if pool_session is not None else agent.conversation

    def _schedule_session_setup(self, session: ACPSession, *, new: bool = False) -> None:
        """Send commands, load rules and client skills once the session response is out.

        Args:
            session: The session that was set up
            new: Whether the session starts a conversation. Only then are the rules staged:
                a loaded or resumed conversation got them with its first prompt.
        """
        self.tasks.create_task(session.send_available_commands_update())
        if new:
            session.rules_loading = self.tasks.create_task(session.agent.read_rules(session.cwd))
        if self.load_skills:
            coro = session.init_client_skills()
            self.tasks.create_task(coro, name=f"init_client_skills_{session.session_id}")

    async def new_session(self, params: NewSessionRequest) -> NewSessionResponse:
        """Create a new session."""
        from agentpool.agents.acp_agent import ACPAgent as ACPAgentClient

        self._require_initialized()
        logger.info("Creating new session", default_agent=self.default_agent.name)
        session = await self._create_session(params)
        state: SessionModeState | None = None
        if isinstance(session.agent, ACPAgentClient):
            # Nested ACP agent - pass through its state directly
            if session.agent._state:
                state = session.agent._state.modes
        else:
            state = await session.mode_state()
        config_options = await session.config_options()
        # Schedule follow-up work to run after the response is sent
        self._schedule_session_setup(session, new=True)
        self.tasks.create_task(session._register_prompt_hub_commands())
        logger.info("Created session", session_id=session.session_id)
        return NewSessionResponse(
            session_id=session.session_id,
            modes=state,
            config_options=config_options or None,
        )

    async def load_session(self, params: LoadSessionRequest) -> LoadSessionResponse:
        """Load an existing session from storage.

        Delegates to the agent's load_session method, which populates agent.conversation.
        Then replays the conversation to the client via ACP notifications.
        """
        from agentpool.agents.acp_agent import ACPAgent as ACPAgentClient
        from agentpool.agents.acp_agent.acp_converters import model_messages_to_session_updates

        self._require_initialized()
        session = await self._attach_stored_session(params)
        conversation = await self._restore_session(session, "load")
        # Replay loaded conversation to client via ACP notifications
        model_messages: list[ModelMessage] = [
            model_msg for chat_msg in conversation.chat_messages for model_msg in chat_msg.messages
        ]
        for update in model_messages_to_session_updates(model_messages):
            await session.notifications.send_update(update)
        logger.info(
            "Conversation replayed",
            session_id=params.session_id,
            message_count=len(model_messages),
        )
        mode_state: SessionModeState | None = None
        if isinstance(self.default_agent, ACPAgentClient) and self.default_agent._state:
            mode_state = self.default_agent._state.modes
        config_opts = await session.config_options()
        self._schedule_session_setup(session)
        logger.info("Session loaded", session_id=params.session_id)
        return LoadSessionResponse(modes=mode_state, config_options=config_opts)

    async def list_sessions(self, params: ListSessionsRequest) -> ListSessionsResponse:
        """List available sessions.

        Delegates to the current agent's list_sessions method which handles
        fetching sessions from storage with proper titles.
        """
        self._require_initialized()
        # Get agent from first active session, or fall back to default
        first_session = next(iter(self.session_manager._active.values()), None)
        agent = first_session.agent if first_session else self.default_agent
        logger.info("Listing sessions for agent", agent_name=agent.name)
        agent_sessions = await agent.list_sessions()
        sessions = [to_session_info(s) for s in agent_sessions]
        logger.info("Listed sessions", count=len(sessions))
        return ListSessionsResponse(sessions=sessions)

    async def fork_session(self, params: ForkSessionRequest) -> ForkSessionResponse:
        """Fork an existing session.

        Creates a new session with the same state as the original.
        UNSTABLE: This feature is not part of the spec yet.
        """
        self._require_initialized()
        logger.info("Forking session", session_id=params.session_id)
        # For now, just create a new session - full fork implementation would copy state
        session = await self._create_session(params)
        config_options = await session.config_options()
        self._schedule_session_setup(session, new=True)
        return ForkSessionResponse(session_id=session.session_id, config_options=config_options)

    async def resume_session(self, params: ResumeSessionRequest) -> ResumeSessionResponse:
        """Resume an existing session without replaying history.

        Like load_session but doesn't send session/update notifications with
        previous messages. The agent restores its internal state so the
        conversation can continue.
        """
        self._require_initialized()
        session = await self._attach_stored_session(params)
        # Restore the agent's state (conversation) without replaying it to the client
        await self._restore_session(session, "restore")
        self._schedule_session_setup(session)
        logger.info("Session resumed", session_id=params.session_id)
        return ResumeSessionResponse()

    async def authenticate(self, params: AuthenticateRequest) -> None:
        """Authenticate with the agent."""
        # TODO: No auth methods are advertised, so clients seeing ``unauthenticated`` from
        # auth/status cannot sign in. Terminal auth (`claude /login`, `codex login`) would fit.
        logger.info("Authentication requested", method_id=params.method_id)

    async def auth_status(self, params: AuthStatusRequest) -> AuthStatusResponse:
        """Report the auth state of the default agent's backend."""
        # TODO: auth/status is per connection, but pools may mix backends and sessions can
        # switch agents. Consider aggregating accounts of all pool members.
        # TODO: Send auth/status_update on changes (Claude AuthStatusMessage, Codex
        # account/updated notification).
        status = await self.default_agent.get_auth_status()
        return to_auth_status_response(status)

    async def logout(self, params: LogoutRequest) -> None:
        """Log out of the current authenticated state."""
        logger.info("Logout requested")

    async def _recreate_session(self, session_id: str) -> ACPSession:
        """Recreate a session that is not active anymore (e.g. after a pool swap)."""
        stored = await self.session_manager.storage.load_session(session_id)
        if stored is None:
            raise RequestError.resource_not_found(session_id)
        logger.info("Session not active, recreating", session_id=session_id)
        session = await self.session_manager.create_session(
            agent=self.default_agent,
            cwd=stored.cwd or ".",
            client=self.client,
            acp_agent=self,
            session_id=session_id,
            client_capabilities=self.client_capabilities,
            client_info=self.client_info,
            output_factory=self.session_output,
        )
        self._schedule_session_setup(session)
        return session

    async def session_for_prompt(self, session_id: str) -> ACPSession:
        """The active session, or the stored one recreated (e.g. after a pool swap).

        Raises:
            RequestError: The session exists neither in memory nor in storage.
        """
        if session := self.session_manager.get_session(session_id):
            return session
        return await self._recreate_session(session_id)

    async def prompt(self, params: PromptRequest) -> PromptResponse:
        """Process a prompt request.

        Failures while running the agent are shown to the user in the session and end
        the turn; only protocol-level problems (unknown session, ...) become errors.
        """
        self._require_initialized()
        logger.info("Processing prompt", session_id=params.session_id)
        session = await self.session_for_prompt(params.session_id)
        stop_reason = await session.process_prompt(params.prompt)
        logger.info("Returning PromptResponse", stop_reason=stop_reason)
        return PromptResponse(stop_reason=stop_reason, usage=session.last_usage)

    async def close_session(self, params: CloseSessionRequest) -> CloseSessionResponse:
        """Close an active session and free its resources.

        Cancels any ongoing work (like session/cancel) and then
        closes the session and releases all associated resources.
        """
        logger.info("Stopping session", session_id=params.session_id)
        if session := self.session_manager.get_session(params.session_id):
            await session.cancel()
        await self.session_manager.close_session(params.session_id)
        logger.info("Session stopped", session_id=params.session_id)
        return CloseSessionResponse()

    async def delete_session(self, params: DeleteSessionRequest) -> DeleteSessionResponse:
        """Delete a session, closing it first if it is active."""
        logger.info("Deleting session", session_id=params.session_id)
        if session := self.session_manager.get_session(params.session_id):
            await session.cancel()
        await self.session_manager.close_session(params.session_id, delete=True)
        return DeleteSessionResponse()

    async def session_status(self, params: SessionStatusRequest) -> SessionStatusResponse:
        """Report whether a session is active in memory or only stored."""
        if self.session_manager.get_session(params.session_id):
            return SessionStatusResponse(status="open")
        if await self.session_manager.storage.load_session(params.session_id):
            return SessionStatusResponse(status="saved")
        raise RequestError.resource_not_found(params.session_id)

    async def session_options(self, params: SessionOptionsRequest) -> SessionOptionsResponse:
        """Report the modes and config options a session would start with."""
        session = self.session_manager.get_session(params.session_id or "")
        if session is not None:
            return SessionOptionsResponse(
                modes=await session.mode_state(),
                config_options=await session.config_options() or None,
            )
        return SessionOptionsResponse(
            modes=await get_session_mode_state(self.default_agent),
            config_options=await get_session_config_options(self.default_agent) or None,
        )

    async def set_session_title(self, params: SetSessionTitleRequest) -> SetSessionTitleResponse:
        """Rename a session and notify the client if it is active."""
        title = params.title.strip()
        await self.session_manager.storage.update_session_title(params.session_id, title)
        if session := self.session_manager.get_session(params.session_id):
            await session.notifications.send_update(SessionInfoUpdate(title=title))
        return SetSessionTitleResponse(title=title)

    async def cancel(self, params: CancelNotification) -> None:
        """Cancel operations for a session."""
        logger.info("Cancelling session", session_id=params.session_id)
        try:
            # Get session and cancel it
            if session := self.session_manager.get_session(params.session_id):
                await session.cancel()
                logger.info("Cancelled operations", session_id=params.session_id)
            else:
                logger.warning("Session not found for cancellation", session_id=params.session_id)

        except Exception:
            logger.exception("Failed to cancel session", session_id=params.session_id)

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """No extension methods are implemented."""
        raise RequestError.method_not_found(f"_{method}")

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        """Unknown extension notifications are ignored, as the protocol requires."""
        logger.debug("Ignoring extension notification", method=method)

    async def set_session_mode(self, params: SetSessionModeRequest) -> SetSessionModeResponse:
        """Set the session mode (change tool confirmation level).

        Calls set_mode directly on the agent with the mode_id, allowing the agent
        to handle mode-specific logic (e.g., acceptEdits auto-allowing edit tools).
        """
        from agentpool.agents.acp_agent import ACPAgent as ACPAgentClient

        session = self._require_session(params.session_id)
        await _set_mode(session, params.mode_id, category_id="mode")
        # Update stored mode state for ACPAgent
        if (
            isinstance(session.agent, ACPAgentClient)
            and session.agent._state
            and session.agent._state.modes
        ):
            session.agent._state.modes.current_mode_id = params.mode_id
        logger.info("Set mode", mode_id=params.mode_id, session_id=params.session_id)
        return SetSessionModeResponse()

    async def set_session_config_option(
        self, params: SetSessionConfigOptionRequest
    ) -> SetSessionConfigOptionResponse:
        """Set a session config option.

        Forwards the config option change to the agent's set_mode method
        and returns the updated config options.
        """
        session = self._require_session(params.session_id)
        logger.info(
            "Set config option",
            config_id=params.config_id,
            value=params.value,
            session_id=params.session_id,
        )
        match params.value:
            case bool():
                # Boolean config option — forward as string mode_id
                value = str(params.value).lower()
            case str():
                # Select config option — config_id maps to category_id, value to mode_id
                value = params.value
            case _ as unreachable:
                assert_never(unreachable)
        await _set_mode(session, value, category_id=params.config_id)
        config_options = await session.config_options()
        return SetSessionConfigOptionResponse(config_options=config_options)

    async def swap_pool(self, config_path: str, agent_name: str | None = None) -> list[str]:
        """Swap the agent pool with a new one from configuration.

        This coordinates the full pool swap:
        1. Closes all active sessions
        2. Delegates to server.swap_pool() for pool lifecycle
        3. Updates internal references

        Args:
            config_path: Path to the new agent configuration file
            agent_name: Optional specific agent name to use as default

        Returns:
            List of agent names in the new pool

        Raises:
            RuntimeError: If server reference is not set
            ValueError: If config is invalid or agent not found
        """
        if not self.server:
            msg = "Server reference not set - cannot swap pool"
            raise RuntimeError(msg)

        logger.info("Swapping pool", config_path=config_path, agent=agent_name)
        # 1. Close all active sessions
        closed_count = await self.session_manager.close_all_sessions()
        logger.info("Closed sessions before pool swap", count=closed_count)
        # 2. Delegate to server for pool lifecycle management - returns new agent instance
        new_agent = await self.server.swap_pool(config_path, agent_name)
        # 3. Update internal references
        self.default_agent = new_agent
        pool = new_agent.agent_pool
        assert pool
        self.session_manager._pool = pool
        agent_names = list(pool.all_agents.keys())
        logger.info("Pool swap complete", agent_names=agent_names)
        return agent_names
