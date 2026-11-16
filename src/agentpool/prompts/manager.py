"""Prompt Manager."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from anyenv import method_spawner
from pydantic_ai import UserPromptPart
from slashed import Command, CommandContext

from agentpool.log import get_logger
from agentpool.prompts.builtin_provider import BuiltinPromptProvider
from agentpool.utils.tasks import TaskManager


if TYPE_CHECKING:
    from agentpool.agents.staged_content import StagedContent
    from agentpool.prompts.base import BasePromptProvider
    from agentpool_config.system_prompts import PromptLibraryConfig, StaticPromptConfig

logger = get_logger(__name__)


def parse_prompt_reference(reference: str) -> tuple[str, str, str | None, dict[str, str]]:
    """Parse a prompt reference string.

    Args:
        reference: Format [provider:]name[@version][?var1=val1,var2=val2]

    Returns:
        Tuple of (provider, identifier, version, variables)
        Provider defaults to "builtin" if not specified
    """
    provider = "builtin"
    version = None
    variables: dict[str, str] = {}

    # Split provider and rest
    if ":" in reference:
        provider, identifier = reference.split(":", 1)
    else:
        identifier = reference

    # Handle version
    if "@" in identifier:
        identifier, version = identifier.split("@", 1)

    # Handle query parameters
    if "?" in identifier:
        identifier, query = identifier.split("?", 1)
        for pair in query.split(","):
            if "=" not in pair:
                continue
            key, value = pair.split("=", 1)
            variables[key.strip()] = value.strip()

    return provider, identifier.strip(), version, variables


class PromptManager:
    """Manages multiple prompt providers.

    Handles:
    - Provider initialization and cleanup
    - Prompt reference parsing and resolution
    - Access to prompts from different sources
    """

    def __init__(self, config: PromptLibraryConfig) -> None:
        """Initialize prompt manager."""
        super().__init__()
        self.config = config
        self.task_manager = TaskManager()
        self.providers: dict[str, BasePromptProvider] = {}
        # Always register builtin provider
        self.providers["builtin"] = BuiltinPromptProvider(config.system_prompts)
        # Register configured providers
        for provider_config in config.providers:
            provider = provider_config.get_provider()
            self.providers[provider.name] = provider

    async def get_from(
        self,
        identifier: str,
        *,
        provider: str | None = None,
        version: str | None = None,
        variables: dict[str, Any] | None = None,
    ) -> str:
        """Get a prompt.

        Args:
            identifier: Prompt identifier/name
            provider: Provider name (None = builtin)
            version: Optional version string
            variables: Optional template variables

        Examples:
            await prompts.get_from("code_review", variables={"language": "python"})
            await prompts.get_from(
                "expert",
                provider="langfuse",
                version="v2",
                variables={"domain": "ML"}
            )
        """
        provider_name = provider or "builtin"
        if provider_name not in self.providers:
            raise KeyError(f"Unknown prompt provider: {provider_name}")

        provider_instance = self.providers[provider_name]
        kwargs: dict[str, Any] = {}
        if provider_instance.supports_versions and version:
            kwargs["version"] = version
        if provider_instance.supports_variables and variables:
            kwargs["variables"] = variables
        try:
            return await provider_instance.get_prompt(identifier, **kwargs)
        except Exception as e:
            raise RuntimeError(f"Failed to get prompt {identifier!r} from {provider_name}") from e

    @method_spawner
    async def get(self, reference: str) -> str:
        """Get a prompt using identifier syntax.

        Args:
            reference: Prompt identifier in format:
                      [provider:]name[@version][?var1=val1,var2=val2]

        Examples:
            await prompts.get("error_handler")  # Builtin prompt
            await prompts.get("langfuse:code_review@v2?style=detailed")
        """
        provider_name, id_, version, vars_ = parse_prompt_reference(reference)
        prov = provider_name if provider_name != "builtin" else None
        return await self.get_from(id_, provider=prov, version=version, variables=vars_)

    async def list_prompts(self, provider: str | None = None) -> dict[str, list[str]]:
        """List available prompts.

        Args:
            provider: Optional provider name to filter by

        Returns:
            Dict mapping provider names to their available prompts
        """
        providers = {provider: self.providers[provider]} if provider else self.providers

        if not providers:
            return {}
        # Get prompts from providers concurrently
        result = {}
        coros = [p.list_prompts() for p in providers.values()]
        gathered_results = await asyncio.gather(*coros, return_exceptions=True)
        for (name, _), prompts in zip(providers.items(), gathered_results, strict=False):
            if isinstance(prompts, BaseException):
                logger.exception("Failed to list prompts", source=name)
                continue
            result[name] = prompts
        return result

    def cleanup(self) -> None:
        """Clean up providers."""
        self.providers.clear()

    def get_default_provider(self) -> str:
        """Get default provider name.

        Returns configured default or "builtin"
        """
        return "builtin"

    def get_builtin_prompts(self) -> dict[str, StaticPromptConfig]:
        """Get builtin prompt configurations.

        Returns:
            Dict mapping prompt names to their StaticPromptConfig objects
        """
        builtin = self.providers.get("builtin")
        if isinstance(builtin, BuiltinPromptProvider):
            return builtin.prompts
        return {}

    def create_prompt_hub_command(
        self,
        provider: str,
        name: str,
        staged_content: StagedContent,
    ) -> Command:
        """Convert prompt hub prompt to slash command.

        Args:
            provider: Provider name (e.g., 'langfuse', 'builtin')
            name: Prompt name
            staged_content: StagedContent object to add the prompt to

        Returns:
            Command that executes the prompt hub prompt
        """

        async def execute_prompt(
            ctx: CommandContext[Any],
            args: list[str],
            kwargs: dict[str, str],
        ) -> None:
            """Execute the prompt hub prompt with parsed arguments."""
            # Build reference string
            reference = f"{provider}:{name}" if provider != "builtin" else name
            # Add variables as query parameters if provided
            if kwargs:
                params = "&".join(f"{k}={v}" for k, v in kwargs.items())
                reference = f"{reference}?{params}"
            try:
                # Get the rendered prompt
                result = await self.get(reference)
                staged_content.add([UserPromptPart(content=result)])
                # Send confirmation
                staged_count = len(staged_content)
                msg = f"✅ Prompt {name!r} from {provider} staged ({staged_count} total parts)"
                await ctx.print(msg)
            except Exception as e:
                logger.exception("Prompt hub execution failed", prompt=name, provider=provider)
                await ctx.print(f"❌ Prompt error: {e}")

        return Command.from_raw(
            execute_prompt,
            name=f"{provider}_{name}" if provider != "builtin" else name,
            description=f"Prompt hub: {provider}:{name}",
            category="prompts",
            usage="[key=value ...]",  # Generic since we don't have parameter schemas
        )
