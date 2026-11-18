"""Process-wide cache of the model catalogue used by native agents.

``tokonomics`` model discovery downloads and parses the whole provider
catalogue on every call (about 10k models for models.dev, several hundred ms
of parsing alone). Agents ask for it on hot paths, such as every ACP prompt
turn to report the context window, so the parsed result is kept here and
shared by all agents that discover from the same providers.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import time
from typing import TYPE_CHECKING, Literal
import weakref


if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import timedelta

    from tokonomics.model_discovery import ProviderType
    from tokonomics.model_discovery.model_info import ModelInfo


type DiscoverySource = ProviderType | Literal["models.dev"]
type _CacheKey = tuple[tuple[DiscoverySource, ...], timedelta]

CATALOG_TTL_SECONDS = 3600.0
"""How long a fetched catalogue is reused before it is fetched again."""


@dataclass(frozen=True, slots=True)
class _Entry:
    models: list[ModelInfo]
    fetched_at: float


_clock = time.monotonic
_cache: dict[_CacheKey, _Entry] = {}
# An asyncio.Lock binds to the loop it first waits in, so locks are per loop.
_locks: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[_CacheKey, asyncio.Lock]] = (
    weakref.WeakKeyDictionary()
)


async def discover_models(
    providers: Sequence[DiscoverySource],
    max_age: timedelta,
) -> list[ModelInfo]:
    """Return the models of ``providers``, fetching them at most once per TTL.

    Concurrent callers for the same providers share one fetch. Empty results
    are not cached: tokonomics reports a failed provider as no models, and
    such a failure (no network, say) should be retried by the next caller.

    Args:
        providers: Providers to discover from, e.g. ``["models.dev"]``.
        max_age: Only include models created within this duration.

    Returns:
        A new list with the discovered models; callers may modify it.
    """
    from tokonomics.model_discovery import get_all_models

    key: _CacheKey = (tuple(providers), max_age)
    if (models := _fresh(key)) is not None:
        return models
    loop_locks = _locks.setdefault(asyncio.get_running_loop(), {})
    async with loop_locks.setdefault(key, asyncio.Lock()):
        if (models := _fresh(key)) is not None:  # Fetched while we waited
            return models
        fetched = await get_all_models(providers=key[0], max_age=max_age)
        if fetched:
            _cache[key] = _Entry(models=fetched, fetched_at=_clock())
        return list(fetched)


def clear_model_catalog() -> None:
    """Drop all cached catalogues, so the next lookup fetches again."""
    _cache.clear()


def _fresh(key: _CacheKey) -> list[ModelInfo] | None:
    entry = _cache.get(key)
    if entry is None or _clock() - entry.fetched_at > CATALOG_TTL_SECONDS:
        return None
    return list(entry.models)
