"""Models for response fields and definitions."""

from __future__ import annotations

from typing import Any

from pydantic import Field, model_validator
from schemez import Schema, SchemaDef


class StructuredResponseConfig(Schema):
    """Base class for response definitions.

    Docs: https://phil65.github.io/agentpool/configuration/responses/
    """

    response_schema: SchemaDef = Field(
        examples=[
            {
                "type": "import",
                "import_path": "pydantic_ai:BinaryImage",
            },
            {
                "type": "inline",
                "fields": {
                    "name": {"type": "str", "description": "User name"},
                    "age": {"type": "int", "description": "User age"},
                },
            },
        ],
        title="Response schema",
    )
    """A model describing the response schema."""

    description: str | None = Field(
        default=None,
        examples=["User profile data", "Search results", "Code analysis output"],
        title="Response description",
    )
    """A description for this response definition."""

    result_tool_name: str = Field(
        default="final_result",
        examples=["final_result", "submit_answer", "return_data"],
        title="Result tool name",
    )
    """The tool name for the Agent tool to create the structured response."""

    result_tool_description: str | None = Field(
        default=None,
        examples=["Submit the final result", "Return structured data"],
        title="Result tool description",
    )
    """The tool description for the Agent tool to create the structured response."""

    output_retries: int | None = Field(default=None, examples=[1, 3, 5], title="Output retries")
    """Retry override. How often the Agent should try to validate the response."""

    @model_validator(mode="before")
    @classmethod
    def default_to_inline_schema(cls, data: Any) -> Any:
        """A `response_schema` without a `type` is an inline schema."""
        if (
            isinstance(data, dict)
            and isinstance(schema := data.get("response_schema"), dict)
            and "type" not in schema
        ):
            return {**data, "response_schema": {**schema, "type": "inline"}}
        return data
