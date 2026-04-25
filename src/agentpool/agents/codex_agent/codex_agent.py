"""Codex agent - wraps Codex app-server via JSON-RPC protocol."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
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
from agentpool.agents.codex_agent.codex_converters import (
    mcp_config_to_codex,
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
from agentpool.agents.exceptions import (
    AgentNotInitializedError,
    UnknownCategoryError,
    UnknownModeError,
)
from agentpool.log import get_logger
from agentpool.messaging import ChatMessage


if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence
    from types import TracebackType

    from codexed import CodexClient, Session
    from codexed.models import (
        McpServerConfig,
        McpServerElicitationRequestParams,
        TokenUsageBreakdown,
        ToolRequestUserInputParams,
        ToolRequestUserInputResponse,
    )
    from codexed.request_handlers import ApprovalParams, ApprovalResponse
    from exxec import ExecutionEnvironment
    from pydantic_ai import UserContent
    from tokonomics.model_discovery.model_info import ModelInfo

    from agentpool.agents.context import ConfirmationResult
    from agentpool.agents.events import RichAgentStreamEvent
    from agentpool.agents.modes import ModeCategory
    from agentpool.common_types import AnyEventHandlerType, MCPServerStatus, StrPath
    from agentpool.delegation import AgentPool
    from agentpool.hooks import AgentHooks
    from agentpool.messaging import MessageHistory
    from agentpool.models.codex_agents import ApprovalPolicy, CodexAgentConfig
    from agentpool.resource_providers import ResourceProvider
    from agentpool.sessions.models import SessionData
    from agentpool.ui.base import InputProvider
    from agentpool_config.mcp_server import MCPServerConfig


logger = get_logger(__name__)

VALID_POLICIES = ["never", "on-request", "on-failure", "untrusted"]
VALID_EFFORTS = ["low", "medium", "high", "xhigh"]
VALID_SANDBOXES = ["read-only", "workspace-write", "danger-full-access", "external-sandbox"]
VALID_PERSONALITIES = ["none", "friendly", "pragmatic"]


class CodexAgent[TDeps = None, OutputDataT = str](BaseAgent[TDeps, OutputDataT]):
    """MessageNode that wraps a Codex app-server instance."""

    AGENT_TYPE: ClassVar = "codex"

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
    ) -> None:
        """Initialize Codex agent.

        Args:
            name: Agent name
            deps_type: Type of dependencies for the agent
            description: Agent description
            display_name: Human-readable display name
            model: Model to use (e.g., "claude-3-5-sonnet-20241022")
            reasoning_effort: Reasoning effort level ("low", "medium", "high")
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
            approval_policy: Approval policy for tool execution
            sandbox: Sandbox mode for execution
            personality: Personality preset (none, friendly, pragmatic)
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
        )

        # Codex settings
        self._base_instructions = base_instructions
        self._developer_instructions = developer_instructions
        self._approval_policy: ApprovalPolicy = approval_policy or "never"
        self._toolsets = toolsets or []
        self._env_vars = env_vars or {}
        # Client state
        self._client: CodexClient | None = None
        self._sdk_session_id: str | None = session_id
        self._sessions: dict[str, Session] = {}
        self._external_mcp_servers = [
            BaseMCPServerConfig.from_string(s) if isinstance(s, str) else s
            for s in mcp_servers or []
        ]
        # Extra MCP servers in Codex format (e.g., tool bridge)
        self._extra_mcp_servers: list[tuple[str, McpServerConfig]] = []
        # Mutable settings (can change mid-session via _set_mode)
        self._current_model: str | None = model
        self._current_effort: ReasoningEffort | None = reasoning_effort
        self._current_sandbox: SandboxMode | None = sandbox
        self._current_personality: Personality | None = personality
        self._adapter: CodexStreamedResponse | None = None
        # Populated by capture_metadata during streaming, read after stream completes
        self._token_usage_data: TokenUsageBreakdown | None = None
        # Pass injection_manager for mid-run injection support
        self._tool_bridge = ToolManagerBridge(node=self, injection_manager=self._injection_manager)

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
            # MCP and toolsets
            mcp_servers=config.get_mcp_servers(),
            toolsets=config.get_tool_providers(),
            # Runtime
            event_handlers=merged_handlers or None,
            input_provider=input_provider,
            agent_pool=agent_pool,
            output_type=resolved_output_type,  # type: ignore[arg-type]
            hooks=config.hooks.get_agent_hooks(),
        )

    async def _setup_toolsets(self) -> None:
        """Setup toolsets and start the tool bridge."""
        from codexed.models import HttpMcpServer as CodexHttpMcpServer

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
        mcp_server = CodexHttpMcpServer(url=self._tool_bridge.url)
        bridge_config = (self._tool_bridge.resolved_server_name, mcp_server)
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
            session = await self._client.thread_resume(self._sdk_session_id)
            thread = session.response.thread
            self._sdk_session_id = thread.id
            self._sessions[self._sdk_session_id] = session
            self.log.info("Codex thread resumed", sdk_session_id=self._sdk_session_id, cwd=cwd)
            # Restore conversation history from resumed thread
            chat_messages = turns_to_chat_messages(thread.turns)
            self.conversation.chat_messages.clear()
            self.conversation.chat_messages.extend(chat_messages)
            self.log.info("Restored conversation history", turn_count=len(thread.turns))
        else:
            # Start a new thread
            session = await self._client.thread_start(
                cwd=cwd,
                model=self._current_model,
                base_instructions=self._base_instructions,
                developer_instructions=self._developer_instructions,
                sandbox=self._current_sandbox,
                approval_policy=self._approval_policy,
                personality=self._current_personality,
            )
            self._sdk_session_id = session.thread_id
            self._sessions[self._sdk_session_id] = session
            self.log.info("Codex thread started", sdk_session_id=self._sdk_session_id, cwd=cwd)
        return self

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
        from codexed.models import (
            CommandExecutionRequestApprovalParams,
            FileChangeRequestApprovalParams,
            SkillRequestApprovalParams,
        )
        from codexed.models.misc import SkillRequestApprovalResponse
        from codexed.models.responses import FileChangeRequestApprovalResponse

        self.log.debug("Permission request")
        ctx = self._tool_bridge._current_context
        if ctx is None:
            raise RuntimeError("Permission callback invoked outside of an active run")
        input_provider = ctx.get_input_provider()
        result = await input_provider.get_tool_confirmation(ctx)
        mapping: dict[ConfirmationResult, Literal["allow"]] = {
            "allow": "allow",
            "skip": "allow",
            "abort_run": "allow",
            "abort_chain": "allow",
        }
        approval_decision = mapping[result]
        # Auto-grant if bypassPermissions mode is active
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
        from mcp.types import ErrorData

        ctx = self._tool_bridge._current_context
        if ctx is None:
            raise RuntimeError("MCP elicitation callback invoked outside of an active run")
        provider = ctx.get_input_provider()
        mcp_request = data.to_mcp()
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

        if self._tool_bridge._current_context is None:
            raise RuntimeError("User input callback invoked outside of an active run")

        input_provider = self._tool_bridge._current_context.get_input_provider()
        answers: dict[str, _Answer] = {}
        for question in params.questions:
            # Build a JSON schema property for this question
            props = {question.id: question.to_schema_property()}
            schema = {"type": "object", "properties": props, "required": [question.id]}
            # Build display message from header + question
            message = (
                f"{question.header}: {question.question}" if question.header else question.question
            )
            mcp_params = ElicitRequestFormParams(message=message, requestedSchema=schema)
            result = await input_provider.get_elicitation(params=mcp_params)

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
                            assert_never(unknown_type)  # ty:ignore[type-assertion-failure]
                case ElicitResult():
                    # User declined or cancelled
                    answers[question.id] = _Answer(answers=[])
                case _ as unreachable:
                    assert_never(unreachable)

        return _Response(answers=answers)

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
        wait_for_connections: bool | None = None,
        store_history: bool = True,
    ) -> AsyncIterator[RichAgentStreamEvent[OutputDataT]]:
        """Stream events from Codex turn execution."""
        from agentpool.agents.events import PlanUpdateEvent
        from agentpool.messaging.messages import TokenCost

        if not self._client or not self._sdk_session_id:
            raise AgentNotInitializedError

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
        if self.storage and self.session_id and self._sdk_session_id:
            await self.storage.update_sdk_session_id(self.session_id, self._sdk_session_id)
        # Stream turn events with bridge context set
        reconstructor = MessageReconstructor(initial_prompts=prompts, model_name=self.model_name)
        # Pass output type directly - adapter handles conversion to JSON schema
        # Resolve input provider: explicit parameter overrides agent default
        effective_input_provider = input_provider or self._input_provider
        run_context = self.get_context(data=deps, input_provider=effective_input_provider)
        session = self._sessions[self._sdk_session_id]
        raw_stream = session.turn_stream(
            input_items,
            model=self._current_model,
            effort=self._current_effort,
            approval_policy=self._approval_policy,
            sandbox_policy=self._current_sandbox,
            output_schema=None if self._output_type in (str, None) else self._output_type,
            personality=self._current_personality,
        )
        self._adapter = CodexStreamedResponse(provider_name="codex", stream=raw_stream)
        try:
            async with self._tool_bridge.set_run_context(run_context, prompt=prompts):
                # Wrap to capture metadata (turn_id, token usage), then convert
                async for native_event in self._adapter:
                    reconstructor.observe(native_event)
                    yield native_event
                    if isinstance(native_event, PlanUpdateEvent) and self.agent_pool:
                        self.agent_pool.todos.replace_all(native_event.entries)

        except Exception as e:
            self.log.exception("Error during Codex turn", error=str(e))
            raise
        finally:
            # Clear turn_id when turn completes or errors
            self._adapter = None

        # Emit completion event
        reconstructor.flush()
        final_text = reconstructor.text_content
        cost_info: TokenCost | None = None
        run_usage = RunUsage()

        if usage := self._token_usage_data:
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
            parent_id=parent_id,
            cost_info=cost_info,
            usage=run_usage,
            model_name=self.model_name,
            messages=reconstructor.model_messages,
            finish_reason=to_finish_reason(s)
            if self._adapter and (s := self._adapter._turn_status)
            else None,
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
        return self  # type: ignore[return-value]  # ty:ignore[invalid-return-type]

    async def set_model(self, model: str) -> None:
        """Set the model for this agent."""
        await self._set_mode(model, "model")

    async def set_approval_policy(self, policy: ApprovalPolicy) -> None:
        """Set the approval policy for tool execution.

        Args:
            policy: Approval policy - "never", "on-request", "on-failure", or "untrusted"
        """
        await self._set_mode(policy, "mode")

    async def _interrupt(self) -> None:
        """Call Codex turn_interrupt if there's an active turn."""
        if (
            self._client
            and self._sdk_session_id
            and self._adapter
            and self._adapter._current_turn_id
        ):
            try:
                session = self._sessions[self._sdk_session_id]
                await session.turn_interrupt(self._adapter._current_turn_id)
                self.log.info(
                    "Codex turn interrupted",
                    sdk_session_id=self._sdk_session_id,
                    turn_id=self._adapter._current_turn_id,
                )
            except Exception:
                self.log.exception("Failed to interrupt Codex turn")

    async def get_available_models(self) -> list[ModelInfo] | None:
        """Get available models from Codex server.

        Returns:
            List of tokonomics ModelInfo for available models, or None if not connected
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

    async def get_modes(self) -> list[ModeCategory]:
        """Get available mode categories for Codex agent (approval poliy, effort, model)."""
        from agentpool.agents.codex_agent.static_info import (
            EFFORT_MODES,
            PERSONALITY_MODES,
            POLICY_MODES,
            SANDBOX_MODES,
        )
        from agentpool.agents.modes import ModeCategory, ModeInfo

        categories = [
            ModeCategory(
                id="mode",
                name="Tool Approval",
                available_modes=POLICY_MODES,
                current_mode_id=self._approval_policy,
                category="mode",
            ),
            ModeCategory(
                id="thought_level",
                name="Reasoning Effort",
                available_modes=EFFORT_MODES,
                current_mode_id=self._current_effort or "medium",
                category="thought_level",
            ),
            ModeCategory(
                id="sandbox",
                name="Sandbox Mode",
                available_modes=SANDBOX_MODES,
                current_mode_id=self._current_sandbox or "workspace-write",
                category="other",
            ),
            ModeCategory(
                id="personality",
                name="Personality",
                available_modes=PERSONALITY_MODES,
                current_mode_id=self._current_personality or "none",
                category="other",
            ),
        ]
        if models := await self.get_available_models():
            model_modes = [
                ModeInfo(
                    value=m.id,
                    name=m.name or m.id,
                    description=m.description or "",
                    category_id="model",
                )
                for m in models
            ]
            categories.append(
                ModeCategory(
                    id="model",
                    name="Model",
                    available_modes=model_modes,
                    current_mode_id=self._current_model or "",
                    category="model",
                )
            )
        return categories

    async def _set_mode(self, mode_id: str | bool, category_id: str) -> None:
        """Handle approval_policy, reasoning_effort, and model mode switching."""
        from agentpool.models.codex_agents import ApprovalPolicy

        match category_id:
            case "mode" if mode_id in VALID_POLICIES:
                self._approval_policy = cast(ApprovalPolicy, mode_id)
            case "mode":
                raise UnknownModeError(mode_id, VALID_POLICIES)
            case "thought_level" if mode_id in VALID_EFFORTS:
                self._current_effort = cast(ReasoningEffort, mode_id)
            case "thought_level":
                raise UnknownModeError(mode_id, VALID_EFFORTS)
            case "model":
                assert isinstance(mode_id, str)
                self._current_model = mode_id
            case "sandbox" if mode_id in VALID_SANDBOXES:
                self._current_sandbox = cast(SandboxMode, mode_id)
            case "sandbox":
                raise UnknownModeError(mode_id, VALID_SANDBOXES)
            case "personality" if mode_id in VALID_PERSONALITIES:
                self._current_personality = cast(Personality, mode_id)
            case "personality":
                raise UnknownModeError(mode_id, VALID_PERSONALITIES)
            case _:
                raise UnknownCategoryError(category_id)
        await self.update_state(config_id=category_id, value_id=mode_id)

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
            session_id: Thread ID to resume

        Returns:
            SessionData if thread was resumed successfully, None otherwise
        """
        if not self._client:
            self.log.error("Cannot load session: Codex client not initialized")
            return None

        try:
            session = await self._client.thread_resume(session_id)
        except Exception:
            self.log.exception("Failed to resume Codex thread", session_id=session_id)
            return None
        # Update current thread ID
        thread = session.response.thread
        self._sdk_session_id = thread.id
        self._sessions[self._sdk_session_id] = session
        self.log.info("Thread resumed from Codex server", sdk_session_id=thread.id)
        # Convert turns to ChatMessages and populate conversation
        if thread.turns:
            chat_messages = turns_to_chat_messages(thread.turns)
            self.conversation.chat_messages.clear()
            self.conversation.chat_messages.extend(chat_messages)
            self.log.info(
                "Restored conversation history",
                session_id=session_id,
                turn_count=len(thread.turns),
                message_count=len(chat_messages),
            )
        cwd = self.env.cwd or str(Path.cwd())
        return to_session_data(thread, agent_name=self.name, cwd=cwd)
