"""Task-related exceptions."""

from __future__ import annotations

from agentpool.utils.baseregistry import AgentPoolError


class JobError(AgentPoolError):
    """General task-related exception."""


class JobRegistrationError(JobError):
    """Task could not get registered."""
