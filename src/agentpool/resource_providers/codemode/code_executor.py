"""Code execution provider with secure tool isolation via FastAPI server."""

import asyncio
import contextlib
from dataclasses import dataclass
from datetime import UTC, datetime
import secrets
from typing import TYPE_CHECKING, Any, Self

import anyenv
import anyio

from agentpool.log import get_logger
from agentpool.utils.network import BearerAuth, wait_until_started


if TYPE_CHECKING:
    from collections.abc import Sequence
    import socket
    from types import TracebackType

    from exxec.base import ExecutionEnvironment
    from exxec.models import ServerInfo
    from exxec_config import ExecutionEnvironmentConfig
    from fastapi import FastAPI
    from fsspec import AbstractFileSystem
    from schemez import ToolsetCodeGenerator
    import uvicorn

    from agentpool.tools.base import Tool


logger = get_logger(__name__)

TOOLS_PREFIX = "/tools"
_CLIENT_CALL = "httpx2.AsyncClient()"
_AUTH_CLIENT = '''
def _tool_server_client():
    """HTTP client for the tool server, with its bearer token."""
    return httpx2.AsyncClient(headers={{"Authorization": {auth!r}}})
'''


@dataclass
class RemoteCodeExecutor:
    """Provides secure code execution with tool access via FastAPI server.

    Architecture:
    - FastAPI server runs in HOST environment with tool routes
    - User code runs in SANDBOX environment (Docker, E2B, etc.)
    - Sandbox makes HTTP calls to server for tool execution
    """

    toolset_generator: ToolsetCodeGenerator
    """Code generator for tools."""

    execution_env: ExecutionEnvironment
    """Execution environment for running code."""

    server_handler: ToolServerLifecycleHandler | None = None
    """The tool server the code calls (its client code is prepended to the code)."""

    def __post_init__(self) -> None:
        """Initialize filesystem for script history after dataclass init."""
        # Fallback filesystem if none provided to execute_code
        from fsspec.implementations.memory import MemoryFileSystem

        self._fallback_fs = MemoryFileSystem()

    @classmethod
    def from_tools(
        cls,
        tools: Sequence[Tool],
        env_config: ExecutionEnvironmentConfig,
        server_host: str = "localhost",
        server_port: int = 8000,
        include_docstrings: bool = True,
    ) -> RemoteCodeExecutor:
        """Create provider from tools and environment configuration.

        Args:
            tools: Tools to make available for code execution
            env_config: Execution environment configuration
            server_host: Host for FastAPI server
            server_port: Port for FastAPI server
            include_docstrings: Include function docstrings in documentation

        Returns:
            RemoteCodeExecutor instance
        """
        from agentpool.resource_providers.codemode.helpers import tools_to_codegen

        toolset_gen = tools_to_codegen(tools, include_docstrings)
        server_handler = ToolServerLifecycleHandler(toolset_gen, server_host, server_port)
        execution_env = env_config.get_provider(server_handler)
        return cls(toolset_gen, execution_env, server_handler)

    def get_tool_description(self) -> str:
        """Get comprehensive description of available tools."""
        return self.toolset_generator.generate_tool_description()

    async def execute_code(
        self,
        code: str,
        title: str,
        internal_fs: AbstractFileSystem | None = None,
    ) -> Any:
        """Execute code with tools available via HTTP API.

        Args:
            code: Python code to execute
            title: Short descriptive title for this script (3-4 words)
            internal_fs: Filesystem to write script history to (uses fallback if None)

        Returns:
            Execution result from the environment
        """
        from upathtools.async_ops import to_async_fs

        fs = internal_fs or self._fallback_fs
        fs = to_async_fs(fs)
        start_time = datetime.now(UTC)
        exit_code = 0
        error_msg: str | None = None
        exec_result = None
        result_str = ""

        full_code = code
        if self.server_handler is not None and self.server_handler.server is not None:
            full_code = f"{self.server_handler.client_code()}\n\n{code}"
        try:
            exec_result = await self.execution_env.execute(full_code)
            result_str = str(exec_result)
            # Check if execution failed
            if exec_result.exit_code is not None and exec_result.exit_code != 0:
                exit_code = exec_result.exit_code
                error_msg = exec_result.error
        except Exception as e:  # noqa: BLE001
            exit_code = 1
            error_msg = str(e)
            result_str = f"Error executing code: {error_msg}"
        finally:
            # Save to filesystem (agent's internal_fs or fallback)
            end_time = datetime.now(UTC)
            duration = (end_time - start_time).total_seconds()
            timestamp = start_time.strftime("%Y%m%d_%H%M%S")

            # Write script file
            script_path = f"remote_code/scripts/{timestamp}_{title}.py"
            await fs._pipe_file(script_path, code.encode("utf-8"))

            # Write metadata file
            metadata = {
                "title": title,
                "timestamp": start_time.isoformat(),
                "exit_code": exit_code,
                "duration": duration,
                "result": result_str,
                "error": error_msg,
            }
            metadata_path = f"remote_code/scripts/{timestamp}_{title}.json"
            dumped = anyenv.dump_json(metadata, indent=True)
            await fs._pipe_file(metadata_path, dumped.encode("utf-8"))

        return exec_result

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


class ToolServerLifecycleHandler:
    """Manages FastAPI server lifecycle for tool access.

    The server listens on localhost, where any local process could reach it: it serves
    only requests carrying its random bearer token (:attr:`token`), which the generated
    client code (:meth:`client_code`) sends.
    """

    def __init__(
        self,
        toolset_generator: ToolsetCodeGenerator,
        host: str = "localhost",
        port: int = 8000,
    ) -> None:
        self.toolset_generator = toolset_generator
        self.host = host
        self.port = port  # Will be set when socket is created
        self.app: FastAPI | None = None
        self.server: uvicorn.Server | None = None
        self._server_task: asyncio.Task[None] | None = None
        self._socket: socket.socket | None = None
        self.token = secrets.token_urlsafe(32)
        """Bearer token a caller must send."""

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def client_code(self) -> str:
        """Python code defining the tool functions, calling this server with the token."""
        code = self.toolset_generator.generate_structured_code(self.url, TOOLS_PREFIX)
        methods = code.clean_methods
        if _CLIENT_CALL not in methods and self.toolset_generator.generators:
            msg = f"Generated tool client code has no {_CLIENT_CALL} to authenticate"
            raise RuntimeError(msg)
        methods = methods.replace(_CLIENT_CALL, "_tool_server_client()")
        auth = _AUTH_CLIENT.format(auth=f"Bearer {self.token}")
        return f"{code.imports}\n{auth}\n{methods}"

    async def __aenter__(self) -> ServerInfo:
        """Start FastAPI server with tool routes."""
        from exxec.models import ServerInfo
        from fastapi import FastAPI
        import uvicorn

        from agentpool.utils.network import _create_socket

        if self.server is not None:
            return ServerInfo(url=self.url, port=self.port)
        # Create socket and get actual port
        self._socket, self.port = _create_socket(self.port)
        # Create FastAPI app
        self.app = FastAPI(title="Tool Server", description="Generated tool endpoints")
        # Add tool routes
        self.toolset_generator.add_all_routes(self.app, TOOLS_PREFIX)
        config = uvicorn.Config(
            BearerAuth(self.app, self.token),
            log_level="error",  # Reduce log noise
            access_log=False,
            host=self.host,
            port=self.port,
            ws="websockets-sansio",
        )
        self.server = uvicorn.Server(config)
        # Start server in background with our socket
        self._server_task = asyncio.create_task(self.server.serve([self._socket]))
        try:
            await wait_until_started(self.server, self._server_task)
        except BaseException:
            await self.__aexit__(None, None, None)
            raise
        logger.info("Started tool server", host=self.host, port=self.port)
        return ServerInfo(url=self.url, port=self.port)

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Stop FastAPI server."""
        # Stop server gracefully
        if self.server:
            self.server.should_exit = True
            self.server.force_exit = True

        # Cancel server task
        if self._server_task and not self._server_task.done():
            self._server_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.wait_for(self._server_task, timeout=1.0)

        # Close socket
        if self._socket:
            with contextlib.suppress(Exception):
                self._socket.close()

        # Reset state
        self.server = None
        self._server_task = None
        self._socket = None
        self.app = None


if __name__ == "__main__":
    from exxec_config import LocalExecutionEnvironmentConfig

    from agentpool.tools.base import FunctionTool

    def add_numbers(x: int, y: int) -> int:
        """Add two numbers."""
        return x + y

    async def main() -> None:
        tools = [FunctionTool.from_callable(add_numbers)]
        config = LocalExecutionEnvironmentConfig()
        provider = RemoteCodeExecutor.from_tools(tools, config, server_port=9876)
        async with provider:
            result = await provider.execute_code(
                "_result = await add_numbers(5, 3)", title="test_addition"
            )
            print(f"Result: {result.result}")

    anyio.run(main)
