"""The live side of one session on an agent: its turns.

An agent instance serves several sessions (``run_stream(session=...)``). A
``SessionRuntime`` is the agent at work for one of them: it runs the session's turns
(queued prompts, hooks, history, connections) and holds what they change while they
run: whether one was cancelled, its task, prompts queued or injected into it, events
of its tools and subagents, and content staged for the next prompt. The agent keeps one
per session, created on use (``BaseAgent.runtime(session)``), and one for its default
session.

The runtime of the running turn is found through a context variable that maps agents
to their active runtime rather than holding one: a turn of agent A can stream a
subagent B's turn (``task``), and both must find their own. So tools
(``ctx.agent.queue_prompt(...)``) and agent implementations (``self._cancelled``) reach
their own turn's state, and one instance runs turns of different sessions concurrently
(where ``capabilities.concurrent_sessions``).

Design: ``ARCHITECTURE_SESSIONS_AGENTS.md`` §9.3-§9.4 (runtime), §6 step 7, §11. The
definition still lives on the agent instance; this splits off the session-bound part.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace
from decimal import Decimal
import time
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from pydantic_ai import RunUsage

from agentpool.agents.agent_tasks import RunningTask, track_task
from agentpool.agents.events import (
    DiffContentItem,
    NoticeEvent,
    StreamCompleteEvent,
    ToolCallProgressEvent,
)
from agentpool.agents.prompt_injection import PromptInjectionManager
from agentpool.agents.staged_content import StagedContent
from agentpool.prompts.convert import convert_prompts
from agentpool.turns import (
    Turn,
    TurnCancelled,
    TurnCompleted,
    outcome_of_error,
    stop_reason as turn_stop_reason,
)
from agentpool.utils.time_utils import get_now


if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Generator, Mapping, Sequence

    from agentpool.agents.base_agent import BaseAgent
    from agentpool.agents.events import RichAgentStreamEvent
    from agentpool.common_types import AnyEventHandlerType, PromptCompatible
    from agentpool.messaging import ChatMessage, MessageHistory
    from agentpool.sessions import Session
    from agentpool.sessions.activity import SessionState, StopReason
    from agentpool.turns import TurnOutcome
    from agentpool.ui.base import InputProvider


class SessionRuntime[TDeps = Any, TResult = Any]:
    """One session's turns on an agent instance, and what they change while running."""

    def __init__(
        self, agent: BaseAgent[TDeps, TResult], session: Session, *, default: bool = False
    ) -> None:
        """Create the runtime of ``session`` on ``agent``.

        Args:
            agent: The agent whose turns these are
            session: The session they belong to
            default: Whether ``session`` is the agent's default session (runs without an
                explicit ``session``: the agent's own ``session_id`` and conversation)
        """
        self.agent = agent
        self.session = session
        self.is_default = default
        self.cancelled = False
        self.task: asyncio.Task[Any] | None = None
        """Task of the running ``run_stream`` (agents cancel it on interrupt)."""
        self.active_runs = 0
        """Runs currently streaming (events of tools and subagents go to their stream)."""
        self.injections = PromptInjectionManager()
        self.events: asyncio.Queue[RichAgentStreamEvent[Any]] = asyncio.Queue()
        self.staged = StagedContent()
        self.turn_message_id: str | None = None
        self.cost = Decimal(0)
        """Cost of this session's turns on this runtime so far (USD)."""
        self.tasks: dict[str, RunningTask] = {}
        """Tasks this session's turns started that still run, by task id."""
        self.last_turn: Turn[TResult] | None = None
        """The last turn of the running (or last) ``run_stream``, however it ended."""
        """ID of the user message of the prompt group running (e.g. file changes record it)."""

    @property
    def idle(self) -> bool:
        """Nothing runs, and nothing waits for the next turn."""
        return (
            self.active_runs == 0
            and not self.tasks  # Background tasks outlive their turn
            and not self.injections.has_queued()
            and not self.injections.has_pending()
            and len(self.staged) == 0
        )

    @property
    def _explicit_session(self) -> Session | None:
        """The session as passed to the run: None for the agent's default session."""
        return None if self.is_default else self.session

    async def run_stream(
        self,
        *prompts: PromptCompatible,
        store_history: bool = True,
        message_id: str | None = None,
        user_message_id: str | None = None,
        session_id: str | None = None,
        parent_id: str | None = None,
        message_history: MessageHistory | None = None,
        input_provider: InputProvider | None = None,
        deps: TDeps | None = None,
        event_handlers: Sequence[AnyEventHandlerType] | None = None,
    ) -> AsyncIterator[RichAgentStreamEvent[TResult]]:
        """Run the prompts as this session's turn, then any queued during it.

        Arguments as for ``BaseAgent.run_stream``; ``session_id`` only applies to the
        default session (it re-points the agent's own session). ``user_message_id`` is the
        ID of the given prompts' user message; queued prompts get their own.
        """
        agent = self.agent
        if self.is_default:
            effective_session_id = await self._start_default_session(prompts, session_id)
            effective_history = message_history
        else:
            effective_session_id = self.session.session_id
            effective_history = (
                message_history if message_history is not None else self.session.history
            )
            new = not self.session.history
            if new:
                await self._log_new_session(
                    prompts, model=self.session.overrides.model, session_id=effective_session_id
                )
            if (notice := await self._follow_definition(new=new)) is not None:
                yield notice

        with activate(agent, self):  # Turn state below is this session's
            # Reset cancellation state and track current task
            self.cancelled = False
            self.task = asyncio.current_task()
            # Queue the initial prompts
            self.injections.insert_queued(prompts)
            self._report_state("running")
            self.last_turn = None
            stop_reason: StopReason = "cancelled"  # Unless the turn ends normally or fails
            error: str | None = None
            usage = RunUsage()
            try:
                # Process queued prompts until queue is empty
                prompt_message_id = user_message_id  # The given prompts' (the first group)
                while self.injections.has_queued() and not self.cancelled:
                    current_prompts = self.injections.pop_queued()
                    if current_prompts is None:
                        break
                    group_message_id, prompt_message_id = prompt_message_id, None
                    async for event in self._run_once(
                        *current_prompts,
                        store_history=store_history,
                        message_id=message_id,
                        user_message_id=group_message_id,
                        session_id=effective_session_id,
                        parent_id=parent_id,
                        message_history=effective_history,
                        input_provider=input_provider,
                        deps=deps,
                        event_handlers=event_handlers,
                    ):
                        if isinstance(event, StreamCompleteEvent):
                            usage.incr(event.message.usage)
                        self._report_event(event)
                        yield event
                    # After each iteration, flush unconsumed injections to queue
                    self.injections.flush_pending_to_queue()
                if not self.cancelled:
                    stop_reason = "end_turn"
            except Exception as e:
                stop_reason, error = "error", str(e)
                raise
            finally:
                if (turn := self.last_turn) is not None:  # How its last turn ended
                    stop_reason = turn_stop_reason(turn.outcome)
                self._report_state("idle", stop_reason=stop_reason, usage=usage, error=error)
                self.task = None
                self.turn_message_id = None
                self.injections.clear()  # Clean slate for next run
                if not self.is_default and self.idle:
                    agent._runtimes.pop(self.session.session_id, None)

    def _report_event(self, event: RichAgentStreamEvent[Any]) -> None:
        """Report what a turn's event says about the session to the pool's activity feed."""
        from agentpool.messaging.message_utils import context_tokens

        if (pool := self.agent.agent_pool) is None:
            return
        session_id, name = self.session.session_id, self.agent.name
        if isinstance(event, StreamCompleteEvent):
            message = event.message
            if message.cost_info is not None:
                self.cost += message.cost_info.total_cost
            pool.activity.usage(
                session_id,
                name,
                used=context_tokens(message),
                size=self.agent.known_context_window,
                cost=float(self.cost) if self.cost else None,
            )
            return
        pool.activity.observe(session_id, name, event)

    def _report_state(
        self,
        state: SessionState,
        *,
        stop_reason: StopReason | None = None,
        usage: RunUsage | None = None,
        error: str | None = None,
    ) -> None:
        """Report this session's foreground state to the pool's activity feed."""
        if (pool := self.agent.agent_pool) is None:
            return
        tokens = (
            {"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens}
            if usage is not None
            else None
        )
        pool.activity.state(
            self.session.session_id,
            self.agent.name,
            state,
            stop_reason=stop_reason,
            usage=tokens,
            error=error,
        )

    async def _start_default_session(
        self, prompts: Sequence[PromptCompatible], session_id: str | None
    ) -> str | None:
        """Initialize or re-point the agent's own session; returns the run's session ID."""
        from agentpool.utils.identifiers import generate_session_id

        agent = self.agent
        # Initialize default session_id once for the entire run (including queued prompts)
        if agent.session_id is None:
            agent.session_id = session_id or generate_session_id()
            agent._default_session.set_session_id(agent.session_id)
            await self._log_new_session(prompts, model=None, session_id=agent.session_id)
        elif session_id and agent.session_id != session_id:
            agent.session_id = session_id
            agent._default_session.set_session_id(session_id)
        return session_id

    async def _follow_definition(self, *, new: bool) -> NoticeEvent | None:
        """Run the session on the agent's current definition, and record it (rule R2).

        Policy ``follow_latest``: a resumed session runs with the definition as it is now.
        When it changed since the session last ran, a notice says so. The session records
        the version it runs with (a new one too: a server may have stored it already, which
        makes the storage skip ``log_session``).
        """
        agent = self.agent
        if agent.definition is None:
            return None
        current = agent.definition.version
        data = self.session.data
        recorded = None if new else data.definition_version
        if not new and recorded == current:
            return None
        data.definition_version = current
        await self._store_definition_version(current)
        if recorded is None:  # New, or recorded before versions were: nothing to compare
            return None
        return NoticeEvent(
            title=f"Agent {agent.name!r} changed since this session last ran",
            description="The session continues with its current definition.",
        )

    async def _store_definition_version(self, version: str) -> None:
        """Store ``version`` on the session's stored record, keeping the rest of it."""
        if (storage := self.agent.storage) is None:
            return
        stored = await storage.load_session(self.session.session_id)
        if stored is not None and stored.definition_version != version:
            await storage.save_session(stored.model_copy(update={"definition_version": version}))

    async def _log_new_session(
        self, prompts: Sequence[PromptCompatible], *, model: str | None, session_id: str | None
    ) -> None:
        """Record a new session in storage, titled after the first text the user wrote.

        Injected context (rules, staged content; ``is_injected``) is not the user's:
        alone, it gives no title.
        """
        from agentpool.agents.staged_content import is_injected

        agent = self.agent
        version = agent.definition.version if agent.definition is not None else None
        self.session.data.definition_version = version
        user_prompts = [p for p in prompts if isinstance(p, str) and not is_injected(p)]
        initial_prompt = next((p for p in user_prompts if p.strip()), None)
        # Agents that title their sessions themselves skip the storage's titles
        await agent.log_session(
            None if agent.TITLES_OWN_SESSIONS else initial_prompt,
            model=model or agent.model_name,
            agent_type=agent.AGENT_TYPE,
            session_id=session_id,
            definition_version=version,
        )

    async def _run_once(  # noqa: PLR0915
        self,
        *prompts: PromptCompatible,
        store_history: bool,
        message_id: str | None,
        user_message_id: str | None,
        session_id: str | None,
        parent_id: str | None,
        message_history: MessageHistory | None,
        input_provider: InputProvider | None,
        deps: TDeps | None,
        event_handlers: Sequence[AnyEventHandlerType] | None,
    ) -> AsyncIterator[RichAgentStreamEvent[TResult]]:
        """Process a single prompt group: hooks, the agent's events, history, connections."""
        from anyenv import MultiEventHandler

        from agentpool.agents.base_agent import BaseAgent
        from agentpool.agents.events import resolve_event_handlers
        from agentpool.messaging import ChatMessage

        agent = self.agent
        session = self._explicit_session
        # Convert prompts to standard UserContent format
        converted_prompts = await convert_prompts(prompts)
        # Append any staged content
        if staged := await self.staged.consume_as_context():
            converted_prompts = [*converted_prompts, staged]
        # Get message history (explicit > session > agent default)
        conversation = (
            message_history
            if message_history is not None
            else (session.history if session is not None else agent.conversation)
        )
        effective_session_id = (
            session.session_id if session is not None else (session_id or agent.session_id)
        )
        # Determine effective parent_id (from param or last message in history)
        pending_parts = conversation.get_pending_parts()
        effective_parent_id = parent_id if parent_id else conversation.get_last_message_id()
        user_msg = ChatMessage.user_prompt(
            message=converted_prompts,
            parent_id=effective_parent_id,
            session_id=effective_session_id,
            message_id=user_message_id,
        )
        self.turn_message_id = user_msg.message_id
        resolved_handler = (
            MultiEventHandler(resolve_event_handlers(event_handlers))
            if event_handlers is not None
            else agent.event_handler
        )
        # Stream events from implementation
        final_message: ChatMessage[TResult] | None = None
        turn: Turn[TResult] | None = None
        started_at, started = get_now(), time.perf_counter()

        def end_turn(outcome: TurnOutcome, result: ChatMessage[TResult] | None) -> Turn[TResult]:
            ended = Turn(
                turn_id=user_msg.message_id,
                session_id=effective_session_id or self.session.session_id,
                node=agent.name,
                prompt=user_msg,
                outcome=outcome,
                result=result,
                usage=result.usage if result is not None else RunUsage(),
                cost=result.cost_info if result is not None else None,
                started_at=started_at,
                duration=time.perf_counter() - started,
            )
            self.last_turn = ended
            return ended

        await agent.message_received.emit(user_msg)
        self.active_runs += 1
        try:
            if agent.hooks:
                pre_run_result = await agent.hooks.run_pre_run_hooks(
                    agent_name=agent.name,
                    prompt=str(user_msg.content),  # TODO: allow UserContent for hook?
                    session_id=effective_session_id,
                    env=agent.env,
                )
                if pre_run_result.get("decision") == "deny":
                    reason = pre_run_result.get("reason", "Blocked by pre-run hook")
                    raise RuntimeError(f"Run blocked: {reason}")  # noqa: TRY301

            context = agent.get_context(input_provider=input_provider)
            async for raw in agent._stream_events(
                [*pending_parts, *converted_prompts],
                user_msg=user_msg,
                effective_parent_id=effective_parent_id,
                store_history=store_history,
                message_id=message_id,
                session_id=effective_session_id,
                parent_id=parent_id,
                message_history=conversation,
                input_provider=input_provider,
                deps=deps,
            ):
                event = raw
                if isinstance(raw, StreamCompleteEvent):  # The turn's end: with its Turn
                    final_message = raw.message
                    outcome = TurnCancelled() if self.cancelled else TurnCompleted()
                    turn = end_turn(outcome, final_message)
                    event = replace(raw, turn=turn)
                await resolved_handler(context, event)
                self._record_file_changes(event)
                self.track_task(event)
                yield event
            if (task := asyncio.current_task()) is not None and task.cancelling():
                # Cancelled from outside, and the agent ended its turn with what it had
                await self._stop_backend()
        except asyncio.CancelledError as e:
            await self._stop_backend()
            if turn is None:  # Cancelled before it answered
                turn = end_turn(outcome_of_error(e), None)
            await agent.turn_completed.emit(turn)
            raise
        except Exception as e:
            agent.log.exception("Agent stream failed")
            failed_event = BaseAgent.RunFailedEvent(
                agent_name=agent.name, message="Agent stream failed", exception=e
            )
            await agent.run_failed.emit(failed_event)
            await agent.turn_completed.emit(end_turn(outcome_of_error(e), None))
            raise
        finally:
            self.active_runs -= 1

        if not self.active_runs:
            # Events queued after the stream stopped draining (e.g. a background task
            # finishing as the run ends) are not held back for the next run
            while not self.events.empty():
                late = BaseAgent.BackgroundEvent(
                    agent_name=agent.name,
                    session_id=effective_session_id,
                    event=self.events.get_nowait(),
                )
                await agent.background_event.emit(late)
        if final_message is not None:
            await self._finalize(
                user_msg=user_msg,
                final_message=final_message,
                conversation=conversation,
                session_id=effective_session_id,
                store_history=store_history,
            )
        if turn is None:  # The stream ended without an answer
            turn = end_turn(TurnCancelled() if self.cancelled else TurnCompleted(), None)
        await agent.turn_completed.emit(turn)  # Once its answer is stored and routed

    def track_task(self, event: RichAgentStreamEvent[Any]) -> None:
        """Record a task event; drop this runtime once its last task ends between turns."""
        track_task(self.tasks, event, self.session.session_id)
        if not self.is_default and self.idle:
            self.agent._runtimes.pop(self.session.session_id, None)

    def _record_file_changes(self, event: RichAgentStreamEvent[Any]) -> None:
        """Record the diffs of a tool's progress as file changes of this turn.

        Every agent's: native tools and the bridge's report them, external agents translate
        their tools' results into them. The pool's tracker serves diffs and reverts.
        """
        if not isinstance(event, ToolCallProgressEvent) or (pool := self.agent.agent_pool) is None:
            return
        for item in event.items:
            if isinstance(item, DiffContentItem):
                pool.file_ops.record_change(
                    path=item.path,
                    old_content=item.old_text,
                    new_content=item.new_text,
                    operation="create" if item.old_text is None else "write",
                    agent_name=self.agent.name,
                    session_id=self.session.session_id,
                    message_id=self.turn_message_id,
                )

    async def _finalize(
        self,
        *,
        user_msg: ChatMessage[Any],
        final_message: ChatMessage[TResult],
        conversation: MessageHistory,
        session_id: str | None,
        store_history: bool,
    ) -> None:
        """Run post-run hooks, persist history, then announce the message.

        ``message_sent`` comes last: the router routes the message on it, to a history
        that already holds it.
        """
        from agentpool.agents.base_agent import split_exchange

        agent = self.agent
        if agent.hooks:
            await agent.hooks.run_post_run_hooks(
                agent_name=agent.name,
                prompt=str(user_msg.content),
                result=final_message.content,
                session_id=session_id,
                env=agent.env,
            )
        if store_history:
            await agent.log_message(final_message)
            conversation.add_chat_messages(split_exchange(user_msg, final_message))
            if (session := self._explicit_session) is not None:
                session.data.touch()
        await agent.message_sent.emit(final_message)

    async def _stop_backend(self) -> None:
        """Tell the agent's backend to stop a turn that was cancelled from outside.

        Cancelling the task that runs a turn only stops its Python side: an agent whose
        turn runs elsewhere (a CLI subprocess, an app-server, a remote ACP or OpenCode
        session) keeps working until its ``_interrupt`` tells it to stop. Nothing to do
        after ``interrupt()`` (it already did) or when the agent marked the turn cancelled
        itself (native agents: the turn was all in this task). ``_interrupt`` runs shielded,
        so the cancellation in progress doesn't cut it short.
        """
        if self.cancelled:
            return
        self.cancelled = True
        self.task = None  # No turn task left for ``_interrupt`` to cancel: it is cancelled
        stop = asyncio.create_task(self.agent._interrupt(), name=f"stop-{self.agent.name}")
        _STOPPING.add(stop)
        stop.add_done_callback(_STOPPING.discard)
        try:
            await asyncio.shield(stop)
        except asyncio.CancelledError:
            pass  # Cancelled again while waiting: the stop goes on in its own task
        except Exception:
            self.agent.log.exception("Stopping the cancelled turn's backend failed")

    async def interrupt(self) -> None:
        """Cancel this session's running turn (the agent's ``_interrupt`` acts on it)."""
        with activate(self.agent, self):
            self.cancelled = True
            await self.agent._interrupt()


_STOPPING: set[asyncio.Task[None]] = set()
"""Backend stops of cancelled turns still running (a reference keeps each task alive)."""

_ACTIVE: ContextVar[Mapping[int, SessionRuntime[Any, Any]]] = ContextVar(
    "agentpool_active_runtimes", default=MappingProxyType({})
)
"""Active runtime of each agent (by ``id(agent)``) in the current context."""


def active_runtime(agent: object) -> SessionRuntime[Any, Any] | None:
    """The runtime of ``agent`` running in this context, if any."""
    return _ACTIVE.get().get(id(agent))


@contextmanager
def activate(agent: object, runtime: SessionRuntime[Any, Any]) -> Generator[None]:
    """Make ``runtime`` the active runtime of ``agent`` in this context while the block runs.

    Restores the previous mapping by value instead of with a token, so leaving the block
    from another context (an async generator closed elsewhere) can't fail.
    """
    previous = _ACTIVE.get()
    _ACTIVE.set({**previous, id(agent): runtime})
    try:
        yield
    finally:
        _ACTIVE.set(previous)
