"""Async HTTP and SSE client for the OpenCode V2 server API (pinned in ``_version.py``)."""

import base64
from typing import TYPE_CHECKING, Any, Literal, Self
from urllib.parse import quote

import httpx2
from pydantic import ValidationError

from opencode_v2_sdk.models import (
    AgentListResponse,
    CredentialListResponse,
    ExperimentalMcpAddBody,
    ExperimentalUsageLimitsResponse,
    FormReply,
    McpListResponse,
    ModelDefaultResponse,
    ModelListResponse,
    ProviderListResponse,
    ServerInfo,
    SessionCompactBody,
    SessionCompactResponse,
    SessionCreateBody,
    SessionCreateResponse,
    SessionForkBody,
    SessionForkResponse,
    SessionGetResponse,
    SessionInterruptResponse,
    SessionMessagesResponse,
    SessionPermissionReplyBody,
    SessionPromptBody,
    SessionPromptResponse,
    SessionsResponse,
    SessionSwitchAgentBody,
    SessionSwitchModelBody,
    SessionUpdateBody,
    V2Event,
)


if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping, Sequence
    from types import TracebackType

    from pydantic import BaseModel

    from opencode_v2_sdk.models import (
        AgentInfo,
        CredentialEntry,
        McpLocalConfigEncoded,
        McpRemoteConfigEncoded,
        McpServer,
        ModelInfo,
        ModelRef,
        ProviderInfo,
        SessionInboxCompaction,
        SessionInboxUser,
        SessionInfo,
        SessionMessageInfo,
        UsageLimitsInfo,
    )


type PublicEvent = Any
"""Unwrapped event instance yielded from ``GET /api/event`` (``V2Event.root``)."""

type PermissionDecision = Literal["once", "always", "reject"]
type FormAnswerValue = str | float | bool | list[str]

AUTH_USERNAME = "opencode"


class OpenCodeV2Error(RuntimeError):
    """HTTP or protocol error returned by an OpenCode V2 server."""

    def __init__(self, message: str, *, status_code: int | None = None, body: str = "") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


def _basic_auth_header(password: str, *, username: str = AUTH_USERNAME) -> str:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return f"Basic {token}"


def _dump_body(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="json", by_alias=True, exclude_none=True)


class OpenCodeV2Client:
    """Typed async HTTP + SSE client for the OpenCode V2 HTTP API.

    Can connect to any OpenCode v2.3.x server (``opencode serve`` or AgentPool's
    ``serve-opencode``) over HTTP, or wrap a caller-supplied ``httpx2.AsyncClient``
    (e.g. with ``ASGITransport`` or ``MockTransport`` in tests).
    """

    def __init__(
        self,
        base_url: str,
        *,
        password: str | None = None,
        directory: str | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float = 30.0,
        http_client: httpx2.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._directory = directory
        self._owns_client = http_client is None
        merged_headers: dict[str, str] = {"Accept": "application/json"}
        if password:
            merged_headers["Authorization"] = _basic_auth_header(password)
        if directory:
            merged_headers["x-opencode-directory"] = quote(directory, safe="/:")
        if headers:
            merged_headers.update(headers)
        self._headers = merged_headers
        self._timeout = timeout
        self._client = http_client

    @property
    def base_url(self) -> str:
        """Normalized base URL of the OpenCode V2 server."""
        return self._base_url

    def _require_client(self) -> httpx2.AsyncClient:
        if self._client is None:
            self._client = httpx2.AsyncClient(
                base_url=self._base_url,
                headers=self._headers,
                timeout=httpx2.Timeout(self._timeout),
            )
        return self._client

    async def __aenter__(self) -> Self:
        self._require_client()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the underlying HTTP client if owned by this instance."""
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> httpx2.Response:
        client = self._require_client()
        clean_params = {k: v for k, v in (params or {}).items() if v is not None}
        response = await client.request(
            method,
            path,
            params=clean_params or None,
            json=json_body,
            # Per request too: a caller-supplied client lacks the auth / directory headers
            headers=self._headers,
        )
        if response.status_code >= 400:  # noqa: PLR2004
            msg = f"OpenCode V2 {method} {path} failed with HTTP {response.status_code}"
            if text := response.text.strip():
                msg = f"{msg}: {text[:400]}"
            raise OpenCodeV2Error(msg, status_code=response.status_code, body=response.text)
        return response

    # --- Server & discovery -----------------------------------------------------------

    async def info(self) -> ServerInfo:
        """Fetch server identity and capabilities (``GET /api/info``)."""
        resp = await self._request("GET", "/api/info")
        return ServerInfo.model_validate_json(resp.content)

    async def list_agents(self) -> list[AgentInfo]:
        """List agents exposed by the server (``GET /api/agent``)."""
        resp = await self._request("GET", "/api/agent")
        return AgentListResponse.model_validate_json(resp.content).data

    async def list_models(self) -> list[ModelInfo]:
        """List available models (``GET /api/model``)."""
        resp = await self._request("GET", "/api/model")
        return ModelListResponse.model_validate_json(resp.content).data

    async def default_model(self) -> ModelInfo | None:
        """Return the server's default model (``GET /api/model/default``)."""
        resp = await self._request("GET", "/api/model/default")
        return ModelDefaultResponse.model_validate_json(resp.content).data

    async def list_providers(self) -> list[ProviderInfo]:
        """List configured model providers (``GET /api/provider``)."""
        resp = await self._request("GET", "/api/provider")
        return ProviderListResponse.model_validate_json(resp.content).data

    async def list_credentials(self) -> list[CredentialEntry]:
        """List stored provider credentials (``GET /api/credential``)."""
        resp = await self._request("GET", "/api/credential")
        return CredentialListResponse.model_validate_json(resp.content).data

    async def usage_limits(self) -> UsageLimitsInfo | None:
        """Fetch plan usage limits (``GET /api/experimental/usage/limits``)."""
        resp = await self._request("GET", "/api/experimental/usage/limits")
        return ExperimentalUsageLimitsResponse.model_validate_json(resp.content).data

    # --- MCP servers ------------------------------------------------------------------

    async def list_mcp_servers(self) -> list[McpServer]:
        """List MCP servers configured on the server (``GET /api/mcp``)."""
        resp = await self._request("GET", "/api/mcp")
        return McpListResponse.model_validate_json(resp.content).data

    async def add_mcp_server(
        self,
        name: str,
        config: McpLocalConfigEncoded | McpRemoteConfigEncoded,
    ) -> None:
        """Register an MCP server (``PUT /api/experimental/mcp/{name}``)."""
        body = ExperimentalMcpAddBody(config=config)
        await self._request(
            "PUT",
            f"/api/experimental/mcp/{quote(name, safe='')}",
            json_body=_dump_body(body),
        )

    async def connect_mcp_server(self, name: str) -> None:
        """Connect a registered MCP server (``POST /api/experimental/mcp/{name}/connect``)."""
        await self._request("POST", f"/api/experimental/mcp/{quote(name, safe='')}/connect")

    async def disconnect_mcp_server(self, name: str) -> None:
        """Disconnect an MCP server (``POST /api/experimental/mcp/{name}/disconnect``)."""
        await self._request("POST", f"/api/experimental/mcp/{quote(name, safe='')}/disconnect")

    async def remove_mcp_server(self, name: str) -> None:
        """Remove a registered MCP server (``DELETE /api/experimental/mcp/{name}``)."""
        await self._request("DELETE", f"/api/experimental/mcp/{quote(name, safe='')}")

    # --- Sessions ---------------------------------------------------------------------

    async def create_session(self, body: SessionCreateBody | None = None) -> SessionInfo:
        """Create a new session (``POST /api/session``)."""
        payload = _dump_body(body) if body is not None else {}
        resp = await self._request("POST", "/api/session", json_body=payload)
        return SessionCreateResponse.model_validate_json(resp.content).data

    async def get_session(self, session_id: str) -> SessionInfo:
        """Fetch session info by ID (``GET /api/session/{sessionID}``)."""
        resp = await self._request("GET", f"/api/session/{quote(session_id, safe='')}")
        return SessionGetResponse.model_validate_json(resp.content).data

    async def list_sessions(
        self,
        *,
        limit: int | None = None,
        order: Literal["asc", "desc"] | None = None,
        search: str | None = None,
        parent_id: str | None = None,
        archived: Literal["true", "false"] | None = None,
        directory: str | None = None,
        cursor: str | None = None,
    ) -> SessionsResponse:
        """List sessions (``GET /api/session``)."""
        resp = await self._request(
            "GET",
            "/api/session",
            params={
                "limit": str(limit) if limit is not None else None,
                "order": order,
                "search": search,
                "parentID": parent_id,
                "archived": archived,
                "directory": directory,
                "cursor": cursor,
            },
        )
        return SessionsResponse.model_validate_json(resp.content)

    async def update_session(self, session_id: str, body: SessionUpdateBody) -> None:
        """Update session title, metadata, permissions, or archived state."""
        await self._request(
            "PATCH",
            f"/api/session/{quote(session_id, safe='')}",
            json_body=_dump_body(body),
        )

    async def delete_session(self, session_id: str) -> None:
        """Delete a session (``DELETE /api/session/{sessionID}``)."""
        await self._request("DELETE", f"/api/session/{quote(session_id, safe='')}")

    async def switch_agent(self, session_id: str, agent: str) -> None:
        """Switch the active agent on a session (``POST /api/session/{sessionID}/agent``)."""
        body = SessionSwitchAgentBody(agent=agent)
        await self._request(
            "POST",
            f"/api/session/{quote(session_id, safe='')}/agent",
            json_body=_dump_body(body),
        )

    async def switch_model(self, session_id: str, model: ModelRef) -> None:
        """Switch the active model on a session (``POST /api/session/{sessionID}/model``)."""
        body = SessionSwitchModelBody(model=model)
        await self._request(
            "POST",
            f"/api/session/{quote(session_id, safe='')}/model",
            json_body=_dump_body(body),
        )

    async def send_prompt(
        self,
        session_id: str,
        body: SessionPromptBody,
    ) -> SessionInboxUser:
        """Enqueue a user prompt on a session (``POST /api/session/{sessionID}/prompt``)."""
        resp = await self._request(
            "POST",
            f"/api/session/{quote(session_id, safe='')}/prompt",
            json_body=_dump_body(body),
        )
        return SessionPromptResponse.model_validate_json(resp.content).data

    async def interrupt_session(self, session_id: str, *, resume: bool = False) -> bool:
        """Interrupt a running execution (``POST /api/session/{sessionID}/interrupt``)."""
        resp = await self._request(
            "POST",
            f"/api/session/{quote(session_id, safe='')}/interrupt",
            params={"resume": "true" if resume else "false"},
        )
        return SessionInterruptResponse.model_validate_json(resp.content).interrupted

    async def compact_session(
        self,
        session_id: str,
        *,
        delivery: Literal["steer", "queue"] | None = None,
    ) -> SessionInboxCompaction:
        """Queue a manual compaction (``POST /api/session/{sessionID}/compact``)."""
        body = SessionCompactBody(delivery=delivery)
        resp = await self._request(
            "POST",
            f"/api/session/{quote(session_id, safe='')}/compact",
            json_body=_dump_body(body),
        )
        return SessionCompactResponse.model_validate_json(resp.content).data

    async def fork_session(
        self,
        session_id: str,
        *,
        before: str | None = None,
    ) -> SessionInfo:
        """Fork a session, optionally truncating before ``before`` message ID."""
        body = SessionForkBody(before=before)
        resp = await self._request(
            "POST",
            f"/api/session/{quote(session_id, safe='')}/fork",
            json_body=_dump_body(body),
        )
        return SessionForkResponse.model_validate_json(resp.content).data

    async def list_messages(
        self,
        session_id: str,
        *,
        limit: int | None = None,
        order: Literal["asc", "desc"] | None = None,
        cursor: str | None = None,
    ) -> SessionMessagesResponse:
        """Fetch one page of projected session messages."""
        resp = await self._request(
            "GET",
            f"/api/session/{quote(session_id, safe='')}/message",
            params={
                "limit": str(limit) if limit is not None else None,
                "order": order,
                "cursor": cursor,
            },
        )
        return SessionMessagesResponse.model_validate_json(resp.content)

    async def list_all_messages(self, session_id: str) -> list[SessionMessageInfo]:
        """Fetch all projected session messages in chronological order."""
        page_size = 200
        messages: list[SessionMessageInfo] = []
        cursor: str | None = None
        while True:
            page = await self.list_messages(
                session_id,
                limit=page_size,
                order="asc" if cursor is None else None,
                cursor=cursor,
            )
            if not page.data:
                break
            messages.extend(page.data)
            if len(page.data) < page_size or not page.cursor.next:
                break
            cursor = page.cursor.next
        return messages

    # --- Permissions & forms ----------------------------------------------------------

    async def reply_permission(
        self,
        session_id: str,
        request_id: str,
        decision: PermissionDecision,
        *,
        message: str | None = None,
    ) -> None:
        """Reply to a pending permission request."""
        body = SessionPermissionReplyBody(decision=decision, message=message)
        sid = quote(session_id, safe="")
        rid = quote(request_id, safe="")
        await self._request(
            "POST",
            f"/api/session/{sid}/permission/{rid}/reply",
            json_body=_dump_body(body),
        )

    async def reply_form(
        self,
        session_id: str,
        form_id: str,
        answer: dict[str, FormAnswerValue],
    ) -> None:
        """Submit answers to a pending session form."""
        body = FormReply(answer=answer)
        await self._request(
            "POST",
            f"/api/session/{quote(session_id, safe='')}/form/{quote(form_id, safe='')}/reply",
            json_body=_dump_body(body),
        )

    async def cancel_form(self, session_id: str, form_id: str) -> None:
        """Cancel a pending session form."""
        await self._request(
            "DELETE",
            f"/api/session/{quote(session_id, safe='')}/form/{quote(form_id, safe='')}",
        )

    # --- SSE event stream -------------------------------------------------------------

    async def events(
        self,
        *,
        types: Sequence[str] | None = None,
    ) -> AsyncIterator[Any]:
        """Subscribe to the live ``GET /api/event`` SSE stream.

        Yields unwrapped ``V2Event.root`` event instances (e.g. ``V2EventServerConnected``,
        ``SessionTextDelta``, ``SessionToolCalled``, ``PermissionAsked``, ``FormCreated``).
        """
        client = self._require_client()
        params: dict[str, list[str]] | None = {"types": list(types)} if types else None
        headers = {**self._headers, "Accept": "text/event-stream"}
        stream_timeout = httpx2.Timeout(connect=10.0, read=None, write=10.0, pool=10.0)
        async with client.stream(
            "GET",
            "/api/event",
            params=params,
            headers=headers,
            timeout=stream_timeout,
        ) as response:
            if response.status_code >= 400:  # noqa: PLR2004
                body = (await response.aread()).decode("utf-8", errors="replace")
                msg = f"OpenCode V2 GET /api/event failed with HTTP {response.status_code}: {body}"
                raise OpenCodeV2Error(msg, status_code=response.status_code, body=body)

            data_lines: list[str] = []
            async for raw_line in response.aiter_lines():
                if not raw_line:
                    if data_lines:
                        payload = "\n".join(data_lines)
                        data_lines.clear()
                        try:
                            parsed = V2Event.model_validate_json(payload)
                        except ValidationError:
                            continue
                        yield parsed.root
                    continue
                if raw_line.startswith(":"):
                    continue
                if raw_line.startswith("data:"):
                    data_lines.append(raw_line[5:].lstrip(" "))

            if data_lines:
                payload = "\n".join(data_lines)
                try:
                    parsed = V2Event.model_validate_json(payload)
                except ValidationError:
                    return
                yield parsed.root
