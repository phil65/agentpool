"""Content conversion utilities for ACP (Agent Client Protocol) integration.

This module handles conversion between pydantic-ai message formats and ACP protocol
content blocks, session updates, and other data structures using the external acp library.
"""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING, Any, assert_never, overload

from aclientp.schema import (
    AcpMcpServer,
    AudioContentBlock,
    AuthAccount,
    AuthStatusResponse,
    BlobResourceContents,
    EmbeddedResourceContentBlock,
    HttpMcpServer,
    ImageContentBlock,
    ResourceContentBlock,
    SessionConfigSelectOption,
    SessionInfo,
    SessionMode,
    SseMcpServer,
    StdioMcpServer,
    TextContentBlock,
    TextResourceContents,
    Usage,
)
from pydantic import HttpUrl
from pydantic_ai import BinaryContent, BinaryImage
from pydantic_ai.messages import _document_format_lookup

from agentpool.log import get_logger
from agentpool.utils.pydantic_ai_helpers import (
    format_uri_as_link,
    get_file_url_obj,
    uri_to_path_reference,
)
from agentpool_config.mcp_server import (
    SSEMCPServerConfig,
    StdioMCPServerConfig,
    StreamableHTTPMCPServerConfig,
)


if TYPE_CHECKING:
    from aclientp.schema import ContentBlock, McpServer, SelectSessionConfigOption, SystemPrompt
    from aclientp.schema.content_blocks import ResourceContents
    from fsspec.asyn import AsyncFileSystem
    from pydantic_ai import UserContent
    from pydantic_ai.usage import UsageBase

    from agentpool.agents.auth import AuthStatus
    from agentpool.agents.modes import ModeCategory, ModeInfo
    from agentpool.common_types import PathReference
    from agentpool.messaging import MessageNode
    from agentpool.sessions import SessionData
    from agentpool_config.mcp_server import MCPServerConfig

logger = get_logger(__name__)


@overload
def convert_acp_mcp_server_to_config(
    acp_server: HttpMcpServer,
) -> StreamableHTTPMCPServerConfig: ...


@overload
def convert_acp_mcp_server_to_config(
    acp_server: SseMcpServer,
) -> SSEMCPServerConfig: ...


@overload
def convert_acp_mcp_server_to_config(
    acp_server: StdioMcpServer,
) -> StdioMCPServerConfig: ...


@overload
def convert_acp_mcp_server_to_config(acp_server: McpServer) -> MCPServerConfig: ...


def convert_acp_mcp_server_to_config(acp_server: McpServer) -> MCPServerConfig:
    """Convert ACP McpServer to native MCPServerConfig.

    Args:
        acp_server: ACP McpServer object from session/new request

    Returns:
        MCPServerConfig instance
    """
    match acp_server:
        case StdioMcpServer(name=name, command=cmd, args=args):
            env = acp_server.get_env_dict()
            return StdioMCPServerConfig(name=name, command=cmd, args=list(args), env=env)
        case SseMcpServer(name=name, url=url):
            h = acp_server.get_headers_dict()
            return SSEMCPServerConfig(name=name, url=HttpUrl(url), headers=h)
        case HttpMcpServer(name=name, url=url):
            h = acp_server.get_headers_dict()
            return StreamableHTTPMCPServerConfig(name=name, url=HttpUrl(url), headers=h)
        case AcpMcpServer(name=name):
            # Only sent to agents advertising `mcp_capabilities.acp`, which we don't.
            msg = f"MCP-over-ACP server {name!r} is not supported"
            raise ValueError(msg)
        case _ as unreachable:
            assert_never(unreachable)


def from_acp_content(  # noqa: PLR0911
    block: ContentBlock,
    fs: AsyncFileSystem | None = None,
) -> UserContent | PathReference:
    """Convert ACP content blocks to UserContent or PathReference objects.

    File/directory references are converted to PathReference objects, deferring
    context resolution to the prompt conversion layer (convert_prompts).

    Args:
        block: ACP ContentBlock
        fs: Optional filesystem for file references

    Returns:
        UserContent or PathReference objects
    """
    logger.info("Processing block", block_type=type(block).__name__)
    match block:
        case TextContentBlock(text=text):
            return text

        case ImageContentBlock(data=data, mime_type=mime_type):
            binary_data = base64.b64decode(data)
            return BinaryImage(data=binary_data, media_type=mime_type)

        case AudioContentBlock(data=data, mime_type=mime_type):
            binary_data = base64.b64decode(data)
            return BinaryContent(data=binary_data, media_type=mime_type)

        case ResourceContentBlock(uri=uri, mime_type=mime_type):
            if mime_type:
                if obj := get_file_url_obj(uri, mime_type):
                    return obj
                # Try to create a PathReference for file:// URIs
                if ref := uri_to_path_reference(uri, mime_type, fs):
                    return ref
                return format_uri_as_link(uri)
            # No MIME type - try to create PathReference for file:// URIs
            if ref := uri_to_path_reference(uri, mime_type, fs):
                return ref
            return format_uri_as_link(uri)

        case EmbeddedResourceContentBlock(resource=resource):
            return resource_to_content(resource)

        case _ as unreachable:
            assert_never(unreachable)


def resource_to_content(resource: ResourceContents) -> str | BinaryImage | BinaryContent:
    match resource:
        case TextResourceContents(uri=uri, text=text):
            return format_uri_as_link(uri) + f'\n<context ref="{uri}">\n{text}\n</context>'
        case BlobResourceContents(blob=blob, mime_type=mime_type):
            binary_data = base64.b64decode(blob)
            if mime_type and mime_type.startswith("image/"):
                return BinaryImage(data=binary_data, media_type=mime_type)
            if (
                mime_type and mime_type.startswith("audio/")
            ) or mime_type in _document_format_lookup:
                return BinaryContent(data=binary_data, media_type=mime_type)
            formatted_uri = format_uri_as_link(resource.uri)
            return f"Binary Resource: {formatted_uri}"
        case _ as unreachable:
            assert_never(unreachable)


def to_session_select_option(mode: ModeInfo) -> SessionConfigSelectOption:
    return SessionConfigSelectOption(
        value=str(mode.value), name=mode.name, description=mode.description
    )


def to_session_config_option(category: ModeCategory) -> SelectSessionConfigOption:
    from aclientp.schema import SelectSessionConfigOption

    return SelectSessionConfigOption(
        id=category.id,
        name=category.name,
        description=None,
        category=category.category,
        current_value=category.current_mode_id,
        options=[to_session_select_option(mode) for mode in category.available_modes],
    )


def to_auth_status_response(status: AuthStatus) -> AuthStatusResponse:
    accounts = [
        AuthAccount(
            label=a.label,
            description=a.description,
            active=a.active,
            name=a.name,
            email=a.email,
            organization=a.organization,
        )
        for a in status.accounts
    ]
    return AuthStatusResponse(state=status.state, accounts=accounts)


def system_prompt_text(system_prompt: SystemPrompt | None) -> str | None:
    """Flatten a client-provided system prompt into text instructions."""
    if system_prompt is None:
        return None
    texts = [block.text for block in system_prompt.content if isinstance(block, TextContentBlock)]
    return "\n\n".join(texts) or None


def to_session_info(session_data: SessionData) -> SessionInfo:
    return SessionInfo(
        session_id=session_data.session_id,
        cwd=session_data.cwd or "",
        title=session_data.title,
        updated_at=session_data.updated_at,
    )


def agent_to_mode(agent: MessageNode[Any, Any]) -> SessionMode:
    """Convert agent to a session mode."""
    desc = agent.description or f"Switch to {agent.name} agent"
    return SessionMode(id=agent.name, name=agent.display_name, description=desc)


def to_usage(usage: UsageBase) -> Usage:
    return Usage(
        total_tokens=usage.total_tokens,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        thought_tokens=usage.details.get("reasoning_tokens") or None,
        cached_read_tokens=usage.cache_read_tokens or None,
        cached_write_tokens=usage.cache_write_tokens or None,
    )
