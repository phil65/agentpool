"""Tests for command get_callable functionality."""

import inspect
from pathlib import Path
import tempfile

import pytest

from agentpool.utils.inspection import get_fn_name
from agentpool_config.commands import (
    CallableCommandConfig,
    FileCommandConfig,
    StaticCommandConfig,
)


def test_static_command_get_callable():
    """Test StaticCommandConfig.get_callable() generates proper function."""
    cmd = StaticCommandConfig(
        name="greet",
        content="Hello {name}, welcome to {project}!",
        description="Greet a user in a project",
    )

    func = cmd.get_callable()

    # Check function metadata
    assert get_fn_name(func) == "greet"
    assert func.__doc__ == "Greet a user in a project"

    # Check signature
    sig = inspect.signature(func)
    assert list(sig.parameters.keys()) == ["name", "project"]
    assert sig.return_annotation is str
    for param in sig.parameters.values():
        assert param.annotation is str
        assert param.default == ""

    # Check function execution
    result = func("Alice", project="MyApp")
    assert result == "Hello Alice, welcome to MyApp!"

    result = func(name="Bob", project="CoolProject")
    assert result == "Hello Bob, welcome to CoolProject!"


def test_file_command_get_callable():
    """Test FileCommandConfig.get_callable() generates proper function."""
    # Create temporary file with template content
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("Analyze {dataset} using {method} with {threshold} confidence")
        temp_path = f.name

    try:
        cmd = FileCommandConfig(name="analyze", path=temp_path, description="Run analysis command")
        func = cmd.get_callable()
        # Check function metadata
        assert get_fn_name(func) == "analyze"
        assert func.__doc__ == "Run analysis command"

        # Check signature
        sig = inspect.signature(func)
        param_names = list(sig.parameters.keys())
        assert set(param_names) == {"dataset", "method", "threshold"}
        assert sig.return_annotation is str

        # Check function execution
        result = func("sales_data", method="regression", threshold="95%")
        assert result == "Analyze sales_data using regression with 95% confidence"

    finally:
        Path(temp_path).unlink()


def test_static_command_no_parameters():
    """Test static command with no template parameters."""
    cmd = StaticCommandConfig(
        name="help",
        content="Show help information",
    )

    func = cmd.get_callable()

    # Should have no parameters
    sig = inspect.signature(func)
    assert len(sig.parameters) == 0

    # Should return content as-is
    result = func()
    assert result == "Show help information"


def test_callable_command_get_callable():
    """Test CallableCommandConfig.get_callable() returns the imported function."""

    def example_function(message: str = "default") -> str:
        """Example function for testing."""
        return f"Processed: {message}"

    # Mock the import by directly providing the function
    cmd = CallableCommandConfig(
        name="process",
        function=example_function,
        description="Process a message",
    )

    func = cmd.get_callable()

    # Should return the same function
    assert func is example_function

    # Test execution
    result = func("test input")
    assert result == "Processed: test input"

    result = func()
    assert result == "Processed: default"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
