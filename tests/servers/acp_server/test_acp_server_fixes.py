"""ACP server fixes (#60): v2 prompt race, fallback message ids, requires_action, swap_pool."""

import asyncio
from typing import TYPE_CHECKING, Any

from aclientp.schema import v2
from pydantic_ai.models.function import AgentInfo, FunctionModel
import pytest

from agentpool import Agent, AgentPool
from agentpool_server.acp_server.v2_client import V2ClientAdapter
from agentpool_server.acp_server.v2_translate import UpdateTranslator
from tests.servers.acp_server.test_v2_agent import TIMEOUT, _new_session, _prompt, _serve


if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from pydantic_ai import ModelMessage


async def test_back_to_back_prompts_are_one_v2_turn(tmp_path: Any) -> None:
    """#60.1: a prompt accepted before the first one's turn started joins it (one running)."""
    seen: list[str] = []

    async def reply(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        seen.append(str(messages[-1].parts[-1].content))  # type: ignore[union-attr]
        yield "ok"

    async with _serve(FunctionModel(stream_function=reply)) as (conn, recorder, _):
        session_id = await _new_session(conn, str(tmp_path))
        await asyncio.gather(
            conn.prompt(_prompt(session_id, "first")), conn.prompt(_prompt(session_id, "second"))
        )
        idle = await recorder.wait_idle()
        await asyncio.sleep(0.2)  # No second turn / idle may follow
    assert idle.stop_reason == "end_turn"
    assert len(recorder.of(v2.RunningStateSessionUpdate)) == 1
    assert len(recorder.of(v2.IdleStateSessionUpdate)) == 1
    assert any("second" in text for text in seen)  # The second prompt ran in that turn


def test_fallback_message_ids_are_per_session_and_turn() -> None:
    """#60.2: chunks without an id share one only within a session's turn."""
    from aclientp.schema import AgentMessageChunk

    translator = UpdateTranslator()

    def message_id(session_id: str) -> str:
        update = translator.translate(AgentMessageChunk.text("x"), session_id)
        assert isinstance(update, v2.AgentMessageChunk)
        return update.message_id

    first = message_id("a")
    assert message_id("a") == first  # Same turn: one message
    assert message_id("b") != first  # Another session
    translator.new_turn("a")
    assert message_id("a") != first  # The next turn


class _FailingConnection:
    def __init__(self) -> None:
        self.fail = True

    async def session_update(self, params: v2.UpdateSessionNotification) -> None:
        if self.fail:
            self.fail = False
            raise ConnectionError("lost")

    async def request_permission(self, params: Any) -> v2.RequestPermissionResponse:
        return v2.RequestPermissionResponse(outcome=v2.CancelledPermissionOutcome())


async def test_a_failed_requires_action_update_does_not_wedge_the_count() -> None:
    """#60.3: the waiting count goes down when sending requires_action fails."""
    adapter = V2ClientAdapter(_FailingConnection(), UpdateTranslator())  # type: ignore[arg-type]  # ty:ignore[invalid-argument-type]
    with pytest.raises(ConnectionError):
        async with adapter._awaiting_user("s"):
            pass
    assert adapter._waiting == {}


async def test_swap_pool_rebinds_the_title_signal_and_new_connections_get_the_new_agent(
    tmp_path: Any,
) -> None:
    """#60.4a/b: titles of the new pool reach the connection; new connections use its agent."""
    from agentpool_server.acp_server import server as server_module
    from agentpool_server.acp_server.acp_agent import AgentPoolACPAgent
    from agentpool_server.acp_server.server import ACPServer

    async with AgentPool() as old_pool, AgentPool() as new_pool:
        old_agent = Agent(name="old", model="test")
        new_agent = Agent(name="new", model="test")
        await old_pool.add_agent(old_agent)
        await new_pool.add_agent(new_agent)
        server = ACPServer(old_pool)

        async def swap_pool(config_path: str, agent_name: str | None = None) -> Any:
            server.pool = new_pool
            return new_agent

        server.swap_pool = swap_pool  # type: ignore[method-assign]
        factories: dict[str, Any] = {}

        async def fake_serve(v1: Any, *, v2_agent: Any = None, **kwargs: Any) -> None:
            factories.update(v1=v1, v2=v2_agent)

        handler = AgentPoolACPAgent(client=object(), default_agent=old_agent, server=server)  # type: ignore[arg-type]  # ty:ignore[invalid-argument-type]
        callback = handler._on_metadata_generated
        assert callback in old_pool.storage.metadata_generated._async_callbacks
        await handler.swap_pool("unused.yml")
        assert callback not in old_pool.storage.metadata_generated._async_callbacks
        assert callback in new_pool.storage.metadata_generated._async_callbacks
        await handler.aclose()
        assert callback not in new_pool.storage.metadata_generated._async_callbacks

        original = server_module.serve
        server_module.serve = fake_serve  # type: ignore[assignment]
        try:
            await server._start_async()
        finally:
            server_module.serve = original  # type: ignore[assignment]
        connection = factories["v1"](object())
        assert connection.default_agent is new_agent
        await asyncio.wait_for(connection.aclose(), TIMEOUT)
