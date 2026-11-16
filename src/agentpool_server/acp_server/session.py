"""ACP (Agent Client Protocol) session management for agentpool.

This module provides session lifecycle management, state tracking, and coordination
between agents and ACP clients through the JSON-RPC protocol.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import re
from typing import TYPE_CHECKING, Any, Literal, assert_never

import anyio
from exxec.acp_provider import ACPExecutionEnvironment
import logfire
from pydantic_ai import UsageLimitExceeded
from slashed import CommandStore
from tokonomics.model_discovery.model_info import ModelInfo

from acp.agent.acp_requests import ACPRequests
from acp.agent.notifications import ACPNotifications
from acp.filesystem import ACPFileSystem
from acp.schema import AvailableCommand, ClientCapabilities
from agentpool import Agent, AgentPool
from agentpool.agents.acp_agent import ACPAgent
from agentpool.agents.modes import ConfigOptionChanged, ModeInfo
from agentpool.log import get_logger
from agentpool_commands.base import NodeCommand
from agentpool_server.acp_server.converters import (
    convert_acp_mcp_server_to_config,
    from_acp_content,
)
from agentpool_server.acp_server.event_converter import ACPEventConverter
from agentpool_server.acp_server.input_provider import ACPInputProvider


if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from pydantic_ai import UserContent
    from slashed import BaseCommand

    from acp import Client, RequestPermissionRequest, RequestPermissionResponse
    from acp.schema import (
        AvailableCommandsUpdate,
        ContentBlock,
        Implementation,
        McpServer,
        StopReason,
        Usage,
    )
    from agentpool.agents.base_agent import BaseAgent
    from agentpool.common_types import PathReference
    from agentpool_server.acp_server.acp_agent import AgentPoolACPAgent
    from agentpool_server.acp_server.session_manager import ACPSessionManager

logger = get_logger(__name__)
SLASH_PATTERN = re.compile(r"^/([\w-]+)(?:\s+(.*))?$")

# Zed-specific instructions for code references
ZED_CLIENT_PROMPT = """\
## Code References

When referencing code locations in responses, use markdown links with `file://` URLs:

- **File**: `[filename](file:///absolute/path/to/file.py)`
- **Line range**: `[filename#L10-25](file:///absolute/path/to/file.py#L10:25)`
- **Single line**: `[filename#L10](file:///absolute/path/to/file.py#L10:10)`
- **Directory**: `[dirname/](file:///absolute/path/to/dir/)`

Line range format is `#L<start>:<end>` (1-based, inclusive).

Use these clickable references instead of inline code blocks when pointing to specific \
code locations. For showing actual code content, still use fenced code blocks.

## Zed-specific URLs

In addition to `file://` URLs, these `zed://` URLs work in the agent context:

- **File reference**: `[text](zed:///agent/file?path=/absolute/path/to/file.py)`
- **Selection**: `[text](zed:///agent/selection?path=/absolute/path/to/file.py#L10:25)`
- **Symbol**: `[text](zed:///agent/symbol/function_name?path=/absolute/path/to/file.py#L10:25)`
- **Directory**: `[text](zed:///agent/directory?path=/absolute/path/to/dir)`

Query params must be URL-encoded (spaces → `%20`). Paths must be absolute.
"""


def get_all_commands() -> Sequence[BaseCommand]:
    from agentpool_commands import get_commands
    from agentpool_server.acp_server.commands import get_commands as get_acp_commands

    return [
        *get_commands(
            enable_set_model=False,
            enable_list_resources=False,
            enable_add_resource=False,
            enable_show_resource=False,
        ),
        *get_acp_commands(),
    ]


def _is_slash_command(text: str) -> bool:
    """Check if text starts with a slash command."""
    return bool(SLASH_PATTERN.match(text.strip()))


def split_commands(
    contents: Sequence[UserContent | PathReference],
    command_store: CommandStore,
) -> tuple[list[str], list[UserContent | PathReference]]:
    """Split content into local slash commands and pass-through content.

    Only commands that exist in the local command_store are extracted.
    Remote commands (from nested ACP agents) stay in non_command_content
    so they flow through to the agent and reach the nested server.
    """
    commands: list[str] = []
    non_command_content: list[UserContent | PathReference] = []
    for item in contents:
        # Check if this is a LOCAL command we handle
        if (
            isinstance(item, str)
            and _is_slash_command(item)
            and (match := SLASH_PATTERN.match(item.strip()))
            and command_store.get_command(match.group(1))
        ):
            commands.append(item.strip())
        else:
            # Not a local command - pass through (may be remote command or regular text)
            non_command_content.append(item)
    return commands, non_command_content


def infer_stop_reason(error_msg: str) -> StopReason:
    """Infers the reason for stopping the session based on the error message."""
    if "request_limit" in error_msg:
        return "max_turn_requests"
    if any(limit in error_msg for limit in ["tokens_limit", "token_limit"]):
        return "max_tokens"
    # Tool call limits don't have a direct ACP stop reason, treat as refusal
    if "tool_calls_limit" in error_msg or "tool call" in error_msg:
        return "refusal"
    return "max_tokens"  # Default to max_tokens for other usage limits


@dataclass
class ACPSession:
    """Individual ACP session state and management.

    Manages the lifecycle and state of a single ACP session, including:
    - Agent instance and conversation state
    - Working directory and environment
    - MCP server connections
    - File system bridge for client operations
    - Tool execution and streaming updates
    """

    session_id: str
    """Unique session identifier"""

    agent: BaseAgent[Any, Any]
    """Currently active agent instance.

    The agent carries its own pool reference via agent.agent_pool,
    which is used for agent switching and pool-level operations.
    """

    cwd: str
    """Working directory for the session"""

    client: Client
    """External library Client interface for operations"""

    acp_agent: AgentPoolACPAgent
    """ACP agent instance for capability tools"""

    mcp_servers: Sequence[McpServer] | None = None
    """Optional MCP server configurations"""

    client_capabilities: ClientCapabilities = field(default_factory=ClientCapabilities)
    """Client capabilities for tool registration"""

    client_info: Implementation | None = None
    """Client implementation info (name, version, title)"""

    manager: ACPSessionManager | None = None
    """Session manager for managing sessions. Used for session management commands."""

    subagent_display_mode: Literal["inline", "tool_box"] = "tool_box"
    """How to display subagent output:
    - 'inline': Subagent output flows into main message stream
    - 'tool_box': Subagent output contained in the tool call's progress box (default)
    """

    def __post_init__(self) -> None:
        """Initialize session state and set up providers."""
        self.mcp_servers = self.mcp_servers or []
        self.log = logger.bind(session_id=self.session_id)
        self._task_lock = asyncio.Lock()
        self._cancelled = False
        self.last_usage: Usage | None = None
        self.fs = ACPFileSystem(self.client, session_id=self.session_id)
        self.command_store = CommandStore(commands=get_all_commands())
        self.command_store._initialize_sync()
        self._update_callbacks: list[Callable[[], None]] = []
        self._remote_commands: list[AvailableCommand] = []  # Commands from nested ACP agents
        # Inject Zed-specific instructions if client is Zed
        if self.client_info and self.client_info.name and "zed" in self.client_info.name.lower():
            self.agent.staged_content.add_text(ZED_CLIENT_PROMPT)
        self.notifications = ACPNotifications(client=self.client, session_id=self.session_id)
        self.requests = ACPRequests(client=self.client, session_id=self.session_id)
        self.input_provider = ACPInputProvider(self)
        self.acp_env = ACPExecutionEnvironment(fs=self.fs, requests=self.requests, cwd=self.cwd)
        for agent in self.agent_pool.all_agents.values():
            agent.env = self.acp_env
            if isinstance(agent, Agent):
                # TODO: need to inject this info for ACP agents, too.
                agent.sys_prompts.prompts.append(self.get_cwd_context)
            if isinstance(agent, ACPAgent):

                async def permission_callback(
                    params: RequestPermissionRequest,
                ) -> RequestPermissionResponse:
                    # Reconstruct request with our session_id (not nested agent's session_id)
                    self.log.debug("Forwarding permission request", request=params)
                    try:
                        forwarded = params.model_copy(update={"session_id": self.session_id})
                        response = await self.requests.client.request_permission(forwarded)
                        self.log.debug("Permission response received", response=response)
                    except Exception as exc:
                        self.log.exception("Permission forwarding failed", error=str(exc))
                        raise
                    else:
                        return response

                agent.acp_permission_callback = permission_callback

            # Subscribe to state change signal for all agents
            agent.state_updated.connect(self._on_state_updated)
        self.log.info("Created ACP session", current_agent=self.agent.name)

    async def _on_state_updated(
        self, state: ModeInfo | ModelInfo | AvailableCommandsUpdate | ConfigOptionChanged
    ) -> None:
        """Handle state update signal from agent - forward to ACP client."""
        from acp.schema import (
            AvailableCommandsUpdate,
            BooleanSessionConfigOption,
            ConfigOptionUpdate,
            CurrentModeUpdate,
            SelectSessionConfigOption,
        )
        from agentpool_server.acp_server.acp_agent import get_session_config_options

        update: CurrentModeUpdate | ConfigOptionUpdate
        match state:
            case ModeInfo(value=mode_id, category_id="mode"):
                assert isinstance(mode_id, str)
                update = CurrentModeUpdate(current_mode_id=mode_id)
                self.log.debug("Forwarding mode change to client", mode_id=mode_id)
            case ModelInfo(id=model_id):
                # Model changes go through ConfigOptionUpdate (model category)
                config_options = await get_session_config_options(self.agent)
                match next((i for i in config_options if i.id == "model"), None):
                    case SelectSessionConfigOption() as model_opt:
                        model_opt.current_value = model_id
                    case _:
                        pass
                update = ConfigOptionUpdate(config_options=config_options)
                self.log.debug("Forwarding model change as config update", model_id=model_id)
            case AvailableCommandsUpdate(available_commands=cmds):
                # Store remote commands and send merged list
                self._remote_commands = list(cmds)
                await self.send_available_commands_update()
                self.log.debug("Merged and sent commands update to client")
                return
            case ConfigOptionChanged(config_id=config_id, value_id=value_id):
                # Get full config_options from agent (required by ACP protocol)
                config_options = await get_session_config_options(self.agent)
                # Update the changed option's current_value
                match next((i for i in config_options if i.id == config_id), None), value_id:
                    case SelectSessionConfigOption() as select_opt, str() as value:
                        select_opt.current_value = value
                    case BooleanSessionConfigOption() as bool_opt, bool() as flag:
                        bool_opt.current_value = flag
                    case _:
                        pass
                # Convert our core type to ACP type with full config_options
                update = ConfigOptionUpdate(config_options=config_options)
                self.log.debug("Config option change", config_id=config_id, value_id=value_id)
                # For permissions, also send legacy CurrentModeUpdate (still needed)
                if config_id == "permissions":
                    assert isinstance(value_id, str)
                    await self.notifications.update_session_mode(value_id)
                    self.log.debug("Also sent legacy mode update", mode_id=value_id)
            case ModeInfo():
                return
            case _ as unreachable:
                assert_never(unreachable)
        await self.notifications.send_update(update)

    async def initialize(self) -> None:
        """Initialize async resources. Must be called after construction."""
        await self.acp_env.__aenter__()

    async def initialize_mcp_servers(self) -> None:
        """Initialize MCP servers if any are configured."""
        if not self.mcp_servers:
            return
        self.log.info("Initializing MCP servers", server_count=len(self.mcp_servers))
        # Add each MCP server to the current agent's MCP manager dynamically
        for server in self.mcp_servers:
            cfg = convert_acp_mcp_server_to_config(server)
            try:
                await self.agent.mcp.setup_server(cfg)
                self.log.info("Added MCP server", server_name=cfg.name, agent=self.agent.name)
            except Exception:
                self.log.exception("Failed to setup MCP server", server_name=cfg.name)
                # Don't fail session creation, just log the error
        # Register MCP prompts as commands after all servers are added
        try:
            await self._register_mcp_prompts_as_commands()
        except Exception:
            self.log.exception("Failed to register MCP prompts as commands")

    async def init_client_skills(self) -> None:
        """Discover and load skills from client-side .claude/skills directory."""
        try:
            await self.agent_pool.skills.add_skills_directory(".claude/skills", fs=self.fs)
            skills = self.agent_pool.skills.list_skills()
            self.log.info("Collected client-side skills", skill_count=len(skills))
        except Exception as e:
            self.log.exception("Failed to discover client-side skills", error=e)

    @property
    def agent_pool(self) -> AgentPool[Any]:
        """Get the agent pool from the current agent."""
        pool = self.agent.agent_pool
        if pool is None:
            msg = "Agent has no associated pool"
            raise RuntimeError(msg)
        return pool

    def get_cwd_context(self) -> str:
        """Get current working directory context for prompts."""
        return f"Working directory: {self.cwd}" if self.cwd else ""

    async def switch_active_agent(self, agent_name: str) -> None:
        """Switch to a different agent in the pool.

        Args:
            agent_name: Name of the agent to switch to

        Raises:
            ValueError: If agent not found in pool
        """
        agents = self.agent_pool.all_agents
        if agent_name not in agents:
            available = list(agents.keys())
            raise ValueError(f"Agent {agent_name!r} not found. Available: {available}")
        old_agent_name = self.agent.name
        # self.agent = pool.get_agent(agent_name, deps_type=ACPSession)
        self.agent = agents[agent_name]
        self.log.info("Switched agents", from_agent=old_agent_name, to_agent=agent_name)
        # Persist the agent switch via session manager
        if self.manager:
            await self.manager.update_session_agent(self.session_id, agent_name)
        await self.send_available_commands_update()

    async def cancel(self) -> None:
        """Cancel the current prompt turn.

        This actively interrupts the running agent by calling its interrupt() method,
        which handles protocol-specific cancellation (e.g., sending CancelNotification
        for ACP agents, calling SDK interrupt for ClaudeCodeAgent, etc.).

        Note:
            Tool call cleanup is handled in process_prompt() to avoid race conditions
            with the converter state being modified from multiple async contexts.
        """
        self._cancelled = True
        self.log.info("Session cancelled, interrupting agent")
        try:  # Actively interrupt the agent's stream
            await self.agent.interrupt()
        except Exception:
            self.log.exception("Failed to interrupt agent")

    def is_cancelled(self) -> bool:
        """Check if the session is cancelled."""
        return self._cancelled

    async def process_prompt(self, content_blocks: Sequence[ContentBlock]) -> StopReason:  # noqa: PLR0911
        """Process a prompt request and stream responses.

        Args:
            content_blocks: List of content blocks from the prompt request

        Returns:
            Stop reason
        """
        self._cancelled = False
        fs = self.agent.env.get_fs()
        contents = [from_acp_content(i, fs=fs) for i in content_blocks]
        self.log.debug("Converted content", content=contents)
        if not contents:
            self.log.warning("Empty prompt received")
            return "refusal"
        commands, non_command_content = split_commands(contents, self.command_store)
        async with self._task_lock:
            if commands:  # Process commands if found
                for command in commands:
                    self.log.info("Processing slash command", command=command)
                    await self.execute_slash_command(command)

                # If only commands, end turn
                if not non_command_content:
                    return "end_turn"

            self.log.debug("Processing prompt", content_items=len(non_command_content))
            event_count = 0
            # Create a new event converter for this prompt
            converter = ACPEventConverter(subagent_display_mode=self.subagent_display_mode)

            try:  # Use the session's persistent input provider
                # Staged content is automatically injected by run_stream
                async for event in self.agent.run_stream(
                    *non_command_content,
                    input_provider=self.input_provider,
                    deps=self,
                    session_id=self.session_id,  # Tie agent conversation to ACP session
                ):
                    if self._cancelled:
                        self.log.info("Cancelled during event loop, cleaning up tool calls")
                        # Send cancellation notifications for any pending tool calls
                        # This happens in the same async context as the converter
                        async for cancel_update in converter.cancel_pending_tools():
                            await self.notifications.send_update(cancel_update)
                        # CRITICAL: Allow time for client to process tool completion notifications
                        # before sending PromptResponse. Without this delay, the client may receive
                        # and process the PromptResponse before the tool notifications, causing UI
                        # state desync where subsequent prompts appear stuck/unresponsive.
                        # This is needed because even though send() awaits the write, the client
                        # may process messages asynchronously or out of order.
                        await anyio.sleep(0.05)
                        return "cancelled"

                    event_count += 1
                    async for update in converter.convert(event):
                        await self.notifications.send_update(update)
                    # Yield control to allow notifications to be sent immediately
                    await anyio.sleep(0.01)
                self.log.info("Streaming finished", events_processed=event_count)

            except asyncio.CancelledError:
                # Task was cancelled (e.g., via interrupt()) - return proper stop reason
                # This is critical: CancelledError doesn't inherit from Exception,
                # so we must catch it explicitly to send the PromptResponse
                self.log.info("Stream cancelled via CancelledError, cleaning up tool calls")
                # Send cancellation notifications for any pending tool calls
                async for cancel_update in converter.cancel_pending_tools():
                    await self.notifications.send_update(cancel_update)
                # CRITICAL: Allow time for client to process tool completion notifications
                # before sending PromptResponse. See comment in cancellation branch above.
                await anyio.sleep(0.05)
                return "cancelled"
            except UsageLimitExceeded as e:
                self.log.info("Usage limit exceeded", error=str(e))
                return infer_stop_reason(str(e))
            except Exception as e:
                self.log.exception("Error during streaming")
                # Send error notification asynchronously to avoid blocking response
                coro = self._send_error_notification(f"❌ Agent error: {e}")
                self.acp_agent.tasks.create_task(coro)
                return "end_turn"
            else:
                # Title generation is now handled automatically by log_session
                self.last_usage = converter.last_usage
                return "end_turn"

    async def _send_error_notification(self, message: str) -> None:
        """Send error notification, with exception handling."""
        if self._cancelled:
            return
        try:
            await self.notifications.send_agent_text(message)
        except Exception:
            self.log.exception("Failed to send error notification")

    async def close(self) -> None:
        """Close the session and cleanup resources."""
        try:
            await self.acp_env.__aexit__(None, None, None)
            # Remove cwd context callable from all agents
            for agent in self.agent_pool.get_agents(Agent).values():
                if self.get_cwd_context in agent.sys_prompts.prompts:
                    agent.sys_prompts.prompts.remove(self.get_cwd_context)

            # Note: Individual agents are managed by the pool's lifecycle
            # The pool will handle agent cleanup when it's closed
            self.log.info("Closed ACP session")
        except Exception:
            self.log.exception("Error closing session")

    async def send_available_commands_update(self) -> None:
        """Send current available commands to client.

        Merges local commands from command_store with any remote commands
        from nested ACP agents.
        """
        commands = [*self.get_acp_commands(), *self._remote_commands]
        try:
            await self.notifications.update_commands(commands)
        except Exception:
            self.log.exception("Failed to send available commands update")

    async def _register_mcp_prompts_as_commands(self) -> None:
        """Register MCP prompts as slash commands."""
        if all_prompts := await self.agent.tools.list_prompts():
            for prompt in all_prompts:
                command = prompt.create_mcp_command(self.agent.staged_content)
                self.command_store.register_command(command)
            self._notify_command_update()
            self.log.info("Registered MCP prompts as commands", prompt_count=len(all_prompts))
            await self.send_available_commands_update()  # Send updated command list to client

    async def _register_prompt_hub_commands(self) -> None:
        """Register prompt hub prompts as slash commands."""
        manager = self.agent_pool.prompt_manager
        cmd_count = 0
        all_prompts = await manager.list_prompts()
        for provider_name, prompt_names in all_prompts.items():
            if not prompt_names:  # Skip empty providers
                continue
            for prompt_name in prompt_names:
                command = manager.create_prompt_hub_command(
                    provider_name,
                    prompt_name,
                    self.agent.staged_content,
                )
                self.command_store.register_command(command)
                cmd_count += 1

        if cmd_count > 0:
            self._notify_command_update()
            self.log.info("Registered hub prompts as slash commands", cmd_count=cmd_count)
            await self.send_available_commands_update()  # Send updated command list to client

    def _notify_command_update(self) -> None:
        """Notify all registered callbacks about command updates."""
        for callback in self._update_callbacks:
            try:
                callback()
            except Exception:
                logger.exception("Command update callback failed")

    def get_acp_commands(self) -> list[AvailableCommand]:
        """Convert all slashed commands to ACP format."""
        # Filter commands by node compatibility
        cmds = []
        for cmd in self.command_store.list_commands():
            # Check if command supports current node type
            if isinstance(cmd, NodeCommand) and not cmd.supports_node(self.agent):
                continue
            available_cmd = AvailableCommand.create(
                name=cmd.name,
                description=cmd.description,
                input_hint=cmd.usage,
            )
            cmds.append(available_cmd)
        return cmds

    @logfire.instrument(r"Execute Slash Command {command_text}")
    async def execute_slash_command(self, command_text: str) -> None:
        """Execute any slash command with unified handling.

        Args:
            command_text: Full command text (including slash)
            session: ACP session context
        """
        if match := SLASH_PATTERN.match(command_text.strip()):
            command_name = match.group(1)
            args = match.group(2) or ""
        else:
            logger.warning("Invalid slash command", command=command_text)
            return

        # Check if command supports current node type
        if (
            (cmd := self.command_store.get_command(command_name))
            and isinstance(cmd, NodeCommand)
            and not cmd.supports_node(self.agent)
        ):
            error_msg = f"❌ Command `/{command_name}` is not available for this node type"
            await self.notifications.send_agent_text(error_msg)
            return

        # Create context with session data
        agent_context = self.agent.get_context(data=self)
        cmd_ctx = self.command_store.create_context(
            data=agent_context,
            output_writer=self.notifications.send_agent_text,
        )

        command_str = f"{command_name} {args}".strip()
        try:
            await self.command_store.execute_command(command_str, cmd_ctx)
        except Exception as e:
            logger.exception("Command execution failed")
            # Send error notification asynchronously to avoid blocking
            coro = self._send_error_notification(f"❌ Command error: {e}")
            self.acp_agent.tasks.create_task(coro)

    def register_update_callback(self, callback: Callable[[], None]) -> None:
        """Register callback for command updates."""
        self._update_callbacks.append(callback)
