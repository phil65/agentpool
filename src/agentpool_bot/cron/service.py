"""Cron service for scheduling and executing agent tasks."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING
import uuid

from agentpool.log import get_logger
from agentpool.utils.time_utils import now_ms
from agentpool_bot.cron.cron_types import CronJob, CronJobState, CronPayload, CronStore


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path

    from agentpool_bot.cron.cron_types import CronSchedule


logger = get_logger(__name__)


def _compute_next_run(schedule: CronSchedule, now_ms: int) -> int | None:  # noqa: PLR0911
    """Compute the next run time in milliseconds for the given schedule.

    Args:
        schedule: The schedule definition.
        now_ms: Current time in milliseconds.

    Returns:
        Next run timestamp in milliseconds, or None if no future run.
    """
    match schedule.kind:
        case "at":
            if schedule.at_ms is not None and schedule.at_ms > now_ms:
                return schedule.at_ms
            return None

        case "every":
            if schedule.every_ms is None or schedule.every_ms <= 0:
                return None
            return now_ms + schedule.every_ms

        case "cron":
            if not schedule.expr:
                return None
            try:
                from zoneinfo import ZoneInfo

                from croniter import croniter

                base_time = now_ms / 1000
                tz = ZoneInfo(schedule.tz) if schedule.tz else datetime.now().astimezone().tzinfo
                base_dt = datetime.fromtimestamp(base_time, tz=tz)
                cron = croniter(schedule.expr, base_dt)
                next_dt: datetime = cron.get_next(datetime)
                return int(next_dt.timestamp() * 1000)
            except Exception:
                logger.exception(
                    "Failed to compute next cron run",
                    expr=schedule.expr,
                    tz=schedule.tz,
                )
                return None


class CronService:
    """Service for managing and executing scheduled jobs.

    Jobs are persisted to a JSON file and executed via an async timer loop.
    The ``on_job`` callback is invoked when a job fires — wire it to your
    agent to process the job's payload.
    """

    def __init__(
        self,
        store_path: Path,
        on_job: Callable[[CronJob], Awaitable[str | None]] | None = None,
    ) -> None:
        self.store_path = store_path
        self.on_job = on_job
        self._store: CronStore | None = None
        self._timer_task: asyncio.Task[None] | None = None
        self._running = False
        self._in_on_timer = False

    # ── Persistence ──────────────────────────────────────────────────

    def _load_store(self) -> CronStore:
        """Load jobs from disk (cached after first load)."""
        if self._store is not None:
            return self._store

        if self.store_path.exists():
            try:
                raw = self.store_path.read_text(encoding="utf-8")
                self._store = CronStore.model_validate_json(raw)
            except Exception:
                logger.exception("Failed to load cron store, starting fresh")
                self._store = CronStore()
        else:
            self._store = CronStore()

        return self._store

    def _save_store(self) -> None:
        """Save jobs to disk."""
        if self._store is None:
            return

        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        self.store_path.write_text(
            self._store.model_dump_json(indent=2),
            encoding="utf-8",
        )

    # ── Lifecycle ────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the cron service: load store, recompute schedules, arm timer."""
        self._running = True
        store = self._load_store()
        self._recompute_next_runs()
        self._save_store()
        self._arm_timer()
        logger.info("Cron service started", job_count=len(store.jobs))

    def stop(self) -> None:
        """Stop the cron service and cancel pending timer."""
        self._running = False
        if self._timer_task is not None:
            self._timer_task.cancel()
            self._timer_task = None

    # ── Timer machinery ──────────────────────────────────────────────

    def _recompute_next_runs(self) -> None:
        """Recompute next run times for all enabled jobs."""
        if self._store is None:
            return
        now = now_ms()
        for job in self._store.jobs:
            if job.enabled:
                job.state.next_run_at_ms = _compute_next_run(job.schedule, now)

    def _get_next_wake_ms(self) -> int | None:
        """Get the earliest ``next_run_at_ms`` across all enabled jobs."""
        if self._store is None:
            return None
        times = [
            j.state.next_run_at_ms
            for j in self._store.jobs
            if j.enabled and j.state.next_run_at_ms is not None
        ]
        return min(times) if times else None

    def _arm_timer(self) -> None:
        """Schedule a single asyncio task that fires at the next due time."""
        if self._in_on_timer:
            # Called from a running job (e.g. ``add_job``): cancelling the timer task would
            # cancel the job itself. ``_on_timer`` re-arms once the due jobs are done.
            return
        if self._timer_task is not None and not self._timer_task.done():
            self._timer_task.cancel()

        next_wake = self._get_next_wake_ms()
        if next_wake is None or not self._running:
            return

        delay_s = max(0, (next_wake - now_ms())) / 1000

        async def _tick() -> None:
            await asyncio.sleep(delay_s)
            if self._running:
                await self._on_timer()

        self._timer_task = asyncio.create_task(_tick())

    async def _on_timer(self) -> None:
        """Run all due jobs, persist state, and re-arm the timer."""
        if self._store is None:
            return

        now = now_ms()
        due_jobs = [
            j
            for j in self._store.jobs
            if j.enabled and j.state.next_run_at_ms is not None and now >= j.state.next_run_at_ms
        ]

        self._in_on_timer = True
        try:
            for job in due_jobs:
                await self._execute_job(job)
        finally:
            self._in_on_timer = False
        self._save_store()
        self._timer_task = None  # the current task: finishing, nothing to cancel
        self._arm_timer()

    async def _execute_job(self, job: CronJob) -> None:
        """Execute a single job and update its state."""
        start_ms = now_ms()
        logger.info("Cron: executing job", job_name=job.name, job_id=job.id)

        try:
            if self.on_job is not None:
                await self.on_job(job)
            job.state.last_status = "ok"
            job.state.last_error = None
            logger.info("Cron: job completed", job_name=job.name)
        except Exception as exc:
            logger.exception("Cron: job failed", job_name=job.name)
            job.state.last_status = "error"
            job.state.last_error = str(exc)

        job.state.last_run_at_ms = start_ms
        job.updated_at_ms = now_ms()

        # Handle one-shot ("at") jobs
        if job.schedule.kind == "at":
            if job.delete_after_run:
                assert self._store is not None
                self._store.jobs = [j for j in self._store.jobs if j.id != job.id]
            else:
                job.enabled = False
                job.state.next_run_at_ms = None
        else:
            job.state.next_run_at_ms = _compute_next_run(job.schedule, now_ms())

    # ── Public API ───────────────────────────────────────────────────

    def list_jobs(self, *, include_disabled: bool = False) -> list[CronJob]:
        """List jobs, sorted by next run time.

        Args:
            include_disabled: Whether to include disabled jobs.
        """
        store = self._load_store()
        jobs = store.jobs if include_disabled else [j for j in store.jobs if j.enabled]
        return sorted(jobs, key=lambda j: j.state.next_run_at_ms or float("inf"))

    def add_job(
        self,
        name: str,
        schedule: CronSchedule,
        message: str,
        *,
        deliver: bool = False,
        channel: str | None = None,
        to: str | None = None,
        delete_after_run: bool = False,
    ) -> CronJob:
        """Add a new scheduled job.

        Args:
            name: Human-readable job name.
            schedule: When the job should run.
            message: The message/prompt to send to the agent.
            deliver: Whether to deliver the response to a channel.
            channel: Target channel for delivery.
            to: Target chat/user ID for delivery.
            delete_after_run: Remove the job after its first execution.

        Returns:
            The newly created job.
        """
        store = self._load_store()
        now = now_ms()

        job = CronJob(
            id=str(uuid.uuid4())[:8],
            name=name,
            enabled=True,
            schedule=schedule,
            payload=CronPayload(
                kind="agent_turn",
                message=message,
                deliver=deliver,
                channel=channel,
                to=to,
            ),
            state=CronJobState(next_run_at_ms=_compute_next_run(schedule, now)),
            created_at_ms=now,
            updated_at_ms=now,
            delete_after_run=delete_after_run,
        )

        store.jobs.append(job)
        self._save_store()
        self._arm_timer()

        logger.info("Cron: added job", job_name=name, job_id=job.id)
        return job

    def remove_job(self, job_id: str) -> bool:
        """Remove a job by ID.

        Returns:
            True if the job was found and removed.
        """
        store = self._load_store()
        before = len(store.jobs)
        store.jobs = [j for j in store.jobs if j.id != job_id]
        removed = len(store.jobs) < before

        if removed:
            self._save_store()
            self._arm_timer()
            logger.info("Cron: removed job", job_id=job_id)

        return removed

    def enable_job(self, job_id: str, *, enabled: bool = True) -> CronJob | None:
        """Enable or disable a job.

        Args:
            job_id: The job to modify.
            enabled: Whether to enable or disable.

        Returns:
            The updated job, or None if not found.
        """
        store = self._load_store()
        for job in store.jobs:
            if job.id == job_id:
                job.enabled = enabled
                job.updated_at_ms = now_ms()
                if enabled:
                    job.state.next_run_at_ms = _compute_next_run(job.schedule, now_ms())
                else:
                    job.state.next_run_at_ms = None
                self._save_store()
                self._arm_timer()
                return job
        return None

    async def run_job(self, job_id: str, *, force: bool = False) -> bool:
        """Manually trigger a job.

        Args:
            job_id: The job to run.
            force: Run even if the job is disabled.

        Returns:
            True if the job was found and executed.
        """
        store = self._load_store()
        for job in store.jobs:
            if job.id == job_id:
                if not force and not job.enabled:
                    return False
                await self._execute_job(job)
                self._save_store()
                self._arm_timer()
                return True
        return False

    def status(self) -> dict[str, object]:
        """Get a summary of the service state."""
        store = self._load_store()
        return {
            "enabled": self._running,
            "jobs": len(store.jobs),
            "next_wake_at_ms": self._get_next_wake_ms(),
        }
