"""Config and provider routes."""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
import logging
import os
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter

from agentpool.models.manifest import AgentsManifest
from agentpool_server.opencode_server.dependencies import StateDep
from agentpool_server.opencode_server.models import (
    Config,
    Mode,
    Model,
    ModelCost,
    ModelLimit,
    Provider,
    ProviderListResponse,
    ProvidersResponse,
)
from agentpool_server.shared.constants import (
    DEFAULT_MODEL_CONTEXT_LIMIT,
    DEFAULT_MODEL_INPUT_COST,
    DEFAULT_MODEL_OUTPUT_COST,
    DEFAULT_MODEL_OUTPUT_LIMIT,
)
from agentpool_server.shared.model_utils import (
    _build_providers_from_tokonomics,
    _extract_provider,
)


logger = logging.getLogger(__name__)


if TYPE_CHECKING:
    from tokonomics.model_discovery.model_info import ModelInfo as TokoModelInfo


router = APIRouter(tags=["config"])

DEFAULT_IGNORE = ["node_modules/**", "__pycache__/**", ".venv/**", "*.pyc", ".mypy_cache/**"]
# Provider display names and environment variable mappings
PROVIDER_INFO: dict[str, tuple[str, list[str]]] = {
    "anthropic": ("Anthropic", ["ANTHROPIC_API_KEY"]),
    "openai": ("OpenAI", ["OPENAI_API_KEY"]),
    "google": ("Google", ["GOOGLE_API_KEY", "GEMINI_API_KEY"]),
    "mistral": ("Mistral", ["MISTRAL_API_KEY"]),
    "groq": ("Groq", ["GROQ_API_KEY"]),
    "deepseek": ("DeepSeek", ["DEEPSEEK_API_KEY"]),
    "xai": ("xAI", ["XAI_API_KEY"]),
    "together": ("Together AI", ["TOGETHER_API_KEY"]),
    "perplexity": ("Perplexity", ["PERPLEXITY_API_KEY"]),
    "cohere": ("Cohere", ["COHERE_API_KEY"]),
    "fireworks": ("Fireworks AI", ["FIREWORKS_API_KEY"]),
    "openrouter": ("OpenRouter", ["OPENROUTER_API_KEY"]),
    "bedrock": ("AWS Bedrock", ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"]),
    "azure": ("Azure OpenAI", ["AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT"]),
    "vertex": ("Google Vertex AI", ["GOOGLE_APPLICATION_CREDENTIALS"]),
}


def _group_models_by_provider(models: list[TokoModelInfo]) -> dict[str, list[TokoModelInfo]]:
    """Group models by their provider."""
    grouped: dict[str, list[TokoModelInfo]] = defaultdict(list)
    for model in models:
        # Skip embedding models - OpenCode is for chat/agent models
        if model.is_embedding:
            continue
        grouped[model.provider].append(model)
    return grouped


def _build_providers(models: list[TokoModelInfo]) -> list[Provider]:
    """Build Provider list from tokonomics models."""
    grouped = _group_models_by_provider(models)
    providers: list[Provider] = []
    for provider_id, provider_models in sorted(grouped.items()):
        # Get provider display info
        display_name, env_vars = PROVIDER_INFO.get(
            provider_id, (provider_id.title(), [f"{provider_id.upper()}_API_KEY"])
        )
        # Convert models to OpenCode format
        models_dict = {i.id_override or i.id: Model.from_tokonomics(i) for i in provider_models}
        provider = Provider(id=provider_id, name=display_name, env=env_vars, models=models_dict)
        providers.append(provider)

    return providers


async def _get_available_models() -> list[TokoModelInfo]:
    """Fetch available models using tokonomics."""
    from tokonomics.model_discovery import get_all_models

    max_age = timedelta(days=7)  # Cache for a week
    return await get_all_models(max_age=max_age)


async def _get_configured_variants(
    manifest: AgentsManifest | None,
) -> dict[str, dict[str, Any]]:
    """Get model variants from manifest configuration.

    Returns empty dict if manifest or model_variants is None/empty.

    Args:
        manifest: The agents manifest containing model_variants configuration.

    Returns:
        Dictionary mapping variant names to their config dicts with provider info.
    """
    variants: dict[str, dict[str, Any]] = {}

    # Check manifest model_variants
    if manifest and manifest.model_variants:
        for name, config in manifest.model_variants.items():
            variants[name] = {
                "provider": _extract_provider(config),
                # Note: variant-specific settings (temp, thinking) are not exposed to clients;
                # they are applied internally by the agent
            }

    return variants


def _build_providers_from_configured(
    configured: dict[str, dict[str, Any]],
) -> list[Provider]:
    """Build providers list from configured variants.

    Args:
        configured: Dictionary mapping variant names to their config dicts.

    Returns:
        List of Provider objects with models grouped by provider.
    """
    providers_by_name: dict[str, Provider] = {}

    for variant_name, variant_config in configured.items():
        provider_name = variant_config.get("provider", "unknown")

        if provider_name not in providers_by_name:
            providers_by_name[provider_name] = Provider(
                id=provider_name.lower(),
                name=provider_name.title(),
                models={},
            )

        providers_by_name[provider_name].models[variant_name] = Model(
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

    return list(providers_by_name.values())


def _build_providers_from_variants(
    variants: dict[str, dict[str, object]],
) -> list[Provider]:
    """Build providers list from agent variant modes.

    For agents with thought_level modes (Codex, Claude Code), creates
    a single provider with all variants as models.

    Args:
        variants: Dictionary mapping variant names to their config dicts.

    Returns:
        List of Provider objects containing the variant models.
    """
    # For agent-specific modes, create a single provider with all variants
    return [
        Provider(
            id="agent",
            name="Agent Modes",
            models={
                name: Model(
                    id=name,
                    name=name,
                    cost=ModelCost(
                        input=DEFAULT_MODEL_INPUT_COST,
                        output=DEFAULT_MODEL_OUTPUT_COST,
                    ),
                    limit=ModelLimit(
                        context=DEFAULT_MODEL_CONTEXT_LIMIT,
                        output=DEFAULT_MODEL_OUTPUT_LIMIT,
                    ),
                )
                for name in variants
            },
        )
    ]


async def _build_providers_with_fallback(
    manifest: AgentsManifest | None,
    agent: object | None = None,
) -> list[Provider]:
    """Build providers list with fallback hierarchy.

    1. Primary: Use configured variants from manifest
    2. Secondary: Dynamically discover via tokonomics
    3. Tertiary: Get agent modes (Codex/Claude thought levels)
    4. Last resort: Return empty list with warning

    Args:
        manifest: The agents manifest containing model_variants configuration.
        agent: Optional agent instance to get agent-specific modes from.

    Returns:
        List of Provider objects following the fallback hierarchy.
    """
    # Primary: Configured variants
    configured = await _get_configured_variants(manifest)
    if configured:
        logger.debug(f"Using {len(configured)} configured variants from manifest")
        return _build_providers_from_configured(configured)

    # Secondary: Tokonomics discovery
    try:
        toko_models = await _get_available_models()
        if toko_models:
            logger.debug(f"Using {len(toko_models)} models from tokonomics discovery")
            return _build_providers_from_tokonomics(toko_models)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Tokonomics discovery failed: {e}")

    # Tertiary: Agent-specific modes
    if agent:
        agent_variants = await _get_variants_from_agent(agent)
        if agent_variants:
            logger.debug(f"Using {len(agent_variants)} variants from agent modes")
            return _build_providers_from_variants(agent_variants)

    # Last resort: Empty with warning
    logger.warning("No model variants configured and no models available from discovery")
    return []


@router.get("/config")
async def get_config(state: StateDep) -> Config:
    """Get server configuration."""
    from agentpool_server.opencode_server.models.config import Keybinds, WatcherConfig

    # Initialize config if not yet set
    if state.config is None:
        state.config = Config()

    # Ensure keybinds are set with defaults
    if state.config.keybinds is None:
        state.config.keybinds = Keybinds()

    # Ensure watcher config is set with sensible defaults
    if state.config.watcher is None:
        state.config.watcher = WatcherConfig(ignore=DEFAULT_IGNORE)

    # Set a default model if not already configured
    if state.config.model is None:
        try:
            # Get available models
            toko_models = await state.agent.get_available_models()
            if toko_models:
                providers = _build_providers(toko_models)

                # Find first connected provider and use its first model
                for provider in providers:
                    if any(os.environ.get(env) for env in provider.env) and provider.models:
                        first_model = next(iter(provider.models.keys()))
                        state.config.model = f"{provider.id}/{first_model}"
                        break
        except Exception:  # noqa: BLE001
            pass  # If we can't set a default, that's okay

    return state.config


@router.patch("/config")
async def update_config(state: StateDep, config_update: Config) -> Config:
    """Update server configuration.

    Only updates fields that are provided (non-None).
    Returns the complete updated config.
    """
    # Initialize config if not yet set
    if state.config is None:
        state.config = Config()

    # Update only the fields that were provided
    update_data = config_update.model_dump(exclude_unset=True)
    for field_name, value in update_data.items():
        setattr(state.config, field_name, value)

    return state.config


async def _get_variants_from_agent(agent: object) -> dict[str, dict[str, object]]:
    """Get variants from agent's thought_level modes.

    Only supported for Codex and Claude Code agents which have static,
    known thought_level modes.

    Args:
        agent: The agent to get modes from

    Returns:
        Dict mapping variant names to empty config dicts (config is agent-internal)
    """
    from agentpool.agents.claude_code_agent import ClaudeCodeAgent
    from agentpool.agents.codex_agent import CodexAgent

    # Only Codex and Claude Code have static thought_level modes we can expose
    if not isinstance(agent, (CodexAgent, ClaudeCodeAgent)):
        return {}

    try:
        mode_categories = await agent.get_modes()
    except Exception:  # noqa: BLE001
        return {}
    for category in mode_categories:
        if category.id == "thought_level":
            # Convert modes to variants - the actual config is handled by set_mode
            return {mode.id: {} for mode in category.available_modes}
    return {}


@router.get("/config/providers")
async def get_providers(state: StateDep) -> ProvidersResponse:
    """Get available providers and models from agent."""
    # Get manifest from agent pool (may be None if not loaded)
    manifest: AgentsManifest | None = None
    try:
        manifest = state.pool.manifest
    except (AttributeError, RuntimeError):
        pass  # No manifest available

    # Build providers using fallback hierarchy
    providers = await _build_providers_with_fallback(manifest, state.agent)

    # Build default models map: use first model for each connected provider
    default_models: dict[str, str] = {}
    connected_providers = [
        provider.id for provider in providers if any(os.environ.get(env) for env in provider.env)
    ]

    for provider in providers:
        if provider.id in connected_providers and provider.models:
            # Simply use the first available model
            default_models[provider.id] = next(iter(provider.models.keys()))

    return ProvidersResponse(providers=providers, default=default_models)


@router.get("/provider")
async def list_providers(state: StateDep) -> ProviderListResponse:
    """List all providers."""
    # Get manifest from agent pool (may be None if not loaded)
    manifest: AgentsManifest | None = None
    try:
        manifest = state.pool.manifest
    except (AttributeError, RuntimeError):
        pass  # No manifest available

    # Build providers using fallback hierarchy
    providers = await _build_providers_with_fallback(manifest, state.agent)

    # Determine which providers are "connected" based on env vars
    connected = [
        provider.id for provider in providers if any(os.environ.get(env) for env in provider.env)
    ]

    # Build default models map: use first model for each connected provider
    default_models: dict[str, str] = {}
    for provider in providers:
        if provider.id in connected and provider.models:
            # Simply use the first available model
            default_models[provider.id] = next(iter(provider.models.keys()))

    return ProviderListResponse(
        all=providers,
        default=default_models,
        connected=connected,
    )


@router.get("/mode")
async def list_modes(state: StateDep) -> list[Mode]:
    """List available modes."""
    _ = state  # unused for now
    return [
        Mode(
            name="default",
            tools={},
        )
    ]
