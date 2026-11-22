"""Test enum elicitation response handling in ACP server."""

from __future__ import annotations

from aclientp import RequestPermissionResponse
from mcp import types

from agentpool_server.acp_server.input_provider import (
    _enum_field,
    _handle_enum_elicitation_response,
)


def test_enum_elicitation_response_returns_dict_format():
    """Test that enum elicitation response returns content in correct dict format.

    According to MCP spec and OpenCode server implementation,
    content should be wrapped in a dict with a "value" key.

    This test reproduces the bug where the ACP server returns
    a plain string instead of {"value": "..."}
    """
    # Create a mock schema with enum options
    schema = {
        "type": "string",
        "enum": ["option_a", "option_b", "option_c"],
    }

    # Simulate user selecting the first option
    response = RequestPermissionResponse.model_validate({
        "outcome": {
            "outcome": "selected",
            "optionId": "enum_0_option_a",
        }
    })

    # Call the function
    result = _handle_enum_elicitation_response(response, None, schema)

    # Verify the result is an ElicitResult (not ErrorData)
    assert isinstance(result, types.ElicitResult)

    # According to MCP spec and OpenCode implementation,
    # content should be a dict with "value" key
    assert result.action == "accept"
    assert isinstance(result.content, dict), (
        f"Expected content to be dict, got {type(result.content)}"
    )
    assert "value" in result.content, (
        f"Expected content to have 'value' key, got keys: {result.content.keys()}"
    )
    assert result.content["value"] == "option_a", (
        f"Expected content['value'] to be 'option_a', got '{result.content.get('value')}'"
    )


def test_enum_elicitation_response_handles_cancel():
    """Test that the cancel option is correctly handled."""
    schema = {
        "type": "string",
        "enum": ["option1", "option2"],
    }

    response = RequestPermissionResponse.model_validate({
        "outcome": {
            "outcome": "selected",
            "optionId": "cancel",
        }
    })

    result = _handle_enum_elicitation_response(response, None, schema)

    # Verify the result is an ElicitResult (not ErrorData)
    assert isinstance(result, types.ElicitResult)
    assert result.action == "cancel"


def test_enum_elicitation_response_handles_multiple_options():
    """Test that different enum options are correctly handled."""
    schema = {
        "type": "string",
        "enum": ["option_x", "option_y", "option_z"],
    }

    # Test selecting the second option
    response = RequestPermissionResponse.model_validate({
        "outcome": {
            "outcome": "selected",
            "optionId": "enum_1_option_y",
        }
    })

    result = _handle_enum_elicitation_response(response, None, schema)

    # Verify the result is an ElicitResult (not ErrorData)
    assert isinstance(result, types.ElicitResult)
    assert result.action == "accept"
    assert isinstance(result.content, dict)
    assert result.content["value"] == "option_y"

    # Test selecting the third option
    response2 = RequestPermissionResponse.model_validate({
        "outcome": {
            "outcome": "selected",
            "optionId": "enum_2_option_z",
        }
    })

    result2 = _handle_enum_elicitation_response(response2, None, schema)

    # Verify the result is an ElicitResult (not ErrorData)
    assert isinstance(result2, types.ElicitResult)
    assert result2.action == "accept"
    assert isinstance(result2.content, dict)
    assert result2.content["value"] == "option_z"


def test_form_with_one_enum_property_is_an_enum_question():
    """MCP forms are objects: a single string enum property is asked as a choice."""
    prop = {"type": "string", "title": "Color", "enum": ["Red", "Blue"]}
    schema = {"type": "object", "properties": {"color": prop}, "required": ["color"]}
    assert _enum_field(schema) == ("color", prop)
    response = RequestPermissionResponse.model_validate({
        "outcome": {"outcome": "selected", "optionId": "enum_1_Blue"}
    })
    result = _handle_enum_elicitation_response(response, "color", prop)
    assert isinstance(result, types.ElicitResult)
    assert (result.action, result.content) == ("accept", {"color": "Blue"})


def test_other_forms_are_not_enum_questions():
    text = {"type": "object", "properties": {"name": {"type": "string"}}}
    two = {
        "type": "object",
        "properties": {"a": {"type": "string", "enum": ["x"]}, "b": {"type": "string"}},
    }
    multi = {
        "type": "object",
        "properties": {"m": {"type": "array", "items": {"type": "string", "enum": ["x"]}}},
    }
    assert _enum_field(text) is None
    assert _enum_field(two) is None
    assert _enum_field(multi) is None
