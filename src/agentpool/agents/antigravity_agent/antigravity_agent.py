"""Google Antigravity agent - wraps the official google-antigravity Python SDK."""

from __future__ import annotations

from contextlib import suppress
from datetime import UTC, datetime
import os
from pathlib import Path
import time
from typing import TYPE_CHECKING, Any, ClassVar, Self, assert_never, cast
from uuid import uuid4

import anyenv
import platformdirs
from pydantic import TypeAdapter, ValidationError

from agentpool.agents.antigravity_agent.converters import (
    elicitation_to_question_response,
    mcp_config_to_antigravity,
    question_to_elicitation,
    sanitize_mcp_server_name,
    steps_to_chat_messages,
    to_finish_reason,
    to_run_usage,
    user_content_to_antigravity,
)
from agentpool.agents.antigravity_agent.static_info import DEFAULT_MODEL, MODELS
from agentpool.agents.antigravity_agent.steps import StepTracker
from agentpool.agents.base_agent import BaseAgent
from agentpool.agents.capabilities import AgentCapabilities
from agentpool.agents.events import RunErrorEvent, RunStartedEvent, StreamCompleteEvent
from agentpool.agents.events.reconstructor import MessageReconstructor
from agentpool.agents.exceptions import AgentNotInitializedError
from agentpool.log import get_logger
from agentpool.messaging import ChatMessage


if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Sequence
    from types import TracebackType

    from exxec import ExecutionEnvironment
    from google.antigravity import Agent as SDKAgent, AgentConfig as SDKAgentConfig
    from google.antigravity.hooks.hooks import Hook, HookContext
    from google.antigravity.hooks.policy import Policy
    from google.antigravity.types import (
        AskQuestionInteractionSpec,
        McpServerConfig as SDKMcpServerConfig,
        QuestionHookResult,
        StopReason,
        ToolCall,
        ToolResult,
    )
    from pydantic_ai import RunUsage, UserContent
    from slashed import BaseCommand
    from tokonomics.model_discovery.model_info import ModelInfo
    from toprompt import AnyPromptType

    from agentpool.agents.auth import AuthStatus
    from agentpool.agents.events import RichAgentStreamEvent
    from agentpool.agents.modes import ConfigOption
    from agentpool.common_types import AnyEventHandlerType, StrPath
    from agentpool.delegation import AgentPool
    from agentpool.hooks import AgentHooks
    from agentpool.messaging import MessageHistory
    from agentpool.models.antigravity_agents import (
        AntigravityAgentConfig,
        AntigravityBehavior,
        AntigravityBudgetConfig,
        AntigravityBuiltinTool,
        AntigravityPolicyMode,
        AntigravityServiceTier,
        AntigravityThinkingLevel,
    )
    from agentpool.resource_providers import ResourceProvider
    from agentpool.sessions import SessionData
    from agentpool.ui.base import InputProvider
    from agentpool_config.events import EventConfig
    from agentpool_config.mcp_server import MCPServerConfig


logger = get_logger(__name__)

_INSTALL_HINT = (
    "google-antigravity is not installed. "
    "Install the optional extra with: uv pip install 'agentpool[antigravity]'"
)


def _ensure_sdk_available() -> None:
    """Verify that the optional ``google-antigravity`` dependency is installed."""
    try:
        import google.antigravity  # noqa: F401
    except ImportError as exc:
        raise ImportError(_INSTALL_HINT) from exc


class AntigravityAgent[TDeps = None, OutputDataT = str](BaseAgent[TDeps, OutputDataT]):
    """Agent backed by Google's ``google-antigravity`` Python SDK.

    Runs Antigravity's local harness with built-in coding/file/shell tools,
    multimodal inputs, native structured output, policy-driven tool confirmations,
    interactive questions, session persistence, and AgentPool toolsets exposed
    over an in-process Streamable HTTP MCP bridge.
    """

    AGENT_TYPE: ClassVar = "antigravity"
    CAPABILITIES: ClassVar[AgentCapabilities] = AgentCapabilities(
        structured_output=True,
        multimodal_input=True,
        native_compaction=False,  # It compacts on its own threshold; compact() is not wired
    )

    def __init__(  # noqa: PLR0915
        self,
        *,
        model: str | None = None,
        thinking_level: AntigravityThinkingLevel | None = None,
        service_tier: AntigravityServiceTier | None = None,
        max_output_tokens: int | None = None,
        api_key: str | None = None,
        vertex: bool | None = None,
        project: str | None = None,
        location: str | None = None,
        base_url: str | None = None,
        http_headers: dict[str, str] | None = None,
        openai_compatible: bool = False,
        system_prompt: str | Sequence[str | AnyPromptType] | None = None,
        include_builtin_system_prompt: bool = True,
        behavior: AntigravityBehavior = "autonomous",
        policy_mode: AntigravityPolicyMode = "allow_all",
        workspace_only: bool = False,
        enabled_tools: Sequence[AntigravityBuiltinTool] | None = None,
        disabled_tools: Sequence[AntigravityBuiltinTool] | None = None,
        enable_subagents: bool = True,
        max_subagent_depth: int | None = None,
        enable_daemons: bool = False,
        command_timeout: float | None = None,
        enable_sandbox: bool = False,
        tool_output_max_tokens: int | None = None,
        mcp_max_output_bytes: int | None = None,
        workspaces: Sequence[str] | None = None,
        skills_paths: Sequence[str] | None = None,
        save_dir: str | None = None,
        app_data_dir: str | None = None,
        compaction_token_threshold: int | None = None,
        budget: AntigravityBudgetConfig | None = None,
        env_vars: dict[str, str] | None = None,
        debug: bool = False,
        session_id: str | None = None,
        toolsets: list[ResourceProvider] | None = None,
        custom_tools: Sequence[Callable[..., Any]] | None = None,
        sdk_agent_factory: Callable[[SDKAgentConfig], SDKAgent] | None = None,
        deps_type: type[TDeps] | None = None,
        name: str = "antigravity",
        description: str | None = None,
        display_name: str | None = None,
        mcp_servers: Sequence[str | MCPServerConfig] | None = None,
        env: ExecutionEnvironment | StrPath | None = None,
        agent_pool: AgentPool[Any] | None = None,
        enable_logging: bool = True,
        event_configs: Sequence[EventConfig] | None = None,
        input_provider: InputProvider | None = None,
        output_type: type[OutputDataT] = str,  # type: ignore[assignment]  # ty:ignore[invalid-parameter-default]
        event_handlers: Sequence[AnyEventHandlerType] | None = None,
        commands: Sequence[BaseCommand] | None = None,
        hooks: AgentHooks | None = None,
    ) -> None:
        """Initialize an Antigravity agent; the SDK harness starts on context entry.

        Args:
            model: Model name (defaults to ``gemini-4-hydrogen``)
            thinking_level: Extended thinking depth (``minimal`` .. ``xhigh``)
            service_tier: Gemini inference tier (``standard``, ``priority``, ``flex``)
            max_output_tokens: Maximum output tokens per model invocation
            api_key: Gemini API key or Vertex AI Express key
            vertex: Whether to use the Vertex AI backend
            project: Google Cloud project ID for Vertex AI
            location: Google Cloud location for Vertex AI
            base_url: Optional custom endpoint base URL
            http_headers: Additional HTTP headers for model API requests
            openai_compatible: Use ``LocalOpenAIAgentConfig`` for OpenAI-compatible servers
            system_prompt: System prompt string or sequence of prompt items
            include_builtin_system_prompt: Append to harness defaults when True, replace when False
            behavior: Harness behavior (``autonomous``, ``interactive``, ``minimal``)
            policy_mode: Tool approval policy preset
            workspace_only: Restrict file tools to configured workspaces
            enabled_tools: Explicit allowlist of built-in harness tools
            disabled_tools: Explicit denylist of built-in harness tools
            enable_subagents: Allow harness subagent delegation
            max_subagent_depth: Maximum subagent recursion depth
            enable_daemons: Allow background daemon shell commands
            command_timeout: Timeout in seconds for ``run_command``
            enable_sandbox: Enable OS-level command sandbox (exebox)
            tool_output_max_tokens: Token limit for truncating large tool outputs
            mcp_max_output_bytes: Max bytes for MCP tool output before spilling to disk
            workspaces: Workspace directories (defaults to ``env.cwd``)
            skills_paths: Directories containing ``SKILL.md`` files
            save_dir: Directory for persisting Antigravity trajectories
            app_data_dir: Optional absolute path for Antigravity app data
            compaction_token_threshold: Token threshold for context compaction
            budget: Session-level budget limits
            env_vars: Extra environment variables for the localharness subprocess
            debug: Enable Antigravity SDK debug logging and tracing
            session_id: Existing Antigravity ``conversation_id`` to resume on connect
            toolsets: AgentPool resource providers exposed via an in-process MCP bridge
            custom_tools: Python callables registered directly as Antigravity custom tools
            sdk_agent_factory: Optional factory creating the SDK ``Agent`` (useful for testing)
            deps_type: Type of dependencies for the agent
            name: Agent name
            description: Agent description
            display_name: Human-readable display name
            mcp_servers: External MCP server configurations
            env: Execution environment or working directory path
            agent_pool: Pool owning this agent
            enable_logging: Whether to log interactions to storage
            event_configs: Event trigger configurations
            input_provider: Provider for user input, confirmations, and elicitations
            output_type: Output type for structured responses (default: ``str``)
            event_handlers: Event handlers for stream events
            commands: Slash commands for this agent
            hooks: AgentPool lifecycle hooks (pre/post run and tool hooks)
        """
        from agentpool.agents.sys_prompts import SystemPrompts
        from agentpool.mcp_server.tool_bridge import ToolManagerBridge
        from agentpool_config.mcp_server import BaseMCPServerConfig

        super().__init__(
            name=name,
            deps_type=deps_type,
            description=description,
            display_name=display_name,
            agent_pool=agent_pool,
            enable_logging=enable_logging,
            event_configs=event_configs,
            env=env,
            input_provider=input_provider,
            output_type=output_type,
            event_handlers=event_handlers,
            commands=commands,
            hooks=hooks,
        )

        # Model & endpoint settings
        self._model = model
        self._thinking_level: AntigravityThinkingLevel | None = thinking_level
        self._service_tier: AntigravityServiceTier | None = service_tier
        self._max_output_tokens = max_output_tokens
        self._api_key = api_key
        self._vertex = vertex
        self._project = project
        self._location = location
        self._base_url = base_url
        self._http_headers = dict(http_headers or {})
        self._openai_compatible = openai_compatible

        # System prompts
        self._include_builtin_system_prompt = include_builtin_system_prompt
        all_prompts: list[AnyPromptType] = []
        if system_prompt is not None:
            if isinstance(system_prompt, str):
                all_prompts.append(system_prompt)
            else:
                all_prompts.extend(system_prompt)
        prompt_manager = agent_pool.prompt_manager if agent_pool else None
        self.sys_prompts = SystemPrompts(all_prompts, prompt_manager=prompt_manager)

        # Capabilities & policy settings
        self._behavior: AntigravityBehavior = behavior
        self._policy_mode: AntigravityPolicyMode = policy_mode
        self._workspace_only = workspace_only
        self._enabled_tools = list(enabled_tools) if enabled_tools is not None else None
        self._disabled_tools = list(disabled_tools) if disabled_tools is not None else None
        self._enable_subagents = enable_subagents
        self._max_subagent_depth = max_subagent_depth
        self._enable_daemons = enable_daemons
        self._command_timeout = command_timeout
        self._enable_sandbox = enable_sandbox
        self._tool_output_max_tokens = tool_output_max_tokens
        self._mcp_max_output_bytes = mcp_max_output_bytes
        self._workspaces = list(workspaces or [])
        self._skills_paths = list(skills_paths or [])
        self._save_dir_override = save_dir
        self._app_data_dir = app_data_dir
        self._compaction_token_threshold = compaction_token_threshold
        self._budget = budget
        self._env_vars = dict(env_vars or {})
        self._debug = debug

        # Toolsets & MCP
        self._toolsets = toolsets or []
        self._custom_tools = list(custom_tools or [])
        self._external_mcp_servers = [
            BaseMCPServerConfig.from_string(s) if isinstance(s, str) else s
            for s in mcp_servers or []
        ]
        self._tool_bridge = ToolManagerBridge(node=self)
        self._bridge_started = False

        # Runtime state
        self._sdk_agent_factory = sdk_agent_factory
        self._sdk_agent: SDKAgent | None = None
        self._sdk_session_id = session_id
        self._needs_restart = False
        self._entered = False
        self._active_input_provider: InputProvider | None = None
        self._turn_tool_results: dict[str, ToolResult] = {}
        self._tool_start_times: dict[str, float] = {}
        self._tool_inputs: dict[str, dict[str, Any]] = {}

    @classmethod
    def from_config(
        cls,
        config: AntigravityAgentConfig,
        *,
        event_handlers: Sequence[AnyEventHandlerType] | None = None,
        input_provider: InputProvider | None = None,
        agent_pool: AgentPool[Any] | None = None,
        deps_type: type[TDeps] | None = None,
    ) -> Self:
        """Create an Antigravity agent from its configuration model."""
        from agentpool.utils.result_utils import to_type

        responses = agent_pool.manifest.responses if agent_pool is not None else None
        resolved_output_type = to_type(config.output_type or str, responses)
        handlers: list[AnyEventHandlerType] = [
            *config.get_event_handlers(),
            *(event_handlers or []),
        ]
        return cls(
            name=config.name or "antigravity",
            deps_type=deps_type,
            description=config.description,
            display_name=config.display_name,
            model=config.model,
            thinking_level=config.thinking_level,
            service_tier=config.service_tier,
            max_output_tokens=config.max_output_tokens,
            api_key=config.get_api_key(),
            vertex=config.vertex,
            project=config.project,
            location=config.location,
            base_url=config.base_url,
            http_headers=config.get_http_headers(),
            openai_compatible=config.openai_compatible,
            system_prompt=config.system_prompt,
            include_builtin_system_prompt=config.include_builtin_system_prompt,
            behavior=config.behavior,
            policy_mode=config.policy_mode,
            workspace_only=config.workspace_only,
            enabled_tools=config.enabled_tools,
            disabled_tools=config.disabled_tools,
            enable_subagents=config.enable_subagents,
            max_subagent_depth=config.max_subagent_depth,
            enable_daemons=config.enable_daemons,
            command_timeout=config.command_timeout,
            enable_sandbox=config.enable_sandbox,
            tool_output_max_tokens=config.tool_output_max_tokens,
            mcp_max_output_bytes=config.mcp_max_output_bytes,
            workspaces=config.workspaces,
            skills_paths=config.skills_paths,
            save_dir=config.save_dir,
            app_data_dir=config.app_data_dir,
            compaction_token_threshold=config.compaction_token_threshold,
            budget=config.budget,
            env_vars=config.get_env_vars(),
            debug=config.debug,
            mcp_servers=config.get_mcp_servers(),
            toolsets=config.get_tool_providers(),
            env=config.get_execution_environment(),
            agent_pool=agent_pool,
            input_provider=input_provider,
            output_type=resolved_output_type,  # type: ignore[arg-type]  # ty:ignore[invalid-argument-type]
            event_handlers=handlers or None,
            hooks=config.hooks.get_agent_hooks(),
            event_configs=list(config.triggers),
        )

    # --- Lifecycle --------------------------------------------------------------------

    def _resolve_save_dir(self) -> Path:
        if self._save_dir_override:
            path = Path(self._save_dir_override).expanduser().resolve()
        else:
            base = Path(platformdirs.user_data_dir("agentpool")) / "antigravity"
            path = base / sanitize_mcp_server_name(self.name)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _resolve_workspaces(self) -> list[str]:
        if self._workspaces:
            return list(self._workspaces)
        return [str(self.env.cwd or Path.cwd())]

    def _mark_needs_restart(self) -> None:
        self._needs_restart = True

    async def _setup_toolsets(self) -> None:
        if not self._toolsets:
            return
        for provider in self._toolsets:
            self.tools.add_provider(provider)
        await self._tool_bridge.start()
        self._bridge_started = True

    async def _close_sdk_agent(self) -> None:
        if self._sdk_agent is not None:
            sdk_agent = self._sdk_agent
            self._sdk_agent = None
            await sdk_agent.__aexit__(None, None, None)

    async def _start_sdk_agent(
        self,
        *,
        resume_conversation_id: str | None = None,
    ) -> SDKAgent:
        _ensure_sdk_available()
        from google.antigravity import Agent as SDKAgent

        await self._close_sdk_agent()
        sdk_config = await self._build_sdk_config(resume_conversation_id=resume_conversation_id)
        sdk_agent = (
            self._sdk_agent_factory(sdk_config)
            if self._sdk_agent_factory is not None
            else SDKAgent(sdk_config)
        )
        await sdk_agent.__aenter__()
        self._sdk_agent = sdk_agent
        self._needs_restart = False
        if sdk_agent.conversation_id:
            self._sdk_session_id = sdk_agent.conversation_id
        return sdk_agent

    async def _ensure_sdk_agent(self) -> SDKAgent:
        if not self._entered:
            raise AgentNotInitializedError
        if self._sdk_agent is None or self._needs_restart:
            resume_id = (
                self._sdk_session_id
                if self._sdk_session_id
                and self._sdk_agent is not None
                and self._sdk_agent.conversation.turn_count > 0
                else None
            )
            return await self._start_sdk_agent(resume_conversation_id=resume_id)
        return self._sdk_agent

    async def __aenter__(self) -> Self:
        _ensure_sdk_available()
        await super().__aenter__()
        self._entered = True
        await self._setup_toolsets()
        await self._start_sdk_agent(resume_conversation_id=self._sdk_session_id)
        if self._sdk_session_id and self._sdk_agent is not None:
            history = self._sdk_agent.conversation.history
            if history:
                messages = steps_to_chat_messages(
                    history,
                    session_id=self.session_id or self._sdk_session_id,
                    agent_name=self.name,
                    model_name=self.model_name,
                )
                self.conversation.chat_messages.clear()
                self.conversation.chat_messages.extend(messages)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self._entered = False
        try:
            await self._close_sdk_agent()
        finally:
            if self._bridge_started:
                with suppress(Exception):
                    await self._tool_bridge.stop()
                self._bridge_started = False
            await super().__aexit__(exc_type, exc_val, exc_tb)

    # --- SDK Config Construction ------------------------------------------------------

    def _build_policies(self, workspaces: list[str]) -> list[Policy]:
        from google.antigravity.hooks import policy

        policies: list[Policy] = []
        match self._policy_mode:
            case "allow_all":
                policies.append(policy.allow_all())
            case "confirm_run_command":
                policies.extend(policy.confirm_run_command(handler=self._handle_tool_approval))
            case "safe_defaults":
                policies.extend(policy.safe_defaults(handler=self._handle_tool_approval))
            case "auto":
                policies.append(policy.auto(handler=self._handle_tool_approval))
            case "deny_all":
                policies.append(policy.deny_all())
            case _ as unreachable:
                assert_never(unreachable)
        if self._workspace_only and workspaces:
            policies.extend(policy.workspace_only(workspaces))
        return policies

    def _build_sdk_hooks(self) -> list[Hook]:
        from google.antigravity.hooks.hooks import (
            OnInteractionHook,
            PostToolCallHook,
            PreToolCallDecideHook,
        )
        from google.antigravity.types import (
            HookResult as SDKHookResult,
            QuestionHookResult,
            QuestionResponse,
        )

        agent = self

        class _PreToolHook(PreToolCallDecideHook):
            async def run(self, context: HookContext, data: ToolCall) -> SDKHookResult:
                del context
                tc_id = data.id or data.step_id or str(data.name)
                agent._tool_start_times[tc_id] = time.perf_counter()
                agent._tool_inputs[tc_id] = dict(data.args)
                if agent.hooks is None or not agent.hooks.pre_tool_use:
                    return SDKHookResult(allow=True)
                result = await agent.hooks.run_pre_tool_hooks(
                    agent_name=agent.name,
                    tool_name=str(data.name),
                    tool_input=dict(data.args),
                    session_id=agent.session_id,
                    env=agent.env,
                )
                if result.get("decision") == "deny":
                    return SDKHookResult(
                        allow=False,
                        message=result.get("reason") or "Blocked by pre-tool hook",
                    )
                modified = result.get("modified_input")
                return SDKHookResult(allow=True, modified_args=modified)

        class _PostToolHook(PostToolCallHook):
            async def run(self, context: HookContext, data: ToolResult) -> None:
                del context
                if data.id:
                    agent._turn_tool_results[data.id] = data
                if data.step_id:
                    agent._turn_tool_results[data.step_id] = data
                if agent.hooks is None or not agent.hooks.post_tool_use:
                    return
                tc_id = data.id or data.step_id or str(data.name)
                started = agent._tool_start_times.pop(tc_id, None)
                duration_ms = (time.perf_counter() - started) * 1000.0 if started else 0.0
                tool_input = agent._tool_inputs.pop(tc_id, {})
                await agent.hooks.run_post_tool_hooks(
                    agent_name=agent.name,
                    tool_name=str(data.name),
                    tool_input=tool_input,
                    tool_output=data.error if data.error is not None else data.result,
                    duration_ms=duration_ms,
                    session_id=agent.session_id,
                    env=agent.env,
                )

        class _InteractionHook(OnInteractionHook):
            async def run(
                self,
                context: HookContext,
                data: AskQuestionInteractionSpec,
            ) -> QuestionHookResult:
                del context
                provider = agent._active_input_provider or agent._input_provider
                if provider is None:
                    logger.warning(
                        "Antigravity asked a question, but no input provider is configured"
                    )
                    return QuestionHookResult(
                        responses=[QuestionResponse(skipped=True) for _ in data.questions]
                    )
                responses: list[QuestionResponse] = []
                cancelled = False
                for entry in data.questions:
                    params = question_to_elicitation(entry)
                    with agent.awaiting_user():
                        elicit_result = await provider.get_elicitation(params)
                    from mcp.types import ElicitResult

                    if isinstance(elicit_result, ElicitResult) and elicit_result.action == "cancel":
                        cancelled = True
                        responses.append(QuestionResponse(skipped=True))
                    else:
                        responses.append(elicitation_to_question_response(entry, elicit_result))
                return QuestionHookResult(responses=responses, cancelled=cancelled)

        sdk_hooks: list[Hook] = [_PostToolHook(), _InteractionHook()]
        if self.hooks is not None and (self.hooks.pre_tool_use or self.hooks.post_tool_use):
            sdk_hooks.insert(0, _PreToolHook())
        return sdk_hooks

    def _build_mcp_servers(self) -> list[SDKMcpServerConfig]:
        from google.antigravity.types import McpStreamableHttpServer

        servers: list[SDKMcpServerConfig] = []
        if self._bridge_started and self._tool_bridge._actual_port is not None:
            servers.append(
                McpStreamableHttpServer(
                    name=sanitize_mcp_server_name(self._tool_bridge.resolved_server_name),
                    url=self._tool_bridge.url,
                )
            )
        servers.extend(
            converted
            for cfg in self._external_mcp_servers
            if (converted := mcp_config_to_antigravity(cfg)) is not None
        )
        return servers

    def _use_vertex(self) -> bool:
        if self._vertex is not None:
            return self._vertex
        return os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "").lower() in (
            "true",
            "1",
        ) or os.environ.get("GOOGLE_GENAI_USE_ENTERPRISE", "").lower() in ("true", "1")

    async def _build_sdk_config(
        self,
        *,
        resume_conversation_id: str | None = None,
    ) -> SDKAgentConfig:
        from google.antigravity import (
            AgentBehavior,
            BuiltinTools,
            CapabilitiesConfig,
            CompactionConfig,
            CustomSystemInstructions,
            GeminiAPIEndpoint,
            GeminiModelOptions,
            LocalAgentConfig,
            LocalOpenAIAgentConfig,
            ModelTarget,
            ModelType,
            ServiceTier,
            SystemInstructionSection,
            TemplatedSystemInstructions,
            ThinkingLevel,
            ToolOutputTruncationConfig,
            VertexEndpoint,
        )
        from google.antigravity.types import (
            BudgetConfig,
            BudgetScope,
            RunCommandConfig,
            SessionContinuationMode,
        )

        # System instructions
        formatted_prompt = await self.sys_prompts.format_system_prompt(self)
        system_instructions: CustomSystemInstructions | TemplatedSystemInstructions | None = None
        if formatted_prompt:
            if self._include_builtin_system_prompt:
                system_instructions = TemplatedSystemInstructions(
                    sections=[SystemInstructionSection(content=formatted_prompt)]
                )
            else:
                system_instructions = CustomSystemInstructions(text=formatted_prompt)

        # Capabilities
        enabled_tools = (
            [BuiltinTools(t) for t in self._enabled_tools]
            if self._enabled_tools is not None
            else None
        )
        disabled_tools = (
            [BuiltinTools(t) for t in self._disabled_tools]
            if self._disabled_tools is not None
            else None
        )
        behavior_enum = AgentBehavior(self._behavior)
        if behavior_enum == AgentBehavior.INTERACTIVE and enabled_tools is None:
            excluded = set(disabled_tools or [])
            enabled_tools = [
                t for t in (*BuiltinTools.default(), BuiltinTools.ASK_QUESTION) if t not in excluded
            ]
            disabled_tools = None

        run_command_cfg: RunCommandConfig | None = None
        if self._enable_daemons or self._command_timeout is not None or self._enable_sandbox:
            run_command_cfg = RunCommandConfig(
                enable_daemons=self._enable_daemons,
                timeout_seconds=self._command_timeout,
                enable_sandbox=self._enable_sandbox,
            )

        truncation_cfg = (
            ToolOutputTruncationConfig(max_tokens=self._tool_output_max_tokens)
            if self._tool_output_max_tokens is not None
            else None
        )
        capabilities = CapabilitiesConfig(
            enable_subagents=self._enable_subagents,
            agent_behavior=behavior_enum,
            enabled_tools=enabled_tools,
            disabled_tools=disabled_tools,
            max_subagent_depth=self._max_subagent_depth if self._enable_subagents else None,
            run_command_config=run_command_cfg,
            tool_output_truncation_config=truncation_cfg,
            mcp_max_output_bytes=self._mcp_max_output_bytes,
        )

        workspaces = self._resolve_workspaces()
        save_dir = str(self._resolve_save_dir())
        policies = self._build_policies(workspaces)
        sdk_hooks = self._build_sdk_hooks()
        mcp_servers = self._build_mcp_servers()

        compaction_config = (
            CompactionConfig(token_threshold=self._compaction_token_threshold)
            if self._compaction_token_threshold is not None
            else None
        )
        budget_config: BudgetConfig | None = None
        if self._budget is not None:
            budget_config = BudgetConfig(
                max_model_calls=self._budget.max_model_calls,
                max_tool_calls=self._budget.max_tool_calls,
                max_input_tokens=self._budget.max_input_tokens,
                max_output_tokens=self._budget.max_output_tokens,
                max_total_tokens=self._budget.max_total_tokens,
                scope=BudgetScope(self._budget.scope),
            )

        response_schema: dict[str, Any] | None = None
        if self._output_type not in (str, None):
            response_schema = TypeAdapter(self._output_type).json_schema()

        continuation_mode = (
            SessionContinuationMode.RESUME if resume_conversation_id is not None else None
        )

        common_kwargs: dict[str, Any] = {
            "system_instructions": system_instructions,
            "capabilities": capabilities,
            "tools": self._custom_tools or None,
            "policies": policies,
            "hooks": sdk_hooks,
            "mcp_servers": mcp_servers or None,
            "workspaces": workspaces,
            "conversation_id": resume_conversation_id,
            "session_continuation_mode": continuation_mode,
            "save_dir": save_dir,
            "app_data_dir": self._app_data_dir,
            "response_schema": response_schema,
            "skills_paths": self._skills_paths or None,
            "compaction_config": compaction_config,
            "budget_config": budget_config,
            "env": self._env_vars or None,
            "debug_config": True if self._debug else None,
        }

        if self._openai_compatible:
            return LocalOpenAIAgentConfig(
                model=self._model,
                base_url=self._base_url,
                **common_kwargs,
            )

        options: GeminiModelOptions | None = None
        if (
            self._thinking_level is not None
            or self._service_tier is not None
            or self._max_output_tokens is not None
        ):
            options = GeminiModelOptions(
                thinking_level=ThinkingLevel(self._thinking_level)
                if self._thinking_level is not None
                else None,
                service_tier=ServiceTier(self._service_tier)
                if self._service_tier is not None
                else None,
                max_output_tokens=self._max_output_tokens,
            )

        endpoint: VertexEndpoint | GeminiAPIEndpoint
        if self._use_vertex():
            endpoint = VertexEndpoint(
                project=self._project,
                location=self._location,
                api_key=self._api_key,
                base_url=self._base_url,
                http_headers=self._http_headers or None,
                options=options,
            )
        else:
            endpoint = GeminiAPIEndpoint(
                api_key=self._api_key,
                base_url=self._base_url,
                http_headers=self._http_headers or None,
                options=options,
            )

        model_target = ModelTarget(
            name=self._model or DEFAULT_MODEL,
            types=[ModelType.TEXT],
            endpoint=endpoint,
        )
        return LocalAgentConfig(
            model=model_target,
            api_key=self._api_key,
            vertex=self._use_vertex(),
            project=self._project,
            location=self._location,
            **common_kwargs,
        )

    async def _handle_tool_approval(self, tool_call: ToolCall, reason: str = "") -> bool:
        """Bridge Antigravity ``ask_user`` policy callbacks to AgentPool's InputProvider."""
        provider = self._active_input_provider or self._input_provider
        if provider is None:
            self.log.warning(
                "Antigravity requested tool confirmation, but no input provider is configured",
                tool=str(tool_call.name),
            )
            return False
        context = self.get_context(
            input_provider=provider,
            tool_call_id=tool_call.id or tool_call.step_id,
            tool_input=dict(tool_call.args),
            tool_name=str(tool_call.name),
        )
        with self.awaiting_user():
            result = await provider.get_tool_confirmation(context, tool_description=reason)
        match result:
            case "allow":
                return True
            case "skip":
                return False
            case "abort_run" | "abort_chain":
                await self._interrupt()
                return False
            case _ as unreachable:
                assert_never(unreachable)

    # --- Streaming Execution ----------------------------------------------------------

    async def _sync_sdk_session_id(self, conversation_id: str) -> None:
        if conversation_id and self._sdk_session_id != conversation_id:
            self._sdk_session_id = conversation_id
            if self.storage is not None and self.session_id is not None:
                await self.storage.update_sdk_session_id(self.session_id, conversation_id)

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
        store_history: bool = True,
    ) -> AsyncIterator[RichAgentStreamEvent[OutputDataT]]:
        from google.antigravity.types import (
            AntigravityCancelledError,
            AntigravityConnectionError,
            AntigravityExecutionError,
        )

        sdk_agent = await self._ensure_sdk_agent()
        final_session_id = session_id or self.session_id
        if final_session_id is None:
            raise ValueError("session_id must be set")
        run_id = str(uuid4())
        reply_id = message_id or str(uuid4())
        yield RunStartedEvent(session_id=final_session_id, run_id=run_id, agent_name=self.name)

        reconstructor = MessageReconstructor(
            initial_prompts=prompts,
            model_name=self.model_name,
            provider_name="google",
        )
        self._turn_tool_results.clear()
        self._tool_start_times.clear()
        self._tool_inputs.clear()
        tracker = StepTracker(
            agent_name=self.name,
            message_id=reply_id,
            session_id=final_session_id,
            tool_results=self._turn_tool_results,
        )

        effective_provider = input_provider or self._input_provider
        run_context = self.get_context(data=deps, input_provider=effective_provider)
        sdk_content = user_content_to_antigravity(prompts)
        conversation = sdk_agent.conversation
        failed = False

        self._active_input_provider = effective_provider
        try:
            async with self._tool_bridge.set_run_context(run_context, prompt=prompts):
                await conversation.send(sdk_content)
                async for step in conversation.receive_steps():
                    await self._sync_sdk_session_id(conversation.conversation_id)
                    for event in tracker.process_step(step):
                        reconstructor.observe(event)
                        yield event
        except AntigravityCancelledError:
            self.log.info("Antigravity turn cancelled", run_id=run_id)
        except (AntigravityExecutionError, AntigravityConnectionError) as exc:
            failed = True
            self.log.warning("Antigravity turn failed", error=str(exc), run_id=run_id)
            yield RunErrorEvent(message=str(exc), run_id=run_id, agent_name=self.name)
        finally:
            self._active_input_provider = None

        for event in tracker.flush_open_tools():
            reconstructor.observe(event)
            yield event

        await self._sync_sdk_session_id(conversation.conversation_id)
        stop_reason: StopReason | None = conversation._last_turn_stop_reason
        finish_reason = to_finish_reason(stop_reason, failed=failed)
        reconstructor.flush(finish_reason=finish_reason)

        structured = tracker.structured_output
        if structured is None:
            structured = conversation.get_last_structured_output()
        final_content = self._parse_output(reconstructor.text_content, structured)
        run_usage: RunUsage = to_run_usage(conversation.last_turn_usage)

        complete_msg: ChatMessage[OutputDataT] = ChatMessage(
            content=final_content,
            role="assistant",
            name=self.name,
            message_id=reply_id,
            session_id=final_session_id,
            parent_id=user_msg.message_id,
            usage=run_usage,
            model_name=self.model_name,
            messages=reconstructor.model_messages,
            finish_reason=finish_reason,
        )
        yield StreamCompleteEvent[OutputDataT](message=complete_msg)

    def _parse_output(self, final_text: str, structured: Any | None) -> OutputDataT:
        if self._output_type in (str, None):
            return cast("OutputDataT", final_text)
        adapter = TypeAdapter(self._output_type)
        if structured is not None:
            try:
                return adapter.validate_python(structured)
            except ValidationError as exc:
                self.log.warning(
                    "Failed to validate Antigravity structured_output",
                    error=str(exc),
                    output_type=self._output_type,
                )
        try:
            parsed = anyenv.load_json(final_text)
            return adapter.validate_python(parsed)
        except (anyenv.JsonLoadError, ValueError) as exc:
            self.log.warning(
                "Failed to parse structured output, returning raw text",
                error=str(exc),
                output_type=self._output_type,
            )
            return cast("OutputDataT", final_text)

    async def _interrupt(self) -> None:
        if self._sdk_agent is not None and self._sdk_agent.is_started:
            try:
                await self._sdk_agent.conversation.cancel()
            except Exception:
                self.log.exception("Failed to cancel Antigravity turn")

    async def steer(
        self, message: str, *, interrupt: bool = False, session_id: str | None = None
    ) -> None:
        """Steer a running Antigravity turn.

        Antigravity's SDK drains an active turn before ``send()`` delivers a new
        message, so ``interrupt=True`` cancels the in-flight turn and queues
        ``message`` to run immediately next; otherwise falls back to base injection.
        """
        if interrupt and self._active_runs > 0:
            self.queue_prompt(message, session_id=session_id)
            await self._interrupt()
            return
        await super().steer(message, interrupt=interrupt, session_id=session_id)

    async def start_new_session(self) -> None:
        """Start a fresh Antigravity conversation on the next turn."""
        await super().start_new_session()
        self._sdk_session_id = None
        await self._close_sdk_agent()
        if self._entered:
            await self._start_sdk_agent()

    # --- Models, Modes, Structured Output & Auth -------------------------------------

    @property
    def model_name(self) -> str:
        """Active model name."""
        return self._model or DEFAULT_MODEL

    def to_structured[NewOutputDataT](
        self,
        output_type: type[NewOutputDataT],
    ) -> AntigravityAgent[TDeps, NewOutputDataT]:
        """Configure structured output enforced via Antigravity's ``response_schema``."""
        from agentpool.utils.result_utils import to_type

        self._output_type = to_type(output_type)  # type: ignore[assignment]  # ty:ignore[invalid-assignment]
        self._mark_needs_restart()
        return self  # type: ignore[return-value]  # ty:ignore[invalid-explicit-return]

    async def set_model(self, model: str) -> None:
        """Switch the Gemini / endpoint model used by the agent."""
        await self._set_mode(model, "model")

    async def get_available_models(self) -> list[ModelInfo] | None:
        """Return curated Gemini models supported by Antigravity."""
        return list(MODELS)

    @property
    def config_options(self) -> Sequence[ConfigOption[Self]]:
        """Tool policy, thinking level, behavior, and model options."""
        from agentpool.agents.antigravity_agent.config_options import CONFIG_OPTIONS

        return CONFIG_OPTIONS

    async def get_auth_status(self) -> AuthStatus:
        """Report whether credentials for the configured backend are present."""
        from agentpool.agents.auth import AuthAccount, AuthStatus

        if self._openai_compatible or self._base_url:
            return AuthStatus(state="not_required")
        if self._use_vertex():
            if self._api_key:
                return AuthStatus(
                    state="authenticated",
                    accounts=[
                        AuthAccount(
                            label="Vertex AI Express API key",
                            description="Vertex AI Express Mode",
                            active=True,
                        )
                    ],
                )
            project = self._project or os.environ.get("GOOGLE_CLOUD_PROJECT")
            location = self._location or os.environ.get("GOOGLE_CLOUD_LOCATION")
            if project and location:
                return AuthStatus(
                    state="authenticated",
                    accounts=[
                        AuthAccount(
                            label=f"Vertex AI ({project}/{location})",
                            description="Google Cloud Vertex AI",
                            active=True,
                        )
                    ],
                )
            return AuthStatus(state="unauthenticated")

        if self._api_key or os.environ.get("GEMINI_API_KEY"):
            return AuthStatus(
                state="authenticated",
                accounts=[
                    AuthAccount(
                        label="Gemini API key",
                        description="Google Gemini Developer API",
                        active=True,
                    )
                ],
            )
        return AuthStatus(state="unauthenticated")

    # --- Sessions ---------------------------------------------------------------------

    async def list_sessions(
        self,
        *,
        cwd: str | None = None,
        limit: int | None = None,
    ) -> list[SessionData]:
        """List saved Antigravity sessions from pool storage (if configured)."""
        storage = self.storage
        if storage is None:
            return []
        try:
            session_ids = await storage.list_session_ids(agent_name=self.name)
            sessions: list[SessionData] = []
            for sid in session_ids:
                if (data := await storage.load_session(sid)) is not None:
                    if cwd is not None and data.cwd and data.cwd != cwd:
                        continue
                    sessions.append(data)
            sessions.sort(key=lambda s: s.last_active, reverse=True)
            return sessions[:limit] if limit is not None else sessions
        except Exception:
            self.log.exception("Failed to list Antigravity sessions")
            return []

    async def load_session(self, session_id: str) -> SessionData | None:
        """Resume a persisted Antigravity conversation and restore its history."""
        from agentpool.sessions import SessionData

        if not self._entered:
            raise AgentNotInitializedError

        pool_session = await self.storage.load_session(session_id) if self.storage else None
        conv_id = (
            pool_session.sdk_session_id
            if pool_session is not None and pool_session.sdk_session_id
            else session_id
        )
        try:
            sdk_agent = await self._start_sdk_agent(resume_conversation_id=conv_id)
        except Exception:
            self.log.exception(
                "Failed to resume Antigravity conversation",
                session_id=session_id,
                conversation_id=conv_id,
            )
            return None

        self._sdk_session_id = sdk_agent.conversation_id or conv_id
        messages = steps_to_chat_messages(
            sdk_agent.conversation.history,
            session_id=session_id,
            agent_name=self.name,
            model_name=self.model_name,
        )
        self.conversation.chat_messages.clear()
        self.conversation.chat_messages.extend(messages)
        if pool_session is not None:
            return pool_session
        now = datetime.now(tz=UTC)
        return SessionData(
            session_id=session_id,
            agent_name=self.name,
            cwd=str(self.env.cwd or Path.cwd()),
            sdk_session_id=self._sdk_session_id,
            created_at=now,
            last_active=now,
        )
