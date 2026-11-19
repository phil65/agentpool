"""Tests for v2 initialize, capabilities and auth models."""

from __future__ import annotations

from pydantic import MISSING

from acp.schema.v2.auth import AuthMethodAgent, AuthMethodTerminal, UnknownAuthMethod
from acp.schema.v2.base import is_set, to_wire
from acp.schema.v2.capabilities import (
    ClientAuthCapabilities,
    ClientCapabilities,
    SessionCapabilities,
    SessionForkCapabilities,
)
from acp.schema.v2.common import Implementation
from acp.schema.v2.initialize import InitializeRequest, InitializeResponse


def test_initialize_response_with_auth_methods() -> None:
    response = InitializeResponse.model_validate({
        "protocolVersion": 2,
        "info": {"name": "claude-agent-acp", "title": "Claude Agent", "version": "0.108.1"},
        "capabilities": {"session": {"prompt": {"image": {}}, "fork": {}}},
        "authMethods": [
            {"type": "terminal", "methodId": "login", "name": "Login", "args": ["--login"]},
            {"type": "agent", "methodId": "gateway", "name": "Gateway"},
            {"type": "_custom", "methodId": "x", "name": "Custom", "extra": True},
        ],
    })

    methods = response.auth_methods
    assert is_set(methods)
    assert [type(m) for m in methods] == [AuthMethodTerminal, AuthMethodAgent, UnknownAuthMethod]
    capabilities = response.capabilities
    assert is_set(capabilities)
    session = capabilities.session
    assert isinstance(session, SessionCapabilities)
    assert isinstance(session.fork, SessionForkCapabilities)
    assert session.delete is MISSING


def test_initialize_request_serializes_only_set_fields() -> None:
    request = InitializeRequest(
        protocol_version=2,
        info=Implementation(name="agentpool", version="1.0"),
        capabilities=ClientCapabilities(auth=ClientAuthCapabilities()),
    )

    assert to_wire(request) == {
        "protocolVersion": 2,
        "info": {"name": "agentpool", "version": "1.0"},
        "capabilities": {"auth": {}},
    }


def test_missing_is_truthy_so_is_set_must_be_used() -> None:
    response = InitializeResponse.model_validate({
        "protocolVersion": 2,
        "info": {"name": "agent", "version": "1"},
    })

    assert response.capabilities  # truthy although absent
    assert not is_set(response.capabilities)
