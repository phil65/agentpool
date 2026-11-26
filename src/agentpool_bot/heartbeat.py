"""Heartbeat service — periodic agent wake-up to check for tasks.

The service reads ``HEARTBEAT.md`` from the workspace directory at a
configurable interval. When the file contains actionable content the
``on_heartbeat`` callback is invoked (typically wired to an agentpool
agent) so the agent can process whatever tasks are listed.
"""

import asyncio
from typing import TYPE_CHECKING

from agentpool.log import get_logger


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path


logger = get_logger(__name__)

DEFAULT_HEARTBEAT_INTERVAL_S = 30 * 60  # 30 minutes

HEARTBEAT_PROMPT = (
    "Read HEARTBEAT.md in your workspace (if it exists).\n"
    "Follow any instructions or tasks listed there.\n"
    "If nothing needs attention, reply with just: HEARTBEAT_OK"
)

HEARTBEAT_OK_TOKEN = "HEARTBEAT_OK"


def _is_heartbeat_empty(content: str | None) -> bool:
    """Return True when ``HEARTBEAT.md`` has no actionable content.

    Lines that are blank, headers, HTML comments, or checkbox markers
    are considered non-actionable.
    """
    if not content:
        return True

    skip_markers = {"- [ ]", "* [ ]", "- [x]", "* [x]"}

    for line in content.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            continue
        if stripped.startswith("<!--"):
            continue
        if stripped in skip_markers:
            continue
        return False

    return True


class HeartbeatService:
    """Periodic heartbeat that wakes the agent to check for tasks.

    The agent reads ``HEARTBEAT.md`` from *workspace* and executes any
    tasks listed there. If nothing needs attention it replies with the
    ``HEARTBEAT_OK`` token which the service recognises as a no-op.

    Args:
        workspace: Root workspace directory containing ``HEARTBEAT.md``.
        on_heartbeat: Async callback ``(prompt) -> response`` that
            processes the heartbeat prompt through the agent.
        interval_s: Seconds between heartbeat checks.
        enabled: Whether the service is active.
    """

    def __init__(
        self,
        workspace: Path,
        on_heartbeat: Callable[[str], Awaitable[str]] | None = None,
        interval_s: int = DEFAULT_HEARTBEAT_INTERVAL_S,
        *,
        enabled: bool = True,
    ) -> None:
        self.workspace = workspace
        self.on_heartbeat = on_heartbeat
        self.interval_s = interval_s
        self.enabled = enabled
        self._running = False
        self._task: asyncio.Task[None] | None = None

    @property
    def heartbeat_file(self) -> Path:
        """Path to the ``HEARTBEAT.md`` file in the workspace."""
        return self.workspace / "HEARTBEAT.md"

    def _read_heartbeat_file(self) -> str | None:
        """Read ``HEARTBEAT.md`` content, or None if missing/unreadable."""
        if not self.heartbeat_file.exists():
            return None
        try:
            return self.heartbeat_file.read_text(encoding="utf-8")
        except Exception:
            logger.exception("Failed to read heartbeat file")
            return None

    # ── Lifecycle ────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the heartbeat loop."""
        if not self.enabled:
            logger.info("Heartbeat disabled")
            return

        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("Heartbeat started", interval_s=self.interval_s)

    def stop(self) -> None:
        """Stop the heartbeat loop."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            self._task = None

    # ── Internal loop ────────────────────────────────────────────────

    async def _run_loop(self) -> None:
        """Sleep → tick → repeat until stopped."""
        while self._running:
            try:
                await asyncio.sleep(self.interval_s)
                if self._running:
                    await self._tick()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Heartbeat error")

    async def _tick(self) -> None:
        """Execute a single heartbeat check."""
        content = self._read_heartbeat_file()

        if _is_heartbeat_empty(content):
            logger.debug("Heartbeat: no tasks (HEARTBEAT.md empty or missing)")
            return

        logger.info("Heartbeat: checking for tasks...")

        if self.on_heartbeat is None:
            logger.warning("Heartbeat: no callback configured, skipping")
            return

        try:
            response = await self.on_heartbeat(HEARTBEAT_PROMPT)

            normalised = response.upper().replace("_", "")
            if HEARTBEAT_OK_TOKEN.replace("_", "") in normalised:
                logger.info("Heartbeat: OK (no action needed)")
            else:
                logger.info("Heartbeat: completed task")
        except Exception:
            logger.exception("Heartbeat execution failed")

    # ── Manual trigger ───────────────────────────────────────────────

    async def trigger_now(self) -> str | None:
        """Manually trigger a heartbeat, bypassing the interval timer.

        Returns:
            The agent's response, or None if no callback is configured.
        """
        if self.on_heartbeat is None:
            return None
        return await self.on_heartbeat(HEARTBEAT_PROMPT)
