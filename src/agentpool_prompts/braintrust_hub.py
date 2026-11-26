"""Braintrust prompt provider implementation."""

import contextlib
from dataclasses import dataclass
from datetime import datetime
import os
from typing import Any

from braintrust import init_logger, load_prompt
import httpx2

from agentpool.prompts.base import BasePromptProvider
from agentpool_config.prompt_hubs import BraintrustConfig


@dataclass
class BraintrustPromptMetadata:
    """Metadata for a Braintrust prompt."""

    id: str
    """Unique identifier for the prompt."""

    name: str
    """Name of the prompt."""

    slug: str
    """Unique slug identifier for the prompt."""

    description: str | None
    """Textual description of the prompt."""

    project_id: str
    """Project ID the prompt belongs to."""

    org_id: str
    """Organization ID."""

    created: datetime | None
    """Creation timestamp."""

    tags: list[str]
    """List of tags associated with the prompt."""

    function_type: str | None
    """Type of function (llm, scorer, task, tool)."""

    has_variables: bool
    """Whether the prompt contains template variables."""

    model: str | None
    """Default model specified in prompt options."""

    @property
    def web_url(self) -> str:
        """URL to view this prompt in Braintrust web interface."""
        return f"https://www.braintrustdata.com/app/prompt/{self.slug}"

    @classmethod
    def from_api_object(cls, obj: dict[str, Any]) -> BraintrustPromptMetadata:
        """Create metadata from Braintrust API response object."""
        created: datetime | None = None
        if obj.get("created"):
            with contextlib.suppress(ValueError, AttributeError):
                created = datetime.fromisoformat(obj["created"].replace("Z", "+00:00"))

        # Extract model from prompt_data.options.model if available
        model = None
        if prompt_data := obj.get("prompt_data"):  # noqa: SIM102
            if options := prompt_data.get("options"):
                model = options.get("model")

        # Check for template variables in prompt content
        has_variables = False
        if prompt_data := obj.get("prompt_data"):  # noqa: SIM102
            if prompt := prompt_data.get("prompt"):
                if prompt.get("type") == "chat" and prompt.get("messages"):
                    # Check for Jinja2 template variables in message content
                    for msg in prompt["messages"]:
                        content = msg.get("content", "")
                        if isinstance(content, str) and ("{{" in content or "{%" in content):
                            has_variables = True
                            break
                elif prompt.get("type") == "completion":
                    content = prompt.get("content", "")
                    if isinstance(content, str) and ("{{" in content or "{%" in content):
                        has_variables = True

        return cls(
            id=obj["id"],
            name=obj["name"],
            slug=obj["slug"],
            description=obj.get("description"),
            project_id=obj["project_id"],
            org_id=obj["org_id"],
            created=created,
            tags=obj.get("tags") or [],
            function_type=obj.get("function_type"),
            has_variables=has_variables,
            model=model,
        )


class BraintrustPromptHub(BasePromptProvider):
    """Braintrust prompt provider implementation."""

    def __init__(self, config: BraintrustConfig) -> None:
        self.config = config or BraintrustConfig()
        api_key = self.config.api_key.get_secret_value() if self.config.api_key else None
        key = api_key or os.getenv("BRAINTRUST_API_KEY")
        init_logger(api_key=key, project=self.config.project)
        self.api_key = key
        self._prompt_metadata: dict[str, BraintrustPromptMetadata] = {}

    async def get_prompt(
        self,
        name: str,
        version: str | None = None,
        variables: dict[str, Any] | None = None,
    ) -> str:
        """Get and optionally compile a prompt from Braintrust."""
        import jinjarope

        env = jinjarope.Environment(enable_async=True)
        prompt = load_prompt(slug=name, version=version, project=self.config.project)
        assert prompt.prompt
        string = prompt.prompt.messages[0].content  # type: ignore
        assert isinstance(string, str)
        return await env.render_string_async(string, **(variables or {}))

    async def list_prompts(self) -> list[str]:
        """List available prompts from Braintrust with pagination support."""
        if not self.api_key:
            raise RuntimeError("API key is required to list prompts")

        params: dict[str, Any] = {"limit": 100}  # Get up to 100 per page
        if self.config.project:  # Add project filter if specified
            params["project_name"] = self.config.project

        all_prompts = []
        async with httpx2.AsyncClient() as client:
            while True:
                url = "https://api.braintrust.dev/v1/prompt"
                headers = {"Authorization": f"Bearer {self.api_key}"}
                response = await client.get(url, headers=headers, params=params)
                match response.status_code:
                    case 401:
                        raise RuntimeError("Unauthorized: Check your Braintrust API key")
                    case 403:
                        raise RuntimeError("Forbidden: no permission to list prompts")
                    case 200:
                        msg = f"Failed to list prompts: {response.status_code} - {response.text}"
                        raise RuntimeError(msg)
                try:
                    data = response.json()
                except Exception as e:
                    raise RuntimeError(f"Failed to parse response JSON: {e}") from e

                objects = data.get("objects", [])
                if not objects:
                    break

                # Parse metadata and collect prompt names
                for obj in objects:
                    if obj.get("name") or obj.get("slug"):
                        metadata = BraintrustPromptMetadata.from_api_object(obj)
                        prompt_name = metadata.name or metadata.slug
                        self._prompt_metadata[prompt_name] = metadata
                        all_prompts.append(prompt_name)

                # Check if there are more pages
                if len(objects) < params["limit"]:
                    break

                # Set up pagination for next request
                last_id = objects[-1].get("id")
                if last_id:
                    params["starting_after"] = last_id
                else:
                    break

        return all_prompts

    def get_prompt_metadata(self, name: str) -> BraintrustPromptMetadata | None:
        """Get metadata for a specific prompt."""
        return self._prompt_metadata.get(name)

    def get_all_metadata(self) -> dict[str, BraintrustPromptMetadata]:
        """Get all parsed prompt metadata."""
        return self._prompt_metadata.copy()

    def filter_prompts_by_tag(self, tag: str) -> list[str]:
        """Filter prompts by tag."""
        return [name for name, metadata in self._prompt_metadata.items() if tag in metadata.tags]

    def filter_prompts_by_function_type(self, function_type: str) -> list[str]:
        """Filter prompts by function type (llm, scorer, task, tool)."""
        return [
            name
            for name, metadata in self._prompt_metadata.items()
            if metadata.function_type == function_type
        ]

    def filter_prompts_with_variables(self) -> list[str]:
        """Get prompts that contain template variables."""
        return [name for name, metadata in self._prompt_metadata.items() if metadata.has_variables]

    def get_prompts_by_model(self, model: str) -> list[str]:
        """Get prompts configured for a specific model."""
        return [name for name, metadata in self._prompt_metadata.items() if metadata.model == model]


if __name__ == "__main__":
    import anyio

    from agentpool_config.prompt_hubs import BraintrustConfig

    config = BraintrustConfig(project="test")
    prompt_hub = BraintrustPromptHub(config)

    async def main() -> None:
        # List available prompts
        print("Available prompts:")
        try:
            prompts = await prompt_hub.list_prompts()
            print(f"Found {len(prompts)} prompts:")
            for prompt in prompts[:10]:  # Show first 10
                print(f"- {prompt}")
        except Exception as e:  # noqa: BLE001
            print(f"Error listing prompts: {e}")

    anyio.run(main)
