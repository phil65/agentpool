"""Pydantic models for the cron scheduling system."""

from typing import Literal

from pydantic import BaseModel, Field


class CronSchedule(BaseModel):
    """Schedule definition for a cron job.

    Supports three scheduling modes:
    - ``at``: one-shot execution at a specific timestamp (milliseconds)
    - ``every``: recurring execution at a fixed interval (milliseconds)
    - ``cron``: recurring execution using a cron expression with optional timezone
    """

    kind: Literal["at", "every", "cron"]
    at_ms: int | None = None
    every_ms: int | None = None
    expr: str | None = None
    tz: str | None = None


class CronPayload(BaseModel):
    """What to execute when the job fires."""

    kind: Literal["system_event", "agent_turn"] = "agent_turn"
    message: str = ""
    deliver: bool = False
    channel: str | None = None
    to: str | None = None


class CronJobState(BaseModel):
    """Runtime state of a scheduled job."""

    next_run_at_ms: int | None = None
    last_run_at_ms: int | None = None
    last_status: Literal["ok", "error", "skipped"] | None = None
    last_error: str | None = None


class CronJob(BaseModel):
    """A single scheduled job with its schedule, payload, and runtime state."""

    id: str
    name: str
    enabled: bool = True
    schedule: CronSchedule = Field(default_factory=lambda: CronSchedule(kind="every"))
    payload: CronPayload = Field(default_factory=CronPayload)
    state: CronJobState = Field(default_factory=CronJobState)
    created_at_ms: int = 0
    updated_at_ms: int = 0
    delete_after_run: bool = False


class CronStore(BaseModel):
    """Persistent store for cron jobs, serialized to JSON on disk."""

    version: int = 1
    jobs: list[CronJob] = Field(default_factory=list)
