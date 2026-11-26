"""Tests for `output_type` on agents: inline definitions and named references."""

from typing import Any

from pydantic import BaseModel, ValidationError
import pytest
from schemez import ImportedSchemaDef, InlineSchemaDef

from agentpool import Agent, AgentsManifest
from agentpool_config.output_types import StructuredResponseConfig


FIELDS = {"score": {"type": "int", "description": "Score"}, "tags": {"type": "list[str]"}}


def _manifest(output_type: Any, **extra: Any) -> AgentsManifest:
    agent = {"type": "native", "model": "test", "output_type": output_type}
    return AgentsManifest.model_validate({"agents": {"a": agent}, **extra})


def test_inline_output_type():
    manifest = _manifest({"response_schema": {"type": "inline", "fields": FIELDS}})
    output_type = manifest.agents["a"].output_type
    assert isinstance(output_type, StructuredResponseConfig)
    assert isinstance(output_type.response_schema, InlineSchemaDef)
    model = manifest.get_output_type("a")
    assert model is not None
    assert issubclass(model, BaseModel)
    assert set(model.model_fields) == {"score", "tags"}


def test_inline_output_type_defaults_to_inline_schema():
    manifest = _manifest({"response_schema": {"fields": FIELDS}})
    output_type = manifest.agents["a"].output_type
    assert isinstance(output_type, StructuredResponseConfig)
    assert isinstance(output_type.response_schema, InlineSchemaDef)


def test_inline_output_type_with_import_schema():
    schema = {"type": "import", "import_path": "pydantic_ai:BinaryImage"}
    manifest = _manifest({"response_schema": schema})
    output_type = manifest.agents["a"].output_type
    assert isinstance(output_type, StructuredResponseConfig)
    assert isinstance(output_type.response_schema, ImportedSchemaDef)


def test_inline_output_type_settings():
    manifest = _manifest({
        "response_schema": {"fields": FIELDS},
        "result_tool_name": "submit",
        "output_retries": 3,
    })
    agent = manifest.agents["a"]
    assert agent.output_retries == 3
    assert isinstance(agent.output_type, StructuredResponseConfig)
    assert agent.output_type.result_tool_name == "submit"


def test_agent_level_output_retries_win():
    agent = {
        "type": "native",
        "model": "test",
        "output_retries": 1,
        "output_type": {"response_schema": {"fields": FIELDS}, "output_retries": 5},
    }
    manifest = AgentsManifest.model_validate({"agents": {"a": agent}})
    assert manifest.agents["a"].output_retries == 1


def test_named_output_type():
    responses = {"Result": {"response_schema": {"type": "inline", "fields": FIELDS}}}
    manifest = _manifest("Result", responses=responses)
    assert manifest.agents["a"].output_type == "Result"
    model = manifest.get_output_type("a")
    assert model is not None
    assert set(model.model_fields) == {"score", "tags"}


@pytest.mark.parametrize(
    "output_type",
    [
        {"description": "no schema"},
        {"result_tool_name": "submit"},
        {},
    ],
)
def test_inline_output_type_without_response_schema(output_type: dict[str, Any]):
    with pytest.raises(ValidationError, match="response_schema"):
        _manifest(output_type)


def test_inline_output_type_with_invalid_schema():
    with pytest.raises(ValidationError, match="fields"):
        _manifest({"response_schema": {"type": "inline"}})


async def test_agent_from_inline_output_type():
    manifest = _manifest({"response_schema": {"fields": FIELDS}})
    agent = Agent.from_config(manifest.agents["a"])  # ty: ignore[invalid-argument-type]
    assert issubclass(agent._output_type, BaseModel)
    assert set(agent._output_type.model_fields) == {"score", "tags"}
