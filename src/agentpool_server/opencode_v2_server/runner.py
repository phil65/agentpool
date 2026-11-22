"""Execution of session turns: inbox draining, agent binding and run streaming.

Interim model (until sessions own their history, see ``ARCHITECTURE_SESSIONS_AGENTS.md``):
pool agents are shared instances holding one active conversation. :class:`AgentBinder`
serializes turns per agent instance and re-points the instance to the session of the
current turn (``load_session`` for existing conversations, ``start_new_session`` for new
ones) before running it.
"""

from __future__ import annotations

import asyncio
import contextlib
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from agentpool.agents.base_agent import BaseAgent
from agentpool.agents.events import NoticeEvent, RunStartedEvent, StreamCompleteEvent
from agentpool.agents.native_agent import Agent
from agentpool.delegation.team import Team
from agentpool.delegation.teamrun import TeamRun
from agentpool.log import get_logger
from agentpool.messaging.compaction import summarizing_context
from agentpool_server.opencode_v2_server.catalog import leaf_agents, variant_category
from agentpool_server.opencode_v2_server.prompt_input import payload_to_prompts
from agentpool_server.opencode_v2_server.run_converter import (
    RunEventConverter,
    structured_error,
    usage_to_tokens,
)
from agentpool_server.opencode_v2_server.tasks import TaskTracker
from opencode_v2_sdk.models import (
    SessionCompactionEnded,
    SessionCompactionEndedData,
    SessionCompactionFailed,
    SessionCompactionFailedData,
    SessionCompactionStarted,
    SessionCompactionStartedData,
    SessionExecutionFailed,
    SessionExecutionFailedData,
    SessionExecutionInterrupted,
    SessionExecutionInterruptedData,
    SessionExecutionStarted,
    SessionExecutionStartedData,
    SessionExecutionSucceeded,
    SessionExecutionSucceededData,
    SessionIdle,
    SessionIdleData,
    SessionInboxCompaction,
    SessionInboxDelivered,
    SessionInboxDeliveredData,
    SessionInboxSynthetic,
    SessionInboxUser,
    SessionMessageUser,
    SessionStatusBusy,
    SessionStatusIdle,
    SessionStatusUpdated,
    SessionStatusUpdatedData,
    TuiToastShow,
    TuiToastShowData,
)


if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable

    from pydantic_ai import UserContent

    from agentpool.agents.events import RichAgentStreamEvent, StreamWithCommandsEvent
    from agentpool.messaging import MessageNode
    from agentpool.messaging.compaction import CompactionPipeline
    from agentpool.sessions import Session
    from agentpool.skills.manager import SkillsManager
    from agentpool_server.opencode_v2_server.catalog import Catalog
    from agentpool_server.opencode_v2_server.interactions import SessionInputProvider
    from agentpool_server.opencode_v2_server.session import V2Session
    from opencode_v2_sdk.models import ModelRef


logger = get_logger(__name__)

SUMMARIZE_PROMPT = (
    "Provide a detailed prompt for continuing our conversation above. Focus on information "
    "that would be helpful for continuing the conversation, including what we did, what we're "
    "doing, which files we're working on, and what we're going to do next considering new "
    "session will not have access to our conversation."
)
"""Prompt used to summarize a conversation before manual compaction."""

type InterruptReason = Literal["user", "shutdown", "superseded", "inactivity"]


class ExecutionError(Exception):
    """A session turn could not be started."""


@dataclass
class AgentBinder:
    """Serializes turns per agent instance and binds instances to sessions."""

    catalog: Catalog
    _locks: dict[str, asyncio.Lock] = field(default_factory=dict)

    def _lock(self, agent: BaseAgent[Any, Any]) -> asyncio.Lock:
        return self._locks.setdefault(agent.name, asyncio.Lock())

    @asynccontextmanager
    async def hold(
        self,
        node: MessageNode[Any, Any],
        session: V2Session,
        model: ModelRef | None,
        *,
        has_history: bool,
    ) -> AsyncGenerator[Session | None]:
        """Prepare the node for a turn of ``session``.

        Native :class:`Agent` runs use an isolated :class:`Session` from ``pool.sessions``
        (its own history and model override) and their own turn state (cancellation,
        queued input, events: ``agentpool.agents.runtime``), so turns of different
        sessions run concurrently, without a lock or re-binding. External agents keep one
        native session per instance: their turns are serialized, and the instance is
        re-bound to the session of each turn (``load_session`` / ``start_new_session``).
        """
        if isinstance(node, Agent):
            yield await self._native_session(node, session, model, has_history=has_history)
            return
        agents = sorted(leaf_agents(node), key=lambda a: a.name)
        async with AsyncExitStack() as stack:
            for agent in agents:
                await stack.enter_async_context(self._lock(agent))
            if isinstance(node, BaseAgent):
                await self._bind(node, session.id, has_history=has_history)
                await self._apply_model(node, model)
            yield None

    async def _native_session(
        self,
        agent: Agent[Any, Any],
        session: V2Session,
        model: ModelRef | None,
        *,
        has_history: bool,
    ) -> Session:
        registry = self.catalog.pool.sessions
        pool_session = registry.get(session.id)
        if pool_session is None:
            stored = (
                await agent.storage.get_session_messages(session.id)
                if has_history and agent.storage is not None
                else None
            )
            pool_session = registry.create(
                agent.name,
                session_id=session.id,
                parent_id=session.info.parent_id,
                cwd=session.directory,
                agent_type=agent.AGENT_TYPE,
                messages=stored,
            )
        elif pool_session.agent_name != agent.name:
            pool_session.rebind_agent(agent.name, agent_type=agent.AGENT_TYPE)
        if model is not None:
            target = await self.catalog.resolve_model(agent, model)
            if target:
                override = target if target != agent.model_name else None
                if pool_session.overrides.model != override:
                    pool_session.overrides = pool_session.overrides.model_copy(
                        update={"model": override}
                    )
            if model.variant and (category := await variant_category(agent)):
                await agent.set_mode(model.variant, category_id=category)
        return pool_session

    async def _bind(
        self, agent: BaseAgent[Any, Any], session_id: str, *, has_history: bool
    ) -> None:
        if agent.session_id == session_id:
            return
        if has_history and await agent.load_session(session_id) is not None:
            agent.session_id = session_id
            return
        await agent.start_new_session()

    async def _apply_model(self, agent: BaseAgent[Any, Any], model: ModelRef | None) -> None:
        if model is None:
            return
        target = await self.catalog.resolve_model(agent, model)
        if target and agent.model_name != target:
            await agent.set_model(target)
        if model.variant and (category := await variant_category(agent)):
            await agent.set_mode(model.variant, category_id=category)


@dataclass
class SessionRunner:
    """Drains a session's inbox and runs its turns."""

    session: V2Session
    catalog: Catalog
    binder: AgentBinder
    skills: SkillsManager
    input_provider: SessionInputProvider
    on_turn_finished: Callable[[V2Session], Awaitable[None]] | None = None
    """Optional callback run after each turn (e.g. to persist session metadata)."""
    converter: RunEventConverter | None = None
    _drain_task: asyncio.Task[None] | None = None
    _run_task: asyncio.Task[None] | None = None
    _interrupt_reason: InterruptReason | None = None
    _resume: bool = True
    compaction_pipeline: CompactionPipeline | None = None
    """Pipeline for manual compaction (defaults to a summarizing pipeline)."""
    environment: dict[str, str] = field(default_factory=dict)
    """Environment variables for the session's shell commands."""
    pending_context: list[str] = field(default_factory=list)
    """Context (e.g. activated skills) prepended to the next turn's prompt."""
    tasks: TaskTracker = field(init=False)
    """Subagent runs and background commands started by the agent (listed as jobs)."""

    def __post_init__(self) -> None:
        self.tasks = TaskTracker(session_id=self.session.id)
        self._background: RunEventConverter | None = None
        """Converter of a turn the agent took on its own, while it runs."""
        self._background_done = asyncio.Event()
        self._background_done.set()

    async def on_background_event(self, event: RichAgentStreamEvent[Any]) -> None:
        """An event the session's agent produced between turns.

        A turn the agent took on its own (Claude Code reacting to a finished background
        task) is framed by ``RunStartedEvent`` / ``StreamCompleteEvent`` and becomes an
        execution of the session, like an OpenCode wake. Other events update the session's
        tasks; notices are shown as toasts.
        """
        self.tasks.observe(event)
        match event:
            case RunStartedEvent(agent_name=agent_name):
                await self._begin_background_turn(agent_name)
            case StreamCompleteEvent() if (converter := self._background) is not None:
                converter.handle(event)
                self._end_background_turn(converter)
            case _ if (converter := self._background) is not None:
                converter.handle(event)
            case NoticeEvent(title=title, description=description, severity=severity):
                data = TuiToastShowData(
                    title=title if description else None,
                    message=description or title,
                    variant=severity,
                )
                self.session.emit_sink(TuiToastShow, data)
            case _:
                pass

    async def _begin_background_turn(self, agent_name: str | None) -> None:
        if self._background is not None or self.running:
            return
        node = self.catalog.get_node(agent_name) if agent_name else self._current_node()
        if node is None:
            return
        session = self.session
        model_ref = session.info.model or await self.catalog.current_model_ref(node)
        self._background_done.clear()
        session.emit(SessionExecutionStarted, SessionExecutionStartedData(session_id=session.id))
        self._status(busy=True)
        self._background = RunEventConverter(
            session_id=session.id,
            agent_id=node.name,
            model_ref=model_ref,
            emit=session.emit_sink,
        )

    def _end_background_turn(self, converter: RunEventConverter) -> None:
        session = self.session
        converter.finish()
        if converter.errors:
            error = structured_error("; ".join(converter.errors))
            session.emit(
                SessionExecutionFailed,
                SessionExecutionFailedData(session_id=session.id, error=error),
            )
        else:
            session.emit(
                SessionExecutionSucceeded, SessionExecutionSucceededData(session_id=session.id)
            )
        self._background = None
        self.tasks.consume_finished()
        self._status(busy=False)
        self._background_done.set()

    @property
    def running(self) -> bool:
        """Whether a turn is currently executing."""
        return self._run_task is not None and not self._run_task.done()

    @property
    def active(self) -> bool:
        """Whether the session is busy: draining its inbox, or in a turn the agent took itself."""
        draining = self._drain_task is not None and not self._drain_task.done()
        return draining or self._background is not None

    def tool_message_id(self, call_id: str) -> str | None:
        """Assistant message ID of a tool call of the current turn."""
        if self.converter is None or (tool := self.converter.tools.get(call_id)) is None:
            return None
        return tool.message_id

    # --- scheduling ----------------------------------------------------------------------

    def schedule(self) -> None:
        """Start draining the inbox unless already draining."""
        self._resume = True
        if not self.active:
            self._drain_task = asyncio.create_task(
                self._drain(), name=f"v2-drain-{self.session.id}"
            )

    async def steer(self, inbox_id: str) -> bool:
        """Steer an input into the running turn (see ``BaseAgent.steer``).

        Returns:
            True if the input was delivered into the running turn.
        """
        item = self.session.projection.inbox.get(inbox_id)
        node = self._current_node()
        if not self.running or item is None or not isinstance(node, BaseAgent):
            return False
        match item:
            case SessionInboxUser(payload=payload):
                text = payload.text
            case SessionInboxSynthetic(payload=payload):
                text = payload.text
            case _:
                return False
        await node.steer(text, session_id=self.session.id)
        self.session.emit(
            SessionInboxDelivered,
            SessionInboxDeliveredData(session_id=self.session.id, inbox_id=inbox_id),
        )
        return True

    async def interrupt(self, reason: InterruptReason = "user", *, resume: bool = False) -> bool:
        """Interrupt the running turn.

        Args:
            reason: Interrupt reason reported in ``session.execution.interrupted``
            resume: Whether to continue draining queued inputs afterwards

        Returns:
            True if a running turn was interrupted.
        """
        self._resume = resume
        if not self.running or self._run_task is None:
            return False
        self._interrupt_reason = reason
        node = self._current_node()
        for agent in leaf_agents(node) if node is not None else []:
            try:
                # A single agent runs this session's turn; team members run their own
                session_id = self.session.id if agent is node else None
                await agent.interrupt(session_id=session_id)
            except Exception:
                logger.exception("Agent interrupt failed", agent=agent.name)
        self._run_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await asyncio.shield(self._run_task)
        return True

    async def wait(self) -> None:
        """Wait until the inbox is drained."""
        if self._drain_task is not None:
            await asyncio.shield(self._drain_task)

    async def shutdown(self) -> None:
        """Interrupt running work and stop draining."""
        await self.interrupt("shutdown")
        if self._drain_task is not None and not self._drain_task.done():
            self._drain_task.cancel()

    def _current_node(self) -> MessageNode[Any, Any] | None:
        agent = self.session.info.agent or self.catalog.default_agent_name()
        return self.catalog.get_node(agent)

    # --- draining ------------------------------------------------------------------------

    def _next_item(
        self,
    ) -> SessionInboxUser | SessionInboxSynthetic | SessionInboxCompaction | None:
        for item in self.session.projection.inbox.values():
            if isinstance(item, SessionInboxUser | SessionInboxSynthetic | SessionInboxCompaction):
                return item
        return None

    async def _drain(self) -> None:
        while self._resume and not self.session.deleted:
            item = self._next_item()
            if item is None:
                return
            work = (
                self._compact(item)
                if isinstance(item, SessionInboxCompaction)
                else self._execute(item)
            )
            self._run_task = asyncio.create_task(work, name=f"v2-turn-{self.session.id}")
            try:
                await self._run_task
            finally:
                self._run_task = None
                self._interrupt_reason = None
            if self.on_turn_finished is not None:
                await self.on_turn_finished(self.session)

    def _status(self, busy: bool) -> None:
        status = SessionStatusBusy() if busy else SessionStatusIdle()
        self.session.emit(
            SessionStatusUpdated,
            SessionStatusUpdatedData(session_id=self.session.id, status=status),
        )
        if not busy:
            self.session.emit(SessionIdle, SessionIdleData(session_id=self.session.id))

    async def _execute(self, item: SessionInboxUser | SessionInboxSynthetic) -> None:
        session = self.session
        # A turn the agent took on its own finishes first (the agent waits for it anyway)
        await self._background_done.wait()
        has_history = any(isinstance(m, SessionMessageUser) for m in session.projection.messages)
        session.emit(SessionExecutionStarted, SessionExecutionStartedData(session_id=session.id))
        self._status(busy=True)
        session.emit(
            SessionInboxDelivered,
            SessionInboxDeliveredData(session_id=session.id, inbox_id=item.id),
        )
        node = self._current_node()
        model = session.info.model
        try:
            if node is None:
                msg = f"Agent {session.info.agent!r} is not available"
                raise ExecutionError(msg)  # noqa: TRY301
            model_ref = model or await self.catalog.current_model_ref(node)
            self.converter = RunEventConverter(
                session_id=session.id,
                agent_id=node.name,
                model_ref=model_ref,
                emit=session.emit_sink,
            )
            prompts = await self._prompts(item)
            async with self.binder.hold(
                node, session, model, has_history=has_history
            ) as pool_session:
                async for event in self._stream(node, prompts, session=pool_session):
                    self.tasks.observe(event)
                    self.converter.handle(event)
            self.converter.finish()
            if self.converter.errors:
                error = structured_error("; ".join(self.converter.errors))
                session.emit(
                    SessionExecutionFailed,
                    SessionExecutionFailedData(session_id=session.id, error=error),
                )
            else:
                session.emit(
                    SessionExecutionSucceeded,
                    SessionExecutionSucceededData(session_id=session.id),
                )
        except asyncio.CancelledError:
            if self.converter is not None:
                self.converter.interrupt()
            reason = self._interrupt_reason or "shutdown"
            session.emit(
                SessionExecutionInterrupted,
                SessionExecutionInterruptedData(session_id=session.id, reason=reason),
            )
            if self._interrupt_reason is None:
                raise
        except Exception as e:
            logger.exception("Session turn failed", session_id=session.id)
            if self.converter is not None:
                self.converter.fail(str(e))
            session.emit(
                SessionExecutionFailed,
                SessionExecutionFailedData(
                    session_id=session.id,
                    error=structured_error(str(e), error_type=type(e).__name__),
                ),
            )
        finally:
            self.converter = None
            self.tasks.consume_finished()
            self._status(busy=False)

    async def _compact(self, item: SessionInboxCompaction) -> None:
        """Manual compaction: summarize the conversation, then compact the agent history."""
        session = self.session
        session.emit(
            SessionInboxDelivered,
            SessionInboxDeliveredData(session_id=session.id, inbox_id=item.id),
        )
        session.emit(
            SessionCompactionStarted,
            SessionCompactionStartedData(session_id=session.id, reason="manual", input_id=item.id),
        )
        self._status(busy=True)
        node = self._current_node()
        try:
            if not isinstance(node, BaseAgent):
                msg = "Compaction needs a single agent (teams keep no own history)"
                raise ExecutionError(msg)  # noqa: TRY301
            if not node.capabilities.native_compaction:  # Before the summary turn
                msg = f"{node.name} cannot compact on demand: its backend compacts on its own"
                raise ExecutionError(msg)  # noqa: TRY301
            has_history = any(
                isinstance(m, SessionMessageUser) for m in session.projection.messages
            )
            summary = ""
            tokens = None
            cost = 0.0
            async with self.binder.hold(
                node, session, session.info.model, has_history=has_history
            ) as pool_session:
                async for event in node.run_stream(
                    SUMMARIZE_PROMPT, session=pool_session, session_id=session.id
                ):
                    if isinstance(event, StreamCompleteEvent):
                        summary = str(event.message.content)
                        tokens = usage_to_tokens(event.message.usage)
                        if event.message.cost_info:
                            cost = float(event.message.cost_info.total_cost)
                pipeline = self.compaction_pipeline or summarizing_context()
                await node.compact(pipeline, session=pool_session)
            session.emit(
                SessionCompactionEnded,
                SessionCompactionEndedData(
                    session_id=session.id,
                    reason="manual",
                    model=session.info.model,
                    text=summary,
                    cost=cost,
                    tokens=tokens,
                ),
            )
        except asyncio.CancelledError:
            session.emit(
                SessionCompactionFailed,
                SessionCompactionFailedData(
                    session_id=session.id,
                    reason="manual",
                    error=structured_error("Compaction interrupted"),
                    input_id=item.id,
                ),
            )
            if self._interrupt_reason is None:
                raise
        except Exception as e:
            logger.exception("Compaction failed", session_id=session.id)
            session.emit(
                SessionCompactionFailed,
                SessionCompactionFailedData(
                    session_id=session.id,
                    reason="manual",
                    error=structured_error(str(e), error_type=type(e).__name__),
                    input_id=item.id,
                ),
            )
        finally:
            self._status(busy=False)

    async def _prompts(self, item: SessionInboxUser | SessionInboxSynthetic) -> list[UserContent]:
        prompts: list[UserContent] = []
        match item:
            case SessionInboxUser(payload=payload):
                prompts.extend(await payload_to_prompts(payload, skills=self.skills))
            case SessionInboxSynthetic(payload=payload):
                prompts.append(payload.text)
        prompts.extend(self.pending_context)
        self.pending_context.clear()
        return prompts

    def _stream(
        self,
        node: MessageNode[Any, Any],
        prompts: list[UserContent],
        *,
        session: Session | None = None,
    ) -> AsyncIterator[StreamWithCommandsEvent[Any]]:
        match node:
            case BaseAgent():
                first = prompts[0] if prompts else None
                if isinstance(first, str) and first.startswith("/"):
                    return node.run_stream_with_commands(
                        *prompts,
                        session=session,
                        session_id=self.session.id,
                        input_provider=self.input_provider,
                    )
                return node.run_stream(
                    *prompts,
                    session=session,
                    session_id=self.session.id,
                    input_provider=self.input_provider,
                )
            case Team() | TeamRun():
                return node.run_stream(*prompts)
            case _:
                msg = f"Node {node.name!r} cannot run session turns"
                raise ExecutionError(msg)
