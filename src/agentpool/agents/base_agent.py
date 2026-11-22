"""Base class for all agent types."""

from __future__ import annotations

from abc import abstractmethod
import asyncio
from collections.abc import Callable
from contextlib import contextmanager, nullcontext, suppress
from dataclasses import dataclass, field, replace
import os
from pathlib import Path
import re
from typing import TYPE_CHECKING, Any, ClassVar, Literal, Self, assert_never, overload

from anyenv import MultiEventHandler, method_spawner
from anyenv.signals import Signal
import anyio
from pydantic_ai import ModelRequest
from upathtools.filesystems import IsolatedMemoryFileSystem

from agentpool.agents.capabilities import AgentCapabilities
from agentpool.agents.events import StreamCompleteEvent, resolve_event_handlers
from agentpool.agents.modes import ModeInfo
from agentpool.agents.runtime import SessionRuntime, activate, active_runtime
from agentpool.hooks import AgentHooks
from agentpool.log import get_logger
from agentpool.messaging import ChatMessage, MessageHistory, MessageNode
from agentpool.sessions.environment import current_session_environment
from agentpool.tools.manager import ToolManager
from agentpool.utils.inspection import call_with_context
from agentpool.utils.time_utils import get_now


if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Generator, Sequence
    from contextlib import AbstractAsyncContextManager
    from datetime import datetime

    from aclientp.schema import AvailableCommandsUpdate
    from evented_config import EventConfig
    from exxec import ExecutionEnvironment
    from fsspec import AbstractFileSystem
    from pydantic_ai import UserContent
    from slashed import BaseCommand, CommandStore
    from tokonomics.model_discovery.model_info import ModelInfo
    from upathtools.filesystems import OverlayFileSystem

    from agentpool.agents.auth import AuthStatus
    from agentpool.agents.context import AgentContext
    from agentpool.agents.events import (
        CommandCompleteEvent,
        CommandOutputEvent,
        RichAgentStreamEvent,
        StreamWithCommandsEvent,
    )
    from agentpool.agents.modes import (
        ConfigOption,
        ConfigOptionChanged,
        ModeCategory,
        ModeCategoryId,
    )
    from agentpool.agents.prompt_injection import PromptInjectionManager
    from agentpool.agents.staged_content import StagedContent
    from agentpool.agents.usage_limits import UsageLimits
    from agentpool.common_types import (
        AgentName,
        AnyEventHandlerType,
        IndividualEventHandler,
        MCPServerStatus,
        ProcessorCallback,
        PromptCompatible,
        StrPath,
    )
    from agentpool.delegation import AgentPool, Team, TeamRun
    from agentpool.messaging import ChatMessage
    from agentpool.messaging.compaction import CompactionPipeline
    from agentpool.sessions import Session, SessionData
    from agentpool.storage import StorageManager
    from agentpool.talk.stats import MessageStats
    from agentpool.ui.base import InputProvider
    from agentpool_config.mcp_server import MCPServerConfig

    # Union type for state updates emitted via state_updated signal
    type StateUpdate = ModeInfo | ModelInfo | AvailableCommandsUpdate | ConfigOptionChanged


logger = get_logger(__name__)

# Literal type for all agent types
type AgentTypeLiteral = Literal[
    "native", "acp", "agui", "antigravity", "claude", "codex", "opencode_v2", "t3"
]


_SLASH_PATTERN: re.Pattern[str] = re.compile(r"^/([\w-]+)(?:\s+(.*))?$")


def _parse_slash_command(command_text: str) -> tuple[str, str] | None:
    """Parse slash command into name and args.

    Args:
        command_text: Full command text

    Returns:
        Tuple of (cmd_name, args) or None if invalid
    """
    if match := _SLASH_PATTERN.match(command_text.strip()):
        cmd_name = match.group(1)
        args = match.group(2) or ""
        return cmd_name, args.strip()
    return None


def split_exchange(
    user_msg: ChatMessage[Any], response: ChatMessage[Any]
) -> list[ChatMessage[Any]]:
    """Split a run into conversation entries without duplicating the prompt.

    A response's ``messages`` hold the whole exchange, starting with the request that
    carried the prompt (that is also how storage persists runs). Keeping that request
    in both entries would send every prompt to the model twice, so the user entry gets
    the request that was actually sent and the response keeps the rest.
    """
    match response.messages:
        case [ModelRequest() as request, *rest]:
            return [replace(user_msg, messages=[request]), replace(response, messages=list(rest))]
        case _:
            return [user_msg, response]


class BaseAgent[TDeps = None, TResult = str](MessageNode[TDeps, TResult]):
    """Base class for all AgentPool agent implementations.

    Provides shared infrastructure:
    - tools: ToolManager for tool registration and execution
    - conversation: MessageHistory for conversation state
    - event_handler: MultiEventHandler for event distribution
    - _event_queue: Queue for events of tools and subagents during a run
    - _input_provider: Provider for user input/confirmations
    - env: ExecutionEnvironment for running code/commands
    - get_context(): AgentContext for tools and callbacks

    Signals:
        - run_failed: Agent execution failed (with error details)
        - interrupted: A running stream was interrupted
        - agent_reset: Conversation and tool states were reset
        - state_updated: A config option (mode, model, ...) changed
        - background_event: An event arrived while no run was streaming
    """

    # Abstract class variable - subclasses must define this
    AGENT_TYPE: ClassVar[AgentTypeLiteral]
    CAPABILITIES: ClassVar[AgentCapabilities] = AgentCapabilities()

    TITLES_OWN_SESSIONS: ClassVar[bool] = False
    """Whether the agent titles its sessions itself (through its backend), instead of the
    storage titling them from the first prompt (``StorageManager.log_session``)."""

    @dataclass(frozen=True)
    class RunFailedEvent:
        """Event emitted when agent execution fails."""

        agent_name: str
        """Name of the agent that failed."""
        message: str
        """Error description."""
        exception: Exception
        """The exception that caused the failure."""
        timestamp: Any = field(default_factory=get_now)  # datetime
        """When the failure occurred."""

    @dataclass(frozen=True)
    class AgentReset:
        """Emitted when agent is reset."""

        agent_name: AgentName
        timestamp: datetime = field(default_factory=get_now)

    @dataclass(frozen=True)
    class InterruptEvent:
        """Emitted when agent is interrupted."""

        agent_name: AgentName
        timestamp: datetime = field(default_factory=get_now)

    @dataclass(frozen=True)
    class BackgroundEvent:
        """An event the agent produced while no run was streaming.

        E.g. a background task finishing after the turn that started it, or work the
        backend did on its own between turns (Claude Code reacting to a finished task).
        """

        agent_name: AgentName
        session_id: str | None
        """Session the agent was bound to (consumers route the event by it)."""
        event: RichAgentStreamEvent[Any]
        timestamp: datetime = field(default_factory=get_now)

    # Signal emitted for events produced outside a run (see emit_event)
    background_event: Signal[BackgroundEvent] = Signal()
    agent_reset = Signal[AgentReset]()
    # Signal emitted when agent execution fails
    run_failed: Signal[RunFailedEvent] = Signal()
    state_updated: Signal[StateUpdate] = Signal()
    # Signal emitted when agent is interrupted
    interrupted: Signal[InterruptEvent] = Signal()

    def __init__(
        self,
        *,
        name: str = "agent",
        deps_type: type[TDeps] | None = None,
        description: str | None = None,
        display_name: str | None = None,
        mcp_servers: Sequence[str | MCPServerConfig] | None = None,
        agent_pool: AgentPool[Any] | None = None,
        enable_logging: bool = True,
        event_configs: Sequence[EventConfig] | None = None,
        # New shared parameters
        env: ExecutionEnvironment | StrPath | None = None,
        input_provider: InputProvider | None = None,
        output_type: type[TResult] = str,  # type: ignore[assignment]  # ty:ignore[invalid-parameter-default]
        event_handlers: Sequence[AnyEventHandlerType] | None = None,
        commands: Sequence[BaseCommand] | None = None,
        hooks: AgentHooks | None = None,
        storage: StorageManager | None = None,
    ) -> None:
        """Initialize base agent with shared infrastructure.

        Args:
            name: Agent name
            deps_type: Type of dependencies to use
            description: Agent description
            display_name: Human-readable display name
            mcp_servers: MCP server configurations
            agent_pool: Agent pool for coordination
            enable_logging: Whether to enable database logging
            event_configs: Event trigger configurations
            env: Execution environment, or a path (str/PathLike) to use as cwd
                for a LocalExecutionEnvironment
            input_provider: Provider for user input and confirmations
            output_type: Output type for this agent
            event_handlers: Event handlers for this agent
            commands: Slash commands to register with this agent
            hooks: Agent hooks for intercepting agent behavior at run and tool events
            storage: Optional per-agent StorageManager. Falls back to pool.storage if not provided.
        """
        from exxec import ExecutionEnvironment, LocalExecutionEnvironment
        from slashed import CommandStore

        from agentpool_commands import get_commands

        super().__init__(
            name=name,
            description=description,
            display_name=display_name,
            mcp_servers=mcp_servers,
            agent_pool=agent_pool,
            enable_logging=enable_logging,
            event_configs=event_configs,
            storage=storage,
        )
        self._infinite = False
        self.deps_type = deps_type  # or type(None)
        self._background_task: asyncio.Task[ChatMessage[Any]] | None = None
        self._env: ExecutionEnvironment
        match env:
            case ExecutionEnvironment():
                self._env = env
            case str() | os.PathLike() | None:
                self._env = LocalExecutionEnvironment(cwd=str(env) if env is not None else None)
            case _ as unreachable:
                assert_never(unreachable)
        from agentpool.sessions import Session

        self._default_session: Session = Session.create(
            agent_name=self.name,
            cwd=self.env.cwd,
            agent_type=self.AGENT_TYPE,
            storage=self.storage,
        )
        self._default_runtime: SessionRuntime[TDeps, TResult] = SessionRuntime(
            self, self._default_session, default=True
        )
        """Runtime of the default session (runs without an explicit ``session``)."""
        self._runtimes: dict[str, SessionRuntime[TDeps, TResult]] = {}
        """Runtimes of explicit sessions by session ID, while they run or have input waiting."""
        self._own_sdk_session_id: str | None = None
        """The backend's own session of the default session (see :attr:`_sdk_session_id`)."""
        self._session_turn_lock = asyncio.Lock()
        """Serializes turns of different sessions where they can't run concurrently."""
        self._input_provider = input_provider
        self._output_type: type[TResult] = output_type
        self.tools = ToolManager()
        handlers = resolve_event_handlers(event_handlers)
        self.event_handler: MultiEventHandler[IndividualEventHandler] = MultiEventHandler(handlers)
        self.hooks = hooks or AgentHooks()
        # Deferred initialization support - subclasses set True in __aenter__,
        # override ensure_initialized() to do actual connection
        self._command_store = CommandStore(commands=[*get_commands(), *(commands or [])])
        # Initialize store (registers builtin help/exit commands)
        self._command_store._initialize_sync()
        # Internal filesystem for tool/session state (can get written to via AgentContext)
        self._internal_fs = IsolatedMemoryFileSystem()

    # --- Runtimes (one per session; see agentpool.agents.runtime) ----------------------

    @property
    def _runtime(self) -> SessionRuntime[TDeps, TResult]:
        """Runtime of this agent's turn running in this context, else of the default session."""
        active = active_runtime(self)
        return active if active is not None else self._default_runtime

    def runtime(self, session: Session | None = None) -> SessionRuntime[TDeps, TResult]:
        """The runtime of ``session`` on this agent (the default session's when None).

        Created on use, and dropped once its turns are over with nothing waiting.
        """
        if session is None:
            return self._default_runtime
        runtime = self._runtimes.get(session.session_id)
        if runtime is None:
            runtime = SessionRuntime(self, session)
            self._runtimes[session.session_id] = runtime
        else:
            runtime.session = session  # E.g. rebuilt from storage after the registry dropped it
        return runtime

    def _runtimes_of(self, session_id: str | None) -> list[SessionRuntime[TDeps, TResult]]:
        """Runtimes a caller outside a run means: that of ``session_id``, else the active one.

        Without ``session_id`` and outside a run of this agent: every runtime with a turn
        running (or the default session's), so a plain ``interrupt()`` stops whatever runs.
        """
        if session_id is not None:
            if (runtime := self._runtimes.get(session_id)) is not None:
                return [runtime]
            return [self._default_runtime] if session_id == self.session_id else []
        if (active := active_runtime(self)) is not None:
            return [active]
        running = [r for r in (self._default_runtime, *self._runtimes.values()) if r.active_runs]
        return running or [self._default_runtime]

    def session_turn(self) -> AbstractAsyncContextManager[Any]:
        """Hold this while running a turn for one of several sessions sharing this instance.

        Agents with ``capabilities.concurrent_sessions`` (native agents) run each session on
        its own history and turn state, so there is nothing to hold. Others have one
        native session per instance: their turns of different sessions run one at a time.
        """
        if self.capabilities.concurrent_sessions:
            return nullcontext()
        return self._session_turn_lock

    @contextmanager
    def use_session(self, session: Session | None) -> Generator[None]:
        """Run the block as part of ``session``'s turns on this agent.

        For callers that act for a session outside ``run_stream``, such as slash commands
        staging content for the session's next prompt.
        """
        with activate(self, self.runtime(session)):
            yield

    @property
    def _explicit_session(self) -> Session | None:
        """The session of this agent's turn in this context, unless it's the default one.

        Agents that keep settings per session (native agents' config options) store them
        on it; with the default session they stay on the instance, as before sessions.
        """
        active = active_runtime(self)
        return active.session if active is not None and not active.is_default else None

    @property
    def env(self) -> ExecutionEnvironment:
        """Where the agent's tools run: the acting session's environment, else its own.

        A protocol server sets the environment of the session it acts for (its client's
        file system and terminals; see :mod:`agentpool.sessions.environment`).
        """
        if (environment := current_session_environment()) is not None:
            return environment.env
        return self._env

    @env.setter
    def env(self, value: ExecutionEnvironment) -> None:
        self._env = value

    @property
    def _sdk_session_id(self) -> str | None:
        """The backend's own session (Claude Code session, Codex or T3 thread, ...).

        Per session: within a turn of an explicit session it is that session's
        (``SessionData.sdk_session_id``), so agents with ``concurrent_sessions`` keep one
        backend session per session; otherwise the agent's own.
        """
        if (session := self._explicit_session) is not None:
            return session.sdk_session_id
        return self._own_sdk_session_id

    @_sdk_session_id.setter
    def _sdk_session_id(self, value: str | None) -> None:
        if (session := self._explicit_session) is not None:
            session.set_sdk_session_id(value)
        else:
            self._own_sdk_session_id = value

    def _session_mode(self, option_id: str) -> str | None:
        """The value of config option ``option_id`` set for the session acting, if any."""
        session = self._explicit_session
        return session.overrides.modes.get(option_id) if session is not None else None

    def _set_session_mode(self, option_id: str, value: str) -> bool:
        """Set ``option_id`` for the session acting in this context; False without one.

        Agents that run sessions concurrently keep their settings this way: within
        ``use_session`` (or a session's turn) per session, otherwise on the agent.
        """
        if (session := self._explicit_session) is None:
            return False
        modes = {**session.overrides.modes, option_id: value}
        session.overrides = session.overrides.model_copy(update={"modes": modes})
        return True

    def _session_model(self) -> str | None:
        """The model set for the session acting, if any."""
        session = self._explicit_session
        return session.overrides.model if session is not None else None

    def _set_session_model(self, model: str) -> bool:
        """Set the model of the session acting in this context; False without one."""
        if (session := self._explicit_session) is None:
            return False
        session.overrides = session.overrides.model_copy(update={"model": model})
        return True

    @property
    def staged_content(self) -> StagedContent:
        """Content for the next prompt of the active turn's session."""
        return self._runtime.staged

    @property
    def _cancelled(self) -> bool:
        return self._runtime.cancelled

    @_cancelled.setter
    def _cancelled(self, value: bool) -> None:
        self._runtime.cancelled = value

    @property
    def _current_stream_task(self) -> asyncio.Task[Any] | None:
        return self._runtime.task

    @_current_stream_task.setter
    def _current_stream_task(self, value: asyncio.Task[Any] | None) -> None:
        self._runtime.task = value

    @property
    def _active_runs(self) -> int:
        """Runs of the active turn's session that are streaming."""
        return self._runtime.active_runs

    @_active_runs.setter
    def _active_runs(self, value: int) -> None:
        self._runtime.active_runs = value

    @property
    def _injection_manager(self) -> PromptInjectionManager:
        return self._runtime.injections

    @property
    def _event_queue(self) -> asyncio.Queue[RichAgentStreamEvent[Any]]:
        """Events of tools and subagents for the active turn's stream."""
        return self._runtime.events

    @property
    def capabilities(self) -> AgentCapabilities:
        """Declared native capabilities of this agent implementation."""
        return type(self).CAPABILITIES

    @property
    def session(self) -> Session:
        """Default :class:`Session` owning this agent's implicit conversation state."""
        if self.session_id is not None and self._default_session.session_id != self.session_id:
            self._default_session.set_session_id(self.session_id)
        return self._default_session

    @property
    def active_session(self) -> Session:
        """The :class:`Session` of this agent's turn running in this context, else the default.

        The context variable holds the session of the innermost running turn, which can
        belong to another agent (e.g. a subagent's turn); only this agent's turns count.
        """
        if (active := active_runtime(self)) is not None and not active.is_default:
            return active.session
        return self.session

    @property
    def conversation(self) -> MessageHistory:
        """Conversation history of the agent's default session."""
        return self._default_session.history

    @conversation.setter
    def conversation(self, value: MessageHistory) -> None:
        self._default_session.history = value

    @property
    def _acting_session_id(self) -> str | None:
        """ID of the session acting: the explicit session's, else the agent's own."""
        session = self._explicit_session
        return session.session_id if session is not None else self.session_id

    @property
    def _history(self) -> MessageHistory:
        """Conversation history of the session acting (see :meth:`use_session`)."""
        session = self._explicit_session
        return session.history if session is not None else self.conversation

    def __repr__(self) -> str:
        typ = self.__class__.__name__
        desc = f", {self.description!r}" if self.description else ""
        return f"{typ}({self.name!r}, model={self.model_name!r}{desc})"

    async def __prompt__(self) -> str:
        typ = self.__class__.__name__
        model = self.model_name or "default"
        parts = [f"Agent: {self.name}", f"Type: {typ}", f"Model: {model}"]
        if self.description:
            parts.append(f"Description: {self.description}")
        parts.extend([await self.tools.__prompt__(), self.conversation.__prompt__()])
        return "\n".join(parts)

    @overload
    def __and__(  # if other doesnt define deps, we take the agents one
        self, other: ProcessorCallback[Any] | MessageNode[TDeps, Any]
    ) -> Team[TDeps]: ...

    @overload
    def __and__(  # otherwise, we dont know and deps is Any
        self, other: ProcessorCallback[Any] | MessageNode[Any, Any]
    ) -> Team[Any]: ...

    def __and__(self, other: MessageNode[Any, Any] | ProcessorCallback[Any]) -> Team[Any]:
        """Create sequential team using & operator.

        Example:
            group = analyzer & planner & executor  # Create group of 3
            group = analyzer & existing_group  # Add to existing group
        """
        from agentpool.agents.native_agent import Agent
        from agentpool.delegation.team import Team

        match other:
            case Team():
                return Team([self, *other.nodes])
            case Callable():
                agent_2 = Agent.from_callback(other, agent_pool=self.agent_pool)
                return Team([self, agent_2])
            case MessageNode():
                return Team([self, other])
            case _:
                raise ValueError(f"Invalid agent type: {type(other)}")

    @overload
    def __or__(self, other: MessageNode[TDeps, Any]) -> TeamRun[TDeps, Any]: ...

    @overload
    def __or__[TOtherDeps](self, other: MessageNode[TOtherDeps, Any]) -> TeamRun[Any, Any]: ...

    @overload
    def __or__(self, other: ProcessorCallback[Any]) -> TeamRun[Any, Any]: ...

    def __or__(self, other: MessageNode[Any, Any] | ProcessorCallback[Any]) -> TeamRun[Any, Any]:
        # Create new execution with sequential mode (for piping)
        from agentpool import TeamRun
        from agentpool.agents.native_agent import Agent

        if callable(other):
            other = Agent.from_callback(other, agent_pool=self.agent_pool)

        return TeamRun([self, other])

    async def update_state(
        self, config_id: str, value_id: str | bool, *, session_id: str | None = None
    ) -> None:
        """Announce that config option ``config_id`` is now ``value_id`` (``state_updated``).

        Args:
            config_id: The option that changed
            value_id: Its new value
            session_id: The session whose option it is; by default the session acting
                (None outside one: the agent's own)
        """
        from agentpool.agents.modes import ConfigOptionChanged

        if session_id is None and (session := self._explicit_session) is not None:
            session_id = session.session_id
        self.log.info("Config option changed", config_id=config_id, mode=value_id)
        change = ConfigOptionChanged(config_id=config_id, value_id=value_id, session_id=session_id)
        await self.state_updated.emit(change)

    @property
    def command_store(self) -> CommandStore:
        """Get the command store for slash commands."""
        return self._command_store

    def _is_local_command(self, text: str) -> bool:
        """Whether ``text`` is a slash command the agent's command store runs."""
        if (match := _SLASH_PATTERN.match(text.strip())) is None:
            return False
        name = match.group(1)
        return self._command_store.get_command(name) is not None and not self.is_backend_command(
            name
        )

    def is_backend_command(self, name: str) -> bool:
        """Whether the agent's backend runs command ``name`` from the prompt text.

        Such commands (e.g. the Claude Code CLI's, a remote ACP agent's) are listed in
        :attr:`command_store`, but servers send ``/name args`` as a prompt, so they run as a
        turn with tools, permissions and events instead of a command without them.
        """
        return False

    @property
    def internal_fs(self) -> IsolatedMemoryFileSystem:
        """Get the internal filesystem for tool/session state.

        Tools can use this to store logs, history, temporary files, etc.
        Access via AgentContext.fs in tool implementations.

        Returns:
            In-memory filesystem scoped to this agent
        """
        return self._internal_fs

    @property
    def overlay_fs(self) -> OverlayFileSystem:
        """Get unified filesystem view combining agent storage and VFS resources.

        Provides a layered filesystem where:
        - Writes go to the agent's internal filesystem (upper layer)
        - Reads fall through to VFS resources if not found locally

        Returns:
            OverlayFileSystem combining internal_fs and pool's VFS registry
        """
        from upathtools.filesystems import OverlayFileSystem

        # Build layers: internal_fs on top, VFS resources below
        layers: list[AbstractFileSystem] = [self._internal_fs]
        if self.agent_pool is not None and not self.agent_pool.vfs_registry.is_empty:
            layers.append(self.agent_pool.vfs_registry.get_fs())
        return OverlayFileSystem(filesystems=layers)

    async def reset(self) -> None:
        """Reset agent state (conversation history and tool states)."""
        await self.conversation.clear()
        await self.tools.reset_states()
        event = self.AgentReset(agent_name=self.name)
        await self.agent_reset.emit(event)

    async def start_new_session(self) -> None:
        """Detach from the current session so the next run starts a fresh conversation.

        Clears the conversation history and the active session ID. Agents backed by an
        external runtime with its own native session (Claude Code, Codex, ACP, ...) override
        this to also start a fresh native session instead of resuming the previous one.
        """
        from agentpool.sessions import SessionData, SessionOverrides
        from agentpool.utils.identifiers import generate_session_id

        await self.reset()
        self.session_id = None
        self._default_session.data = SessionData(
            session_id=generate_session_id(),
            agent_name=self.name,
            cwd=self.env.cwd,
            agent_type=self.AGENT_TYPE,
        )
        self._default_session.overrides = SessionOverrides()

    async def compact(
        self,
        pipeline: CompactionPipeline | None = None,
        *,
        session: Session | None = None,
    ) -> tuple[int, int]:
        """Compact the conversation of ``session`` (or the default session).

        Implemented where it changes what the model sees: native agents compact their
        history, Codex and OpenCode V2 their backend's context. Other agents keep the
        conversation in their backend, so compacting our copy would change nothing
        (``capabilities.native_compaction`` says which).

        Returns:
            ``(before_count, after_count)`` of messages in our history.

        Raises:
            UnsupportedOperationError: The agent cannot compact on demand.
        """
        from agentpool.agents.exceptions import UnsupportedOperationError

        reason = "its backend keeps the conversation; compacting our copy changes nothing"
        raise UnsupportedOperationError(self.name, "compact", reason)

    async def _compact_history(
        self, pipeline: CompactionPipeline | None, session: Session | None
    ) -> tuple[int, int]:
        """Compact our history of ``session`` and persist it (for :meth:`compact`)."""
        from agentpool.messaging.compaction import compact_conversation, summarizing_context

        target_history = session.history if session is not None else self.conversation
        target_sid = session.session_id if session is not None else self.session_id
        effective_pipeline = pipeline or summarizing_context()
        counts = await compact_conversation(effective_pipeline, target_history)
        if self.enable_db_logging and self.storage and target_sid:
            await self.storage.replace_conversation_messages(
                target_sid, target_history.get_history()
            )
        return counts

    async def fork_session(
        self,
        *,
        at_message_id: str | None = None,
        session: Session | None = None,
    ) -> Session:
        """Fork ``session`` (or the default session) into a new child :class:`Session`.

        Implemented where the fork continues from the forked conversation: native agents
        (our history), Codex and OpenCode V2 (their backend's session, forked too).
        Other agents would continue their original native session
        (``capabilities.native_fork`` says which).

        Args:
            at_message_id: Copy history up to this message (inclusive); all when omitted.
            session: The session to fork; the agent's default session when omitted.

        Raises:
            UnsupportedOperationError: The agent cannot fork, or not at that message.
        """
        from agentpool.agents.exceptions import UnsupportedOperationError

        reason = "its backend's session would not be forked"
        raise UnsupportedOperationError(self.name, "fork a session", reason)

    async def _fork_history(self, at_message_id: str | None, session: Session | None) -> Session:
        """Fork our history of ``session``, register and persist it (for :meth:`fork_session`)."""
        source = session if session is not None else self.session
        forked = source.fork(at_message_id=at_message_id, agent_name=self.name)
        if self.agent_pool is not None:
            self.agent_pool.sessions.register(forked)
        if self.enable_db_logging and self.storage:
            await self.storage.log_session(
                session_id=forked.session_id,
                node_name=self.name,
                model=forked.overrides.model or self.model_name,
                agent_type=self.AGENT_TYPE,
            )
            if messages := forked.history.get_history():
                await self.storage.replace_conversation_messages(forked.session_id, messages)
        return forked

    def get_context(
        self,
        data: Any = None,
        input_provider: InputProvider | None = None,
        tool_call_id: str | None = None,
        tool_input: dict[str, Any] | None = None,
        tool_name: str | None = None,
    ) -> AgentContext[Any]:
        """Create a new context for this agent.

        Args:
            data: Optional custom data to attach to the context
            input_provider: Optional input provider override
            tool_call_id: Optional tool call ID
            tool_input: Optional tool input
            tool_name: Optional tool name

        Returns:
            A new AgentContext instance
        """
        from agentpool.agents.context import AgentContext

        return AgentContext(
            node=self,
            pool=self.agent_pool,
            input_provider=input_provider or self._input_provider,
            data=data,
            model_name=self.model_name,
            tool_call_id=tool_call_id,
            tool_input=tool_input or {},
            tool_name=tool_name,
            runtime=active_runtime(self),
        )

    @property
    @abstractmethod
    def model_name(self) -> str | None:
        """Get the model name used by this agent."""
        ...

    @abstractmethod
    async def set_model(self, model: str) -> None:
        """Set the model for this agent.

        Args:
            model: New model identifier to use
        """
        ...

    async def run_iter(
        self,
        *prompt_groups: Sequence[PromptCompatible],
        store_history: bool = True,
        wait_for_connections: bool | None = None,
    ) -> AsyncIterator[ChatMessage[TResult]]:
        """Run agent sequentially on multiple prompt groups.

        Args:
            prompt_groups: Groups of prompts to process sequentially
            store_history: Whether to store in conversation history
            wait_for_connections: Whether to wait for connected agents

        Yields:
            Response messages in sequence

        Example:
            questions = [
                ["What is your name?"],
                ["How old are you?", image1],
                ["Describe this image", image2],
            ]
            async for response in agent.run_iter(*questions):
                print(response.content)
        """
        for prompts in prompt_groups:
            response = await self.run(
                *prompts,
                store_history=store_history,
                wait_for_connections=wait_for_connections,
            )
            yield response

    async def run_in_background(
        self,
        *prompt: PromptCompatible,
        max_count: int | None = None,
        interval: float = 1.0,
        **kwargs: Any,
    ) -> asyncio.Task[ChatMessage[TResult] | None]:
        """Run agent continuously in background with prompt or dynamic prompt function.

        Args:
            prompt: Static prompt or function that generates prompts
            max_count: Maximum number of runs (None = infinite)
            interval: Seconds between runs
            **kwargs: Arguments passed to run()
        """
        self._infinite = max_count is None

        async def _continuous() -> ChatMessage[Any]:
            count = 0
            self.log.debug("Starting continuous run", max_count=max_count, interval=interval)
            latest = None
            while (max_count is None or count < max_count) and not self._cancelled:
                try:
                    agent_ctx = self.get_context(input_provider=kwargs.get("input_provider"))
                    current_prompts = [
                        call_with_context(p, agent_ctx, **kwargs) if callable(p) else p
                        for p in prompt
                    ]
                    self.log.debug("Generated prompt", iteration=count)
                    latest = await self.run(current_prompts, **kwargs)
                    self.log.debug("Run continuous result", iteration=count)

                    count += 1
                    await anyio.sleep(interval)
                except asyncio.CancelledError:
                    self.log.debug("Continuous run cancelled")
                    break
                except Exception:
                    # Check if we were cancelled (may surface as other exceptions)
                    if self._cancelled:
                        self.log.debug("Continuous run cancelled via flag")
                        break
                    count += 1
                    self.log.exception("Background run failed")
                    await anyio.sleep(interval)
            self.log.debug("Continuous run completed", iterations=count)
            return latest  # type: ignore[return-value]  # ty:ignore[invalid-explicit-return]

        await self.stop()  # Cancel any existing background task
        self._cancelled = False  # Reset cancellation flag for new run
        task = asyncio.create_task(_continuous(), name=f"background_{self.name}")
        self.log.debug("Started background task", task_name=task.get_name())
        self._background_task = task
        return task

    async def stop(self) -> None:
        """Stop continuous execution if running."""
        self._cancelled = True  # Signal cancellation via flag
        if self._background_task and not self._background_task.done():
            self._background_task.cancel()
            with suppress(asyncio.CancelledError):  # Expected when we cancel the task
                await self._background_task
            self._background_task = None

    def is_busy(self) -> bool:
        """Check if agent is currently processing tasks."""
        return bool(self.task_manager._pending_tasks or self._background_task)

    async def emit_event(self, event: RichAgentStreamEvent[Any]) -> None:
        """Deliver an event produced by tools, subagents or the backend.

        While a run is streaming, the event joins its stream. Otherwise (e.g. a background
        task finishing after its turn) it is emitted as :attr:`background_event`, instead
        of waiting for the next run.
        """
        if self._active_runs:
            await self._event_queue.put(event)
            return
        background = BaseAgent.BackgroundEvent(
            agent_name=self.name, session_id=self.session_id, event=event
        )
        await self.background_event.emit(background)

    async def wait(self) -> ChatMessage[TResult]:
        """Wait for background execution to complete."""
        if not self._background_task:
            raise RuntimeError("No background task running")
        if self._infinite:
            raise RuntimeError("Cannot wait on infinite execution")
        try:
            return await self._background_task
        finally:
            self._background_task = None

    def queue_prompt(self, *prompts: PromptCompatible, session_id: str | None = None) -> None:
        """Queue a prompt to be processed after the current run completes.

        When called during an active run_stream, the queued prompt will be
        processed in a continuation loop without exiting the stream. This
        allows tools or external code to schedule follow-up work.

        Args:
            *prompts: Prompts to queue (same format as run/run_stream)
            session_id: The session whose turn to queue for (from outside its run; see
                :meth:`interrupt`)

        Example:
            # In a tool implementation:
            async def my_tool(ctx: AgentContext) -> str:
                ctx.agent.queue_prompt("Now analyze the results")
                return "Initial work done"
        """
        for runtime in self._runtimes_of(session_id)[:1]:
            runtime.injections.queue(*prompts)

    def inject_prompt(self, message: str) -> None:
        """Inject context into the conversation mid-run (for tools; user input: :meth:`steer`).

        The message will be injected after the next tool completes (if the
        agent supports tool hooks). If no tool executes before the run
        iteration completes, the message is automatically queued for the
        next iteration.

        Args:
            message: Message to inject

        Example:
            # In a tool implementation:
            async def my_tool(ctx: AgentContext) -> str:
                ctx.agent.inject_prompt("Also check the test coverage")
                return "Changes made"
        """
        self._injection_manager.inject(message)

    async def steer(
        self, message: str, *, interrupt: bool = False, session_id: str | None = None
    ) -> None:
        """Deliver user input into the running turn.

        Unlike :meth:`inject_prompt` (context from a tool, added after it completes),
        this is input from the user. It reaches the model at the next step boundary
        (after the running tool call), or at once with ``interrupt``, which ends the
        generation in progress (where the backend supports it). Agents whose backend
        takes messages mid-turn override this; the default injects the message after the
        next tool call, or runs it once the turn ends.

        Args:
            message: The user's message
            interrupt: Cut into the generation in progress instead of waiting for the
                next step boundary
            session_id: The session whose turn to steer (from outside its run; see
                :meth:`interrupt`)
        """
        for runtime in self._runtimes_of(session_id)[:1]:
            runtime.injections.inject(message)

    def has_queued_prompts(self) -> bool:
        """Check if there are queued prompts waiting to be processed."""
        return self._injection_manager.has_queued()

    def has_pending_injections(self) -> bool:
        """Check if there are pending injections."""
        return self._injection_manager.has_pending()

    def clear_queued_prompts(self) -> None:
        """Clear all queued prompts and pending injections."""
        self._injection_manager.clear()

    @method_spawner
    async def run_stream(
        self,
        *prompts: PromptCompatible,
        store_history: bool = True,
        message_id: str | None = None,
        session: Session | None = None,
        session_id: str | None = None,
        parent_id: str | None = None,
        message_history: MessageHistory | None = None,
        input_provider: InputProvider | None = None,
        wait_for_connections: bool | None = None,
        deps: TDeps | None = None,
        event_handlers: Sequence[AnyEventHandlerType] | None = None,
    ) -> AsyncIterator[RichAgentStreamEvent[TResult]]:
        """Run agent with streaming output.

        Runs as a turn of ``session`` (the agent's default session when omitted), in that
        session's runtime (:meth:`runtime`).
        If prompts are queued via queue_prompt() during execution, they will be
        processed in sequence without exiting the stream.

        Args:
            *prompts: Input prompts (various formats supported)
            store_history: Whether to store in history
            message_id: Optional message ID
            session: Explicit :class:`Session` owning history and overrides for this run.
                Leaves the agent's default session untouched.
            session_id: Optional conversation ID
            parent_id: Optional parent message ID
            message_history: Optional message history
            input_provider: Optional input provider
            wait_for_connections: Whether to wait for connected agents
            deps: Optional dependencies
            event_handlers: Optional event handlers

        Yields:
            Stream events during execution
        """
        async for event in self.runtime(session).run_stream(
            *prompts,
            store_history=store_history,
            message_id=message_id,
            session_id=session_id,
            parent_id=parent_id,
            message_history=message_history,
            input_provider=input_provider,
            wait_for_connections=wait_for_connections,
            deps=deps,
            event_handlers=event_handlers,
        ):
            yield event

    async def _execute_slash_command_streaming(
        self, command_text: str
    ) -> AsyncIterator[CommandOutputEvent | CommandCompleteEvent]:
        """Execute a single slash command and yield events as they happen.

        Args:
            command_text: Full command text including slash

        Yields:
            Command output and completion events
        """
        from slashed import CommandExecutedEvent, CommandOutputEvent as SlashedCommandOutputEvent

        from agentpool.agents.events import CommandCompleteEvent, CommandOutputEvent

        parsed = _parse_slash_command(command_text)
        if not parsed:
            self.log.warning("Invalid slash command", command=command_text)
            yield CommandCompleteEvent(command="unknown", success=False)
            return

        cmd_name, args = parsed
        # Set up event queue for this command execution
        event_queue: asyncio.Queue[Any] = asyncio.Queue()
        # Temporarily set event handler on command store
        old_handler = self._command_store.event_handler
        self._command_store.event_handler = event_queue.put
        cmd_ctx = self._command_store.create_context(data=self.get_context())
        command_str = f"{cmd_name} {args}".strip()
        try:
            coro = self._command_store.execute_command(command_str, cmd_ctx)
            execute_task = asyncio.create_task(coro)
            success = True
            # Yield events from queue as command runs
            while not execute_task.done():
                try:
                    event = await asyncio.wait_for(event_queue.get(), timeout=0.1)
                    match event:
                        case SlashedCommandOutputEvent(output=output):
                            yield CommandOutputEvent(command=cmd_name, output=output)
                        case CommandExecutedEvent(success=False, error=error) if error:
                            yield CommandOutputEvent(
                                command=cmd_name, output=f"Command error: {error}"
                            )
                            success = False
                except TimeoutError:
                    continue

            # Ensure command task completes and handle any remaining events
            try:
                await execute_task
            except Exception as e:
                self.log.exception("Command execution failed", command=cmd_name)
                success = False
                yield CommandOutputEvent(command=cmd_name, output=f"Command error: {e}")

            # Drain any remaining events from queue
            while not event_queue.empty():
                try:
                    event = event_queue.get_nowait()
                    if isinstance(event, SlashedCommandOutputEvent):
                        yield CommandOutputEvent(command=cmd_name, output=event.output)
                except asyncio.QueueEmpty:
                    break

            yield CommandCompleteEvent(command=cmd_name, success=success)

        finally:
            self._command_store.event_handler = old_handler

    async def run_stream_with_commands(
        self,
        *prompts: PromptCompatible,
        **kwargs: Any,
    ) -> AsyncIterator[StreamWithCommandsEvent[TResult]]:
        """Run agent with slash command support.

        Separates the agent's slash commands from regular prompts, executes them first,
        then processes remaining content through the agent. Commands the agent's backend
        runs itself (:meth:`is_backend_command`) and unknown ones stay in the prompt.

        Args:
            *prompts: Input prompts (may include slash commands)
            **kwargs: Additional arguments passed to run_stream

        Yields:
            Command events from slash command execution, then stream events from agent
        """
        # Separate slash commands from regular content
        commands: list[str] = []
        regular_prompts: list[Any] = []

        for prompt in prompts:
            if isinstance(prompt, str) and self._is_local_command(prompt):
                self.log.debug("Found slash command", command=prompt)
                commands.append(prompt.strip())
            else:
                regular_prompts.append(prompt)

        # Execute all commands first with streaming
        for command in commands:
            self.log.info("Processing slash command", command=command)
            async for cmd_event in self._execute_slash_command_streaming(command):
                yield cmd_event

        # If we have regular content, process it through the agent
        if regular_prompts:
            self.log.debug("Processing prompts through agent", num_prompts=len(regular_prompts))
            async for event in self.run_stream(*regular_prompts, **kwargs):
                yield event

    @abstractmethod
    def _stream_events(
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
        """Agent-specific streaming implementation.

        Subclasses must implement this to provide their streaming logic.
        Prompts are pre-converted to UserContent format by run_stream().

        Args:
            prompts: Converted prompts in UserContent format
            user_msg: Pre-created user ChatMessage (from base class)
            effective_parent_id: Resolved parent message ID for threading
            message_id: Optional message ID
            session_id: Optional conversation ID
            parent_id: Optional parent message ID
            input_provider: Optional input provider
            message_history: Optional message history
            deps: Optional dependencies
            wait_for_connections: Whether to wait for connected agents
            store_history: Whether to store in history

        Yields:
            Stream events during execution
        """
        ...

    async def set_tool_confirmation_mode(self, mode: str) -> None:
        """Set the tool confirmation mode, in the agent's own values.

        Each agent type handles permission modes differently:
        - Agent / AGUIAgent: tool confirmation ("always", "never", "per_tool")
        - ClaudeCodeAgent: permission mode ("default", "acceptEdits", "plan", "bypassPermissions")
        - CodexAgent: approval policy ("never", "on-request", "untrusted")
        - ACPAgent: a mode ID of the remote agent

        The default implementation sets the ``mode`` config option (``_set_mode``).

        Args:
            mode: Mode value in the agent's native format
        """
        await self._set_mode(mode, "mode")

    async def ensure_initialized(self) -> None:
        """Wait for deferred initialization to complete.

        Agents that connect to their backend in the background (Claude Code starts its
        CLI after ``__aenter__`` returns) override this to wait for it. The base
        implementation is a no-op.
        """

    def is_cancelled(self) -> bool:
        """Check if the agent has been cancelled."""
        return self._cancelled

    async def interrupt(self, session_id: str | None = None) -> None:
        """Interrupt the running turn of ``session_id``.

        Without ``session_id``: the turn running in this context, else whatever runs on
        this agent. Sets the turn's cancelled flag, calls the subclass's ``_interrupt()``
        with that turn active (so it cancels that turn's work), and emits the
        interrupted signal.
        """
        runtimes = self._runtimes_of(session_id)
        for runtime in runtimes:
            await runtime.interrupt()
        if runtimes:
            await self.interrupted.emit(self.InterruptEvent(agent_name=self.name))
            logger.info("Agent interrupted", agent=self.name, session_id=session_id)

    @abstractmethod
    async def _interrupt(self) -> None:
        """Subclass-specific interrupt implementation."""

    async def get_stats(self) -> MessageStats:
        """Get message statistics."""
        from agentpool.talk.stats import MessageStats

        return MessageStats(messages=list(self.conversation.chat_messages))

    async def get_mcp_server_info(self) -> dict[str, MCPServerStatus]:
        """Get information about configured MCP servers.

        Returns a dict mapping server names to their status info. Used by
        the OpenCode /mcp endpoint to display MCP servers in the UI.

        The default implementation checks external_providers on the tool manager.
        Subclasses may override to provide agent-specific MCP server info
        (e.g., ClaudeCodeAgent has its own MCP server handling).

        Returns:
            Dict mapping server name to MCPServerStatus
        """
        return await self.tools.get_mcp_server_info()

    @method_spawner
    async def run(
        self,
        *prompts: PromptCompatible | ChatMessage[Any],
        store_history: bool = True,
        message_id: str | None = None,
        session: Session | None = None,
        session_id: str | None = None,
        parent_id: str | None = None,
        message_history: MessageHistory | None = None,
        deps: TDeps | None = None,
        input_provider: InputProvider | None = None,
        event_handlers: Sequence[AnyEventHandlerType] | None = None,
        wait_for_connections: bool | None = None,
    ) -> ChatMessage[TResult]:
        """Run agent with prompt and get response.

        Shared by all agent types: collects the events of run_stream() and returns the
        final message.

        Args:
            prompts: User query or instruction
            store_history: Whether the message exchange should be added to the
                            context window
            message_id: Optional message id for the returned message.
                        Automatically generated if not provided.
            session: Explicit :class:`Session` owning history and overrides for this run.
            session_id: Optional conversation id for the returned message.
            parent_id: Parent message id
            message_history: Optional MessageHistory object to
                             use instead of agent's own conversation
            deps: Optional dependencies for the agent
            input_provider: Optional input provider for the agent
            event_handlers: Optional event handlers for this run (overrides agent's handlers)
            wait_for_connections: Whether to wait for connected agents to complete

        Returns:
            ChatMessage containing response and run information

        Raises:
            RuntimeError: If no final message received from stream
            UnexpectedModelBehavior: If the model fails or behaves unexpectedly
        """
        # Collect all events through run_stream
        final_message: ChatMessage[TResult] | None = None
        async for event in self.run_stream(
            *prompts,
            store_history=store_history,
            message_id=message_id,
            session=session,
            session_id=session_id,
            parent_id=parent_id,
            message_history=message_history,
            deps=deps,
            input_provider=input_provider,
            event_handlers=event_handlers,
            wait_for_connections=wait_for_connections,
        ):
            if isinstance(event, StreamCompleteEvent):
                final_message = event.message

        if final_message is None:
            raise RuntimeError("No final message received from stream")

        return final_message

    @abstractmethod
    async def get_available_models(self) -> list[ModelInfo] | None:
        """Get available models for this agent.

        Returns a list of models that can be used with this agent, or None
        if model discovery is not supported for this agent type.

        Uses tokonomics.ModelInfo which includes pricing, capabilities,
        and limits. Can be converted to protocol-specific formats (OpenCode, ACP).

        Returns:
            List of tokonomics ModelInfo, or None if not supported
        """
        ...

    @property
    def config_options(self) -> Sequence[ConfigOption[Self]]:
        """The switchable settings of this agent, in display order.

        ``get_modes()`` and ``set_mode()`` work from this list. Agents whose options
        come from elsewhere (e.g. a remote ACP server) override those methods instead.
        """
        return ()

    async def get_modes(self) -> list[ModeCategory]:
        """Get available mode categories for this agent.

        Returns a list of mode categories that can be switched. Each category
        represents a group of mutually exclusive modes (e.g., permissions,
        models, behavior presets).

        Built from :attr:`config_options` (e.g. Claude Code: permission mode, model,
        reasoning effort, fast mode). ACPAgent passes through the remote agent's modes.

        Returns:
            List of ModeCategory, empty list if no modes supported
        """
        categories = [await option.describe(self) for option in self.config_options]
        return [c for c in categories if c is not None]

    async def get_auth_status(self) -> AuthStatus:
        """Get the authentication status of the agent backend.

        Defaults to ``not_required``: credentials come from config/env, there is no sign-in.
        """
        # TODO: AGUIAgent inherits this, though ``unknown`` may fit a remote endpoint better.
        from agentpool.agents.auth import AuthStatus

        return AuthStatus(state="not_required")

    async def get_usage_limits(self) -> UsageLimits | None:
        """Get the usage limits of the plan the backend runs on.

        Returns:
            The plan's windows, or None if the backend has no plan limits (API keys,
            local models) or cannot report them.
        """
        return None

    @overload
    async def set_mode(self, mode: ModeInfo) -> None: ...

    @overload
    async def set_mode(self, mode: str | bool, category_id: ModeCategoryId | str) -> None: ...

    async def set_mode(
        self, mode: ModeInfo | str | bool, category_id: ModeCategoryId | str | None = None
    ) -> None:
        """Set a mode within a category.

        Args:
            mode: The mode to activate - either a ModeInfo object or mode ID string.
            category_id: Category ID. Required if mode is a string, optional if ModeInfo.
        """
        if isinstance(mode, ModeInfo):
            mode_id = mode.value
            resolved_category = category_id or mode.category_id
        else:
            mode_id = mode
            if not category_id:
                raise ValueError("category_id is required when mode is a string")
            resolved_category = category_id

        if not resolved_category:
            raise ValueError("category_id could not be determined from ModeInfo")

        await self._set_mode(mode_id, resolved_category)

    async def _set_mode(self, mode_id: str | bool, category_id: str) -> None:
        """Apply ``mode_id`` to the config option ``category_id``."""
        from agentpool.agents.exceptions import UnknownCategoryError

        options = {option.id: option for option in self.config_options}
        if (option := options.get(category_id)) is None:
            raise UnknownCategoryError(category_id, list(options))
        await option.apply(self, mode_id)

    @abstractmethod
    async def list_sessions(
        self,
        *,
        cwd: str | None = None,
        limit: int | None = None,
    ) -> list[SessionData]:
        """List available sessions for this agent.

        Returns session information including session IDs, working directories,
        titles, and last update timestamps.

        Args:
            cwd: Filter sessions by working directory (optional)
            limit: Maximum number of sessions to return (optional)

        Returns:
            List of SessionData objects
        """
        ...

    @abstractmethod
    async def load_session(self, session_id: str) -> SessionData | None:
        """Load and restore a session by ID.

        Loads session data and restores the conversation history for the specified session.
        For agents that support session persistence, this switches the active session.

        Args:
            session_id: Unique identifier for the session to load

        Returns:
            SessionData if session was found and loaded, None otherwise
        """
        ...

    async def resume_session(self, session_id: str) -> SessionData | None:
        """Resume a session by ID without loading conversation history.

        Unlike load_session, this does NOT populate conversation.chat_messages.
        It restores the agent's internal state so the conversation can continue,
        but assumes the client already has the history (or doesn't need it).

        This is useful for:
        - Reconnecting after a disconnect
        - Automated workflows that don't need UI history
        - Faster session switching when history display isn't needed

        Default implementation calls load_session (subclasses may optimize).

        UNSTABLE: This feature is not part of the ACP spec yet.

        Args:
            session_id: Unique identifier for the session to resume

        Returns:
            SessionData if session was found and resumed, None otherwise
        """
        # Default: just delegate to load_session
        # Subclasses can override to skip history loading for efficiency
        return await self.load_session(session_id)

    async def load_rules(self, project_dir: str | None = None) -> None:
        """Stage the agent rules (see :meth:`read_rules`) for the agent's next prompt.

        For a server whose sessions share this agent, stage them per session instead
        (with :meth:`read_rules`): the agent's staged content goes to whichever turn
        runs next.

        Args:
            project_dir: Project directory to search for rules. Falls back to
                env.cwd if not provided.
        """
        if (rules := await self.read_rules(project_dir)) is not None:
            self.staged_content.add_text(rules)

    async def read_rules(self, project_dir: str | None = None) -> str | None:
        """Read agent rules from global and project locations.

        Searches for AGENTS.md/CLAUDE.md files in:
        1. Global: ~/.config/agentpool/AGENTS.md (user-wide rules)
        2. Project: {project_dir}/AGENTS.md (project-specific rules)

        Uses the agent's execution environment for filesystem access,
        making this work across local and remote (ACP) environments.

        Args:
            project_dir: Project directory to search for rules. Falls back to
                env.cwd if not provided.

        Returns:
            Both merged, or None when there are none.
        """
        from agentpool_config.resolution import get_global_config_dir

        effective_project_dir = project_dir or self.env.cwd
        fs = self.env.get_fs()
        rules_file_names = ("AGENTS.md", "CLAUDE.md")
        rules_parts: list[str] = []
        # 1. Global rules from config directory
        global_dir = get_global_config_dir()
        for name in rules_file_names:
            path = global_dir / name
            if path.is_file():
                try:
                    content = path.read_text(encoding="utf-8")
                    rules_parts.append(f"## Global Rules\n\n{content}")
                    logger.debug("Loaded global rules", path=str(path))
                except OSError:
                    logger.exception("Failed to read global rules", path=str(path))
                break

        # 2. Project rules - use provided dir, env.cwd, or current directory
        if effective_project_dir is None:
            effective_project_dir = str(Path.cwd())

        if effective_project_dir:
            for name in rules_file_names:
                rules_path = f"{effective_project_dir}/{name}"
                try:
                    if fs:
                        # Use filesystem abstraction (works for local and ACP)
                        if await fs._exists(rules_path):
                            content_bytes = await fs._cat_file(rules_path)
                            content = content_bytes.decode("utf-8")
                            rules_parts.append(f"## Project Rules\n\n{content}")
                            logger.debug("Loaded project rules", path=rules_path)
                            break
                    else:
                        # Fallback to direct file access
                        local_path = Path(rules_path)
                        if local_path.is_file():
                            content = local_path.read_text(encoding="utf-8")
                            rules_parts.append(f"## Project Rules\n\n{content}")
                            logger.debug("Loaded project rules", path=rules_path)
                            break
                except (OSError, UnicodeDecodeError):
                    logger.debug("No project rules found", path=rules_path)

        if not rules_parts:
            return None
        logger.info(
            "Read agent rules",
            global_rules=bool(any("Global" in p for p in rules_parts)),
            project_rules=bool(any("Project" in p for p in rules_parts)),
        )
        return "\n\n".join(rules_parts)
