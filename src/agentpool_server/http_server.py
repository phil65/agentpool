"""Base class for HTTP-based servers.

This module provides a base class for servers that expose HTTP routes via Starlette.
"""

from abc import abstractmethod
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Self

from agentpool.log import get_logger
from agentpool_server.base import BaseServer


if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Sequence

    from starlette.applications import Starlette
    from starlette.routing import BaseRoute
    from upathtools import JoinablePathLike

    from agentpool import AgentPool


logger = get_logger(__name__)

DEFAULT_HTTP_PORT = 8000


class HTTPServer(BaseServer):
    """Base class for HTTP-based servers.

    Provides common infrastructure for servers that expose HTTP routes via Starlette.

    Subclasses must implement:
    - `get_routes()`: Return list of Starlette Route objects

    Example:
        ```python
        class MyHTTPServer(HTTPServer):
            async def get_routes(self) -> list[Route]:
                return [
                    Route("/hello", self.hello_handler, methods=["GET"]),
                    Route("/world", self.world_handler, methods=["POST"]),
                ]
        ```
    """

    def __init__(
        self,
        pool: AgentPool,
        *,
        name: str | None = None,
        host: str = "localhost",
        port: int = DEFAULT_HTTP_PORT,
        raise_exceptions: bool = False,
    ) -> None:
        """Initialize HTTP server.

        Args:
            pool: AgentPool containing available agents
            name: Optional server name (auto-generated if None)
            host: Host to bind server to
            port: Port to bind server to
            raise_exceptions: Whether to raise exceptions during server start
        """
        super().__init__(pool, name=name, raise_exceptions=raise_exceptions)
        self.host = host
        self.port = port

    @abstractmethod
    async def get_routes(self) -> Sequence[BaseRoute]:
        """Get Starlette routes for this server.

        Subclasses must implement this method to provide their HTTP routes.

        Returns:
            List of Starlette routes (``Route``, ``Mount``, ...)
        """
        ...

    @asynccontextmanager
    async def app_lifespan(self, app: Starlette) -> AsyncGenerator[None]:
        """Lifespan of the Starlette app (startup / shutdown of per-app resources).

        Starlette doesn't run the lifespans of mounted sub-applications: a server that
        mounts ASGI apps with their own lifespan enters them here.
        """
        yield

    async def create_app(self) -> Starlette:
        """Create Starlette application with this server's routes.

        Returns:
            Starlette application instance
        """
        from starlette.applications import Starlette

        routes = await self.get_routes()
        app = Starlette(debug=False, routes=list(routes), lifespan=self.app_lifespan)
        self.log.info(
            "Created HTTP app",
            route_count=len(routes),
            host=self.host,
            port=self.port,
        )
        return app

    async def _start_async(self) -> None:
        """Start the HTTP server (blocking async - runs until stopped)."""
        try:
            import uvicorn
        except ImportError as e:
            raise ImportError("Please install 'uvicorn' to use HTTPServer.") from e

        self.log.info(
            "Starting HTTP server",
            host=self.host,
            port=self.port,
        )

        app = await self.create_app()

        config = uvicorn.Config(
            app=app,
            host=self.host,
            port=self.port,
            log_level="info",
        )

        server = uvicorn.Server(config)

        try:
            await server.serve()
        except Exception:
            self.log.exception("HTTP server error")
            raise
        finally:
            self.log.info("HTTP server stopped")

    @classmethod
    def from_config(
        cls,
        config_path: JoinablePathLike,
        *,
        host: str = "localhost",
        port: int = DEFAULT_HTTP_PORT,
        raise_exceptions: bool = False,
    ) -> Self:
        """Create HTTP server from configuration file.

        Args:
            config_path: Path to agentpool YAML config file
            host: Host to bind server to
            port: Port to bind server to
            raise_exceptions: Whether to raise exceptions during server start

        Returns:
            Configured server instance with agent pool from config
        """
        from agentpool import AgentPool
        from agentpool.models.manifest import AgentsManifest

        manifest = AgentsManifest.from_file(config_path)
        pool = AgentPool(manifest=manifest)
        server = cls(pool, host=host, port=port, raise_exceptions=raise_exceptions)
        agent_names = list(server.pool.all_agents.keys())
        server.log.info("Created HTTP server from config", agent_names=agent_names)
        return server

    @property
    def base_url(self) -> str:
        """Get the base URL for the server."""
        return f"http://{self.host}:{self.port}"
