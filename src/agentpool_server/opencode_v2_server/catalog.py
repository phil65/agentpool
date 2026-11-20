"""Agents, models and providers of the pool in OpenCode V2 form.

In V2, agent and model are session state. Every pool node (agents and teams) becomes an
``Agent.Info``. Models come from each agent's model discovery
(``get_available_models``). Agents without model discovery (and teams) get a synthetic
model ``agentpool/<agent name>`` so that ``Session.Message.Assistant.model`` can always
be filled.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import time
from typing import TYPE_CHECKING, Any

from agentpool.agents.base_agent import BaseAgent
from agentpool.delegation.base_team import BaseTeam
from agentpool.log import get_logger
from opencode_v2_sdk.models import (
    AgentInfo,
    ModelCapabilities,
    ModelCost,
    ModelCostCache,
    ModelInfo,
    ModelInfoLimit,
    ModelInfoTime,
    ModelRef,
    ModelVariant,
    PermissionRule,
    ProviderInfo,
    ProviderRequest,
    ProviderSettings,
)


if TYPE_CHECKING:
    from tokonomics.model_discovery.model_info import ModelInfo as TokoModelInfo

    from agentpool.delegation import AgentPool
    from agentpool.messaging import MessageNode


logger = get_logger(__name__)

SYNTHETIC_PROVIDER = "agentpool"
"""Provider ID used for synthetic models of agents without model selection."""

MODEL_CACHE_TTL = 300.0
"""Seconds to cache an agent's discovered models."""

VARIANT_CATEGORIES = ("thought_level", "effort")
"""Mode categories exposed as V2 model variants."""

PROVIDER_NAMES: dict[str, str] = {
    "anthropic": "Anthropic",
    "openai": "OpenAI",
    "google": "Google",
    "google-gla": "Google",
    "google-vertex": "Google Vertex AI",
    "mistral": "Mistral",
    "groq": "Groq",
    "deepseek": "DeepSeek",
    "xai": "xAI",
    "together": "Together AI",
    "perplexity": "Perplexity",
    "cohere": "Cohere",
    "fireworks": "Fireworks AI",
    "openrouter": "OpenRouter",
    "bedrock": "AWS Bedrock",
    "azure": "Azure OpenAI",
    SYNTHETIC_PROVIDER: "AgentPool",
}


def model_ref_key(ref: ModelRef) -> tuple[str, str]:
    """Hashable identity of a model reference (variant excluded)."""
    return (ref.provider_id, ref.id)


@dataclass
class AgentModels:
    """Models selectable for one pool node."""

    models: list[ModelInfo]
    """Models in V2 form."""
    targets: dict[tuple[str, str], str]
    """(providerID, id) -> identifier passed to ``agent.set_model``."""
    synthetic: bool
    """Whether the node has no model selection (single synthetic model)."""
    fetched_at: float = field(default_factory=time.monotonic)


def _synthetic_model(node_name: str, display: str) -> ModelInfo:
    return ModelInfo(
        id=node_name,
        model_id=node_name,
        provider_id=SYNTHETIC_PROVIDER,
        name=display,
        capabilities=ModelCapabilities(tools=True, input=["text"], output=["text"]),
        variants=[],
        time=ModelInfoTime(released=0),
        cost=[ModelCost(input=0, output=0, cache=ModelCostCache(read=0, write=0))],
        status="active",
        enabled=True,
        limit=ModelInfoLimit(context=0, output=0),
    )


def synthetic_ref(node_name: str) -> ModelRef:
    """Synthetic model reference of a node without model selection."""
    return ModelRef(id=node_name, provider_id=SYNTHETIC_PROVIDER)


def toko_model_ref(model: TokoModelInfo) -> ModelRef:
    """V2 model reference of a tokonomics model."""
    return ModelRef(id=model.id_override or model.id, provider_id=model.provider or "unknown")


def toko_to_v2_model(model: TokoModelInfo, variants: list[str]) -> ModelInfo:
    """Convert a tokonomics model into a V2 ``Model.Info``."""
    from tokonomics.model_discovery.model_info import ModelPricing

    ref = toko_model_ref(model)
    pricing = model.pricing or ModelPricing()
    per_million = 1_000_000
    cost = ModelCost(
        input=(pricing.prompt or 0.0) * per_million,
        output=(pricing.completion or 0.0) * per_million,
        cache=ModelCostCache(
            read=(pricing.input_cache_read or 0.0) * per_million,
            write=(pricing.input_cache_write or 0.0) * per_million,
        ),
    )
    released = model.created_at.timestamp() * 1000 if model.created_at else 0
    return ModelInfo(
        id=ref.id,
        model_id=model.id,
        provider_id=ref.provider_id,
        family=model.owned_by,
        name=model.name,
        capabilities=ModelCapabilities(
            tools=True,
            input=sorted(model.input_modalities) or ["text"],
            output=sorted(model.output_modalities) or ["text"],
            temperature=True,
        ),
        variants=[ModelVariant(id=v) for v in variants],
        time=ModelInfoTime(released=released),
        cost=[cost],
        status="deprecated" if model.is_deprecated else "active",
        enabled=True,
        limit=ModelInfoLimit(
            context=model.context_window or 0, output=model.max_output_tokens or 0
        ),
    )


async def agent_variants(agent: BaseAgent[Any, Any]) -> list[str]:
    """Thinking/effort levels of an agent, exposed as model variants."""
    try:
        categories = await agent.get_modes()
    except Exception:
        logger.debug("Could not read agent modes", agent=agent.name, exc_info=True)
        return []
    for category in categories:
        if category.id in VARIANT_CATEGORIES:
            return [str(mode.value) for mode in category.available_modes]
    return []


async def variant_category(agent: BaseAgent[Any, Any]) -> str | None:
    """The mode category that model variants map to, if any."""
    try:
        categories = await agent.get_modes()
    except Exception:  # noqa: BLE001
        return None
    return next((c.id for c in categories if c.id in VARIANT_CATEGORIES), None)


def leaf_agents(node: MessageNode[Any, Any]) -> list[BaseAgent[Any, Any]]:
    """All agents a node runs (the node itself, or the members of a team, recursively)."""
    match node:
        case BaseAgent():
            return [node]
        case BaseTeam():
            agents: list[BaseAgent[Any, Any]] = []
            for member in node.nodes:
                for agent in leaf_agents(member):
                    if agent not in agents:
                        agents.append(agent)
            return agents
        case _:
            return []


def _match_target(targets: dict[tuple[str, str], str], identifier: str) -> tuple[str, str] | None:
    """Key of the target matching a model identifier (``provider:model``, ID or target)."""
    for key, target in targets.items():
        if identifier in (target, key[1], f"{key[0]}:{key[1]}"):
            return key
    return None


def _ref_from_identifier(identifier: str) -> ModelRef:
    provider, sep, model_id = identifier.partition(":")
    if sep:
        return ModelRef(provider_id=provider, id=model_id)
    return ModelRef(provider_id=SYNTHETIC_PROVIDER, id=identifier)


def _configured_model(ref: ModelRef, identifier: str) -> ModelInfo:
    model = _synthetic_model(ref.id, identifier)
    return model.model_copy(update={"provider_id": ref.provider_id, "model_id": ref.id})


@dataclass
class Catalog:
    """Cached V2 views of the pool's nodes and their models."""

    pool: AgentPool[Any]
    _models: dict[str, AgentModels] = field(default_factory=dict)
    _locks: dict[str, asyncio.Lock] = field(default_factory=dict)

    # --- nodes ---------------------------------------------------------------------------

    def nodes(self) -> dict[str, MessageNode[Any, Any]]:
        """Pool nodes that can drive a session (agents and teams)."""
        nodes: dict[str, MessageNode[Any, Any]] = dict(self.pool.all_agents)
        nodes.update(self.pool.teams)
        return nodes

    def get_node(self, name: str) -> MessageNode[Any, Any] | None:
        """Get a pool node by name."""
        return self.nodes().get(name)

    def default_agent_name(self) -> str:
        """Name of the pool's main agent."""
        return self.pool.main_agent.name

    def agent_info(self, node: MessageNode[Any, Any], model: ModelRef | None) -> AgentInfo:
        """V2 ``Agent.Info`` of a pool node."""
        description = node.description
        if isinstance(node, BaseTeam):
            members = ", ".join(n.name for n in node.nodes)
            description = description or f"Team: {members}"
        return AgentInfo(
            id=node.name,
            name=node.display_name,
            model=model,
            request=ProviderRequest(settings=ProviderSettings(), headers={}, body={}),
            description=description,
            mode="primary",
            hidden=False,
            permissions=[PermissionRule(action="*", resource="*", effect="allow")],
        )

    async def agent_infos(self) -> list[AgentInfo]:
        """``Agent.Info`` for every pool node."""
        return [
            self.agent_info(node, await self.current_model_ref(node))
            for node in self.nodes().values()
        ]

    # --- models --------------------------------------------------------------------------

    async def models_for(self, node: MessageNode[Any, Any]) -> AgentModels:
        """Selectable models of a node (cached)."""
        cached = self._models.get(node.name)
        if cached is not None and time.monotonic() - cached.fetched_at < MODEL_CACHE_TTL:
            return cached
        lock = self._locks.setdefault(node.name, asyncio.Lock())
        async with lock:
            cached = self._models.get(node.name)
            if cached is not None and time.monotonic() - cached.fetched_at < MODEL_CACHE_TTL:
                return cached
            result = await self._discover(node)
            self._models[node.name] = result
            return result

    async def _discover(self, node: MessageNode[Any, Any]) -> AgentModels:
        synthetic = AgentModels(
            models=[_synthetic_model(node.name, node.display_name)],
            targets={},
            synthetic=True,
        )
        if not isinstance(node, BaseAgent):
            return synthetic
        try:
            discovered = await node.get_available_models()
        except Exception:
            logger.warning("Model discovery failed", agent=node.name, exc_info=True)
            discovered = None
        if not discovered:
            return synthetic
        variants = await agent_variants(node)
        models: list[ModelInfo] = []
        targets: dict[tuple[str, str], str] = {}
        for toko in discovered:
            if toko.is_embedding:
                continue
            ref = toko_model_ref(toko)
            if model_ref_key(ref) in targets:
                continue
            targets[model_ref_key(ref)] = toko.pydantic_ai_id
            models.append(toko_to_v2_model(toko, variants))
        # The configured model must always be selectable, even if discovery doesn't list it
        # (custom/test models, fallback models, ...).
        if (current := node.model_name) and _match_target(targets, current) is None:
            ref = _ref_from_identifier(current)
            targets[model_ref_key(ref)] = current
            models.insert(0, _configured_model(ref, current))
        return AgentModels(models=models, targets=targets, synthetic=False)

    async def all_models(self) -> list[ModelInfo]:
        """Union of all nodes' models (deduplicated)."""
        seen: set[tuple[str, str]] = set()
        result: list[ModelInfo] = []
        for node in self.nodes().values():
            for model in (await self.models_for(node)).models:
                key = (model.provider_id, model.id)
                if key not in seen:
                    seen.add(key)
                    result.append(model)
        return result

    async def providers(self) -> list[ProviderInfo]:
        """Providers of all known models."""
        provider_ids = sorted({m.provider_id for m in await self.all_models()})
        return [
            ProviderInfo(
                id=pid,
                name=PROVIDER_NAMES.get(pid, pid.replace("-", " ").title()),
                activation="enabled",
                package=pid,
            )
            for pid in provider_ids
        ]

    async def current_model_ref(self, node: MessageNode[Any, Any]) -> ModelRef:
        """Model reference matching the node's currently configured model."""
        models = await self.models_for(node)
        if models.synthetic or not isinstance(node, BaseAgent):
            return synthetic_ref(node.name)
        current = node.model_name
        if current:
            if (key := _match_target(models.targets, current)) is not None:
                return ModelRef(provider_id=key[0], id=key[1])
            return _ref_from_identifier(current)
        return synthetic_ref(node.name)

    async def resolve_model(self, node: MessageNode[Any, Any], ref: ModelRef) -> str | None:
        """Identifier to pass to ``set_model`` for a reference, or None if not selectable.

        For nodes without model selection only their synthetic reference is accepted, in
        which case the empty string is returned (nothing to switch).
        """
        models = await self.models_for(node)
        if models.synthetic:
            return "" if model_ref_key(ref) == model_ref_key(synthetic_ref(node.name)) else None
        return models.targets.get(model_ref_key(ref))

    def invalidate(self, node_name: str | None = None) -> None:
        """Drop cached models (of one node, or all)."""
        if node_name is None:
            self._models.clear()
        else:
            self._models.pop(node_name, None)
