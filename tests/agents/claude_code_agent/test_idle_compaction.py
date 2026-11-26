"""Compacting an idle Claude Code session shortly before its prompt cache expires."""

import asyncio
from typing import Any

from anthropic.types.beta import BetaCacheCreation, BetaUsage
import pytest

from agentpool.agents.claude_code_agent.idle_compaction import (
    DEFAULT_DELAY,
    LAPSED_AFTER,
    IdleCompaction,
)


START = 1_000_000.0


def _usage(*, context: int = 300_000, ttl: str = "1h") -> BetaUsage:
    """A request's usage: ``context`` tokens, mostly read from a cache written with ``ttl``."""
    written = 1_000
    return BetaUsage(
        input_tokens=10,
        output_tokens=5,
        cache_read_input_tokens=context - written - 10,
        cache_creation_input_tokens=written,
        cache_creation=BetaCacheCreation(
            ephemeral_1h_input_tokens=written if ttl == "1h" else 0,
            ephemeral_5m_input_tokens=written if ttl == "5m" else 0,
        ),
    )


class Harness:
    """An IdleCompaction with a settable clock, recording its compactions."""

    def __init__(
        self, *, settings: dict[str, Any] | None = None, env: dict[str, str] | None = None
    ) -> None:
        self.now = START
        self.busy = False
        self.compactions = 0
        self._settings = settings or {}

        async def compact() -> None:
            self.compactions += 1

        async def read_settings() -> dict[str, Any]:
            return self._settings

        self.compaction = IdleCompaction(
            compact=compact,
            is_busy=lambda: self.busy,
            settings=read_settings,
            env=env or {},
            clock=lambda: self.now,
        )

    async def idle_at(self, now: float) -> int:
        """Go idle at ``now`` (the timer fires at once once the deadline passed)."""
        self.now = now
        self.compaction.on_idle()
        await asyncio.sleep(0.01)
        return self.compactions


DUE = START + DEFAULT_DELAY


async def test_compacts_once_when_due() -> None:
    harness = Harness()
    harness.compaction.note_request("msg_1", _usage())
    assert await harness.idle_at(DUE) == 1
    # Nothing new was requested: no second compaction (a failed one would loop)
    assert await harness.idle_at(DUE + 1) == 1
    # A new request starts the next cycle
    harness.compaction.note_request("msg_2", _usage())
    assert await harness.idle_at(DUE + 1) == 1  # Its own deadline is an hour away


async def test_timer_waits_for_the_deadline_and_a_new_request_disarms_it() -> None:
    harness = Harness()
    harness.compaction.note_request("msg_1", _usage())
    assert await harness.idle_at(START + 60) == 0  # Armed for ~54 minutes later
    assert harness.compaction._timer is not None
    harness.compaction.note_request("msg_2", _usage())
    assert harness.compaction._timer is None


@pytest.mark.parametrize(
    ("setup", "now"),
    [
        (lambda h: h.compaction.note_request("m", _usage(ttl="5m")), DUE),
        (lambda h: h.compaction.note_request("m", _usage(context=150_000)), DUE),
        (lambda h: h.compaction.note_rate_limit("rejected"), DUE),
        (lambda h: setattr(h, "busy", True), DUE),
        (lambda h: None, DUE + LAPSED_AFTER + 1),  # Machine slept: the cache is gone
    ],
    ids=["5m cache", "small context", "rate limited", "busy", "lapsed"],
)
async def test_skips(setup: Any, now: float) -> None:
    harness = Harness()
    harness.compaction.note_request("msg_1", _usage())
    setup(harness)
    assert await harness.idle_at(now) == 0


@pytest.mark.parametrize(
    ("settings", "env"),
    [
        ({"autoCompactEnabled": False}, {}),
        ({"idleCompaction": False}, {}),
        ({}, {"DISABLE_COMPACT": "1"}),
        ({"env": {"DISABLE_AUTO_COMPACT": "true"}}, {}),  # The settings' env block
    ],
)
async def test_cli_switches_turn_it_off(settings: dict[str, Any], env: dict[str, str]) -> None:
    harness = Harness(settings=settings, env=env)
    harness.compaction.note_request("msg_1", _usage())
    assert await harness.idle_at(DUE) == 0


async def test_min_tokens_from_env() -> None:
    harness = Harness(env={"CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS": "500000"})
    harness.compaction.note_request("msg_1", _usage(context=300_000))
    assert await harness.idle_at(DUE) == 0


async def test_reset_forgets_the_conversation() -> None:
    harness = Harness()
    harness.compaction.note_request("msg_1", _usage())
    harness.compaction.reset()  # /clear: the cache belonged to the old conversation
    assert await harness.idle_at(DUE) == 0
