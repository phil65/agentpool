"""System prompts configuration for agents."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any, Literal

from pydantic import ConfigDict, Field, ImportString
from schemez import Schema

from agentpool_config.prompt_hubs import PromptHubConfig


SystemPromptCategory = Literal["role", "methodology", "quality", "task"]

DEFAULT_TEMPLATE = """
{%- for prompt in role_prompts %}
Role: {{ prompt.content }}
{%- endfor %}

{%- for prompt in methodology_prompts %}
Method: {{ prompt.content }}
{%- endfor %}

{%- for prompt in quality_prompts %}
Quality Check: {{ prompt.content }}
{%- endfor %}

{%- for prompt in task_prompts %}
Task: {{ prompt.content }}
{%- endfor %}
"""


class BaseSystemPrompt(Schema):
    """Individual system prompt definition."""

    content: str = Field(
        examples=["You are a helpful assistant", "Always be concise"],
        title="Prompt content",
    )
    """The actual prompt text."""

    category: SystemPromptCategory = Field(
        default="role",
        examples=["role", "methodology", "quality", "task"],
        title="Prompt category",
    )
    """Categorization for template organization."""


class StaticPromptConfig(BaseSystemPrompt):
    """Configuration for a static text prompt."""

    model_config = ConfigDict(json_schema_extra={"x-doc-title": "Static Prompt"})

    type: Literal["static"] = Field("static", init=False)
    """Static prompt reference."""

    content: str = Field(
        examples=["You are a helpful assistant", "Always be concise"],
        title="Prompt content",
    )
    """The prompt text content."""


class FilePromptConfig(BaseSystemPrompt):
    """Configuration for a file-based Jinja template prompt."""

    model_config = ConfigDict(json_schema_extra={"x-doc-title": "File Prompt"})

    type: Literal["file"] = Field("file", init=False)
    """File prompt reference."""

    path: str = Field(
        examples=["prompts/system.j2", "/templates/agent_prompt.jinja"],
        title="Template file path",
    )
    """Path to the Jinja template file."""

    variables: dict[str, Any] = Field(default_factory=dict, title="Template variables")
    """Variables to pass to the template."""


class LibraryPromptConfig(Schema):
    """Configuration for a library reference prompt."""

    model_config = ConfigDict(json_schema_extra={"x-doc-title": "Library Prompt"})

    type: Literal["library"] = Field("library", init=False)
    """Library prompt reference."""

    reference: str = Field(
        examples=["coding_assistant", "helpful_agent", "creative_writer"],
        title="Library reference",
    )
    """Library prompt reference identifier."""


class FunctionPromptConfig(BaseSystemPrompt):
    """Configuration for a function-generated prompt."""

    model_config = ConfigDict(json_schema_extra={"x-doc-title": "Function Prompt"})

    type: Literal["function"] = Field("function", init=False)
    """Function prompt reference."""

    function: ImportString[Callable[..., str]] = Field(
        examples=["mymodule.generate_prompt", "prompts.dynamic:create_system_prompt"],
        title="Function import path",
    )
    """Import path to the function that generates the prompt."""

    arguments: dict[str, Any] = Field(default_factory=dict, title="Function arguments")
    """Arguments to pass to the function."""


PromptConfig = Annotated[
    StaticPromptConfig | FilePromptConfig | LibraryPromptConfig | FunctionPromptConfig,
    Field(discriminator="type"),
]
"""Union type for different prompt configuration types."""


class PromptLibraryConfig(Schema):
    """Complete prompt configuration.

    Docs: https://phil65.github.io/agentpool/YAML%20Configuration/prompt_configuration/
    """

    system_prompts: dict[str, StaticPromptConfig] = Field(
        default_factory=dict,
        title="System prompt definitions",
    )
    """Mapping of system prompt identifiers to their definitions."""

    template: str | None = Field(
        default=None,
        examples=["Role: {{role}}\nMethod: {{method}}", "{{content}}"],
        title="Combination template",
    )
    """Optional template for combining prompts.
    Has access to prompts grouped by type."""

    providers: list[PromptHubConfig] = Field(
        default_factory=list,
        examples=[
            [
                {
                    "type": "promptlayer",
                    "api_key": "pl_abc123",
                }
            ],
            [
                {
                    "type": "fabric",
                }
            ],
        ],
        title="Prompt hub providers",
    )
    """List of external prompt providers to use."""

    def format_prompts(self, identifiers: list[str] | None = None) -> str:
        """Format selected prompts using template.

        Args:
            identifiers: Optional list of prompt IDs to include.
                       If None, includes all prompts.
        """
        # Filter prompts if identifiers provided
        prompts = (
            {k: v for k, v in self.system_prompts.items() if k in identifiers}
            if identifiers
            else self.system_prompts
        )

        # Group prompts by type for template
        by_type = {
            "role_prompts": [p for p in prompts.values() if p.category == "role"],
            "methodology_prompts": [p for p in prompts.values() if p.category == "methodology"],
            "quality_prompts": [p for p in prompts.values() if p.category == "quality"],
            "task_prompts": [p for p in prompts.values() if p.category == "task"],
        }

        # Render template
        from jinja2 import Template

        template: Template = Template(self.template or DEFAULT_TEMPLATE)
        return template.render(**by_type)
