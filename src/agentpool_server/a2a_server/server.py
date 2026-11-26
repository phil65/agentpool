"""A2A server implementation for agentpool pool.

This module provides a server that exposes all agents in an AgentPool
via the A2A (Agent-to-Agent) protocol, with each agent accessible at its own route.
"""

from __future__ import annotations

from contextlib import AsyncExitStack, asynccontextmanager
from functools import partial
from typing import TYPE_CHECKING, Any

from agentpool.log import get_logger
from agentpool_server.http_server import HTTPServer


if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from fasta2a import FastA2A  # type: ignore[import-untyped]
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import Response
    from starlette.routing import BaseRoute
    from starlette.types import ASGIApp, Receive, Scope, Send

    from agentpool import AgentPool


logger = get_logger(__name__)

DEFAULT_PORT = 8001


class _AtRoot:
    """Serve ``POST /{agent}`` (no trailing slash) as the mounted app's ``/``.

    The agent card advertises ``/{agent}`` as the JSON-RPC endpoint; a ``Mount`` would
    answer it with a redirect, which A2A clients don't follow for a POST.
    """

    def __init__(self, app: ASGIApp, prefix: str) -> None:
        self.app = app
        self.prefix = prefix

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        root_path = scope.get("root_path", "") + self.prefix
        await self.app({**scope, "path": "/", "root_path": root_path}, receive, send)


class A2AServer(HTTPServer):
    """A2A server for exposing pool agents on separate routes.

    Provides a unified HTTP server that exposes all agents in the pool via
    the A2A protocol. Each agent is accessible at `/{agent_name}` route.

    Example:
        ```python
        pool = AgentPool(manifest)

        server = A2AServer(pool, host="localhost", port=8001)

        async with server:
            async with server.run_context():
                # Agents accessible at:
                # POST http://localhost:8001/agent1
                # POST http://localhost:8001/agent2
                await do_other_work()
        ```
    """

    def __init__(
        self,
        pool: AgentPool,
        *,
        name: str | None = None,
        host: str = "localhost",
        port: int = DEFAULT_PORT,
        raise_exceptions: bool = False,
    ) -> None:
        """Initialize A2A server.

        Args:
            pool: AgentPool containing available agents
            name: Optional server name (auto-generated if None)
            host: Host to bind server to
            port: Port to bind server to
            raise_exceptions: Whether to raise exceptions during server start
        """
        super().__init__(pool, name=name, host=host, port=port, raise_exceptions=raise_exceptions)
        self.apps: dict[str, FastA2A] = {}
        """One FastA2A app per agent (task storage, broker and worker live for the server)."""

    def _create_agent_app(self, agent_name: str) -> FastA2A:
        from fasta2a import FastA2A  # type: ignore[import-untyped]
        from fasta2a.broker import InMemoryBroker  # type: ignore[import-untyped]
        from fasta2a.storage import InMemoryStorage  # type: ignore[import-untyped]

        from agentpool_server.a2a_server.agent_worker import AgentWorker, worker_lifespan

        agent = self.pool.all_agents[agent_name]
        storage: InMemoryStorage[Any] = InMemoryStorage()
        broker = InMemoryBroker()
        worker = AgentWorker(agent=agent, broker=broker, storage=storage)
        # The pool owns the agent's lifecycle: the app runs only its task manager + worker.
        lifespan = partial(worker_lifespan, worker=worker, agent=None)
        return FastA2A(
            storage=storage,
            broker=broker,
            name=agent.name,
            description=agent.description,
            url=self.get_agent_url(agent_name),
            lifespan=lifespan,
        )

    async def get_routes(self) -> list[BaseRoute]:
        """Get Starlette routes for A2A protocol.

        Each agent's FastA2A app is mounted at ``/{agent_name}`` (JSON-RPC endpoint,
        ``/.well-known/agent-card.json``, ``/docs``); ``/`` lists the agents.

        Returns:
            Routes for each agent plus the root listing endpoint
        """
        from starlette.responses import JSONResponse
        from starlette.routing import Mount, Route

        routes: list[BaseRoute] = []
        self.apps = {name: self._create_agent_app(name) for name in self.pool.all_agents}
        for agent_name, app in self.apps.items():
            prefix = f"/{agent_name}"
            routes.append(Route(prefix, _AtRoot(app, prefix), methods=["POST"]))
            routes.append(Mount(prefix, app=app))
            self.log.debug("Registered A2A routes", agent=agent_name, route=prefix)

        async def list_agents(request: Request) -> Response:
            """List all available agents."""
            agent_list = [
                {
                    "name": name,
                    "route": f"/{name}",
                    "agent_card": f"/{name}/.well-known/agent-card.json",
                    "docs": f"/{name}/docs",
                    "model": agent.model_name,
                }
                for name, agent in self.pool.all_agents.items()
            ]
            return JSONResponse({
                "agents": agent_list,
                "count": len(agent_list),
                "protocol": "A2A",
                "version": "1.0",
            })

        routes.append(Route("/", list_agents, methods=["GET"]))
        self.log.info("Created A2A routes", agent_count=len(self.apps))
        return routes

    @asynccontextmanager
    async def app_lifespan(self, app: Starlette) -> AsyncGenerator[None]:
        """Run every agent app's lifespan (task manager and worker) for the server's life."""
        async with AsyncExitStack() as stack:
            for a2a_app in self.apps.values():
                await stack.enter_async_context(a2a_app.router.lifespan_context(a2a_app))
            yield

    def get_agent_url(self, agent_name: str) -> str:
        """Get the URL for a specific agent.

        Args:
            agent_name: Name of the agent

        Returns:
            Full URL for the agent's A2A endpoint
        """
        return f"{self.base_url}/{agent_name}"

    def get_agent_card_url(self, agent_name: str) -> str:
        """Get the agent card URL for a specific agent.

        Args:
            agent_name: Name of the agent

        Returns:
            Full URL for the agent's A2A card endpoint
        """
        return f"{self.base_url}/{agent_name}/.well-known/agent-card.json"

    def list_agent_routes(self) -> dict[str, dict[str, str]]:
        """List all agent routes.

        Returns:
            Dictionary mapping agent names to their URLs
        """
        return {
            name: {
                "endpoint": self.get_agent_url(name),
                "agent_card": self.get_agent_card_url(name),
                "docs": f"{self.base_url}/{name}/docs",
            }
            for name in self.pool.all_agents
        }
