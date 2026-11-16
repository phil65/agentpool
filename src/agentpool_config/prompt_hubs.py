"""Prompt models for agent configuration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import ConfigDict, Field, SecretStr
from pydantic.networks import HttpUrl
from schemez.schema import Schema


if TYPE_CHECKING:
    from agentpool_prompts.braintrust_hub import BraintrustPromptHub
    from agentpool_prompts.fabric import FabricPromptHub
    from agentpool_prompts.langfuse_hub import LangfusePromptHub
    from agentpool_prompts.promptlayer_provider import PromptLayerProvider


class BasePromptHubConfig(Schema):
    """Configuration for prompt providers."""

    type: str = Field(init=False, title="Prompt hub type")
    """Base prompt configuration."""

    model_config = ConfigDict(frozen=True, use_attribute_docstrings=True, extra="forbid")


class PromptLayerConfig(BasePromptHubConfig):
    """Configuration for PromptLayer prompt provider."""

    model_config = ConfigDict(json_schema_extra={"x-doc-title": "PromptLayer"})

    type: Literal["promptlayer"] = Field("promptlayer", init=False)
    """Configuration for PromptLayer prompt provider."""

    api_key: SecretStr = Field(title="PromptLayer API key")
    """API key for the PromptLayer API."""

    def get_provider(self) -> PromptLayerProvider:
        from agentpool_prompts.promptlayer_provider import PromptLayerProvider

        return PromptLayerProvider(self)


class LangfuseConfig(BasePromptHubConfig):
    """Configuration for Langfuse prompt provider."""

    model_config = ConfigDict(json_schema_extra={"x-doc-title": "Langfuse"})

    type: Literal["langfuse"] = Field("langfuse", init=False)
    """Configuration for Langfuse prompt provider."""

    secret_key: SecretStr = Field(title="Langfuse secret key")
    """Secret key for the Langfuse API."""

    public_key: SecretStr = Field(title="Langfuse public key")
    """Public key for the Langfuse API."""

    host: HttpUrl = Field(
        default=HttpUrl("https://cloud.langfuse.com"),
        examples=["https://cloud.langfuse.com", "https://langfuse.example.com"],
        title="Langfuse host",
    )
    """Langfuse host address."""

    cache_ttl_seconds: int = Field(
        default=60,
        ge=0,
        examples=[60, 300, 3600],
        title="Cache TTL seconds",
    )
    """Cache TTL for responses in seconds."""

    max_retries: int = Field(default=2, ge=0, examples=[1, 2, 5], title="Maximum retries")
    """Maximum number of retries for failed requests."""

    fetch_timeout_seconds: int = Field(
        default=20,
        ge=0,
        examples=[10, 20, 60],
        title="Fetch timeout seconds",
    )
    """Timeout for fetching responses in seconds."""

    def get_provider(self) -> LangfusePromptHub:
        from agentpool_prompts.langfuse_hub import LangfusePromptHub

        return LangfusePromptHub(self)


class BraintrustConfig(BasePromptHubConfig):
    """Configuration for Braintrust prompt provider."""

    model_config = ConfigDict(json_schema_extra={"x-doc-title": "Braintrust"})

    type: Literal["braintrust"] = Field("braintrust", init=False)
    """Configuration for Braintrust prompt provider."""

    api_key: SecretStr | None = Field(default=None, title="Braintrust API key")
    """API key for the Braintrust API. Defaults to BRAINTRUST_API_KEY env var"""

    project: str | None = Field(
        default=None,
        examples=["my_project", "ai_agents", "production"],
        title="Braintrust project",
    )
    """Braintrust Project name."""

    def get_provider(self) -> BraintrustPromptHub:
        from agentpool_prompts.braintrust_hub import BraintrustPromptHub

        return BraintrustPromptHub(self)


class FabricConfig(BasePromptHubConfig):
    """Configuration for Fabric GitHub prompt provider."""

    model_config = ConfigDict(json_schema_extra={"x-doc-title": "Fabric"})

    type: Literal["fabric"] = Field("fabric", init=False)
    """Configuration for Fabric GitHub prompt provider."""

    def get_provider(self) -> FabricPromptHub:
        from agentpool_prompts.fabric import FabricPromptHub

        return FabricPromptHub(self)


PromptHubConfig = Annotated[
    PromptLayerConfig | LangfuseConfig | FabricConfig | BraintrustConfig,
    Field(discriminator="type"),
]
