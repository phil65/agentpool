"""Tests for the cron service."""

import asyncio
from typing import TYPE_CHECKING

from agentpool.utils.time_utils import now_ms
from agentpool_bot.cron.cron_types import CronSchedule
from agentpool_bot.cron.service import CronService


if TYPE_CHECKING:
    from pathlib import Path

    from agentpool_bot.cron.cron_types import CronJob


async def test_job_adding_a_job_is_not_cancelled(tmp_path: Path):
    """A job that calls ``add_job`` while it runs must not cancel itself."""
    events: list[tuple[str, str]] = []
    service: CronService | None = None

    async def on_job(job: CronJob) -> None:
        assert service is not None
        events.append(("start", job.name))
        if job.name == "a":
            service.add_job("b", CronSchedule(kind="every", every_ms=3_600_000), "later")
        try:
            await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            events.append(("cancelled", job.name))
            raise
        events.append(("done", job.name))

    service = CronService(tmp_path / "store.json", on_job=on_job)
    await service.start()
    try:
        service.add_job("a", CronSchedule(kind="at", at_ms=now_ms() + 20), "now")
        for _ in range(50):  # bounded wait: at most ~1 s
            if ("done", "a") in events or len(events) > 10:
                break
            await asyncio.sleep(0.02)
        await asyncio.sleep(0.1)  # a runaway loop would add more events here
        # the timer was re-armed for the job added during the run
        assert service._timer_task is not None
        assert not service._timer_task.done()
    finally:
        service.stop()

    assert events == [("start", "a"), ("done", "a")]
    jobs = service.list_jobs(include_disabled=True)
    assert sorted(j.name for j in jobs) == ["a", "b"]
    job_a = next(j for j in jobs if j.name == "a")
    assert job_a.state.last_status == "ok"
    assert not job_a.enabled
