"""Shared model utilities for AgentPool servers.

This module provides helper functions for extracting provider information,
building provider lists from tokonomics discovery, and merging configured
variants across ACP and OpenCode servers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from llmling_models_config import (
    AnthropicModelConfig,
    AnyModelConfig,
    FallbackModelConfig,
    GeminiModelConfig,
    OpenAIModelConfig,
    StringModelConfig,
)

from agentpool_server.shared.constants import (
    DEFAULT_MODEL_CONTEXT_LIMIT,
    DEFAULT_MODEL_INPUT_COST,
    DEFAULT_MODEL_OUTPUT_COST,
    DEFAULT_MODEL_OUTPUT_LIMIT,
)


if TYPE_CHECKING:
    from tokonomics.model_discovery.model_info import ModelInfo as TokoModelInfo

    from agentpool_server.opencode_server.models import Provider


def _extract_provider_from_identifier(identifier: str) -> str:
    """Extract provider name from a model identifier string.

    Args:
        identifier: Model identifier string (e.g., "openai:gpt-4o")

    Returns:
        Provider name extracted from identifier (e.g., "openai"), or "unknown"
        if no provider prefix found.
    """
    if ":" in identifier:
        return identifier.split(":", 1)[0]
    return "unknown"


def _extract_provider(config: AnyModelConfig) -> str:
    """Extract provider name from AnyModelConfig.

    Handles:
    - StringModelConfig: Extract provider from identifier (e.g., "openai:gpt-4o" -> "openai")
    - AnthropicModelConfig: Returns "anthropic"
    - OpenAIModelConfig: Returns "openai"
    - GeminiModelConfig: Returns "google"
    - FallbackModelConfig: Returns provider of first model in chain

    Args:
        config: Model configuration to extract provider from.

    Returns:
        Provider name as a string.
    """
    match config:
        case StringModelConfig(identifier=identifier):
            return _extract_provider_from_identifier(str(identifier))

        case AnthropicModelConfig():
            return "anthropic"

        case OpenAIModelConfig():
            return "openai"

        case GeminiModelConfig():
            return "google"

        case FallbackModelConfig(models=models) if models:
            first = models[0]
            match first:
                case StringModelConfig(identifier=identifier):
                    return _extract_provider_from_identifier(str(identifier))
                case AnthropicModelConfig():
                    return "anthropic"
                case OpenAIModelConfig():
                    return "openai"
                case GeminiModelConfig():
                    return "google"
                case FallbackModelConfig():
                    return _extract_provider(first)
                case _:
                    return "unknown"

        case _:
            return "unknown"


def _build_providers_from_tokonomics(toko_models: list[TokoModelInfo]) -> list[Provider]:
    """Build providers list from tokonomics discovery results.

    Groups models by (provider, provider_display_name) and creates Provider
    objects with their associated models.

    Args:
        toko_models: List of tokonomics ModelInfo objects from discovery.

    Returns:
        List of Provider objects with models converted using Model.from_tokonomics().
    """
    from agentpool_server.opencode_server.models import Model, Provider

    providers_by_name: dict[str, Provider] = {}

    for info in toko_models:
        # Skip embedding models
        if info.is_embedding:
            continue

        provider_id = info.provider

        if provider_id not in providers_by_name:
            providers_by_name[provider_id] = Provider(
                id=provider_id,
                name=provider_id.title(),
                models={},
            )

        model_id = info.id_override or info.id
        providers_by_name[provider_id].models[model_id] = Model.from_tokonomics(info)

    return list(providers_by_name.values())


def _apply_configured_variants(
    providers: list[Provider],
    configured_variants: dict[str, dict[str, Any]],
) -> None:
    """Merge configured variants into providers list.

    Configured variants with matching IDs override discovered models.
    New configured variants are added to their respective providers.

    Args:
        providers: List of Provider objects to modify in place.
        configured_variants: Dictionary mapping variant names to their
            configuration dictionaries. Each config dict should have a
            "provider" key indicating which provider the variant belongs to.

    Note:
        This function modifies the providers list in place. New providers
        are created if a configured variant references a non-existent provider.
    """
    from agentpool_server.opencode_server.models import Model, ModelCost, ModelLimit, Provider

    # Build lookup for provider name -> Provider object
    provider_lookup: dict[str, Provider] = {}
    for provider in providers:
        provider_lookup[provider.id.lower()] = provider

    for variant_name, variant_config in configured_variants.items():
        provider_name = variant_config.get("provider", "unknown").lower()

        if provider_name not in provider_lookup:
            # Create new provider entry for this variant
            provider_lookup[provider_name] = Provider(
                id=provider_name,
                name=provider_name.title(),
                models={},
            )
            providers.append(provider_lookup[provider_name])

        provider = provider_lookup[provider_name]

        # Check if model with this ID already exists
        if variant_name in provider.models:
            # Override existing (configured takes precedence)
            existing = provider.models[variant_name]
            existing.name = variant_name
            # Note: variant-specific settings (temp, thinking) not exposed to client
        else:
            # Add new model - use a minimal Model creation
            provider.models[variant_name] = Model(
                id=variant_name,
                name=variant_name,
                cost=ModelCost(
                    input=DEFAULT_MODEL_INPUT_COST,
                    output=DEFAULT_MODEL_OUTPUT_COST,
                ),
                limit=ModelLimit(
                    context=DEFAULT_MODEL_CONTEXT_LIMIT,
                    output=DEFAULT_MODEL_OUTPUT_LIMIT,
                ),
            )
