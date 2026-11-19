"""Tests for the v2 JSON-RPC layer: method tables and envelopes."""

from __future__ import annotations

from typing import get_args

from pydantic import TypeAdapter

from acp.schema.v2 import (
    AGENT_NOTIFICATIONS,
    AGENT_REQUESTS,
    CLIENT_NOTIFICATIONS,
    CLIENT_REQUESTS,
    MISSING,
    PROTOCOL_NOTIFICATIONS,
    PROXY_NOTIFICATIONS,
    PROXY_REQUESTS,
    AgentMethod,
    ClientMethod,
    ClientRequest,
    ErrorResponse,
    PromptRequest,
    ProtocolMethod,
    ProxyMethod,
    ResultResponse,
    SetConfigOptionIdRequest,
    to_wire,
)


def test_method_tables_cover_the_method_lists() -> None:
    assert set(AGENT_REQUESTS) | set(AGENT_NOTIFICATIONS) == set(get_args(AgentMethod))
    assert set(CLIENT_REQUESTS) | set(CLIENT_NOTIFICATIONS) == set(get_args(ClientMethod))
    assert set(PROTOCOL_NOTIFICATIONS) == set(get_args(ProtocolMethod))
    assert set(PROXY_REQUESTS) | set(PROXY_NOTIFICATIONS) == set(get_args(ProxyMethod))


def test_params_are_parsed_by_method() -> None:
    request = ClientRequest.model_validate({
        "jsonrpc": "2.0",
        "id": 7,
        "method": "session/set_config_option",
        "params": {"sessionId": "s1", "configId": "model", "type": "id", "value": "fast"},
    })

    params = AGENT_REQUESTS[request.method].params.validate_python(request.params)

    assert isinstance(params, SetConfigOptionIdRequest)
    assert params.value == "fast"


def test_request_envelope_without_params() -> None:
    request = ClientRequest(id="r1", method="auth/logout")

    assert request.params is MISSING
    assert to_wire(request) == {"jsonrpc": "2.0", "id": "r1", "method": "auth/logout"}


def test_responses() -> None:
    responses: TypeAdapter[ResultResponse | ErrorResponse] = TypeAdapter(
        ResultResponse | ErrorResponse
    )

    ok = responses.validate_python({"jsonrpc": "2.0", "id": 1, "result": {"messageId": "m1"}})
    failed = responses.validate_python({
        "jsonrpc": "2.0",
        "id": None,
        "error": {"code": -32600, "message": "Invalid request"},
    })

    assert isinstance(ok, ResultResponse)
    assert isinstance(failed, ErrorResponse)
    assert failed.error.code == -32600


def test_prompt_needs_content() -> None:
    adapter = AGENT_REQUESTS["session/prompt"].params

    prompt = adapter.validate_python({
        "sessionId": "s1",
        "prompt": [{"type": "text", "text": "hi"}],
    })

    assert isinstance(prompt, PromptRequest)
