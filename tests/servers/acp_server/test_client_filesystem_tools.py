"""Tests for FSSpecTools using MockExecutionEnvironment."""

from exxec import MockExecutionEnvironment
import pytest

from agentpool import Agent, AgentContext
from agentpool.tools.base import ToolResult
from agentpool_toolsets.fsspec_toolset import FSSpecTools


@pytest.fixture
def agent_ctx() -> AgentContext:
    """Create a fresh mock context for each test."""
    agent = Agent(name="test_agent", model="test")
    return AgentContext(node=agent)


@pytest.fixture
def mock_env() -> MockExecutionEnvironment:
    """Create mock execution environment."""
    return MockExecutionEnvironment()


@pytest.fixture
def fs_tools(mock_env: MockExecutionEnvironment) -> FSSpecTools:
    """Create FSSpecTools with mock environment."""
    return FSSpecTools(source=mock_env, name="test_fs")


async def test_read_file_success(
    fs_tools: FSSpecTools,
    mock_env: MockExecutionEnvironment,
    agent_ctx: AgentContext,
):
    """Test successful file reading."""
    await mock_env.set_file_content("/home/user/test.txt", "Hello, World!\nThis is a test file.\n")

    tools = await fs_tools.get_tools()
    read_tool = next(tool for tool in tools if tool.name == "read")
    result = await read_tool.execute_and_unwrap(ctx=agent_ctx, path="/home/user/test.txt")

    assert isinstance(result, str)
    assert "Hello, World!" in result
    assert "This is a test file." in result


async def test_read_file_with_line_and_limit(
    fs_tools: FSSpecTools,
    mock_env: MockExecutionEnvironment,
    agent_ctx: AgentContext,
):
    """Test file reading with line and limit parameters."""
    await mock_env.set_file_content(
        "/home/large_file.txt", "Line 1\nLine 2\nLine 3\nLine 4\nLine 5"
    )

    tools = await fs_tools.get_tools()
    read_tool = next(tool for tool in tools if tool.name == "read")
    result = await read_tool.execute_and_unwrap(
        ctx=agent_ctx, path="/home/large_file.txt", line=2, limit=2
    )

    assert isinstance(result, str)
    content_lines = result.split("\n")
    assert "Line 2" in content_lines
    assert "Line 3" in content_lines
    assert len(content_lines) == 2


async def test_read_file_error(
    fs_tools: FSSpecTools,
    agent_ctx: AgentContext,
):
    """Test file reading error handling."""
    tools = await fs_tools.get_tools()
    read_tool = next(tool for tool in tools if tool.name == "read")
    result = await read_tool.execute_and_unwrap(ctx=agent_ctx, path="/home/user/nonexistent.txt")
    assert "error" in result


async def test_write_text_file_success(
    fs_tools: FSSpecTools,
    mock_env: MockExecutionEnvironment,
    agent_ctx: AgentContext,
):
    """Test successful file writing."""
    tools = await fs_tools.get_tools()
    write_tool = next(tool for tool in tools if tool.name == "write")
    result = await write_tool.run(
        agent_ctx=agent_ctx,
        path="/home/user/output.txt",
        content="Hello, World!\nThis is written content.\n",
    )

    assert isinstance(result, ToolResult)
    assert "/home/user/output.txt" in result.content

    # Verify content was written
    content = await mock_env.get_file_content("/home/user/output.txt")
    assert content == b"Hello, World!\nThis is written content.\n"


async def test_write_text_file_json(
    fs_tools: FSSpecTools,
    mock_env: MockExecutionEnvironment,
    agent_ctx: AgentContext,
):
    """Test writing JSON content."""
    json_str = '{\n  "debug": true,\n  "version": "1.0.0"\n}'

    tools = await fs_tools.get_tools()
    write_tool = next(tool for tool in tools if tool.name == "write")
    result = await write_tool.run(
        agent_ctx=agent_ctx, path="/home/user/config.json", content=json_str
    )

    assert isinstance(result, ToolResult)
    assert "/home/user/config.json" in result.content

    content = await mock_env.get_file_content("/home/user/config.json")
    assert content == json_str.encode()


async def test_read_empty_file(
    fs_tools: FSSpecTools,
    mock_env: MockExecutionEnvironment,
    agent_ctx: AgentContext,
):
    """Test reading an empty file."""
    await mock_env.set_file_content("/home/user/empty.txt", "")

    tools = await fs_tools.get_tools()
    read_tool = next(tool for tool in tools if tool.name == "read")
    result = await read_tool.execute_and_unwrap(ctx=agent_ctx, path="/home/user/empty.txt")

    assert isinstance(result, str)
    assert result == ""


async def test_write_empty_file(
    fs_tools: FSSpecTools,
    mock_env: MockExecutionEnvironment,
    agent_ctx: AgentContext,
):
    """Test writing empty content to a file."""
    tools = await fs_tools.get_tools()
    write_tool = next(tool for tool in tools if tool.name == "write")
    result = await write_tool.run(
        agent_ctx=agent_ctx, path="/home/user/empty_output.txt", content=""
    )

    assert isinstance(result, ToolResult)
    assert "/home/user/empty_output.txt" in result.content

    content = await mock_env.get_file_content("/home/user/empty_output.txt")
    assert content == b""


async def test_read_file_with_unicode(
    fs_tools: FSSpecTools,
    mock_env: MockExecutionEnvironment,
    agent_ctx: AgentContext,
):
    """Test reading file with unicode content."""
    content = "Hello 世界! 🌍\nThis has émojis and spëcial chars: café"
    await mock_env.set_file_content("/home/user/unicode.txt", content)

    tools = await fs_tools.get_tools()
    read_tool = next(tool for tool in tools if tool.name == "read")
    result = await read_tool.execute_and_unwrap(ctx=agent_ctx, path="/home/user/unicode.txt")

    assert isinstance(result, str)
    assert "世界" in result
    assert "🌍" in result
    assert "émojis" in result
    assert "café" in result


async def test_write_file_with_unicode(
    fs_tools: FSSpecTools,
    mock_env: MockExecutionEnvironment,
    agent_ctx: AgentContext,
):
    """Test writing file with unicode content."""
    content = "Testing unicode: 日本語, русский, العربية 🎉"

    tools = await fs_tools.get_tools()
    write_tool = next(tool for tool in tools if tool.name == "write")
    result = await write_tool.run(agent_ctx=agent_ctx, path="/home/output.txt", content=content)

    assert isinstance(result, ToolResult)
    assert "/home/output.txt" in result.content

    written = await mock_env.get_file_content("/home/output.txt")
    assert written == content.encode()


async def test_read_then_write(
    fs_tools: FSSpecTools,
    mock_env: MockExecutionEnvironment,
    agent_ctx: AgentContext,
):
    """Test reading and writing in sequence."""
    await mock_env.set_file_content("/home/user/test.txt", "original content")

    tools = await fs_tools.get_tools()
    read_tool = next(tool for tool in tools if tool.name == "read")
    write_tool = next(tool for tool in tools if tool.name == "write")

    # Read original
    result = await read_tool.execute_and_unwrap(ctx=agent_ctx, path="/home/user/test.txt")
    assert result == "original content"

    # Write new content
    await write_tool.run(
        agent_ctx=agent_ctx, path="/home/user/test.txt", content="modified content", overwrite=True
    )

    # Read modified
    result = await read_tool.execute_and_unwrap(ctx=agent_ctx, path="/home/user/test.txt")
    assert result == "modified content"


if __name__ == "__main__":
    pytest.main(["-v", __file__])
