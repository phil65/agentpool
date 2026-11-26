"""AG-UI server implementation for agentpool pool.

This module provides a server that exposes all agents in an AgentPool
via the AG-UI protocol, with each agent accessible at its own route.

Supports all agent types (native, ACP, Claude Code, Codex, AG-UI) through
the BaseAgentAGUIAdapter.
"""

from typing import TYPE_CHECKING

from agentpool.log import get_logger
from agentpool_server.agui_server.base_agent_adapter import BaseAgentAGUIAdapter
from agentpool_server.http_server import HTTPServer


if TYPE_CHECKING:
    from starlette.requests import Request
    from starlette.responses import Response
    from starlette.routing import Route

    from agentpool import AgentPool


logger = get_logger(__name__)

DEFAULT_PORT = 8002


class AGUIServer(HTTPServer):
    """AG-UI server for exposing pool agents on separate routes.

    Provides a unified HTTP server that exposes all agents in the pool via
    the AG-UI protocol. Each agent is accessible at `/{agent_name}` route.

    Example:
        ```python
        pool = AgentPool(manifest)

        server = AGUIServer(pool, host="localhost", port=8002)

        async with server:
            async with server.run_context():
                # Agents accessible at:
                # POST http://localhost:8002/agent1
                # POST http://localhost:8002/agent2
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
        """Initialize AG-UI server.

        Args:
            pool: AgentPool containing available agents
            name: Optional server name (auto-generated if None)
            host: Host to bind server to
            port: Port to bind server to
            raise_exceptions: Whether to raise exceptions during server start
        """
        super().__init__(pool, name=name, host=host, port=port, raise_exceptions=raise_exceptions)

    async def get_routes(self) -> list[Route]:
        """Get Starlette routes for AG-UI protocol.

        Returns:
            List of Route objects for each agent plus root listing endpoint
        """
        from starlette.routing import Route

        routes: list[Route] = []

        # Create route for each agent in the pool (all agent types supported)
        for agent_name in self.pool.all_agents:

            async def agent_handler(request: Request, agent_name: str = agent_name) -> Response:
                """Handle AG-UI requests for a specific agent."""
                from starlette.responses import JSONResponse

                pool_agent = self.pool.all_agents.get(agent_name)
                if pool_agent is None:
                    msg = f"Agent {agent_name!r} not found"
                    return JSONResponse({"error": msg}, status_code=404)
                try:
                    # Use BaseAgentAGUIAdapter which works with any agent type
                    return await BaseAgentAGUIAdapter.dispatch_request(request, agent=pool_agent)
                except Exception as e:
                    self.log.exception("Error handling AG-UI request", agent=agent_name)
                    return JSONResponse({"error": str(e)}, status_code=500)

            routes.append(Route(f"/{agent_name}", agent_handler, methods=["POST"]))
            self.log.debug("Registered AG-UI route", agent=agent_name, route=f"/{agent_name}")

        # Add root endpoint that lists available agents
        async def list_agents(request: Request) -> Response:
            """List all available agents."""
            from starlette.responses import JSONResponse

            agent_list = [
                {"name": name, "route": f"/{name}", "model": agent.model_name}
                for name, agent in self.pool.all_agents.items()
            ]
            return JSONResponse({"agents": agent_list, "count": len(agent_list)})

        routes.append(Route("/", list_agents, methods=["GET"]))
        self.log.info("Created AG-UI routes", agent_count=len(self.pool.all_agents))
        return routes

    def get_agent_url(self, agent_name: str) -> str:
        """Get the endpoint URL for a specific agent."""
        return f"{self.base_url}/{agent_name}"

    def list_agent_routes(self) -> dict[str, str]:
        """List all agent routes.

        Returns:
            Dictionary mapping agent names to their URLs
        """
        return {name: self.get_agent_url(name) for name in self.pool.all_agents}
