"""A Claude Code CLI process: the backend of one session of a ClaudeCodeAgent.

The CLI runs one conversation per process, so the agent runs a process per session it
serves (``concurrent_sessions``): the default session's, started with the agent, and one
per explicit session, started on its first turn and resumed from its Claude session
(``sdk_session_id``) when it was closed. Idle processes beyond the agent's limit are
closed, least recently used first.

The process is connected from within its session's context (runtime and environment):
the SDK's reader and the tasks it starts for callbacks and hooks inherit it, and so does
the idle watcher, so they act for that session.
"""

from dataclasses import dataclass, field
import time
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    import asyncio

    from clawd_code_sdk import ClaudeSDKClient
    from clawd_code_sdk.models import PermissionMode

    from agentpool.agents.claude_code_agent.idle_compaction import IdleCompaction
    from agentpool.agents.claude_code_agent.idle_watch import IdleWatcher
    from agentpool.agents.claude_code_agent.steering import RunSteering


@dataclass
class CliProcess:
    """One CLI process and what runs on it."""

    key: str
    """The session it serves: its ID, or ``""`` for the agent's default session."""

    idle_watch: IdleWatcher
    """Reads what the CLI sends between runs."""

    idle_compaction: IdleCompaction | None = None
    """Compacts the conversation before the prompt cache expires (if enabled)."""

    client: ClaudeSDKClient | None = None
    connection_task: asyncio.Task[None] | None = None
    """Connects the client (in the background, to reduce first-prompt latency)."""

    steering: RunSteering | None = None
    """Messages steered into the running turn (None outside of runs)."""

    open_user_inputs: int = 0
    """Permission requests and questions waiting for the user."""

    titled_session: str | None = None
    """The session a title was asked for (once per session)."""

    title_task: asyncio.Task[None] | None = None

    pre_plan_mode: PermissionMode | None = None
    """The permission mode to return to when plan mode ends."""

    instructions: str = ""
    """The session's instructions as the process has them (its system prompt, then updates)."""

    last_used: float = field(default_factory=time.monotonic)
    """When a turn last started on it (closing idle processes: least recent first)."""

    turns: int = 0
    """Turns of the agent in progress on it (from before it connects to their end)."""

    @property
    def idle(self) -> bool:
        """Connected, no turn runs on it, the CLI runs none of its own, nobody is asked."""
        connecting = self.connection_task is not None and not self.connection_task.done()
        return (
            not connecting
            and not self.turns
            and self.steering is None
            and not self.idle_watch.busy
            and not self.open_user_inputs
        )
