"""ACP Connection."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Coroutine
from contextvars import ContextVar
import copy
from dataclasses import dataclass
import inspect
from typing import TYPE_CHECKING, Any, Literal, Self, TypeIs

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
NotificationFilter = Callable[[str, JsonValue | None], bool]
"""Selects notifications by ``(method, params)``."""

RequestId = int | str


logger = structlog.get_logger(__name__)

CANCEL_REQUEST_METHOD = "$/cancel_request"
_LOG_LINE_LIMIT = 500

# The completion future of the notification handled by the current task, if any.
_current_notification: ContextVar[asyncio.Future[None] | None] = ContextVar(
    "acp_current_notification", default=None
)


StreamDirection = Literal["incoming", "outgoing"]


@dataclass(frozen=True, slots=True)
class StreamEvent:
    """Stream event."""

    direction: StreamDirection
    message: dict[str, Any]


StreamObserver = Callable[[StreamEvent], Coroutine[Any, Any, None] | None]


def is_request_id(value: Any) -> TypeIs[RequestId]:
    """Whether ``value`` is a JSON-RPC request id usable for correlation.

    ``bool`` is excluded explicitly: Python would otherwise alias ``true`` with ``1``.
    """
    return isinstance(value, str) or (isinstance(value, int) and not isinstance(value, bool))


class Connection:
    """Minimal JSON-RPC 2.0 connection over newline-delimited JSON frames.

    Using anyio streams for cross-platform compatibility.

    - Outgoing messages always include {"jsonrpc": "2.0"}
    - Requests and notifications are dispatched to a single async handler
    - Responses resolve pending futures by id
    - Malformed frames from the peer are logged and skipped, never fatal
    - Once the peer disconnects or the connection is closed, pending requests fail
      and new sends raise ``ConnectionError``
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
        self._disconnected = asyncio.Event()
        self._observers: list[StreamObserver] = list(observers or [])
        # Incoming requests currently being handled, for `$/cancel_request`
        self._inflight: dict[RequestId, asyncio.Task[dict[str, Any]]] = {}
        # Notifications received but not handled yet, with their (method, params)
        self._pending_notifications: dict[asyncio.Future[None], tuple[str, JsonValue | None]] = {}
        sender_type = sender_type or MessageSender
        self._sender = sender_type(self._writer, self._tasks)
        dispatcher_type = dispatcher_type or DefaultMessageDispatcher
        self._dispatcher = dispatcher_type(
            queue=self._queue,
            supervisor=self._tasks,
            store=self._state,
            request_runner=self._run_request,
            notification_runner=self._run_notification,
        )
        self._dispatcher.start()
        self._recv_task = self._tasks.create(
            self._receive_loop(),
            name="acp.Connection.receive",
            on_error=self._on_receive_error,
        )

    @property
    def closed(self) -> bool:
        """Whether the connection was closed or the peer disconnected."""
        return self._closed or self._disconnected.is_set()

    async def close(self) -> None:
        """Stop the receive loop and cancel any in-flight handler tasks.

        Messages already queued for sending (e.g. ``$/cancel_request``) are flushed
        first. A handler may close its own connection; no response is guaranteed
        for the request it is handling.
        """
        if self._closed:
            return
        self._closed = True
        await self._dispatcher.stop()
        await self._sender.close()
        await self._tasks.shutdown()
        self._disconnect(ConnectionError("Connection closed"))

    async def wait_closed(self) -> None:
        """Wait until the peer disconnected (EOF) or the connection was closed."""
        await self._disconnected.wait()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    async def send_request(self, method: str, params: JsonValue | None = None) -> Any:
        """Send a request and wait for the peer's response.

        Cancelling the caller sends ``$/cancel_request`` for a request still in flight.

        Raises:
            RequestError: The peer answered with an error.
            ConnectionError: The connection is closed or got closed before a response.
        """
        self._raise_if_closed()
        request_id = self._next_request_id
        self._next_request_id += 1
        future = self._state.register_outgoing(request_id, method)
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        try:
            await self._sender.send(payload)
            self._notify_observers("outgoing", payload)
            return await future
        except BaseException as exc:
            answered = future.done() and not future.cancelled()
            future.cancel()  # Nobody waits for it anymore
            if not answered:
                self._state.reject_outgoing(request_id, RequestError.request_cancelled())
                # A request cancelled while queued for sending still gets written.
                if isinstance(exc, asyncio.CancelledError):
                    self._send_cancel_request(request_id)
            raise

    async def request_object(self, method: str, params: JsonValue | None = None) -> dict[str, Any]:
        """Send a request whose result is a JSON object, as for all ACP methods.

        A ``null`` result is treated as an empty object.

        Raises:
            RequestError: The peer answered with an error or a non-object result.
            ConnectionError: The connection is closed or got closed before a response.
        """
        match await self.send_request(method, params):
            case None:
                return {}
            case dict() as result:
                return result
            case other:
                details = f"Expected an object result for {method}, got {type(other).__name__}"
                raise RequestError.internal_error({"details": details})

    async def send_notification(self, method: str, params: JsonValue | None = None) -> None:
        """Send a notification.

        Raises:
            ConnectionError: The connection is closed.
        """
        self._raise_if_closed()
        payload = {"jsonrpc": "2.0", "method": method, "params": params}
        await self._sender.send(payload)
        self._notify_observers("outgoing", payload)

    async def cancel_request(self, request_id: RequestId) -> None:
        """Ask the peer to cancel a request this side issued (``$/cancel_request``)."""
        await self.send_notification(CANCEL_REQUEST_METHOD, {"requestId": request_id})

    async def wait_for_notifications(self, select: NotificationFilter | None = None) -> None:
        """Wait until the notifications received so far have been handled.

        Notifications are handled concurrently, so a response can be processed while
        notifications the peer sent before it are still being handled. Awaiting this
        right after a response restores the peer's ordering. Notifications received
        later, and the one handled by the calling task, are not waited for.

        Args:
            select: Only wait for notifications matching this ``(method, params)`` filter.
        """
        current = _current_notification.get()
        pending = {
            done
            for done, (method, params) in self._pending_notifications.items()
            if done is not current and (select is None or select(method, params))
        }
        if pending:
            await asyncio.wait(pending)

    def _send_cancel_request(self, request_id: RequestId) -> None:
        """Enqueue ``$/cancel_request`` without awaiting, so it survives cancellation."""
        if self.closed:
            return
        payload = {
            "jsonrpc": "2.0",
            "method": CANCEL_REQUEST_METHOD,
            "params": {"requestId": request_id},
        }
        try:
            written = self._sender.send_nowait(payload)
        except ConnectionError:
            return
        self._notify_observers("outgoing", payload)

        def _log_failure(fut: asyncio.Future[None]) -> None:
            if not fut.cancelled() and (exc := fut.exception()) is not None:
                logger.debug("Failed to send $/cancel_request", request_id=request_id, exc=exc)

        written.add_done_callback(_log_failure)

    def _handle_cancel_request(self, params: Any) -> None:
        if not isinstance(params, dict) or not is_request_id(request_id := params.get("requestId")):
            return
        # Unknown or already answered requests are ignored, as required by the spec.
        if (task := self._inflight.get(request_id)) is not None:
            task.cancel()

    async def _receive_loop(self) -> None:
        """Read newline-delimited JSON messages using anyio TextReceiveStream.

        This approach has no 64KB line limit unlike asyncio's readline(), and the
        incremental decoder handles UTF-8 sequences split across chunks.
        """
        buffer = ""
        try:
            async for chunk in self._text_reader:
                lines = (buffer + chunk).split("\n")
                buffer = lines.pop()  # Keep incomplete line in buffer
                for line in lines:
                    if message := self._decode_line(line):
                        self._notify_observers("incoming", message)
                        await self._process_message(message)
            # A final frame without trailing newline
            if message := self._decode_line(buffer):
                self._notify_observers("incoming", message)
                await self._process_message(message)
        except asyncio.CancelledError:
            return
        except (anyio.ClosedResourceError, anyio.BrokenResourceError, anyio.EndOfStream):
            pass
        # EOF / closed: reject in-flight requests so callers get an error
        # instead of hanging forever (e.g. subprocess crash during initialize).
        self._disconnect(ConnectionError("Connection closed: remote end sent EOF"))

    def _decode_line(self, line: str) -> dict[str, Any] | None:
        """Decode one frame, returning None for blank or malformed ones.

        Like the Rust and TypeScript SDKs, malformed frames are skipped rather than
        answered, so a peer printing stray output to stdout cannot break the session.
        """
        if not (line := line.strip()):
            return None
        try:
            message = anyenv.load_json(line)
        except anyenv.JsonLoadError:
            logger.warning("Skipping unparsable JSON-RPC frame", line=line[:_LOG_LINE_LIMIT])
            return None
        if not isinstance(message, dict) or not message:
            # Includes batches, which ACP does not use.
            logger.warning("Skipping non-object JSON-RPC frame", line=line[:_LOG_LINE_LIMIT])
            return None
        return message

    async def _process_message(self, message: dict[str, Any]) -> None:
        match message:
            case {"method": str() as method, **rest} if "id" not in rest:
                if method == CANCEL_REQUEST_METHOD:
                    self._handle_cancel_request(message.get("params"))
                    return
                done = asyncio.get_running_loop().create_future()
                self._pending_notifications[done] = (method, message.get("params"))
                await self._queue.publish(RpcTask("notification", message, done))
            case {"method": str(), "id": _}:
                await self._queue.publish(RpcTask("request", message))
            case {"id": _}:
                self._handle_response(message)
            case _:
                logger.warning("Ignoring invalid JSON-RPC message", message=message)

    def _notify_observers(self, direction: StreamDirection, message: dict[str, Any]) -> None:
        if not self._observers:
            return
        snapshot = copy.deepcopy(message)
        event = StreamEvent(direction, snapshot)
        for observer in list(self._observers):
            try:
                result = observer(event)
            except Exception:
                logger.exception("Stream observer failed")
                continue
            if inspect.isawaitable(result) and not self._tasks.closed:
                name = f"acp.Connection.observer.{direction}"
                self._tasks.create(result, name=name, on_error=self._on_observer_error)

    def _on_observer_error(self, task: asyncio.Task[Any], exc: BaseException) -> None:
        logger.error("Stream observer coroutine failed", exc_info=exc)

    async def _run_request(self, task: RpcTask) -> dict[str, Any]:
        """Run a request handler and send its response.

        The handler runs in its own task, so ``$/cancel_request`` can cancel it without
        also cancelling delivery of the response the peer still expects.
        """
        message = task.message
        request_id = message["id"]
        handler = self._tasks.create(self._execute_request(message), name="acp.Connection.handler")
        if is_request_id(request_id):
            self._inflight[request_id] = handler

            def _forget(done: asyncio.Task[dict[str, Any]]) -> None:
                if self._inflight.get(request_id) is done:
                    del self._inflight[request_id]

            handler.add_done_callback(_forget)
        # Unlike `await handler`, this does not re-raise the handler's cancellation.
        await asyncio.wait({handler})
        if handler.cancelled():  # By `$/cancel_request` or from inside the handler
            err = RequestError.request_cancelled()
            payload = {"jsonrpc": "2.0", "id": request_id, "error": err.to_error_obj()}
        else:
            payload = handler.result()
        try:
            await self._sender.send(payload)
        except ConnectionError:
            if not self.closed:
                raise
            # The connection went away while the handler was running.
            logger.debug("Dropping response: connection closed", request_id=request_id)
            return payload
        self._notify_observers("outgoing", payload)
        return payload

    async def _execute_request(self, message: dict[str, Any]) -> dict[str, Any]:
        """Run the handler and turn its outcome into a response payload."""
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": message["id"]}
        method = message["method"]
        try:
            result = await self._handler(method, message.get("params"), False)
        except RequestError as exc:
            payload["error"] = exc.to_error_obj()
        except ValidationError as exc:
            errors = exc.errors(include_url=False, include_context=False)
            payload["error"] = RequestError.invalid_params({"errors": errors}).to_error_obj()
        except Exception as exc:
            logger.exception("Unhandled error while handling request", method=method)
            try:
                data = anyenv.load_json(str(exc), return_type=dict)
            except Exception:  # noqa: BLE001
                data = {"details": str(exc)}
            payload["error"] = RequestError.internal_error(data).to_error_obj()
        else:
            if isinstance(result, BaseModel):
                result = result.model_dump(mode="json", by_alias=True, exclude_none=True)
            payload["result"] = result
        return payload

    async def _run_notification(self, task: RpcTask) -> None:
        method = task.message["method"]
        token = _current_notification.set(task.done)
        try:
            await self._handler(method, task.message.get("params"), True)
        except Exception:
            logger.exception("Unhandled error while handling notification", method=method)
        finally:
            _current_notification.reset(token)
            if task.done is not None:
                self._complete_notification(task.done)

    def _complete_notification(self, done: asyncio.Future[None]) -> None:
        self._pending_notifications.pop(done, None)
        if not done.done():
            done.set_result(None)

    def _handle_response(self, message: dict[str, Any]) -> None:
        request_id = message["id"]
        # Outgoing ids are integers; anything else (e.g. `id: null` in a peer's parse
        # error report) cannot belong to a request of ours.
        if not isinstance(request_id, int) or isinstance(request_id, bool):
            logger.warning("Ignoring response with unknown id", message=message)
            return
        if "error" in message:
            error = RequestError.from_error_obj(message["error"])
            self._state.reject_outgoing(request_id, error)
        else:
            self._state.resolve_outgoing(request_id, message.get("result"))

    def _raise_if_closed(self) -> None:
        if self.closed:
            raise ConnectionError("Connection closed")

    def _disconnect(self, error: ConnectionError) -> None:
        self._disconnected.set()
        self._state.reject_all_outgoing(error)
        # Notifications that will never run must not block `wait_for_notifications`.
        # Queued ones still run after a peer EOF, but not after close().
        if self._closed:
            for done in list(self._pending_notifications):
                self._complete_notification(done)

    def _on_receive_error(self, task: asyncio.Task[Any], exc: BaseException) -> None:
        logger.error("Receive loop failed", exc_info=exc)
        self._disconnect(ConnectionError(f"Connection failed: {exc!r}"))

    def _on_task_error(self, task: asyncio.Task[Any], exc: BaseException) -> None:
        logger.error("Background task failed", task=task.get_name(), exc_info=exc)
