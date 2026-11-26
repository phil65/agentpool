"""ACP Session Manager - delegates to pool's SessionManager."""

import asyncio
from typing import TYPE_CHECKING, Any, Self

from aclientp.schema import ClientCapabilities

from agentpool.log import get_logger
from agentpool.mcp_server.shared import SharedMCPConnections
from agentpool.sessions import SessionData
from agentpool_server.acp_server.session import ACPSession
from agentpool_server.acp_server.v1_output import V1SessionOutput


if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from aclientp import Client
    from aclientp.schema import Implementation, McpServer

    from agentpool import AgentPool
    from agentpool.agents.base_agent import BaseAgent
    from agentpool.storage.manager import StorageManager
    from agentpool_server.acp_server.acp_agent import AgentPoolACPAgent
    from agentpool_server.acp_server.output import SessionOutput


logger = get_logger(__name__)


class ACPSessionManager:
    """Manages ACP sessions, delegating persistence to pool's StorageManager.

    This is a thin coordinator that:
    - Creates and tracks active ACP sessions
    - Delegates session persistence to pool.storage (StorageManager)
    - Handles ACP-specific session initialization
    """

    def __init__(self, pool: AgentPool[Any]) -> None:
        """Initialize ACP session manager.

        Args:
            pool: Agent pool containing StorageManager for persistence
        """
        self._pool = pool
        self._active: dict[str, ACPSession] = {}
        self._lock = asyncio.Lock()
        self._command_update_task: asyncio.Task[None] | None = None
        self.client_mcp = SharedMCPConnections(name="client_mcp")
        """The MCP servers clients send with their sessions, shared by the sessions."""
        logger.info("Initialized ACP session manager")

    @property
    def storage(self) -> StorageManager:
        """Get the pool's storage manager for persistence."""
        return self._pool.storage

    async def create_session(
        self,
        agent: BaseAgent[Any, Any],
        cwd: str,
        client: Client,
        acp_agent: AgentPoolACPAgent,
        mcp_servers: Sequence[McpServer] | None = None,
        session_id: str | None = None,
        client_capabilities: ClientCapabilities | None = None,
        client_info: Implementation | None = None,
        additional_directories: Sequence[str] = (),
        system_prompt: str | None = None,
        output_factory: Callable[[ACPSession], SessionOutput] | None = None,
    ) -> ACPSession:
        """Create a new ACP session.

        Args:
            agent: The agent instance to use for this session
            cwd: Working directory for the session
            client: ACP client connection
            acp_agent: ACP agent instance
            mcp_servers: Optional MCP server configurations
            session_id: Optional specific session ID (generated if None)
            client_capabilities: Client capabilities for tool registration
            client_info: Client implementation info (name, version)
            additional_directories: Additional workspace roots
            system_prompt: Client-provided system instructions
            output_factory: Builds the session's output (v1 when omitted)

        Returns:
            The created session

        Raises:
            ValueError: A session with this ID is already active
        """
        from agentpool.utils.identifiers import generate_session_id

        async with self._lock:
            # Generate session ID if not provided
            if session_id is None:
                session_id = generate_session_id()

            # Check for existing session
            if session_id in self._active:
                logger.warning("Session ID already exists", session_id=session_id)
                msg = f"Session {session_id} already exists"
                raise ValueError(msg)
            # Persist session data. A loaded/resumed session keeps its stored record
            # (title, creation time, SDK session id, ...) and only gets refreshed.
            metadata = {"protocol": "acp", "mcp_server_count": len(mcp_servers or [])}
            if stored := await self.storage.load_session(session_id):
                data = stored.with_agent(agent.name).with_metadata(**metadata)
                data = data.model_copy(update={"cwd": cwd or stored.cwd})
            else:
                data = SessionData(
                    session_id=session_id, agent_name=agent.name, cwd=cwd, metadata=metadata
                )
            await self.storage.save_session(data)
            # Create the ACP-specific runtime session
            session = ACPSession(
                session_id=session_id,
                agent=agent,
                cwd=cwd,
                client=client,
                mcp_servers=mcp_servers,
                additional_directories=additional_directories,
                system_prompt=system_prompt,
                acp_agent=acp_agent,
                client_capabilities=client_capabilities or ClientCapabilities(),
                client_info=client_info,
                manager=self,
                output_factory=output_factory or V1SessionOutput,
            )
            session.register_update_callback(self._on_commands_updated)
            await session.initialize()
            await session.initialize_mcp_servers()
            self._active[session_id] = session
            logger.info("Created ACP session", session_id=session_id, agent=agent.name)
            return session

    def get_session(self, session_id: str) -> ACPSession | None:
        """Get an active session by ID."""
        return self._active.get(session_id)

    async def resume_session(
        self,
        session_id: str,
        client: Client,
        acp_agent: AgentPoolACPAgent,
        client_capabilities: ClientCapabilities | None = None,
        client_info: Implementation | None = None,
    ) -> ACPSession | None:
        """Resume a session from storage.

        Args:
            session_id: Session identifier
            client: ACP client connection
            acp_agent: ACP agent instance
            client_capabilities: Client capabilities
            client_info: Client implementation info (name, version)

        Returns:
            Resumed ACPSession if found, None otherwise
        """
        async with self._lock:
            # Check if already active
            if session_id in self._active:
                return self._active[session_id]
            # Try to load from pool's session store
            data = await self.storage.load_session(session_id)
            if data is None:
                logger.warning("Session not found in store", session_id=session_id)
                return None

            # Validate agent still exists
            if data.agent_name not in self._pool.all_agents:
                msg = "Session agent no longer exists"
                logger.warning(msg, session_id=session_id, agent=data.agent_name)
                return None
            agent = self._pool.all_agents[data.agent_name]
            # Reconstruct ACP session
            session = ACPSession(
                session_id=session_id,
                agent=agent,
                cwd=data.cwd or "",
                client=client,
                mcp_servers=None,  # MCP servers would need to be re-provided
                acp_agent=acp_agent,
                client_capabilities=client_capabilities or ClientCapabilities(),
                client_info=client_info,
                manager=self,
            )
            session.register_update_callback(self._on_commands_updated)
            await session.initialize()
            self._active[session_id] = session
            logger.info("Resumed ACP session", session_id=session_id)
            return session

    async def close_session(self, session_id: str, *, delete: bool = False) -> None:
        """Close and optionally delete a session.

        Args:
            session_id: Session identifier to close
            delete: Whether to also delete from persistent storage
        """
        async with self._lock:
            session = self._active.pop(session_id, None)

        if session:
            await session.close()
            logger.info("Closed ACP session", session_id=session_id)

        if delete:
            await self.storage.delete_session(session_id)
            logger.info("Deleted session from store", session_id=session_id)

    async def update_session_agent(self, session_id: str, agent_name: str) -> None:
        """Update the agent for a session and persist.

        Args:
            session_id: Session identifier
            agent_name: New agent name
        """
        if not self._active.get(session_id):
            return
        # Load, update, and save session data
        data = await self.storage.load_session(session_id)
        if data:
            updated = data.with_agent(agent_name)
            await self.storage.save_session(updated)

    async def list_sessions(self, *, active_only: bool = False) -> list[str]:
        """List session IDs.

        Args:
            active_only: Only return currently active sessions

        Returns:
            List of session IDs
        """
        if active_only:
            return list(self._active.keys())

        return await self.storage.list_session_ids()

    async def close_all_sessions(self) -> int:
        """Close all active sessions.

        This is used during pool hot-switching to cleanly shut down
        all sessions before swapping the pool.

        Returns:
            Number of sessions that were closed
        """
        async with self._lock:
            sessions = list(self._active.values())
            self._active.clear()

        closed_count = 0
        for session in sessions:
            try:
                await session.close()
                closed_count += 1
            except Exception:
                logger.exception("Error closing session", session=session.session_id)
        logger.info("Closed all sessions.", count=closed_count)
        return closed_count

    async def __aenter__(self) -> Self:
        """Async context manager entry."""
        return self

    async def __aexit__(self, *args: object) -> None:
        """Async context manager exit - close all active sessions."""
        sessions = list(self._active.values())
        self._active.clear()
        for session in sessions:
            try:
                await session.close()
            except Exception:
                logger.exception("Error closing session", session=session.session_id)
        await self.client_mcp.close()
        logger.info("Closed all %d ACP sessions", len(sessions))

    def _on_commands_updated(self) -> None:
        """Handle command updates by notifying all active sessions."""
        task = asyncio.create_task(self._update_all_sessions_commands())
        self._command_update_task = task

    async def _update_all_sessions_commands(self) -> None:
        """Update available commands for all active sessions."""
        async with self._lock:
            for session in self._active.values():
                try:
                    await session.send_available_commands_update()
                except Exception:
                    msg = "Failed to update commands"
                    logger.exception(msg, session_id=session.session_id)
