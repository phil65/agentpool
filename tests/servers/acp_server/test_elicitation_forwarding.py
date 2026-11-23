"""Tests for forwarding MCP elicitation to ACP clients."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from aclientp.schema import (
    AllowedOutcome,
    ClientCapabilities,
    ElicitationCapabilities,
    ElicitationFormCapabilities,
    ElicitationRequest,
    ElicitationResponse,
    ElicitationUrlCapabilities,
    RequestPermissionResponse,
)
from mcp import types

from agentpool_server.acp_server.input_provider import ACPInputProvider


FORM_SCHEMA = {
    "type": "object",
    "properties": {"name": {"type": "string"}, "count": {"type": "integer"}},
    "required": ["name"],
}


def _provider(caps: ClientCapabilities, response: ElicitationResponse | None = None) -> Any:
    requests = SimpleNamespace(
        elicit=AsyncMock(return_value=response),
        request_permission=AsyncMock(
            return_value=RequestPermissionResponse(outcome=AllowedOutcome(option_id="false"))
        ),
    )
    client = SimpleNamespace(elicitation_complete=AsyncMock())
    session: Any = SimpleNamespace(client_capabilities=caps, requests=requests, client=client)
    return ACPInputProvider(session)


def _form_caps() -> ClientCapabilities:
    elicitation = ElicitationCapabilities(form=ElicitationFormCapabilities())
    return ClientCapabilities(elicitation=elicitation)


async def test_form_elicitation_is_forwarded() -> None:
    response = ElicitationResponse.accept({"name": "x", "count": 2})
    provider = _provider(_form_caps(), response)
    params = types.ElicitRequestFormParams(message="Who?", requested_schema=FORM_SCHEMA)

    result = await provider.get_elicitation(params, tool_call_id="tc1")

    assert result == types.ElicitResult(action="accept", content={"name": "x", "count": 2})
    request: ElicitationRequest = provider.session.requests.elicit.call_args.args[0]
    assert request.mode == "form"
    assert request.tool_call_id == "tc1"
    assert request.requested_schema
    assert set(request.requested_schema.properties) == {"name", "count"}
    provider.session.requests.request_permission.assert_not_called()


async def test_single_value_schema_is_forwarded_as_a_value_field() -> None:
    """The question tool asks for one value: a form with a "value" field, not accept/decline."""
    provider = _provider(_form_caps(), ElicitationResponse.accept({"value": "blue"}))
    params = types.ElicitRequestFormParams(message="Colour?", requested_schema={"type": "string"})

    result = await provider.get_elicitation(params, tool_call_id="tc1")

    assert result == types.ElicitResult(action="accept", content={"value": "blue"})
    request: ElicitationRequest = provider.session.requests.elicit.call_args.args[0]
    assert request.requested_schema
    assert list(request.requested_schema.properties) == ["value"]
    assert request.requested_schema.required == ["value"]
    provider.session.requests.request_permission.assert_not_called()


async def test_url_elicitation_needs_url_capability() -> None:
    params = types.ElicitRequestURLParams(
        message="Log in", url="https://example.com", elicitation_id="e1"
    )
    provider = _provider(_form_caps(), ElicitationResponse.decline())
    assert provider._to_acp_elicitation(params) is None

    caps = ClientCapabilities(elicitation=ElicitationCapabilities(url=ElicitationUrlCapabilities()))
    provider = _provider(caps, ElicitationResponse.decline())
    result = await provider.get_elicitation(params)
    assert result == types.ElicitResult(action="decline")
    request: ElicitationRequest = provider.session.requests.elicit.call_args.args[0]
    assert (request.mode, request.elicitation_id, request.url) == (
        "url",
        "e1",
        "https://example.com",
    )


async def test_url_elicitation_completion_is_relayed() -> None:
    caps = ClientCapabilities(elicitation=ElicitationCapabilities(url=ElicitationUrlCapabilities()))
    provider = _provider(caps, ElicitationResponse.accept())
    params = types.ElicitRequestURLParams(
        message="Log in", url="https://example.com", elicitation_id="e1"
    )
    await provider.get_elicitation(params)

    await provider.elicitation_complete("unknown")
    await provider.elicitation_complete("e1")
    await provider.elicitation_complete("e1")

    complete = provider.session.client.elicitation_complete
    assert complete.call_count == 1
    assert complete.call_args.args[0].elicitation_id == "e1"


async def test_url_elicitation_without_client_support_is_declined() -> None:
    provider = _provider(ClientCapabilities())
    params = types.ElicitRequestURLParams(
        message="Log in", url="https://example.com", elicitation_id="e1"
    )
    assert await provider.get_elicitation(params) == types.ElicitResult(action="decline")
    provider.session.requests.request_permission.assert_not_called()


async def test_unsupported_schema_falls_back_to_permission_options() -> None:
    nested = {"type": "object", "properties": {"a": {"type": "object"}}}
    provider = _provider(_form_caps())
    params = types.ElicitRequestFormParams(message="Nested?", requested_schema=nested)
    assert provider._to_acp_elicitation(params) is None


async def test_fallback_boolean_reports_declined_value() -> None:
    provider = _provider(ClientCapabilities())
    params = types.ElicitRequestFormParams(message="Ok?", requested_schema={"type": "boolean"})

    result = await provider.get_elicitation(params)

    assert result == types.ElicitResult(action="accept", content={"value": False})
    provider.session.requests.elicit.assert_not_called()
