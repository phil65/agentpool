"""Resource provider implementations."""

__lazy_modules__ = [
    "agentpool.resource_providers.aggregating",
    "agentpool.resource_providers.base",
    "agentpool.resource_providers.filtering",
    "agentpool.resource_providers.mcp_provider",
    "agentpool.resource_providers.plan_provider",
    "agentpool.resource_providers.resource_info",
    "agentpool.resource_providers.static",
]

from agentpool.resource_providers.base import (
    ProviderKind,
    ResourceChangeEvent,
    ResourceProvider,
    ResourceType,
)
from agentpool.resource_providers.resource_info import ResourceInfo
from agentpool.resource_providers.static import StaticResourceProvider
from agentpool.resource_providers.filtering import FilteringResourceProvider
from agentpool.resource_providers.aggregating import AggregatingResourceProvider
from agentpool.resource_providers.mcp_provider import MCPResourceProvider
from agentpool.resource_providers.plan_provider import PlanProvider

__all__ = [
    "AggregatingResourceProvider",
    "FilteringResourceProvider",
    "MCPResourceProvider",
    "PlanProvider",
    "ProviderKind",
    "ResourceChangeEvent",
    "ResourceInfo",
    "ResourceProvider",
    "ResourceType",
    "StaticResourceProvider",
]
