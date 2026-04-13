"""Conversions between internal and MCP types."""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING, Any, assert_never

from pydantic_ai import (
    BinaryContent,
    BinaryImage,
    FileUrl,
    SystemPromptPart,
    TextPart,
    UserPromptPart,
)

from agentpool.log import get_logger
from agentpool.utils.pydantic_ai_helpers import url_from_mime_type


if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from fastmcp import Client
    from mcp.types import (
        ContentBlock,
        PromptMessage,
        SamplingMessage,
        SamplingMessageContentBlock,
    )
    from pydantic_ai import ModelRequestPart, ModelResponsePart, UserContent

logger = get_logger(__name__)


def to_mcp_messages(
    part: ModelRequestPart | ModelResponsePart,
) -> Iterator[PromptMessage]:
    """Convert internal PromptMessage to MCP PromptMessage."""
    from mcp.types import AudioContent, ImageContent, PromptMessage, TextContent

    match part:
        case UserPromptPart(content=str() as c):
            content = TextContent(type="text", text=c)
            yield PromptMessage(role="user", content=content)
        case UserPromptPart(content=content_items):
            for item in content_items:
                match item:
                    case BinaryContent(data=data, media_type=media_type) if item.is_audio:
                        encoded = base64.b64encode(data).decode()
                        audio = AudioContent(type="audio", data=encoded, mimeType=media_type)
                        yield PromptMessage(role="user", content=audio)
                    case BinaryContent(data=data, media_type=media_type) if item.is_image:
                        encoded = base64.b64encode(data).decode()
                        image = ImageContent(type="image", data=encoded, mimeType=media_type)
                        yield PromptMessage(role="user", content=image)
                    case FileUrl(url=url):
                        content = TextContent(type="text", text=url)
                        yield PromptMessage(role="user", content=content)

        case SystemPromptPart(content=msg):
            yield PromptMessage(role="user", content=TextContent(type="text", text=msg))
        case TextPart(content=msg):
            text_content = TextContent(type="text", text=msg)
            yield PromptMessage(role="assistant", content=text_content)


def sampling_messages_to_user_content(msgs: list[SamplingMessage]) -> list[UserContent]:

    # Convert messages to prompts for the agent
    prompts: list[UserContent] = []
    for mcp_msg in msgs:
        match mcp_msg.content:
            case list(content_blocks):
                prompts.extend(c for i in content_blocks if (c := content_block_to_user_content(i)))
            case _ as content_block:
                if content := content_block_to_user_content(content_block):
                    prompts.append(content)
    return prompts


def content_block_to_user_content(content: SamplingMessageContentBlock) -> UserContent | None:
    from mcp import types

    match content:
        case types.TextContent(text=text):
            return text
        case (
            types.ImageContent(data=data, mimeType=mime)
            | types.AudioContent(data=data, mimeType=mime)
        ):
            bin_content = BinaryContent(data=base64.b64decode(data), media_type=mime)
            return BinaryContent.narrow_type(bin_content)
        case types.ToolUseContent() | types.ToolResultContent():
            return None
        case _ as unreachable:
            assert_never(unreachable)


async def from_mcp_content(
    mcp_content: Sequence[ContentBlock],
    client: Client[Any] | None = None,
) -> list[str | BinaryContent]:
    """Convert MCP content blocks to PydanticAI content types.

    If a FastMCP client is given, this function will try to resolve the ResourceLinks.

    """
    from mcp.types import (
        AudioContent,
        BlobResourceContents,
        EmbeddedResource,
        ImageContent,
        ResourceLink,
        TextContent,
        TextResourceContents,
    )

    contents: list[Any] = []

    for block in mcp_content:
        match block:
            case TextContent(text=text):
                contents.append(text)
            case TextResourceContents(text=text):
                contents.append(text)
            case ImageContent(data=data, mimeType=mime_type):
                decoded_data = base64.b64decode(data)
                img = BinaryImage(data=decoded_data, media_type=mime_type)
                contents.append(img)
            case AudioContent(data=data, mimeType=mime_type):
                decoded_data = base64.b64decode(data)
                content = BinaryContent(data=decoded_data, media_type=mime_type)
                contents.append(content)
            case BlobResourceContents(blob=blob):
                decoded_data = base64.b64decode(blob)
                mime = "application/octet-stream"
                content = BinaryContent(data=decoded_data, media_type=mime)
                contents.append(content)
            case ResourceLink(uri=uri, mimeType=mime_type) if client:
                try:
                    res = await client.read_resource(uri)
                    emb_resources = [EmbeddedResource(type="resource", resource=r) for r in res]
                    nested = await from_mcp_content(emb_resources, client)
                    contents.extend(nested)
                except Exception:  # noqa: BLE001
                    # Fallback to URL if reading fails
                    logger.warning("Failed to read resource", uri=uri)
            case ResourceLink(uri=uri, mimeType=mime_type):
                # Convert to appropriate URL type based on MIME type
                contents.append(url_from_mime_type(str(uri), mime_type))
            # mypy doesnt understand exhaustivness check for "nested typing", so we nest match-case
            case EmbeddedResource(resource=resource):
                match resource:
                    case TextResourceContents(text=text):
                        contents.append(text)
                    case BlobResourceContents(mimeType=mime_type):
                        contents.append(f"[Binary data: {mime_type}]")
                    case _ as unreachable:
                        assert_never(unreachable)  # ty: ignore
            case _ as unreachable:
                assert_never(unreachable)
    return contents


def content_block_as_text(content: ContentBlock) -> str:
    from mcp.types import (
        AudioContent,
        BlobResourceContents,
        EmbeddedResource,
        ImageContent,
        ResourceLink,
        TextContent,
        TextResourceContents,
    )

    match content:
        case TextContent(text=text):
            return text
        case EmbeddedResource(resource=resource):
            match resource:
                case TextResourceContents() as text_contents:
                    return text_contents.text
                case BlobResourceContents() as blob_contents:
                    return f"[Resource: {blob_contents.uri}]"
                case _ as unreachable:
                    assert_never(unreachable)  # ty: ignore
        case ResourceLink(uri=uri, description=desc):
            return f"[Resource Link: {uri}] - {desc}" if desc else f"[Resource Link: {uri}]"
        case ImageContent(mimeType=mime_type):
            return f"[Image: {mime_type}]"
        case AudioContent(mimeType=mime_type):
            return f"[Audio: {mime_type}]"
        case _ as unreachable:
            assert_never(unreachable)
