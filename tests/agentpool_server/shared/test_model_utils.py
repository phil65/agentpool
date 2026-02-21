"""Tests for model_utils module."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from tokonomics.model_discovery.model_info import ModelInfo, ModelPricing

from agentpool_server.opencode_server.models import ModelCost, ModelLimit
from agentpool_server.shared.model_utils import (
    _apply_configured_variants,
    _build_providers_from_tokonomics,
    _extract_provider,
    _extract_provider_from_identifier,
)


def create_toko_model_info(
    model_id: str,
    name: str,
    provider: str,
    is_embedding: bool = False,
    **kwargs: Any,
) -> ModelInfo:
    """Create a ModelInfo instance for testing."""
    return ModelInfo(
        id=model_id,
        name=name,
        provider=provider,
        is_embedding=is_embedding,
        **kwargs,
    )


class TestExtractProviderFromIdentifier:
    """Tests for _extract_provider_from_identifier function."""

    def test_extract_provider_with_colon(self) -> None:
        """Extract provider from identifier with colon separator."""
        result = _extract_provider_from_identifier("openai:gpt-4o")
        assert result == "openai"

    def test_extract_provider_anthropic(self) -> None:
        """Extract provider from anthropic identifier."""
        result = _extract_provider_from_identifier("anthropic:claude-3-opus")
        assert result == "anthropic"

    def test_extract_provider_without_colon(self) -> None:
        """Return unknown for identifier without colon."""
        result = _extract_provider_from_identifier("gpt-4o")
        assert result == "unknown"

    def test_extract_provider_empty_string(self) -> None:
        """Return unknown for empty string."""
        result = _extract_provider_from_identifier("")
        assert result == "unknown"

    def test_extract_provider_multiple_colons(self) -> None:
        """Extract only first part when multiple colons present."""
        result = _extract_provider_from_identifier("provider:ns:model")
        assert result == "provider"


class TestExtractProvider:
    """Tests for _extract_provider function with AnyModelConfig."""

    def test_string_config_openai(self) -> None:
        """Extract provider from StringModelConfig with openai identifier."""
        from llmling_models_config import StringModelConfig

        config = StringModelConfig(identifier="openai:gpt-4o")
        result = _extract_provider(config)
        assert result == "openai"

    def test_string_config_anthropic(self) -> None:
        """Extract provider from StringModelConfig with anthropic identifier."""
        from llmling_models_config import StringModelConfig

        config = StringModelConfig(identifier="anthropic:claude-sonnet-4")
        result = _extract_provider(config)
        assert result == "anthropic"

    def test_string_config_no_provider(self) -> None:
        """Return unknown for StringModelConfig without provider prefix."""
        from llmling_models_config import StringModelConfig

        config = StringModelConfig(identifier="gpt-4o")
        result = _extract_provider(config)
        assert result == "unknown"

    def test_anthropic_config(self) -> None:
        """Return anthropic for AnthropicModelConfig."""
        from llmling_models_config import AnthropicModelConfig

        config = AnthropicModelConfig(identifier="claude-opus-4-5")
        result = _extract_provider(config)
        assert result == "anthropic"

    def test_openai_config(self) -> None:
        """Return openai for OpenAIModelConfig."""
        from llmling_models_config import OpenAIModelConfig

        config = OpenAIModelConfig(identifier="gpt-5")
        result = _extract_provider(config)
        assert result == "openai"

    def test_gemini_config(self) -> None:
        """Return google for GeminiModelConfig."""
        from llmling_models_config import GeminiModelConfig

        config = GeminiModelConfig(identifier="gemini-2.0-flash")
        result = _extract_provider(config)
        assert result == "google"

    def test_fallback_config_first_string(self) -> None:
        """Extract provider from first model in FallbackModelConfig."""
        from llmling_models_config import FallbackModelConfig, StringModelConfig

        config = FallbackModelConfig(models=[StringModelConfig(identifier="openai:gpt-4o")])
        result = _extract_provider(config)
        assert result == "openai"

    def test_fallback_config_first_anthropic(self) -> None:
        """Extract anthropic when first model is AnthropicModelConfig."""
        from llmling_models_config import AnthropicModelConfig, FallbackModelConfig

        config = FallbackModelConfig(models=[AnthropicModelConfig(identifier="claude-opus-4-5")])
        result = _extract_provider(config)
        assert result == "anthropic"

    def test_fallback_config_empty_models(self) -> None:
        """Return unknown for FallbackModelConfig with empty models list - minimum 1 required."""
        # Note: FallbackModelConfig requires at least 1 model, so we test with String instead
        from llmling_models_config import FallbackModelConfig

        # Single model fallback with unknown provider string
        config = FallbackModelConfig(models=["unknown-model-name"])
        result = _extract_provider(config)
        assert result == "unknown"

    def test_fallback_config_nested_fallback(self) -> None:
        """Handle nested FallbackModelConfig."""
        from llmling_models_config import (
            AnthropicModelConfig,
            FallbackModelConfig,
        )

        inner = FallbackModelConfig(models=[AnthropicModelConfig(identifier="claude-opus-4-5")])
        outer = FallbackModelConfig(models=[inner])
        result = _extract_provider(outer)
        assert result == "anthropic"


class TestBuildProvidersFromTokonomics:
    """Tests for _build_providers_from_tokonomics function."""

    def test_empty_list(self) -> None:
        """Return empty list for empty input."""
        result = _build_providers_from_tokonomics([])
        assert result == []

    def test_single_model(self) -> None:
        """Build provider with single model."""
        model = create_toko_model_info(
            model_id="gpt-4o",
            name="GPT-4o",
            provider="openai",
        )
        result = _build_providers_from_tokonomics([model])

        assert len(result) == 1
        assert result[0].id == "openai"
        assert result[0].name == "Openai"
        assert "gpt-4o" in result[0].models

    def test_multiple_models_same_provider(self) -> None:
        """Group multiple models from same provider."""
        models = [
            create_toko_model_info(model_id="gpt-4o", name="GPT-4o", provider="openai"),
            create_toko_model_info(model_id="gpt-4o-mini", name="GPT-4o Mini", provider="openai"),
        ]
        result = _build_providers_from_tokonomics(models)

        assert len(result) == 1
        assert result[0].id == "openai"
        assert len(result[0].models) == 2
        assert "gpt-4o" in result[0].models
        assert "gpt-4o-mini" in result[0].models

    def test_multiple_providers(self) -> None:
        """Create separate providers for different providers."""
        models = [
            create_toko_model_info(model_id="gpt-4o", name="GPT-4o", provider="openai"),
            create_toko_model_info(
                model_id="claude-3-opus", name="Claude 3 Opus", provider="anthropic"
            ),
        ]
        result = _build_providers_from_tokonomics(models)

        assert len(result) == 2
        provider_ids = {p.id for p in result}
        assert provider_ids == {"openai", "anthropic"}

    def test_skip_embedding_models(self) -> None:
        """Skip models marked as embeddings."""
        models = [
            create_toko_model_info(
                model_id="text-embedding-3-small",
                name="Embedding Small",
                provider="openai",
                is_embedding=True,
            ),
            create_toko_model_info(
                model_id="gpt-4o", name="GPT-4o", provider="openai", is_embedding=False
            ),
        ]
        result = _build_providers_from_tokonomics(models)

        assert len(result) == 1
        assert len(result[0].models) == 1
        assert "gpt-4o" in result[0].models
        assert "text-embedding-3-small" not in result[0].models

    def test_id_override(self) -> None:
        """Use id_override when available."""
        model = ModelInfo(
            id="claude-opus-4-20250514",
            name="Claude Opus 4",
            provider="anthropic",
            id_override="opus",
        )
        result = _build_providers_from_tokonomics([model])

        assert "opus" in result[0].models
        assert "claude-opus-4-20250514" not in result[0].models


class TestApplyConfiguredVariants:
    """Tests for _apply_configured_variants function."""

    @pytest.fixture
    def sample_provider(self) -> Any:
        """Create a sample Provider for testing."""
        from agentpool_server.opencode_server.models import Model, Provider

        model = Model(
            id="gpt-4o",
            name="GPT-4o",
            cost=ModelCost(input=5.0, output=15.0),
            limit=ModelLimit(context=128000.0, output=4096.0),
        )
        return Provider(
            id="openai",
            name="OpenAI",
            models={"gpt-4o": model},
        )

    def test_empty_variants(self, sample_provider: Any) -> None:
        """Handle empty configured variants dict."""
        providers = [sample_provider]
        _apply_configured_variants(providers, {})

        assert len(providers) == 1
        assert len(providers[0].models) == 1

    def test_new_provider_creation(self, sample_provider: Any) -> None:
        """Create new provider when variant references unknown provider."""
        providers = [sample_provider]
        variants = {"custom-model": {"provider": "customai"}}

        _apply_configured_variants(providers, variants)

        assert len(providers) == 2
        custom_provider = next(p for p in providers if p.id == "customai")
        assert "custom-model" in custom_provider.models

    def test_model_override(self, sample_provider: Any) -> None:
        """Override existing model when variant ID matches."""
        providers = [sample_provider]
        variants = {"gpt-4o": {"provider": "openai"}}

        _apply_configured_variants(providers, variants)

        assert len(providers) == 1
        assert len(providers[0].models) == 1
        assert providers[0].models["gpt-4o"].name == "gpt-4o"

    def test_add_model_to_existing_provider(self, sample_provider: Any) -> None:
        """Add new model to existing provider."""
        providers = [sample_provider]
        variants = {"gpt-5": {"provider": "openai"}}

        _apply_configured_variants(providers, variants)

        assert len(providers[0].models) == 2
        assert "gpt-4o" in providers[0].models
        assert "gpt-5" in providers[0].models

    def test_provider_name_case_insensitive(self, sample_provider: Any) -> None:
        """Treat provider names case-insensitively."""
        providers = [sample_provider]
        variants = {"new-model": {"provider": "OPENAI"}}

        _apply_configured_variants(providers, variants)

        assert len(providers) == 1
        assert "new-model" in providers[0].models

    def test_multiple_variants_same_provider(self, sample_provider: Any) -> None:
        """Handle multiple variants for the same provider."""
        providers = [sample_provider]
        variants = {
            "fast": {"provider": "openai"},
            "smart": {"provider": "openai"},
        }

        _apply_configured_variants(providers, variants)

        assert len(providers[0].models) == 3
        assert "fast" in providers[0].models
        assert "smart" in providers[0].models
        assert "gpt-4o" in providers[0].models

    def test_default_provider_unknown(self, sample_provider: Any) -> None:
        """Use unknown provider when not specified."""
        providers = [sample_provider]
        variants: dict[str, dict[str, Any]] = {"orphan-model": {}}

        _apply_configured_variants(providers, variants)

        unknown_provider = next(p for p in providers if p.id == "unknown")
        assert "orphan-model" in unknown_provider.models


class TestIntegration:
    """Integration tests combining multiple functions."""

    def test_end_to_end_workflow(self) -> None:
        """Test complete workflow from tokonomics to merged providers."""
        # Create tokonomics models
        toko_models = [
            create_toko_model_info(
                model_id="gpt-4o",
                name="GPT-4o",
                provider="openai",
                pricing=ModelPricing(prompt=0.00001, completion=0.00003),
                context_window=128000,
                max_output_tokens=4096,
                created_at=datetime(2024, 5, 13),
            ),
            create_toko_model_info(
                model_id="claude-3-opus",
                name="Claude 3 Opus",
                provider="anthropic",
            ),
        ]

        # Build providers from tokonomics
        providers = _build_providers_from_tokonomics(toko_models)
        assert len(providers) == 2

        # Apply configured variants
        configured_variants = {
            "fast": {"provider": "openai"},
            "smart": {"provider": "anthropic"},
        }
        _apply_configured_variants(providers, configured_variants)

        # Verify merged results
        openai_provider = next(p for p in providers if p.id == "openai")
        anthropic_provider = next(p for p in providers if p.id == "anthropic")

        assert "gpt-4o" in openai_provider.models
        assert "fast" in openai_provider.models
        assert "claude-3-opus" in anthropic_provider.models
        assert "smart" in anthropic_provider.models
