"""Base class for message processing nodes."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Literal, Self, overload

from anyenv.signals import Signal

from agentpool.log import get_logger
from agentpool.messaging import ChatMessage
from agentpool.talk import AggregatedTalkStats
from agentpool.utils.tasks import TaskManager


if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from datetime import timedelta
    from types import TracebackType

    from agentpool.common_types import (
        AnyTransformFn,
        AsyncFilterFn,
        ProcessorCallback,
        QueueStrategy,
    )
    from agentpool.delegation import AgentPool
    from agentpool.messaging.context import NodeContext
    from agentpool.storage import StorageManager
    from agentpool.talk import Talk, TeamTalk
    from agentpool.talk.router import Router
    from agentpool.talk.stats import AggregatedMessageStats, MessageStats
    from agentpool.tools.base import FunctionTool
    from agentpool.ui.base import InputProvider
    from agentpool_config.events import EventConfig
    from agentpool_config.forward_targets import ConnectionSessionPolicy, ConnectionType
    from agentpool_config.mcp_server import MCPServerConfig


logger = get_logger(__name__)


class MessageNode[TDeps, TResult](ABC):
    """Base class for all message processing nodes."""

    message_received = Signal[ChatMessage[Any]]()
    """Signal emitted when node receives a message."""

    message_sent = Signal[ChatMessage[Any]]()
    """Signal emitted when node creates a message."""

    def __init__(
        self,
        name: str | None = None,
        description: str | None = None,
        display_name: str | None = None,
        mcp_servers: Sequence[str | MCPServerConfig] | None = None,
        agent_pool: AgentPool[Any] | None = None,
        enable_logging: bool = True,
        event_configs: Sequence[EventConfig] | None = None,
        storage: StorageManager | None = None,
    ) -> None:
        """Initialize message node."""
        super().__init__()
        self._storage = storage

        from agentpool.mcp_server.manager import MCPManager
        from agentpool.messaging.connection_manager import ConnectionManager

        self.task_manager = TaskManager()
        self._name = name or self.__class__.__name__
        self._display_name = display_name
        self.log = logger.bind(agent_name=self._name)
        self.agent_pool = agent_pool
        self.description = description
        self._router: Router | None = None
        self.connections = ConnectionManager(self)
        for config in event_configs or []:  # They run where its router's triggers run
            self.router.triggers.add(self, config)
        name_ = f"node_{self._name}"
        self.mcp = MCPManager(name_, servers=mcp_servers, owner=self.name)
        self.enable_db_logging = enable_logging
        self.session_id: str | None = None
        self.session_title: str | None = None

    async def log_session(
        self,
        initial_prompt: str | None = None,
        model: str | None = None,
        agent_type: str | None = None,
        *,
        session_id: str | None = None,
        definition_version: str | None = None,
    ) -> None:
        """Log conversation to storage if enabled.

        Should be called at the start of run_stream() after session_id is set.
        For native agents, generate session_id first with uuid4().
        For wrapped agents (Claude Code), set session_id from SDK session first.

        Args:
            initial_prompt: Optional initial prompt to trigger title generation.
            model: Requested model identifier for this session.
            agent_type: Type of agent backend (native, claude, codex, etc.).
            session_id: Explicit session ID to log (defaults to ``self.session_id``).
            definition_version: Version of the agent definition the session runs with.
        """
        target_session_id = session_id or self.session_id

        def _set_session_title(title: str) -> None:
            """Callback for setting conversation title (called by storage manager)."""
            if target_session_id == self.session_id:
                self.session_title = title
            if (pool := self.agent_pool) is not None and target_session_id is not None:
                session = pool.sessions.get(target_session_id)
                parent_id = session.parent_id if session is not None else None
                pool.activity.info(target_session_id, self.name, title=title, parent_id=parent_id)

        if self.enable_db_logging and self.storage and target_session_id:
            await self.storage.log_session(
                session_id=target_session_id,
                node_name=self.name,
                model=model,
                agent_type=agent_type,
                initial_prompt=initial_prompt,
                on_title_generated=_set_session_title,
                definition_version=definition_version,
            )

    async def __aenter__(self) -> Self:
        """Initialize base message node."""
        try:
            await self.mcp.__aenter__()
            if self.agent_pool is None:  # A pool runs its nodes' triggers itself (after MCP)
                await self.router.triggers.__aenter__()
        except Exception as e:
            await self.__aexit__(type(e), e, e.__traceback__)

            raise RuntimeError(f"Failed to initialize {self.name}") from e
        else:
            return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Clean up base resources."""
        if self.agent_pool is None and self._router is not None:
            await self._router.triggers.__aexit__(exc_type, exc_val, exc_tb)
        await self.mcp.__aexit__(exc_type, exc_val, exc_tb)
        await self.task_manager.cleanup_tasks()

    @property
    def router(self) -> Router:
        """The router holding this node's connections: its pool's, else its own.

        A node that joins a pool hands the connections of its own router to the pool's.
        """
        from agentpool.talk.router import Router

        if (pool := self.agent_pool) is not None:
            if self._router is not None:
                pool.router.adopt(self._router)
                self._router = None
            return pool.router
        if self._router is None:
            self._router = Router()
        return self._router

    @property
    def connection_stats(self) -> AggregatedTalkStats:
        """Get stats for all active connections of this node."""
        stats = [talk.stats for talk in self.connections.get_connections()]
        return AggregatedTalkStats(stats=stats)

    def get_context(
        self,
        data: Any = None,
        input_provider: InputProvider | None = None,
    ) -> NodeContext:
        """Create a new context for this node.

        Args:
            data: Optional custom data to attach to the context
            input_provider: Optional input provider override

        Returns:
            A new NodeContext instance
        """
        raise NotImplementedError

    @property
    def storage(self) -> StorageManager | None:
        """Get storage manager (per-agent override or from pool)."""
        if self._storage is not None:
            return self._storage
        # `is not None`: a pool is a registry, falsy while it holds no nodes
        return self.agent_pool.storage if self.agent_pool is not None else None

    @property
    def name(self) -> str:
        """Get agent name."""
        return self._name

    @property
    def display_name(self) -> str:
        """Get human-readable display name, falls back to name."""
        return self._display_name or self._name

    def to_tool(
        self, *, name: str | None = None, description: str | None = None, **kwargs: Any
    ) -> FunctionTool[TResult]:
        """Convert node to a callable tool.

        Args:
            name: Optional tool name override
            description: Optional tool description override
            **kwargs: Additional arguments for subclass customization

        Returns:
            Tool instance that can be registered
        """
        from agentpool.agents.base_agent import BaseAgent
        from agentpool.tools.base import FunctionTool

        async def wrapped(prompt: str) -> TResult:
            result = await self.run(prompt)
            return result.content

        tool_name = name or f"ask_{self.name}"
        docstring = description or f"Get expert answer from {self.name}"
        if self.description:
            docstring = f"{docstring}\n\n{self.description}"
        wrapped.__doc__ = docstring
        wrapped.__name__ = tool_name
        return_val = out if isinstance(self, BaseAgent) and (out := self._output_type) else Any
        wrapped.__annotations__ = {"prompt": str, "return": return_val}
        return FunctionTool.from_callable(wrapped)

    @overload
    def __rshift__(
        self, other: MessageNode[Any, Any] | ProcessorCallback[Any]
    ) -> Talk[TResult]: ...

    @overload
    def __rshift__(
        self, other: Sequence[MessageNode[Any, Any] | ProcessorCallback[Any]]
    ) -> TeamTalk[TResult]: ...

    def __rshift__(
        self,
        other: MessageNode[Any, Any]
        | ProcessorCallback[Any]
        | Sequence[MessageNode[Any, Any] | ProcessorCallback[Any]],
    ) -> Talk[Any] | TeamTalk[Any]:
        """Connect agent to another agent or group.

        Example:
            agent >> other_agent  # Connect to single agent
            agent >> (agent2 & agent3)  # Connect to group
            agent >> "other_agent"  # Connect by name (needs pool)
        """
        return self.connect_to(other)

    @overload
    def connect_to(
        self,
        target: MessageNode[Any, Any] | ProcessorCallback[Any],
        *,
        queued: Literal[True],
        queue_strategy: Literal["concat"],
    ) -> Talk[str]: ...

    @overload
    def connect_to(
        self,
        target: MessageNode[Any, Any] | ProcessorCallback[Any],
        *,
        connection_type: ConnectionType = "run",
        name: str | None = None,
        delay: timedelta | None = None,
        queued: bool = False,
        queue_strategy: QueueStrategy = "latest",
        transform: AnyTransformFn[Any] | None = None,
        filter_condition: AsyncFilterFn | None = None,
        stop_condition: AsyncFilterFn | None = None,
        exit_condition: AsyncFilterFn | None = None,
        session: ConnectionSessionPolicy = "target",
    ) -> Talk[TResult]: ...

    @overload
    def connect_to(
        self,
        target: Sequence[MessageNode[Any, Any] | ProcessorCallback[Any]],
        *,
        queued: Literal[True],
        queue_strategy: Literal["concat"],
    ) -> TeamTalk[str]: ...

    @overload
    def connect_to(
        self,
        target: Sequence[MessageNode[Any, TResult] | ProcessorCallback[TResult]],
        *,
        connection_type: ConnectionType = "run",
        name: str | None = None,
        delay: timedelta | None = None,
        queued: bool = False,
        queue_strategy: QueueStrategy = "latest",
        transform: AnyTransformFn[Any] | None = None,
        filter_condition: AsyncFilterFn | None = None,
        stop_condition: AsyncFilterFn | None = None,
        exit_condition: AsyncFilterFn | None = None,
        session: ConnectionSessionPolicy = "target",
    ) -> TeamTalk[TResult]: ...

    @overload
    def connect_to(
        self,
        target: Sequence[MessageNode[Any, Any] | ProcessorCallback[Any]],
        *,
        connection_type: ConnectionType = "run",
        name: str | None = None,
        delay: timedelta | None = None,
        queued: bool = False,
        queue_strategy: QueueStrategy = "latest",
        transform: AnyTransformFn[Any] | None = None,
        filter_condition: AsyncFilterFn | None = None,
        stop_condition: AsyncFilterFn | None = None,
        exit_condition: AsyncFilterFn | None = None,
        session: ConnectionSessionPolicy = "target",
    ) -> TeamTalk: ...

    def connect_to(
        self,
        target: MessageNode[Any, Any]
        | ProcessorCallback[Any]
        | Sequence[MessageNode[Any, Any] | ProcessorCallback[Any]],
        *,
        connection_type: ConnectionType = "run",
        name: str | None = None,
        delay: timedelta | None = None,
        queued: bool = False,
        queue_strategy: QueueStrategy = "latest",
        transform: AnyTransformFn[Any] | None = None,
        filter_condition: AsyncFilterFn | None = None,
        stop_condition: AsyncFilterFn | None = None,
        exit_condition: AsyncFilterFn | None = None,
        session: ConnectionSessionPolicy = "target",
    ) -> Talk[Any] | TeamTalk:
        """Create connection(s) to target(s)."""
        # Handle callable case
        from agentpool.agents import Agent
        from agentpool.delegation.base_team import BaseTeam

        if callable(target):
            target = Agent.from_callback(target)
            if pool := self.agent_pool:
                target.agent_pool = pool
                pool.register(target.name, target)
        # we are explicit here just to make disctinction clear, we only want sequences
        # of message units
        if isinstance(target, Sequence) and not isinstance(target, BaseTeam):
            targets: list[MessageNode[Any, Any]] = []
            for t in target:
                match t:
                    case _ if callable(t):
                        other = Agent.from_callback(t)
                        if pool := self.agent_pool:
                            other.agent_pool = pool
                            pool.register(other.name, other)
                        targets.append(other)
                    case MessageNode():
                        targets.append(t)
                    case _:
                        raise TypeError(f"Invalid node type: {type(t)}")
        else:
            targets = target  # type: ignore[assignment]
        return self.connections.create_connection(
            self,
            targets,
            connection_type=connection_type,
            name=name,
            delay=delay,
            queued=queued,
            queue_strategy=queue_strategy,
            transform=transform,
            filter_condition=filter_condition,
            stop_condition=stop_condition,
            exit_condition=exit_condition,
            session=session,
        )

    async def disconnect_all(self) -> None:
        """Disconnect from all nodes."""
        for target in list(self.connections.get_targets()):
            self.stop_passing_results_to(target)

    def stop_passing_results_to(self, other: MessageNode[Any, Any]) -> None:
        """Stop forwarding results to another node."""
        self.connections.disconnect(other)

    @abstractmethod
    async def run(self, *prompts: Any, **kwargs: Any) -> ChatMessage[TResult]:
        """Execute node with prompts. Implementation-specific run logic."""

    async def run_message(self, message: ChatMessage[Any], **kwargs: Any) -> ChatMessage[TResult]:
        """Run with an incoming ChatMessage (e.g., from Talk routing).

        Extracts content from the message, preserves session_id,
        and sets parent_id to track the message chain.

        Args:
            message: The incoming ChatMessage to process
            **kwargs: Additional arguments passed to run()

        Returns:
            Response ChatMessage with message chain tracked via parent_id
        """
        return await self.run(
            message.content,
            session_id=message.session_id,
            parent_id=message.message_id,
            **kwargs,
        )

    async def get_message_history(self, limit: int | None = None) -> list[ChatMessage[Any]]:
        """Get message history from storage."""
        from agentpool_config.session import SessionQuery

        if not self.enable_db_logging or not self.storage:
            return []
        query = SessionQuery(name=self.session_id, limit=limit)
        return await self.storage.filter_messages(query)

    async def log_message(self, message: ChatMessage[Any]) -> None:
        """Handle message from chat signal."""
        if self.enable_db_logging and self.storage:
            await self.storage.log_message(message)

    @abstractmethod
    async def get_stats(self) -> MessageStats | AggregatedMessageStats:
        """Get message statistics for this node."""

    @abstractmethod
    def run_iter(self, *prompts: Any, **kwargs: Any) -> AsyncIterator[ChatMessage[Any]]:
        """Yield messages during execution."""
