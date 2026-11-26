"""Meta-resource provider that exposes tools through Python execution."""

from datetime import UTC, datetime
from functools import partial
import inspect
from typing import TYPE_CHECKING, Any

import anyenv
from schemez.code_generation.namespace_callable import NamespaceCallable

from agentpool.agents.context import AgentContext
from agentpool.resource_providers import AggregatingResourceProvider
from agentpool.resource_providers.codemode.default_prompt import USAGE
from agentpool.resource_providers.codemode.helpers import (
    tools_to_codegen,
    validate_code,
)
from agentpool.utils.inspection import get_argument_key
from agentpool_toolsets.fsspec_toolset.toolset import FSSpecTools


if TYPE_CHECKING:
    from collections.abc import Sequence

    from schemez import ToolsetCodeGenerator

    from agentpool.resource_providers import ResourceProvider
    from agentpool.tools.base import Tool


class CodeModeResourceProvider(AggregatingResourceProvider):
    """Provider that wraps tools into a single Python execution environment."""

    def __init__(
        self,
        providers: list[ResourceProvider],
        name: str = "meta_tools",
        include_docstrings: bool = True,
        usage_notes: str = USAGE,
    ) -> None:
        """Initialize meta provider.

        Args:
            providers: Providers whose tools to wrap
            name: Provider name
            include_docstrings: Include function docstrings in documentation
            usage_notes: Usage notes for the codemode tool
        """
        super().__init__(providers=providers, name=name)
        self.include_docstrings = include_docstrings
        self._cached_tool: Tool | None = None
        self.usage_notes = usage_notes

    async def get_tools(self) -> Sequence[Tool]:
        """Return single meta-tool for Python execution with available tools."""
        # Always generate fresh toolset to reflect current tools
        toolset_generator = await self._get_code_generator()
        desc = toolset_generator.generate_tool_description()
        desc += self.usage_notes

        if self._cached_tool is None:
            # Create a closure that captures self but isn't a bound method
            async def execute_tool(ctx: AgentContext, python_code: str, title: str) -> Any:
                """These docstings are overriden by description_override."""
                return await self.execute(ctx, python_code, title)

            self._cached_tool = self.create_tool(execute_tool, description_override=desc)
        else:
            # Update the description on existing cached tool
            self._cached_tool.description = desc

        return [self._cached_tool]

    async def execute(self, ctx: AgentContext, python_code: str, title: str) -> Any:  # noqa: D417
        """Execute Python code with all wrapped tools available as functions.

        Args:
            python_code: Python code to execute
            title: Short descriptive title for this script (3-4 words)

        Returns:
            Result of the last expression or explicit return value
        """
        toolset_generator = await self._get_code_generator()
        namespace = toolset_generator.generate_execution_namespace()

        # Inject the AgentContext into every tool taking one (found by annotation type,
        # whatever the parameter is called, like the native agent's tool wrapping does).
        for value in namespace.values():
            if isinstance(value, NamespaceCallable) and (
                key := get_argument_key(value.callable, AgentContext)
            ):
                value.callable = partial(value.callable, **{key: ctx})

        # async def report_progress(current: int, total: int, message: str = ""):
        #     """Report progress during code execution."""
        #     await ctx.report_progress(current, total, message)

        # namespace["report_progress"] = NamespaceCallable(report_progress)

        validate_code(python_code)
        start_time = datetime.now(UTC)
        exit_code = 0
        error_msg = None
        result_value = None

        try:
            exec(python_code, namespace)
            result_value = await namespace["main"]()
            # Handle edge cases with coroutines and return values
            if inspect.iscoroutine(result_value):
                result_value = await result_value

            if not result_value:  # in order to not confuse the model, return a success message.
                result_value = "Code executed successfully"
        except Exception as e:  # noqa: BLE001
            exit_code = 1
            error_msg = f"{e!s}"
            result_value = f"Error executing code: {error_msg}"
        finally:
            # Save to agent's internal filesystem
            end_time = datetime.now(UTC)
            duration = (end_time - start_time).total_seconds()
            timestamp = start_time.strftime("%Y%m%d_%H%M%S")

            # Write script file
            script_path = f"codemode/scripts/{timestamp}_{title}.py"
            ctx.internal_fs.pipe(script_path, python_code.encode("utf-8"))

            # Write metadata file
            metadata = {
                "title": title,
                "timestamp": start_time.isoformat(),
                "exit_code": exit_code,
                "duration": duration,
                "result": str(result_value),
                "error": error_msg,
            }
            metadata_path = f"codemode/scripts/{timestamp}_{title}.json"
            ctx.internal_fs.pipe(metadata_path, anyenv.dump_json(metadata, indent=True).encode())

        return result_value

    def invalidate_cache(self) -> None:
        """Invalidate cached tool when providers change."""
        self._cached_tool = None
        # Note: We no longer cache the toolset generator, so no need to clear it

    async def get_wrapped_tools(self) -> Sequence[Tool]:
        """Get the tools of the wrapped providers (not the meta tool)."""
        return await super().get_tools()

    async def _get_code_generator(self) -> ToolsetCodeGenerator:
        """Get fresh toolset generator with current tools."""
        tools = await self.get_wrapped_tools()
        return tools_to_codegen(tools=tools, include_docstrings=self.include_docstrings)


if __name__ == "__main__":
    import anyio

    from agentpool import Agent
    from agentpool.delegation.pool import AgentPool

    static_provider = FSSpecTools()
    provider = CodeModeResourceProvider([static_provider])

    async def main() -> None:
        print("Available tools:")
        for tool in await provider.get_tools():
            print(f"- {tool.name}: {tool.description[:100]}...")

        async with AgentPool() as pool:
            agent: Agent[None, str] = Agent(
                model="openai:gpt-5-nano", event_handlers=["simple"], retries=1
            )
            pool.register("test_agent", agent)
            async with agent:
                agent.tools.add_provider(provider)
                prompt = (
                    "Call list_directory with path='.'. "
                    "Write: async def main(): "
                    "result = await list_directory(path='.'); "
                    "return result"
                )
                result = await agent.run(prompt)
                print(f"Result: {result}")

    anyio.run(main)
