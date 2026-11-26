"""Durable execution configuration models."""

from datetime import timedelta
from typing import Annotated, Literal

from pydantic import ConfigDict, Field, field_validator
from schemez import Schema

from agentpool.utils.time_utils import parse_time_period


class BaseDurableExecutionConfig(Schema):
    """Base configuration for durable execution providers."""

    model_config = ConfigDict(
        json_schema_extra={
            "x-icon": "octicon:sync-16",
            "x-doc-title": "Durable Execution Configuration",
            "title": "Base Durable Execution Configuration",
        }
    )

    type: str = Field(init=False, title="Provider type")
    """Durable execution provider type discriminator."""


class TemporalActivityConfig(Schema):
    """Temporal activity configuration options.

    Maps to temporalio.workflow.ActivityConfig.
    """

    start_to_close_timeout: str | timedelta | None = Field(
        default="60s",
        title="Start to close timeout",
        examples=["60s", "5m", "1h"],
    )
    """Maximum time for a single activity execution attempt."""

    schedule_to_close_timeout: str | timedelta | None = Field(
        default=None,
        title="Schedule to close timeout",
        examples=["5m", "1h"],
    )
    """Total time for all activity attempts including retries."""

    schedule_to_start_timeout: str | timedelta | None = Field(
        default=None,
        title="Schedule to start timeout",
        examples=["30s", "2m"],
    )
    """Maximum time activity can wait in task queue."""

    heartbeat_timeout: str | timedelta | None = Field(
        default=None,
        title="Heartbeat timeout",
        examples=["10s", "30s"],
    )
    """Maximum time between activity heartbeats."""

    @field_validator(
        "start_to_close_timeout",
        "schedule_to_close_timeout",
        "schedule_to_start_timeout",
        "heartbeat_timeout",
        mode="before",
    )
    @classmethod
    def parse_duration(cls, v: str | timedelta | None) -> timedelta | None:
        """Parse string duration to timedelta."""
        if v is None or isinstance(v, timedelta):
            return v
        return parse_time_period(v)


class TemporalRetryPolicy(Schema):
    """Temporal retry policy configuration.

    Maps to temporalio.common.RetryPolicy.
    """

    model_config = ConfigDict(json_schema_extra={"title": "Temporal Retry Policy Configuration"})

    initial_interval: str | timedelta = Field(
        default="1s",
        title="Initial retry interval",
        examples=["1s", "5s", "10s"],
    )
    """Initial backoff interval."""

    backoff_coefficient: float = Field(
        default=2.0,
        ge=1.0,
        title="Backoff coefficient",
    )
    """Coefficient for exponential backoff."""

    maximum_interval: str | timedelta | None = Field(
        default=None,
        title="Maximum interval",
        examples=["5m", "10m"],
    )
    """Maximum backoff interval."""

    maximum_attempts: int = Field(
        default=0,
        ge=0,
        title="Maximum attempts",
    )
    """Maximum number of attempts (0 = unlimited)."""

    non_retryable_error_types: list[str] = Field(
        default_factory=list,
        title="Non-retryable error types",
    )
    """Error type names that should not be retried."""

    @field_validator("initial_interval", "maximum_interval", mode="before")
    @classmethod
    def parse_duration(cls, v: str | timedelta | None) -> timedelta | None:
        """Parse string duration to timedelta."""
        if v is None or isinstance(v, timedelta):
            return v
        return parse_time_period(v)


class TemporalDurableConfig(BaseDurableExecutionConfig):
    """Configuration for Temporal durable execution.

    Wraps agents in TemporalAgent for workflow-based execution with
    automatic retries and state persistence.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "x-icon": "simple-icons:temporal",
            "x-doc-title": "Temporal Durable Execution",
            "title": "Temporal Durable Execution Configuration",
        }
    )

    type: Literal["temporal"] = Field("temporal", init=False)
    """Temporal durable execution provider."""

    activity_config: TemporalActivityConfig = Field(
        default_factory=TemporalActivityConfig,
        title="Base activity configuration",
    )
    """Base Temporal activity config for all activities."""

    model_activity_config: TemporalActivityConfig | None = Field(
        default=None,
        title="Model activity configuration",
    )
    """Activity config for model request activities (merged with base)."""

    retry_policy: TemporalRetryPolicy | None = Field(
        default=None,
        title="Retry policy",
    )
    """Retry policy for activities."""

    toolset_activity_config: dict[str, TemporalActivityConfig] | None = Field(
        default=None,
        title="Toolset activity configurations",
    )
    """Per-toolset activity configs keyed by toolset ID."""

    tool_activity_config: dict[str, dict[str, TemporalActivityConfig | bool]] | None = Field(
        default=None,
        title="Tool activity configurations",
    )
    """Per-tool activity configs: {toolset_id: {tool_name: config | False}}."""


class PrefectTaskConfig(Schema):
    """Prefect task configuration options.

    Maps to Prefect task decorator options.
    """

    retries: int = Field(
        default=0,
        ge=0,
        title="Maximum retries",
    )
    """Maximum number of retries for the task."""

    retry_delay_seconds: float | list[float] = Field(
        default=1.0,
        title="Retry delay",
    )
    """Delay between retries (single value or list for custom backoff)."""

    timeout_seconds: float | None = Field(
        default=None,
        title="Task timeout",
    )
    """Maximum time in seconds for task completion."""

    persist_result: bool = Field(
        default=True,
        title="Persist result",
    )
    """Whether to persist task results."""

    log_prints: bool = Field(
        default=False,
        title="Log prints",
    )
    """Whether to capture print statements in task logs."""


class PrefectDurableConfig(BaseDurableExecutionConfig):
    """Configuration for Prefect durable execution.

    Wraps agents in PrefectAgent for flow-based execution with
    automatic task tracking and observability.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "x-icon": "simple-icons:prefect",
            "x-doc-title": "Prefect Durable Execution",
        }
    )

    type: Literal["prefect"] = Field("prefect", init=False)
    """Prefect durable execution provider."""

    model_task_config: PrefectTaskConfig | None = Field(
        default=None,
        title="Model task configuration",
    )
    """Task config for model request tasks."""

    mcp_task_config: PrefectTaskConfig | None = Field(
        default=None,
        title="MCP task configuration",
    )
    """Task config for MCP server tasks."""

    tool_task_config: PrefectTaskConfig | None = Field(
        default=None,
        title="Default tool task configuration",
    )
    """Default task config for tool calls."""

    tool_task_config_by_name: dict[str, PrefectTaskConfig | None] | None = Field(
        default=None,
        title="Per-tool task configurations",
    )
    """Per-tool task configs (None value disables task wrapping)."""

    event_stream_handler_task_config: PrefectTaskConfig | None = Field(
        default=None,
        title="Event handler task configuration",
    )
    """Task config for event stream handler."""


class DBOSStepConfig(Schema):
    """DBOS step configuration options.

    Maps to DBOS step decorator options.
    """

    retries_allowed: bool = Field(
        default=True,
        title="Retries allowed",
    )
    """Whether retries are allowed for this step."""

    interval_seconds: float = Field(
        default=1.0,
        gt=0,
        title="Retry interval",
    )
    """Base interval between retries in seconds."""

    max_attempts: int = Field(
        default=3,
        ge=1,
        title="Maximum attempts",
    )
    """Maximum number of attempts."""

    backoff_rate: float = Field(
        default=2.0,
        ge=1.0,
        title="Backoff rate",
    )
    """Multiplier for exponential backoff."""


class DBOSDurableConfig(BaseDurableExecutionConfig):
    """Configuration for DBOS durable execution.

    Wraps agents in DBOSAgent for step-based execution with
    automatic checkpointing and recovery.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "x-icon": "octicon:database-16",
            "x-doc-title": "DBOS Durable Execution",
        }
    )

    type: Literal["dbos"] = Field("dbos", init=False)
    """DBOS durable execution provider."""

    model_step_config: DBOSStepConfig | None = Field(
        default=None,
        title="Model step configuration",
    )
    """Step config for model request steps."""

    mcp_step_config: DBOSStepConfig | None = Field(
        default=None,
        title="MCP step configuration",
    )
    """Step config for MCP server steps."""


DurableExecutionConfig = Annotated[
    TemporalDurableConfig | PrefectDurableConfig | DBOSDurableConfig,
    Field(discriminator="type"),
]
