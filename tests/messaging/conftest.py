"""Fixtures for messaging tests."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Self

from evented.base import EventSource
from evented.event_data import EventData
import pytest


if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from types import TracebackType

    from agentpool_config.events import EventConfig


class _Event(EventData):
    def to_prompt(self) -> str:
        return f"event from {self.source}"


class OneShotSource(EventSource):
    """Emits one event as soon as it is iterated, then waits until stopped."""

    started: list[str] = []  # noqa: RUF012

    def __init__(self, config: EventConfig) -> None:
        self.name = config.name

    async def __aenter__(self) -> Self:
        OneShotSource.started.append(self.name)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        return None

    async def events(self) -> AsyncGenerator[EventData]:
        yield _Event(source=self.name)
        await asyncio.Event().wait()


@pytest.fixture
def one_shot_sources(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    OneShotSource.started = []
    monkeypatch.setattr(EventSource, "from_config", classmethod(lambda cls, c: OneShotSource(c)))
    return OneShotSource.started
