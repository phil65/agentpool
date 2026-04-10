"""ClaudeCodeAgent - Native Claude Agent SDK integration."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
import re
from typing import TYPE_CHECKING, Any, ClassVar, Literal, Self, assert_never, cast
import uuid

import anyio
from pydantic import TypeAdapter
from pydantic_ai import RunUsage

from agentpool.agents.base_agent import BaseAgent
from agentpool.agents.claude_code_agent.converters import (
    confirmation_result_to_native,
    convert_mcp_servers_to_sdk_format,
    to_finish_reason,
    to_mcp_server_status,
    to_prompt_input,
    to_run_usage,
    to_thinking_config,
)
from agentpool.agents.claude_code_agent.slash_commands import create_claude_code_command
from agentpool.agents.claude_code_agent.static_info import models_to_category
from agentpool.agents.events import RunErrorEvent, RunStartedEvent, StreamCompleteEvent
from agentpool.agents.events.reconstructor import MessageReconstructor
from agentpool.agents.exceptions import (
    AgentNotInitializedError,
    UnknownCategoryError,
    UnknownModeError,
)
from agentpool.common_types import MCPServerStatus
from agentpool.log import get_logger
from agentpool.messaging import ChatMessage
from agentpool.messaging.messages import TokenCost
from agentpool.sessions.models import SessionData
from agentpool.utils.streams import merge_queue_into_iterator
from agentpool.utils.time_utils import get_now


if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence
    from types import TracebackType

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
    from evented_config import EventConfig
    from exxec import ExecutionEnvironment
    from mcp.types import ElicitResult
    from pydantic_ai import UserContent
    from slashed import BaseCommand
    from tokonomics.model_discovery.model_info import ModelInfo
    from tokonomics.model_names import AnthropicMaxModelName
    from toprompt import AnyPromptType

    from agentpool.agents.events import RichAgentStreamEvent
    from agentpool.agents.modes import ModeCategory
    from agentpool.common_types import AnyEventHandlerType, SimpleJsonType, StrPath
    from agentpool.delegation import AgentPool
    from agentpool.hooks import AgentHooks
    from agentpool.messaging import MessageHistory
    from agentpool.models.claude_code_agents import ClaudeCodeAgentConfig, SettingSource, ToolName
    from agentpool.resource_providers import ResourceProvider
    from agentpool.ui.base import InputProvider
    from agentpool_config.mcp_server import MCPServerConfig

logger = get_logger(__name__)

ThinkingMode = Literal["off", "4k", "8k", "16k", "32k"]

_MCP_TOOL_PATTERN = re.compile(r"^mcp__agentpool-(.+)-tools__(.+)$")
"""Pattern to detect CC-provided tool names ( mcp__agentpool-{agent_name}-tools__{tool_name} )."""

VALID_EFFORTS: set[str] = {"low", "medium", "high", "max"}

# see https://github.com/zed-industries/claude-agent-acp/blob/main/src/acp-agent.ts for a list
UNSUPPORTED_COMMANDS = frozenset({
    # "cost",
    "keybindings-help",
    "login",
    "logout",
    "output-style:new",
    "release-notes",
    "todos",
})


THINKING_MODE_TOKENS: dict[ThinkingMode, int] = {
    "off": 0,
    "4k": 4000,
    "8k": 8000,
    "16k": 16000,
    "32k": 32000,
}
"""Token limit for each thinking mode."""


def _strip_mcp_prefix(tool_name: str) -> str:
    """Strip MCP server prefix from tool names for cleaner UI display.

    Handles dynamic prefixes like mcp__agentpool-{agent_name}-tools__{tool}
    """
    if match := _MCP_TOOL_PATTERN.match(tool_name):
        return match.group(2)  # group(1) is agent name, group(2) is tool name
    return tool_name


class ClaudeCodeAgent[TDeps = None, TResult = str](BaseAgent[TDeps, TResult]):
    """Agent wrapping Claude Agent SDK's ClaudeSDKClient.

    This provides native integration with Claude Code, enabling:
    - Bidirectional streaming for interactive conversations
    - Tool permission handling via can_use_tool callback
    - Full access to Claude Code's capabilities (file ops, terminals, etc.)

    The agent manages:
    - ClaudeSDKClient lifecycle (connect on enter, disconnect on exit)
    - Event conversion from Claude SDK to agentpool events
    - Tool confirmation via input provider
    """

    AGENT_TYPE: ClassVar = "claude"

    def __init__(
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
        max_thinking_tokens: int | Literal["adaptive"] | None = None,
        reasoning_effort: ReasoningEffort | None = None,
        permission_mode: PermissionMode | None = None,
        mcp_servers: Sequence[MCPServerConfig] | None = None,
        env_vars: dict[str, str] | None = None,
        add_dir: list[str] | None = None,
        builtin_tools: list[ToolName | str] | None = None,
        fallback_model: AnthropicMaxModelName | str | None = None,
        setting_sources: list[SettingSource] | None = None,
        use_subscription: bool = False,
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
            model: Model to use (e.g., "claude-sonnet-4-5")
            max_turns: Maximum conversation turns
            max_budget_usd: Maximum budget to consume in dollars
            max_thinking_tokens: Max tokens for extended thinking
            reasoning_effort: Reasoning effort level
            permission_mode: Permission mode ("default", "acceptEdits", "plan", "bypassPermissions")
            mcp_servers: External MCP servers to connect to (internal format, converted at runtime)
            env_vars: Environment variables for the agent process
            add_dir: Additional directories to allow tool access to
            builtin_tools: Available tools from built-in set. Special: "LSP" for code intelligence,
                           "Chrome" for browser control
            fallback_model: Fallback model when default is overloaded
            setting_sources: Setting sources to load ("user", "project", "local")
            use_subscription: Force Claude subscription usage instead of API key
            env: Execution environment
            input_provider: Provider for user input/confirmations
            agent_pool: Agent pool for multi-agent coordination
            enable_logging: Whether to enable logging
            event_configs: Event configuration
            event_handlers: Event handlers for streaming events
            output_type: Type for structured output (uses JSON schema)
            builtin_subagents: builtin Subagents configuration
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
        self._max_thinking_tokens: int | Literal["adaptive"] | None = max_thinking_tokens
        self._effort: ReasoningEffort | None = reasoning_effort
        self._permission_mode: PermissionMode = permission_mode or "default"
        self._thinking_mode: ThinkingMode = "32k"
        self._external_mcp_servers = list(mcp_servers) if mcp_servers else []
        self._env_vars = env_vars
        self._add_dir = add_dir
        self._builtin_tools = builtin_tools
        self._fallback_model = fallback_model
        self._setting_sources = setting_sources
        self._use_subscription = use_subscription
        self._toolsets = toolsets or []
        # Client state
        self._client: ClaudeSDKClient | None = None
        self._connection_task: asyncio.Task[None] | None = None
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
            max_thinking_tokens=config.max_thinking_tokens,
            permission_mode=config.permission_mode,
            mcp_servers=config.get_mcp_servers(),
            env_vars=config.env_vars,
            add_dir=config.add_dir,
            builtin_subagents=config.get_subagent_configs(),
            builtin_tools=config.builtin_tools,
            fallback_model=config.fallback_model,
            setting_sources=config.setting_sources,
            use_subscription=config.use_subscription,
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
        cfg = McpHttpServerConfig(type="http", url=self._tool_bridge.url)
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
        """Build ClaudeAgentOptions from runtime state.

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
            thinking=to_thinking_config(self._max_thinking_tokens),
            effort=self._effort,
            permission_mode=self._permission_mode,
            env=env,
            agents=self._subagents,
            add_dirs=self._add_dir or [],
            tools=self._builtin_tools,
            fallback_model=self._fallback_model,
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
        """Handle tool permission requests."""
        from clawd_code_sdk import PermissionResultAllow, PermissionResultDeny

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
        input_provider = ctx.get_input_provider()
        # Auto-grant if bypassPermissions mode is active
        match self._permission_mode:
            case "bypassPermissions":
                return PermissionResultAllow()
            case "plan":
                return PermissionResultDeny(message="Plan mode active - tool execution disabled")
            case "acceptEdits":
                # Auto-allow file editing tools
                if display_name.lower() in ("edit", "write", "edit_file", "write_file"):
                    return PermissionResultAllow()
                result = await input_provider.get_tool_confirmation(context=ctx)
                return confirmation_result_to_native(result)
            case "default" | "plan" | "delegate" | "dontAsk" | "auto":
                result = await input_provider.get_tool_confirmation(context=ctx)
                return confirmation_result_to_native(result)
            case _ as unreachable:
                assert_never(unreachable)

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
        return await handle_clarifying_questions(
            agent_ctx=ctx,
            input_data=input_data,
            context=context,
        )

    async def _on_elicitation(self, request: SDKControlElicitationRequest) -> ElicitResult:
        """Handle MCP elicitation requests."""
        from mcp.types import ElicitResult, ErrorData

        if self._tool_bridge._current_context is None:
            raise RuntimeError("Elicitation callback invoked outside of an active run")
        input_provider = self._tool_bridge._current_context.get_input_provider()
        params = request.to_mcp()
        match await input_provider.get_elicitation(params=params):
            case ElicitResult() as result:
                return result
            case ErrorData():
                return ElicitResult(action="decline")
            case _ as unreachable_:
                assert_never(unreachable_)  # ty:ignore[type-assertion-failure]

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
            await self._populate_commands()
            self.log.info("Claude Code client connected")
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

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Disconnect from Claude Code."""
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

        Commands that are not supported or not useful for external use
        are filtered out (e.g., login, logout, context, cost).
        """
        await self.ensure_initialized()
        assert self._client, "Client not connected after ensure_initialized"
        server_info = await self._client.get_server_info()
        assert server_info, "No server info returned (streaming mode should always provide it)"
        # Commands to skip - not useful or problematic in this context
        commands = [
            create_claude_code_command(cmd_info)
            for cmd_info in server_info.commands
            if cmd_info.name and cmd_info.name not in UNSUPPORTED_COMMANDS
        ]
        for command in commands:
            self._command_store.register_command(command, replace=True)
        self.log.info("Populated command store", command_count=len(commands))

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
        try:
            await client.query(*claude_prompts)
            # Capture SDK session ID from init message
            stream = client.receive_response()
            first_msg = await anext(stream)
            assert not isinstance(first_msg, AssistantMessage | UserMessage), (
                f"invalid message type {type(first_msg)}"
            )
            self._sdk_session_id = first_msg.session_id
            # Persist SDK session ID to storage for cross-referencing
            if self.storage and self.session_id:
                await self.storage.update_sdk_session_id(self.session_id, self._sdk_session_id)
            adapter = ClaudeCodeStreamedResponse(
                provider_name="claude_code",
                stream=stream,
                tool_metadata=self._tool_bridge.tool_metadata,
                agent_name=self.name,
                session_id=self.session_id,
            )
            async with (
                self._tool_bridge.set_run_context(run_context, prompt=prompts),
                merge_queue_into_iterator(adapter, self._event_queue) as merged_events,  # ty: ignore[invalid-argument-type]
            ):
                async for event in merged_events:
                    reconstructor.observe(event)  # ty:ignore[invalid-argument-type]
                    yield event  # ty:ignore[invalid-yield]

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
            if fork_client:
                try:
                    await fork_client.disconnect()
                except Exception as e:  # noqa: BLE001
                    self.log.warning("Error disconnecting fork client", error=e)

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
            run_usage = to_run_usage(client.query_usage)
            total_cost = Decimal(str(client.query_cost))
            cost_info = TokenCost(total_cost=total_cost)
            stop_reason = result_message.stop_reason
        # Build metadata with SDK session ID
        msg_metadata = {}
        if self._sdk_session_id:
            msg_metadata["sdk_session_id"] = self._sdk_session_id
        finish_reason = (
            "stop" if self._cancelled or not stop_reason else to_finish_reason(stop_reason)
        )
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
            metadata=msg_metadata,
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
        """Set reasoning effort level.

        This requires a session reconnect since effort is a CLI startup flag.
        The current session is preserved via session resumption.

        Args:
            effort: Reasoning effort level ("low", "medium", "high", "max")
        """
        await self._set_mode(effort, "effort")

    async def set_permission_mode(self, mode: PermissionMode) -> None:
        """Set permission mode."""
        await self._set_mode(mode, "mode")

    async def get_available_models(self) -> list[ModelInfo]:
        """Get available models for Claude Code agent (defined as static list)."""
        from agentpool.agents.claude_code_agent.static_info import MODELS

        return MODELS

    async def get_modes(self) -> list[ModeCategory]:
        """Get available mode categories for Claude Code agent.

        Claude Code exposes permission modes, model selection, thinking level,
        and reasoning effort.

        Returns:
            List of ModeCategory for permissions, models, thinking, and effort
        """
        from agentpool.agents.claude_code_agent.static_info import (
            EFFORT_MODES,
            MODES,
            THINKING_MODES,
        )
        from agentpool.agents.modes import ModeCategory

        categories = [
            ModeCategory(
                id="mode",
                name="Mode",
                available_modes=MODES,
                current_mode_id=self._permission_mode or "default",
                category="mode",
            )
        ]
        # Model selection
        models = await self.get_available_models()
        categories.append(models_to_category(models, current_mode=self.model_name))
        # Thinking level selection
        categories.append(
            ModeCategory(
                id="thought_level",
                name="Thinking Level",
                available_modes=THINKING_MODES,
                current_mode_id=self._thinking_mode,
                category="thought_level",
            )
        )
        # Reasoning effort selection
        categories.append(
            ModeCategory(
                id="effort",
                name="Reasoning Effort",
                available_modes=EFFORT_MODES,
                current_mode_id=self._effort or "high",
                category="other",
            )
        )

        return categories

    async def _set_mode(self, mode_id: str | bool, category_id: str) -> None:
        """Handle permissions, model, thinking_level, and effort mode switching."""
        from clawd_code_sdk.models import ReasoningEffort

        from agentpool.agents.claude_code_agent.static_info import VALID_MODES

        match category_id:
            case "mode":
                # Map mode_id to PermissionMode
                if mode_id not in VALID_MODES:
                    raise UnknownModeError(mode_id, list(VALID_MODES))
                self._permission_mode = mode_id  # ty:ignore[invalid-assignment]
                if self._client:  # Update SDK client if initialized
                    await self.ensure_initialized()
                    await self._client.set_permission_mode(self._permission_mode)
            case "model":
                # Validate model exists
                if models := await self.get_available_models():
                    valid_ids = {m.id_override if m.id_override else m.id for m in models}
                    if mode_id not in valid_ids:
                        raise UnknownModeError(mode_id, list(valid_ids))
                # Set the model directly
                assert isinstance(mode_id, str)
                self._model = mode_id
                if self._client:
                    await self.ensure_initialized()
                    assert isinstance(mode_id, str)
                    await self._client.set_model(mode_id)
            case "thought_level":
                # Validate thinking mode
                if mode_id not in THINKING_MODE_TOKENS:
                    raise UnknownModeError(mode_id, list(THINKING_MODE_TOKENS.keys()))
                self._thinking_mode = mode_id  # ty:ignore[invalid-assignment]
                # Set thinking tokens via SDK
                if self._client:
                    await self.ensure_initialized()
                    tokens = THINKING_MODE_TOKENS[self._thinking_mode]
                    await self._client.set_max_thinking_tokens(tokens)
            case "effort":
                # Validate effort level
                if mode_id not in VALID_EFFORTS:
                    raise UnknownModeError(mode_id, list(VALID_EFFORTS))
                self._effort = cast(ReasoningEffort, mode_id)
                # Effort is a CLI startup flag only - requires session reconnect
                if self._client:
                    await self.reconnect(resume_session=True)
            case _:
                raise UnknownCategoryError(category_id)
        await self.update_state(config_id=category_id, value_id=mode_id)

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

    async def load_session(self, session_id: str) -> SessionData | None:
        """Load and restore a session from Claude storage (requires reconnect)."""
        storage = self.storage
        if not storage:
            return None
        try:
            messages = await storage.get_session_messages(session_id=session_id)
        except Exception:
            self.log.exception("Failed to load Claude session", session_id=session_id)
            return None
        if not messages:
            self.log.warning("No messages found in session", session_id=session_id)
            return None
        # Restore to conversation history
        self.conversation.chat_messages.clear()
        self.conversation.chat_messages.extend(messages)
        self.log.info("Session loaded", session_id=session_id, message_count=len(messages))
        # Set the SDK session ID so reconnect can resume this session
        self._sdk_session_id = session_id
        # Reconnect to Claude SDK with the loaded session to properly resume
        try:
            await self.reconnect(resume_session=True)
            self.log.info("Reconnected with loaded session", session_id=session_id)
        except Exception:
            error_msg = "Failed to reconnect with loaded session, continuing with local history"
            self.log.exception(error_msg, session_id=session_id)
        # Build SessionData from storage metadata
        if session_data := await storage.load_session(session_id):
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
