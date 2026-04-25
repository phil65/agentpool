"""Tests for agent configuration models."""

from __future__ import annotations

from pydantic import ValidationError
import pytest
from schemez import InlineSchemaDef
import yamling

from agentpool import AgentsManifest


VALID_AGENT_CONFIG = """\
responses:
  TestResponse:
    response_schema:
        description: Test response
        type: inline
        fields:
            message:
                type: str
                description: A message
            score:
                type: int
                ge: 0
                le: 100

agents:
  test_agent:  # Key is the agent ID
    type: native
    name: Test Agent
    description: A test agent
    model: test
    output_type: TestResponse
    system_prompt: You are a test agent
"""

INVALID_RESPONSE_CONFIG = """\
responses:
  InvalidResponse:
    type: object

agents:
  test_agent:
    type: native
    model: "openai:gpt-4o"
    system_prompt: "test"
    output_type: NonExistentResponse
"""


ENV_CONFIG = """\
{}
"""

ENV_AGENT = """\
responses:
    BasicResult:
       response_schema:
            description: Test result
            type: inline
            fields:
                message:
                    type: str
                    description: Test message

agents:
    test_agent:
        type: native
        name: test
        model: test
        output_type: BasicResult
"""


def test_valid_agent_definition():
    """Test valid complete agent configuration."""
    agent_def = AgentsManifest.model_validate(yamling.load_yaml(VALID_AGENT_CONFIG))
    schema = agent_def.responses["TestResponse"].response_schema
    assert isinstance(schema, InlineSchemaDef)
    score = schema.fields["score"]
    assert score.ge == 0
    assert score.le == 100


def test_missing_referenced_response():
    """Test referencing non-existent response model."""
    config = yamling.load_yaml(INVALID_RESPONSE_CONFIG)
    with pytest.raises(ValidationError):
        AgentsManifest.model_validate(config)
