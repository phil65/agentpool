"""In-memory byte streams and a raw-frame peer for connection tests."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import anyenv
import anyio
from anyio.abc import ByteReceiveStream, ByteSendStream


if TYPE_CHECKING:
    from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream


TIMEOUT = 2


class MemoryReader(ByteReceiveStream):
    """Byte receive stream over an anyio memory stream; a closed stream reads as EOF."""

    def __init__(self, stream: MemoryObjectReceiveStream[bytes]) -> None:
        self._stream = stream

    async def receive(self, max_bytes: int = 65536) -> bytes:
        try:
            return await self._stream.receive()
        except anyio.EndOfStream:
            raise
        except anyio.ClosedResourceError as exc:
            raise anyio.EndOfStream from exc

    async def aclose(self) -> None:
        await self._stream.aclose()


class MemoryWriter(ByteSendStream):
    """Byte send stream over an anyio memory stream."""

    def __init__(self, stream: MemoryObjectSendStream[bytes]) -> None:
        self._stream = stream

    async def send(self, item: bytes) -> None:
        await self._stream.send(item)

    async def aclose(self) -> None:
        await self._stream.aclose()


class Peer:
    """The remote end of a connection, speaking raw frames."""

    def __init__(
        self,
        to_peer: MemoryObjectReceiveStream[bytes],
        from_peer: MemoryObjectSendStream[bytes],
    ) -> None:
        self.to_peer = to_peer
        self.from_peer = from_peer
        self._buffer = b""

    async def send_raw(self, data: bytes) -> None:
        await self.from_peer.send(data)

    async def send(self, message: dict[str, Any]) -> None:
        await self.send_raw((anyenv.dump_json(message) + "\n").encode())

    async def receive(self) -> dict[str, Any]:
        with anyio.fail_after(TIMEOUT):
            while b"\n" not in self._buffer:
                self._buffer += await self.to_peer.receive()
        line, self._buffer = self._buffer.split(b"\n", 1)
        return anyenv.load_json(line, return_type=dict)

    async def hang_up(self) -> None:
        """Simulate the peer going away: EOF on our reads, broken pipe on our writes."""
        await self.from_peer.aclose()
        await self.to_peer.aclose()


def make_streams() -> tuple[MemoryWriter, MemoryReader, Peer]:
    to_peer_send, to_peer_recv = anyio.create_memory_object_stream[bytes](100)
    from_peer_send, from_peer_recv = anyio.create_memory_object_stream[bytes](100)
    return (
        MemoryWriter(to_peer_send),
        MemoryReader(from_peer_recv),
        Peer(to_peer_recv, from_peer_send),
    )
