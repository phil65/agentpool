"""**UNSTABLE**: Proxy chain types (``proxy/initialize``, ``proxy/successor``).

A conductor chains components (proxies) in front of a final agent. It sends
``proxy/initialize`` instead of ``initialize`` to every component that has a successor,
and components reach their successor by wrapping messages in ``proxy/successor``.
"""

from __future__ import annotations

from typing import Any

from acp.schema.agent_responses import InitializeResponse
from acp.schema.base import AnnotatedObject
from acp.schema.client_requests import InitializeRequest


class InitializeProxyRequest(InitializeRequest):
    """Request parameters for ``proxy/initialize``. Same as ``initialize``."""


class InitializeProxyResponse(InitializeResponse):
    """Response to ``proxy/initialize``. Same as the ``initialize`` response.

    The proxy MUST NOT change ``protocol_version``.
    """


class ProxySuccessorRequest(AnnotatedObject):
    """A request wrapped for delivery to the proxy's successor.

    The result is the wrapped request's result, forwarded unchanged.
    ``field_meta`` belongs to the envelope and is hop-local.
    """

    method: str
    """Method name of the wrapped request."""

    params: Any = None
    """Params of the wrapped request, forwarded unchanged."""


class ProxySuccessorNotification(AnnotatedObject):
    """A notification wrapped for delivery to the proxy's successor."""

    method: str
    """Method name of the wrapped notification."""

    params: Any = None
    """Params of the wrapped notification, forwarded unchanged."""
