"""The V2 server reports an execution by its turn's outcome (not only by errors)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentpool import AgentContext  # noqa: TC001  # Tools' annotations resolve at runtime

from .conftest import create_session, prompt_and_wait


if TYPE_CHECKING:
    from httpx2 import AsyncClient

    from agentpool import AgentPool


async def test_a_turn_the_agent_stops_itself_is_interrupted(
    client: AsyncClient, pool: AgentPool[Any]
) -> None:
    async def halt(ctx: AgentContext) -> str:
        """Stop the turn (as a denial that stops it does)."""
        ctx.stop_turn()
        return "stopping"

    pool.all_agents["helper"].tools.register_tool(halt)
    sid = await create_session(client, agent="helper")
    await prompt_and_wait(client, sid, "go")
    session = (await client.get(f"/api/session/{sid}")).json()["data"]
    assert session["outcome"] == "interrupted"


async def test_a_completed_turn_still_succeeds(client: AsyncClient) -> None:
    sid = await create_session(client, agent="other")
    await prompt_and_wait(client, sid, "hello")
    session = (await client.get(f"/api/session/{sid}")).json()["data"]
    assert session["outcome"] == "succeeded"
