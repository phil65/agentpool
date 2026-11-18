"""Tests for the process-wide model catalogue cache of native agents."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import TYPE_CHECKING

from pydantic_ai import RunUsage
import pytest
import tokonomics
from tokonomics import model_discovery
from tokonomics.model_discovery.model_info import ModelInfo

from agentpool import Agent
from agentpool.agents.native_agent import model_catalog
from agentpool.agents.native_agent.model_catalog import clear_model_catalog, discover_models
from agentpool.messaging.messages import TokenCost


if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from tokonomics.model_discovery import ProviderType


MAX_AGE = timedelta(days=200)


class FakeDiscovery:
    """Stand-in for tokonomics' get_all_models that counts its fetches."""

    def __init__(self, models: list[ModelInfo]) -> None:
        self.models = models
        self.calls: list[tuple[str, ...]] = []
        self.release = asyncio.Event()
        self.release.set()

    async def __call__(
        self,
        *,
        providers: Sequence[ProviderType | str] | None = None,
        include_deprecated: bool = False,
        max_age: timedelta | None = None,
    ) -> list[ModelInfo]:
        self.calls.append(tuple(providers or ()))
        await self.release.wait()
        return list(self.models)


@pytest.fixture(autouse=True)
def _empty_catalog() -> Iterator[None]:
    clear_model_catalog()
    yield
    clear_model_catalog()


@pytest.fixture
def discovery(monkeypatch: pytest.MonkeyPatch) -> FakeDiscovery:
    fake = FakeDiscovery([ModelInfo(id="gpt-x", name="GPT X", context_window=1000)])
    monkeypatch.setattr(model_discovery, "get_all_models", fake)
    return fake


async def test_catalogue_is_fetched_once(discovery: FakeDiscovery) -> None:
    first = await discover_models(["models.dev"], max_age=MAX_AGE)
    second = await discover_models(["models.dev"], max_age=MAX_AGE)
    assert [m.id for m in first] == [m.id for m in second] == ["gpt-x"]
    assert discovery.calls == [("models.dev",)]


async def test_callers_get_their_own_list(discovery: FakeDiscovery) -> None:
    first = await discover_models(["models.dev"], max_age=MAX_AGE)
    first.clear()
    assert len(await discover_models(["models.dev"], max_age=MAX_AGE)) == 1


async def test_providers_are_cached_separately(discovery: FakeDiscovery) -> None:
    await discover_models(["models.dev"], max_age=MAX_AGE)
    await discover_models(["openai"], max_age=MAX_AGE)
    await discover_models(["openai"], max_age=MAX_AGE)
    assert discovery.calls == [("models.dev",), ("openai",)]


async def test_empty_result_is_retried(discovery: FakeDiscovery) -> None:
    discovery.models = []  # What tokonomics returns when every provider failed
    assert await discover_models(["models.dev"], max_age=MAX_AGE) == []
    discovery.models = [ModelInfo(id="gpt-x", name="GPT X")]
    assert len(await discover_models(["models.dev"], max_age=MAX_AGE)) == 1
    assert len(discovery.calls) == 2


async def test_concurrent_callers_share_one_fetch(discovery: FakeDiscovery) -> None:
    discovery.release.clear()
    lookups = [
        asyncio.create_task(discover_models(["models.dev"], max_age=MAX_AGE)) for _ in range(5)
    ]
    await asyncio.sleep(0)
    discovery.release.set()
    results = await asyncio.gather(*lookups)
    assert all(len(models) == 1 for models in results)
    assert len(discovery.calls) == 1


async def test_catalogue_is_refetched_after_ttl(
    discovery: FakeDiscovery, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = 1000.0
    monkeypatch.setattr(model_catalog, "_clock", lambda: now)
    await discover_models(["models.dev"], max_age=MAX_AGE)
    now += model_catalog.CATALOG_TTL_SECONDS
    await discover_models(["models.dev"], max_age=MAX_AGE)
    assert len(discovery.calls) == 1
    now += 1
    await discover_models(["models.dev"], max_age=MAX_AGE)
    assert len(discovery.calls) == 2


async def test_agents_share_the_catalogue(discovery: FakeDiscovery) -> None:
    first = Agent(name="first", model="test")
    second = Agent(name="second", model="test")
    models = await first.get_available_models()
    assert models is not None
    assert [m.id for m in models] == ["gpt-x"]
    await second.get_available_models()
    assert discovery.calls == [("models.dev",)]


async def test_function_model_usage_is_not_priced(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fail(*args: object, **kwargs: object) -> None:
        pytest.fail("pydantic-ai local models must not be looked up")

    monkeypatch.setattr(tokonomics, "calculate_token_cost", fail)
    usage = RunUsage(input_tokens=10, output_tokens=5)
    cost = await TokenCost.from_usage(usage, "function::stream")
    assert cost is not None
    assert cost.total_cost == 0
