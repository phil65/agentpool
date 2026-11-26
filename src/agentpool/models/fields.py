"""Predefined Configuration fields."""

from collections.abc import Sequence
from typing import Annotated

from exxec_config import E2bExecutionEnvironmentConfig, ExecutionEnvironmentConfig
from pydantic import Field

from agentpool_config.output_types import StructuredResponseConfig
from agentpool_config.system_prompts import PromptConfig


OutputTypeField = Annotated[
    str | StructuredResponseConfig | None,
    Field(
        default=None,
        examples=["json_response", "code_output"],
        title="Response type",
        description="Optional structured output type for responses. "
        "Can be either a reference to a response defined in manifest.responses, "
        "or an inline StructuredResponseConfig.",
    ),
]


EnvVarsField = Annotated[
    dict[str, str],
    Field(
        default_factory=dict,
        title="Environment Variables",
        examples=[{"PATH": "/usr/local/bin:/usr/bin", "DEBUG": "1"}],
        description="Environment variables to set.",
    ),
]


ExecutionEnvironmentField = Annotated[
    ExecutionEnvironmentConfig | str | None,
    Field(
        default=None,
        title="Execution Environment",
        examples=["docker", E2bExecutionEnvironmentConfig(template="python-sandbox")],
    ),
]


SystemPromptField = Annotated[
    str | Sequence[str | PromptConfig] | None,
    Field(
        default=None,
        title="System Prompt",
        examples=[
            "You are a helpful assistant.",
            ["You are an AI assistant.", "Always be concise."],
        ],
        json_schema_extra={
            "documentation_url": "https://phil65.github.io/agentpool/configuration/prompts/"
        },
        description="""System prompt for the agent.
        Can be a string or list of strings/prompt configs""",
    ),
]
