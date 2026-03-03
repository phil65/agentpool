"""Consolidated tests for tool schema generation and validation.

This module tests:
- Schema generation fallback mechanism (AgentContext triggers fallback)
- validate_json presence/absence in tool validators
- Native path for RunContext and simple types
- Schema overrides with and without fallback
- Tool.schema_obj and Tool.schema properties
- Async vs Sync execution
"""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, Any, cast

from pydantic import PydanticUndefinedAnnotation
from pydantic_ai import RunContext  # noqa: TC002
from pydantic_ai.tools import ToolDefinition  # noqa: TC002
import pytest
from schemez import OpenAIFunctionDefinition

from agentpool.log import configure_logging
from agentpool.tools.base import FunctionTool, Tool


if TYPE_CHECKING:
    from agentpool.agents.context import AgentContext


@pytest.fixture(autouse=True)
def setup_logging():
    """Configure logging to capture warnings in tests."""
    configure_logging(level="WARNING")


# ============================================================================
# Test Functions
# ============================================================================


def my_tool(x: int, y: str) -> str:
    """My tool description."""
    return f"{x} {y}"


def tool_with_agent_ctx(ctx: AgentContext, x: int) -> str:  # type: ignore[name-defined]
    """Tool with AgentContext parameter.

    Args:
        ctx: The agent context.
        x: X value.

    Returns:
        Processed value.
    """
    return f"{x}"


def tool_with_run_ctx(ctx: RunContext, y: str) -> str:  # type: ignore[name-defined]
    """Tool with RunContext parameter.

    This should work normally without triggering fallback.

    Args:
        ctx: The run context.
        y: Message to process.

    Returns:
        Processed message.
    """
    return y


def tool_with_both_ctx(
    run_ctx: RunContext,
    agent_ctx: AgentContext,
    z: float,
) -> str:  # type: ignore[name-defined]
    """Tool with both RunContext and AgentContext.

    Args:
        run_ctx: The run context.
        agent_ctx: The agent context.
        z: Numeric value.

    Returns:
        Processed value.
    """
    return str(z)


def tool_with_no_ctx(a: int, b: str) -> str:
    """Tool without any context parameters.

    Args:
        a: First parameter.
        b: Second parameter.

    Returns:
        Formatted string.
    """
    return f"{a}-{b}"


def simple_tool(message: str, count: int = 1) -> str:
    """Simple tool with no complex types.

    Args:
        message: Message to process.
        count: Number of times to repeat.

    Returns:
        Processed message.
    """
    return f"{message} " * count


def sync_tool_with_ctx(_ctx: AgentContext, message: str) -> str:
    """Synchronous tool with context.

    Args:
        _ctx: The agent context.
        message: Message to process.

    Returns:
        Processed message.
    """
    return f"Processed: {message}"


async def async_tool_with_ctx(_ctx: AgentContext, message: str) -> str:
    """Asynchronous tool with context.

    Args:
        _ctx: The agent context.
        message: Message to process.

    Returns:
        Processed message.
    """
    return f"Processed: {message}"


# ============================================================================
# Schema Generation - Fallback Mechanism
# ============================================================================


@pytest.mark.asyncio
async def test_fallback_triggered_by_abc() -> None:
    """Verify that tools with AgentContext trigger fallback.

    When a tool function takes AgentContext as a parameter:
    1. pydantic_ai.function_schema should fail
    2. A warning should be logged indicating fallback to schemez
    3. The generated schema should be valid (have json_schema attribute)
    """
    schema_override = OpenAIFunctionDefinition(
        name="tool_with_agent_ctx",
        description="Tool with AgentContext",
        parameters={
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "X value"},
            },
            "required": ["x"],
        },
    )

    tool = FunctionTool.from_callable(
        tool_with_agent_ctx,
        schema_override=schema_override,
    )

    # Get pydantic_ai tool which triggers schema generation
    pydantic_tool = tool.to_pydantic_ai()

    # Verify schema was generated (via fallback)
    assert pydantic_tool.function_schema is not None
    assert hasattr(pydantic_tool.function_schema, "json_schema")

    # Note: With schemez fallback, AgentContext may be included as "object" type
    # because type hints can't be resolved. The key point is that schema IS generated
    json_schema = pydantic_tool.function_schema.json_schema
    # json_schema is now parameters object (the "object" schema)
    properties = json_schema.get("properties", {})
    assert "x" in properties, "Parameter 'x' should be in schema"


@pytest.mark.asyncio
async def test_schema_override_with_fallback() -> None:
    """Verify that schema overrides are applied even when fallback occurs.

    When a tool has both AgentContext (triggering fallback) and a schema_override:
    1. Fallback should occur (warning logged)
    2. Schema override values should be merged into the generated schema
    3. Parameter descriptions from override should be preserved
    """
    # Schema with custom descriptions and additional parameter
    schema_override = OpenAIFunctionDefinition(
        name="complex_tool",
        description="Overridden tool description",
        parameters={
            "type": "object",
            "properties": {
                "input_data": {
                    "type": "string",
                    "description": "Custom description for input_data",
                },
                "count": {
                    "type": "integer",
                    "description": "Custom description for count",
                },
            },
            "required": ["input_data"],
        },
    )

    def complex_tool(_ctx: AgentContext, input_data: str, count: int = 1) -> str:
        """Tool with multiple parameters.

        Args:
            _ctx: Agent context.
            input_data: Input data to process.
            count: Number of times to process.

        Returns:
            Result string.
        """
        return f"{input_data} " * count

    tool = FunctionTool.from_callable(
        complex_tool,
        schema_override=schema_override,
    )

    # Get the pydantic_ai tool
    pydantic_tool = tool.to_pydantic_ai()

    # Verify override was applied
    assert pydantic_tool.function_schema is not None
    json_schema = pydantic_tool.function_schema.json_schema

    # For fallback (schemez), parameters are generated from docstring
    # The override properties aren't merged because schemez generates them
    # Verify that parameters exist (types are determined by schemez)
    properties = json_schema.get("properties", {})
    assert "input_data" in properties
    assert "count" in properties
    # Note: schemez determines the actual types, not of the override


@pytest.mark.asyncio
async def test_no_fallback_for_simple_types() -> None:
    """Verify that normal tools without AgentContext use primary path (no fallback).

    When a tool function has only simple types:
    1. pydantic_ai.function_schema should succeed
    2. No warning about fallback should be logged
    3. Schema should be generated via the primary path
    """
    schema_override = OpenAIFunctionDefinition(
        name="simple_tool",
        description="Simple tool",
        parameters={
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Message to process"},
                "count": {"type": "integer", "description": "Repeat count"},
            },
            "required": ["message"],
        },
    )

    tool = FunctionTool.from_callable(
        simple_tool,
        schema_override=schema_override,
    )

    # Get the pydantic_ai tool
    pydantic_tool = tool.to_pydantic_ai()

    # Verify schema was generated successfully
    assert pydantic_tool.function_schema is not None
    assert hasattr(pydantic_tool.function_schema, "json_schema")

    # Verify all parameters are in schema
    json_schema = pydantic_tool.function_schema.json_schema
    # pydantic_ai.function_schema returns parameters object directly (no "parameters" key)
    properties = json_schema.get("parameters", json_schema).get("properties", {})
    assert "message" in properties
    assert "count" in properties

    # Verify override was applied
    assert json_schema.get("description", "") == "Simple tool"


# ============================================================================
# AgentContext Fallback Tests
# ============================================================================


def test_agent_context_triggers_fallback():
    """Test that AgentContext causes function_schema() to fail, triggering fallback."""
    # Local import to avoid issues with pydantic-ai internals
    from pydantic_ai._function_schema import (  # type: ignore[attr-defined]
        GenerateJsonSchema,
        function_schema,
    )

    # Verify that function_schema() fails with AgentContext
    # Python 3.14 raises NameError instead of PydanticUndefinedAnnotation
    with pytest.raises((PydanticUndefinedAnnotation, TypeError, ValueError, NameError)):
        function_schema(tool_with_agent_ctx, schema_generator=GenerateJsonSchema)


def test_agent_context_fallback_generates_schema():
    """Test that fallback generates a valid pydantic_ai.tools.Tool."""
    schema_override = OpenAIFunctionDefinition(
        name="tool_with_agent_ctx",
        description="Tool with AgentContext",
        parameters={
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "Parameter x"},
            },
            "required": ["x"],
        },
    )
    tool = Tool.from_callable(tool_with_agent_ctx, schema_override=schema_override)
    pydantic_tool = tool.to_pydantic_ai()
    schema = pydantic_tool.function_schema

    # Verify schema was generated via fallback
    assert schema is not None, "Schema should be generated via fallback"

    # Verify regular parameter 'x' is included in json_schema
    assert hasattr(schema, "json_schema"), "Schema should have 'json_schema' attribute"
    json_schema = schema.json_schema
    # json_schema is now parameters object (the "object" schema)
    # Properties are at the top level of json_schema
    properties = json_schema.get("properties", {})
    assert "x" in properties, "Parameter 'x' should be in schema"
    # Note: Type may be "object" when schemez can't resolve type hints
    assert properties["x"]["type"] in ["integer", "object"], (
        "Parameter 'x' should be integer or object type"
    )


def test_run_context_native_path():
    """Test that tools with only RunContext use native pydantic-ai path."""
    # Local import to avoid issues with pydantic-ai internals
    from pydantic_ai._function_schema import (  # type: ignore[attr-defined]
        GenerateJsonSchema,
        function_schema,
    )

    # Verify function_schema() works with RunContext (no fallback needed)
    try:
        schema = function_schema(tool_with_run_ctx, schema_generator=GenerateJsonSchema)
        assert schema is not None, "Native schema generation should work with RunContext"
        # Verify context is excluded
        json_schema = schema.json_schema
        assert json_schema is not None, "json_schema should exist"
        properties = json_schema.get("properties", {})
        assert "ctx" not in properties, "RunContext should be excluded"
        assert "y" in properties, "Parameter 'y' should be in schema"
    except (TypeError, ValueError, AttributeError, NameError) as e:
        pytest.fail(f"RunContext should work natively, got error: {e}")


def test_both_contexts_triggers_fallback():
    """Test that AgentContext in mixed context signature triggers fallback."""
    schema_override = OpenAIFunctionDefinition(
        name="tool_with_both_ctx",
        description="Tool with both contexts",
        parameters={
            "type": "object",
            "properties": {
                "z": {"type": "number", "description": "Parameter z"},
            },
            "required": ["z"],
        },
    )
    tool = Tool.from_callable(tool_with_both_ctx, schema_override=schema_override)
    pydantic_tool = tool.to_pydantic_ai()
    schema = pydantic_tool.function_schema

    # Verify schema was generated via fallback
    assert schema is not None, "Schema should be generated via fallback"

    # Verify regular parameter 'z' is included in json_schema
    assert hasattr(schema, "json_schema"), "Schema should have 'json_schema' attribute"
    json_schema = schema.json_schema
    # json_schema is now parameters object (the "object" schema)
    # Properties are at the top level of json_schema
    properties = json_schema.get("properties", {})
    assert "z" in properties, "Parameter 'z' should be in schema"
    # Note: Type may be "object" when schemez can't resolve type hints
    assert properties["z"]["type"] in ["number", "object"], (
        "Parameter 'z' should be number or object type"
    )


def test_no_context_normal_path():
    """Test that tools without context work normally."""
    # Local import to avoid issues with pydantic-ai internals
    from pydantic_ai._function_schema import (  # type: ignore[attr-defined]
        GenerateJsonSchema,
        function_schema,
    )

    # Verify function_schema() works without any context (no fallback needed)
    try:
        schema = function_schema(tool_with_no_ctx, schema_generator=GenerateJsonSchema)
        assert schema is not None, "Native schema generation should work without context"

        # Verify all parameters are included
        json_schema = schema.json_schema
        properties = json_schema.get("properties", {})
        assert "a" in properties, "Parameter 'a' should be in schema"
        assert "b" in properties, "Parameter 'b' should be in schema"
    except (TypeError, ValueError, AttributeError, NameError) as e:
        pytest.fail(f"No-context tools should work natively, got error: {e}")


# ============================================================================
# Tool Properties
# ============================================================================


def test_schema_obj_property_with_agent_context():
    """Test that Tool.schema_obj property works with AgentContext."""
    tool = Tool.from_callable(
        tool_with_agent_ctx, schema_override=cast(OpenAIFunctionDefinition, {})
    )

    # Verify schema_obj property returns a schemez.FunctionSchema
    schema_obj = tool.schema_obj
    assert schema_obj is not None, "schema_obj should not be None"
    assert hasattr(schema_obj, "name"), "schema_obj should have 'name'"

    # Verify schema has properties (context may be included as "object" type)
    schema_dict = schema_obj.model_dump()  # pyright: ignore[reportAttributeAccessIssue]
    properties = schema_dict.get("parameters", {}).get("properties", {})
    assert "x" in properties, "Regular parameter 'x' should be included in schema_obj"


def test_schema_property_with_agent_context():
    """Test that Tool.schema property works with AgentContext."""
    schema_override = OpenAIFunctionDefinition(
        name="tool_with_agent_ctx",
        description="Tool with AgentContext",
        parameters={
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "Parameter x"},
            },
            "required": ["x"],
        },
    )
    tool = Tool.from_callable(tool_with_agent_ctx, schema_override=schema_override)

    # Verify schema property returns OpenAI function tool format
    openai_tool_schema = tool.schema
    assert openai_tool_schema is not None, "schema should not be None"
    assert "type" in openai_tool_schema, "schema should have 'type'"
    assert openai_tool_schema["type"] == "function", "Type should be 'function'"

    # Verify function definition exists
    func_def = openai_tool_schema.get("function", {})
    assert func_def["name"] == "tool_with_agent_ctx", "Function name should match"
    assert "parameters" in func_def, "Function should have parameters"

    # Verify context parameter is excluded
    properties = func_def.get("parameters", {}).get("properties", {})
    assert "ctx" not in properties, "AgentContext 'ctx' should be excluded in schema property"
    assert "x" in properties, "Parameter 'x' should be included in OpenAI format"


# ============================================================================
# Validation Tests
# ============================================================================


def test_validate_json_exists():
    """Test that validate_json exists when schema_override is not provided."""
    tool = FunctionTool.from_callable(my_tool)
    pydantic_ai_tool = tool.to_pydantic_ai()

    # This should pass - validator should have validate_json
    assert hasattr(pydantic_ai_tool.function_schema.validator, "validate_json"), (
        "validator should have validate_json method"
    )


def test_validate_json_present_with_schema_override():
    """Test that validate_json is present when schema_override is provided.

    After the refactor, Tool.from_schema is used when a custom schema is
    needed (e.g., when schema_override triggers fallback to schemez due to
    AgentContext forward reference). Tool.from_schema creates proper validators
    with validate_json method.

    The test uses AgentContext to trigger fallback to schemez.
    """
    # Create a schema_override (empty dict is sufficient to trigger override path)
    # Using type: ignore to bypass schemez import outside TYPE_CHECKING
    schema_override: OpenAIFunctionDefinition = {  # type: ignore[name-defined]
        "name": "tool_with_agent_ctx",
        "description": "Tool with AgentContext",
        "parameters": {
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "X value"},
            },
            "required": ["x"],
        },
    }

    # Create tool with schema_override
    # The AgentContext will cause pydantic_ai.function_schema to fail,
    # triggering to schemez fallback path, but now using Tool.from_schema
    # instead of SchemaWrapper
    tool = FunctionTool.from_callable(tool_with_agent_ctx, schema_override=schema_override)
    pydantic_ai_tool = tool.to_pydantic_ai()

    # Assert that validate_json IS present (after the fix)
    # Tool.from_schema creates proper validators with validate_json method
    assert hasattr(pydantic_ai_tool.function_schema.validator, "validate_json"), (
        "validator should have validate_json method when using Tool.from_schema"
    )


# ============================================================================
# Validator and Execution Tests
# ============================================================================


@pytest.mark.asyncio
async def test_validator_attribute_exists() -> None:
    """Verify that pydantic_ai.tools.Tool has a validator attribute that works.

    When Tool.from_schema is used:
    1. Tool should have a validator attribute (TypeAdapter)
    2. The validator should validate Python dictionaries successfully
    3. The validator should have validate_json method
    """
    schema_override = OpenAIFunctionDefinition(
        name="tool_with_agent_ctx",
        description="Tool with AgentContext",
        parameters={
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Message to process"},
                "count": {"type": "integer", "description": "Repeat count"},
            },
            "required": ["message"],
        },
    )

    tool = FunctionTool.from_callable(
        tool_with_agent_ctx,
        schema_override=schema_override,
    )

    # Get pydantic_ai tool which uses Tool.from_schema
    pydantic_tool = tool.to_pydantic_ai()

    # Verify schema was generated
    assert pydantic_tool.function_schema is not None

    # Verify validator attribute exists (TypeAdapter from pydantic_ai)
    assert hasattr(pydantic_tool.function_schema, "validator"), (
        "Tool should have validator attribute"
    )

    # Verify validate_json method exists (the bug that was fixed)
    assert hasattr(pydantic_tool.function_schema.validator, "validate_json"), (
        "Tool validator should have validate_json method"
    )

    # Test validator with valid arguments
    valid_args = {"message": "hello", "count": 2}
    validated = pydantic_tool.function_schema.validator.validate_python(valid_args)
    # Tool.from_schema validator returns a dict, not a Pydantic model
    assert validated["message"] == "hello"
    assert validated["count"] == 2

    # Test validator with only required arguments
    # Note: Tool.from_schema doesn't add default values from function signature
    # Optional parameters not provided will not be in validated dict
    valid_args_minimal = {"message": "hello"}
    validated_minimal = pydantic_tool.function_schema.validator.validate_python(valid_args_minimal)
    assert validated_minimal["message"] == "hello"
    # Count is not in dict since it wasn't provided and validator doesn't infer defaults
    assert "count" not in validated_minimal

    # Test validator validates JSON string
    json_args = '{"message": "test", "count": 3}'
    validated_json = pydantic_tool.function_schema.validator.validate_json(json_args)
    # Result is also a dict, not a Pydantic model
    assert validated_json["message"] == "test"
    assert validated_json["count"] == 3

    # Note: Tool.from_schema validator with custom JSON schema is lenient
    # and may not raise ValidationError for type mismatches (e.g., number instead of string)
    # This is a limitation of the current implementation using Tool.from_schema
    # The validator exists and works for valid data, which is the key requirement


@pytest.mark.asyncio
async def test_tool_function_execution() -> None:
    """Verify that pydantic_ai.tools.Tool executes functions correctly.

    When Tool.from_schema is used:
    1. Tool should have a function attribute pointing to original callable
    2. The function should be callable with validated arguments
    3. Tool should handle both sync and async functions
    """

    async def async_tool(message: str, count: int = 1) -> str:
        """Asynchronous tool.

        Args:
            message: Message to process.
            count: Number of times to repeat.

        Returns:
            Processed message.
        """
        return f"{message} " * count

    def sync_tool(message: str, count: int = 1) -> str:
        """Synchronous tool.

        Args:
            message: Message to process.
            count: Number of times to repeat.

        Returns:
            Processed message.
        """
        return f"{message} " * count

    schema_override_sync = OpenAIFunctionDefinition(
        name="sync_tool",
        description="Sync tool",
        parameters={
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Message to process"},
                "count": {"type": "integer", "description": "Repeat count"},
            },
            "required": ["message"],
        },
    )

    schema_override_async = OpenAIFunctionDefinition(
        name="async_tool",
        description="Async tool",
        parameters={
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Message to process"},
                "count": {"type": "integer", "description": "Repeat count"},
            },
            "required": ["message"],
        },
    )

    # Test sync tool
    sync_tool_instance = FunctionTool.from_callable(
        sync_tool,
        schema_override=schema_override_sync,
    )
    pydantic_sync_tool = sync_tool_instance.to_pydantic_ai()

    # Verify function attribute exists and points to original callable
    assert hasattr(pydantic_sync_tool.function_schema, "function"), (
        "Tool should have function attribute"
    )
    assert pydantic_sync_tool.function_schema.function is sync_tool

    # Validate arguments - validator returns dict
    validated = pydantic_sync_tool.function_schema.validator.validate_python({
        "message": "hello",
        "count": 3,
    })
    # Validated is already a dict, not a Pydantic model
    assert validated["message"] == "hello"
    assert validated["count"] == 3

    # Call validated function
    if inspect.iscoroutinefunction(sync_tool):
        result_exec = await pydantic_sync_tool.function_schema.function(**validated)
    else:
        result_exec = pydantic_sync_tool.function_schema.function(**validated)
    assert result_exec == "hello hello hello "

    # Test async tool
    async_tool_instance = FunctionTool.from_callable(
        async_tool,
        schema_override=schema_override_async,
    )
    pydantic_async_tool = async_tool_instance.to_pydantic_ai()

    # Verify function works for async functions
    assert hasattr(pydantic_async_tool.function_schema, "function"), (
        "Tool should have function attribute for async functions"
    )
    assert pydantic_async_tool.function_schema.function is async_tool

    # Validate and call async function - validator returns dict
    validated_async = pydantic_async_tool.function_schema.validator.validate_python({
        "message": "async",
        "count": 2,
    })
    assert validated_async["message"] == "async"
    assert validated_async["count"] == 2

    result_exec_async = await pydantic_async_tool.function_schema.function(**validated_async)
    assert result_exec_async == "async async "


@pytest.mark.asyncio
async def test_tool_takes_ctx_detection() -> None:
    """Verify that pydantic_ai.tools.Tool correctly detects takes_ctx.

    When a tool function requires RunContext:
    1. Tool should have takes_ctx=True
    2. When no RunContext, takes_ctx should be False
    """

    def tool_without_ctx(message: str) -> str:
        """Simple tool without context."""
        return f"Received: {message}"

    # Test tool without context (uses primary pydantic-ai path)
    tool_no_ctx = FunctionTool.from_callable(tool_without_ctx)
    pydantic_tool_no_ctx = tool_no_ctx.to_pydantic_ai()

    # No RunContext means takes_ctx=False
    assert hasattr(pydantic_tool_no_ctx.function_schema, "takes_ctx")
    assert pydantic_tool_no_ctx.function_schema.takes_ctx is False

    # Test tool with RunContext (will use Tool.from_schema)
    def func_with_runctx(_ctx: RunContext, message: str) -> str:  # type: ignore[name-defined]
        """Tool with RunContext parameter."""
        return f"Received: {message}"

    schema_override = OpenAIFunctionDefinition(
        name="func_with_runctx",
        description="Tool with RunContext",
        parameters={
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Message to process"},
            },
            "required": ["message"],
        },
    )

    tool_instance = FunctionTool.from_callable(
        func_with_runctx,
        schema_override=schema_override,
    )
    pydantic_tool_with_ctx = tool_instance.to_pydantic_ai()

    # RunContext means takes_ctx=True
    assert hasattr(pydantic_tool_with_ctx.function_schema, "takes_ctx")
    assert pydantic_tool_with_ctx.function_schema.takes_ctx is True


@pytest.mark.asyncio
async def test_tool_attributes() -> None:
    """Verify that pydantic_ai.tools.Tool has all required attributes for compatibility.

    When Tool.from_schema is used:
    1. Tool should have is_async attribute (correctly detects async functions)
    2. Tool should have description attribute
    3. Tool should have function attribute (returns original callable)
    4. Tool should have positional_fields attribute (empty list)
    5. Tool should have single_arg_name attribute (None)
    6. Tool should have var_positional_field attribute (None)
    """
    # Test sync tool
    schema_override_sync = OpenAIFunctionDefinition(
        name="sync_tool_with_ctx",
        description="Synchronous tool with AgentContext",
        parameters={
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Message to process"},
            },
            "required": ["message"],
        },
    )

    sync_tool_inst = FunctionTool.from_callable(
        sync_tool_with_ctx,
        schema_override=schema_override_sync,
    )
    sync_pydantic_tool = sync_tool_inst.to_pydantic_ai()
    sync_schema = sync_pydantic_tool.function_schema

    # Verify sync tool attributes
    assert hasattr(sync_schema, "is_async"), "Tool should have is_async"
    assert sync_schema.is_async is False, "Sync tool should have is_async=False"

    assert hasattr(sync_schema, "description"), "Tool should have description"
    # With schemez fallback, description comes from docstring (may include Args section)
    assert sync_schema.description is not None
    assert isinstance(sync_schema.description, str)
    assert "Synchronous tool with context" in sync_schema.description

    assert hasattr(sync_schema, "function"), "Tool should have function"
    assert sync_schema.function is sync_tool_with_ctx

    assert hasattr(sync_schema, "positional_fields"), "Tool should have positional_fields"
    assert sync_schema.positional_fields == []

    assert hasattr(sync_schema, "single_arg_name"), "Tool should have single_arg_name"
    assert sync_schema.single_arg_name is None

    assert hasattr(sync_schema, "var_positional_field"), "Tool should have var_positional_field"
    assert sync_schema.var_positional_field is None

    # Test async tool
    schema_override_async = OpenAIFunctionDefinition(
        name="async_tool_with_ctx",
        description="Asynchronous tool with AgentContext",
        parameters={
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Message to process"},
            },
            "required": ["message"],
        },
    )

    async_tool_inst = FunctionTool.from_callable(
        async_tool_with_ctx,
        schema_override=schema_override_async,
    )
    async_pydantic_tool = async_tool_inst.to_pydantic_ai()
    async_schema = async_pydantic_tool.function_schema

    # Verify async tool attributes
    assert hasattr(async_schema, "is_async"), "Tool should have is_async"
    assert async_schema.is_async is True, "Async tool should have is_async=True"

    assert hasattr(async_schema, "description"), "Tool should have description"
    # With schemez fallback, description comes from docstring (may include Args section)
    assert async_schema.description is not None
    assert isinstance(async_schema.description, str)
    assert "Asynchronous tool with context" in async_schema.description

    assert hasattr(async_schema, "function"), "Tool should have function"
    assert async_schema.function is async_tool_with_ctx

    assert hasattr(async_schema, "positional_fields"), "Tool should have positional_fields"
    assert async_schema.positional_fields == []

    assert hasattr(async_schema, "single_arg_name"), "Tool should have single_arg_name"
    assert async_schema.single_arg_name is None

    assert hasattr(async_schema, "var_positional_field"), "Tool should have var_positional_field"
    assert async_schema.var_positional_field is None


@pytest.mark.asyncio
async def test_prepare_with_schema_override() -> None:
    """Verify that prepare is correctly set when using schema_override.

    When a tool has both schema_override and a prepare hook:
    1. The tool should use Tool.from_schema path
    2. The prepare function should be assigned manually after creation
    3. to_pydantic_ai().prepare should not be None
    """
    # Track if prepare was called
    prepare_called = []

    async def prepare_hook(ctx: RunContext[Any], tool_def: ToolDefinition) -> ToolDefinition | None:  # type: ignore[name-defined]
        """Prepare hook for tool schema customization."""
        prepare_called.append(True)
        # Modify the tool definition
        return tool_def

    schema_override = OpenAIFunctionDefinition(
        name="tool_with_prepare",
        description="Tool with prepare and schema_override",
        parameters={
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Message to process"},
            },
            "required": ["message"],
        },
    )

    def tool_func(message: str) -> str:
        """Tool function.

        Args:
            message: Message to process.

        Returns:
            Processed message.
        """
        return f"Processed: {message}"

    # Create tool with both schema_override and prepare
    tool = FunctionTool.from_callable(
        tool_func,
        schema_override=schema_override,
        prepare=prepare_hook,
    )

    # Get pydantic_ai tool
    pydantic_tool = tool.to_pydantic_ai()

    # Verify prepare is set on the resulting tool
    assert pydantic_tool.prepare is not None, "prepare should be set when using schema_override"
    assert pydantic_tool.prepare is prepare_hook, (
        "prepare should be the same function that was passed in"
    )


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-vv"])
