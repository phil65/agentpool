"""Tests for the agentic_edit tool with forked agent context."""

from pathlib import Path
import tempfile
from typing import TYPE_CHECKING

import pytest

from agentpool import Agent
from agentpool_toolsets.fsspec_toolset import FSSpecTools


if TYPE_CHECKING:
    from tokonomics.model_names import ModelId

pytestmark = [pytest.mark.integration, pytest.mark.slow]
EDIT_MODEL: ModelId = "openrouter:anthropic/claude-haiku-4.5"


@pytest.fixture
def temp_file():
    """Create a temporary Python file for editing."""
    content = '''\
def greet(name):
    """Greet someone."""
    print(f"Hello, {name}!")


def main():
    greet("World")


if __name__ == "__main__":
    main()
'''
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(content)
        f.flush()
        yield Path(f.name)
    # Cleanup
    Path(f.name).unlink(missing_ok=True)


async def test_agentic_edit_basic(temp_file: Path):
    """Test basic agentic edit with forked context.

    This test verifies that:
    1. The agent can use agentic_edit as a tool
    2. The edit uses the full conversation context (forked)
    3. The file is actually modified
    4. The main conversation history is not polluted
    """
    # Create agent with FSSpec tools - must enable agentic edit mode
    fsspec_tools = FSSpecTools(edit_tool="agentic")

    async with Agent(
        name="editor",
        model=EDIT_MODEL,
        system_prompt="You are a helpful coding assistant. Use agentic_edit to modify files.",
        toolsets=[fsspec_tools],
    ) as agent:
        # First, establish some context by having a conversation
        initial_prompt = f"""I have a Python file at {temp_file} with a simple greeting function.
I want you to modify it to add a farewell function that says goodbye.
Please use agentic_edit to make this change."""

        # Run the agent - it should call agentic_edit
        [event async for event in agent.run_stream(initial_prompt)]

        # Verify the file was modified
        modified_content = temp_file.read_text()

        # Check that farewell/goodbye was added
        assert "farewell" in modified_content.lower() or "goodbye" in modified_content.lower(), (
            f"Expected farewell/goodbye in modified file, got:\n{modified_content}"
        )

        # Original content should still be there
        assert "def greet" in modified_content
        assert "Hello" in modified_content


async def test_agentic_edit_preserves_main_history(temp_file: Path):
    """Test that agentic_edit doesn't pollute the main conversation history."""
    fsspec_tools = FSSpecTools(edit_tool="agentic")

    async with Agent(
        name="editor",
        model=EDIT_MODEL,
        system_prompt="You are a helpful coding assistant.",
        toolsets=[fsspec_tools],
    ) as agent:
        # Get initial history length
        initial_history = agent.conversation.get_history()
        initial_len = len(initial_history)

        # Make a simple request first
        async for _ in agent.run_stream("What is 2+2? Answer briefly."):
            pass

        # Check history grew by expected amount (1 user + 1 assistant)
        after_first = agent.conversation.get_history()
        assert len(after_first) == initial_len + 2

        # Now do an agentic edit
        edit_prompt = f"Use agentic_edit to add a comment at the top of {temp_file}"
        async for _ in agent.run_stream(edit_prompt):
            pass

        # History should grow by 2 more (user + assistant with tool call/result)
        # The forked edit's internal messages should NOT be in history
        after_edit = agent.conversation.get_history()

        # We expect: initial + 2 (first exchange) + 2 (edit exchange) = initial + 4
        assert len(after_edit) == initial_len + 4, (
            f"Expected {initial_len + 4} messages, got {len(after_edit)}. "
            "Forked edit messages may have leaked into main history."
        )


async def test_agentic_edit_create_mode(temp_file: Path):
    """Test agentic_edit in create mode."""
    fsspec_tools = FSSpecTools(edit_tool="agentic")
    new_file = temp_file.parent / "new_module.py"

    try:
        async with Agent(
            name="creator",
            model=EDIT_MODEL,
            system_prompt="You are a coding assistant. Use agentic_edit with mode='create'.",
            toolsets=[fsspec_tools],
        ) as agent:
            prompt = f"""Create a new Python file at {new_file} that contains:
1. A function called 'add' that adds two numbers
2. A function called 'subtract' that subtracts two numbers
Use agentic_edit with mode='create'."""

            async for _ in agent.run_stream(prompt):
                pass

            # Verify file was created
            assert new_file.exists(), f"Expected {new_file} to be created"

            content = new_file.read_text()
            assert "def add" in content
            assert "def subtract" in content
    finally:
        new_file.unlink(missing_ok=True)


async def test_agentic_edit_diff_mode(temp_file: Path):
    """Test agentic_edit in diff-based edit mode (default)."""
    fsspec_tools = FSSpecTools(edit_tool="agentic")

    # Write specific content we can target
    temp_file.write_text('''\
def calculate(x, y):
    """Calculate something."""
    return x + y
''')

    async with Agent(
        name="editor",
        model=EDIT_MODEL,
        system_prompt="You are a code editor. Use agentic_edit to modify files.",
        toolsets=[fsspec_tools],
    ) as agent:
        prompt = f"""Edit {temp_file} to rename the function from 'calculate' to 'compute'.
Use agentic_edit in edit mode (the default). The edit uses diff format."""

        async for _ in agent.run_stream(prompt):
            pass

        content = temp_file.read_text()

        # Function should be renamed
        assert "def compute" in content, f"Expected 'def compute' in:\n{content}"
        assert "def calculate" not in content, f"'calculate' should be renamed in:\n{content}"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
