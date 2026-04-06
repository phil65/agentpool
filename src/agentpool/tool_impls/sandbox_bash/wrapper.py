"""Pythonic wrappers around bashkit's native types.

Provides ``SandboxBash`` (wraps ``bashkit.Bash``) and ``SandboxExecResult``
(wraps ``bashkit.ExecResult``) with richer APIs, type safety, and integration
points for agentpool infrastructure.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, Self

from bashkit import Bash, ScriptedTool


if TYPE_CHECKING:
    from collections.abc import Callable

    from bashkit import ExecResult


class ExternalHandler(Protocol):
    """Protocol for external function handlers called from embedded Python."""

    async def __call__(
        self,
        fn_name: str,
        args: list[Any],
        kwargs: dict[str, Any],
    ) -> Any: ...


__all__ = [
    "ExternalHandler",
    "SandboxBash",
    "SandboxExecResult",
    "SandboxScriptedTool",
]


@dataclass(frozen=True, slots=True)
class SandboxExecResult:
    """Immutable, richly-typed result from a sandboxed bash execution.

    Wraps bashkit's ``ExecResult`` with additional convenience methods.
    """

    stdout: str
    """Standard output from the command."""

    stderr: str
    """Standard error from the command."""

    exit_code: int
    """Process exit code (0 = success)."""

    error: str | None
    """Error message if execution failed at the interpreter level."""

    @property
    def success(self) -> bool:
        """Whether the command completed successfully."""
        return self.exit_code == 0

    @property
    def output(self) -> str:
        """Combined stdout and stderr, suitable for LLM consumption."""
        parts: list[str] = []
        if self.stdout:
            parts.append(self.stdout)
        if self.error:
            parts.append(f"Error: {self.error}")
        if self.stderr:
            parts.append(f"STDERR: {self.stderr}")
        if self.exit_code != 0:
            parts.append(f"[Exit code: {self.exit_code}]")
        return "\n".join(parts) if parts else "[No output]"

    @classmethod
    def from_native(cls, result: ExecResult) -> SandboxExecResult:
        """Create from a native bashkit ExecResult."""
        return cls(
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.exit_code,
            error=result.error,
        )

    def raise_on_error(self) -> Self:
        """Return self if successful, raise ``SandboxExecutionError`` otherwise."""
        if not self.success:
            msg = self.error or self.stderr or f"Command failed with exit code {self.exit_code}"
            raise SandboxExecutionError(msg, result=self)
        return self


class SandboxExecutionError(RuntimeError):
    """Raised when a sandboxed command fails and ``raise_on_error()`` is used."""

    def __init__(self, message: str, *, result: SandboxExecResult) -> None:
        super().__init__(message)
        self.result = result


class SandboxBash:
    """Pythonic wrapper around bashkit's ``Bash`` interpreter.

    Provides a stateful, sandboxed bash environment with a virtual filesystem.
    Files created in one ``execute()`` call persist for subsequent calls.

    Example::

        async with SandboxBash(username="agent") as bash:
            result = await bash.execute("echo hello")
            print(result.stdout)  # hello

            await bash.execute("echo data > /tmp/file.txt")
            content = await bash.read_file("/tmp/file.txt")
            print(content)  # data
    """

    def __init__(
        self,
        *,
        username: str | None = None,
        hostname: str | None = None,
        max_commands: int | None = None,
        max_loop_iterations: int | None = None,
        python: bool = False,
        external_functions: list[str] | None = None,
        external_handler: ExternalHandler | None = None,
    ) -> None:
        """Initialize a sandboxed bash interpreter.

        Args:
            username: Custom username for the virtual environment.
            hostname: Custom hostname for the virtual environment.
            max_commands: Maximum total commands allowed.
            max_loop_iterations: Maximum loop iterations allowed.
            python: Enable embedded Python interpreter (Monty).
            external_functions: Function names callable from embedded Python.
            external_handler: Async handler for external function calls from Python.
        """
        self._bash = Bash(
            username=username,
            hostname=hostname,
            max_commands=max_commands,
            max_loop_iterations=max_loop_iterations,
            python=python,
            external_functions=external_functions,
            external_handler=external_handler,
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        self.reset()

    async def execute(self, commands: str) -> SandboxExecResult:
        """Execute bash commands asynchronously.

        Args:
            commands: Bash commands to execute.

        Returns:
            Structured execution result.
        """
        native_result = await self._bash.execute(commands)
        return SandboxExecResult.from_native(native_result)

    def execute_sync(self, commands: str) -> SandboxExecResult:
        """Execute bash commands synchronously.

        Args:
            commands: Bash commands to execute.

        Returns:
            Structured execution result.
        """
        native_result = self._bash.execute_sync(commands)
        return SandboxExecResult.from_native(native_result)

    def cancel(self) -> None:
        """Cancel any currently running execution."""
        self._bash.cancel()

    def reset(self) -> None:
        """Reset interpreter state (clears filesystem, variables, etc.)."""
        self._bash.reset()

    async def run(self, commands: str) -> SandboxExecResult:
        """Execute commands, raising on failure.

        Convenience method that calls ``execute()`` then ``raise_on_error()``.

        Args:
            commands: Bash commands to execute.

        Returns:
            Result (guaranteed successful).

        Raises:
            SandboxExecutionError: If the command fails.
        """
        result = await self.execute(commands)
        return result.raise_on_error()

    async def read_file(self, path: str) -> str:
        """Read a file from the virtual filesystem.

        Args:
            path: Absolute path in the virtual filesystem.

        Returns:
            File contents as a string.

        Raises:
            SandboxExecutionError: If the file cannot be read.
        """
        result = await self.run(f"cat {_shell_quote(path)}")
        return result.stdout

    async def write_file(self, path: str, content: str) -> None:
        """Write content to a file in the virtual filesystem.

        Args:
            path: Absolute path in the virtual filesystem.
            content: Content to write.

        Raises:
            SandboxExecutionError: If the write fails.
        """
        import secrets

        delimiter = f"AGENTPOOL_EOF_{secrets.token_hex(8)}"
        cmd = f"cat > {_shell_quote(path)} << '{delimiter}'\n{content}\n{delimiter}"
        await self.run(cmd)

    async def file_exists(self, path: str) -> bool:
        """Check whether a file exists in the virtual filesystem.

        Args:
            path: Absolute path to check.

        Returns:
            True if the file exists.
        """
        result = await self.execute(f"test -e {_shell_quote(path)}")
        return result.success

    async def list_dir(self, path: str = ".") -> list[str]:
        """List directory contents in the virtual filesystem.

        Args:
            path: Directory path (defaults to cwd).

        Returns:
            List of filenames.

        Raises:
            SandboxExecutionError: If the directory cannot be listed.
        """
        result = await self.run(f"ls -1 {_shell_quote(path)}")
        return [line for line in result.stdout.splitlines() if line]

    async def mkdir(self, path: str, parents: bool = True) -> None:
        """Create a directory in the virtual filesystem.

        Args:
            path: Directory path to create.
            parents: If True, create parent directories as needed.

        Raises:
            SandboxExecutionError: If directory creation fails.
        """
        flag = " -p" if parents else ""
        await self.run(f"mkdir{flag} {_shell_quote(path)}")

    async def remove(self, path: str, recursive: bool = False) -> None:
        """Remove a file or directory from the virtual filesystem.

        Args:
            path: Path to remove.
            recursive: If True, remove directories recursively.

        Raises:
            SandboxExecutionError: If removal fails.
        """
        flag = " -rf" if recursive else ""
        await self.run(f"rm{flag} {_shell_quote(path)}")

    async def get_env(self, key: str) -> str | None:
        """Get an environment variable value.

        Args:
            key: Environment variable name.

        Returns:
            Variable value, or None if not set.
        """
        result = await self.execute(f'printf "%s" "${{{_shell_quote_var(key)}}}"')
        if not result.success:
            return None
        return result.stdout or None

    async def set_env(self, key: str, value: str) -> None:
        """Set an environment variable.

        Args:
            key: Environment variable name.
            value: Value to set.
        """
        await self.run(f"export {_shell_quote_var(key)}={_shell_quote(value)}")


class SandboxScriptedTool:
    """Pythonic wrapper around bashkit's ``ScriptedTool``.

    Register Python callbacks as bash builtins, then execute bash scripts
    that orchestrate all registered tools via pipes, loops, and branching.

    Example::

        tool = SandboxScriptedTool("api")
        tool.add_tool(
            "get_user",
            "Fetch user by ID",
            callback=lambda params, stdin=None: '{"name": "Alice"}',
        )
        result = await tool.execute("get_user --id 1 | jq -r '.name'")
        print(result.stdout)  # Alice
    """

    def __init__(
        self,
        name: str,
        *,
        short_description: str | None = None,
        max_commands: int | None = None,
        max_loop_iterations: int | None = None,
    ) -> None:
        """Initialize a scripted tool.

        Args:
            name: Name for this tool composition.
            short_description: Brief description of what the composed tool does.
            max_commands: Maximum total commands allowed.
            max_loop_iterations: Maximum loop iterations allowed.
        """
        self._tool = ScriptedTool(
            name,
            short_description=short_description,
            max_commands=max_commands,
            max_loop_iterations=max_loop_iterations,
        )

    @property
    def name(self) -> str:
        """Tool name."""
        return self._tool.name

    @property
    def tool_count(self) -> int:
        """Number of registered sub-tools."""
        return self._tool.tool_count()

    def add_tool(
        self,
        name: str,
        description: str,
        callback: Callable[[dict[str, Any], str | None], str],
        *,
        schema: dict[str, Any] | None = None,
    ) -> Self:
        """Register a Python callback as a bash builtin.

        Args:
            name: Command name in bash.
            description: Human-readable description for LLM tool-use.
            callback: Python function ``(params, stdin) -> stdout_string``.
            schema: Optional JSON Schema for the tool's parameters.

        Returns:
            Self for chaining.
        """
        self._tool.add_tool(name, description, callback=callback, schema=schema)
        return self

    def env(self, key: str, value: str) -> Self:
        """Set an environment variable for the scripted tool.

        Args:
            key: Variable name.
            value: Variable value.

        Returns:
            Self for chaining.
        """
        self._tool.env(key, value)
        return self

    async def execute(self, commands: str) -> SandboxExecResult:
        """Execute a bash script that may invoke registered tools.

        Args:
            commands: Bash script to execute.

        Returns:
            Structured execution result.
        """
        native_result = await self._tool.execute(commands)
        return SandboxExecResult.from_native(native_result)

    def execute_sync(self, commands: str) -> SandboxExecResult:
        """Execute a bash script synchronously.

        Args:
            commands: Bash script to execute.

        Returns:
            Structured execution result.
        """
        native_result = self._tool.execute_sync(commands)
        return SandboxExecResult.from_native(native_result)

    def description(self) -> str:
        """Get token-efficient tool description."""
        return self._tool.description()

    def help(self) -> str:
        """Get Markdown help document."""
        return self._tool.help()

    def system_prompt(self) -> str:
        """Get system prompt for LLM integration."""
        return self._tool.system_prompt()

    def input_schema(self) -> str:
        """Get JSON input schema."""
        return self._tool.input_schema()

    def output_schema(self) -> str:
        """Get JSON output schema."""
        return self._tool.output_schema()


def _shell_quote(value: str) -> str:
    """Quote a value for safe use in shell commands."""
    import shlex

    return shlex.quote(value)


def _shell_quote_var(name: str) -> str:
    """Validate and return an environment variable name.

    Raises:
        ValueError: If the name contains invalid characters.
    """
    import re

    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        msg = f"Invalid environment variable name: {name!r}"
        raise ValueError(msg)
    return name
