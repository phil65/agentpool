"""ACP (Agent Client Protocol) server implementation for agentpool.

This module provides the main server class for exposing AgentPool via
the Agent Client Protocol.
"""

import asyncio
from typing import TYPE_CHECKING, Any, Literal, Self

from aclientp import serve

from agentpool import AgentPool
from agentpool.log import get_logger
from agentpool.models.manifest import AgentsManifest
from agentpool_server import BaseServer
from agentpool_server.acp_server.acp_agent import AgentPoolACPAgent
from agentpool_server.acp_server.v2_agent import AgentPoolV2Agent


if TYPE_CHECKING:
    from aclientp import Transport
    from upathtools import JoinablePathLike

    from agentpool.agents.base_agent import BaseAgent


logger = get_logger(__name__)


class ACPServer(BaseServer):
    """ACP (Agent Client Protocol) server for agentpool using external library.

    Provides a bridge between agentpool's Agent system and the standard ACP
    JSON-RPC protocol using the external acp library for robust communication.

    The actual client communication happens via the AgentSideConnection created
    when start() is called, which communicates with the external process over stdio.
    """

    def __init__(
        self,
        pool: AgentPool[Any],
        *,
        name: str | None = None,
        debug_messages: bool = False,
        debug_file: str | None = None,
        debug_commands: bool = False,
        agent: str | None = None,
        load_skills: bool = True,
        config_path: str | None = None,
        transport: Transport = "stdio",
        max_protocol: Literal[1, 2] = 2,
    ) -> None:
        """Initialize ACP server with configuration.

        Args:
            pool: AgentPool containing available agents
            name: Optional Server name (auto-generated if None)
            debug_messages: Whether to enable debug message logging
            debug_file: File path for debug message logging
            debug_commands: Whether to enable debug slash commands for testing
            agent: Optional specific agent name to use (defaults to first agent)
            load_skills: Whether to load client-side skills from .claude/skills and .agents/skills
            config_path: Path to the configuration file (for tracking/hot-switching)
            transport: Transport configuration ("stdio", "websocket", or transport object)
            max_protocol: Highest ACP version to serve. With 2, each client gets the
                version it asks for in ``initialize`` (v2 clients v2, others v1); 1
                serves every client v1.
        """
        super().__init__(pool, name=name, raise_exceptions=True)
        self.debug_messages = debug_messages
        self.debug_file = debug_file
        self.debug_commands = debug_commands
        self.agent = agent
        self.load_skills = load_skills
        self.config_path = config_path
        self.transport: Transport = transport
        self.max_protocol: Literal[1, 2] = max_protocol

    @classmethod
    def from_config(
        cls,
        config: JoinablePathLike | AgentsManifest,
        *,
        debug_messages: bool = False,
        debug_file: str | None = None,
        debug_commands: bool = False,
        agent: str | None = None,
        load_skills: bool = True,
        transport: Transport = "stdio",
        max_protocol: Literal[1, 2] = 2,
    ) -> Self:
        """Create ACP server from configuration path or manifest.

        Args:
            config: Path to YAML config file or pre-loaded AgentsManifest
            debug_messages: Enable saving JSON messages to file
            debug_file: Path to debug file
            debug_commands: Enable debug slash commands for testing
            agent: Optional specific agent name to use (defaults to first agent)
            load_skills: Whether to load client-side skills from .claude/skills and .agents/skills
            transport: Transport configuration ("stdio", "websocket", or transport object)
            max_protocol: Highest ACP version to serve (see ``ACPServer``)

        Returns:
            Configured ACP server instance with agent pool
        """
        # AgentPool handles both path and manifest
        pool = AgentPool(manifest=config, main_agent_name=agent)

        # Determine config_path for tracking
        config_path = config.config_file_path if isinstance(config, AgentsManifest) else str(config)

        server = cls(
            pool,
            debug_messages=debug_messages,
            debug_file=debug_file or "acp-debug.jsonl" if debug_messages else None,
            debug_commands=debug_commands,
            agent=agent,
            load_skills=load_skills,
            config_path=config_path,
            transport=transport,
            max_protocol=max_protocol,
        )
        agent_names = list(server.pool.all_agents.keys())

        # Validate specified agent exists if provided
        if agent and agent not in pool.manifest.agents:
            msg = f"Specified agent {agent!r} not found in config. Available agents: {agent_names}"
            raise ValueError(msg)

        server.log.info("Created ACP server", agent_names=agent_names, config_path=config_path)
        if agent:
            server.log.info("ACP session agent", agent=agent)
        return server

    def _resolve_default_agent(self) -> BaseAgent[Any, Any]:
        """Resolve the default agent from name or get pool's default agent.

        Returns:
            The resolved agent instance

        Raises:
            RuntimeError: If no agents are available
            ValueError: If specified agent doesn't exist
        """
        # Use specified agent name or fall back to pool's default agent
        if self.agent:
            if self.agent not in self.pool.all_agents:
                raise ValueError(f"Agent {self.agent!r} not found in pool")
            return self.pool.all_agents[self.agent]
        return self.pool.main_agent

    async def _start_async(self) -> None:
        """Start the ACP server (blocking async - runs until stopped)."""
        transport_name = (
            type(self.transport).__name__ if not isinstance(self.transport, str) else self.transport
        )
        self.log.info("Starting ACP server", transport=transport_name)
        # Resolve agent instance from name
        default_agent = self._resolve_default_agent()
        self.log.info("Using default agent", agent=default_agent.name)
        # The default agent is resolved per connection: after swap_pool, the new pool's

        def create_acp_agent(conn: Any) -> AgentPoolACPAgent:
            return AgentPoolACPAgent(
                conn,
                default_agent=self._resolve_default_agent(),
                debug_commands=self.debug_commands,
                load_skills=self.load_skills,
                server=self,
            )

        def create_v2_agent(conn: Any) -> AgentPoolV2Agent:
            return AgentPoolV2Agent(
                conn,
                default_agent=self._resolve_default_agent(),
                debug_commands=self.debug_commands,
                load_skills=self.load_skills,
                server=self,
            )

        debug_file = self.debug_file if self.debug_messages else None
        self.log.info("ACP server started", max_protocol=self.max_protocol)
        try:
            await serve(
                create_acp_agent,
                transport=self.transport,
                v2_agent=create_v2_agent if self.max_protocol >= 2 else None,  # noqa: PLR2004
                shutdown_event=self._shutdown_event,
                debug_file=debug_file,
            )
        except asyncio.CancelledError:
            self.log.info("ACP server shutdown requested")
            raise
        except KeyboardInterrupt:
            self.log.info("ACP server shutdown requested")
        except Exception:
            self.log.exception("ACP server error")

    async def swap_pool(
        self, config_path: str, agent_name: str | None = None
    ) -> BaseAgent[Any, Any]:
        """Swap the current pool with a new one from config.

        This method handles the full lifecycle of swapping pools:
        1. Validates the new configuration
        2. Creates and initializes the new pool
        3. Cleans up the old pool
        4. Updates internal references

        Args:
            config_path: Path to the new agent configuration file
            agent_name: Optional specific agent name to use as default

        Returns:
            The resolved default agent instance from the new pool

        Raises:
            ValueError: If config is invalid or specified agent not found
            FileNotFoundError: If config file doesn't exist
        """
        # 1. Parse and validate new config before touching current pool
        self.log.info("Loading new pool configuration", config_path=config_path)
        new_manifest = AgentsManifest.from_file(config_path)
        new_pool = AgentPool(manifest=new_manifest)
        # 2. Validate agent exists in new pool if specified
        agent_names = list(new_pool.all_agents.keys())
        if not agent_names:
            msg = "New configuration contains no agents"
            raise ValueError(msg)
        if agent_name and agent_name not in agent_names:
            msg = f"Agent {agent_name!r} not found in new config. Available: {agent_names}"
            raise ValueError(msg)
        # 3. Enter new pool context first (so we can roll back if it fails)
        try:
            await new_pool.__aenter__()
        except Exception as e:
            self.log.exception("Failed to initialize new pool")
            msg = f"Failed to initialize new pool: {e}"
            raise ValueError(msg) from e
        # 4. Exit old pool context
        old_pool = self.pool
        try:
            await old_pool.__aexit__(None, None, None)
        except Exception:
            self.log.exception("Error closing old pool (continuing with swap)")
        # 5. Update references
        self.pool = new_pool
        self.agent = agent_name
        self.config_path = config_path
        # 6. Resolve and return the default agent instance
        default_agent = self._resolve_default_agent()
        self.log.info(
            "Pool swapped successfully", agent_names=agent_names, default_agent=default_agent.name
        )
        return default_agent
