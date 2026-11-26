"""ACP v2 subagent sessions: each subagent run is a child session of the session it runs in.

A run's events arrive as ``SubAgentEvent`` (flat: its depth and the tool call that started
it in its immediate parent). The first one announces a child session on that parent
(``subagent_update``: title, state, a ``cancel`` capability when its task can be stopped),
before anything names the child; the tool call that started it then shows it
(``session`` content). The run's own events become the child's updates, under the child's
session ID, through a converter and translator of its own (message and tool call IDs are
per session). The child's state is mirrored on the parent's association.

A child ends in ``idle`` (``end_turn``, ``error`` or ``cancelled``) when its run or task
finishes, or when the parent's turn is cancelled (foreground children only); one still
running when its parent's turn ended otherwise is ``unknown``. The association stays: v2
has no removal. Per spec, a child takes ``session/cancel`` only (when it advertises it,
which stops its task); other client methods are refused.
"""

from dataclasses import dataclass, field
from pathlib import PurePath
from typing import TYPE_CHECKING, Any, Literal
import uuid

from aclientp.schema import v2

from agentpool.agents.events import (
    RunErrorEvent,
    StreamCompleteEvent,
    SubAgentEvent,
    TaskFinishedEvent,
    TaskStartedEvent,
    TaskUpdatedEvent,
    ToolCallStartEvent,
)
from agentpool_server.acp_server.v2_translate import UpdateTranslator


if TYPE_CHECKING:
    from collections.abc import Callable

    from agentpool.agents.events import RichAgentStreamEvent
    from agentpool_server.acp_server.event_converter import ACPEventConverter


type Outgoing = list[tuple[str, v2.SessionUpdate]]
"""Updates to send, each with the session ID it goes out under."""

type StopReason = Literal["end_turn", "error", "cancelled"]

_STOP_REASONS: dict[str, StopReason] = {
    "completed": "end_turn",
    "failed": "error",
    "cancelled": "cancelled",
}


@dataclass
class ChildSession:
    """A subagent run shown as a child session."""

    session_id: str
    parent_id: str
    """The session that owns it: the root ACP session, or another child."""
    root_id: str
    """The ACP session whose agent runs it (also nested children's)."""
    converter: ACPEventConverter
    translator: UpdateTranslator
    task_id: str | None = None
    """The task behind it (stopped by ``session/cancel`` when ``stoppable``)."""
    stoppable: bool = False
    background: bool = False
    """Its task goes on after the parent's turn (not cancelled with it)."""
    active: bool = True
    """Its work runs (else it is idle or unknown)."""


@dataclass
class _Task:
    task_id: str
    stoppable: bool
    background: bool


@dataclass
class SubagentSessions:
    """The child sessions of one v2 connection."""

    children: dict[str, ChildSession] = field(default_factory=dict)
    _by_key: dict[tuple[str, str], str] = field(default_factory=dict)
    """Child session ID by (root session, run key)."""
    _tool_calls: dict[str, str] = field(default_factory=dict)
    """The child session a tool call runs in, by tool call ID."""
    _tasks: dict[tuple[str, str], _Task] = field(default_factory=dict)
    """Tasks of subagent runs, by (root session, starting tool call ID)."""

    def child(self, session_id: str) -> ChildSession | None:
        """The child session ``session_id``, if it is one."""
        return self.children.get(session_id)

    def session_of_tool_call(self, tool_call_id: str | None, default: str) -> str:
        """The session a tool call belongs to: a child's, else ``default``."""
        if tool_call_id is None:
            return default
        return self._tool_calls.get(tool_call_id, default)

    def observe(
        self,
        root_id: str,
        event: RichAgentStreamEvent[Any],
        *,
        converter: Callable[[], ACPEventConverter],
        cwd: str | None,
    ) -> _Pending:
        """What an event of root session ``root_id`` means for its children.

        Returns the updates to send; for a ``SubAgentEvent`` also its converted updates
        (``_Pending.convert``), since conversion is asynchronous.
        """
        pending = _Pending()
        match event:
            case TaskStartedEvent(kind="agent", tool_call_id=str(tool_call_id)):
                task = _Task(event.task_id, event.stoppable, event.background)
                self._tasks[root_id, tool_call_id] = task
                if (child := self._child_of(root_id, tool_call_id)) is not None:
                    pending.out += self._adopt(child, task)
            case TaskUpdatedEvent(task_id=task_id, background=bool(background)):
                for child in self.children.values():
                    if child.task_id == task_id:
                        child.background = background
            case TaskFinishedEvent(task_id=task_id, status=status):
                for child in [c for c in self.children.values() if c.task_id == task_id]:
                    pending.out += self._finish(child, _STOP_REASONS[status])
            case SubAgentEvent():
                child, announced = self._child_for(root_id, event, converter=converter, cwd=cwd)
                pending.out += announced
                pending.child = child
                pending.inner = event.event
                match event.event:
                    case ToolCallStartEvent(tool_call_id=tool_call_id):
                        self._tool_calls[tool_call_id] = child.session_id
                    case StreamCompleteEvent():
                        pending.ends = "end_turn"
                    case RunErrorEvent():
                        pending.ends = "error"
                    case _:
                        pass
            case _:
                pass
        return pending

    def end_turn(self, root_id: str, *, cancelled: bool) -> Outgoing:
        """The parent's turn ended: its foreground children stop (cancelled) or go unknown."""
        out: Outgoing = []
        for child in list(self.children.values()):
            if child.root_id != root_id or not child.active or child.background:
                continue
            if cancelled:  # A cancel stops the work the turn was waiting on
                out += self._finish(child, "cancelled")
            else:  # Nothing reports its end any more
                child.active = False
                out += self._state(child, v2.UnknownStateUpdate())
        return out

    def finish(self, child: ChildSession, stop_reason: StopReason) -> Outgoing:
        """Child ``child``'s work ended (idle with ``stop_reason``)."""
        return self._finish(child, stop_reason)

    def _child_of(self, root_id: str, tool_call_id: str) -> ChildSession | None:
        child_id = self._by_key.get((root_id, tool_call_id))
        return self.children.get(child_id) if child_id is not None else None

    def _child_for(
        self,
        root_id: str,
        event: SubAgentEvent,
        *,
        converter: Callable[[], ACPEventConverter],
        cwd: str | None,
    ) -> tuple[ChildSession, Outgoing]:
        """The child an event's run is, announced (with its link) when it is new."""
        key = event.parent_tool_call_id or f"{event.source_name}:{event.depth}"
        if (child_id := self._by_key.get((root_id, key))) is not None:
            child = self.children[child_id]
            if child.active:
                return child, []
            # A finished run's next one is new work: a new child (IDs aren't reused)
        parent_id = self.session_of_tool_call(event.parent_tool_call_id, root_id)
        child = ChildSession(
            session_id=f"{root_id}:subagent:{uuid.uuid4().hex[:12]}",
            parent_id=parent_id,
            root_id=root_id,
            converter=converter(),
            translator=UpdateTranslator(message_id=str(uuid.uuid4())),
        )
        self.children[child.session_id] = child
        self._by_key[root_id, key] = child.session_id
        task = self._tasks.get((root_id, key))
        if task is not None:
            child.task_id, child.stoppable = task.task_id, task.stoppable
            child.background = task.background
        announce = v2.SubagentUpdate(
            session_id=child.session_id,
            title=event.source_name,
            state=v2.RunningStateUpdate(),
            capabilities=_capabilities(child),
        )
        if cwd is not None and PurePath(cwd).is_absolute():
            announce.cwd = cwd
        out: Outgoing = [(parent_id, announce)]
        if event.parent_tool_call_id is not None:  # The tool call that started it shows it
            reference = v2.ToolCallContentChunk(
                tool_call_id=event.parent_tool_call_id,
                content=v2.SessionReference(session_id=child.session_id),
            )
            out.append((parent_id, reference))
        out.append((child.session_id, v2.RunningStateSessionUpdate()))
        return child, out

    def _adopt(self, child: ChildSession, task: _Task) -> Outgoing:
        """A task reported after its child was announced: offer cancel if it can stop."""
        child.task_id, child.stoppable, child.background = (
            task.task_id,
            task.stoppable,
            task.background,
        )
        if not task.stoppable or not child.active:
            return []
        patch = v2.SubagentUpdate(session_id=child.session_id, capabilities=_capabilities(child))
        return [(child.parent_id, patch)]

    def _finish(self, child: ChildSession, stop_reason: StopReason) -> Outgoing:
        if not child.active:
            return []
        child.active = False
        out = self._state(child, v2.IdleStateUpdate(stop_reason=stop_reason))
        if child.stoppable:  # Nothing left to cancel
            withdraw = v2.SubagentUpdate(session_id=child.session_id, capabilities=None)
            out.append((child.parent_id, withdraw))
        return out

    def _state(self, child: ChildSession, state: v2.StateUpdate) -> Outgoing:
        """``state`` on the child, mirrored on its parent's association (same snapshot)."""
        own = state_session_update(state)
        mirror = v2.SubagentUpdate(session_id=child.session_id, state=state)
        return [(child.session_id, own), (child.parent_id, mirror)]


@dataclass
class _Pending:
    """What observing an event asks to send."""

    out: Outgoing = field(default_factory=list)
    child: ChildSession | None = None
    """The child a ``SubAgentEvent`` belongs to (its ``inner`` event to convert)."""
    inner: RichAgentStreamEvent[Any] | None = None
    ends: StopReason | None = None
    """The inner event ends the child's run."""


def _capabilities(child: ChildSession) -> v2.SubagentSessionCapabilities | None:
    if not child.stoppable:
        return None
    return v2.SubagentSessionCapabilities(cancel=v2.SessionCancelCapabilities())


def state_session_update(state: v2.StateUpdate) -> v2.StateSessionUpdate:
    """``state`` as a ``state_update`` of the session itself."""
    match state:
        case v2.IdleStateUpdate():
            return v2.IdleStateSessionUpdate(stop_reason=state.stop_reason)
        case v2.RunningStateUpdate():
            return v2.RunningStateSessionUpdate()
        case v2.RequiresActionStateUpdate():
            return v2.RequiresActionStateSessionUpdate()
        case _:
            return v2.UnknownStateSessionUpdate()
