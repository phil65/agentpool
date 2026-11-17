"""**UNSTABLE**: LLM provider configuration (``providers/list``, ``providers/set``, ...).

Lets a client route an agent's model traffic through its own endpoints.
Only available if the Agent advertises the ``providers`` capability.
"""

from __future__ import annotations

from collections.abc import Sequence  # noqa: TC003
from typing import Literal

from pydantic import Field

from acp.schema.base import AnnotatedObject, Request, Response


ProviderId = str
"""Provider identifier, for example ``main`` or ``openai``."""

LlmProtocol = Literal["anthropic", "openai", "azure", "vertex", "bedrock"] | str
"""API protocol spoken by a provider endpoint."""


class ProviderCurrentConfig(AnnotatedObject):
    """Current effective non-secret routing config of a provider."""

    api_type: LlmProtocol
    """Protocol currently used by this provider."""

    base_url: str
    """Base URL currently used by this provider."""


class ProviderInfo(AnnotatedObject):
    """A configurable provider with its current routing info, suitable for UI display."""

    provider_id: ProviderId
    """Provider identifier."""

    supported: Sequence[LlmProtocol]
    """Supported protocol types for this provider."""

    required: bool
    """Whether this provider is mandatory. Clients must not call ``providers/disable`` for it."""

    current: ProviderCurrentConfig | None = None
    """Current effective config. ``None`` means the provider is disabled."""


class ListProvidersRequest(Request):
    """Request the configurable providers."""


class ListProvidersResponse(Response):
    """Response to ``providers/list``."""

    providers: Sequence[ProviderInfo]
    """Configurable providers with current routing info."""


class SetProviderRequest(Request):
    """Configure a provider's routing."""

    provider_id: ProviderId
    """Provider ID to configure."""

    api_type: LlmProtocol
    """Protocol type for this provider."""

    base_url: str
    """Base URL for requests sent through this provider."""

    headers: dict[str, str] = Field(default_factory=dict)
    """Full headers map for this provider (may include authorization headers)."""


class SetProviderResponse(Response):
    """Response to ``providers/set``."""


class DisableProviderRequest(Request):
    """Disable a non-required provider."""

    provider_id: ProviderId
    """Provider ID to disable."""


class DisableProviderResponse(Response):
    """Response to ``providers/disable``."""
