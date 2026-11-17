"""Robustness tests for the JSON-RPC connection layer.

The peer is driven with raw NDJSON frames over in-memory streams, so every test
controls exactly what goes over the wire.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import anyenv
import pytest

from acp import RequestError
from acp.client.connection import ClientSideConnection
from acp.client.implementations.noop_client import NoOpClient
from acp.connection import Connection
from acp.schema import PromptRequest, TextContentBlock
from acp.task import TaskSupervisor
from tests.acp.streams import TIMEOUT, Peer, make_streams


if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from acp.schema import SessionNotification


async def _noop_handler(method: str, params: Any, is_notification: bool) -> Any:
    return {}


@pytest.fixture
async def pair() -> AsyncIterator[tuple[Connection, Peer]]:
    writer, reader, peer = make_streams()
    conn = Connection(_noop_handler, writer, reader)
    yield conn, peer
    await conn.close()


async def _request(conn: Connection, peer: Peer) -> tuple[asyncio.Task[Any], Any]:
    """Send a request from `conn`, returning its task and the id the peer saw."""
    task = asyncio.create_task(conn.send_request("ping", {}))
    request = await peer.receive()
    return task, request["id"]


# --- responses ---------------------------------------------------------------


async def test_error_response_without_data_raises_request_error(
    pair: tuple[Connection, Peer],
) -> None:
    conn, peer = pair
    task, request_id = await _request(conn, peer)
    error = {"code": -32601, "message": "Method not found"}  # `data` is optional
    await peer.send({"jsonrpc": "2.0", "id": request_id, "error": error})
    with pytest.raises(RequestError) as exc_info:
        await asyncio.wait_for(task, TIMEOUT)
    assert exc_info.value.code == -32601
    assert str(exc_info.value) == "Method not found"
    assert exc_info.value.data is None


async def test_null_result_resolves_to_none(pair: tuple[Connection, Peer]) -> None:
    conn, peer = pair
    task, request_id = await _request(conn, peer)
    await peer.send({"jsonrpc": "2.0", "id": request_id, "result": None})
    assert await asyncio.wait_for(task, TIMEOUT) is None


async def test_request_object_rejects_non_object_result(pair: tuple[Connection, Peer]) -> None:
    conn, peer = pair
    task = asyncio.create_task(conn.request_object("ping", {}))
    request_id = (await peer.receive())["id"]
    await peer.send({"jsonrpc": "2.0", "id": request_id, "result": [1, 2]})
    with pytest.raises(RequestError, match="Internal error"):
        await asyncio.wait_for(task, TIMEOUT)


async def test_responses_with_foreign_ids_are_ignored(pair: tuple[Connection, Peer]) -> None:
    conn, peer = pair
    task, request_id = await _request(conn, peer)
    # `true` must not alias request id 1 / 0, strings and null belong to no request of ours
    for foreign_id in (True, False, str(request_id), None, 1.0):
        await peer.send({"jsonrpc": "2.0", "id": foreign_id, "result": {"wrong": True}})
    await peer.send({"jsonrpc": "2.0", "id": request_id, "result": {"right": True}})
    assert await asyncio.wait_for(task, TIMEOUT) == {"right": True}


# --- malformed input ---------------------------------------------------------


async def test_malformed_frames_do_not_stop_the_receive_loop(
    pair: tuple[Connection, Peer],
) -> None:
    conn, peer = pair
    junk = [
        b"not json at all\n",
        b"[1, 2]\n",
        b"null\n",
        b"42\n",
        b"{}\n",
        b'{"jsonrpc": "2.0"}\n',
        b'{"foo": "bar"}\n',
        b'{"jsonrpc": "2.0", "method": 5}\n',
        b"   \n",
        b"\r\n",
    ]
    for frame in junk:
        await peer.send_raw(frame)
    task, request_id = await _request(conn, peer)
    await peer.send_raw(b'{"jsonrpc": "2.0", "id": %d, "result": {"ok": 1}}\r\n' % request_id)
    assert await asyncio.wait_for(task, TIMEOUT) == {"ok": 1}


async def test_frame_split_across_chunks_and_multibyte_characters(
    pair: tuple[Connection, Peer],
) -> None:
    conn, peer = pair
    task, request_id = await _request(conn, peer)
    frame = anyenv.dump_json({"jsonrpc": "2.0", "id": request_id, "result": {"t": "ü€"}}) + "\n"
    data = frame.encode()
    split = data.index("€".encode()) + 1  # inside the multi-byte sequence
    await peer.send_raw(data[:split])
    await peer.send_raw(data[split:])
    assert await asyncio.wait_for(task, TIMEOUT) == {"t": "ü€"}


async def test_final_frame_without_newline_is_processed() -> None:
    received: list[str] = []

    async def handler(method: str, params: Any, is_notification: bool) -> Any:
        received.append(method)

    writer, reader, peer = make_streams()
    conn = Connection(handler, writer, reader)
    await peer.send_raw(b'{"jsonrpc": "2.0", "method": "last"}')
    await peer.hang_up()
    await asyncio.wait_for(conn.wait_closed(), TIMEOUT)
    await asyncio.wait_for(conn.wait_for_notifications(), TIMEOUT)
    assert received == ["last"]
    await conn.close()


# --- connection lifecycle ----------------------------------------------------


async def test_pending_request_fails_when_peer_hangs_up(pair: tuple[Connection, Peer]) -> None:
    conn, peer = pair
    task, _ = await _request(conn, peer)
    await peer.hang_up()
    with pytest.raises(ConnectionError):
        await asyncio.wait_for(task, TIMEOUT)
    assert conn.closed


async def test_sends_after_peer_hang_up_fail_fast(pair: tuple[Connection, Peer]) -> None:
    conn, peer = pair
    await peer.hang_up()
    await asyncio.wait_for(conn.wait_closed(), TIMEOUT)
    for _ in range(3):  # Every attempt must fail, none may hang
        with pytest.raises(ConnectionError):
            await asyncio.wait_for(conn.send_notification("note", {}), TIMEOUT)
        with pytest.raises(ConnectionError):
            await asyncio.wait_for(conn.send_request("ping", {}), TIMEOUT)


async def test_sends_after_write_failure_fail_fast() -> None:
    writer, reader, peer = make_streams()
    conn = Connection(_noop_handler, writer, reader)
    await peer.to_peer.aclose()  # Writes break, reads stay open
    for _ in range(3):
        with pytest.raises(ConnectionError):
            await asyncio.wait_for(conn.send_notification("note", {}), TIMEOUT)
    await conn.close()


async def test_send_request_after_close_fails_fast(pair: tuple[Connection, Peer]) -> None:
    conn, _peer = pair
    await conn.close()
    with pytest.raises(ConnectionError):
        await asyncio.wait_for(conn.send_request("ping", {}), TIMEOUT)


async def test_close_rejects_pending_requests(pair: tuple[Connection, Peer]) -> None:
    conn, peer = pair
    task, _ = await _request(conn, peer)
    await conn.close()
    with pytest.raises(ConnectionError):
        await asyncio.wait_for(task, TIMEOUT)


async def test_handler_can_close_its_own_connection() -> None:
    conn_ref: list[Connection] = []
    closed = asyncio.Event()

    async def handler(method: str, params: Any, is_notification: bool) -> Any:
        await conn_ref[0].close()
        closed.set()
        return {}

    writer, reader, peer = make_streams()
    conn = Connection(handler, writer, reader)
    conn_ref.append(conn)
    await peer.send({"jsonrpc": "2.0", "id": 1, "method": "shutdown"})
    await asyncio.wait_for(closed.wait(), TIMEOUT)
    assert conn.closed


async def test_cancel_request_is_flushed_by_close(pair: tuple[Connection, Peer]) -> None:
    conn, peer = pair
    task, request_id = await _request(conn, peer)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await conn.close()  # Right away: the queued `$/cancel_request` must still go out
    cancel = await peer.receive()
    assert cancel["method"] == "$/cancel_request"
    assert cancel["params"] == {"requestId": request_id}


# --- request handling --------------------------------------------------------


async def test_unhandled_handler_error_becomes_internal_error() -> None:
    async def handler(method: str, params: Any, is_notification: bool) -> Any:
        raise ValueError("boom")

    writer, reader, peer = make_streams()
    conn = Connection(handler, writer, reader)
    await peer.send({"jsonrpc": "2.0", "id": "abc", "method": "explode"})
    response = await peer.receive()
    assert response["id"] == "abc"
    assert response["error"]["code"] == -32603
    assert response["error"]["data"] == {"details": "boom"}
    await conn.close()


async def test_cancel_request_answers_with_request_cancelled() -> None:
    started = asyncio.Event()

    async def handler(method: str, params: Any, is_notification: bool) -> Any:
        started.set()
        await asyncio.sleep(10)

    writer, reader, peer = make_streams()
    conn = Connection(handler, writer, reader)
    await peer.send({"jsonrpc": "2.0", "id": "req-1", "method": "slow"})
    await asyncio.wait_for(started.wait(), TIMEOUT)
    # Malformed and unknown cancellations are ignored
    for params in (None, {}, {"requestId": True}, {"requestId": "other"}):
        await peer.send({"jsonrpc": "2.0", "method": "$/cancel_request", "params": params})
    await peer.send({
        "jsonrpc": "2.0",
        "method": "$/cancel_request",
        "params": {"requestId": "req-1"},
    })
    response = await peer.receive()
    assert response == {
        "jsonrpc": "2.0",
        "id": "req-1",
        "error": {"code": -32800, "message": "Request cancelled", "data": None},
    }
    await conn.close()


# --- notification ordering ---------------------------------------------------


async def test_wait_for_notifications_waits_for_matching_ones() -> None:
    release = asyncio.Event()
    handled: list[str] = []

    async def handler(method: str, params: Any, is_notification: bool) -> Any:
        if method == "slow":
            await release.wait()
        handled.append(method)

    writer, reader, peer = make_streams()
    conn = Connection(handler, writer, reader)
    await peer.send({"jsonrpc": "2.0", "method": "slow"})
    await peer.send({"jsonrpc": "2.0", "method": "fast"})
    await asyncio.sleep(0.05)
    assert handled == ["fast"]
    # Only waiting for "fast" returns right away
    await asyncio.wait_for(conn.wait_for_notifications(lambda m, _p: m == "fast"), TIMEOUT)
    waiter = asyncio.create_task(conn.wait_for_notifications())
    await asyncio.sleep(0.05)
    assert not waiter.done()
    release.set()
    await asyncio.wait_for(waiter, TIMEOUT)
    assert handled == ["fast", "slow"]
    await conn.close()


async def test_wait_for_notifications_from_a_notification_handler_does_not_deadlock() -> None:
    conn_ref: list[Connection] = []
    done = asyncio.Event()

    async def handler(method: str, params: Any, is_notification: bool) -> Any:
        await conn_ref[0].wait_for_notifications()
        done.set()

    writer, reader, peer = make_streams()
    conn = Connection(handler, writer, reader)
    conn_ref.append(conn)
    await peer.send({"jsonrpc": "2.0", "method": "note"})
    await asyncio.wait_for(done.wait(), TIMEOUT)
    await conn.close()


async def test_close_releases_notification_waiters() -> None:
    async def handler(method: str, params: Any, is_notification: bool) -> Any:
        await asyncio.sleep(10)

    writer, reader, peer = make_streams()
    conn = Connection(handler, writer, reader)
    await peer.send({"jsonrpc": "2.0", "method": "stuck"})
    await asyncio.sleep(0.05)
    waiter = asyncio.create_task(conn.wait_for_notifications())
    await conn.close()
    await asyncio.wait_for(waiter, TIMEOUT)


class _SlowUpdatesClient(NoOpClient):
    def __init__(self) -> None:
        self.updates: list[SessionNotification] = []

    async def session_update(self, params: SessionNotification) -> None:
        await asyncio.sleep(0.05)  # E.g. awaiting a signal emission
        self.updates.append(params)


async def test_prompt_returns_after_its_session_updates_were_handled() -> None:
    writer, reader, peer = make_streams()
    client = _SlowUpdatesClient()
    conn = ClientSideConnection(client, writer, reader)
    request = PromptRequest(session_id="s1", prompt=[TextContentBlock(text="hi")])
    prompt = asyncio.create_task(conn.prompt(request))
    request_id = (await peer.receive())["id"]
    chunk = {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "x"}}
    frames = [
        {
            "jsonrpc": "2.0",
            "method": "session/update",
            "params": {"sessionId": "s1", "update": chunk},
        }
        for _ in range(3)
    ]
    frames.append({"jsonrpc": "2.0", "id": request_id, "result": {"stopReason": "end_turn"}})
    await peer.send_raw("".join(anyenv.dump_json(f) + "\n" for f in frames).encode())
    response = await asyncio.wait_for(prompt, TIMEOUT)
    assert response.stop_reason == "end_turn"
    assert len(client.updates) == 3
    await conn.close()


# --- helpers -----------------------------------------------------------------


async def test_supervisor_shutdown_from_supervised_task() -> None:
    supervisor = TaskSupervisor(source="test")
    other = supervisor.create(asyncio.sleep(10))

    async def shut_down() -> str:
        await supervisor.shutdown()
        return "survived"

    task = supervisor.create(shut_down())
    assert await asyncio.wait_for(task, TIMEOUT) == "survived"
    assert other.cancelled()


@pytest.mark.parametrize(
    ("error", "code", "message", "data"),
    [
        ({"code": 1, "message": "m", "data": {"x": 1}}, 1, "m", {"x": 1}),
        ({"code": 1, "message": "m"}, 1, "m", None),
        ({}, -32603, "Error", None),
        ({"code": True, "message": ""}, -32603, "Error", None),
        ({"code": "1", "message": 5}, -32603, "Error", None),
    ],
)
def test_request_error_from_error_obj(
    error: dict[str, Any], code: int, message: str, data: Any
) -> None:
    exc = RequestError.from_error_obj(error)
    assert (exc.code, str(exc), exc.data) == (code, message, data)


def test_request_error_from_non_object() -> None:
    exc = RequestError.from_error_obj("oops")
    assert exc.code == -32603
    assert exc.data == {"details": "Malformed error response", "error": "oops"}
