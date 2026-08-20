"""Unit tests for McpServerCap — MCP server capability with Resource Protocols.

Tests cover:
- Delegation: list_tools, call_tool, list_resources, read_resource, resource_exists
- Resource templates: list_resource_templates, complete_resource_template
- Multimodal read_resource (text + blob)
- Lazy init: no connection at construct, connection on first list_tools
- Change notification mapping: tools/list_changed, resource_list_changed, resource_updated
- MCPMessageHandler dispatch with real MCP notification types
- MCPClient subscribe/unsubscribe via low-level ClientSession
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import mcp.types
from pydantic import AnyUrl
import pytest

from wolfharness.capabilities.change_event import ChangeEvent
from wolfharness.capabilities.mcp_server_cap import McpServerCap
from wolfharness.capabilities.resource_protocols import (
    BlobResourceContent,
    ChangeObservable,
    CompletionArgument,
    CompletionResult,
    McpResource,
    ResourceAccess,
    ResourceTemplateAccess,
    ResourceTemplateEntry,
    TextResourceContent,
    ToolAccess,
    ToolEntry,
)
from wolfharness.mcp_server.message_handler import MCPMessageHandler


pytestmark = pytest.mark.unit


if TYPE_CHECKING:
    from typing import Self


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


@dataclass
class FakeMCPClient:
    """Async mock of MCPClient for testing McpServerCap."""

    _tools: list[Any] = field(default_factory=list)
    _resources: list[Any] = field(default_factory=list)
    _read_results: dict[str, list[Any]] = field(default_factory=dict)
    _resource_templates: list[Any] = field(default_factory=list)
    _resource_next_cursor: str | None = None
    _template_next_cursor: str | None = None
    _completion_result: Any = None
    _connected: bool = False
    _tool_change_callback: Any = None
    _resource_list_changed_callback: Any = None
    _resource_updated_callback: Any = None
    _prompt_change_callback: Any = None
    _subscribed_uris: list[str] = field(default_factory=list)
    _unsubscribed_uris: list[str] = field(default_factory=list)
    config: Any = None

    async def __aenter__(self) -> Self:
        self._connected = True
        return self

    async def __aexit__(self, *args: object) -> None:
        self._connected = False

    async def list_tools(self) -> list[Any]:
        if not self._connected:
            raise RuntimeError("Not connected")
        return list(self._tools)

    async def list_resources(self) -> list[Any]:
        if not self._connected:
            raise RuntimeError("Not connected")
        return list(self._resources)

    def supports_resources(self) -> bool:
        return True

    async def list_resources_page(self, cursor: str | None = None) -> mcp.types.ListResourcesResult:
        del cursor
        return mcp.types.ListResourcesResult(
            resources=list(self._resources),
            nextCursor=self._resource_next_cursor,
        )

    async def list_resource_templates(self) -> list[Any]:
        if not self._connected:
            raise RuntimeError("Not connected")
        return list(self._resource_templates)

    async def list_resource_templates_page(
        self, cursor: str | None = None
    ) -> mcp.types.ListResourceTemplatesResult:
        del cursor
        return mcp.types.ListResourceTemplatesResult(
            resourceTemplates=list(self._resource_templates),
            nextCursor=self._template_next_cursor,
        )

    async def read_resource(self, uri: str) -> list[Any]:
        if not self._connected:
            raise RuntimeError("Not connected")
        if uri not in self._read_results:
            raise RuntimeError(f"Resource not found: {uri}")
        return self._read_results[uri]

    async def complete(
        self,
        ref_type: str,
        ref_uri: str,
        argument_name: str,
        argument_value: str,
        context: dict[str, str] | None = None,
    ) -> Any:
        if not self._connected:
            raise RuntimeError("Not connected")
        return self._completion_result

    async def call_tool(self, name: str, *args: Any, **kwargs: Any) -> str:
        return f"called:{name}"

    def convert_tool(self, tool: Any) -> Any:
        return tool

    def set_notification_callbacks(
        self,
        *,
        tool_change_callback: Any = None,
        prompt_change_callback: Any = None,
        resource_list_changed_callback: Any = None,
        resource_updated_callback: Any = None,
    ) -> None:
        """Mirror MCPClient.set_notification_callbacks."""
        self._tool_change_callback = tool_change_callback
        self._prompt_change_callback = prompt_change_callback
        self._resource_list_changed_callback = resource_list_changed_callback
        self._resource_updated_callback = resource_updated_callback

    async def trigger_tool_change(self) -> None:
        """Simulate MCP server sending notifications/tools/list_changed."""
        if self._tool_change_callback is not None:
            await self._tool_change_callback()

    async def trigger_resource_list_changed(self) -> None:
        """Simulate notifications/resources/list_changed."""
        if self._resource_list_changed_callback is not None:
            await self._resource_list_changed_callback()

    async def trigger_resource_updated(self, uri: str) -> None:
        """Simulate notifications/resources/updated."""
        if self._resource_updated_callback is not None:
            await self._resource_updated_callback(uri)

    async def subscribe_resource(self, uri: str) -> None:
        self._subscribed_uris.append(uri)

    async def unsubscribe_resource(self, uri: str) -> None:
        self._unsubscribed_uris.append(uri)


class FakeSessionPool:
    """Fake SessionConnectionPool that returns a FakeMCPClient."""

    def __init__(self, client: FakeMCPClient) -> None:
        self._client = client
        self.get_client_call_count = 0

    async def get_client(self, config: Any, skill_name: str | None = None) -> FakeMCPClient:
        self.get_client_call_count += 1
        self._client._connected = True
        return self._client


def _make_tool(name: str = "test_tool", description: str = "A test tool") -> MagicMock:
    """Create a fake MCP tool."""
    tool = MagicMock()
    tool.name = name
    tool.description = description
    tool.inputSchema = {"type": "object", "properties": {}}
    return tool


def _make_resource(
    uri: str, name: str = "", description: str = "", mime_type: str = ""
) -> mcp.types.Resource:
    """Create a fake MCP resource."""
<<<<<<< Updated upstream
    res = MagicMock()
    res.uri = uri
    res.name = name
    res.title = None
    res.description = description
    res.mimeType = mime_type
    return res
=======
    return mcp.types.Resource(
        uri=AnyUrl(uri),
        name=name,
        description=description or None,
        mimeType=mime_type or None,
    )
>>>>>>> Stashed changes


def _make_text_content(text: str, uri: str = "file:///resource") -> mcp.types.TextResourceContents:
    """Create a fake TextResourceContents."""
    return mcp.types.TextResourceContents(uri=AnyUrl(uri), text=text)


def _make_blob_content(
    blob: str,
    mime_type: str = "application/octet-stream",
    uri: str = "file:///resource",
) -> mcp.types.BlobResourceContents:
    """Create a fake BlobResourceContents."""
    return mcp.types.BlobResourceContents(
        uri=AnyUrl(uri),
        blob=blob,
        mimeType=mime_type,
    )


def _make_resource_template(
    uri_template: str,
    name: str = "",
    title: str = "",
    description: str = "",
    mime_type: str = "",
) -> mcp.types.ResourceTemplate:
    """Create a fake MCP ResourceTemplate."""
    return mcp.types.ResourceTemplate(
        uriTemplate=uri_template,
        name=name,
        title=title or None,
        description=description or None,
        mimeType=mime_type or None,
    )


def _make_completion(
    values: list[str],
    total: int | None = None,
    has_more: bool | None = None,
) -> MagicMock:
    """Create a fake mcp.types.Completion."""
    completion = MagicMock()
    completion.values = values
    completion.total = total
    completion.hasMore = has_more
    return completion


def _make_config(client_id: str = "test_server") -> MagicMock:
    """Create a fake BaseMCPServerConfig."""
    config = MagicMock()
    config.client_id = client_id
    config.display_name = client_id
    return config


# ---------------------------------------------------------------------------
# isinstance tests
# ---------------------------------------------------------------------------


def test_mcp_server_cap_is_mcp_resource() -> None:
    """McpServerCap is an instance of McpResource (structural typing)."""
    cap = McpServerCap(
        config=_make_config(),
        session_pool=FakeSessionPool(FakeMCPClient()),
    )
    assert isinstance(cap, McpResource)


def test_mcp_server_cap_is_tool_access() -> None:
    """McpServerCap is an instance of ToolAccess protocol."""
    cap = McpServerCap(
        config=_make_config(),
        session_pool=FakeSessionPool(FakeMCPClient()),
    )
    assert isinstance(cap, ToolAccess)


def test_mcp_server_cap_is_resource_access() -> None:
    """McpServerCap is an instance of ResourceAccess protocol."""
    cap = McpServerCap(
        config=_make_config(),
        session_pool=FakeSessionPool(FakeMCPClient()),
    )
    assert isinstance(cap, ResourceAccess)


def test_mcp_server_cap_is_resource_template_access() -> None:
    """McpServerCap is an instance of ResourceTemplateAccess protocol."""
    cap = McpServerCap(
        config=_make_config(),
        session_pool=FakeSessionPool(FakeMCPClient()),
    )
    assert isinstance(cap, ResourceTemplateAccess)


def test_mcp_server_cap_is_change_observable() -> None:
    """McpServerCap is an instance of ChangeObservable."""
    cap = McpServerCap(
        config=_make_config(),
        session_pool=FakeSessionPool(FakeMCPClient()),
    )
    assert isinstance(cap, ChangeObservable)


# ---------------------------------------------------------------------------
# Lazy init tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_lazy_init_no_connection_at_construct() -> None:
    """McpServerCap does not connect at construction time."""
    client = FakeMCPClient()
    pool = FakeSessionPool(client)
    cap = McpServerCap(config=_make_config(), session_pool=pool)

    assert cap._client is None
    assert pool.get_client_call_count == 0


@pytest.mark.anyio
async def test_lazy_init_connection_on_first_list_tools() -> None:
    """First list_tools() triggers client creation via _ensure_client()."""
    client = FakeMCPClient(_tools=[_make_tool()])
    pool = FakeSessionPool(client)
    cap = McpServerCap(config=_make_config(), session_pool=pool)

    assert pool.get_client_call_count == 0
    tools = await cap.list_tools()
    assert pool.get_client_call_count == 1
    assert len(tools) == 1
    assert isinstance(tools[0], ToolEntry)
    assert tools[0].name == "test_tool"

    # Second call reuses cached client
    await cap.list_tools()
    assert pool.get_client_call_count == 1


# ---------------------------------------------------------------------------
# Delegation tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_tools_delegation() -> None:
    """list_tools() delegates to MCPClient.list_tools() and maps to ToolEntry."""
    tools = [_make_tool("tool_a", "Tool A"), _make_tool("tool_b", "Tool B")]
    client = FakeMCPClient(_tools=tools)
    cap = McpServerCap(config=_make_config(), session_pool=FakeSessionPool(client))

    result = await cap.list_tools()
    assert len(result) == 2
    assert result[0].name == "tool_a"
    assert result[0].description == "Tool A"
    assert result[1].name == "tool_b"


@pytest.mark.anyio
async def test_list_resources_delegation() -> None:
    """list_resources() delegates to MCPClient.list_resources()."""
    resources = [
        _make_resource("file:///path1", "res1", "Resource 1", "text/plain"),
        _make_resource("file:///path2", "res2"),
    ]
    client = FakeMCPClient(_resources=resources)
    cap = McpServerCap(config=_make_config(), session_pool=FakeSessionPool(client))

    result = await cap.list_resources()
    assert len(result) == 2
    assert result[0].uri == "file:///path1"
    assert result[0].name == "res1"
    assert result[0].description == "Resource 1"
    assert result[0].mime_type == "text/plain"


@pytest.mark.anyio
<<<<<<< Updated upstream
async def test_list_resources_prefers_title() -> None:
    """Title is used as the ResourceEntry name when present."""
    titled = MagicMock()
    titled.uri = "file:///titled"
    titled.name = "get_titled"
    titled.title = "Human Readable Title"
    titled.description = ""
    titled.mimeType = "text/plain"
    client = FakeMCPClient(_resources=[titled])
    cap = McpServerCap(config=_make_config(), session_pool=FakeSessionPool(client))

    result = await cap.list_resources()

    assert result[0].name == "Human Readable Title"
=======
async def test_resource_pages_preserve_all_mcp_fields_and_cursor() -> None:
    """Single-page mapping keeps identity, metadata, annotations, and cursor."""
    resource = mcp.types.Resource(
        uri=AnyUrl("kb:///resources"),
        name="catalog",
        title="Knowledge catalog",
        description="Available manuals",
        mimeType="application/json",
        size=123,
        annotations=mcp.types.Annotations(audience=["assistant"], priority=0.8),
        _meta={"tenant": "factory-a"},
    )
    client = FakeMCPClient(
        _resources=[resource],
        _resource_next_cursor="upstream-resource-cursor",
    )
    cap = McpServerCap(config=_make_config(), session_pool=FakeSessionPool(client))

    page = await cap.list_resources_page()

    assert page.next_cursor == "upstream-resource-cursor"
    assert page.entries[0].title == "Knowledge catalog"
    assert page.entries[0].size == 123
    assert page.entries[0].annotations == {
        "audience": ["assistant"],
        "priority": 0.8,
    }
    assert page.entries[0].meta == {"tenant": "factory-a"}
>>>>>>> Stashed changes


@pytest.mark.anyio
async def test_read_resource_existing() -> None:
    """read_resource() returns TextResourceContent list for existing resource."""
    text_content = _make_text_content("hello world", "file:///path1")
    client = FakeMCPClient(
        _resources=[_make_resource("file:///path1")],
        _read_results={"file:///path1": [text_content]},
    )
    cap = McpServerCap(config=_make_config(), session_pool=FakeSessionPool(client))

    result = await cap.read_resource("file:///path1")
    assert result is not None
    assert len(result) == 1
    assert isinstance(result[0], TextResourceContent)
    assert result[0].text == "hello world"
    assert result[0].uri == "file:///path1"


@pytest.mark.anyio
async def test_read_resource_nonexistent() -> None:
    """read_resource() returns None for nonexistent resource."""
    client = FakeMCPClient(_resources=[])
    cap = McpServerCap(config=_make_config(), session_pool=FakeSessionPool(client))

    content = await cap.read_resource("file:///nonexistent")
    assert content is None


@pytest.mark.anyio
async def test_resource_exists_true() -> None:
    """resource_exists() returns True for existing resource."""
    client = FakeMCPClient(_resources=[_make_resource("file:///path1")])
    cap = McpServerCap(config=_make_config(), session_pool=FakeSessionPool(client))

    assert await cap.resource_exists("file:///path1") is True


@pytest.mark.anyio
async def test_resource_exists_false() -> None:
    """resource_exists() returns False for nonexistent resource."""
    client = FakeMCPClient(_resources=[])
    cap = McpServerCap(config=_make_config(), session_pool=FakeSessionPool(client))

    assert await cap.resource_exists("file:///nonexistent") is False


# ---------------------------------------------------------------------------
# Resource template tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_resource_templates() -> None:
    """list_resource_templates() delegates to MCPClient and maps to ResourceTemplateEntry."""
    templates = [
        _make_resource_template(
            "file:///{path}",
            name="file_template",
            title="File Template",
            description="Template for file paths",
            mime_type="text/plain",
        ),
        _make_resource_template(
            "db://{table}/{row}",
            name="db_template",
            description="Database row template",
        ),
    ]
    client = FakeMCPClient(_resource_templates=templates)
    cap = McpServerCap(config=_make_config(), session_pool=FakeSessionPool(client))

    result = await cap.list_resource_templates()
    assert len(result) == 2
    assert isinstance(result[0], ResourceTemplateEntry)
    assert result[0].uri_template == "file:///{path}"
    assert result[0].name == "file_template"
    assert result[0].title == "File Template"
    assert result[0].description == "Template for file paths"
    assert result[0].mime_type == "text/plain"
    assert result[1].uri_template == "db://{table}/{row}"
    assert result[1].name == "db_template"


@pytest.mark.anyio
async def test_template_pages_preserve_metadata_and_cursor() -> None:
    """Template pages retain MCP annotations, meta, and continuation state."""
    template = mcp.types.ResourceTemplate(
        uriTemplate="kb:///resources/{resource_id}",
        name="resource",
        title="Knowledge resource",
        description="Read a resource",
        mimeType="application/json",
        annotations=mcp.types.Annotations(audience=["assistant"]),
        _meta={"provider": "unikb"},
    )
    client = FakeMCPClient(
        _resource_templates=[template],
        _template_next_cursor="upstream-template-cursor",
    )
    cap = McpServerCap(config=_make_config(), session_pool=FakeSessionPool(client))

    page = await cap.list_resource_templates_page()

    assert page.next_cursor == "upstream-template-cursor"
    assert page.entries[0].title == "Knowledge resource"
    assert page.entries[0].annotations == {"audience": ["assistant"]}
    assert page.entries[0].meta == {"provider": "unikb"}


@pytest.mark.anyio
async def test_list_resource_templates_empty() -> None:
    """list_resource_templates() returns empty sequence when no templates."""
    client = FakeMCPClient(_resource_templates=[])
    cap = McpServerCap(config=_make_config(), session_pool=FakeSessionPool(client))

    result = await cap.list_resource_templates()
    assert len(result) == 0


@pytest.mark.anyio
async def test_complete_resource_template() -> None:
    """complete_resource_template() delegates to MCPClient and maps to CompletionResult."""
    completion = _make_completion(
        values=["config.json", "config.yaml", "config.toml"],
        total=3,
        has_more=False,
    )
    client = FakeMCPClient(_completion_result=completion)
    cap = McpServerCap(config=_make_config(), session_pool=FakeSessionPool(client))

    result = await cap.complete_resource_template(
        uri_template="file:///{path}",
        argument=CompletionArgument(name="path", value="config"),
    )
    assert isinstance(result, CompletionResult)
    assert result.values == ["config.json", "config.yaml", "config.toml"]
    assert result.total == 3
    assert result.has_more is False


@pytest.mark.anyio
async def test_complete_resource_template_with_context() -> None:
    """complete_resource_template() passes context arguments through."""
    completion = _make_completion(values=["option1"], total=1, has_more=None)
    client = FakeMCPClient(_completion_result=completion)
    cap = McpServerCap(config=_make_config(), session_pool=FakeSessionPool(client))

    result = await cap.complete_resource_template(
        uri_template="db://{table}/{row}",
        argument=CompletionArgument(name="table", value="us"),
        context={"row": "123"},
    )
    assert isinstance(result, CompletionResult)
    assert result.values == ["option1"]
    assert result.total == 1
    assert result.has_more is None


# ---------------------------------------------------------------------------
# Multimodal read_resource tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_read_resource_multimodal_text_and_blob() -> None:
    """read_resource() returns both TextResourceContent and BlobResourceContent."""
    text_content = _make_text_content("hello", "file:///mixed")
    blob_content = _make_blob_content("SGVsbG8=", "application/octet-stream", "file:///mixed")
    client = FakeMCPClient(
        _resources=[_make_resource("file:///mixed")],
        _read_results={"file:///mixed": [text_content, blob_content]},
    )
    cap = McpServerCap(config=_make_config(), session_pool=FakeSessionPool(client))

    result = await cap.read_resource("file:///mixed")
    assert result is not None
    assert len(result) == 2
    assert isinstance(result[0], TextResourceContent)
    assert result[0].text == "hello"
    assert result[0].uri == "file:///mixed"
    assert isinstance(result[1], BlobResourceContent)
    assert result[1].blob == "SGVsbG8="
    assert result[1].mime_type == "application/octet-stream"
    assert result[1].uri == "file:///mixed"


@pytest.mark.anyio
async def test_read_resource_blob_only() -> None:
    """read_resource() returns BlobResourceContent for binary resources."""
    blob_content = _make_blob_content("iVBORw0KGgo=", "image/png", "file:///image.png")
    client = FakeMCPClient(
        _resources=[_make_resource("file:///image.png")],
        _read_results={"file:///image.png": [blob_content]},
    )
    cap = McpServerCap(config=_make_config(), session_pool=FakeSessionPool(client))

    result = await cap.read_resource("file:///image.png")
    assert result is not None
    assert len(result) == 1
    assert isinstance(result[0], BlobResourceContent)
    assert result[0].blob == "iVBORw0KGgo="
    assert result[0].mime_type == "image/png"


@pytest.mark.anyio
async def test_read_resource_empty_contents() -> None:
    """read_resource() returns None when contents list is empty."""
    client = FakeMCPClient(
        _resources=[_make_resource("file:///empty")],
        _read_results={"file:///empty": []},
    )
    cap = McpServerCap(config=_make_config(), session_pool=FakeSessionPool(client))

    result = await cap.read_resource("file:///empty")
    assert result is None


# ---------------------------------------------------------------------------
# Change notification tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_change_notification_tools_changed() -> None:
    """tools/list_changed notification yields ChangeEvent."""
    client = FakeMCPClient(_tools=[_make_tool()])
    cap = McpServerCap(config=_make_config(), session_pool=FakeSessionPool(client))

    # Initialize the client (sets up change callback)
    await cap._ensure_client()

    # Get the change stream
    stream = cap.on_change()
    assert stream is not None

    # Trigger a tool change
    await client.trigger_tool_change()

    # Consume the event with a timeout
    async with asyncio.timeout(1.0):
        event = await stream.__anext__()

    assert isinstance(event, ChangeEvent)
    assert event.kind == "tools_changed"
    assert event.capability_name == cap.name
    assert event.source_uri == f"mcp://{cap.name}"


# ---------------------------------------------------------------------------
# Lifecycle tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_aenter_returns_self() -> None:
    """__aenter__ returns self without connecting (lazy)."""
    client = FakeMCPClient()
    cap = McpServerCap(config=_make_config(), session_pool=FakeSessionPool(client))

    result = await cap.__aenter__()
    assert result is cap
    assert cap._client is None  # Lazy: no connection


@pytest.mark.anyio
async def test_aexit_clears_client() -> None:
    """__aexit__ clears the cached client reference."""
    client = FakeMCPClient(_tools=[_make_tool()])
    cap = McpServerCap(config=_make_config(), session_pool=FakeSessionPool(client))

    # Force client creation
    await cap.list_tools()
    assert cap._client is not None

    await cap.__aexit__(None, None, None)
    assert cap._client is None
    assert len(cap._change_queues) == 0


# ---------------------------------------------------------------------------
# get_instructions test
# ---------------------------------------------------------------------------


def test_get_instructions_returns_none() -> None:
    """get_instructions() returns None — MCP servers don't provide prompt instructions."""
    cap = McpServerCap(
        config=_make_config(),
        session_pool=FakeSessionPool(FakeMCPClient()),
    )
    assert cap.get_instructions() is None


# ---------------------------------------------------------------------------
# Change event tests: resource_list_changed and resource_updated
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_change_event_resource_list_changed() -> None:
    """resource_list_changed notification emits ChangeEvent with correct kind."""
    client = FakeMCPClient()
    cap = McpServerCap(config=_make_config(), session_pool=FakeSessionPool(client))

    # Force client init to wire callbacks
    await cap.list_tools()

    stream = cap.on_change()
    assert stream is not None

    await client.trigger_resource_list_changed()

    async with asyncio.timeout(1.0):
        event = await stream.__anext__()

    assert isinstance(event, ChangeEvent)
    assert event.kind == "resource_list_changed"
    assert event.capability_name == cap.name
    assert event.source_uri == f"mcp://{cap.name}"


@pytest.mark.anyio
async def test_change_event_resource_updated_carries_uri() -> None:
    """resource_updated notification emits ChangeEvent with the specific resource URI."""
    client = FakeMCPClient()
    cap = McpServerCap(config=_make_config(), session_pool=FakeSessionPool(client))

    await cap.list_tools()

    stream = cap.on_change()
    assert stream is not None

    updated_uri = "mcp://github/issues/42"
    await client.trigger_resource_updated(updated_uri)

    async with asyncio.timeout(1.0):
        event = await stream.__anext__()

    assert isinstance(event, ChangeEvent)
    assert event.kind == "resource_updated"
    assert event.source_uri == updated_uri
    assert event.capability_name == cap.name


@pytest.mark.anyio
async def test_change_event_resource_updated_different_uris() -> None:
    """Multiple resource_updated events carry distinct source_uri values."""
    client = FakeMCPClient()
    cap = McpServerCap(config=_make_config(), session_pool=FakeSessionPool(client))

    await cap.list_tools()

    stream = cap.on_change()
    assert stream is not None

    uri1 = "mcp://server/file_a.txt"
    uri2 = "mcp://server/file_b.txt"

    await client.trigger_resource_updated(uri1)
    await client.trigger_resource_updated(uri2)

    events: list[ChangeEvent] = []
    async with asyncio.timeout(1.0):
        events.append(await stream.__anext__())
        events.append(await stream.__anext__())

    assert events[0].source_uri == uri1
    assert events[1].source_uri == uri2
    assert all(e.kind == "resource_updated" for e in events)


@pytest.mark.anyio
async def test_change_event_resource_list_changed_no_listeners() -> None:
    """Triggering resource_list_changed with no active stream does not raise."""
    client = FakeMCPClient()
    cap = McpServerCap(config=_make_config(), session_pool=FakeSessionPool(client))

    await cap.list_tools()

    # No on_change() stream created — should not raise
    await client.trigger_resource_list_changed()


@pytest.mark.anyio
async def test_change_event_resource_updated_no_listeners() -> None:
    """Triggering resource_updated with no active stream does not raise."""
    client = FakeMCPClient()
    cap = McpServerCap(config=_make_config(), session_pool=FakeSessionPool(client))

    await cap.list_tools()

    # No on_change() stream created — should not raise
    await client.trigger_resource_updated("mcp://server/whatever")


# ---------------------------------------------------------------------------
# MCPMessageHandler tests with real MCP notification types
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_message_handler_resource_list_changed_calls_callback() -> None:
    """on_resource_list_changed calls the renamed callback."""
    called = False

    async def callback() -> None:
        nonlocal called
        called = True

    handler = MCPMessageHandler(
        client=MagicMock(),
        resource_list_changed_callback=callback,
    )

    notification = mcp.types.ResourceListChangedNotification()
    await handler.on_resource_list_changed(notification)

    assert called


@pytest.mark.anyio
async def test_message_handler_resource_updated_calls_callback_with_uri() -> None:
    """on_resource_updated calls the callback with the URI from the notification."""
    received_uri: str | None = None

    async def callback(uri: str) -> None:
        nonlocal received_uri
        received_uri = uri

    handler = MCPMessageHandler(
        client=MagicMock(),
        resource_updated_callback=callback,
    )

    expected_uri = "mcp://server/important-resource.txt"
    notification = mcp.types.ResourceUpdatedNotification(
        params=mcp.types.ResourceUpdatedNotificationParams(
            uri=AnyUrl(expected_uri),
        ),
    )
    await handler.on_resource_updated(notification)

    assert received_uri == expected_uri


@pytest.mark.anyio
async def test_message_handler_resource_list_changed_no_callback() -> None:
    """on_resource_list_changed does not raise when callback is None."""
    handler = MCPMessageHandler(client=SimpleNamespace(_resource_list_changed_callback=None))

    notification = mcp.types.ResourceListChangedNotification()
    await handler.on_resource_list_changed(notification)


@pytest.mark.anyio
async def test_message_handler_resource_updated_no_callback() -> None:
    """on_resource_updated does not raise when callback is None."""
    handler = MCPMessageHandler(client=SimpleNamespace(_resource_updated_callback=None))

    notification = mcp.types.ResourceUpdatedNotification(
        params=mcp.types.ResourceUpdatedNotificationParams(
            uri=AnyUrl("mcp://server/some-resource"),
        ),
    )
    await handler.on_resource_updated(notification)


@pytest.mark.anyio
async def test_message_handler_dispatches_resource_updated_via_call() -> None:
    """Full __call__ dispatch routes ResourceUpdatedNotification to on_resource_updated."""
    received_uris: list[str] = []

    async def callback(uri: str) -> None:
        received_uris.append(uri)

    handler = MCPMessageHandler(
        client=MagicMock(),
        resource_updated_callback=callback,
    )

    server_notification = mcp.types.ServerNotification(
        root=mcp.types.ResourceUpdatedNotification(
            params=mcp.types.ResourceUpdatedNotificationParams(
                uri=AnyUrl("mcp://server/dispatched.txt"),
            ),
        ),
    )

    await handler(server_notification)

    assert received_uris == ["mcp://server/dispatched.txt"]


@pytest.mark.anyio
async def test_message_handler_dispatches_resource_list_changed_via_call() -> None:
    """Full __call__ dispatch routes ResourceListChangedNotification correctly."""
    call_count = 0

    async def callback() -> None:
        nonlocal call_count
        call_count += 1

    handler = MCPMessageHandler(
        client=MagicMock(),
        resource_list_changed_callback=callback,
    )

    server_notification = mcp.types.ServerNotification(
        root=mcp.types.ResourceListChangedNotification(),
    )

    await handler(server_notification)

    assert call_count == 1


# ---------------------------------------------------------------------------
# MCPClient subscribe/unsubscribe tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_mcpclient_subscribe_resource_delegates_to_session() -> None:
    """subscribe_resource calls ClientSession.subscribe_resource with AnyUrl."""
    from wolfharness.mcp_server.client import MCPClient
    from wolfharness_config.mcp_server import StdioMCPServerConfig

    config = StdioMCPServerConfig(command="echo", args=["test"])
    client = MCPClient(config)

    mock_session = MagicMock()
    mock_session.subscribe_resource = AsyncMock(return_value=None)
    mock_fastmcp_client = MagicMock()
    mock_fastmcp_client.session = mock_session
    client._client = mock_fastmcp_client

    test_uri = "mcp://server/resource.txt"
    await client.subscribe_resource(test_uri)

    mock_session.subscribe_resource.assert_called_once()
    called_arg = mock_session.subscribe_resource.call_args[0][0]
    assert str(called_arg) == test_uri


@pytest.mark.anyio
async def test_mcpclient_unsubscribe_resource_delegates_to_session() -> None:
    """unsubscribe_resource calls ClientSession.unsubscribe_resource with AnyUrl."""
    from wolfharness.mcp_server.client import MCPClient
    from wolfharness_config.mcp_server import StdioMCPServerConfig

    config = StdioMCPServerConfig(command="echo", args=["test"])
    client = MCPClient(config)

    mock_session = MagicMock()
    mock_session.unsubscribe_resource = AsyncMock(return_value=None)
    mock_fastmcp_client = MagicMock()
    mock_fastmcp_client.session = mock_session
    client._client = mock_fastmcp_client

    test_uri = "mcp://server/resource.txt"
    await client.unsubscribe_resource(test_uri)

    mock_session.unsubscribe_resource.assert_called_once()
    called_arg = mock_session.unsubscribe_resource.call_args[0][0]
    assert str(called_arg) == test_uri


@pytest.mark.anyio
async def test_mcpclient_subscribe_resource_not_connected_raises() -> None:
    """subscribe_resource raises RuntimeError when not connected."""
    from wolfharness.mcp_server.client import MCPClient
    from wolfharness_config.mcp_server import StdioMCPServerConfig

    config = StdioMCPServerConfig(command="echo", args=["test"])
    client = MCPClient(config)

    with pytest.raises(RuntimeError, match="Not connected"):
        await client.subscribe_resource("mcp://server/resource.txt")
