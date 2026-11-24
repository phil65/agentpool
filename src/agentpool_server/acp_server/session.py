"""ACP (Agent Client Protocol) session management for agentpool.

This module provides session lifecycle management, state tracking, and coordination
between agents and ACP clients through the JSON-RPC protocol.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
from decimal import Decimal
import re
import shlex
import subprocess
import sys
from typing import TYPE_CHECKING, Any, Literal, assert_never

from aclientp.agent.acp_requests import ACPRequests
from aclientp.agent.notifications import ACPNotifications
from aclientp.filesystem import ACPFileSystem
from aclientp.schema import ClientCapabilities
from exxec.acp_provider import ACPExecutionEnvironment
from exxec.local_provider import LocalExecutionEnvironment
import logfire
from slashed import CommandStore

from agentpool.agents.acp_agent import ACPAgent
from agentpool.agents.events import StreamCompleteEvent
from agentpool.agents.modes import ConfigOptionChanged
from agentpool.agents.staged_content import StagedContent
from agentpool.log import get_logger
from agentpool.sessions.environment import SessionEnvironment, use_session_environment
from agentpool.turns import (
    TurnCancelled,
    TurnCompleted,
    TurnFailed,
    TurnLimitReached,
    TurnRefused,
    outcome_of_error,
)
from agentpool_commands.base import NodeCommand
from agentpool_server.acp_server.converters import (
    convert_acp_mcp_server_to_config,
    from_acp_content,
)
from agentpool_server.acp_server.input_provider import ACPInputProvider
from agentpool_server.acp_server.v1_output import V1SessionOutput


if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from aclientp import Client, RequestPermissionRequest, RequestPermissionResponse
    from aclientp.schema import (
        ContentBlock,
        Implementation,
        InitialSessionConfigOption,
        McpServer,
        SessionConfigOption,
        SessionModeState,
        StopReason,
        Usage,
    )
    from exxec.base import ExecutionEnvironment
    from pydantic_ai import UserContent
    from slashed import BaseCommand

    from agentpool import AgentPool
    from agentpool.agents.base_agent import BaseAgent
    from agentpool.common_types import PathReference
    from agentpool.sessions import Session as PoolSession
    from agentpool.turns import TurnOutcome
    from agentpool_server.acp_server.acp_agent import AgentPoolACPAgent
    from agentpool_server.acp_server.output import AgentStateChange, SessionOutput
    from agentpool_server.acp_server.session_manager import ACPSessionManager
    from agentpool_server.acp_server.turns import TurnOutput

logger = get_logger(__name__)
SLASH_PATTERN = re.compile(r"^/([\w-]+)(?:\s+(.*))?$")


def supports_client_execution(capabilities: ClientCapabilities) -> bool:
    """Whether tools can run through the client's terminal and filesystem.

    ``ACPFileSystem`` reads and writes files via ``fs/*`` requests but answers
    ``exists``/``ls``/``info`` by running shell commands in a client terminal,
    so it needs both capabilities.
    """
    fs = capabilities.fs
    return bool(capabilities.terminal and fs and fs.read_text_file and fs.write_text_file)


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


def get_all_commands(*, debug: bool = False) -> Sequence[BaseCommand]:
    """The session's slash commands (the debug ones only with ``debug``)."""
    from agentpool_commands import get_commands
    from agentpool_server.acp_server.commands import get_commands as get_acp_commands

    return [
        *get_commands(
            enable_set_model=False,
            enable_list_resources=False,
            enable_add_resource=False,
            enable_show_resource=False,
        ),
        *get_acp_commands(debug=debug),
    ]


def merge_commands(
    session_commands: Sequence[BaseCommand], agent: BaseAgent[Any, Any]
) -> list[BaseCommand]:
    """A session's commands and its agent's (the session's win on a name clash).

    Commands that do not support the agent are left out.
    """
    commands: list[BaseCommand] = []
    names: set[str] = set()
    for cmd in [*session_commands, *agent.command_store.list_commands()]:
        if cmd.name in names:
            continue
        if isinstance(cmd, NodeCommand) and not cmd.supports_node(agent):
            continue
        names.add(cmd.name)
        commands.append(cmd)
    return commands


def _is_slash_command(text: str) -> bool:
    """Check if text starts with a slash command."""
    return bool(SLASH_PATTERN.match(text.strip()))


def split_commands(
    contents: Sequence[UserContent | PathReference],
    is_local: Callable[[str], bool],
) -> tuple[list[str], list[UserContent | PathReference]]:
    """Split content into local slash commands and pass-through content.

    Only commands ``is_local`` accepts are extracted. Commands the agent's backend runs
    (the Claude Code CLI's, a nested ACP agent's) stay in non_command_content so they flow
    through to the agent as the prompt.
    """
    commands: list[str] = []
    non_command_content: list[UserContent | PathReference] = []
    for item in contents:
        # Check if this is a LOCAL command we handle
        if (
            isinstance(item, str)
            and _is_slash_command(item)
            and (match := SLASH_PATTERN.match(item.strip()))
            and is_local(match.group(1))
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

    additional_directories: Sequence[str] = ()
    """Additional workspace roots provided by the client."""

    system_prompt: str | None = None
    """Client-provided system instructions (``append`` mode)."""

    client_capabilities: ClientCapabilities = field(default_factory=ClientCapabilities)
    """Client capabilities for tool registration"""

    client_info: Implementation | None = None
    """Client implementation info (name, version, title)"""

    manager: ACPSessionManager | None = None
    """Session manager for managing sessions. Used for session management commands."""

    output_factory: Callable[[ACPSession], SessionOutput] = V1SessionOutput
    """Builds the session's output for its client's protocol version (v1 by default)."""

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
        self.total_cost = Decimal(0)  # Cumulative cost of this session's turns
        self.background_tasks: dict[str, str] = {}  # Running background tasks (ID -> description)
        commands = get_all_commands(debug=self.acp_agent.debug_commands)
        self.command_store = CommandStore(commands=commands)
        self.command_store._initialize_sync()
        self._update_callbacks: list[Callable[[], None]] = []
        self._commands_update: asyncio.Task[None] | None = None
        self.rules_loading: asyncio.Task[str | None] | None = None
        """Reading of the AGENTS.md / CLAUDE.md rules; the first turn waits for it."""
        self.staged = StagedContent()
        """Context for this session's next prompt. Kept here, not on the agent: sessions
        may share an agent instance, and its staged content goes to whichever turn is next."""
        # Inject Zed-specific instructions if client is Zed
        if self.client_info and self.client_info.name and "zed" in self.client_info.name.lower():
            self.staged.add_text(ZED_CLIENT_PROMPT)
        if self.system_prompt and not self.agent.capabilities.session_instructions:
            # The others get it with the session's instructions (see `environment`)
            self.staged.add_text(self.system_prompt)
        self.notifications = ACPNotifications(client=self.client, session_id=self.session_id)
        self.output: SessionOutput = self.output_factory(self)
        self.requests = ACPRequests(client=self.client, session_id=self.session_id)
        self.input_provider = ACPInputProvider(self)
        self.env = self._create_execution_environment()
        self.fs = self.env.get_fs()
        # The agents run this session's turns in its environment (see `environment`)
        for agent in self.agent_pool.all_agents.values():
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

                agent.set_permission_callback(permission_callback, session_id=self.session_id)

            # Subscribe to state change signal for all agents
            agent.state_updated.connect(self._on_state_updated)
            agent.background_event.connect(self._on_background_event)
        self._watch_agent_commands(self.agent, watch=True)
        self.log.info("Created ACP session", current_agent=self.agent.name)

    def _watch_agent_commands(self, agent: BaseAgent[Any, Any], *, watch: bool) -> None:
        """Follow (or stop following) the commands of the session's agent.

        The agent's commands can change at any time, e.g. Claude Code's once an MCP server
        with prompts has connected.
        """
        events = agent.command_store.command_events
        for signal in (events.added, events.removed, events.changed):
            if watch:
                signal.connect(self._on_agent_commands_changed)
            else:
                with contextlib.suppress(ValueError):
                    signal.disconnect(self._on_agent_commands_changed)

    def _on_agent_commands_changed(self, *_: object) -> None:
        if self._commands_update is not None and not self._commands_update.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # Changed outside of the event loop: the next update has it
            return
        self._commands_update = loop.create_task(self._send_commands_after_changes())

    async def _send_commands_after_changes(self) -> None:
        await asyncio.sleep(0)  # A batch of changes (e.g. all CLI commands) sends one update
        await self.send_available_commands_update()

    def _command_store_for(self, name: str) -> CommandStore | None:
        """The store that runs command ``name`` (None: it goes to the agent as the prompt).

        The session's own commands come first, then the agent's, except those the agent's
        backend runs itself (see ``BaseAgent.is_backend_command``).
        """
        if self.command_store.get_command(name) is not None:
            return self.command_store
        agent_store = self.agent.command_store
        if agent_store.get_command(name) is not None and not self.agent.is_backend_command(name):
            return agent_store
        return None

    async def _on_background_event(self, background: BaseAgent.BackgroundEvent) -> None:
        """Forward an agent event produced between turns (e.g. a task finishing).

        Only this session's events count.
        """
        if background.session_id != self.session_id:
            return
        try:
            await self.output.background_event(background.event)
        except Exception:
            self.log.exception("Failed to forward background event")

    async def _on_state_updated(self, state: AgentStateChange) -> None:
        """Forward an agent's changed mode, model, config option or commands.

        A config option of another session (on an agent shared by sessions) is not ours.
        """
        if isinstance(state, ConfigOptionChanged) and state.session_id not in (
            None,
            self.session_id,
        ):
            return
        await self.output.state_changed(state)

    async def run_command(
        self, command: str, args: Sequence[str], *, cwd: str | None = None
    ) -> tuple[str, int | None]:
        """Run a program where this session's tools run: a client terminal or this host.

        Args:
            command: Program to run
            args: Its arguments
            cwd: Working directory; the session's when omitted

        Returns:
            The combined output and the exit code (None if the program did not run)
        """
        # `cwd` may differ from the environment's, and execute_command takes none
        directory = cwd or self.cwd
        if sys.platform == "win32" and isinstance(self.env, LocalExecutionEnvironment):
            # This host's shell is cmd.exe: no POSIX quoting, and `cd` needs /d for drives
            line = f'cd /d "{directory}" && {subprocess.list2cmdline([command, *args])}'
        else:
            line = f"cd {shlex.quote(directory)} && {shlex.join([command, *args])}"
        result = await self.env.execute_command(line)
        return (result.stdout or "") + (result.stderr or ""), result.exit_code

    def _create_execution_environment(self) -> ExecutionEnvironment:
        """Run tools on the client when it supports that, otherwise on this host."""
        caps = self.client_capabilities
        client_name = self.client_info.name if self.client_info else None
        if supports_client_execution(caps):
            self.log.info(
                "Using client execution environment (terminal and fs via ACP)",
                client=client_name,
                cwd=self.cwd,
            )
            fs = ACPFileSystem(self.client, session_id=self.session_id)
            return ACPExecutionEnvironment(fs=fs, requests=self.requests, cwd=self.cwd)
        self.log.info(
            "Client lacks terminal or fs capabilities, running tools on the server host",
            client=client_name,
            cwd=self.cwd,
            terminal=bool(caps.terminal),
            read_text_file=bool(caps.fs and caps.fs.read_text_file),
            write_text_file=bool(caps.fs and caps.fs.write_text_file),
        )
        return LocalExecutionEnvironment(cwd=self.cwd)

    async def initialize(self) -> None:
        """Initialize async resources. Must be called after construction."""
        await self.env.__aenter__()

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

    @property
    def client_skill_dirs(self) -> list[str]:
        """Client-side skill directories under every workspace root.

        Both ``.claude/skills`` and the cross-client ``.agents/skills`` convention
        are scanned. The roots are the session cwd and the client's
        ``additionalDirectories``, which ACP requires to be absolute paths.
        """
        roots = (
            [self.cwd, *self.additional_directories] if self.cwd else self.additional_directories
        )
        return [
            f"{root.rstrip('/')}/{subdir}"
            for root in dict.fromkeys(roots)
            for subdir in (".claude/skills", ".agents/skills")
        ]

    async def init_client_skills(self) -> None:
        """Discover and load skills from the client's workspace roots.

        Read over ACP when the client provides the filesystem; otherwise the
        roots are local paths on this host.
        """
        manager = self.agent_pool.skills
        for skills_dir in self.client_skill_dirs:
            try:
                if isinstance(self.fs, ACPFileSystem):
                    await manager.add_skills_directory(skills_dir, fs=self.fs)
                else:
                    await manager.add_skills_directory(skills_dir)
            except Exception:
                self.log.exception("Failed to discover client-side skills", path=skills_dir)
        skill_count = len(manager.list_skills())
        self.log.info("Collected client-side skills", skill_count=skill_count)

    @property
    def agent_pool(self) -> AgentPool[Any]:
        """Get the agent pool from the current agent."""
        pool = self.agent.agent_pool
        if pool is None:
            msg = "Agent has no associated pool"
            raise RuntimeError(msg)
        return pool

    @property
    def environment(self) -> SessionEnvironment:
        """Where this session's turns run (the client's), and what agents should know.

        Agents are shared by sessions: the session makes it current while it acts
        (``use_session_environment``), instead of setting it on them.
        """
        return SessionEnvironment(env=self.env, instructions=self.get_cwd_context())

    def get_cwd_context(self) -> str:
        """Get workspace and client instructions context for prompts."""
        parts = [f"Working directory: {self.cwd}"] if self.cwd else []
        if self.additional_directories:
            parts.append("Additional workspace roots: " + ", ".join(self.additional_directories))
        if self.system_prompt:
            parts.append(self.system_prompt)
        return "\n\n".join(parts)

    @property
    def supports_notices(self) -> bool:
        """Whether the client renders ``notice`` session updates."""
        session_caps = self.client_capabilities.session
        return bool(session_caps and session_caps.notices is not None)

    @property
    def supports_compaction(self) -> bool:
        """Whether the client renders ``compaction_update`` session updates."""
        session_caps = self.client_capabilities.session
        return bool(session_caps and session_caps.compaction is not None)

    @property
    def supports_prompt_suggestions(self) -> bool:
        """Whether the client renders ``prompt_suggestions_update`` session updates."""
        session_caps = self.client_capabilities.session
        return bool(session_caps and session_caps.prompt_suggestions is not None)

    async def apply_initial_config_options(
        self, options: Sequence[InitialSessionConfigOption]
    ) -> None:
        """Apply config option values the client sent on session setup."""
        for option in options:
            value = str(option.value).lower() if isinstance(option.value, bool) else option.value
            try:
                await self.set_mode(value, category_id=option.config_id)
            except Exception:
                self.log.exception("Failed to apply initial config option", option=option)

    async def mode_state(self) -> SessionModeState | None:
        """The agent's mode state as this session sees it (see :meth:`set_mode`)."""
        from agentpool_server.acp_server.acp_agent import get_session_mode_state

        with self.agent.use_session(await self.pool_session()):
            return await get_session_mode_state(self.agent)

    async def config_options(self) -> list[SessionConfigOption]:
        """The agent's config options as this session sees them (see :meth:`set_mode`)."""
        from agentpool_server.acp_server.acp_agent import get_session_config_options

        with self.agent.use_session(await self.pool_session()):
            return await get_session_config_options(self.agent)

    async def set_mode(self, value: str | bool, *, category_id: str) -> None:
        """Set a mode or config option of the agent for this session.

        A native agent keeps it per session (``SessionOverrides``), since sessions share
        the pool's agent instance; other agents keep one value for their native session.
        """
        with self.agent.use_session(await self.pool_session()):
            await self.agent.set_mode(value, category_id=category_id)

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
        self._watch_agent_commands(self.agent, watch=False)
        self.agent = agents[agent_name]
        self._watch_agent_commands(self.agent, watch=True)
        if (pool_session := self.agent_pool.sessions.get(self.session_id)) is not None:
            pool_session.rebind_agent(agent_name, agent_type=self.agent.AGENT_TYPE)
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
        try:  # Actively interrupt the agent's stream (only this session's turn)
            await self.agent.interrupt(session_id=self.session_id)
        except Exception:
            self.log.exception("Failed to interrupt agent")

    def is_cancelled(self) -> bool:
        """Check if the session is cancelled."""
        return self._cancelled

    async def process_prompt(self, content_blocks: Sequence[ContentBlock]) -> StopReason:
        """Run a v1 prompt turn: stream its updates, answer with its stop reason.

        Agent failures are shown as a notice and end the turn normally; v1 has no
        error stop reason (v2 has, see ``ACP_V2_SERVER.md``).

        Args:
            content_blocks: Content blocks of the prompt request
        """
        match await self.run_turn(self.convert_prompt(content_blocks), self.output.turn()):
            case TurnCompleted():
                return "end_turn"
            case TurnCancelled():
                return "cancelled"
            case TurnLimitReached(message=message):
                return infer_stop_reason(message)
            case TurnFailed():
                return "end_turn"
            case TurnRefused():
                return "refusal"
            case _ as unreachable:
                assert_never(unreachable)

    def convert_prompt(
        self, content_blocks: Sequence[ContentBlock]
    ) -> list[UserContent | PathReference]:
        """The agent's content for the content blocks of a prompt."""
        fs = self.env.get_fs()  # The client's
        contents = [from_acp_content(i, fs=fs) for i in content_blocks]
        self.log.debug("Converted content", content=contents)
        return contents

    @property
    def is_running(self) -> bool:
        """Whether a foreground prompt turn is currently executing on this session."""
        return self._task_lock.locked()

    async def run_turn(
        self, contents: Sequence[UserContent | PathReference], output: TurnOutput
    ) -> TurnOutcome:
        """Run one prompt turn, protocol-neutral: slash commands, then the agent.

        Turns run one at a time. Agent events go to ``output``, which also wraps up
        the turn (``TurnOutput.finish``) before the next one can start.

        Args:
            contents: The prompt's content
            output: Where the turn's events go
        """
        if not contents:
            self.log.warning("Empty prompt received")
            return TurnRefused(reason="empty prompt")
        commands, non_command_content = split_commands(
            contents, lambda name: self._command_store_for(name) is not None
        )
        async with self._task_lock:
            with use_session_environment(self.environment):  # Commands and turn: ours
                return await self._run_turn_locked(commands, non_command_content, output)

    async def _run_turn_locked(
        self,
        commands: Sequence[str],
        non_command_content: Sequence[UserContent | PathReference],
        output: TurnOutput,
    ) -> TurnOutcome:
        """The commands, then the prompt of a turn (with the turn lock held)."""
        # Reset only once the previous turn is over, it may still be cancelling
        self._cancelled = False
        for command in commands:
            self.log.info("Processing slash command", command=command)
            # Commands may stage content for the next prompt: this session's, even
            # on an agent other sessions share (and may switch the agent)
            with self.agent.use_session(await self.pool_session()):
                await self.execute_slash_command(command)
        if not non_command_content:
            return TurnCompleted()
        self.log.debug("Processing prompt", content_items=len(non_command_content))
        await self._wait_for_rules()
        if staged := await self.staged.consume_as_context():
            non_command_content = [*non_command_content, staged]
        await output.start()  # After the commands: they may switch model or agent
        outcome = await self._stream_agent(non_command_content, output)
        await output.finish(outcome)
        return outcome

    async def _wait_for_rules(self) -> None:
        """Stage the rules first, so they go into this turn's prompt and no later one."""
        if (task := self.rules_loading) is None:
            return
        self.rules_loading = None
        try:
            rules = await task
        except Exception:  # The turn runs without them
            self.log.exception("Failed to load the rules")
            return
        if rules is not None:
            self.staged.add_text(rules)

    async def pool_session(self) -> PoolSession | None:
        """The pool ``Session`` of this ACP session, for agents that run sessions concurrently.

        Agents with ``capabilities.concurrent_sessions`` (native agents, T3) keep each
        session's history, settings and backend session (``sdk_session_id``) on it. Others
        have one native session per instance and run on their own (default) session.
        """
        if not self.agent.capabilities.concurrent_sessions or self.agent.agent_pool is None:
            return None
        return await self.agent.agent_pool.sessions.open(
            self.session_id, self.agent.name, cwd=self.cwd, agent_type=self.agent.AGENT_TYPE
        )

    async def _stream_agent(
        self, contents: Sequence[UserContent | PathReference], output: TurnOutput
    ) -> TurnOutcome:
        event_count = 0
        outcome: TurnOutcome | None = None  # The agent turn's own (its final event's)
        try:  # Use the session's persistent input provider
            # Staged content is automatically injected by run_stream
            async with self.agent.session_turn():  # Sessions share pool agents
                async for event in self.agent.run_stream(
                    *contents,
                    input_provider=self.input_provider,
                    deps=self,
                    session=await self.pool_session(),
                    session_id=self.session_id,
                ):
                    if self._cancelled:
                        # Interrupted agents end their stream: let it end here instead of
                        # leaving it to the garbage collector, whose cleanup in another
                        # task cancels this one through the run's cancel scopes
                        continue
                    event_count += 1
                    if isinstance(event, StreamCompleteEvent) and event.turn is not None:
                        outcome = event.turn.outcome
                    await output.event(event)
        except asyncio.CancelledError:
            # Task was cancelled (e.g., via interrupt() or `$/cancel_request`). Answer
            # with the proper outcome instead of propagating the cancellation.
            self.log.info("Stream cancelled via CancelledError, cleaning up tool calls")
            return TurnCancelled()
        except Exception as e:  # The turn's outcome, as every server maps it
            failed = outcome_of_error(e)
            if isinstance(failed, TurnFailed):
                self.log.exception("Error during streaming")
            else:
                self.log.info("Turn ended", outcome=type(failed).__name__, error=str(e))
            return failed
        self.log.info("Streaming finished", events_processed=event_count)
        # An interrupted agent may end its stream normally without another event
        if self._cancelled:
            return TurnCancelled()
        return outcome if outcome is not None else TurnCompleted()

    async def _get_context_window(self) -> int | None:
        """Context window of the agent's current model, if it can be determined."""
        return await self.agent.context_window()

    async def _send_error_notification(self, message: str) -> None:
        """Show an error, unless the turn was cancelled."""
        if self._cancelled:
            return
        try:
            await self.output.error(message)
        except Exception:
            self.log.exception("Failed to send error notification")

    async def close(self) -> None:
        """Close the session and cleanup resources."""
        try:
            await self.env.__aexit__(None, None, None)
            for pool_agent in self.agent_pool.all_agents.values():
                with contextlib.suppress(ValueError):  # Agents added after this session
                    pool_agent.background_event.disconnect(self._on_background_event)
                with contextlib.suppress(ValueError):
                    pool_agent.state_updated.disconnect(self._on_state_updated)
                if isinstance(pool_agent, ACPAgent):
                    pool_agent.set_permission_callback(None, session_id=self.session_id)
            self._watch_agent_commands(self.agent, watch=False)
            if self.agent.agent_pool is not None:
                self.agent.agent_pool.sessions.remove(self.session_id)
            if self._commands_update is not None:
                self._commands_update.cancel()

            # Note: Individual agents are managed by the pool's lifecycle
            # The pool will handle agent cleanup when it's closed
            self.log.info("Closed ACP session")
        except Exception:
            self.log.exception("Error closing session")

    async def send_available_commands_update(self) -> None:
        """Send the session's current slash commands to the client."""
        try:
            await self.output.commands_changed(self.list_commands())
        except Exception:
            self.log.exception("Failed to send available commands update")

    async def _register_mcp_prompts_as_commands(self) -> None:
        """Register MCP prompts as slash commands."""
        if all_prompts := await self.agent.tools.list_prompts():
            for prompt in all_prompts:
                command = prompt.create_mcp_command(self.staged)
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
                    self.staged,
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

    def list_commands(self) -> list[BaseCommand]:
        """The session's commands and the agent's (the session's win on a name clash).

        Commands that do not support the session's agent are left out.
        """
        return merge_commands(self.command_store.list_commands(), self.agent)

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

        # The session's command, else the agent's (unknown names: the session reports them)
        store = self._command_store_for(command_name) or self.command_store
        # Check if command supports current node type
        if (
            (cmd := store.get_command(command_name))
            and isinstance(cmd, NodeCommand)
            and not cmd.supports_node(self.agent)
        ):
            error_msg = f"❌ Command `/{command_name}` is not available for this node type"
            await self.output.text(error_msg)
            return

        # Create context with session data
        agent_context = self.agent.get_context(data=self)
        cmd_ctx = store.create_context(
            data=agent_context,
            output_writer=self.output.text,
        )

        command_str = f"{command_name} {args}".strip()
        try:
            await store.execute_command(command_str, cmd_ctx)
        except Exception as e:
            logger.exception("Command execution failed")
            await self._send_error_notification(f"❌ Command error: {e}")

    def register_update_callback(self, callback: Callable[[], None]) -> None:
        """Register callback for command updates."""
        self._update_callbacks.append(callback)
