"""ACP Connection."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Coroutine
import contextlib
import copy
from dataclasses import dataclass
import inspect
import logging
from typing import TYPE_CHECKING, Any, Literal, Self

import anyenv
import anyio
from anyio.streams.text import TextReceiveStream
from pydantic import BaseModel, ValidationError
import structlog

from acp.exceptions import RequestError
from acp.task import (
    DefaultMessageDispatcher,
    InMemoryMessageQueue,
    InMemoryMessageStateStore,
    MessageSender,
    RpcTask,
    TaskSupervisor,
)


if TYPE_CHECKING:
    from anyio.abc import ByteReceiveStream, ByteSendStream

    from acp.task import MessageDispatcher, MessageQueue, MessageStateStore


JsonValue = Any
MethodHandler = Callable[[str, JsonValue | None, bool], Awaitable[JsonValue | None]]


logger = structlog.get_logger(__name__)


StreamDirection = Literal["incoming", "outgoing"]


@dataclass(frozen=True, slots=True)
class StreamEvent:
    """Stream event."""

    direction: StreamDirection
    message: dict[str, Any]


StreamObserver = Callable[[StreamEvent], Coroutine[Any, Any, None] | None]


class Connection:
    """Minimal JSON-RPC 2.0 connection over newline-delimited JSON frames.

    Using anyio streams for cross-platform compatibility.

    - Outgoing messages always include {"jsonrpc": "2.0"}
    - Requests and notifications are dispatched to a single async handler
    - Responses resolve pending futures by numeric id
    """

    def __init__(
        self,
        handler: MethodHandler,
        writer: ByteSendStream,
        reader: ByteReceiveStream,
        *,
        queue: MessageQueue | None = None,
        state_store: MessageStateStore | None = None,
        dispatcher_type: type[MessageDispatcher] | None = None,
        sender_type: type[MessageSender] | None = None,
        observers: list[StreamObserver] | None = None,
    ) -> None:
        self._handler = handler
        self._writer = writer
        self._reader = reader
        self._text_reader = TextReceiveStream(reader)
        self._next_request_id = 0
        self._state = state_store or InMemoryMessageStateStore()
        self._tasks = TaskSupervisor(source="acp.Connection", error_handlers=[self._on_task_error])
        self._queue = queue or InMemoryMessageQueue()
        self._closed = False
        sender_type = sender_type or MessageSender
        self._sender = sender_type(self._writer, self._tasks)
        self._recv_task = self._tasks.create(
            self._receive_loop(),
            name="acp.Connection.receive",
            on_error=self._on_receive_error,
        )
        dispatcher_type = dispatcher_type or DefaultMessageDispatcher
        self._dispatcher = dispatcher_type(
            queue=self._queue,
            supervisor=self._tasks,
            store=self._state,
            request_runner=self._run_request,
            notification_runner=self._run_notification,
        )
        self._dispatcher.start()
        self._observers: list[StreamObserver] = list(observers or [])

    async def close(self) -> None:
        """Stop the receive loop and cancel any in-flight handler tasks."""
        if self._closed:
            return
        self._closed = True
        await self._dispatcher.stop()
        await self._sender.close()
        await self._tasks.shutdown()
        self._state.reject_all_outgoing(ConnectionError("Connection closed"))

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    async def send_request(self, method: str, params: JsonValue | None = None) -> dict[str, Any]:
        request_id = self._next_request_id
        self._next_request_id += 1
        future = self._state.register_outgoing(request_id, method)
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        await self._sender.send(payload)
        self._notify_observers("outgoing", payload)
        data = await future
        assert isinstance(data, dict)
        return data

    async def send_notification(self, method: str, params: JsonValue | None = None) -> None:
        payload = {"jsonrpc": "2.0", "method": method, "params": params}
        await self._sender.send(payload)
        self._notify_observers("outgoing", payload)

    async def _receive_loop(self) -> None:
        """Read newline-delimited JSON messages using anyio TextReceiveStream.

        This approach has no 64KB line limit unlike asyncio's readline().
        Pattern follows MCP SDK's stdio client implementation.
        """
        buffer = ""
        try:
            async for chunk in self._text_reader:
                lines = (buffer + chunk).split("\n")
                buffer = lines.pop()  # Keep incomplete line in buffer

                for line in lines:
                    if not line or line == "null":
                        continue
                    try:
                        message = anyenv.load_json(line, return_type=dict)
                    except Exception:
                        # Align with Rust/TS: on parse error, just skip instead of response
                        logger.exception("Error parsing JSON-RPC message", line=line)
                        continue
                    else:
                        self._notify_observers("incoming", message)
                        await self._process_message(message)
        except asyncio.CancelledError:
            return
        except anyio.ClosedResourceError:
            pass
        except anyio.EndOfStream:
            pass
        # EOF / closed: reject in-flight requests so callers get an error
        # instead of hanging forever (e.g. subprocess crash during initialize).
        self._state.reject_all_outgoing(ConnectionError("Connection closed: remote end sent EOF"))

    async def _process_message(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        has_id = "id" in message
        if method is not None:
            task = RpcTask("request" if has_id else "notification", message)
            await self._queue.publish(task)
            return
        if has_id:
            await self._handle_response(message)

    def _notify_observers(self, direction: StreamDirection, message: dict[str, Any]) -> None:
        if not self._observers:
            return
        snapshot = copy.deepcopy(message)
        event = StreamEvent(direction, snapshot)
        for observer in list(self._observers):
            try:
                result = observer(event)
            except Exception:
                logging.exception("Stream observer failed")
                continue
            if inspect.isawaitable(result):
                name = f"acp.Connection.observer.{direction}"
                self._tasks.create(result, name=name, on_error=self._on_observer_error)

    def _on_observer_error(self, task: asyncio.Task[Any], exc: BaseException) -> None:
        logging.exception("Stream observer coroutine failed", exc_info=exc)

    async def _run_request(self, message: dict[str, Any]) -> Any:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": message["id"]}
        try:
            result = await self._handler(message["method"], message.get("params"), False)
            if isinstance(result, BaseModel):
                result = result.model_dump(by_alias=True, exclude_none=True)
            payload["result"] = result if result is not None else None
            await self._sender.send(payload)
            self._notify_observers("outgoing", payload)
            return payload.get("result")
        except RequestError as exc:
            payload["error"] = exc.to_error_obj()
            await self._sender.send(payload)
            self._notify_observers("outgoing", payload)
            raise
        except ValidationError as exc:
            err = RequestError.invalid_params({"errors": exc.errors()})
            payload["error"] = err.to_error_obj()
            await self._sender.send(payload)
            self._notify_observers("outgoing", payload)
            raise err from None
        except Exception as exc:  # noqa: BLE001
            try:
                data = anyenv.load_json(str(exc), return_type=dict)
            except Exception:  # noqa: BLE001
                data = {"details": str(exc)}
            err = RequestError.internal_error(data)
            payload["error"] = err.to_error_obj()
            await self._sender.send(payload)
            self._notify_observers("outgoing", payload)
            raise err from None

    async def _run_notification(self, message: dict[str, Any]) -> None:
        with contextlib.suppress(Exception):
            await self._handler(message["method"], message.get("params"), True)

    async def _handle_response(self, message: dict[str, Any]) -> None:
        match message:
            case {"id": request_id, "result": result}:
                self._state.resolve_outgoing(request_id, result)
            case {"id": request_id, "error": {"code": code, "message": err, "data": data}}:
                error = RequestError(code, err or "Error", data)
                self._state.reject_outgoing(request_id, error)
            case {"id": request_id}:
                self._state.resolve_outgoing(request_id, None)

    def _on_receive_error(self, task: asyncio.Task[Any], exc: BaseException) -> None:
        logging.exception("Receive loop failed", exc_info=exc)
        self._state.reject_all_outgoing(exc)

    def _on_task_error(self, task: asyncio.Task[Any], exc: BaseException) -> None:
        logging.exception("Background task failed", exc_info=exc)
