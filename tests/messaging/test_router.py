"""The router owns the connections between nodes."""

from __future__ import annotations

from datetime import timedelta

from pydantic_ai.models.test import TestModel

from agentpool import Agent, AgentPool


def _agent(name: str) -> Agent[None, str]:
    return Agent(name, model=TestModel(custom_output_text=f"from {name}"))


async def test_disconnected_talk_leaves_router_and_registry() -> None:
    async with AgentPool() as pool:
        a, b = _agent("a"), _agent("b")
        await pool.add_agent(a)
        await pool.add_agent(b)
        talk = a.connect_to(b, name="a_to_b")
        assert "a_to_b" in pool.connection_registry
        talk.disconnect()
        assert a.connections.get_connections() == []
        assert "a_to_b" not in pool.connection_registry


async def test_disconnect_removes_every_connection_to_the_node() -> None:
    a, b = _agent("a"), _agent("b")
    a.connect_to(b)
    a.connect_to(b)
    a.connections.disconnect(b)
    assert a.connections.get_connections() == []


async def test_pool_less_connections_route() -> None:
    a, b = _agent("a"), _agent("b")
    talk = a >> b
    async with a, b:
        await a.run("hi")
    assert [m.content for m in talk.stats.messages] == ["from a"]


async def test_joining_a_pool_moves_connections_to_its_router() -> None:
    a, b = _agent("a"), _agent("b")
    talk = a.connect_to(b, name="early")
    async with AgentPool() as pool:
        await pool.add_agent(a)
        await pool.add_agent(b)
        assert pool.router.outgoing(a) == [talk]
        assert pool.connection_registry["early"] is talk
        await a.run("hi")
    assert [m.content for m in talk.stats.messages] == ["from a"]


async def test_routing_follows_message_sent() -> None:
    a, b = _agent("a"), _agent("b")
    talk = a.connect_to(b)
    async with a, b:
        message = await a.run("hi")
        await a.message_sent.emit(message)  # Announcing it again routes it again
        assert len(talk.stats.messages) == 2
        talk.disconnect()
        await a.message_sent.emit(message)  # No connection left: nothing routes it
    assert len(talk.stats.messages) == 2


async def test_targets_run_after_the_source_stored_its_response() -> None:
    a = _agent("a")
    seen: list[int] = []

    def record(text: str) -> str:
        seen.append(len(a.conversation.chat_messages))
        return text

    a.connect_to(record)
    async with a:
        await a.run("hi")
    assert seen == [2]  # The prompt and the response


async def test_paused_routing_holds_messages_back() -> None:
    a, b = _agent("a"), _agent("b")
    talk = a.connect_to(b)
    async with a, b:
        async with a.connections.paused_routing():
            await a.run("held")
        await a.run("routed")
    assert [m.content for m in talk.stats.messages] == ["from a"]
    assert talk.active


async def test_waiting_connection_finishes_before_the_source_returns() -> None:
    a, b = _agent("a"), _agent("b")
    a.connect_to(b, delay=timedelta(milliseconds=50))
    a.connections.set_wait_state(b)
    async with a, b:
        await a.run("hi")
        assert len(b.conversation.chat_messages) == 2  # b ran before a's run returned
