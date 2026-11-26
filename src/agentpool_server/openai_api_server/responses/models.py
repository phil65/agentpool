"""Models for OpenAI responses endpoint."""

import base64
from typing import TYPE_CHECKING, Any, Literal, assert_never

from openai.types.responses import EasyInputMessageParam
from pydantic import Field
from pydantic_ai import BinaryContent, DocumentUrl, ImageUrl, UploadedFile
from schemez import Schema


if TYPE_CHECKING:
    from openai.types.responses.response_input_content_param import ResponseInputContentParam
    from pydantic_ai import UserContent


ReasoningEffort = Literal["none", "minimal", "low", "medium", "high", "xhigh"]
ServiceTier = Literal["auto", "default", "flex"]
Truncation = Literal["auto", "disabled"]
GenerateSummary = Literal["auto", "concise", "detailed"]


class Reasoning(Schema):
    """Reasoning/thinking configuration."""

    effort: ReasoningEffort = "medium"
    generate_summary: GenerateSummary | None = None
    summary: GenerateSummary | None = None


class ResponseRequest(Schema):
    """Request for /v1/responses endpoint."""

    model: str
    input: str | list[EasyInputMessageParam] | None = None
    instructions: str | None = None
    previous_response_id: str | None = None
    stream: bool = False
    temperature: float = 1.0
    tools: list[dict[str, Any]] = Field(default_factory=list)
    tool_choice: str = "auto"
    max_output_tokens: int | None = None
    max_tool_calls: int | None = None
    parallel_tool_calls: bool = True
    reasoning: Reasoning | None = None
    store: bool = True
    truncation: Truncation | None = None
    service_tier: ServiceTier | None = None
    user: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)


def _convert_content_part(part: ResponseInputContentParam) -> UserContent | None:
    """Convert a single OpenAI input content part to pydantic-ai UserContent."""
    match part:
        case {"type": "input_text", "text": str(text)}:
            return text
        case {"type": "input_image", "image_url": str(url), **rest}:
            detail = rest.get("detail")
            metadata = {"detail": detail} if detail else None
            return ImageUrl(url=url, vendor_metadata=metadata)
        case {"type": "input_image" | "input_file", "file_id": str(file_id)}:
            return UploadedFile(file_id=file_id, provider_name="openai")
        case {"type": "input_file", "file_url": str(url)}:
            return DocumentUrl(url=url)
        case {"type": "input_file", "file_data": str(data_str), "filename": str(filename)}:
            data = base64.b64decode(data_str)
            media_type = (
                "application/pdf" if filename.endswith(".pdf") else "application/octet-stream"
            )
            return BinaryContent(data=data, media_type=media_type)
        case _:
            return None


def extract_user_content(request: ResponseRequest) -> list[UserContent]:
    """Extract user content from a ResponseRequest as pydantic-ai UserContent parts.

    Raises:
        ValueError: If input format is invalid or required fields are missing.
    """
    match request.input:
        case str():
            return [request.input]
        case []:
            msg = "'input' must not be an empty list"
            raise ValueError(msg)
        case list():
            last_msg = request.input[-1]
            msg_content = last_msg["content"]
            if isinstance(msg_content, str):
                return [msg_content]
            parts = [converted for p in msg_content if (converted := _convert_content_part(p))]
            if not parts:
                return [""]
            return parts
        case None:
            if request.previous_response_id is None:
                msg = "Either 'input' or 'previous_response_id' is required"
                raise ValueError(msg)
            return [""]
        case _ as unreachable:
            assert_never(unreachable)
