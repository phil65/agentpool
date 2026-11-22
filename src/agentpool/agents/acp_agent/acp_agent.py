"""ACP Agent - MessageNode wrapping an external ACP agent.

This module provides an agent implementation that communicates with external
ACP (Agent Client Protocol) servers via stdio, or with remote ones over Streamable
HTTP or WebSocket (experimental), enabling integration of any ACP-compatible agent.

The ACPAgent class acts as an ACP client, spawning an ACP server subprocess
and communicating with it via JSON-RPC over stdio. This allows:
- Integration of external ACP-compatible agents (like claude-agent-acp)
- Composition with native agents via connections, teams, etc.
- Full ACP protocol support including file operations and terminals

Example:
    ```python
    from agentpool.models.acp_agents import ACPAgentConfig

    config = ACPAgentConfig(
        command="claude-agent-acp",
        name="claude_coder",
        cwd="/path/to/project",
    )
    agent = ACPAgent.from_config(config)
    async with agent:
        result = await agent.run("Write a hello world program")
        print(result.content)
    ```
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Self
import uuid

from aclientp import InitializeRequest
from aclientp.agent import ACPAgentAPI
import anyio
from pydantic import HttpUrl

from agentpool.agents.acp_agent.session_state import ACPSessionState
from agentpool.agents.base_agent import BaseAgent
from agentpool.agents.capabilities import AgentCapabilities as RuntimeCapabilities
from agentpool.agents.events import RunStartedEvent, StreamCompleteEvent
from agentpool.agents.events.reconstructor import MessageReconstructor
from agentpool.agents.exceptions import (
    AgentNotInitializedError,
    UnknownCategoryError,
    UnknownModeError,
)
from agentpool.log import get_logger
from agentpool.messaging import ChatMessage
from agentpool.utils.streams import merge_queue_into_iterator
from agentpool.utils.subprocess_utils import SubprocessError, run_with_process_monitor
from agentpool.utils.token_breakdown import calculate_usage_from_parts


if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine, Sequence
    from types import TracebackType

    from aclientp.client.connection import ClientSideConnection
    from aclientp.schema import (
        Implementation,
        RequestPermissionRequest,
        RequestPermissionResponse,
        SelectSessionConfigOption,
    )
    from aclientp.schema.capabilities import AgentCapabilities
    from aclientp.schema.mcp import McpServer
    from anyio.abc import ByteReceiveStream, ByteSendStream, Process
    from evented_config import EventConfig
    from exxec import ExecutionEnvironment
    from pydantic_ai import UserContent
    from slashed import BaseCommand
    from tokonomics.model_discovery.model_info import ModelInfo

    from agentpool.agents.acp_agent.client_handler import ACPClientHandler
    from agentpool.agents.auth import AuthStatus
    from agentpool.agents.events import RichAgentStreamEvent
    from agentpool.agents.modes import ModeCategory
    from agentpool.common_types import AnyEventHandlerType
    from agentpool.delegation import AgentPool
    from agentpool.hooks import AgentHooks
    from agentpool.messaging import MessageHistory
    from agentpool.models.acp_agents import BaseACPAgentConfig
    from agentpool.resource_providers import ResourceProvider
    from agentpool.sessions import SessionData
    from agentpool.ui.base import InputProvider
    from agentpool_config.mcp_server import MCPServerConfig

logger = get_logger(__name__)

CANCEL_GRACE_PERIOD = 5.0
"""Seconds to wait for the remote agent to end a turn after ``session/cancel``."""


def get_updated_at(date_str: str | None) -> datetime:
    from agentpool.utils.time_utils import get_now

    updated_at = get_now()
    if date_str:
        with contextlib.suppress(ValueError, AttributeError):
            updated_at = datetime.fromisoformat(date_str)
    return updated_at


class _Monitor:
    """Runs startup steps, failing early if the agent's subprocess exits (if there is one)."""

    def __init__(self, process: Process | None) -> None:
        self._process = process

    async def run[T](self, coro: Callable[[], Coroutine[Any, Any, T]], context: str) -> T:
        if self._process is None:
            return await coro()
        return await run_with_process_monitor(self._process, coro, context=context)


class ACPAgent[TDeps = None](BaseAgent[TDeps, str]):
    """Agent backed by an external ACP agent: a subprocess, or a remote agent over the network.

    The subprocess is started from a command or resolved from the ACP registry; a remote
    agent is reached by URL (WebSocket for ``ws(s)://``, Streamable HTTP otherwise).
    This allows integrating any ACP-compatible agent into the agentpool pool, enabling
    composition with native agents via connections, teams, etc.
    """

    AGENT_TYPE: ClassVar = "acp"
    CAPABILITIES: ClassVar[RuntimeCapabilities] = RuntimeCapabilities(
        multimodal_input=True,
        live_model_discovery=True,
        background_events=True,
    )

    def __init__(
        self,
        *,
        # Source (exactly one of command, registry_id or url must be set)
        command: str | None = None,
        args: list[str] | None = None,
        registry_id: str | None = None,
        url: str | None = None,
        headers: dict[str, str] | None = None,
        # Identity
        name: str | None = None,
        description: str | None = None,
        display_name: str | None = None,
        provider_type: str = "acp",
        # Environment
        cwd: str | None = None,
        env_vars: dict[str, str] | None = None,
        execution_env: ExecutionEnvironment | None = None,
        client_execution_env: ExecutionEnvironment | None = None,
        # ACP initialization
        init_request: InitializeRequest | None = None,
        # Tools
        tool_providers: list[ResourceProvider] | None = None,
        mcp_servers: Sequence[str | MCPServerConfig] | None = None,
        # Runtime options
        deps_type: type[TDeps] | None = None,
        input_provider: InputProvider | None = None,
        agent_pool: AgentPool[Any] | None = None,
        enable_logging: bool = True,
        event_configs: Sequence[EventConfig] | None = None,
        event_handlers: Sequence[AnyEventHandlerType] | None = None,
        auto_approve: bool = False,
        commands: Sequence[BaseCommand] | None = None,
        hooks: AgentHooks | None = None,
        session_id: str | None = None,
    ) -> None:
        """Initialize ACP agent (exactly one of ``command``, ``registry_id`` or ``url``).

        Args:
            command: Executable of the agent subprocess
            args: Arguments for the command (or for the registry agent)
            registry_id: ID of an agent in the ACP registry (e.g. "goose"), whose
                distribution provides the command and its env vars
            url: URL of a remote agent (``ws(s)://`` for WebSocket, else Streamable HTTP)
            headers: HTTP headers for the remote agent (e.g. authorization)
            name: Agent name (defaults to the command, registry ID or URL)
            description: Agent description
            display_name: Human-readable display name
            provider_type: Provider name recorded on the agent's model responses
            cwd: Working directory of the subprocess and its sessions
            env_vars: Environment variables for the subprocess
            execution_env: Execution environment of the agent
            client_execution_env: Environment for the file and terminal requests of the
                remote agent (defaults to ``execution_env``)
            init_request: ACP initialize request (defaults to agentpool's capabilities)
            tool_providers: Resource providers whose tools are exposed to the agent
                through an MCP bridge
            mcp_servers: MCP servers to pass to the agent's sessions
            deps_type: Type of dependencies for the agent
            input_provider: Provider for user input and permission requests
            agent_pool: Agent pool for coordination
            enable_logging: Whether to enable database logging
            event_configs: Event trigger configurations
            event_handlers: Event handlers for this agent
            auto_approve: Approve all permission requests of the agent without asking
            commands: Slash commands to register with this agent
            hooks: Agent hooks for intercepting agent behavior
            session_id: Session to load on connect instead of creating a new one
        """
        from agentpool.mcp_server.tool_bridge import ToolManagerBridge

        sources = [s for s in (command, registry_id, url) if s]
        if len(sources) != 1:
            msg = "Exactly one of 'command', 'registry_id' or 'url' must be provided"
            raise ValueError(msg)
        source = sources[0]

        super().__init__(
            name=name or source,
            description=description,
            deps_type=deps_type,
            display_name=display_name,
            mcp_servers=mcp_servers,
            agent_pool=agent_pool,
            enable_logging=enable_logging,
            event_configs=event_configs,
            env=execution_env,
            input_provider=input_provider,
            event_handlers=event_handlers,
            commands=commands,
            hooks=hooks,
        )
        # Permission handling
        self.auto_approve = auto_approve
        # Command / registry
        self._command = command
        self._args = args or []
        self._registry_id = registry_id
        # Remote agent (WebSocket or Streamable HTTP)
        self._url = url
        self._headers = headers or {}
        self._transport_stack: contextlib.AsyncExitStack | None = None
        # Environment
        self._cwd = cwd
        self._env_vars = env_vars or {}
        self._client_env = client_execution_env
        # ACP initialization
        self._init_request = init_request or InitializeRequest.create_for_package(
            "agentpool", prompt_suggestions=True, compaction=True, notices=True
        )
        # Tools
        self._tool_providers = tool_providers or []
        # Provider type for model messages
        self._provider_type = provider_type
        # ACP-specific state
        self.acp_permission_callback: (
            Callable[[RequestPermissionRequest], Awaitable[RequestPermissionResponse]] | None
        ) = None
        self._process: Process | None = None
        self._connection: ClientSideConnection | None = None
        self._api: ACPAgentAPI | None = None
        self._client_handler: ACPClientHandler | None = None
        self._agent_info: Implementation | None = None
        self._caps: AgentCapabilities | None = None
        self._sdk_session_id: str | None = session_id
        self._state: ACPSessionState | None = None
        self._extra_mcp_servers: list[McpServer] = []
        self._sessions_cache: list[SessionData] | None = None
        # Create bridge (not started yet) - pass injection_manager for mid-run injection support
        self._tool_bridge = ToolManagerBridge(node=self, injection_manager=self._injection_manager)
        # Track the prompt task for cancellation
        self._prompt_task: asyncio.Task[Any] | None = None

    @classmethod
    def from_config(
        cls,
        config: BaseACPAgentConfig,
        *,
        event_handlers: Sequence[AnyEventHandlerType] | None = None,
        input_provider: InputProvider | None = None,
        deps_type: type[TDeps] | None = None,
        agent_pool: AgentPool[Any] | None = None,
    ) -> Self:
        """Create an ACPAgent from a config object."""
        # Merge config-level handlers with provided handlers
        config_handlers = config.get_event_handlers()
        merged_handlers: list[AnyEventHandlerType] = [*config_handlers, *(event_handlers or [])]
        return cls(
            command=config.get_command(),
            args=config.get_args(),
            registry_id=config.get_registry_id(),
            url=config.get_url(),
            headers=config.get_headers(),
            # Identity
            name=config.name,
            description=config.description,
            display_name=config.display_name,
            provider_type=config.type,
            # Environment
            cwd=config.cwd,
            env_vars=config.env_vars,
            execution_env=config.get_execution_environment(),
            client_execution_env=config.get_client_execution_environment(),
            # ACP initialization
            init_request=InitializeRequest.create_for_package(
                "agentpool",
                allow_terminal=config.allow_terminal,
                allow_file_operations=config.allow_file_operations,
                prompt_suggestions=True,
                compaction=True,
                notices=True,
            ),
            # Tools
            tool_providers=config.get_tool_providers(),
            mcp_servers=config.mcp_servers,
            # Runtime options
            event_handlers=merged_handlers or None,
            event_configs=list(config.triggers),
            input_provider=input_provider,
            agent_pool=agent_pool,
            deps_type=deps_type,
            auto_approve=config.auto_approve,
            hooks=config.hooks.get_agent_hooks(),
        )

    @property
    def client_env(self) -> ExecutionEnvironment:
        """Execution environment for handling subprocess requests.

        This is used by ACPClientHandler for file/terminal operations requested
        by the subprocess. Falls back to the agent's main env if not explicitly set.
        """
        return self._client_env if self._client_env is not None else self.env

    async def _setup_toolsets(self) -> None:
        """Initialize toolsets and start bridge if needed."""
        from aclientp.schema import HttpMcpServer

        if not self._tool_providers:
            return
        # Add all tool providers to tool manager
        for provider in self._tool_providers:
            self.tools.add_provider(provider)
        await self._tool_bridge.start()

        url = HttpUrl(self._tool_bridge.url)
        mcp_config = HttpMcpServer(name=self._tool_bridge.resolved_server_name, url=url)
        self._extra_mcp_servers.append(mcp_config)

    async def __aenter__(self) -> Self:
        """Start the subprocess (or connect to the remote agent) and initialize ACP."""
        await super().__aenter__()
        await self._setup_toolsets()
        try:
            if self._url is not None:
                reader, writer = await self._connect_remote(self._url)
                process: Process | None = None
            else:
                process = await self._start_process()
                assert process.stdout is not None
                assert process.stdin is not None
                reader, writer = process.stdout, process.stdin
            monitor = _Monitor(process)
            await monitor.run(lambda: self._initialize(reader, writer), "ACP initialization")
            # Load existing session or create new one
            if session_to_load := self._sdk_session_id:
                self._sdk_session_id = None
                result = await monitor.run(
                    lambda: self.load_session(session_to_load), "ACP session load"
                )
                if result is None:
                    self.log.warning(
                        "Failed to load session, creating new one",
                        session_id=session_to_load,
                    )
                    await monitor.run(self._create_session, "ACP session creation")
            else:
                await monitor.run(self._create_session, "ACP session creation")
        except SubprocessError as e:
            raise RuntimeError(str(e)) from e
        await anyio.sleep(0.3)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Clean up subprocess and connection."""
        await self._cleanup()
        await super().__aexit__(exc_type, exc_val, exc_tb)

    async def _resolve_command(self) -> list[str]:
        """Resolve the command to run, either from explicit command or registry."""
        from aclientp.registry import fetch_agent

        if self._command:
            return [self._command, *self._args]
        # Registry-based resolution
        assert self._registry_id is not None
        agent = await fetch_agent(self._registry_id)
        if agent is None:
            raise RuntimeError(f"Agent {self._registry_id!r} not found in ACP registry")
        # Merge registry env vars (agent-specific take precedence)
        self._env_vars = {**agent.dist.env, **self._env_vars}
        return await agent.prepare(self._args)

    async def _start_process(self) -> Process:
        """Start the ACP server subprocess."""
        cmd = await self._resolve_command()
        self.log.info("Starting ACP subprocess", command=cmd)
        env = {**os.environ, **self._env_vars}
        cwd = str(self._cwd) if self._cwd else None
        self._process = await anyio.open_process(cmd, env=env, cwd=cwd)
        if not self._process.stdin or not self._process.stdout:
            raise RuntimeError("Failed to create subprocess pipes")
        return self._process

    async def _connect_remote(self, url: str) -> tuple[ByteReceiveStream, ByteSendStream]:
        """Connect to a remote agent (WebSocket for ws(s)://, else Streamable HTTP)."""
        from aclientp import connect_remote_transport

        self.log.info("Connecting to remote ACP agent", url=url)
        stack = contextlib.AsyncExitStack()
        try:
            transport = connect_remote_transport(url, headers=self._headers)
            reader, writer = await stack.enter_async_context(transport)
        except BaseException:
            await stack.aclose()
            raise
        self._transport_stack = stack
        return reader, writer

    async def _initialize(self, reader: ByteReceiveStream, writer: ByteSendStream) -> None:
        """Initialize the ACP connection."""
        from aclientp.client.connection import ClientSideConnection

        from agentpool.agents.acp_agent.client_handler import ACPClientHandler

        self._state = ACPSessionState(session_id="")
        self._client_handler = ACPClientHandler(self, self._state, self._input_provider)
        self._connection = ClientSideConnection(
            to_client=self._client_handler,
            input_stream=writer,
            output_stream=reader,
        )
        self._api = ACPAgentAPI(self._connection)
        init_response = await self._connection.initialize(self._init_request)
        self._agent_info = init_response.agent_info
        self._caps = init_response.agent_capabilities
        self.log.info("ACP connection initialized", agent_info=self._agent_info)

    async def _create_session(self) -> None:
        """Create a new ACP session with configured MCP servers."""
        from agentpool.agents.acp_agent.acp_converters import mcp_config_to_acp
        from agentpool.agents.acp_agent.helpers import filter_servers_by_capabilities

        if not self._api:
            raise AgentNotInitializedError

        # Collect all MCP servers (extra + from mcp_servers list)
        all_servers = self._extra_mcp_servers[:]
        # Add servers from mcp_servers (converted to ACP format)
        if self.mcp.servers:
            all_servers.extend([mcp_config_to_acp(config) for config in self.mcp.servers])
        mcp_servers = filter_servers_by_capabilities(all_servers, self._caps, logger=self.log)
        response = await self._api.new_session(self._cwd, mcp_servers=mcp_servers or None)
        self._sdk_session_id = response.session_id
        if self._state:
            self._state.session_id = self._sdk_session_id
            if response.config_options:
                self._state.config_options = list(response.config_options)
            self._state.modes = response.modes
        self.log.info("ACP session created", session_id=self._sdk_session_id, model=self.model_name)

    async def _cleanup(self) -> None:
        """Clean up resources."""
        if self._tool_bridge._mcp is not None:
            await self._tool_bridge.stop()
        self._extra_mcp_servers.clear()
        if self._client_handler:
            await self._client_handler.cleanup()
            self._client_handler = None
        if self._connection:
            try:
                await self._connection.close()
            except Exception:
                self.log.exception("Error closing ACP connection")
            self._connection = None
            self._api = None
        if self._transport_stack is not None:
            try:
                await self._transport_stack.aclose()
            except Exception:
                self.log.exception("Error closing remote ACP transport")
            self._transport_stack = None

        if self._process:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except TimeoutError:
                self._process.kill()
                await self._process.wait()
            except Exception:
                self.log.exception("Error terminating ACP process")
            self._process = None

    async def _stream_events(
        self,
        prompts: list[UserContent],
        *,
        user_msg: ChatMessage[Any],
        message_history: MessageHistory,
        effective_parent_id: str | None,
        message_id: str | None = None,
        session_id: str | None = None,
        parent_id: str | None = None,
        input_provider: InputProvider | None = None,
        deps: TDeps | None = None,
        wait_for_connections: bool | None = None,
        store_history: bool = True,
    ) -> AsyncIterator[RichAgentStreamEvent[str]]:
        from agentpool.agents.acp_agent.acp_converters import (
            convert_to_acp_content,
            to_finish_reason,
        )

        # Resolve input provider: explicit parameter overrides agent default
        effective_input_provider = input_provider or self._input_provider
        run_context = self.get_context(data=deps, input_provider=effective_input_provider)
        if self._client_handler:
            self._client_handler._input_provider = effective_input_provider
        if not self._api or not self._sdk_session_id or not self._state:
            raise AgentNotInitializedError

        run_id = str(uuid.uuid4())
        self._state.clear()
        reconstructor = MessageReconstructor(
            initial_prompts=prompts,
            model_name=self.model_name,
            provider_name=self._provider_type,
        )
        assert self.session_id is not None
        yield RunStartedEvent(session_id=self.session_id, run_id=run_id, agent_name=self.name)
        # Persist SDK session ID to storage for cross-referencing
        if self.storage and self.session_id and self._sdk_session_id:
            await self.storage.update_sdk_session_id(self.session_id, self._sdk_session_id)
        final_blocks = convert_to_acp_content(prompts)
        # Handle ephemeral execution (fork session if store_history=False)
        session_id = self._sdk_session_id
        if not store_history and self._sdk_session_id:
            cwd = self._cwd or str(Path.cwd())
            fork_response = await self._api.fork_session(self._sdk_session_id, cwd)
            session_id = fork_response.session_id
            self.log.debug("Forked session", parent=self._sdk_session_id, fork=session_id)
        self.log.debug("Starting streaming prompt", num_blocks=len(final_blocks))
        prompt_task = asyncio.create_task(self._api.prompt(session_id, final_blocks))
        self._prompt_task = prompt_task

        from agentpool.agents.acp_agent.stream_adapter import AcpAgentStreamedResponse

        assert self._client_handler
        streamed_response = AcpAgentStreamedResponse(
            provider_name="acp",
            state=self._state,
            update_event=self._client_handler._update_event,
            prompt_task=prompt_task,
            agent_name=self.name,
            session_id=self.session_id,
            tool_metadata=self._tool_bridge.tool_metadata,
        )

        try:
            async with (
                self._tool_bridge.set_run_context(run_context, prompt=prompts),
                merge_queue_into_iterator(streamed_response, self._event_queue) as merged_events,
            ):
                # On interrupt, keep draining: the remote agent flushes its last updates
                # and ends the turn with stopReason "cancelled" (see _interrupt).
                async for event in merged_events:
                    reconstructor.observe(event)
                    yield event
        except asyncio.CancelledError:
            self.log.info("Stream cancelled via task cancellation")
            self._cancelled = True
            prompt_task.cancel()

        if not prompt_task.done() or prompt_task.cancelled():
            reconstructor.flush()
            message = ChatMessage[str](
                content=reconstructor.text_content,
                role="assistant",
                name=self.name,
                message_id=message_id or str(uuid.uuid4()),
                session_id=self.session_id,
                parent_id=user_msg.message_id,
                model_name=self.model_name,
                messages=reconstructor.model_messages,
                finish_reason="stop",
            )
            yield StreamCompleteEvent(message=message)
            self._prompt_task = None
            return

        response = await prompt_task
        finish_reason = to_finish_reason(response.stop_reason)
        reconstructor.flush(finish_reason=finish_reason)

        text_content = reconstructor.text_content
        usage, cost_info = await calculate_usage_from_parts(
            input_parts=prompts,
            response_parts=reconstructor.all_response_parts,
            text_content=text_content,
            model_name=self.model_name,
            provider=self._provider_type,
        )

        message = ChatMessage[str](
            content=text_content,
            role="assistant",
            name=self.name,
            message_id=message_id or str(uuid.uuid4()),
            session_id=self.session_id,
            parent_id=user_msg.message_id,
            model_name=self.model_name,
            messages=reconstructor.model_messages,
            finish_reason=finish_reason,
            usage=usage,
            cost_info=cost_info,
        )
        yield StreamCompleteEvent(message=message)

    def _model_option(self) -> SelectSessionConfigOption | None:
        """Return the remote agent's model selector config option, if any."""
        from aclientp.schema import SelectSessionConfigOption

        if not self._state:
            return None
        return next(
            (
                opt
                for opt in self._state.config_options
                if isinstance(opt, SelectSessionConfigOption) and opt.category == "model"
            ),
            None,
        )

    @property
    def model_name(self) -> str | None:
        """Get the model name in a consistent format."""
        return option.current_value if (option := self._model_option()) else None

    def is_backend_command(self, name: str) -> bool:
        """The remote agent's commands run on it: ``/name args`` goes to it as the prompt."""
        commands = self._state.available_commands if self._state else None
        return commands is not None and any(c.name == name for c in commands.available_commands)

    async def set_model(self, model: str) -> None:
        """Update the model for the current session via ACP protocol."""
        option = self._model_option()
        await self._set_mode(model, option.id if option else "model")

    async def set_auto_approve(self, auto_approve: bool) -> None:
        """Set auto-approve mode for permission requests.

        Args:
            auto_approve: If True, automatically approve all permission requests.
                         If False, forward to callback/input_provider.
        """
        self.auto_approve = auto_approve
        self.log.info("Auto-approve mode changed", auto_approve=auto_approve)

    async def _interrupt(self) -> None:
        """Cancel the remote prompt turn, forcing it if the agent does not comply in time."""
        if self._api and self._sdk_session_id:
            try:
                await self._api.cancel(self._sdk_session_id)
                self.log.info("Sent cancel notification to ACP server")
            except Exception:
                self.log.exception("Failed to send cancel notification to ACP server")

        if (prompt_task := self._prompt_task) and not prompt_task.done():
            # Graceful: the agent should answer session/prompt with stopReason "cancelled"
            done, _ = await asyncio.wait({prompt_task}, timeout=CANCEL_GRACE_PERIOD)
            if done:
                return
            prompt_task.cancel()  # also sends $/cancel_request
            self.log.warning("ACP agent ignored session/cancel, cancelled prompt task")
        if self._current_stream_task and not self._current_stream_task.done():
            self._current_stream_task.cancel()

    async def get_available_models(self) -> list[ModelInfo] | None:
        """Get available models from the ACP session state."""
        from aclientp.schema import SessionConfigSelectOption
        from tokonomics.model_discovery.model_info import ModelInfo

        if not (option := self._model_option()):
            return None
        choices = [
            choice
            for item in option.options
            for choice in ([item] if isinstance(item, SessionConfigSelectOption) else item.options)
        ]
        return [ModelInfo(id=c.value, name=c.name, description=c.description) for c in choices]

    async def get_auth_status(self) -> AuthStatus:
        """Forward ``auth/status`` to the remote agent if it supports it."""
        from aclientp.schema import AuthStatusRequest

        from agentpool.agents.acp_agent.acp_converters import acp_to_native_auth_status
        from agentpool.agents.auth import AuthStatus

        auth_caps = self._caps.auth if self._caps else None
        if not (self._connection and auth_caps and auth_caps.status):
            return AuthStatus(state="unknown")
        try:
            response = await self._connection.auth_status(AuthStatusRequest())
        except Exception:
            self.log.exception("Failed to get auth status from ACP agent")
            return AuthStatus(state="unknown")
        return acp_to_native_auth_status(response)

    async def get_modes(self) -> list[ModeCategory]:
        """Get available modes from the ACP session state."""
        from agentpool.agents.acp_agent.acp_converters import to_native_modes

        if not self._state:
            return []

        return to_native_modes(self._state.config_options, available_modes=self._state.modes)

    async def _set_mode(self, mode_id: str | bool, category_id: str) -> None:
        """Forward mode change to remote ACP server."""
        if not self._api or not self._sdk_session_id or not self._state:
            raise RuntimeError("Not connected to ACP server")
        available_modes = await self.get_modes()
        if matching_category := next((c for c in available_modes if c.id == category_id), None):
            valid_ids = {str(m.value) for m in matching_category.available_modes}
            if mode_id not in valid_ids:
                raise UnknownModeError(mode_id, sorted(valid_ids))
        else:
            available_cats = {c.id for c in available_modes}
            raise UnknownCategoryError(category_id, sorted(available_cats))
        if self._state.config_options:
            assert category_id
            response = await self._api.set_session_config_option(
                self._sdk_session_id, category_id, mode_id
            )
            if response and response.config_options:
                self._state.config_options = list(response.config_options)
        elif category_id == "mode":
            assert isinstance(mode_id, str)
            await self._api.set_session_mode(self._sdk_session_id, mode_id)
            if self._state.modes:
                self._state.modes.current_mode_id = mode_id
        else:
            raise UnknownCategoryError(category_id, ["mode"])
        await self.update_state(config_id=category_id, value_id=mode_id)

    async def list_sessions(
        self,
        *,
        cwd: str | None = None,
        limit: int | None = None,
    ) -> list[SessionData]:
        """List sessions from the remote ACP server."""
        from agentpool.sessions.models import SessionData

        if not self._api:
            raise RuntimeError("Not connected to ACP server")
        try:
            response = await self._api.list_sessions(cwd)
        except Exception:
            self.log.exception("Failed to list sessions from ACP server")
            return []
        else:
            result: list[SessionData] = []
            for acp_session in response.sessions:
                updated_at = get_updated_at(acp_session.updated_at)
                meta = acp_session.field_meta or {}
                created_at = updated_at
                if meta_created := meta.get("created_at"):
                    created_at = get_updated_at(meta_created)
                session_data = SessionData(
                    session_id=acp_session.session_id,
                    agent_name=self.name,
                    cwd=acp_session.cwd,
                    created_at=created_at,
                    last_active=updated_at,
                    pool_id=meta.get("pool_id"),
                    project_id=meta.get("project_id"),
                    parent_id=meta.get("parent_id"),
                    version=meta.get("version", "1"),
                    metadata={"title": acp_session.title} if acp_session.title else {},
                )
                result.append(session_data)
            self._sessions_cache = result
            if limit is not None:
                result = result[:limit]
            return result

    async def start_new_session(self) -> None:
        """Start a fresh session on the remote ACP server."""
        await super().start_new_session()
        if self._api is None:
            self._sdk_session_id = None
        else:
            await self._create_session()

    async def load_session(self, session_id: str) -> SessionData | None:
        """Load and restore a session from the remote ACP server."""
        from agentpool.agents.acp_agent.acp_converters import (
            acp_notifications_to_messages,
            mcp_config_to_acp,
        )
        from agentpool.agents.acp_agent.helpers import filter_servers_by_capabilities
        from agentpool.sessions.models import SessionData
        from agentpool.utils.time_utils import get_now

        if not self._api:
            self.log.error("Cannot load session: not connected to ACP server")
            return None

        if not self._state:
            self.log.error("Cannot load session: state not initialized")
            return None
        all_servers = self._extra_mcp_servers[:]
        if self.mcp.servers:
            all_servers.extend([mcp_config_to_acp(config) for config in self.mcp.servers])
        mcp_servers = filter_servers_by_capabilities(all_servers, self._caps, logger=self.log)
        cwd = self._cwd or str(Path.cwd())
        try:
            self._state.start_load()
            response = await self._api.load_session(session_id, cwd, mcp_servers or None)
            raw_updates = self._state.finish_load()

            self._sdk_session_id = session_id
            self._state.session_id = session_id

            if response.config_options:
                self._state.config_options = list(response.config_options)
            if response.modes:
                self._state.modes = response.modes

            if raw_updates:
                messages = acp_notifications_to_messages(
                    raw_updates,
                    session_id=session_id,
                    agent_name=self.name,
                    model_name=self.model_name,
                )
                self.conversation.chat_messages.clear()
                self.conversation.chat_messages.extend(messages)
                self.log.info("Restored session", session_id=session_id, msg_count=len(messages))
            else:
                self.log.debug("No conversation history to restore", session_id=session_id)

            self.log.info("Session loaded from ACP server", session_id=session_id)

            def find_in_cache(sid: str) -> SessionData | None:
                if self._sessions_cache is None:
                    return None
                return next((s for s in self._sessions_cache if s.session_id == sid), None)

            if session_info := find_in_cache(session_id):
                return session_info

            try:
                await self.list_sessions()
                if session_info := find_in_cache(session_id):
                    return session_info
            except Exception:  # noqa: BLE001
                self.log.debug("Could not fetch session metadata", session_id=session_id)
            now = get_now()
            return SessionData(
                session_id=session_id,
                agent_name=self.name,
                cwd=cwd,
                last_active=now,
                created_at=now,
            )

        except Exception:
            if self._state:
                self._state.finish_load()
            self.log.exception("Failed to load session from ACP server")
            return None


if __name__ == "__main__":

    async def main() -> None:
        async with ACPAgent(command="claude-agent-acp") as agent:
            async for event in agent.run_stream("hello"):
                print(event)

    asyncio.run(main())
