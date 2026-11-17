from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING

import anyenv
import anyio
from anyio.abc import ByteReceiveStream, ByteSendStream
import pytest

from acp import (
    AgentMessageChunk,
    AgentSideConnection,
    AuthenticateRequest,
    AuthenticateResponse,
    CancelNotification,
    ClientSideConnection,
    InitializeRequest,
    InitializeResponse,
    LoadSessionRequest,
    LoadSessionResponse,
    NewSessionRequest,
    ReadTextFileRequest,
    SessionNotification,
    SetSessionModeRequest,
    SetSessionModeResponse,
    UserMessageChunk,
    WriteTextFileRequest,
)


if TYPE_CHECKING:
    from acp import DefaultACPClient
    from acp.agent.implementations.testing import TestAgent


class AsyncioReaderAdapter(ByteReceiveStream):
    """Adapts asyncio.StreamReader to anyio's ByteReceiveStream interface."""

    def __init__(self, reader: asyncio.StreamReader) -> None:
        self._reader = reader

    async def receive(self, max_bytes: int = 65536) -> bytes:
        data = await self._reader.read(max_bytes)
        if not data:
            raise anyio.EndOfStream
        return data

    async def aclose(self) -> None:
        pass  # StreamReader doesn't need explicit close


class AsyncioWriterAdapter(ByteSendStream):
    """Adapts asyncio.StreamWriter to anyio's ByteSendStream interface."""

    def __init__(self, writer: asyncio.StreamWriter) -> None:
        self._writer = writer

    async def send(self, item: bytes) -> None:
        self._writer.write(item)
        await self._writer.drain()

    async def aclose(self) -> None:
        self._writer.close()
        with contextlib.suppress(Exception):
            await self._writer.wait_closed()


class _Server:
    def __init__(self) -> None:
        self._server: asyncio.AbstractServer | None = None
        self.server_reader: asyncio.StreamReader | None = None
        self.server_writer: asyncio.StreamWriter | None = None
        self.client_reader: asyncio.StreamReader | None = None
        self.client_writer: asyncio.StreamWriter | None = None

    async def __aenter__(self):
        async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
            self.server_reader = reader
            self.server_writer = writer

        self._server = await asyncio.start_server(handle, host="127.0.0.1", port=0)
        host, port = self._server.sockets[0].getsockname()[:2]
        self.client_reader, self.client_writer = await asyncio.open_connection(host, port)

        # wait until server side is set
        for _ in range(100):
            if self.server_reader and self.server_writer:
                break
            await anyio.sleep(0.01)
        assert self.server_reader
        assert self.server_writer
        return self

    async def __aexit__(self, *exc: object):
        if self.client_writer:
            self.client_writer.close()
            with contextlib.suppress(Exception):
                await self.client_writer.wait_closed()
        if self.server_writer:
            self.server_writer.close()
            with contextlib.suppress(Exception):
                await self.server_writer.wait_closed()
        if self._server:
            self._server.close()
            await self._server.wait_closed()


async def test_initialize_and_new_session(
    test_agent: TestAgent, test_client: DefaultACPClient
) -> None:
    async with _Server() as s:
        assert s.client_writer is not None
        assert s.client_reader is not None
        assert s.server_writer is not None
        assert s.server_reader is not None
        # server side is agent; client side is client
        # Wrap asyncio streams with anyio adapters
        agent_conn = ClientSideConnection(
            lambda _conn: test_client,
            AsyncioWriterAdapter(s.client_writer),
            AsyncioReaderAdapter(s.client_reader),
        )
        _client_conn = AgentSideConnection(
            lambda _conn: test_agent,
            AsyncioWriterAdapter(s.server_writer),
            AsyncioReaderAdapter(s.server_reader),
        )

        resp = await agent_conn.initialize(InitializeRequest(protocol_version=1))
        assert isinstance(resp, InitializeResponse)
        assert resp.protocol_version == 1
        request = NewSessionRequest(mcp_servers=[], cwd="/test")
        new_sess = await agent_conn.new_session(request)
        assert new_sess.session_id == "test-session-123"
        load_resp = await agent_conn.load_session(
            LoadSessionRequest(session_id=new_sess.session_id, cwd="/test", mcp_servers=[])
        )
        assert isinstance(load_resp, LoadSessionResponse)

        auth_resp = await agent_conn.authenticate(AuthenticateRequest(method_id="password"))
        assert isinstance(auth_resp, AuthenticateResponse)

        mode_resp = await agent_conn.set_session_mode(
            SetSessionModeRequest(session_id=new_sess.session_id, mode_id="ask")
        )
        assert isinstance(mode_resp, SetSessionModeResponse)


async def test_bidirectional_file_ops(test_agent: TestAgent, test_client: DefaultACPClient) -> None:
    async with _Server() as s:
        assert s.client_writer is not None
        assert s.client_reader is not None
        assert s.server_writer is not None
        assert s.server_reader is not None
        test_client.files["/test/file.txt"] = "Hello, World!"
        _agent_conn = ClientSideConnection(
            lambda _conn: test_client,
            AsyncioWriterAdapter(s.client_writer),
            AsyncioReaderAdapter(s.client_reader),
        )
        client_conn = AgentSideConnection(
            lambda _conn: test_agent,
            AsyncioWriterAdapter(s.server_writer),
            AsyncioReaderAdapter(s.server_reader),
        )

        # Agent asks client to read
        read_request = ReadTextFileRequest(session_id="sess", path="/test/file.txt")
        res = await client_conn.read_text_file(read_request)
        assert res.content == "Hello, World!"
        # Agent asks client to write
        req = WriteTextFileRequest(session_id="sess", path="/test/file.txt", content="A")
        await client_conn.write_text_file(req)
        assert test_client.files["/test/file.txt"] == "A"


async def test_cancel_notification_and_capture_wire(
    test_agent: TestAgent, test_client: DefaultACPClient
) -> None:
    async with _Server() as s:
        assert s.client_writer is not None
        assert s.client_reader is not None
        assert s.server_writer is not None
        assert s.server_reader is not None
        # Build only agent-side (server) connection. Client side: reader to inspect wire
        agent_conn = ClientSideConnection(
            lambda _conn: test_client,
            AsyncioWriterAdapter(s.client_writer),
            AsyncioReaderAdapter(s.client_reader),
        )
        _client_conn = AgentSideConnection(
            lambda _conn: test_agent,
            AsyncioWriterAdapter(s.server_writer),
            AsyncioReaderAdapter(s.server_reader),
        )
        # Send cancel notification from client-side connection to agent
        await agent_conn.cancel(CancelNotification(session_id="test-123"))
        # Read raw line from server peer (it will be consumed by agent recv loop quickly).
        # Instead, wait a brief moment and assert agent recorded it.
        for _ in range(50):
            if test_agent.cancellations:
                break
            await anyio.sleep(0.01)
        assert test_agent.cancellations == ["test-123"]


async def test_session_notifications_flow(
    test_agent: TestAgent, test_client: DefaultACPClient
) -> None:
    async with _Server() as s:
        assert s.client_writer is not None
        assert s.client_reader is not None
        assert s.server_writer is not None
        assert s.server_reader is not None
        _agent_conn = ClientSideConnection(
            lambda _conn: test_client,
            AsyncioWriterAdapter(s.client_writer),
            AsyncioReaderAdapter(s.client_reader),
        )
        client_conn = AgentSideConnection(
            lambda _conn: test_agent,
            AsyncioWriterAdapter(s.server_writer),
            AsyncioReaderAdapter(s.server_reader),
        )
        # Agent -> Client notifications
        agent_chunk = AgentMessageChunk.text("Hello")
        agent_notification = SessionNotification(session_id="sess", update=agent_chunk)
        await client_conn.session_update(agent_notification)
        chunk = UserMessageChunk.text("World")
        user_notification = SessionNotification(session_id="sess", update=chunk)
        await client_conn.session_update(user_notification)

        # Wait for async dispatch
        for _ in range(50):
            if len(test_client.notifications) >= 2:
                break
            await anyio.sleep(0.01)
        assert len(test_client.notifications) >= 2
        assert test_client.notifications[0].session_id == "sess"


async def test_concurrent_reads(test_agent: TestAgent, test_client: DefaultACPClient) -> None:
    async with _Server() as s:
        assert s.client_writer is not None
        assert s.client_reader is not None
        assert s.server_writer is not None
        assert s.server_reader is not None
        for i in range(5):
            test_client.files[f"/test/file{i}.txt"] = f"Content {i}"
        _agent_conn = ClientSideConnection(
            lambda _conn: test_client,
            AsyncioWriterAdapter(s.client_writer),
            AsyncioReaderAdapter(s.client_reader),
        )
        client_conn = AgentSideConnection(
            lambda _conn: test_agent,
            AsyncioWriterAdapter(s.server_writer),
            AsyncioReaderAdapter(s.server_reader),
        )

        async def read_one(i: int):
            request = ReadTextFileRequest(session_id="sess", path=f"/test/file{i}.txt")
            return await client_conn.read_text_file(request)

        results = await asyncio.gather(*(read_one(i) for i in range(5)))
        for i, res in enumerate(results):
            assert res.content == f"Content {i}"


async def test_invalid_params_results_in_error_response(
    test_agent: TestAgent, caplog: pytest.LogCaptureFixture
):
    caplog.set_level("CRITICAL")
    async with _Server() as s:
        # Only start agent-side (server) so we can inject raw request from client socket
        assert s.client_writer is not None
        assert s.client_reader is not None
        assert s.server_writer is not None
        assert s.server_reader is not None
        _server_conn = AgentSideConnection(
            lambda _conn: test_agent,
            AsyncioWriterAdapter(s.server_writer),
            AsyncioReaderAdapter(s.server_reader),
        )

        # Send initialize with wrong param type (protocolVersion should be int)
        req = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "oops"},
        }
        s.client_writer.write((anyenv.dump_json(req) + "\n").encode())
        await s.client_writer.drain()

        # Read response
        line = await asyncio.wait_for(s.client_reader.readline(), timeout=1)
        resp = anyenv.load_json(line)
        assert resp["id"] == 1
        assert "error" in resp
        invalid_params_code = -32602
        assert resp["error"]["code"] == invalid_params_code


async def test_method_not_found_results_in_error_response(
    test_agent: TestAgent, caplog: pytest.LogCaptureFixture
):
    caplog.set_level("CRITICAL")
    async with _Server() as s:
        assert s.client_writer is not None
        assert s.client_reader is not None
        assert s.server_writer is not None
        assert s.server_reader is not None
        _server_conn = AgentSideConnection(
            lambda _conn: test_agent,
            AsyncioWriterAdapter(s.server_writer),
            AsyncioReaderAdapter(s.server_reader),
        )

        req = {"jsonrpc": "2.0", "id": 2, "method": "unknown/method", "params": {}}
        s.client_writer.write((anyenv.dump_json(req) + "\n").encode())
        await s.client_writer.drain()

        line = await asyncio.wait_for(s.client_reader.readline(), timeout=1)
        resp = anyenv.load_json(line)
        assert resp["id"] == 2
        method_not_found_code = -32601
        assert resp["error"]["code"] == method_not_found_code


async def test_cancel_request_cancels_inflight_handler(test_agent: TestAgent) -> None:
    started = asyncio.Event()

    async def slow_ext_method(method: str, params: dict) -> dict:
        started.set()
        await asyncio.sleep(10)
        return {}

    test_agent.ext_method = slow_ext_method  # type: ignore[method-assign]
    async with _Server() as s:
        assert s.client_writer is not None
        assert s.client_reader is not None
        assert s.server_writer is not None
        assert s.server_reader is not None
        _server_conn = AgentSideConnection(
            lambda _conn: test_agent,
            AsyncioWriterAdapter(s.server_writer),
            AsyncioReaderAdapter(s.server_reader),
        )
        req = {"jsonrpc": "2.0", "id": 7, "method": "_slow", "params": {}}
        s.client_writer.write((anyenv.dump_json(req) + "\n").encode())
        await s.client_writer.drain()
        await asyncio.wait_for(started.wait(), timeout=1)

        # Unknown request IDs are ignored, then the real one gets cancelled
        for request_id in (99, 7):
            cancel = {
                "jsonrpc": "2.0",
                "method": "$/cancel_request",
                "params": {"requestId": request_id},
            }
            s.client_writer.write((anyenv.dump_json(cancel) + "\n").encode())
        await s.client_writer.drain()

        line = await asyncio.wait_for(s.client_reader.readline(), timeout=1)
        resp = anyenv.load_json(line)
        assert resp["id"] == 7
        request_cancelled_code = -32800
        assert resp["error"]["code"] == request_cancelled_code


async def test_cancelling_outgoing_request_sends_cancel_request(
    test_client: DefaultACPClient,
) -> None:
    async with _Server() as s:
        assert s.client_writer is not None
        assert s.client_reader is not None
        assert s.server_reader is not None
        conn = ClientSideConnection(
            lambda _conn: test_client,
            AsyncioWriterAdapter(s.client_writer),
            AsyncioReaderAdapter(s.client_reader),
        )
        task = asyncio.create_task(conn.ext_method("_slow", {}))
        request = anyenv.load_json(
            await asyncio.wait_for(s.server_reader.readline(), timeout=1), return_type=dict
        )
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        cancel = anyenv.load_json(
            await asyncio.wait_for(s.server_reader.readline(), timeout=1), return_type=dict
        )
        assert cancel["method"] == "$/cancel_request"
        assert cancel["params"] == {"requestId": request["id"]}


async def test_unimplemented_optional_method_is_method_not_found(test_agent: TestAgent) -> None:
    async with _Server() as s:
        assert s.client_writer is not None
        assert s.client_reader is not None
        assert s.server_writer is not None
        assert s.server_reader is not None
        _server_conn = AgentSideConnection(
            lambda _conn: test_agent,
            AsyncioWriterAdapter(s.server_writer),
            AsyncioReaderAdapter(s.server_reader),
        )
        req = {"jsonrpc": "2.0", "id": 3, "method": "session/delete", "params": {"sessionId": "x"}}
        s.client_writer.write((anyenv.dump_json(req) + "\n").encode())
        await s.client_writer.drain()

        line = await asyncio.wait_for(s.client_reader.readline(), timeout=1)
        resp = anyenv.load_json(line)
        method_not_found_code = -32601
        assert resp["error"]["code"] == method_not_found_code


async def test_set_session_mode_and_extensions(
    test_agent: TestAgent, test_client: DefaultACPClient
) -> None:
    async with _Server() as s:
        assert s.client_writer is not None
        assert s.client_reader is not None
        assert s.server_writer is not None
        assert s.server_reader is not None
        agent = test_agent
        client = test_client
        agent_conn = ClientSideConnection(
            lambda _conn: client,
            AsyncioWriterAdapter(s.client_writer),
            AsyncioReaderAdapter(s.client_reader),
        )
        _client_conn = AgentSideConnection(
            lambda _conn: agent,
            AsyncioWriterAdapter(s.server_writer),
            AsyncioReaderAdapter(s.server_reader),
        )

        # set_session_mode
        request = SetSessionModeRequest(session_id="sess", mode_id="yolo")
        resp = await agent_conn.set_session_mode(request)
        # Either empty object or typed response depending on implementation
        assert resp is None or resp.__class__.__name__ == "SetSessionModeResponse"

        # ext_method
        res = await agent_conn.ext_method("ping", {"x": 1})
        assert res.get("ok") is True

        # ext_notification
        await agent_conn.ext_notification("note", {"y": 2})
        # allow dispatch
        await anyio.sleep(0.05)
        assert agent.ext_notes
        assert agent.ext_notes[-1][0] == "note"


async def test_ignore_invalid_messages(test_agent: TestAgent):
    async with _Server() as s:
        assert s.client_writer is not None
        assert s.client_reader is not None
        assert s.server_writer is not None
        assert s.server_reader is not None
        _server_conn = AgentSideConnection(
            lambda _conn: test_agent,
            AsyncioWriterAdapter(s.server_writer),
            AsyncioReaderAdapter(s.server_reader),
        )

        # Message without id and method
        msg1 = {"jsonrpc": "2.0"}
        s.client_writer.write((anyenv.dump_json(msg1) + "\n").encode())
        await s.client_writer.drain()

        # Message without jsonrpc and without id/method
        msg2 = {"foo": "bar"}
        s.client_writer.write((anyenv.dump_json(msg2) + "\n").encode())
        await s.client_writer.drain()

        # Should not receive any response lines
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(s.client_reader.readline(), timeout=0.1)


if __name__ == "__main__":
    pytest.main(["-v", __file__])
