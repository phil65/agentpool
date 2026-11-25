"""Cron scheduling toolset — lets agents manage scheduled jobs at runtime."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Self

from agentpool.log import get_logger
from agentpool.resource_providers import ResourceProvider
from agentpool.utils.time_utils import datetime_to_ms


if TYPE_CHECKING:
    from collections.abc import Sequence
    from types import TracebackType

    from agentpool.tools.base import Tool
    from agentpool_bot.cron.cron_types import CronSchedule
    from agentpool_bot.cron.service import CronService


logger = get_logger(__name__)


class CronTools(ResourceProvider):
    """Provider that exposes cron job management tools.

    Wraps a ``CronService`` and gives agents the ability to
    add, list, and remove scheduled jobs at runtime.

    The provider manages the service lifecycle: calling ``__aenter__``
    starts the service and ``__aexit__`` stops it.

    Args:
        service: The cron service instance to wrap.
        name: Provider name.
    """

    kind = "tools"

    def __init__(self, service: CronService, name: str = "cron") -> None:
        super().__init__(name=name)
        self.service = service
        self._tools: list[Tool] | None = None

    async def __aenter__(self) -> Self:
        """Start the cron service."""
        await self.service.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Stop the cron service."""
        self.service.stop()

    async def get_tools(self) -> Sequence[Tool]:
        """Return cron management tools."""
        if self._tools is not None:
            return self._tools

        self._tools = [
            self.create_tool(self.cron_add, category="other"),
            self.create_tool(self.cron_list, category="read", read_only=True, idempotent=True),
            self.create_tool(self.cron_remove, category="delete"),
        ]
        return self._tools

    async def cron_add(
        self,
        message: str,
        name: str | None = None,
        every_seconds: int | None = None,
        cron_expr: str | None = None,
        tz: str | None = None,
        at: str | None = None,
    ) -> str:
        """Schedule a new cron job.

        Exactly one of ``every_seconds``, ``cron_expr``, or ``at`` must be
        provided to define when the job fires.

        Args:
            message: The prompt / reminder text for the job.
            name: Optional human-readable label (defaults to first 30 chars of message).
            every_seconds: Run every N seconds (recurring).
            cron_expr: Cron expression like ``0 9 * * *`` (recurring).
            tz: IANA timezone for cron expressions (e.g. ``America/Vancouver``).
            at: ISO datetime for one-shot execution (e.g. ``2026-03-01T10:30:00``).

        Returns:
            Confirmation with the job id.
        """
        from agentpool_bot.cron.cron_types import CronSchedule

        if tz and not cron_expr:
            return "Error: tz can only be used with cron_expr"
        if tz:
            from zoneinfo import ZoneInfo

            try:
                ZoneInfo(tz)
            except KeyError, ValueError:
                return f"Error: unknown timezone {tz!r}"

        schedule: CronSchedule
        delete_after = False

        if every_seconds is not None:
            schedule = CronSchedule(kind="every", every_ms=every_seconds * 1000)
        elif cron_expr is not None:
            schedule = CronSchedule(kind="cron", expr=cron_expr, tz=tz)
        elif at is not None:
            try:
                dt = datetime.fromisoformat(at)
            except ValueError:
                return f"Error: invalid ISO datetime {at!r}"
            schedule = CronSchedule(kind="at", at_ms=datetime_to_ms(dt))
            delete_after = True
        else:
            return "Error: provide one of every_seconds, cron_expr, or at"

        job = self.service.add_job(
            name=name or message[:30],
            schedule=schedule,
            message=message,
            delete_after_run=delete_after,
        )
        return f"Created job {job.name!r} (id: {job.id})"

    async def cron_list(self) -> str:
        """List all active scheduled jobs.

        Returns:
            Formatted list of jobs, or a message if none exist.
        """
        jobs = self.service.list_jobs()
        if not jobs:
            return "No scheduled jobs."
        lines = [
            f"- {j.name} (id: {j.id}, {j.schedule.kind}, message: {j.payload.message[:50]})"
            for j in jobs
        ]
        return "Scheduled jobs:\n" + "\n".join(lines)

    async def cron_remove(self, job_id: str) -> str:
        """Remove a scheduled job.

        Args:
            job_id: The id of the job to remove.

        Returns:
            Confirmation or error if the job was not found.
        """
        if self.service.remove_job(job_id):
            return f"Removed job {job_id}"
        return f"Job {job_id} not found"
