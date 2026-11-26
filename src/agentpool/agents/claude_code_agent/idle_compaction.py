"""Compacting an idle Claude Code session shortly before its prompt cache expires.

Claude Code writes the conversation to a prompt cache that lives for one hour on a Claude
plan. The first prompt after it expired writes the whole context again: for a large
context, slow and a big share of the plan's usage. Compacting shortly before that, while
the cache is still warm, makes the next prompt write only the summary. The CLI does this
itself, but only for interactive sessions, not for SDK ones (as Zed's adapter found, whose
rules this follows: ``idle-compaction.ts``).

Compaction runs when all of these hold:

- the last request wrote the cache with the one-hour TTL (its usage says so);
- the context is at least ``CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS`` (200k, no less than 100k);
- the deadline, 90% of the hour after the request started, has not lapsed by over a
  minute (a machine that slept: the cache is gone already);
- the rate limit status is ``allowed``, and nothing was compacted since that request;
- the agent is idle, and neither the CLI's settings (``autoCompactEnabled``,
  ``idleCompaction``) nor ``DISABLE_COMPACT`` / ``DISABLE_AUTO_COMPACT`` turn it off.
"""

import asyncio
from dataclasses import dataclass, field
import time
from typing import TYPE_CHECKING, Any, Literal

from agentpool.log import get_logger


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from anthropic.types.beta import BetaUsage


logger = get_logger(__name__)

HOUR = 3600.0
DEFAULT_DELAY = 0.9 * HOUR
"""Seconds after the request that wrote the cache: a margin before the one-hour TTL."""
LAPSED_AFTER = 60.0
"""A timer this late (e.g. after the machine slept) finds the cache expired already."""
DEFAULT_MIN_TOKENS = 200_000
MIN_TOKENS_FLOOR = 100_000
_TRUTHY = frozenset({"1", "true", "yes", "on"})

type CacheTtl = Literal["1h", "5m"]


def cache_ttl(usage: BetaUsage) -> CacheTtl | None:
    """The TTL a request wrote the prompt cache with (None: it wrote nothing)."""
    match usage.cache_creation:
        case None:
            return None
        case creation if creation.ephemeral_1h_input_tokens > 0:
            return "1h"
        case creation if creation.ephemeral_5m_input_tokens > 0:
            return "5m"
        case _:
            return None


def context_tokens(usage: BetaUsage) -> int:
    """The context a request sent: new, cache-read and cache-written input tokens."""
    return (
        usage.input_tokens
        + (usage.cache_read_input_tokens or 0)
        + (usage.cache_creation_input_tokens or 0)
    )


def _truthy(value: str | None) -> bool:
    return value is not None and value.strip().lower() in _TRUTHY


@dataclass
class IdleCompaction:
    """Schedules ``/compact`` for an idle session whose large cache is about to expire."""

    compact: Callable[[], Awaitable[None]]
    """Sends ``/compact`` to the CLI (the idle watcher reads the CLI's turn)."""

    is_busy: Callable[[], bool]
    """Whether a run, or a turn of the CLI's own, is in progress."""

    settings: Callable[[], Awaitable[Mapping[str, Any]]]
    """The CLI's effective settings."""

    env: Mapping[str, str | None]
    """The environment the CLI runs with (its settings' ``env`` block wins)."""

    clock: Callable[[], float] = time.time
    """Wall time: unlike the event loop's clock, it advances while the machine sleeps."""

    _ttl: CacheTtl | None = None
    _requested_at: float | None = None
    _request_id: str | None = None
    _context_tokens: int | None = None
    _rate_limit_status: str | None = None
    _compacted_since_request: bool = False
    _compacting: bool = False
    _timer: asyncio.TimerHandle | None = None
    _task: asyncio.Task[None] | None = field(default=None, repr=False)

    @property
    def compacting(self) -> bool:
        """Whether a compaction this started has not ended yet."""
        return self._compacting

    def note_request(self, request_id: str, usage: BetaUsage) -> None:
        """A main-conversation request started (its ``message_start``)."""
        self._ttl = cache_ttl(usage) or self._ttl
        self._context_tokens = context_tokens(usage)
        if request_id == self._request_id:
            return
        self._request_id = request_id
        self._requested_at = self.clock()
        self._compacted_since_request = False
        self.disarm()

    def note_rate_limit(self, status: str) -> None:
        """The status of the latest ``rate_limit_event`` (account state: kept by reset)."""
        self._rate_limit_status = status

    def on_idle(self) -> None:
        """The CLI went idle: arm the timer if a compaction may become due."""
        self._compacting = False
        if (reason := self._skip_reason()) is not None:
            logger.debug("Idle compaction not armed", reason=reason)
            return
        assert self._requested_at is not None
        self.disarm()
        delay = max(0.0, self._requested_at + DEFAULT_DELAY - self.clock())
        self._timer = asyncio.get_running_loop().call_later(delay, self._fire)

    def disarm(self) -> None:
        """Drop the armed timer (a new request, a run starting, a steer)."""
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def reset(self) -> None:
        """The conversation was replaced (``/clear``, a new or loaded session)."""
        self.disarm()
        self._ttl = None
        self._requested_at = None
        self._request_id = None
        self._context_tokens = None
        self._compacted_since_request = False

    async def close(self) -> None:
        self.disarm()
        if self._task is not None and not self._task.done():
            self._task.cancel()

    def _fire(self) -> None:
        self._timer = None
        self._task = asyncio.get_running_loop().create_task(self._compact())

    async def _compact(self) -> None:
        if (reason := self._skip_reason()) is not None:
            logger.debug("Idle compaction skipped", reason=reason)
            return
        if (disabled := self._disabled_by(await self.settings())) is not None:
            logger.debug("Idle compaction disabled", by=disabled)
            return
        if self.is_busy():  # A run may have started while the settings were read
            return
        logger.info("Compacting the idle session before its prompt cache expires")
        self._compacting = True
        self._compacted_since_request = True
        try:
            await self.compact()
        except Exception:
            self._compacting = False
            logger.exception("Idle compaction failed")

    def _skip_reason(self) -> str | None:  # noqa: PLR0911
        """Why no compaction may happen now (None: all conditions hold), one rule each."""
        if self._compacting:
            return "compaction running"
        if self._requested_at is None:
            return "no request yet"
        if self.clock() > self._requested_at + DEFAULT_DELAY + LAPSED_AFTER:
            return "cache lapsed"
        if self._rate_limit_status not in (None, "allowed"):
            return f"rate limit status {self._rate_limit_status}"
        if self._compacted_since_request:
            return "compacted since the last request"
        if self._ttl != "1h":
            return f"cache TTL {self._ttl or 'unknown'}"
        if self._context_tokens is None or self._context_tokens < self._min_tokens():
            return f"context of {self._context_tokens} tokens below the minimum"
        if self.is_busy():
            return "busy"
        return None

    def _disabled_by(self, settings: Mapping[str, Any]) -> str | None:
        if settings.get("idleCompaction") is False:
            return "idleCompaction setting"
        if settings.get("autoCompactEnabled") is False:
            return "autoCompactEnabled setting"
        for name in ("DISABLE_COMPACT", "DISABLE_AUTO_COMPACT"):
            if _truthy(self._env(name, settings)):
                return name
        return None

    def _env(self, name: str, settings: Mapping[str, Any] | None = None) -> str | None:
        settings_env = (settings or {}).get("env")
        if isinstance(settings_env, dict) and isinstance(value := settings_env.get(name), str):
            return value
        return self.env.get(name)

    def _min_tokens(self) -> int:
        raw = (self._env("CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS") or "").strip()
        value = int(raw) if raw.isdigit() else 0
        return max(MIN_TOKENS_FLOOR, value or DEFAULT_MIN_TOKENS)
