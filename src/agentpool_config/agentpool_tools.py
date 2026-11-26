"""Models for agentpool standalone tool configuration."""

from typing import TYPE_CHECKING, Literal

from exxec_config import ExecutionEnvironmentConfig  # noqa: TC002
from pydantic import ConfigDict, Field

from agentpool_config.converters import ConversionConfig  # noqa: TC001
from agentpool_config.tools import BaseToolConfig


if TYPE_CHECKING:
    from agentpool.tools.base import Tool


class BashToolConfig(BaseToolConfig):
    """Configuration for bash command execution tool.

    Example:
        ```yaml
        tools:
          - type: bash
            timeout: 30.0
            output_limit: 10000
            requires_confirmation: true
            environment:
              type: mock
              deterministic_ids: true
        ```
    """

    model_config = ConfigDict(title="Bash Tool")

    type: Literal["bash"] = Field("bash", init=False)
    """Bash command execution tool."""

    timeout: float | None = Field(
        default=None,
        examples=[30.0, 60.0, 120.0],
        title="Command timeout",
    )
    """Command timeout in seconds. None means no timeout."""

    output_limit: int | None = Field(
        default=None,
        examples=[10000, 50000, 100000],
        title="Output limit",
    )
    """Maximum bytes of output to return."""

    environment: ExecutionEnvironmentConfig | None = Field(
        default=None,
        title="Execution environment",
    )
    """Execution environment for command execution. Falls back to agent's env if not set."""

    def get_tool(self) -> Tool:
        """Convert config to BashTool instance."""
        from agentpool.tool_impls.bash import create_bash_tool

        env = self.environment.get_provider() if self.environment else None
        return create_bash_tool(
            env=env,
            timeout=self.timeout,
            output_limit=self.output_limit,
            name=self.name or "bash",
            description=self.description or "Execute a shell command and return the output.",
            requires_confirmation=self.requires_confirmation,
        )


class AgentCliToolConfig(BaseToolConfig):
    """Configuration for agent CLI tool.

    Example:
        ```yaml
        tools:
          - type: agent_cli
        ```
    """

    model_config = ConfigDict(title="Agent CLI Tool")

    type: Literal["agent_cli"] = Field("agent_cli", init=False)
    """Agent CLI tool."""

    def get_tool(self) -> Tool:
        """Convert config to AgentCliTool instance."""
        from agentpool.tool_impls.agent_cli import create_agent_cli_tool

        return create_agent_cli_tool(
            name=self.name or "run_agent_cli_command",
            description=self.description or "Execute an internal agent management command.",
            requires_confirmation=self.requires_confirmation,
        )


class QuestionToolConfig(BaseToolConfig):
    """Configuration for user interaction tool.

    Example:
        ```yaml
        tools:
          - type: question
        ```
    """

    model_config = ConfigDict(title="Ask User Tool")

    type: Literal["question"] = Field("question", init=False)
    """User interaction tool."""

    def get_tool(self) -> Tool:
        """Convert config to QuestionTool instance."""
        from agentpool.tool_impls.question import create_question_tool

        return create_question_tool(
            name=self.name or "question",
            description=self.description or "Ask the user a clarifying question.",
            requires_confirmation=self.requires_confirmation,
        )


class ExecuteCodeToolConfig(BaseToolConfig):
    """Configuration for Python code execution tool.

    Example:
        ```yaml
        tools:
          - type: execute_code
            requires_confirmation: true
            environment:
              type: mock
              deterministic_ids: true
        ```
    """

    model_config = ConfigDict(title="Execute Code Tool")

    type: Literal["execute_code"] = Field("execute_code", init=False)
    """Python code execution tool."""

    environment: ExecutionEnvironmentConfig | None = Field(
        default=None,
        title="Execution environment",
    )
    """Execution environment for code execution. Falls back to agent's env if not set."""

    def get_tool(self) -> Tool:
        """Convert config to ExecuteCodeTool instance."""
        from agentpool.tool_impls.execute_code import create_execute_code_tool

        env = self.environment.get_provider() if self.environment else None
        return create_execute_code_tool(
            env=env,
            name=self.name or "execute_code",
            description=self.description or "Execute Python code and return the result.",
            requires_confirmation=self.requires_confirmation,
        )


class ReadToolConfig(BaseToolConfig):
    """Configuration for file reading tool.

    Example:
        ```yaml
        tools:
          - type: read
            max_file_size_kb: 128
            max_image_size: 1500
            large_file_tokens: 10000
            conversion:
              default_provider: markitdown
            environment:
              type: local
        ```
    """

    model_config = ConfigDict(title="Read Tool")

    type: Literal["read"] = Field("read", init=False)
    """File reading tool."""

    environment: ExecutionEnvironmentConfig | None = Field(
        default=None,
        title="Execution environment",
    )
    """Execution environment for filesystem access. Falls back to agent's env if not set."""

    cwd: str | None = Field(
        default=None,
        title="Working directory",
    )
    """Working directory for resolving relative paths."""

    max_file_size_kb: int = Field(
        default=64,
        examples=[64, 128, 256],
        title="Max file size",
    )
    """Maximum file size in KB for read operations."""

    max_image_size: int | None = Field(
        default=2000,
        examples=[1500, 2000, 2500],
        title="Max image dimensions",
    )
    """Max width/height for images in pixels. Images are auto-resized if larger."""

    max_image_bytes: int | None = Field(
        default=None,
        title="Max image file size",
    )
    """Max file size for images in bytes. Images are compressed if larger."""

    large_file_tokens: int = Field(
        default=12_000,
        examples=[10_000, 12_000, 15_000],
        title="Large file threshold",
    )
    """Token threshold for switching to structure map for large files."""

    map_max_tokens: int = Field(
        default=2048,
        examples=[1024, 2048, 4096],
        title="Structure map max tokens",
    )
    """Maximum tokens for structure map output."""

    conversion: ConversionConfig | None = Field(
        default=None,
        title="Conversion config",
    )
    """Optional conversion config for binary files. If set, converts supported files to markdown."""

    def get_tool(self) -> Tool:
        """Convert config to ReadTool instance."""
        from agentpool.tool_impls.read import create_read_tool

        env = self.environment.get_provider() if self.environment else None

        # Create converter if conversion config is provided
        converter = None
        if self.conversion is not None:
            try:
                from agentpool.prompts.conversion_manager import ConversionManager

                converter = ConversionManager(self.conversion)
            except Exception:  # noqa: BLE001
                # ConversionManager not available, continue without it
                pass

        return create_read_tool(
            env=env,
            converter=converter,
            cwd=self.cwd,
            max_file_size_kb=self.max_file_size_kb,
            max_image_size=self.max_image_size,
            max_image_bytes=self.max_image_bytes,
            large_file_tokens=self.large_file_tokens,
            map_max_tokens=self.map_max_tokens,
            name=self.name or "read",
            description=self.description or "Read file contents with automatic format detection.",
            requires_confirmation=self.requires_confirmation,
        )


class ListDirectoryToolConfig(BaseToolConfig):
    """Configuration for directory listing tool.

    Example:
        ```yaml
        tools:
          - type: list_directory
            max_items: 1000
            environment:
              type: local
        ```
    """

    model_config = ConfigDict(title="List Directory Tool")

    type: Literal["list_directory"] = Field("list_directory", init=False)
    """Directory listing tool."""

    environment: ExecutionEnvironmentConfig | None = Field(
        default=None,
        title="Execution environment",
    )
    """Execution environment for filesystem access. Falls back to agent's env if not set."""

    cwd: str | None = Field(
        default=None,
        title="Working directory",
    )
    """Working directory for resolving relative paths."""

    max_items: int = Field(
        default=500,
        examples=[500, 1000, 2000],
        title="Max items",
    )
    """Maximum number of items to return (safety limit)."""

    def get_tool(self) -> Tool:
        """Convert config to ListDirectoryTool instance."""
        from agentpool.tool_impls.list_directory import create_list_directory_tool

        env = self.environment.get_provider() if self.environment else None
        return create_list_directory_tool(
            env=env,
            cwd=self.cwd,
            max_items=self.max_items,
            name=self.name or "list_directory",
            description=self.description or "List files in a directory with filtering support.",
            requires_confirmation=self.requires_confirmation,
        )


class GrepToolConfig(BaseToolConfig):
    """Configuration for grep search tool.

    Example:
        ```yaml
        tools:
          - type: grep
            max_output_kb: 128
            use_subprocess_grep: true
            environment:
              type: local
        ```
    """

    model_config = ConfigDict(title="Grep Tool")

    type: Literal["grep"] = Field("grep", init=False)
    """Grep search tool."""

    environment: ExecutionEnvironmentConfig | None = Field(
        default=None,
        title="Execution environment",
    )
    """Execution environment for filesystem access. Falls back to agent's env if not set."""

    cwd: str | None = Field(
        default=None,
        title="Working directory",
    )
    """Working directory for resolving relative paths."""

    max_output_kb: int = Field(
        default=64,
        examples=[64, 128, 256],
        title="Max output size",
    )
    """Maximum output size in KB."""

    use_subprocess_grep: bool = Field(
        default=True,
        title="Use subprocess grep",
    )
    """Use ripgrep/grep subprocess if available (faster for large codebases)."""

    def get_tool(self) -> Tool:
        """Convert config to GrepTool instance."""
        from agentpool.tool_impls.grep import create_grep_tool

        env = self.environment.get_provider() if self.environment else None
        return create_grep_tool(
            env=env,
            cwd=self.cwd,
            max_output_kb=self.max_output_kb,
            use_subprocess_grep=self.use_subprocess_grep,
            name=self.name or "grep",
            description=self.description or "Search file contents for patterns.",
            requires_confirmation=self.requires_confirmation,
        )


class DeletePathToolConfig(BaseToolConfig):
    """Configuration for delete path tool.

    Example:
        ```yaml
        tools:
          - type: delete_path
            requires_confirmation: true
            environment:
              type: local
        ```
    """

    model_config = ConfigDict(title="Delete Path Tool")

    type: Literal["delete_path"] = Field("delete_path", init=False)
    """Delete path tool."""

    environment: ExecutionEnvironmentConfig | None = Field(
        default=None,
        title="Execution environment",
    )
    """Execution environment for filesystem access. Falls back to agent's env if not set."""

    cwd: str | None = Field(
        default=None,
        title="Working directory",
    )
    """Working directory for resolving relative paths."""

    def get_tool(self) -> Tool:
        """Convert config to DeletePathTool instance."""
        from agentpool.tool_impls.delete_path import create_delete_path_tool

        env = self.environment.get_provider() if self.environment else None
        return create_delete_path_tool(
            env=env,
            cwd=self.cwd,
            name=self.name or "delete_path",
            description=self.description or "Delete a file or directory.",
            requires_confirmation=self.requires_confirmation,
        )


class DownloadFileToolConfig(BaseToolConfig):
    """Configuration for file download tool.

    Example:
        ```yaml
        tools:
          - type: download_file
            chunk_size: 16384
            timeout: 60.0
            environment:
              type: local
        ```
    """

    model_config = ConfigDict(title="Download File Tool")

    type: Literal["download_file"] = Field("download_file", init=False)
    """File download tool."""

    environment: ExecutionEnvironmentConfig | None = Field(
        default=None,
        title="Execution environment",
    )
    """Execution environment for filesystem access. Falls back to agent's env if not set."""

    cwd: str | None = Field(
        default=None,
        title="Working directory",
    )
    """Working directory for resolving relative paths."""

    chunk_size: int = Field(
        default=8192,
        examples=[8192, 16384, 32768],
        title="Chunk size",
    )
    """Size of chunks to download in bytes."""

    timeout: float = Field(
        default=30.0,
        examples=[30.0, 60.0, 120.0],
        title="Request timeout",
    )
    """Request timeout in seconds."""

    def get_tool(self) -> Tool:
        """Convert config to DownloadFileTool instance."""
        from agentpool.tool_impls.download_file import create_download_file_tool

        env = self.environment.get_provider() if self.environment else None
        return create_download_file_tool(
            env=env,
            cwd=self.cwd,
            chunk_size=self.chunk_size,
            timeout=self.timeout,
            name=self.name or "download_file",
            description=self.description or "Download a file from a URL.",
            requires_confirmation=self.requires_confirmation,
        )


# Union type for agentpool tool configs
AgentpoolToolConfig = (
    AgentCliToolConfig
    | QuestionToolConfig
    | BashToolConfig
    | DeletePathToolConfig
    | DownloadFileToolConfig
    | ExecuteCodeToolConfig
    | GrepToolConfig
    | ListDirectoryToolConfig
    | ReadToolConfig
)
