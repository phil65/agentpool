"""ClaudeCodeAgent - Native Claude Agent SDK integration."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import replace
from decimal import Decimal
import os
from pathlib import Path
import re
import time
from typing import TYPE_CHECKING, Any, ClassVar, Literal, Self, assert_never, cast
import uuid

import anyio
from pydantic import TypeAdapter, ValidationError
from pydantic_ai import RunUsage, TextContent

from agentpool.agents.base_agent import BaseAgent
from agentpool.agents.claude_code_agent.converters import (
    confirmation_result_to_native,
    convert_mcp_servers_to_sdk_format,
    to_auth_status,
    to_finish_reason,
    to_mcp_server_status,
    to_prompt_input,
    to_run_usage,
    to_turn_error,
)
from agentpool.agents.claude_code_agent.idle_compaction import IdleCompaction
from agentpool.agents.claude_code_agent.idle_watch import IdleWatcher
from agentpool.agents.claude_code_agent.slash_commands import create_claude_code_command
from agentpool.agents.claude_code_agent.steering import RunSteering, run_messages
from agentpool.agents.events import RunErrorEvent, RunStartedEvent, StreamCompleteEvent
from agentpool.agents.events.reconstructor import MessageReconstructor
from agentpool.agents.exceptions import (
    AgentNotInitializedError,
)
from agentpool.common_types import MCPServerStatus
from agentpool.log import get_logger
from agentpool.messaging import ChatMessage
from agentpool.messaging.messages import TokenCost
from agentpool.sessions.models import SessionData
from agentpool.utils.streams import merge_queue_into_iterator
from agentpool.utils.time_utils import get_now


if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Generator, Mapping, Sequence
    from types import TracebackType

    from anthropic.types.beta import BetaUsage
    from clawd_code_sdk import (
        AgentDefinition,
        ClaudeSDKClient,
        McpServerConfig,
        PermissionMode,
        PermissionResult,
        ToolPermissionContext,
    )
    from clawd_code_sdk.models import (
        AskUserQuestionInput,
        ReasoningEffort,
        SDKControlElicitationRequest,
        StopReason,
        ToolInput,
    )
    from clawd_code_sdk.models.messages import RateLimitInfo, ResultMessage
    from clawd_code_sdk.models.server_info import (
        ClaudeCodeCommandInfo,
        ClaudeCodeModelInfo,
        ClaudeCodeServerInfo,
    )
    from evented_config import EventConfig
    from exxec import ExecutionEnvironment
    from mcp.types import ElicitResult
    from pydantic_ai import FinishReason, UserContent
    from slashed import BaseCommand
    from tokonomics.model_discovery.model_info import ModelInfo
    from tokonomics.model_names import AnthropicMaxModelName
    from toprompt import AnyPromptType

    from agentpool.agents.auth import AuthStatus
    from agentpool.agents.events import RichAgentStreamEvent
    from agentpool.agents.modes import ConfigOption
    from agentpool.agents.usage_limits import UsageLimits
    from agentpool.common_types import AnyEventHandlerType, SimpleJsonType, StrPath
    from agentpool.delegation import AgentPool
    from agentpool.hooks import AgentHooks
    from agentpool.messaging import MessageHistory
    from agentpool.models.claude_code_agents import ClaudeCodeAgentConfig, SettingSource, ToolName
    from agentpool.resource_providers import ResourceProvider
    from agentpool.storage import StorageManager
    from agentpool.ui.base import InputProvider
    from agentpool_config.mcp_server import MCPServerConfig

logger = get_logger(__name__)

_MCP_TOOL_PATTERN = re.compile(r"^mcp__agentpool-(.+)-tools__(.+)$")
"""Pattern to detect CC-provided tool names ( mcp__agentpool-{agent_name}-tools__{tool_name} )."""

# Commands that do not work through the SDK, as Zed's adapter filters them
# (https://github.com/agentclientprotocol/claude-agent-acp/blob/main/src/slash-commands.ts)
UNSUPPORTED_COMMANDS = frozenset({
    # "cost",
    "keybindings-help",
    "login",
    "logout",
    "output-style:new",
    "release-notes",
    "todos",
})


_EDIT_TOOLS = frozenset({
    "Edit",
    "Write",
    "NotebookEdit",
    "edit",
    "write",
    "edit_file",
    "write_file",
})
"""Tools that ``acceptEdits`` allows without asking (Claude Code's and agentpool's own)."""

TITLE_CONTEXT_CHARS = 1000
"""How much text a title is generated from (the CLI reads no more either)."""
MAX_PROMPT_TITLE = 60
"""Longest prompt that is its own session's title when the CLI finds it too short."""

USAGE_LIMITS_TTL = 300.0
"""Seconds a get_usage result is reused (rate limit events update it meanwhile)."""


def _strip_mcp_prefix(tool_name: str) -> str:
    """Strip MCP server prefix from tool names for cleaner UI display.

    Handles dynamic prefixes like mcp__agentpool-{agent_name}-tools__{tool}
    """
    if match := _MCP_TOOL_PATTERN.match(tool_name):
        return match.group(2)  # group(1) is agent name, group(2) is tool name
    return tool_name


class ClaudeCodeAgent[TDeps = None, TResult = str](BaseAgent[TDeps, TResult]):
    """Agent running the Claude Code CLI through clawd-code-sdk's ClaudeSDKClient.

    This provides native integration with Claude Code, enabling:
    - Bidirectional streaming for interactive conversations
    - Tool permission handling via can_use_tool callback
    - Full access to Claude Code's capabilities (file ops, terminals, etc.)
    - Steering user messages into a running turn (:meth:`steer`)

    The agent manages:
    - ClaudeSDKClient lifecycle (connects in the background on enter, disconnects on exit)
    - Event conversion from Claude SDK to agentpool events
    - Tool confirmation via input provider
    - agentpool toolsets, exposed to the CLI through an MCP bridge
    - The CLI's models, permission mode, effort and fast mode as config options
    """

    AGENT_TYPE: ClassVar = "claude"
    TITLES_OWN_SESSIONS: ClassVar = True

    def __init__(  # noqa: PLR0915
        self,
        *,
        name: str | None = None,
        deps_type: type[TDeps] | None = None,
        description: str | None = None,
        display_name: str | None = None,
        allowed_tools: list[ToolName | str] | None = None,
        disallowed_tools: list[ToolName | str] | None = None,
        system_prompt: str | Sequence[str | AnyPromptType] | None = None,
        include_builtin_system_prompt: bool = True,
        model: AnthropicMaxModelName | str | None = "opus",
        max_turns: int | None = None,
        max_budget_usd: float | None = None,
        reasoning_effort: ReasoningEffort | None = None,
        permission_mode: PermissionMode | None = None,
        mcp_servers: Sequence[MCPServerConfig] | None = None,
        env_vars: dict[str, str] | None = None,
        add_dir: list[str] | None = None,
        builtin_tools: list[ToolName | str] | None = None,
        fallback_model: AnthropicMaxModelName | str | None = None,
        setting_sources: list[SettingSource] | None = None,
        use_subscription: bool = False,
        prompt_suggestions: bool = False,
        enable_tool_search: bool | int | Literal["auto"] | None = None,
        idle_compaction: bool = True,
        env: ExecutionEnvironment | StrPath | None = None,
        input_provider: InputProvider | None = None,
        agent_pool: AgentPool[Any] | None = None,
        enable_logging: bool = True,
        event_configs: Sequence[EventConfig] | None = None,
        event_handlers: Sequence[AnyEventHandlerType] | None = None,
        output_type: type[TResult] | None = None,
        builtin_subagents: dict[str, AgentDefinition] | None = None,
        commands: Sequence[BaseCommand] | None = None,
        hooks: AgentHooks | None = None,
        session_id: str | None = None,
        toolsets: list[ResourceProvider] | None = None,
    ) -> None:
        """Initialize ClaudeCodeAgent.

        Args:
            name: Agent name
            deps_type: Type of dependencies for the agent
            description: Agent description
            display_name: Display name for UI
            allowed_tools: List of allowed tool names
            disallowed_tools: List of disallowed tool names
            system_prompt: System prompt - string or list (appended to builtin by default)
            include_builtin_system_prompt: If True, the builtin system prompt is included.
            model: Model to use (e.g., "opus", "sonnet", "claude-opus-5-5")
            max_turns: Maximum conversation turns
            max_budget_usd: Maximum budget to consume in dollars
            reasoning_effort: Reasoning effort level (adaptive thinking depth)
            permission_mode: Permission mode ("default", "acceptEdits", "plan", "bypassPermissions")
            mcp_servers: External MCP servers to connect to (internal format, converted at runtime)
            env_vars: Environment variables for the agent process
            add_dir: Additional directories to allow tool access to
            builtin_tools: Available tools from built-in set. Special: "LSP" for code intelligence,
                           "Chrome" for browser control
            fallback_model: Fallback model when default is overloaded
            setting_sources: Setting sources to load ("user", "project", "local")
            use_subscription: Force Claude subscription usage instead of API key
            prompt_suggestions: Predict the next user prompt after each turn
            enable_tool_search: Control MCP tool search (True/False/"auto"/threshold)
            idle_compaction: Compact an idle session shortly before its prompt cache expires
            env: Execution environment
            input_provider: Provider for user input/confirmations
            agent_pool: Agent pool for multi-agent coordination
            enable_logging: Whether to enable logging
            event_configs: Event configuration
            event_handlers: Event handlers for streaming events
            output_type: Type for structured output (uses JSON schema)
            builtin_subagents: Subagent definitions for Claude Code's Agent tool
            commands: Slash commands
            hooks: Lifecycle hooks for intercepting agent behavior
            session_id: Session ID to resume on connect (avoids reconnect overhead)
            toolsets: Resource providers for tools to expose via MCP bridge
        """
        from agentpool.agents.claude_code_agent.hook_manager import ClaudeCodeHookManager
        from agentpool.agents.sys_prompts import SystemPrompts
        from agentpool.mcp_server.tool_bridge import ToolManagerBridge
        from agentpool.storage import StorageManager
        from agentpool_storage.claude_provider import ClaudeStorageProvider

        claude_provider = ClaudeStorageProvider()
        claude_storage = StorageManager(providers=[claude_provider])
        super().__init__(
            name=name or "claude_code",
            description=description,
            deps_type=deps_type,
            display_name=display_name,
            agent_pool=agent_pool,
            enable_logging=enable_logging,
            event_configs=event_configs,
            env=env,
            input_provider=input_provider,
            output_type=output_type or str,  # type: ignore[arg-type]
            event_handlers=event_handlers,
            commands=commands,
            hooks=hooks,
            storage=claude_storage,
        )
        self._subagents = builtin_subagents
        self._allowed_tools = allowed_tools
        self._disallowed_tools = disallowed_tools
        self._include_builtin_system_prompt = include_builtin_system_prompt
        # Initialize SystemPrompts manager
        all_prompts: list[AnyPromptType] = []
        if system_prompt is not None:
            if isinstance(system_prompt, str):
                all_prompts.append(system_prompt)
            else:
                all_prompts.extend(system_prompt)
        prompt_manager = agent_pool.prompt_manager if agent_pool else None
        self.sys_prompts = SystemPrompts(all_prompts, prompt_manager=prompt_manager)
        self._model = model
        self._max_turns = max_turns
        self._max_budget_usd = max_budget_usd
        self._effort: ReasoningEffort | None = reasoning_effort
        self._permission_mode: PermissionMode = permission_mode or "default"
        self._pre_plan_mode: PermissionMode = "default"
        """The mode that approving a plan (ExitPlanMode) returns to."""
        self._external_mcp_servers = list(mcp_servers) if mcp_servers else []
        self._env_vars = env_vars
        self._add_dir = add_dir
        self._builtin_tools = builtin_tools
        self._fallback_model = fallback_model
        self._setting_sources = setting_sources
        self._use_subscription = use_subscription
        self._prompt_suggestions = prompt_suggestions
        self._enable_tool_search = enable_tool_search
        self._idle_compaction = (
            IdleCompaction(
                compact=self._compact_idle,
                is_busy=self._is_cli_busy,
                settings=self._cli_settings,
                env={**os.environ, **(env_vars or {})},
            )
            if idle_compaction
            else None
        )
        self._toolsets = toolsets or []
        # Client state
        self._client: ClaudeSDKClient | None = None
        self._connection_task: asyncio.Task[None] | None = None
        self._idle_watch = IdleWatcher(self)
        """Reads what the CLI sends between runs (see idle_watch)."""
        self._usage_limits: tuple[float, UsageLimits | None] | None = None
        self._steering: RunSteering | None = None
        """Messages steered into the running turn (None outside of runs)."""
        self._open_user_inputs = 0
        """Permission requests and questions waiting for the user."""
        self._server_info: ClaudeCodeServerInfo | None = None
        """What the CLI reported at connect: models, commands, account, fast mode state."""
        self._fast_mode: bool | None = None
        """Fast mode as chosen (``None``: as the CLI's settings have it)."""
        self._context_windows: dict[str, int] = {}
        self._titled_session: str | None = None
        """The session a title was asked for (once per session)."""
        self._title_task: asyncio.Task[None] | None = None
        self._cli_command_names: set[str] = set()
        """Names of the CLI's commands in the command store."""
        """Context window per concrete model ID (``claude-opus-5-5``), as the CLI reported it."""
        """(monotonic time of the last get_usage, limits) cache."""
        self._sdk_session_id: str | None = session_id
        # ToolBridge state for exposing toolsets via MCP
        self._tool_bridge = ToolManagerBridge(node=self, injection_manager=self._injection_manager)
        self._mcp_servers: dict[str, McpServerConfig] = {}  # Claude SDK MCP server configs
        # Claude storage provider is available via self.storage
        self._hook_manager = ClaudeCodeHookManager(
            agent_name=self.name,
            agent_hooks=self.hooks,
            injection_manager=self._injection_manager,
            set_mode=self._set_mode,
            env=self.env,
        )

    @classmethod
    def from_config(
        cls,
        config: ClaudeCodeAgentConfig,
        *,
        event_handlers: Sequence[AnyEventHandlerType] | None = None,
        input_provider: InputProvider | None = None,
        agent_pool: AgentPool[Any] | None = None,
        deps_type: type[TDeps] | None = None,
    ) -> Self:
        """Create a ClaudeCodeAgent from a config object.

        All config values are extracted here and passed to the constructor.
        """
        from agentpool.models.manifest import AgentsManifest
        from agentpool.utils.result_utils import to_type

        # Get manifest from pool or create empty one
        manifest = agent_pool.manifest if agent_pool is not None else AgentsManifest()
        # Resolve output type from config
        resolved_output_type = to_type(t, manifest.responses) if (t := config.output_type) else None
        # Merge config-level handlers with provided handlers
        config_handlers = config.get_event_handlers()
        merged_handlers: list[AnyEventHandlerType] = [*config_handlers, *(event_handlers or [])]
        return cls(
            # Identity
            name=config.name,
            description=config.description,
            deps_type=deps_type,
            display_name=config.display_name,
            # Claude Code settings
            allowed_tools=config.allowed_tools,
            disallowed_tools=config.disallowed_tools,
            system_prompt=config.system_prompt,
            env=config.get_execution_environment(),
            include_builtin_system_prompt=config.include_builtin_system_prompt,
            model=config.model,
            max_turns=config.max_turns,
            max_budget_usd=config.max_budget_usd,
            reasoning_effort=config.reasoning_effort,
            permission_mode=config.permission_mode,
            mcp_servers=config.get_mcp_servers(),
            env_vars=config.env_vars,
            add_dir=config.add_dir,
            builtin_subagents=config.get_subagent_configs(),
            builtin_tools=config.builtin_tools,
            fallback_model=config.fallback_model,
            setting_sources=config.setting_sources,
            use_subscription=config.use_subscription,
            prompt_suggestions=config.prompt_suggestions,
            enable_tool_search=config.enable_tool_search,
            idle_compaction=config.idle_compaction,
            # Toolsets
            toolsets=config.get_tool_providers() if config.tools else [],
            # Runtime
            event_configs=list(config.triggers),
            event_handlers=merged_handlers or None,
            input_provider=input_provider,
            agent_pool=agent_pool,
            output_type=resolved_output_type,  # type: ignore[arg-type]  # ty:ignore[invalid-argument-type]
            hooks=config.hooks.get_agent_hooks(),
        )

    async def _setup_toolsets(self) -> None:
        """Initialize toolsets from config and create bridge if needed.

        Creates providers from toolset configs, adds them to the tool manager,
        and starts an MCP bridge to expose them to Claude Code via the SDK's
        native MCP support. Also converts external MCP servers to SDK format.
        """
        from clawd_code_sdk.models import McpHttpServerConfig

        # Convert external MCP servers to SDK format first
        if self._external_mcp_servers:
            external_configs = convert_mcp_servers_to_sdk_format(self._external_mcp_servers)
            self._mcp_servers.update(external_configs)
            self.log.info("External MCP servers configured", server_count=len(external_configs))

        if not self._toolsets:
            return
        # Add toolset providers to tool manager
        for provider in self._toolsets:
            self.tools.add_provider(provider)
        await self._tool_bridge.start()
        # Get Claude SDK-compatible MCP config and merge into our servers dict
        if self._tool_bridge._actual_port is None:
            raise RuntimeError("Bridge not started - call start() first")

        # Use HTTP transport to preserve _meta field with claudecode/toolUseId
        # SDK transport drops _meta in Claude Agent SDK's query.py
        # Bridge tools are the agent's own core tools: never defer them behind
        # tool search (whose refresh path is also where Claude Code's
        # list_changed handling is least reliable).
        cfg = McpHttpServerConfig(type="http", url=self._tool_bridge.url, alwaysLoad=True)
        mcp_config = {self._tool_bridge.resolved_server_name: cfg}
        self._mcp_servers.update(mcp_config)
        self.log.info("Toolsets initialized", toolset_count=len(self._toolsets))

    @property
    def model_name(self) -> str | None:
        """Get the requested model name."""
        return self._model

    async def get_mcp_server_info(self) -> dict[str, MCPServerStatus]:
        """Get information about configured MCP servers."""
        result: dict[str, MCPServerStatus] = {}
        # Try live status from connected client
        if self._client:
            try:
                await self.ensure_initialized()
                mcp_servers = await self._client.get_mcp_status()
            except Exception:  # noqa: BLE001
                pass
            else:
                return {s.name: to_mcp_server_status(s) for s in mcp_servers}
        # Fallback: report from config
        for name, config in self._mcp_servers.items():
            result[name] = MCPServerStatus(name=name, status="connected", server_type=config.type)
        return result

    def _get_client(
        self,
        *,
        system_prompt: str | None = None,
        fork_session: bool = False,
    ) -> ClaudeSDKClient:
        """Build the SDK client with options from the runtime state.

        Args:
            system_prompt: Pre-formatted system prompt from SystemPrompts manager
            fork_session: Whether to fork the session
        """
        from clawd_code_sdk import ClaudeAgentOptions, ClaudeSDKClient
        from clawd_code_sdk.models import NewSession, ResumeSession

        # Check builtin_tools for special tools that need extra handling
        builtin_tools = self._builtin_tools or []
        # Build environment variables
        env = dict(self._env_vars or {})
        env["CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK"] = "1"
        env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
        if "LSP" in builtin_tools:
            env["ENABLE_LSP_TOOL"] = "1"
        if self._use_subscription:  # Force subscription usage by clearing API key
            env["ANTHROPIC_API_KEY"] = ""
        session = (
            ResumeSession(session_id=self._sdk_session_id, fork=fork_session)
            if self._sdk_session_id
            else NewSession()
        )
        opts = ClaudeAgentOptions(
            cwd=self.env.cwd,
            allowed_tools=self._allowed_tools or [],
            disallowed_tools=self._disallowed_tools,
            system_prompt=system_prompt,
            include_builtin_system_prompt=self._include_builtin_system_prompt,
            model=self._model,
            max_turns=self._max_turns,
            max_budget_usd=self._max_budget_usd,
            effort=self._effort,
            permission_mode=self._permission_mode,
            env=env,
            agents=self._subagents,
            add_dirs=self._add_dir or [],
            tools=self._builtin_tools,
            fallback_model=self._fallback_model,
            prompt_suggestions=self._prompt_suggestions,
            enable_tool_search=self._enable_tool_search,
            on_permission=self._on_permission,
            on_user_question=self._on_user_question,
            on_elicitation=self._on_elicitation,
            output_schema=self._output_type if self._output_type is not str else None,
            mcp_servers=self._mcp_servers or {},
            hooks=self._hook_manager.build_hooks(),
            setting_sources=self._setting_sources,
            chrome="Chrome" in builtin_tools,
            session=session,
            stderr=lambda line: logger.debug("claude_cli_stderr", output=line),
            allow_dangerously_skip_permissions=True,
        )
        return ClaudeSDKClient(opts)

    async def _on_permission(
        self,
        tool_name: str,
        input_data: ToolInput | dict[str, Any],
        context: ToolPermissionContext,
    ) -> PermissionResult:
        """Handle the CLI's permission requests (what its permission mode leaves open)."""
        from clawd_code_sdk import PermissionResultAllow, PermissionResultDeny
        from clawd_code_sdk.models.permissions import ModePermissionUpdate

        input_dict = cast(dict[str, Any], input_data)
        tc_id = context.tool_use_id
        display_name = _strip_mcp_prefix(tool_name)
        self.log.debug("Permission request", tool_name=display_name, tool_call_id=tc_id)
        if self._tool_bridge._current_context is None:
            raise RuntimeError("Permission callback invoked outside of an active run")
        ctx = replace(
            self._tool_bridge._current_context,
            tool_call_id=tc_id,
            tool_input=input_dict,
            tool_name=display_name,
        )
        # The CLI applies the permission mode itself and only asks for what the mode
        # leaves open (in plan mode: ExitPlanMode), so most requests go to the user.
        match self._permission_mode:
            case "bypassPermissions":
                return PermissionResultAllow()
            case "acceptEdits" if display_name in _EDIT_TOOLS:
                return PermissionResultAllow()
            case "default" | "acceptEdits" | "plan" | "delegate" | "dontAsk" | "auto":
                pass
            case _ as unreachable:
                assert_never(unreachable)
        with self._awaiting_user():
            result = await ctx.get_input_provider().get_tool_confirmation(context=ctx)
        permission = confirmation_result_to_native(result)
        if isinstance(permission, PermissionResultDeny):
            return permission
        # Plan mode tools change the CLI's mode once allowed: follow it.
        match display_name:
            case "ExitPlanMode":
                mode = self._pre_plan_mode
                update = ModePermissionUpdate(type="setMode", mode=mode, destination="session")
                permission = PermissionResultAllow(updated_permissions=[update])
                await self._follow_permission_mode(mode)
            case "EnterPlanMode":
                await self._follow_permission_mode("plan")
        return permission

    def _remember_permission_mode(self, mode: PermissionMode) -> None:
        """Store ``mode``, and the mode to return to when it enters plan mode."""
        if mode == "plan" and self._permission_mode != "plan":
            self._pre_plan_mode = self._permission_mode
        self._permission_mode = mode

    async def _follow_permission_mode(self, mode: PermissionMode) -> None:
        """Adopt a mode the CLI switched to by itself and announce it."""
        self._remember_permission_mode(mode)
        await self.update_state(config_id="mode", value_id=mode)

    async def _on_user_question(
        self,
        input_data: AskUserQuestionInput,
        context: ToolPermissionContext,
    ) -> PermissionResult:
        """Handle AskUserQuestion elicitation requests."""
        from agentpool.agents.claude_code_agent.elicitation import handle_clarifying_questions

        ctx = self._tool_bridge._current_context
        if ctx is None:
            raise RuntimeError("User question callback invoked outside of an active run")
        with self._awaiting_user():
            return await handle_clarifying_questions(
                agent_ctx=ctx, input_data=input_data, context=context
            )

    async def _on_elicitation(self, request: SDKControlElicitationRequest) -> ElicitResult:
        """Handle MCP elicitation requests."""
        from mcp.types import ElicitResult, ErrorData

        if self._tool_bridge._current_context is None:
            raise RuntimeError("Elicitation callback invoked outside of an active run")
        input_provider = self._tool_bridge._current_context.get_input_provider()
        params = request.to_mcp()
        with self._awaiting_user():
            response = await input_provider.get_elicitation(params=params)
        match response:
            case ElicitResult() as result:
                return result
            case ErrorData():
                return ElicitResult(action="decline")
            case _ as unreachable_:
                assert_never(unreachable_)

    @contextlib.contextmanager
    def _awaiting_user(self) -> Generator[None]:
        """Count a request that waits for the user (steering then waits for it too)."""
        self._open_user_inputs += 1
        try:
            yield
        finally:
            self._open_user_inputs -= 1

    async def steer(self, message: str, *, interrupt: bool = False) -> None:
        """Send the user's message into the running turn (Claude Code's own steering).

        By default the CLI takes it in at the next step boundary (priority ``next``): after
        the running tool call, within the same cycle. With ``interrupt`` it ends the
        generation in progress (``now``), unless a permission request or question waits
        for the user. Outside of a run, the base behavior applies.
        """
        if (steering := self._steering) is None or steering.closed:
            await super().steer(message, interrupt=interrupt)
            return
        cut_in = interrupt and not self._open_user_inputs
        await steering.send(message, priority="now" if cut_in else "next")

    async def __aenter__(self) -> Self:
        """Connect to Claude Code with deferred client connection."""
        await super().__aenter__()
        await self._setup_toolsets()  # Setup toolsets before building opts (they add MCP servers)
        formatted_prompt = await self.sys_prompts.format_system_prompt(self)
        self._client = self._get_client(system_prompt=formatted_prompt)
        # Start connection in background task to reduce first-prompt latency
        # The task owns the anyio context, we just await it when needed
        self._connection_task = asyncio.create_task(self._do_connect())
        return self

    async def _do_connect(self) -> None:
        """Actually connect the client. Runs in background task."""
        if not self._client:
            raise AgentNotInitializedError

        try:
            await self._client.connect()
            server_info = await self._client.get_server_info()
            assert server_info, "No server info returned (streaming mode should always provide it)"
            self._server_info = server_info
            if self._fast_mode is None:
                self._fast_mode = server_info.fast_mode_state not in (None, "off")
            elif self._fast_mode:  # Turned on before the CLI ran
                await self._apply_fast_mode(enabled=True)
            await self._populate_commands()
            await self._refresh_context_window()
            self._idle_watch.start(self._client)
            self.log.info("Claude Code client connected")
            # The options so far came from static lists: announce the CLI's own.
            if (model := self._current_model_info()) is not None:
                await self.update_state(config_id="model", value_id=self._model or model.value)
        except Exception:
            self.log.exception("Failed to connect Claude Code client")
            raise

    async def reconnect(self, *, resume_session: bool = True) -> None:
        """Reconnect to Claude Code SDK, optionally resuming the current session.

        This is useful for recovering from hangs or connection issues without
        losing conversation history.

        Args:
            resume_session: If True, attempt to resume the current session using
                the stored session ID. If False, start a fresh session.
        """
        # Recreate client with new options
        session_to_resume = self._sdk_session_id if resume_session else None
        self.log.info("Reconnecting CC agent", resume=resume_session, session_id=session_to_resume)
        # Cancel existing connection if active
        if self._connection_task and not self._connection_task.done():
            self._connection_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._connection_task
        self._connection_task = None
        await self._idle_watch.stop()
        if self._idle_compaction is not None:  # A new CLI process: nothing to compact yet
            self._idle_compaction.reset()
        # # Clean up tool bridge
        # if self._tool_bridge._mcp is not None:
        #     await self._tool_bridge.stop()
        # self._mcp_servers.clear()
        if self._client:
            try:
                await self._client.disconnect()
                self.log.info("Disconnected existing Claude Code client")
            except Exception:
                self.log.exception("Error disconnecting Claude Code client during reconnect")
            self._client = None

        # Clear session ID if not resuming (before _get_client which uses it)
        if not resume_session:
            self._sdk_session_id = None

        formatted_prompt = await self.sys_prompts.format_system_prompt(self)
        # _get_client includes resume=self._sdk_session_id automatically
        if session_to_resume:
            self.log.info("Attempting to resume session", session=session_to_resume)
        self._client = self._get_client(system_prompt=formatted_prompt)
        try:  # Reconnect in background
            self._connection_task = asyncio.create_task(self._do_connect())
            await self._connection_task
            mode = "resumed" if session_to_resume else "fresh"
            self.log.info("Claude Code agent reconnected successfully", session_mode=mode)
        except Exception:
            self.log.exception("Error reconnecting Claude Code agent")
            raise

    async def ensure_initialized(self) -> None:
        """Wait for background connection task to complete."""
        if self._connection_task and self._connection_task is not asyncio.current_task():
            await self._connection_task
            self._connection_task = None

    async def _title_session(
        self, client: ClaudeSDKClient, session_id: str, prompts: Sequence[UserContent]
    ) -> None:
        """Title a new session with the CLI's title model (the prompt itself if it is short).

        Runs beside the session's first turn; the title reaches clients through the
        storage's ``metadata_generated`` signal.
        """
        from clawd_code_sdk import ClaudeSDKError

        storage = self.storage
        assert storage is not None
        if await storage.get_session_title(session_id):
            return
        text = "\n".join(
            item if isinstance(item, str) else item.content
            for item in prompts
            if isinstance(item, str | TextContent)
        ).strip()
        if not text:
            return
        try:
            title = await client.generate_session_title(f"User: {text}"[-TITLE_CONTEXT_CHARS:])
        except ClaudeSDKError as e:
            self.log.debug("Session title unavailable", error=str(e))
            title = None
        if title is None:  # Too short to title: like the storage, use the prompt itself
            collapsed = " ".join(text.split())
            cut = f"{collapsed[: MAX_PROMPT_TITLE - 1]}…"
            title = collapsed if len(collapsed) <= MAX_PROMPT_TITLE else cut
        await storage.apply_session_title(session_id, title)

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Disconnect from Claude Code."""
        if self._idle_compaction is not None:
            await self._idle_compaction.close()
        if self._title_task and not self._title_task.done():
            self._title_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._title_task
        self._title_task = None
        # Cancel connection task if still running
        if self._connection_task and not self._connection_task.done():
            self._connection_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._connection_task
        self._connection_task = None

        # Clean up tool bridge first
        # Only stop bridge if it was started (has _mcp set)
        if self._tool_bridge._mcp is not None:
            await self._tool_bridge.stop()
        self._mcp_servers.clear()
        await self._idle_watch.stop()
        if self._client:
            try:
                await self._client.disconnect()
                self.log.info("Claude Code client disconnected")
            except Exception:  # noqa: BLE001
                self.log.warning("Error disconnecting Claude Code client")
            self._client = None
        await super().__aexit__(exc_type, exc_val, exc_tb)

    async def _populate_commands(self) -> None:
        """Populate the command store with slash commands from Claude Code.

        Fetches available commands from the connected Claude Code server
        and registers them as slashed Commands. Should be called after
        connection is established.

        Commands that do not work through the SDK are filtered out
        (``UNSUPPORTED_COMMANDS``, e.g. login, logout, todos).
        """
        await self.ensure_initialized()
        server_info = self._server_info
        assert server_info, "No server info after ensure_initialized"
        self._register_cli_commands(server_info.commands)

    def is_backend_command(self, name: str) -> bool:
        """The CLI's commands run as turns: ``/name args`` goes to the CLI as the prompt."""
        return name in self._cli_command_names

    async def _follow_sdk_session(self, sdk_session_id: str) -> None:
        """Adopt the CLI's current session ID: ``/clear`` moves to a new session.

        A reconnect resumes this ID, so a stale one would bring back a cleared conversation.
        """
        if sdk_session_id == self._sdk_session_id:
            return
        self._sdk_session_id = sdk_session_id
        if self._idle_compaction is not None:  # Another conversation: its cache is not ours
            self._idle_compaction.reset()
        await self._record_sdk_session_id()

    @property
    def _session_store(self) -> StorageManager | None:
        """Where this agent's sessions are recorded under their pool IDs.

        That is the pool's storage (ACP and the other servers keep their sessions there),
        not `self.storage`: that one is Claude's own history, keyed by the CLI's session
        IDs, and cannot record a pool session.
        """
        # `is not None`: a pool is a registry, falsy while it holds no nodes
        return self.agent_pool.storage if self.agent_pool is not None else self.storage

    async def _record_sdk_session_id(self) -> None:
        """Link the current pool session to the CLI session it runs on.

        Loading the pool session later (ACP `session/load`) resumes the CLI session
        through this link: `claude --resume` takes only the CLI's own ID.
        """
        store = self._session_store
        if store is not None and self.session_id and self._sdk_session_id:
            await store.update_sdk_session_id(self.session_id, self._sdk_session_id)

    def _register_cli_commands(self, cli_commands: Sequence[ClaudeCodeCommandInfo]) -> None:
        """Offer the CLI's commands, replacing the ones it offered before.

        The CLI reports them at connect and again when they change (``commands_changed``),
        e.g. once an MCP server with prompts has connected.
        """
        commands = [
            create_claude_code_command(cmd_info)
            for cmd_info in cli_commands
            if cmd_info.name and cmd_info.name not in UNSUPPORTED_COMMANDS
        ]
        names = {command.name for command in commands}
        for name in self._cli_command_names - names:
            self._command_store.unregister_command(name)
        for command in commands:
            self._command_store.register_command(command, replace=True)
        self._cli_command_names = names
        self.log.info("Registered Claude Code commands", command_count=len(commands))

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
    ) -> AsyncIterator[RichAgentStreamEvent[TResult]]:
        from clawd_code_sdk import AssistantMessage, ResultSuccessMessage, UserMessage

        from agentpool.agents.claude_code_agent.stream_adapter import ClaudeCodeStreamedResponse

        await self.ensure_initialized()
        # Resolve input provider: explicit parameter overrides agent default
        effective_input_provider = input_provider or self._input_provider
        run_context = self.get_context(data=deps, input_provider=effective_input_provider)
        if not self._client:
            raise AgentNotInitializedError
        # Take over the CLI's message stream (after a turn it started on its own)
        await self._idle_watch.stop()
        if self._idle_compaction is not None:
            self._idle_compaction.disarm()
        run_id = str(uuid.uuid4())
        assert self.session_id is not None  # Initialized by BaseAgent.run_stream()
        yield RunStartedEvent(session_id=self.session_id, run_id=run_id, agent_name=self.name)

        # Handle ephemeral execution (fork session if store_history=False)
        fork_client = None
        client = self._client
        if not store_history and self._sdk_session_id:
            # Create fork client that shares parent's context but has separate session ID
            # See: src/agentpool/agents/claude_code_agent/FORKING.md
            fork_client = self._get_client(fork_session=True)
            await fork_client.connect()
            client = fork_client

        reconstructor = MessageReconstructor(initial_prompts=prompts)
        claude_prompts = [*to_prompt_input(prompts)]
        prompt_uuid = str(uuid.uuid4())
        steering = RunSteering(
            client=client,
            reconstructor=reconstructor,
            unanswered={prompt_uuid},
            taken_in={prompt_uuid},
        )
        try:
            await client.query(*claude_prompts, uuid=prompt_uuid)
            self._steering = steering
            # Up to the result that answers the prompt (background work may run on)
            stream = run_messages(client, steering)
            # Capture SDK session ID from init message
            first_msg = await anext(stream)
            assert not isinstance(first_msg, AssistantMessage | UserMessage), (
                f"invalid message type {type(first_msg)}"
            )
            self._sdk_session_id = first_msg.session_id
            await self._record_sdk_session_id()
            if (
                self.storage
                and self.session_id
                and store_history
                and self._titled_session != self.session_id
            ):
                self._titled_session = self.session_id
                self._title_task = asyncio.create_task(
                    self._title_session(client, self.session_id, prompts),
                    name=f"claude-code-title-{self.session_id[:8]}",
                )
            adapter = ClaudeCodeStreamedResponse(
                provider_name="claude_code",
                stream=stream,
                tool_metadata=self._tool_bridge.tool_metadata,
                agent_name=self.name,
                session_id=self.session_id,
                on_rate_limit=self.record_rate_limit,
                on_command_lifecycle=steering.on_lifecycle,
                on_commands_changed=self._register_cli_commands,
                on_request=self._note_request,
            )
            async with (
                self._tool_bridge.set_run_context(run_context, prompt=prompts),
                merge_queue_into_iterator(adapter, self._event_queue) as merged_events,
            ):
                async for event in merged_events:
                    reconstructor.observe(event)
                    yield event

        except asyncio.CancelledError:
            self.log.info("Stream cancelled via CancelledError")
            msg_metadata: SimpleJsonType = {}
            if self._sdk_session_id:
                msg_metadata["sdk_session_id"] = self._sdk_session_id
            reconstructor.flush()
            resolved = adapter.model_name or self.model_name  # pyright: ignore[reportPossiblyUnboundVariable]
            response_msg = ChatMessage[TResult](
                content=reconstructor.text_content,  # type: ignore[arg-type]  # ty:ignore[invalid-argument-type]
                role="assistant",
                name=self.name,
                message_id=message_id or str(uuid.uuid4()),
                session_id=self.session_id,
                parent_id=user_msg.message_id,
                model_name=resolved,
                messages=reconstructor.model_messages,
                finish_reason="stop",
                metadata=msg_metadata,
            )
            yield StreamCompleteEvent(message=response_msg)
            return

        except Exception as e:
            yield RunErrorEvent(message=str(e), run_id=run_id, agent_name=self.name)
            raise

        finally:
            self._steering = None
            if fork_client:
                try:
                    await fork_client.disconnect()
                except Exception as e:  # noqa: BLE001
                    self.log.warning("Error disconnecting fork client", error=e)
            if self._client is not None:
                self._idle_watch.start(self._client)
                if self._client.session_state == "idle":  # Else the idle watcher reports it
                    self._on_cli_idle()

        reconstructor.flush()

        # Determine final content - use structured output if available
        result_message = adapter._result_message
        content = reconstructor.text_content
        final_content: TResult
        if (
            self._output_type is not str
            and isinstance(result_message, ResultSuccessMessage)
            and result_message.structured_output
        ):
            _adapter = TypeAdapter(self._output_type)
            final_content = _adapter.validate_python(result_message.structured_output)
        else:
            final_content = content  # type: ignore[assignment]  # ty:ignore[invalid-assignment]

        # Build cost_info and usage from client per-query tracking.
        # result_message.total_cost_usd is cumulative across the session,
        # but client.query_cost is the per-turn delta computed by the SDK.
        # result_message.usage is last-API-call-only; client.query_usage
        # accumulates all API calls in the turn.
        cost_info: TokenCost | None = None
        run_usage: RunUsage = RunUsage()
        stop_reason: StopReason | None = "end_turn"
        if result_message:
            await self._follow_sdk_session(result_message.session_id)
            self._learn_context_windows(result_message)
            run_usage = to_run_usage(client.query_usage)
            total_cost = Decimal(str(client.query_cost))
            cost_info = TokenCost(total_cost=total_cost)
            stop_reason = result_message.stop_reason
        # Build metadata with SDK session ID
        final_metadata: SimpleJsonType = {}
        if self._sdk_session_id:
            final_metadata["sdk_session_id"] = self._sdk_session_id
        # The CLI stops fast mode by itself (e.g. at a model switch): follow it.
        if result_message and (fast_state := result_message.fast_mode_state) is not None:
            await self._follow_fast_mode(enabled=fast_state != "off")
        turn_error = to_turn_error(result_message) if result_message else None
        finish_reason: FinishReason
        if self._cancelled:
            finish_reason = "stop"
        elif turn_error is not None:
            finish_reason = "error"
            final_metadata["error_code"] = turn_error.code
            yield RunErrorEvent(
                message=turn_error.message,
                code=turn_error.code,
                run_id=run_id,
                agent_name=self.name,
            )
        else:
            finish_reason = to_finish_reason(stop_reason) if stop_reason else "stop"
        chat_message = ChatMessage[TResult](
            content=final_content,
            role="assistant",
            name=self.name,
            message_id=message_id or str(uuid.uuid4()),
            session_id=self.session_id,
            parent_id=user_msg.message_id,
            model_name=adapter.model_name or self.model_name,
            messages=reconstructor.model_messages,
            cost_info=cost_info,
            usage=run_usage,
            response_time=result_message.duration_ms / 1000 if result_message else None,
            finish_reason=finish_reason,
            metadata=final_metadata,
        )

        # Emit stream complete - post-processing handled by base class
        yield StreamCompleteEvent[TResult](message=chat_message)

    async def _interrupt(self) -> None:
        """Call Claude SDK's native interrupt() to stop the query."""
        if self._client:
            try:
                await self._client.interrupt()
                self.log.info("Claude Code client interrupted")
            except Exception:
                self.log.exception("Failed to interrupt Claude Code client")

    async def set_model(self, model: AnthropicMaxModelName | str) -> None:
        """Set the model for future requests."""
        await self._set_mode(model, "model")

    async def set_effort(self, effort: ReasoningEffort) -> None:
        """Set reasoning effort level (applied to the running CLI, no reconnect).

        Args:
            effort: Reasoning effort level
        """
        await self._set_mode(effort, "thought_level")

    async def set_permission_mode(self, mode: PermissionMode) -> None:
        """Set permission mode."""
        await self._set_mode(mode, "mode")

    async def get_available_models(self) -> list[ModelInfo]:
        """The models the CLI offers this account (a static list until it is connected)."""
        from agentpool.agents.claude_code_agent.converters import to_model_info
        from agentpool.agents.claude_code_agent.static_info import MODELS

        if self._server_info is None:
            return MODELS
        return [
            to_model_info(model, context_window=self._context_window_of(model))
            for model in self._server_info.models
        ]

    def _context_window_of(self, model: ClaudeCodeModelInfo) -> int:
        """The window the CLI reported for ``model``, else the default."""
        from agentpool.agents.claude_code_agent.converters import DEFAULT_CONTEXT_WINDOW

        for model_id in (model.resolved_model, model.value):
            if model_id and (window := self._context_windows.get(model_id)):
                return window
        return DEFAULT_CONTEXT_WINDOW

    def _learn_context_windows(self, result: ResultMessage) -> None:
        """Remember the context windows a result reports, per model it used."""
        for model_id, usage in result.model_usage.items():
            if usage.context_window > 0:
                self._context_windows[model_id] = usage.context_window

    async def _refresh_context_window(self) -> None:
        """Ask the CLI for the current model's window, unless a result reported it already.

        Lets the first prompt show the right size; results stay authoritative.
        """
        from clawd_code_sdk import ClaudeSDKError

        assert self._client
        try:
            usage = await self._client.get_context_usage()
        except (ClaudeSDKError, ValidationError) as e:
            self.log.debug("Context usage unavailable", error=str(e))
            return
        if usage.raw_max_tokens > 0:
            self._context_windows.setdefault(usage.model, usage.raw_max_tokens)

    def _current_model_info(self) -> ClaudeCodeModelInfo | None:
        """The CLI's entry for the selected model (None before connecting or if unlisted)."""
        if (server_info := self._server_info) is None:
            return None
        model = self._model or server_info.current_model or "default"
        return next(
            (info for info in server_info.models if model in (info.value, info.resolved_model)),
            None,
        )

    def _effort_levels(self) -> list[ReasoningEffort] | None:
        """Effort levels of the selected model (None: not known before connecting)."""
        if (model := self._current_model_info()) is None:
            return None
        return list(model.supported_effort_levels or []) if model.supports_effort else []

    async def _apply_effort(self, effort: ReasoningEffort) -> None:
        """Set the effort of the running CLI (flag settings: no restart)."""
        from clawd_code_sdk.models.settings import ClaudeCodeSettings

        self._effort = effort
        if self._client:
            await self.ensure_initialized()
            await self._client.apply_flag_settings(ClaudeCodeSettings(effort_level=effort))

    async def _follow_fast_mode(self, *, enabled: bool) -> None:
        """Adopt the fast mode state the CLI reports and announce a change."""
        if self._fast_mode != enabled:
            self._fast_mode = enabled
            await self.update_state(config_id="fast", value_id="on" if enabled else "off")

    async def _apply_fast_mode(self, *, enabled: bool) -> None:
        """Turn fast mode on or off in the running CLI.

        Turning it on sends ``fastMode: false`` first: the CLI ignores ``true`` while its
        flag already holds ``true``, which it keeps after it stopped serving fast mode
        (e.g. at a model switch).
        """
        from clawd_code_sdk.models.settings import ClaudeCodeSettings

        assert self._client
        if enabled:
            await self._client.apply_flag_settings(ClaudeCodeSettings(fast_mode=False))
        await self._client.apply_flag_settings(ClaudeCodeSettings(fast_mode=enabled))

    async def get_auth_status(self) -> AuthStatus:
        """Get the Claude account the CLI is using."""
        # Waits for the deferred CLI connection, so the first call can be slow.
        await self.ensure_initialized()
        server_info = await self._client.get_server_info() if self._client else None
        return to_auth_status(server_info.account if server_info else None)

    async def get_usage_limits(self) -> UsageLimits | None:
        """Get the Claude plan's rate-limit windows (None for API keys, Bedrock, Vertex).

        Read with ``get_usage`` at most every ``USAGE_LIMITS_TTL`` seconds; in between,
        the ``rate_limit_event`` messages of each turn keep the windows current.
        """
        from agentpool.agents.claude_code_agent.usage_limits import to_usage_limits

        cached = self._usage_limits
        if cached is not None and time.monotonic() - cached[0] < USAGE_LIMITS_TTL:
            return cached[1]
        await self.ensure_initialized()
        if self._client is None:
            return None
        limits = to_usage_limits(await self._client.get_usage())
        self._usage_limits = (time.monotonic(), limits)
        return limits

    def record_rate_limit(self, info: RateLimitInfo) -> None:
        """Update the cached usage limits from a ``rate_limit_event`` of the stream."""
        from agentpool.agents.claude_code_agent.usage_limits import apply_rate_limit_event

        if self._idle_compaction is not None:
            self._idle_compaction.note_rate_limit(info.status)
        if self._usage_limits is not None and (limits := self._usage_limits[1]) is not None:
            self._usage_limits = (self._usage_limits[0], apply_rate_limit_event(limits, info))

    def _note_request(self, request_id: str, usage: BetaUsage) -> None:
        """A main-conversation request started (in a run or a turn of the CLI's own)."""
        if self._idle_compaction is not None:
            self._idle_compaction.note_request(request_id, usage)

    def _on_cli_idle(self) -> None:
        """The CLI went idle: an idle compaction may become due."""
        if self._idle_compaction is not None and self._steering is None:
            self._idle_compaction.on_idle()

    def _is_cli_busy(self) -> bool:
        return self._steering is not None or self._idle_watch.busy

    async def _cli_settings(self) -> Mapping[str, Any]:
        assert self._client
        return (await self._client.get_settings()).effective

    async def _compact_idle(self) -> None:
        """Send ``/compact``; the idle watcher reports it as a turn of the CLI's own."""
        assert self._client
        self._idle_watch.expect_turn()  # A run starting meanwhile waits for it
        await self._client.query("/compact")

    @property
    def config_options(self) -> Sequence[ConfigOption[Self]]:
        """Permission mode, model, reasoning effort and fast mode."""
        from agentpool.agents.claude_code_agent.config_options import CONFIG_OPTIONS

        return CONFIG_OPTIONS

    async def list_sessions(
        self,
        *,
        cwd: str | None = None,
        limit: int | None = None,
    ) -> list[SessionData]:
        """List sessions from Claude storage (~/.claude/projects/)."""
        storage = self.storage
        if not storage:
            return []
        result: list[SessionData] = []
        default_cwd = str(self.env.cwd or Path.cwd())
        for session_id in await storage.list_session_ids(agent_name=self.name):
            if session_data := await storage.load_session(session_id):
                if not session_data.cwd:
                    session_data = session_data.model_copy(update={"cwd": default_cwd})
                if cwd is not None and session_data.cwd != cwd:
                    continue
                result.append(session_data)
                if limit is not None and len(result) >= limit:
                    break
        result.sort(key=lambda s: s.updated_at or "", reverse=True)
        return result

    async def start_new_session(self) -> None:
        """Start a fresh Claude Code session instead of resuming the current one."""
        await super().start_new_session()
        if self._client is None:
            self._sdk_session_id = None
        else:
            await self.reconnect(resume_session=False)

    async def load_session(self, session_id: str) -> SessionData | None:
        """Load a session and resume its CLI session (reconnects).

        `session_id` is a pool session ID (as ACP `session/load` passes it), linked to
        its CLI session by `_record_sdk_session_id`, or the ID of a session listed from
        Claude's history, which is the CLI's own. History is read from Claude's storage
        under the CLI ID.
        """
        storage = self.storage
        if not storage:
            return None
        store = self._session_store
        pool_session = await store.load_session(session_id) if store is not None else None
        # `claude --resume` takes only a CLI session ID (or title), never the pool's
        cli_session_id = (
            pool_session.sdk_session_id
            if pool_session is not None and pool_session.sdk_session_id
            else session_id
        )
        try:
            messages = await storage.get_session_messages(session_id=cli_session_id)
        except Exception:
            self.log.exception("Failed to load Claude session", session_id=cli_session_id)
            return None
        if not messages:
            self.log.warning(
                "No messages found in session",
                session_id=session_id,
                sdk_session_id=cli_session_id,
            )
            return None
        # Restore to conversation history
        self.conversation.chat_messages.clear()
        self.conversation.chat_messages.extend(messages)
        self.log.info("Session loaded", session_id=session_id, message_count=len(messages))
        self._sdk_session_id = cli_session_id
        try:
            await self.reconnect(resume_session=True)
            self.log.info(
                "Reconnected with loaded session",
                session_id=session_id,
                sdk_session_id=cli_session_id,
            )
        except Exception:
            error_msg = "Failed to reconnect with loaded session, continuing with local history"
            self.log.exception(error_msg, session_id=session_id)
        if pool_session is not None:
            return pool_session
        if session_data := await storage.load_session(cli_session_id):
            return session_data
        # Fallback: build from messages
        last_active = messages[-1].timestamp or get_now()
        cwd = str(self.env.cwd or Path.cwd())
        for msg in reversed(messages):
            if (val := msg.metadata.get("cwd")) and isinstance(val, str):
                cwd = val
                break
        return SessionData(
            session_id=session_id,
            agent_name=self.name,
            cwd=cwd,
            created_at=messages[0].timestamp or last_active,
            last_active=last_active,
        )


if __name__ == "__main__":
    import time

    async def main() -> None:
        """Demo: Basic call to Claude Code."""
        async with ClaudeCodeAgent(name="demo", event_handlers=["detailed"]) as agent:
            # print("Response (streaming): ", end="", flush=True)
            # async for _ in agent.run_stream("What files are in the current directory?"):
            #     pass
            await agent.ensure_initialized()
            print(now := time.time())
            sessions = await agent.list_sessions()
            print(time.time() - now)
            print(sessions)

    anyio.run(main)
