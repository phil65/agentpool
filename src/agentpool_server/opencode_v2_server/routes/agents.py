"""Agent, model and provider routes."""

from fastapi import APIRouter
from fastapi.responses import Response

from agentpool_server.opencode_v2_server.deps import DirectoryDep, StateDep, V2Response, ok
from agentpool_server.opencode_v2_server.errors import agent_not_found, provider_not_found
from agentpool_server.opencode_v2_server.location import public_ref
from opencode_v2_sdk.models import (
    AgentGetResponse,
    AgentListResponse,
    ModelDefaultResponse,
    ModelListResponse,
    ProviderGetResponse,
    ProviderListResponse,
)


router = APIRouter(tags=["agent"], default_response_class=V2Response)


@router.get("/api/agent")
async def agent_list(state: StateDep, directory: DirectoryDep) -> Response:
    """All pool agents and teams."""
    infos = await state.catalog.agent_infos()
    return ok(AgentListResponse(location=public_ref(directory), data=infos))


@router.get("/api/agent/{agent_id}")
async def agent_get(agent_id: str, state: StateDep, directory: DirectoryDep) -> Response:
    """One pool agent or team."""
    node = state.catalog.get_node(agent_id)
    if node is None:
        raise agent_not_found(agent_id)
    info = state.catalog.agent_info(node, await state.catalog.current_model_ref(node))
    return ok(AgentGetResponse(location=public_ref(directory), data=info))


@router.get("/api/model")
async def model_list(state: StateDep, directory: DirectoryDep) -> Response:
    """Models of all agents."""
    models = await state.catalog.all_models()
    return ok(ModelListResponse(location=public_ref(directory), data=models))


@router.get("/api/model/default")
async def model_default(state: StateDep, directory: DirectoryDep) -> Response:
    """The model of the default agent."""
    node = state.pool.main_agent
    ref = await state.catalog.current_model_ref(node)
    models = (await state.catalog.models_for(node)).models
    model = next((m for m in models if (m.provider_id, m.id) == (ref.provider_id, ref.id)), None)
    return ok(ModelDefaultResponse(location=public_ref(directory), data=model))


@router.get("/api/provider")
async def provider_list(state: StateDep, directory: DirectoryDep) -> Response:
    """Providers of all known models."""
    providers = await state.catalog.providers()
    return ok(ProviderListResponse(location=public_ref(directory), data=providers))


@router.get("/api/provider/{provider_id}")
async def provider_get(provider_id: str, state: StateDep, directory: DirectoryDep) -> Response:
    """One provider."""
    for provider in await state.catalog.providers():
        if provider.id == provider_id:
            return ok(ProviderGetResponse(location=public_ref(directory), data=provider))
    raise provider_not_found(provider_id)
