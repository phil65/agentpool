"""The terminals of a v2 session: agent-owned and display-only.

v1 tool calls point to terminals the client owns (``terminal/create``); in v2 the agent
owns them and reports their state with ``terminal_update`` (command, exit status, output
snapshots) and ``terminal_output_chunk`` (appended output). ``TerminalTracker`` derives
those updates from the agent's events:

- process events (``ToolCallProgressEvent.process``: start, output, exit or kill),
- the ``_meta`` display-terminal convention (``terminal_info`` / ``terminal_output`` /
  ``terminal_exit``, used by the Claude Code agent),
- any other terminal a tool call references: it exists from then on, and exits (status
  unknown) with its tool call when nothing reported its end.

Processes outlive the tool call that started them (``start_process``, then
``get_process_output`` in another call): their terminals end with their process, or
when a cancelled turn leaves their tool call unfinished.
"""

from typing import TYPE_CHECKING, Any

from aclientp.schema import v2
from pydantic import MISSING

from agentpool.agents.events import (
    TerminalContentItem,
    ToolCallCompleteEvent,
    ToolCallProgressEvent,
    ToolCallStartEvent,
)


if TYPE_CHECKING:
    from collections.abc import Sequence

    from agentpool.agents.events import ProcessSignal, RichAgentStreamEvent


class TerminalTracker:
    """Turns a session's events into v2 terminal updates."""

    def __init__(self, cwd: str | None = None) -> None:
        self.cwd = cwd
        """Working directory reported for started commands (absolute)."""
        self._owner: dict[str, str] = {}
        """Terminals not exited yet, with the tool call that references them."""
        self._known: set[str] = set()
        """Every terminal the client has heard of."""
        self._processes: set[str] = set()
        """Terminals of processes, which outlive their tool call."""

    def updates(self, event: RichAgentStreamEvent[Any]) -> list[v2.SessionUpdate]:
        """The terminal updates to send before ``event``'s own updates."""
        match event:
            case (
                ToolCallStartEvent(tool_call_id=tool_call_id, content=content, field_meta=meta)
                | ToolCallProgressEvent(tool_call_id=tool_call_id, items=content, field_meta=meta)
            ):
                updates: list[v2.SessionUpdate] = []
                if isinstance(event, ToolCallProgressEvent) and event.process is not None:
                    updates += self._process(tool_call_id, event.process)
                updates += self._meta(tool_call_id, meta)
                return updates + self._referenced(tool_call_id, content)
            case ToolCallCompleteEvent(tool_call_id=tool_call_id):
                ended = [
                    t
                    for t, owner in self._owner.items()
                    if owner == tool_call_id and t not in self._processes
                ]
                return [self._exit(terminal_id) for terminal_id in ended]
            case _:
                return []

    def end(self, tool_call_ids: Sequence[str]) -> list[v2.SessionUpdate]:
        """Exit (code unknown) the running terminals of tool calls that will not finish."""
        ended = [t for t, owner in self._owner.items() if owner in tool_call_ids]
        return [self._exit(terminal_id) for terminal_id in ended]

    def _start(
        self, terminal_id: str, tool_call_id: str, command: str | None = None
    ) -> v2.TerminalUpdate:
        self._known.add(terminal_id)
        self._owner[terminal_id] = tool_call_id
        return v2.TerminalUpdate(
            terminal_id=terminal_id,
            command=command if command is not None else MISSING,
            cwd=self.cwd if command is not None and self.cwd else MISSING,
        )

    def _exit(
        self, terminal_id: str, exit_code: int | None = None, signal: str | None = None
    ) -> v2.TerminalUpdate:
        self._owner.pop(terminal_id, None)
        status = v2.TerminalExitStatus(
            exit_code=MISSING if exit_code is None else exit_code,
            signal=MISSING if signal is None else signal,
        )
        return v2.TerminalUpdate(terminal_id=terminal_id, exit_status=status)

    def _referenced(self, tool_call_id: str, content: Sequence[Any]) -> list[v2.SessionUpdate]:
        """Terminals a tool call shows that the client has not heard of yet."""
        return [
            self._start(item.terminal_id, tool_call_id)
            for item in content
            if isinstance(item, TerminalContentItem) and item.terminal_id not in self._known
        ]

    def _process(self, tool_call_id: str, signal: ProcessSignal) -> list[v2.SessionUpdate]:
        terminal_id = signal.terminal_id
        updates: list[v2.SessionUpdate] = []
        if signal.command is not None or terminal_id not in self._known:
            updates.append(self._start(terminal_id, tool_call_id, signal.command))
        self._processes.add(terminal_id)
        if signal.snapshot:
            snapshot = v2.TerminalOutput.from_bytes((signal.output or "").encode())
            updates.append(v2.TerminalUpdate(terminal_id=terminal_id, output=snapshot))
        elif signal.output:
            data = signal.output.encode()
            updates.append(v2.TerminalOutputChunk.from_bytes(terminal_id, data))
        if signal.exited:
            killed = "SIGKILL" if signal.killed else None
            updates.append(self._exit(terminal_id, signal.exit_code, killed))
        return updates

    def _meta(self, tool_call_id: str, meta: dict[str, Any] | None) -> list[v2.SessionUpdate]:
        """Updates from the ``_meta`` display-terminal convention."""
        if not meta:
            return []
        updates: list[v2.SessionUpdate] = []
        info = meta.get("terminal_info")
        if isinstance(info, dict) and (tid := info.get("terminal_id")) and tid not in self._owner:
            updates.append(self._start(str(tid), tool_call_id))
        if isinstance(out := meta.get("terminal_output"), dict) and (tid := out.get("terminal_id")):
            snapshot = v2.TerminalOutput.from_bytes(str(out.get("data") or "").encode())
            updates.append(v2.TerminalUpdate(terminal_id=str(tid), output=snapshot))
        if isinstance(ended := meta.get("terminal_exit"), dict) and (
            tid := ended.get("terminal_id")
        ):
            code = ended.get("exit_code")
            signal = ended.get("signal")
            updates.append(
                self._exit(
                    str(tid),
                    code if isinstance(code, int) else None,
                    signal if isinstance(signal, str) else None,
                )
            )
        return updates
