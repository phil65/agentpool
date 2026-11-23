"""Process-wide cache of the model catalogue used by native agents.

``tokonomics`` model discovery parses a provider's whole catalogue (about 10k
models for models.dev). Agents ask for it on hot paths, so the result is kept here
and shared by all agents that discover from the same providers: the whole
catalogue, once, with the release-date cut-off (``max_age``) applied as it is read.
A model picker can leave out old models while a lookup still finds them.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
import time
from typing import TYPE_CHECKING, Literal
import weakref


if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import timedelta

    from tokonomics.model_discovery import ProviderType
    from tokonomics.model_discovery.model_info import ModelInfo


type DiscoverySource = ProviderType | Literal["models.dev"]
type _CacheKey = tuple[DiscoverySource, ...]

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
    max_age: timedelta | None = None,
) -> list[ModelInfo]:
    """Return the models of ``providers``, fetching them at most once per TTL.

    Concurrent callers for the same providers share one fetch. Empty results
    are not cached: tokonomics reports a failed provider as no models, and
    such a failure (no network, say) should be retried by the next caller.

    Args:
        providers: Providers to discover from, e.g. ``["models.dev"]``.
        max_age: Only include models released within this duration (models without
            a release date are kept); None for all.

    Returns:
        A new list with the discovered models; callers may modify it.
    """
    from tokonomics.model_discovery import get_all_models

    key: _CacheKey = tuple(providers)
    if (models := _fresh(key)) is None:
        loop_locks = _locks.setdefault(asyncio.get_running_loop(), {})
        async with loop_locks.setdefault(key, asyncio.Lock()):
            if (models := _fresh(key)) is None:  # Else fetched while we waited
                models = await get_all_models(providers=key)
                if models:
                    _cache[key] = _Entry(models=models, fetched_at=_clock())
    if max_age is None:
        return list(models)
    cutoff = datetime.now() - max_age
    return [m for m in models if m.created_at is None or m.created_at >= cutoff]


def clear_model_catalog() -> None:
    """Drop all cached catalogues, so the next lookup fetches again."""
    _cache.clear()


def _fresh(key: _CacheKey) -> list[ModelInfo] | None:
    entry = _cache.get(key)
    if entry is None or _clock() - entry.fetched_at > CATALOG_TTL_SECONDS:
        return None
    return entry.models
