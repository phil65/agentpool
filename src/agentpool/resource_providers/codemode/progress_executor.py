"""Statement-by-statement execution with automatic progress tracking."""

import ast
from dataclasses import dataclass
import inspect
import time
from typing import TYPE_CHECKING, Any

import anyio


if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable


@dataclass
class ProgressInfo:
    """Information about code execution progress."""

    current: float
    """Current progress value."""

    total: float
    """Total progress value."""

    message: str
    """Progress message describing current operation."""


def count_statements(code: str) -> int:
    """Count total statements in the code."""
    try:
        tree = ast.parse(code)
        return len(tree.body)
    except SyntaxError:
        return 0


class ProgressTrackingExecutor:
    """Execute Python code statement-by-statement with automatic progress reporting."""

    def __init__(
        self,
        progress_callback: Callable[[ProgressInfo], Awaitable[Any]] | None = None,
        step_delay: float = 0.01,
    ) -> None:
        """Initialize executor.

        Args:
            progress_callback: Optional callback for progress reporting
            step_delay: Delay between statement executions (for async behavior)
        """
        self.globals: dict[str, Any] = {"__builtins__": __builtins__}
        self.locals: dict[str, Any] = {}
        self.progress_callback = progress_callback
        self.step_delay = step_delay
        self.total_statements = 0
        self.current_statement = 0
        self.start_time: float | None = None

    def update_namespace(self, namespace: dict[str, Any]) -> None:
        """Update the execution namespace with additional globals."""
        self.globals.update(namespace)

    async def execute_with_progress(self, code: str) -> Any:
        """Execute code with automatic progress reporting.

        Args:
            code: Python source code to execute

        Returns:
            Result of code execution
        """
        self.total_statements = count_statements(code)
        self.current_statement = 0
        self.start_time = time.time()

        if self.total_statements == 0:
            return "No statements to execute"

        try:
            tree = ast.parse(code)

            for i, node in enumerate(tree.body):
                self.current_statement = i + 1

                # Report progress if callback available
                if self.progress_callback:
                    node_type = type(node).__name__
                    message = f"Executing {node_type.lower()} statement"

                    progress_info = ProgressInfo(
                        current=float(self.current_statement),
                        total=float(self.total_statements),
                        message=message,
                    )
                    await self.progress_callback(progress_info)

                # Execute the statement
                compiled = compile(
                    ast.Module(body=[node], type_ignores=[]), f"<statement_{i}>", "exec"
                )
                exec(compiled, self.globals, self.locals)

                # Small delay for async behavior
                if self.step_delay > 0:
                    await anyio.sleep(self.step_delay)

            # Try to get result from main() function or return success
            if "main" in self.locals and callable(self.locals["main"]):
                result = self.locals["main"]()
                if inspect.iscoroutine(result):
                    result = await result
                return result
        except Exception as e:
            # Report error with current progress
            if self.progress_callback:
                progress_info = ProgressInfo(
                    current=float(self.current_statement),
                    total=float(self.total_statements),
                    message=f"Error: {str(e)[:50]}...",
                )
                await self.progress_callback(progress_info)
            raise
        else:
            return "Code executed successfully"

    async def execute_statements(self, code: str) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        """Execute code statement by statement, yielding each executed statement.

        This method is useful for external progress tracking or debugging.

        Yields:
            (statement_source, metadata) tuples
        """
        self.total_statements = count_statements(code)
        self.current_statement = 0
        self.start_time = time.time()

        if self.total_statements == 0:
            return

        tree = ast.parse(code)
        for i, node in enumerate(tree.body):
            self.current_statement = i + 1
            statement_code = ast.unparse(node)
            # Create metadata
            elapsed_time = time.time() - (self.start_time or time.time())
            metadata = {
                "statement_index": i,
                "total_statements": self.total_statements,
                "node_type": type(node).__name__,
                "elapsed_seconds": elapsed_time,
                "locals_before": dict(self.locals),
            }

            # Execute the statement
            try:
                compiled = compile(
                    ast.Module(body=[node], type_ignores=[]), f"<statement_{i}>", "exec"
                )
                exec(compiled, self.globals, self.locals)
                metadata["status"] = "success"
            except Exception as e:  # noqa: BLE001
                metadata["status"] = "error"
                metadata["error"] = str(e)

            metadata["locals_after"] = dict(self.locals)
            yield statement_code, metadata

            if self.step_delay > 0:
                await anyio.sleep(self.step_delay)


if __name__ == "__main__":
    from pydantic_ai import RunContext  # noqa: TC002

    from agentpool import log
    from agentpool.agents import AgentContext  # noqa: TC001

    log.configure_logging()
    code = """
x = 10
y = 20
z = x + y
print(f"Result: {z}")
for i in range(3):
    print(f"Loop: {i}")
"""

    async def run_me(run_context: RunContext, ctx: AgentContext) -> str:
        """Test function using the unified progress system."""
        executor = ProgressTrackingExecutor(step_delay=0.5)
        results = []
        async for x in executor.execute_statements(code):
            await ctx.events.custom(x)
            results.append(x[0])

        return f"Code executed successfully. Executed {len(results)} statements."

    async def main() -> None:
        from agentpool import Agent, AgentPool

        async with AgentPool() as pool:
            agent = Agent("test-agent", model="openai:gpt-5-nano", tools=[run_me])
            await pool.add_agent(agent)
            print("🚀 Testing unified progress system...")
            async for event in agent.run_stream("Run run_me and show progress."):
                print(f"Event: {event}")

    anyio.run(main)
