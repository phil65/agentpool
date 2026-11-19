"""**UNSTABLE**: v2 LLM provider configuration (``providers/list``, ``/set``, ``/disable``)."""

from __future__ import annotations

from collections.abc import Sequence  # noqa: TC003
from typing import Literal

from pydantic import MISSING

from acp.schema.v2.base import AnnotatedObject
from acp.schema.v2.common import ProviderId  # noqa: TC001


LlmProtocol = Literal["anthropic", "openai", "azure", "vertex", "bedrock"] | str
"""**UNSTABLE**: API protocol of an LLM provider; unknown values MUST be handled gracefully."""


class ProviderCurrentConfig(AnnotatedObject):
    """**UNSTABLE**: Current effective non-secret routing configuration for a provider."""

    api_type: LlmProtocol
    """Protocol currently used by this provider."""

    base_url: str
    """Base URL currently used by this provider."""


class ProviderInfo(AnnotatedObject):
    """**UNSTABLE**: Information about a configurable LLM provider."""

    provider_id: ProviderId
    """Provider identifier, for example "main" or "openai"."""

    supported: Sequence[LlmProtocol]
    """Supported protocol types for this provider."""

    required: bool
    """Whether this provider is mandatory and cannot be disabled via ``providers/disable``. If
    true, clients must not call ``providers/disable`` for this provider ID."""

    current: ProviderCurrentConfig | MISSING | None = MISSING
    """Current effective non-secret routing config. Null or omitted means provider is
    disabled."""


class ListProvidersRequest(AnnotatedObject):
    """**UNSTABLE**: Request parameters for ``providers/list``."""


class ListProvidersResponse(AnnotatedObject):
    """**UNSTABLE**: Response to ``providers/list``."""

    providers: Sequence[ProviderInfo]
    """Configurable providers with current routing info suitable for UI display."""


class SetProviderRequest(AnnotatedObject):
    """**UNSTABLE**: Request parameters for ``providers/set``."""

    provider_id: ProviderId
    """Provider ID to configure."""

    api_type: LlmProtocol
    """Protocol type for this provider."""

    base_url: str
    """Base URL for requests sent through this provider."""

    headers: dict[str, str] | MISSING = MISSING
    """Full headers map for this provider. May include authorization, routing, or other
    integration-specific headers."""


class SetProviderResponse(AnnotatedObject):
    """**UNSTABLE**: Response to ``providers/set``."""


class DisableProviderRequest(AnnotatedObject):
    """**UNSTABLE**: Request parameters for ``providers/disable``."""

    provider_id: ProviderId
    """Provider ID to disable."""


class DisableProviderResponse(AnnotatedObject):
    """**UNSTABLE**: Response to ``providers/disable``."""
