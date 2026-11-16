"""Global routes (health, events)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING

import anyenv
from fastapi import APIRouter
from fastapi.sse import EventSourceResponse, ServerSentEvent

from agentpool import log
from agentpool_server.opencode_server.dependencies import StateDep
from opencode_sdk.models import (
    Config,
    Event,
    HealthResponse,
    ServerConnectedEvent,
    ServerHeartbeatEvent,
)


if TYPE_CHECKING:
    from agentpool_server.opencode_server.state import ServerState


logger = log.get_logger(__name__)
router = APIRouter(tags=["global"])

VERSION = "0.1.0"


@router.get("/global/health")
async def get_health() -> HealthResponse:
    """Get server health status."""
    return HealthResponse(healthy=True, version=VERSION)


def _serialize_event(event: Event, wrap_payload: bool = False) -> str:
    """Serialize event, optionally wrapping in payload structure."""
    event_data = event.model_dump(by_alias=True, exclude_none=True)
    if wrap_payload:
        return anyenv.dump_json({"payload": event_data})
    return anyenv.dump_json(event_data)


async def _event_generator(
    state: ServerState, *, wrap_payload: bool = False
) -> AsyncGenerator[ServerSentEvent]:
    """Generate SSE events."""
    queue: asyncio.Queue[Event] = asyncio.Queue()
    state.event_subscribers.append(queue)
    subscriber_count = len(state.event_subscribers)
    logger.info("SSE: New client connected (total subscribers: %s)", subscriber_count)

    # Trigger first subscriber callback if this is the first connection
    if (
        subscriber_count == 1
        and not state._first_subscriber_triggered
        and state.on_first_subscriber is not None
    ):
        state._first_subscriber_triggered = True
        state.create_background_task(state.on_first_subscriber(), name="on_first_subscriber")

    try:
        # Send initial connected event
        connected = ServerConnectedEvent()
        data = _serialize_event(connected, wrap_payload=wrap_payload)
        logger.info("SSE: Sending connected event", data=data)
        yield ServerSentEvent(raw_data=data)
        # Stream events with heartbeat
        heartbeat = ServerHeartbeatEvent()
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=10.0)
            except TimeoutError:
                # Send heartbeat every 10s to prevent stalled proxy streams
                data = _serialize_event(heartbeat, wrap_payload=wrap_payload)
                yield ServerSentEvent(raw_data=data)
                continue
            data = _serialize_event(event, wrap_payload=wrap_payload)
            logger.info("SSE: Sending event", event_type=event.type)
            yield ServerSentEvent(raw_data=data)
    finally:
        state.event_subscribers.remove(queue)
        logger.info("SSE: Client disconnected", remaining_subscribers=len(state.event_subscribers))


@router.get("/global/event", response_class=EventSourceResponse)
async def get_global_events(state: StateDep) -> AsyncGenerator[ServerSentEvent]:
    """Get global events as SSE stream (uses payload wrapper)."""
    async for event in _event_generator(state, wrap_payload=True):
        yield event


@router.get("/global/config")
async def get_global_config(state: StateDep) -> Config:
    """Get global configuration."""
    return state.config


@router.patch("/global/config")
async def update_global_config(state: StateDep, config: Config) -> Config:
    """Update global configuration."""
    state.config = config
    return state.config


@router.post("/global/dispose")
async def global_dispose(state: StateDep) -> bool:
    """Dispose all instances and release resources."""
    await state.cleanup_tasks()
    return True


@router.post("/global/upgrade")
async def global_upgrade(state: StateDep) -> dict[str, object]:
    """Upgrade opencode (stub - not applicable for agentpool)."""
    _ = state
    return {"success": False, "error": "Upgrade not supported in agentpool"}


@router.post("/instance/dispose")
async def instance_dispose(state: StateDep) -> bool:
    """Dispose the current instance."""
    await state.cleanup_tasks()
    return True


@router.get("/event", response_class=EventSourceResponse)
async def get_events(state: StateDep) -> AsyncGenerator[ServerSentEvent]:
    """Get events as SSE stream (no payload wrapper)."""
    async for event in _event_generator(state, wrap_payload=False):
        yield event
