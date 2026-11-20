"""ACP Agent configuration models."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from agentpool.models.acp_agents.base import (
    ACPAgentConfig,
    BaseACPAgentConfig,
    RegistryACPAgentConfig,
    RemoteACPAgentConfig,
)

# Union of all ACP agent config types (discriminated by 'provider')
ACPAgentConfigTypes = Annotated[
    ACPAgentConfig | RegistryACPAgentConfig | RemoteACPAgentConfig,
    Field(discriminator="provider"),
]

__all__ = [
    "ACPAgentConfig",
    "ACPAgentConfigTypes",
    "BaseACPAgentConfig",
    "RegistryACPAgentConfig",
    "RemoteACPAgentConfig",
]
