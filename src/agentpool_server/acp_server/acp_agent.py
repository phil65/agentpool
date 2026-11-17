"""ACP (Agent Client Protocol) Agent implementation."""

from __future__ import annotations

from dataclasses import KW_ONLY, dataclass, field
from importlib.metadata import version as _version
from typing import TYPE_CHECKING, Any, ClassVar, assert_never

from acp import Agent as ACPAgent
from acp.exceptions import RequestError
from acp.schema import (
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
    from pydantic_ai import ModelMessage

    from acp import Client
    from acp.schema import (
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
    from agentpool import AgentPool
    from agentpool.agents.base_agent import BaseAgent
    from agentpool.storage.manager import SessionMetadataGeneratedEvent
    from agentpool_server.acp_server.server import ACPServer

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
    """Whether to load client-side skills from .claude/skills directory."""

    server: ACPServer | None = field(default=None)
    """Reference to the ACPServer for pool hot-switching."""

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

    async def _create_session(
        self,
        params: NewSessionRequest | LoadSessionRequest | ResumeSessionRequest | ForkSessionRequest,
        session_id: str | None = None,
    ) -> str:
        """Create an ACP session from a session setup request."""
        new_id = await self.session_manager.create_session(
            agent=self.default_agent,
            cwd=params.cwd or "",
            client=self.client,
            acp_agent=self,
            mcp_servers=params.mcp_servers,
            client_capabilities=self.client_capabilities,
            client_info=self.client_info,
            session_id=session_id,
            additional_directories=params.additional_directories,
            system_prompt=system_prompt_text(params.system_prompt),
        )
        if params.config_options and (session := self.session_manager.get_session(new_id)):
            await session.apply_initial_config_options(params.config_options)
        return new_id

    async def new_session(self, params: NewSessionRequest) -> NewSessionResponse:
        """Create a new session."""
        from agentpool.agents.acp_agent import ACPAgent as ACPAgentClient

        if not self._initialized:
            raise RuntimeError("Agent not initialized")

        logger.info("Creating new session", default_agent=self.default_agent.name)
        try:
            session_id = await self._create_session(params)
            state: SessionModeState | None = None
            config_options: list[SessionConfigOption] = []

            if session := self.session_manager.get_session(session_id):
                if isinstance(session.agent, ACPAgentClient):
                    # Nested ACP agent - pass through its state directly
                    if session.agent._state:
                        state = session.agent._state.modes
                    # Also get config_options from nested agent
                    config_options = await get_session_config_options(session.agent)
                else:
                    # Use unified helpers for all other agents
                    state = await get_session_mode_state(session.agent)
                    config_options = await get_session_config_options(session.agent)
        except Exception:
            logger.exception("Failed to create new session")
            raise
        else:
            # Schedule available commands update after session response is returned
            if session := self.session_manager.get_session(session_id):
                # Schedule task to run after response is sent
                self.tasks.create_task(session.send_available_commands_update())
                self.tasks.create_task(session.agent.load_rules(session.cwd))
                self.tasks.create_task(session._register_prompt_hub_commands())
                if self.load_skills:
                    coro_4 = session.init_client_skills()
                    self.tasks.create_task(coro_4, name=f"init_client_skills_{session_id}")
            logger.info("Created session", session_id=session_id)

            return NewSessionResponse(
                session_id=session_id,
                modes=state,
                config_options=config_options if config_options else None,
            )

    async def load_session(self, params: LoadSessionRequest) -> LoadSessionResponse:
        """Load an existing session from storage.

        Delegates to the agent's load_session method, which populates agent.conversation.
        Then replays the conversation to the client via ACP notifications.
        """
        from agentpool.agents.acp_agent import ACPAgent as ACPAgentClient
        from agentpool.agents.acp_agent.acp_converters import model_messages_to_session_updates

        if not self._initialized:
            raise RuntimeError("Agent not initialized")

        try:
            # Get or create an active session wrapper
            session = self.session_manager.get_session(params.session_id)
            if not session:
                session_id = await self._create_session(params, session_id=params.session_id)
                session = self.session_manager.get_session(session_id)

            if not session:
                logger.error("Failed to create session")
                return LoadSessionResponse()

            # Load session via agent - this populates agent.conversation
            if not await self.default_agent.load_session(params.session_id):
                logger.warning("Agent failed to load session", session_id=params.session_id)
                return LoadSessionResponse()

            # Replay loaded conversation to client via ACP notifications
            if msgs := self.default_agent.conversation.chat_messages:
                model_messages: list[ModelMessage] = []
                for chat_msg in msgs:
                    if chat_msg.messages:
                        model_messages.extend(chat_msg.messages)
                for update in model_messages_to_session_updates(model_messages):
                    await session.notifications.send_update(update)
                # await session.notifications.send_update(
                #     UsageUpdate(used=chat_msg.usage.total_tokens)
                # )
                # await session.notifications.send_update(
                #     SessionInfoUpdate(
                #         session_id=params.session_id,
                #         title="",
                #         updated_at=model_messages[-1].timestamp,
                #     )
                # )

                logger.info(
                    "Conversation replayed",
                    session_id=params.session_id,
                    message_count=len(model_messages),
                )
            mode_state: SessionModeState | None = None
            if isinstance(self.default_agent, ACPAgentClient) and self.default_agent._state:
                mode_state = self.default_agent._state.modes
            config_opts = await get_session_config_options(self.default_agent)
            # Schedule post-load tasks
            self.tasks.create_task(session.send_available_commands_update())
            self.tasks.create_task(session.agent.load_rules(session.cwd))
            logger.info("Session loaded", session_id=params.session_id)
            return LoadSessionResponse(modes=mode_state, config_options=config_opts)
        except Exception:
            logger.exception("Failed to load session", session_id=params.session_id)
            return LoadSessionResponse()

    async def list_sessions(self, params: ListSessionsRequest) -> ListSessionsResponse:
        """List available sessions.

        Delegates to the current agent's list_sessions method which handles
        fetching sessions from storage with proper titles.

        Uses a short TTL cache to avoid redundant expensive storage reads
        when clients request the list multiple times in quick succession.
        """
        if not self._initialized:
            raise RuntimeError("Agent not initialized")

        # Get agent from first active session, or fall back to default
        first_session = next(iter(self.session_manager._active.values()), None)
        agent = first_session.agent if first_session else self.default_agent
        try:
            logger.info("Listing sessions for agent", agent_name=agent.name)
            agent_sessions = await agent.list_sessions()
            logger.info("Agent returned sessions", count=len(agent_sessions))
            sessions = [to_session_info(s) for s in agent_sessions]
        except Exception:
            logger.exception("Failed to list sessions")
            return ListSessionsResponse(sessions=[])
        else:
            logger.info("Listed sessions", count=len(sessions))
            return ListSessionsResponse(sessions=sessions)

    async def fork_session(self, params: ForkSessionRequest) -> ForkSessionResponse:
        """Fork an existing session.

        Creates a new session with the same state as the original.
        UNSTABLE: This feature is not part of the spec yet.
        """
        if not self._initialized:
            raise RuntimeError("Agent not initialized")

        logger.info("Forking session", session_id=params.session_id)
        # For now, just create a new session - full fork implementation would copy state
        session_id = await self._create_session(params)
        session = self.session_manager.get_session(session_id)
        config_options = await get_session_config_options(session.agent) if session else []
        return ForkSessionResponse(session_id=session_id, config_options=config_options)

    async def resume_session(self, params: ResumeSessionRequest) -> ResumeSessionResponse:
        """Resume an existing session without replaying history.

        Like load_session but doesn't send session/update notifications with
        previous messages. The agent restores its internal state so the
        conversation can continue.

        UNSTABLE: This feature is not part of the spec yet.
        """
        if not self._initialized:
            raise RuntimeError("Agent not initialized")

        try:
            # Get or create session wrapper
            session = self.session_manager.get_session(params.session_id)
            if not session:
                session_id = await self._create_session(params, session_id=params.session_id)
                session = self.session_manager.get_session(session_id)

            if not session:
                logger.error("Failed to create session for resume")
                return ResumeSessionResponse()

            # Load session state in the agent (populates conversation) but don't replay to client
            # This restores the agent's internal state so it can continue the conversation
            if not await self.default_agent.load_session(params.session_id):
                logger.warning("Agent failed to load session state", session_id=params.session_id)
                # Continue anyway - session wrapper is created, agent may still work

            # Schedule post-resume tasks
            self.tasks.create_task(session.send_available_commands_update())
            self.tasks.create_task(session.agent.load_rules(session.cwd))
            logger.info("Session resumed", session_id=params.session_id)
            return ResumeSessionResponse()

        except Exception:
            logger.exception("Failed to resume session", session_id=params.session_id)
            return ResumeSessionResponse()

    async def authenticate(self, params: AuthenticateRequest) -> None:
        """Authenticate with the agent."""
        logger.info("Authentication requested", method_id=params.method_id)

    async def auth_status(self, params: AuthStatusRequest) -> AuthStatusResponse:
        """Report the auth state of the default agent's backend."""
        status = await self.default_agent.get_auth_status()
        return to_auth_status_response(status)

    async def logout(self, params: LogoutRequest) -> None:
        """Log out of the current authenticated state."""
        logger.info("Logout requested")

    async def prompt(self, params: PromptRequest) -> PromptResponse:
        """Process a prompt request."""
        if not self._initialized:
            raise RuntimeError("Agent not initialized")

        logger.info("Processing prompt", session_id=params.session_id)
        session = self.session_manager.get_session(params.session_id)
        # Auto-recreate session if not found (e.g., after pool swap)
        if not session:
            logger.info("Session not found, recreating", session_id=params.session_id)
            # Try to get cwd from stored session data
            cwd = "."
            try:
                stored = await self.session_manager.storage.load_session(params.session_id)
                if stored and stored.cwd:
                    cwd = stored.cwd
            except Exception:  # noqa: BLE001
                pass  # Use default cwd

            try:
                # Recreate session with same ID using default agent
                await self.session_manager.create_session(
                    agent=self.default_agent,
                    cwd=cwd,
                    client=self.client,
                    acp_agent=self,
                    session_id=params.session_id,
                    client_capabilities=self.client_capabilities,
                    client_info=self.client_info,
                )
                if session := self.session_manager.get_session(params.session_id):
                    # Initialize session extras
                    self.tasks.create_task(session.send_available_commands_update())
                    self.tasks.create_task(session.agent.load_rules(session.cwd))
            except Exception:
                logger.exception("Failed to recreate session", session_id=params.session_id)
                return PromptResponse(stop_reason="end_turn")

        try:
            if not session:
                raise ValueError(f"Session {params.session_id} not found")  # noqa: TRY301
            stop_reason = await session.process_prompt(params.prompt)
            # Return the actual stop reason from the session
        except Exception as e:
            logger.exception("Failed to process prompt", session_id=params.session_id)
            msg = f"Error processing prompt: {e}"
            if session:
                # Send error notification asynchronously to avoid blocking response
                name = f"error_notification_{params.session_id}"
                self.tasks.create_task(session._send_error_notification(msg), name=name)

            return PromptResponse(stop_reason="end_turn")
        else:
            response = PromptResponse(
                stop_reason=stop_reason,
                usage=session.last_usage,
            )
            logger.info("Returning PromptResponse", stop_reason=stop_reason)
            return response

    async def close_session(self, params: CloseSessionRequest) -> CloseSessionResponse:
        """Close an active session and free its resources.

        Cancels any ongoing work (like session/cancel) and then
        closes the session and releases all associated resources.
        """
        logger.info("Stopping session", session_id=params.session_id)
        try:
            # Cancel ongoing work first
            if session := self.session_manager.get_session(params.session_id):
                await session.cancel()
            # Close and release session resources
            await self.session_manager.close_session(params.session_id)
            logger.info("Session stopped", session_id=params.session_id)
        except Exception:
            logger.exception("Failed to close session", session_id=params.session_id)
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
        agent = session.agent if session else self.default_agent
        return SessionOptionsResponse(
            modes=await get_session_mode_state(agent),
            config_options=await get_session_config_options(agent) or None,
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
        return {"example": "response"}

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        return None

    async def set_session_mode(
        self, params: SetSessionModeRequest
    ) -> SetSessionModeResponse | None:
        """Set the session mode (change tool confirmation level).

        Calls set_mode directly on the agent with the mode_id, allowing the agent
        to handle mode-specific logic (e.g., acceptEdits auto-allowing edit tools).
        """
        from agentpool.agents.acp_agent import ACPAgent as ACPAgentClient

        session = self.session_manager.get_session(params.session_id)
        if not session:
            logger.warning("Session not found for mode switch", session_id=params.session_id)
            return None
        try:
            # Call set_mode directly - agent handles mode-specific logic
            await session.agent.set_mode(params.mode_id, category_id="mode")
            # Update stored mode state for ACPAgent
            if (
                isinstance(session.agent, ACPAgentClient)
                and session.agent._state
                and session.agent._state.modes
            ):
                session.agent._state.modes.current_mode_id = params.mode_id

            logger.info("Set mode", mode_id=params.mode_id, session_id=params.session_id)
            return SetSessionModeResponse()

        except Exception:
            logger.exception("Failed to set session mode", session_id=params.session_id)
            return None

    async def set_session_config_option(
        self, params: SetSessionConfigOptionRequest
    ) -> SetSessionConfigOptionResponse | None:
        """Set a session config option.

        Forwards the config option change to the agent's set_mode method
        and returns the updated config options.
        """
        session = self.session_manager.get_session(params.session_id)
        if not session or not session.agent:
            msg = "Session not found for config option change"
            logger.warning(msg, session_id=params.session_id)
            return None
        logger.info(
            "Set config option",
            config_id=params.config_id,
            value=params.value,
            session_id=params.session_id,
        )
        try:
            match params.value:
                case bool():
                    # Boolean config option — forward as string mode_id
                    bool_str = str(params.value).lower()
                    await session.agent.set_mode(bool_str, category_id=params.config_id)
                case str():
                    # Select config option — config_id maps to category_id, value to mode_id
                    await session.agent.set_mode(params.value, category_id=params.config_id)
                case _ as unreachable:
                    assert_never(unreachable)
            # Return updated config options
            config_options = await get_session_config_options(session.agent)
            return SetSessionConfigOptionResponse(config_options=config_options)
        except Exception:
            logger.exception("Failed to set session config option", session_id=params.session_id)
            return None

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
