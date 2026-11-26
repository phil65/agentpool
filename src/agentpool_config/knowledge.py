"""Knowledge configuration."""

from pydantic import ConfigDict, Field
from schemez import Schema

from agentpool.prompts.prompts import PromptType


class Knowledge(Schema):
    """Collection of context sources for an agent.

    Supports both simple paths and rich resource types for content loading,
    plus AgentPool's prompt system for dynamic content generation.

    Docs: https://phil65.github.io/agentpool/configuration/knowledge/
    """

    model_config = ConfigDict(json_schema_extra={"title": "Knowledge Configuration"})

    paths: list[str] = Field(
        default_factory=list,
        examples=[["docs/readme.md", "https://api.example.com/docs"], ["/data/context.txt"]],
        title="Resource paths",
    )
    """Quick access to files and URLs."""

    prompts: list[PromptType] = Field(default_factory=list, title="Dynamic prompts")
    """Prompts for dynamic content generation:
    - StaticPrompt: Fixed message templates
    - DynamicPrompt: Python function-based
    - FilePrompt: File-based with template support
    """

    convert_to_markdown: bool = Field(default=False, title="Convert to markdown")
    """Whether to convert content to markdown when possible."""

    def get_resources(self) -> list[PromptType | str]:
        """Get all resources."""
        return self.prompts + self.paths
