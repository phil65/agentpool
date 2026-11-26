"""Code execution provider with secure tool isolation via FastAPI server."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Self

from agentpool.log import get_logger


if TYPE_CHECKING:
    from collections.abc import Sequence
    from types import TracebackType

    from exxec.base import ExecutionEnvironment
    from exxec_config import ExecutionEnvironmentConfig
    from schemez import ToolsetCodeGenerator

    from agentpool.tools.base import Tool


logger = get_logger(__name__)


@dataclass
class RemoteMCPExecutor:
    """Provides secure code execution with tool access.

    Code Generation mode (ctx-zip style):
       - Tool functions are generated as Python files inside sandbox
       - User code imports tools directly, no HTTP server needed
       - Better for cloud sandboxes (E2B, etc.) that can't reach localhost
    """

    toolset_generator: ToolsetCodeGenerator
    """Code generator for tools."""

    execution_env: ExecutionEnvironment
    """Execution environment for running code."""

    use_code_generation: bool = False
    """If True, use code generation approach instead of HTTP server."""

    @classmethod
    def from_tools(
        cls,
        tools: Sequence[Tool],
        env_config: ExecutionEnvironmentConfig,
        include_docstrings: bool = True,
    ) -> RemoteMCPExecutor:
        """Create provider from tools and environment configuration.

        Args:
            tools: Tools to make available for code execution
            env_config: Execution environment configuration
            include_docstrings: Include function docstrings in documentation

        Returns:
            RemoteMCPExecutor instance
        """
        from agentpool.resource_providers.codemode.helpers import tools_to_codegen

        toolset_gen = tools_to_codegen(tools, include_docstrings)
        execution_env = env_config.get_provider()
        return cls(toolset_gen, execution_env)

    def get_tool_description(self) -> str:
        """Get comprehensive description of available tools."""
        # For code generation mode, provide import-based usage instructions
        tool_names = [gen.name for gen in self.toolset_generator.generators]
        desc = self.toolset_generator.generate_tool_description()
        desc += "Usage:\n"
        for name in tool_names:
            desc += f"  from tools.{name} import {name}\n"
            desc += f"  result = await {name}(...)\n"
        return desc

    async def execute_code(self, code: str) -> Any:
        """Execute code with tools available.

        Args:
            code: Python code to execute

        Returns:
            Execution result from the environment
        """
        return await self.execution_env.execute(code)

    async def __aenter__(self) -> Self:
        """Async context manager entry."""
        await self.execution_env.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Async context manager exit."""
        return await self.execution_env.__aexit__(exc_type, exc_val, exc_tb)


if __name__ == "__main__":
    import anyio
    from exxec_config import LocalExecutionEnvironmentConfig

    from agentpool.tools.base import FunctionTool

    def add_numbers(x: int, y: int) -> int:
        """Add two numbers."""
        return x + y

    def multiply_numbers(x: int, y: int) -> int:
        """Multiply two numbers."""
        return x * y

    async def main() -> None:
        print("\n=== Code Generation Approach (ctx-zip style) ===")
        tools = [
            FunctionTool.from_callable(add_numbers),
            FunctionTool.from_callable(multiply_numbers),
        ]
        config = LocalExecutionEnvironmentConfig()  # Could be E2B, etc.
        provider = RemoteMCPExecutor.from_tools(tools, config)
        async with provider:
            print("Tool description:")
            print(provider.get_tool_description())
            print("\nExecuting code with direct imports...")

            # Code that imports tools directly (no HTTP calls)
            code = """
from tools.add_numbers import add_numbers
from tools.multiply_numbers import multiply_numbers

# Direct function calls - no HTTP server needed!
result1 = add_numbers(5, 3)
result2 = multiply_numbers(4, 7)

_result = {"addition": result1, "multiplication": result2}
print(f"Addition: {result1}, Multiplication: {result2}")
"""
            result = await provider.execute_code(code)
            print(f"Result: {result.result}")

    anyio.run(main)
