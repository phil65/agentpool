"""Codex agent - wraps Codex app-server via JSON-RPC protocol."""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
import time
from typing import TYPE_CHECKING, Any, ClassVar, Literal, Self, assert_never, cast
from uuid import uuid4

import anyenv
from codexed.models import (
    CommandExecutionRequestApprovalResponse,
    McpServerElicitationResponse,
    Personality,
    ReasoningEffort,
    SandboxMode,
)
from pydantic import TypeAdapter
from pydantic_ai import RunUsage

from agentpool.agents.base_agent import BaseAgent
from agentpool.agents.capabilities import AgentCapabilities
from agentpool.agents.codex_agent.codex_converters import (
    codex_thread_options,
    mcp_config_to_codex,
    options_taken_by,
    to_auth_status,
    to_finish_reason,
    to_model_info,
    to_run_usage,
    to_session_data,
    turns_to_chat_messages,
    user_content_to_codex,
)
from agentpool.agents.codex_agent.stream_adapter import CodexStreamedResponse
from agentpool.agents.events import RunStartedEvent, StreamCompleteEvent
from agentpool.agents.events.reconstructor import MessageReconstructor
from agentpool.agents.exceptions import AgentNotInitializedError, UnsupportedOperationError
from agentpool.agents.runtime import activate
from agentpool.log import get_logger
from agentpool.mcp_server.tool_bridge import DEFAULT_ROUTE
from agentpool.messaging import ChatMessage
from agentpool.sessions.environment import current_instructions, instructions_update
from agentpool.tools.decisions import Approve, Deny


if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping, Sequence
    from types import TracebackType

    from codexed import CodexClient, Session
    from codexed.models import (
        HttpMcpServer,
        McpServerConfig,
        McpServerElicitationRequestParams,
        ToolRequestUserInputParams,
        ToolRequestUserInputResponse,
    )
    from codexed.request_handlers import ApprovalParams, ApprovalResponse
    from exxec import ExecutionEnvironment
    from pydantic_ai import UserContent
    from tokonomics.model_discovery.model_info import ModelInfo

    from agentpool.agents.auth import AuthStatus
    from agentpool.agents.context import AgentContext
    from agentpool.agents.events import RichAgentStreamEvent
    from agentpool.agents.modes import ConfigOption
    from agentpool.agents.usage_limits import UsageLimits
    from agentpool.common_types import AnyEventHandlerType, MCPServerStatus, StrPath
    from agentpool.delegation import AgentPool
    from agentpool.hooks import AgentHooks
    from agentpool.messaging import MessageHistory
    from agentpool.messaging.compaction import CompactionPipeline
    from agentpool.models.codex_agents import ApprovalPolicy, CodexAgentConfig
    from agentpool.resource_providers import ResourceProvider
    from agentpool.sessions import Session as PoolSession
    from agentpool.sessions.models import SessionData
    from agentpool.ui.base import InputProvider
    from agentpool_config.events import EventConfig
    from agentpool_config.mcp_server import MCPServerConfig


logger = get_logger(__name__)

USAGE_LIMITS_TTL = 300.0
"""Seconds a rate limits read is reused (notifications update it meanwhile)."""


class CodexAgent[TDeps = None, OutputDataT = str](BaseAgent[TDeps, OutputDataT]):
    """Agent running a Codex app-server (through codexed's CodexClient).

    Turns run on a Codex thread per session, all over one app-server; agentpool
    toolsets are exposed to Codex through an MCP bridge, which each thread reaches on its
    session's route (Codex connects MCP servers per thread). Settings set within a
    session (``use_session``) are that session's.
    """

    AGENT_TYPE: ClassVar = "codex"
    CAPABILITIES: ClassVar[AgentCapabilities] = AgentCapabilities(
        native_steer=True,
        structured_output=True,
        multimodal_input=True,
        live_model_discovery=True,
        native_compaction=True,
        native_fork=True,
        concurrent_sessions=True,
        session_instructions=True,  # The thread's developer instructions, changes per turn
    )

    def __init__(
        self,
        *,
        deps_type: type[TDeps] | None = None,
        name: str | None = None,
        description: str | None = None,
        display_name: str | None = None,
        model: str | None = None,
        reasoning_effort: ReasoningEffort | None = None,
        base_instructions: str | None = None,
        developer_instructions: str | None = None,
        agent_pool: AgentPool[Any] | None = None,
        enable_logging: bool = True,
        mcp_servers: Sequence[str | MCPServerConfig] | None = None,
        env: ExecutionEnvironment | StrPath | None = None,
        input_provider: InputProvider | None = None,
        env_vars: dict[str, str] | None = None,
        output_type: type[OutputDataT] = str,  # type: ignore[assignment]  # ty:ignore[invalid-parameter-default]
        event_handlers: Sequence[AnyEventHandlerType] | None = None,
        hooks: AgentHooks | None = None,
        session_id: str | None = None,
        toolsets: list[ResourceProvider] | None = None,
        approval_policy: ApprovalPolicy | None = None,
        sandbox: SandboxMode | None = None,
        personality: Personality | None = None,
        thread_options: Mapping[str, Any] | None = None,
        event_configs: Sequence[EventConfig] | None = None,
    ) -> None:
        """Initialize Codex agent.

        Args:
            name: Agent name
            deps_type: Type of dependencies for the agent
            description: Agent description
            display_name: Human-readable display name
            model: Model to use, as the app-server lists it (e.g., "gpt-6.2-sol")
            reasoning_effort: Reasoning effort level ("low", "medium", "high", "xhigh")
            base_instructions: Base system instructions for the session
            developer_instructions: Developer-provided instructions
            agent_pool: Agent pool for coordination
            enable_logging: Whether to enable database logging
            mcp_servers: MCP server configurations
            env: Execution environment
            input_provider: Provider for user input
            env_vars: Environment variables for the agent
            output_type: Output type for structured responses (default: str)
            event_handlers: Event handlers for this agent
            hooks: Agent hooks for pre/post tool execution
            session_id: Session/thread ID to resume on connect (avoids reconnect overhead)
            toolsets: Resource providers for tools to expose via MCP bridge
            approval_policy: Approval policy ("never", "on-request", "untrusted")
            sandbox: Sandbox mode ("read-only", "workspace-write", "danger-full-access")
            personality: Personality preset (none, friendly, pragmatic)
            thread_options: ``thread/start`` parameters passed through (those agentpool
                doesn't set itself; also on resume and fork, where Codex takes them)
            event_configs: Event trigger configurations
        """
        from agentpool.mcp_server.tool_bridge import ToolManagerBridge
        from agentpool_config.mcp_server import BaseMCPServerConfig

        super().__init__(
            name=name or "codex",
            deps_type=deps_type,
            description=description,
            display_name=display_name,
            agent_pool=agent_pool,
            enable_logging=enable_logging,
            env=env,
            input_provider=input_provider,
            output_type=output_type,
            event_handlers=event_handlers,
            hooks=hooks,
            event_configs=event_configs,
        )

        # Codex settings
        self._base_instructions = base_instructions
        self._developer_instructions = developer_instructions
        self._own_approval_policy: ApprovalPolicy = approval_policy or "never"
        self._toolsets = toolsets or []
        self._env_vars = env_vars or {}
        # Client state
        self._client: CodexClient | None = None
        self._rate_limits_read_at = float("-inf")
        """Monotonic time of the last account/rateLimits/read."""
        self._sdk_session_id = session_id
        self._sessions: dict[str, Session] = {}
        """Threads open in this client, by thread ID."""
        self._thread_routes: dict[str, str] = {}
        """Bridge route of each open thread (its session's; see ``ToolManagerBridge``)."""
        self._adapters: dict[str, CodexStreamedResponse] = {}
        """Stream of the turn running on each thread."""
        self._thread_instructions: dict[str, str] = {}
        """The session's instructions as each thread has them (its developer instructions,
        then updates; a thread resumed here counts as having none)."""
        self._external_mcp_servers = [
            BaseMCPServerConfig.from_string(s) if isinstance(s, str) else s
            for s in mcp_servers or []
        ]
        # Extra MCP servers in Codex format (e.g., tool bridge)
        self._extra_mcp_servers: list[tuple[str, McpServerConfig]] = []
        # Settings of the agent; a session's own where set within it (see the properties)
        self._own_model: str | None = model
        self._own_effort: ReasoningEffort | None = reasoning_effort
        self._own_sandbox: SandboxMode | None = sandbox
        self._own_personality: Personality | None = personality
        self._thread_options = codex_thread_options(thread_options or {})
        self._tool_bridge = ToolManagerBridge(node=self)

    # --- Settings: per session when set within one (``use_session``), else the agent's ---

    @property
    def _approval_policy(self) -> ApprovalPolicy:
        value = self._session_mode("mode")
        return cast("ApprovalPolicy", value) if value is not None else self._own_approval_policy

    @_approval_policy.setter
    def _approval_policy(self, value: ApprovalPolicy) -> None:
        if not self._set_session_mode("mode", value):
            self._own_approval_policy = value

    @property
    def _current_effort(self) -> ReasoningEffort | None:
        value = self._session_mode("thought_level")
        return value if value is not None else self._own_effort

    @_current_effort.setter
    def _current_effort(self, value: ReasoningEffort) -> None:
        if not self._set_session_mode("thought_level", value):
            self._own_effort = value

    @property
    def _current_sandbox(self) -> SandboxMode | None:
        value = self._session_mode("sandbox")
        return cast("SandboxMode", value) if value is not None else self._own_sandbox

    @_current_sandbox.setter
    def _current_sandbox(self, value: SandboxMode) -> None:
        if not self._set_session_mode("sandbox", value):
            self._own_sandbox = value

    @property
    def _current_personality(self) -> Personality | None:
        value = self._session_mode("personality")
        return cast("Personality", value) if value is not None else self._own_personality

    @_current_personality.setter
    def _current_personality(self, value: Personality) -> None:
        if not self._set_session_mode("personality", value):
            self._own_personality = value

    @property
    def _current_model(self) -> str | None:
        return self._session_model() or self._own_model

    @_current_model.setter
    def _current_model(self, value: str) -> None:
        if not self._set_session_model(value):
            self._own_model = value

    # --- Threads -----------------------------------------------------------------------

    @property
    def _route(self) -> str:
        """Bridge route of the session acting (its ID; the default session's plain URL)."""
        session = self._explicit_session
        return session.session_id if session is not None else DEFAULT_ROUTE

    def _thread_mcp_servers(self) -> dict[str, McpServerConfig]:
        """MCP servers of a thread of the session acting: ours, the bridge on its route.

        All of them: a thread's ``mcp_servers`` replaces the ones given to the client.
        """
        servers = dict(self._extra_mcp_servers) | dict(
            mcp_config_to_codex(c) for c in self._external_mcp_servers
        )
        if self._tool_bridge._actual_port is not None:
            servers[self._tool_bridge.resolved_server_name] = self._bridge_server(self._route)
        return servers

    def _bridge_server(self, route: str) -> HttpMcpServer:
        """The bridge as Codex MCP server, on ``route``.

        Its tools run without Codex asking: they are agentpool's, configured for this
        agent. Codex would ask for every tool not annotated read-only, and under approval
        policy ``never`` reject them instead.
        """
        from codexed.models import HttpMcpServer as CodexHttpMcpServer

        url = self._tool_bridge.url_for(route)
        return CodexHttpMcpServer(url=url, default_tools_approval_mode="approve")

    def _open_thread(self, session: Session) -> None:
        """Make ``session`` (a started or resumed thread) the thread of the session acting."""
        self._sdk_session_id = session.thread_id
        self._sessions[session.thread_id] = session
        self._thread_routes[session.thread_id] = self._route

    async def _resume_thread(self, thread_id: str) -> Session:
        """Resume ``thread_id`` for the session acting (its MCP servers on its route)."""
        if self._client is None:
            raise AgentNotInitializedError
        session = await self._client.thread_resume(
            thread_id,
            mcp_servers=self._thread_mcp_servers(),
            **options_taken_by(self._client.thread_resume, self._thread_options),
        )
        self._open_thread(session)
        return session

    def _run_context(self, thread_id: str | None) -> AgentContext[Any] | None:
        """Context of the turn running on ``thread_id``.

        Without a thread (skill approvals don't name one), the only turn running, if
        just one is.
        """
        if thread_id is not None:
            route = self._thread_routes.get(thread_id, DEFAULT_ROUTE)
            return self._tool_bridge.context(route)
        running = [
            ctx
            for route in set(self._thread_routes.values())
            if (ctx := self._tool_bridge.context(route)) is not None
        ]
        return running[0] if len(running) == 1 else None

    @classmethod
    def from_config(
        cls,
        config: CodexAgentConfig,
        *,
        event_handlers: Sequence[AnyEventHandlerType] | None = None,
        input_provider: InputProvider | None = None,
        agent_pool: AgentPool[Any] | None = None,
        deps_type: type[TDeps] | None = None,
    ) -> Self:
        """Create agent from configuration.

        All config values are extracted here and passed to the constructor.
        """
        from agentpool.utils.result_utils import to_type

        # Resolve output type from config
        responses = agent_pool.manifest.responses if agent_pool is not None else None
        resolved_output_type = to_type(config.output_type or str, responses)
        # Merge config-level handlers with provided handlers
        config_handlers = config.get_event_handlers()
        merged_handlers: list[AnyEventHandlerType] = [*config_handlers, *(event_handlers or [])]
        # Extract toolsets from config
        return cls(
            # Identity
            name=config.name,
            deps_type=deps_type,
            description=config.description,
            display_name=config.display_name,
            # Codex settings
            model=config.model,
            env=config.get_execution_environment(),
            reasoning_effort=config.reasoning_effort,
            base_instructions=config.base_instructions,
            developer_instructions=config.developer_instructions,
            approval_policy=config.approval_policy,
            sandbox=config.sandbox,
            personality=config.personality,
            thread_options=config.thread_options,
            # MCP and toolsets
            mcp_servers=config.get_mcp_servers(),
            toolsets=config.get_tool_providers(),
            # Runtime
            event_handlers=merged_handlers or None,
            input_provider=input_provider,
            agent_pool=agent_pool,
            output_type=resolved_output_type,  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
            hooks=config.hooks.get_agent_hooks(),
            event_configs=list(config.triggers),
        )

    async def _setup_toolsets(self) -> None:
        """Setup toolsets and start the tool bridge."""
        if not self._toolsets:
            return
        # Add toolset providers to tool manager
        for provider in self._toolsets:
            self.tools.add_provider(provider)
        # Start bridge to expose tools via MCP
        await self._tool_bridge.start()
        # Add bridge's MCP server config to extra servers
        if self._tool_bridge._actual_port is None:
            raise RuntimeError("Bridge not started - call start() first")
        bridge_config = (self._tool_bridge.resolved_server_name, self._bridge_server(DEFAULT_ROUTE))
        self._extra_mcp_servers.append(bridge_config)

    async def __aenter__(self) -> Self:
        """Start Codex client and create or resume thread."""
        from codexed import CodexClient

        await super().__aenter__()
        await self._setup_toolsets()
        # Collect MCP servers: extra (bridge) + configured servers
        # Build dict mapping server name -> McpServerConfig (Codex type)
        mcp_servers_dict = dict(self._extra_mcp_servers) | dict(
            mcp_config_to_codex(c) for c in self._external_mcp_servers
        )
        # Create and connect client with MCP servers and elicitation callback
        self._client = CodexClient(
            mcp_servers=mcp_servers_dict,
            on_user_input=self._on_user_input,
            on_mcp_elicitation=self._on_mcp_elicitation,
            on_approval=self._on_approval,
        )
        await self._client.__aenter__()
        cwd = str(self.env.cwd or Path.cwd())
        # Resume existing session or start new thread
        if self._sdk_session_id:
            # Resume the specified thread
            session = await self._resume_thread(self._sdk_session_id)
            thread = session.response.thread
            self.log.info("Codex thread resumed", sdk_session_id=thread.id, cwd=cwd)
            # Restore conversation history from resumed thread
            chat_messages = turns_to_chat_messages(thread.turns)
            self.conversation.chat_messages.clear()
            self.conversation.chat_messages.extend(chat_messages)
            self.log.info("Restored conversation history", turn_count=len(thread.turns))
        else:
            await self._start_thread()
        return self

    async def _start_thread(self) -> None:
        """Start a new Codex thread for the session acting."""
        if self._client is None:
            raise AgentNotInitializedError
        cwd = str(self.env.cwd or Path.cwd())
        # The session's instructions (working directory, the client's) with the agent's own;
        # later changes go into the prompt of the next turn (see _stream_events)
        instructions = current_instructions()
        developer = "\n\n".join(p for p in (self._developer_instructions, instructions) if p)
        session = await self._client.thread_start(
            cwd=cwd,
            model=self._current_model,
            base_instructions=self._base_instructions,
            developer_instructions=developer or None,
            sandbox=self._current_sandbox,
            approval_policy=self._approval_policy,
            personality=self._current_personality,
            mcp_servers=self._thread_mcp_servers(),
            **self._thread_options,
        )
        self._open_thread(session)
        self._thread_instructions[session.thread_id] = instructions
        self.log.info("Codex thread started", sdk_session_id=session.thread_id, cwd=cwd)

    async def start_new_session(self) -> None:
        """Start a fresh Codex thread instead of continuing the current one."""
        await super().start_new_session()
        if self._client is None:
            self._sdk_session_id = None
        else:
            await self._start_thread()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Clean up Codex client."""
        await self._cleanup()
        await super().__aexit__(exc_type, exc_val, exc_tb)

    async def _on_approval(self, data: ApprovalParams) -> ApprovalResponse:
        """Ask the user to approve a command or file change of Codex."""
        from codexed.models import (
            CommandExecutionRequestApprovalParams,
            FileChangeRequestApprovalParams,
            SkillRequestApprovalParams,
        )
        from codexed.models.misc import SkillRequestApprovalResponse
        from codexed.models.responses import FileChangeRequestApprovalResponse

        self.log.debug("Permission request")
        thread_id = None if isinstance(data, SkillRequestApprovalParams) else data.thread_id
        ctx = self._run_context(thread_id)
        if ctx is None:
            raise RuntimeError("Permission callback invoked outside of an active run")
        # What the user decides on, named so a decision for the session covers its kind
        tool_name: str
        tool_input: dict[str, Any]
        match data:
            case CommandExecutionRequestApprovalParams(command=command, cwd=cwd, reason=reason):
                tool_name = "command_execution"
                tool_input = {"command": command, "cwd": cwd, "reason": reason}
            case FileChangeRequestApprovalParams(grant_root=root, reason=reason):
                tool_name = "file_change"
                tool_input = {"grant_root": root, "reason": reason}
            case SkillRequestApprovalParams(skill_name=skill):
                tool_name = f"skill:{skill}"
                tool_input = {"skill": skill}
            case _ as unreachable:
                assert_never(unreachable)
        call_ctx = replace(
            ctx, tool_name=tool_name, tool_call_id=data.item_id, tool_input=tool_input
        )
        approval_decision: Literal["allow", "deny"]
        match await call_ctx.decide_tool():
            case Approve():
                approval_decision = "allow"
            case Deny(stop=stop):
                approval_decision = "deny"
                if stop:
                    call_ctx.stop_turn()
        match data:
            case CommandExecutionRequestApprovalParams():
                return CommandExecutionRequestApprovalResponse(decision=approval_decision)
            case SkillRequestApprovalParams():
                return SkillRequestApprovalResponse(decision=approval_decision)
            case FileChangeRequestApprovalParams():
                return FileChangeRequestApprovalResponse(decision=approval_decision)
            case _ as unreachable:
                assert_never(unreachable)

    async def _on_mcp_elicitation(
        self, data: McpServerElicitationRequestParams
    ) -> McpServerElicitationResponse:
        """Ask the user for what an MCP server of Codex requests."""
        from mcp.types import ErrorData

        ctx = self._run_context(data.thread_id)
        if ctx is None:
            raise RuntimeError("MCP elicitation callback invoked outside of an active run")
        provider = ctx.get_input_provider()
        mcp_request = data.to_mcp()
        with self.awaiting_user():
            result = await provider.get_elicitation(mcp_request)
        if isinstance(result, ErrorData):
            return McpServerElicitationResponse(action="cancel")
        return McpServerElicitationResponse(action=result.action, content=result.content)

    async def _on_user_input(
        self,
        params: ToolRequestUserInputParams,
    ) -> ToolRequestUserInputResponse:
        """Handle user input requests from Codex server.

        Converts Codex's ToolRequestUserInputParams to MCP ElicitRequestFormParams,
        delegates to the input provider's get_elicitation(), and converts back.

        Args:
            params: User input request with questions

        Returns:
            ToolRequestUserInputResponse with answers
        """
        from codexed.models import (
            ToolRequestUserInputAnswer as _Answer,
            ToolRequestUserInputResponse as _Response,
        )
        from mcp.types import ElicitRequestFormParams, ElicitResult, ErrorData

        if (run_context := self._run_context(params.thread_id)) is None:
            raise RuntimeError("User input callback invoked outside of an active run")

        input_provider = run_context.get_input_provider()
        answers: dict[str, _Answer] = {}
        for question in params.questions:
            # Build a JSON schema property for this question
            props = {question.id: question.to_schema_property()}
            schema = {"type": "object", "properties": props, "required": [question.id]}
            # Build display message from header + question
            message = (
                f"{question.header}: {question.question}" if question.header else question.question
            )
            mcp_params = ElicitRequestFormParams(message=message, requested_schema=schema)
            with self.awaiting_user():
                result = await input_provider.get_elicitation(
                    mcp_params, tool_call_id=params.item_id
                )

            match result:
                case ErrorData():
                    answers[question.id] = _Answer(answers=[])
                    continue
                case ElicitResult(action="accept", content=content) if content:
                    raw_value = content.get(question.id)
                    match raw_value:
                        case list():
                            answers[question.id] = _Answer(answers=raw_value)
                        case None:
                            answers[question.id] = _Answer(answers=[])
                        case str() | int() | float() | bool():
                            answers[question.id] = _Answer(answers=[str(raw_value)])
                        case _ as unknown_type:
                            assert_never(unknown_type)
                case ElicitResult():
                    # User declined or cancelled
                    answers[question.id] = _Answer(answers=[])
                case _ as unreachable:
                    assert_never(unreachable)

        return _Response(answers=answers)

    async def reconnect_mcp_server(self, name: str) -> None:
        """Reload Codex's MCP servers: Codex reloads them all, not one alone.

        Raises:
            KeyError: If Codex has no MCP server ``name``
            UnsupportedOperationError: If Codex isn't connected
        """
        if self._client is None:
            raise UnsupportedOperationError(
                self.name, "reload MCP servers", "Codex isn't connected"
            )
        if name not in (servers := await self.get_mcp_server_info()):
            msg = f"No MCP server {name!r} (known: {', '.join(sorted(servers)) or 'none'})"
            raise KeyError(msg)
        await self._client.mcp_server_refresh()

    async def set_mcp_server_enabled(self, name: str, enabled: bool) -> None:
        """Not supported: Codex's app-server can't switch one MCP server on or off."""
        operation = f"{'enable' if enabled else 'disable'} MCP server {name!r}"
        reason = "Codex's app-server only reloads all MCP servers"
        raise UnsupportedOperationError(self.name, operation, reason)

    async def get_mcp_server_info(self) -> dict[str, MCPServerStatus]:
        """Get MCP server status from connected Codex client.

        Queries live status including tools, resources, and auth when
        the client is connected. Falls back to config-based reporting.
        """
        from agentpool.common_types import MCPServerStatus

        result: dict[str, MCPServerStatus] = {}
        if self._client:
            try:
                response = await self._client.mcp_server_status_list()
            except Exception:  # noqa: BLE001
                pass
            else:
                for server in response.data:
                    result[server.name] = MCPServerStatus(
                        name=server.name,
                        status="connected" if server.tools else "disconnected",
                        server_name=server.name,
                    )
                return result
        # Fallback: report from config
        for name, _cfg in self._extra_mcp_servers:
            result[name] = MCPServerStatus(name=name, status="connected")
        return result

    async def _cleanup(self) -> None:
        """Clean up resources."""
        # Stop tool bridge if it was started
        if self._tool_bridge._mcp is not None:
            await self._tool_bridge.stop()
        self._extra_mcp_servers.clear()
        if self._client:
            try:
                await self._client.__aexit__(None, None, None)
            except Exception:
                self.log.exception("Error closing Codex client")
            self._client = None
        self._sdk_session_id = None
        self._sessions.clear()
        self._thread_routes.clear()
        self._adapters.clear()
        self._thread_instructions.clear()

    async def _stream_events(  # noqa: PLR0915
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
        store_history: bool = True,
    ) -> AsyncIterator[RichAgentStreamEvent[OutputDataT]]:
        """Stream events from Codex turn execution."""
        from agentpool.agents.events import PlanUpdateEvent
        from agentpool.messaging.messages import TokenCost

        if not self._client:
            raise AgentNotInitializedError
        # The session's thread: new, or resumed in this client (e.g. after a restart)
        if self._sdk_session_id is None:
            await self._start_thread()
        elif self._sdk_session_id not in self._sessions:
            await self._resume_thread(self._sdk_session_id)
        thread_id = self._sdk_session_id
        assert thread_id is not None
        current = current_instructions()
        started = self._thread_instructions.get(thread_id, "")
        if (update := instructions_update(started, current)) is not None:
            prompts = [update, *prompts]  # Changed since the thread started
            self._thread_instructions[thread_id] = current

        input_items = list(user_content_to_codex(prompts))
        # Generate IDs if not provided
        run_id = str(uuid4())
        final_message_id = message_id or str(uuid4())
        final_session_id = session_id or self.session_id
        # Ensure session_id is set (should always be from base class)
        if final_session_id is None:
            raise ValueError("session_id must be set")
        yield RunStartedEvent(session_id=final_session_id, run_id=run_id)
        # Persist SDK session ID to storage for cross-referencing
        if self.storage:
            await self.storage.update_sdk_session_id(final_session_id, thread_id)
        # Stream turn events with bridge context set
        reconstructor = MessageReconstructor(initial_prompts=prompts, model_name=self.model_name)
        # Pass output type directly - adapter handles conversion to JSON schema
        # Resolve input provider: explicit parameter overrides agent default
        effective_input_provider = input_provider or self._input_provider
        run_context = self.get_context(data=deps, input_provider=effective_input_provider)
        session = self._sessions[thread_id]
        raw_stream = session.turn_stream(
            input_items,
            model=self._current_model,
            effort=self._current_effort,
            approval_policy=self._approval_policy,
            sandbox_policy=self._current_sandbox,
            output_schema=None if self._output_type in (str, None) else self._output_type,
            personality=self._current_personality,
        )
        adapter = CodexStreamedResponse(provider_name="codex", stream=raw_stream)
        self._adapters[thread_id] = adapter
        bridge_context = self._tool_bridge.set_run_context(
            run_context, prompt=prompts, route=self._route
        )
        try:
            async with bridge_context:
                # Wrap to capture metadata (turn_id, token usage), then convert
                async for native_event in adapter:
                    reconstructor.observe(native_event)
                    yield native_event
                    if isinstance(native_event, PlanUpdateEvent) and self.agent_pool:
                        self.agent_pool.todos.replace_all(native_event.entries)

        except Exception as e:
            self.log.exception("Error during Codex turn", error=str(e))
            raise
        finally:
            self._adapters.pop(thread_id, None)  # The thread's turn is over

        # Emit completion event
        reconstructor.flush()
        final_text = reconstructor.text_content
        cost_info: TokenCost | None = None
        run_usage = RunUsage()

        if usage := adapter._token_usage_data:
            run_usage = to_run_usage(usage)
            # TODO: Calculate actual cost - for now set to 0
            cost_info = TokenCost(total_cost=Decimal(0))
        # Parse structured output if output_type is not str
        final_content: OutputDataT
        if self._output_type not in (str, None):
            try:
                parsed = anyenv.load_json(final_text)
                final_content = TypeAdapter(self._output_type).validate_python(parsed)
            except (anyenv.JsonLoadError, ValueError) as e:
                msg = "Failed to parse structured output, returning raw text"
                self.log.warning(msg, error=str(e), output_type=self._output_type)
                final_content = final_text  # type: ignore[assignment]  # ty:ignore[invalid-assignment]
        else:
            final_content = final_text  # type: ignore[assignment]  # ty:ignore[invalid-assignment]

        complete_msg: ChatMessage[OutputDataT] = ChatMessage(
            content=final_content,
            role="assistant",
            message_id=final_message_id,
            session_id=final_session_id,
            parent_id=user_msg.message_id,  # The turn's prompt (like every agent's answer)
            cost_info=cost_info,
            usage=run_usage,
            model_name=self.model_name,
            messages=reconstructor.model_messages,
            finish_reason=to_finish_reason(s) if (s := adapter._turn_status) else None,
        )

        yield StreamCompleteEvent[OutputDataT](message=complete_msg)

    @property
    def model_name(self) -> str:
        """Get current model name."""
        return self._current_model or "unknown"

    def to_structured[NewOutputDataT](
        self,
        output_type: type[NewOutputDataT],
    ) -> CodexAgent[TDeps, NewOutputDataT]:
        """Configure agent for structured output.

        Codex supports structured output via output_schema parameter in turn_stream.
        This method sets the output type which will be converted to JSON schema
        and passed to Codex on each turn.

        Args:
            output_type: Pydantic model type for structured responses

        Returns:
            Self (mutates in place)
        """
        from agentpool.utils.result_utils import to_type

        self.log.debug("Setting result type", output_type=output_type)
        self._output_type = to_type(output_type)  # type: ignore[assignment]  # ty:ignore[invalid-assignment]
        return self  # type: ignore[return-value]  # ty:ignore[invalid-explicit-return]

    async def set_model(self, model: str) -> None:
        """Set the model for this agent."""
        await self._set_mode(model, "model")

    async def set_approval_policy(self, policy: ApprovalPolicy) -> None:
        """Set the approval policy for tool execution.

        Args:
            policy: Approval policy - "never", "on-request", or "untrusted"
        """
        await self._set_mode(policy, "mode")

    async def steer(
        self, message: str, *, interrupt: bool = False, session_id: str | None = None
    ) -> None:
        """Steer an active Codex turn natively via ``turn/steer``.

        When a Codex turn is in progress, delivers ``message`` directly into the active
        turn with ``expected_turn_id``. With ``interrupt=True``, interrupts the current
        generation and queues ``message`` for immediate execution. Outside an active
        turn, falls back to the base injection behavior.
        """
        runtimes = self._runtimes_of(session_id)
        with activate(self, runtimes[0]) if runtimes else nullcontext():  # That session's
            thread_id = self._sdk_session_id
        if (
            self._client
            and thread_id
            and (adapter := self._adapters.get(thread_id))
            and (turn_id := adapter._current_turn_id)
        ):
            session = self._sessions[thread_id]
            if interrupt:
                self.queue_prompt(message, session_id=session_id)
                await session.turn_interrupt(turn_id)
            else:
                await session.turn_steer(message, expected_turn_id=turn_id)
            return
        await super().steer(message, interrupt=interrupt, session_id=session_id)

    async def compact(
        self,
        pipeline: CompactionPipeline | None = None,
        *,
        session: PoolSession | None = None,
    ) -> tuple[int, int]:
        """Compact the Codex thread of ``session`` (or the default one) natively.

        Raises:
            UnsupportedOperationError: Without a thread open in this client.
        """
        from agentpool.agents.exceptions import UnsupportedOperationError

        with self.use_session(session):
            thread_id = self._sdk_session_id
            history = self._history
        if (
            self._client is not None
            and thread_id is not None
            and (sdk_session := self._sessions.get(thread_id)) is not None
        ):
            before = len(history.get_history())
            await sdk_session.compact_start()
            return (before, before)
        reason = "only a session whose thread is open, once one is running"
        raise UnsupportedOperationError(self.name, "compact", reason)

    async def fork_session(
        self,
        *,
        at_message_id: str | None = None,
        session: PoolSession | None = None,
    ) -> PoolSession:
        """Fork ``session`` (or the default one) together with its Codex thread.

        The forked thread reaches the bridge on the fork's route.

        Raises:
            UnsupportedOperationError: At a message (Codex forks whole threads), or
                without a thread open in this client.
        """
        from agentpool.agents.exceptions import UnsupportedOperationError

        with self.use_session(session):
            thread_id = self._sdk_session_id
        if (
            at_message_id is not None
            or self._client is None
            or thread_id is None
            or (sdk_session := self._sessions.get(thread_id)) is None
        ):
            reason = "only the whole thread of a session, once one is open"
            raise UnsupportedOperationError(self.name, "fork this", reason)
        forked = await self._fork_history(None, session)
        with self.use_session(forked):
            forked_sdk = await sdk_session.fork(
                mcp_servers=self._thread_mcp_servers(),
                **options_taken_by(sdk_session.fork, self._thread_options),
            )
            self._open_thread(forked_sdk)  # Stored on the fork (its sdk_session_id)
        return forked

    async def _interrupt(self) -> None:
        """Call Codex turn_interrupt if there's an active turn."""
        thread_id = self._sdk_session_id  # Of the session being interrupted
        if (
            self._client
            and thread_id
            and (adapter := self._adapters.get(thread_id))
            and (turn_id := adapter._current_turn_id)
        ):
            try:
                await self._sessions[thread_id].turn_interrupt(turn_id)
                self.log.info("Codex turn interrupted", sdk_session_id=thread_id, turn_id=turn_id)
            except Exception:
                self.log.exception("Failed to interrupt Codex turn")

    async def get_available_models(self) -> list[ModelInfo] | None:
        """Get available models from Codex server.

        Returns:
            List of tokonomics ModelInfo for available models, or None if not connected
            or the request failed
        """
        if not self._client:
            self.log.warning("Cannot get models: client not connected")
            return None

        try:
            response = await self._client.model_list()
            models = [to_model_info(i) for i in response.data]
        except Exception:
            self.log.exception("Failed to fetch models from Codex")
            return None
        else:
            return models

    async def get_usage_limits(self) -> UsageLimits | None:
        """Get the ChatGPT plan's rate-limit windows (None when signed in with an API key).

        Read from the app-server at most every ``USAGE_LIMITS_TTL`` seconds; in between,
        its ``account/rateLimits/updated`` notifications keep the snapshot current.
        """
        from codexed.exceptions import CodexRequestError

        from agentpool.agents.codex_agent.usage_limits import to_usage_limits

        if not self._client:
            return None
        account = self._client.account
        stale = time.monotonic() - self._rate_limits_read_at >= USAGE_LIMITS_TTL
        if account.rate_limits is None or stale:
            try:
                await account.rate_limits_read()
            except CodexRequestError as e:
                # E.g. "chatgpt authentication required" for API keys: no plan limits
                self.log.debug("Codex rate limits unavailable", error=str(e))
                return None
            self._rate_limits_read_at = time.monotonic()
        snapshot = account.rate_limits
        return to_usage_limits(snapshot) if snapshot is not None else None

    async def get_auth_status(self) -> AuthStatus:
        """Get the account the Codex app-server is using."""
        from agentpool.agents.auth import AuthStatus

        if not self._client:
            return AuthStatus(state="unknown")
        try:
            response = await self._client.account.read()
        except Exception:
            self.log.exception("Failed to read Codex account")
            return AuthStatus(state="unknown")
        return to_auth_status(response)

    @property
    def config_options(self) -> Sequence[ConfigOption[Self]]:
        """Approval policy, reasoning effort, sandbox, personality and model."""
        from agentpool.agents.codex_agent.config_options import CONFIG_OPTIONS

        return CONFIG_OPTIONS

    async def list_sessions(
        self,
        *,
        cwd: str | None = None,
        limit: int | None = None,
    ) -> list[SessionData]:
        """List threads ("sessions") from Codex server."""
        if not self._client:
            return []
        try:
            response = await self._client.thread_list(limit=limit)
        except Exception:
            self.log.exception("Failed to list Codex threads")
            return []
        else:
            cwd = self.env.cwd or str(Path.cwd())
            result = [to_session_data(i, agent_name=self.name, cwd=cwd) for i in response.data]
            # Apply cwd filter (Codex doesn't support cwd filter in request)
            if cwd is not None:
                result = [s for s in result if s.cwd == cwd]
            return result

    async def load_session(self, session_id: str) -> SessionData | None:
        """Load and resume a thread from Codex server.

        Resumes the specified thread on the Codex server, making it the active thread
        for this agent. The conversation history is managed by the Codex server.

        Args:
            session_id: A pool session ID (as ACP `session/load` passes it), whose thread
                is stored as its `sdk_session_id`, or the ID of a thread listed from the
                Codex server

        Returns:
            SessionData if thread was resumed successfully, None otherwise
        """
        if not self._client:
            self.log.error("Cannot load session: Codex client not initialized")
            return None
        storage = self.storage
        pool_session = await storage.load_session(session_id) if storage is not None else None
        # Codex knows only its own thread IDs, never the pool's
        thread_id = (
            pool_session.sdk_session_id
            if pool_session is not None and pool_session.sdk_session_id
            else session_id
        )
        try:
            session = await self._resume_thread(thread_id)
        except Exception:
            self.log.exception(
                "Failed to resume Codex thread", session_id=session_id, thread_id=thread_id
            )
            return None
        thread = session.response.thread
        self.log.info("Thread resumed from Codex server", sdk_session_id=thread.id)
        # Convert turns to ChatMessages and populate the session's history
        if thread.turns:
            chat_messages = turns_to_chat_messages(thread.turns)
            self._history.chat_messages.clear()
            self._history.chat_messages.extend(chat_messages)
            self.log.info(
                "Restored conversation history",
                session_id=session_id,
                turn_count=len(thread.turns),
                message_count=len(chat_messages),
            )
        cwd = self.env.cwd or str(Path.cwd())
        return to_session_data(thread, agent_name=self.name, cwd=cwd)
