"""``idempotencyKey`` records of ACP v2 ``session/prompt`` in the session storage.

A v2 client sends the same key on every attempt of one prompt. The agent inserts at
most one message per key and answers a retry with the original ``messageId``; it has
to recognize the key for as long as it keeps the session, across restarts. The records
therefore live in the session's stored metadata, next to its other ACP data.
"""

from typing import TYPE_CHECKING, Any
import weakref

from aclientp.v2.idempotency import IdempotencyStore

from agentpool.log import get_logger


if TYPE_CHECKING:
    from agentpool.storage.manager import StorageManager


logger = get_logger(__name__)

METADATA_KEY = "acp_idempotency_keys"
"""Session metadata entry: ``{session_id: {idempotency_key: message_id}}``.

Nested by session ID so a fork, which copies the metadata, does not take over its
parent's keys: the same key in another session names another prompt.
"""

_stores: weakref.WeakKeyDictionary[StorageManager, IdempotencyStore] = weakref.WeakKeyDictionary()


class SessionStorageIdempotencyBackend:
    """Keeps idempotency records in the stored :class:`SessionData` of each session.

    A session missing from the storage (or a storage that fails) falls back to records in
    memory, which last while the process runs: enough for live-only sessions.
    """

    def __init__(self, storage: StorageManager) -> None:
        self.storage = storage
        self._live: dict[tuple[str, str], str] = {}

    async def get(self, session_id: str, key: str) -> str | None:
        try:
            data = await self.storage.load_session(session_id)
        except Exception:
            logger.exception("Could not read idempotency keys", session_id=session_id)
            data = None
        if data is not None:
            recorded = _records(data.metadata).get(session_id, {}).get(key)
            if isinstance(recorded, str):
                return recorded
        return self._live.get((session_id, key))

    async def put(self, session_id: str, key: str, message_id: str) -> None:
        self._live[session_id, key] = message_id
        try:
            data = await self.storage.load_session(session_id)
            if data is None:
                return
            records = _records(data.metadata)
            session_records = {**records.get(session_id, {}), key: message_id}
            updated = data.with_metadata(**{METADATA_KEY: {session_id: session_records}})
            await self.storage.save_session(updated)
        except Exception:
            logger.exception("Could not store an idempotency key", session_id=session_id)


def _records(metadata: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw = metadata.get(METADATA_KEY)
    if not isinstance(raw, dict):
        return {}
    return {sid: keys for sid, keys in raw.items() if isinstance(keys, dict)}


def idempotency_store(storage: StorageManager) -> IdempotencyStore:
    """The store of ``storage``, shared by all connections of the process.

    Shared so a retry on a new connection waits for an attempt that is still inserting
    on the old one.
    """
    if (store := _stores.get(storage)) is None:
        store = _stores[storage] = IdempotencyStore(SessionStorageIdempotencyBackend(storage))
    return store
