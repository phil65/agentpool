"""Compact FastMCP server demonstrating sampling and elicitation in one workflow."""

from pathlib import Path
from typing import Any, Literal

import anyio
from fastmcp import Context, FastMCP
from fastmcp.tools import ToolResult
from fastmcp.utilities.types import Audio, File, Image
from mcp.types import (
    CreateMessageRequest,
    CreateMessageRequestParams,
    CreateMessageResult,
    ElicitRequest,
    ElicitRequestFormParams,
    ElicitResult,
    InputRequiredResult,
    ModelHint,
    ModelPreferences,
    SamplingMessage,
    TextContent,
)
from pydantic import BaseModel


mcp = FastMCP("Test Server")


@mcp.tool
async def sample_test(ctx: Context, message: str) -> str | InputRequiredResult:
    """Ask the client's LLM a yes/no question via sampling (multi round-trip)."""
    match (ctx.input_responses or {}).get("answer"):
        case CreateMessageResult(content=TextContent(text=text)):
            return text
        case None:
            params = CreateMessageRequestParams(
                messages=[
                    SamplingMessage(role="user", content=TextContent(type="text", text=message))
                ],
                max_tokens=500,
                system_prompt="only 'yes' or 'no'.",
                model_preferences=ModelPreferences(hints=[ModelHint(name="gpt-5-nano")]),
            )
            return InputRequiredResult(
                input_requests={"answer": CreateMessageRequest(params=params)}
            )
        case other:
            return f"Unexpected sampling response: {other!r}"


@mcp.tool
async def test_elicitation(ctx: Context, message: str) -> str | InputRequiredResult:
    """Ask the user a yes/no question via elicitation (multi round-trip)."""
    match (ctx.input_responses or {}).get("confirm"):
        case ElicitResult(action="accept", content=content):
            return f"accepted: {(content or {}).get('value')}"
        case ElicitResult(action=action):
            return action
        case None:
            params = ElicitRequestFormParams(
                message=message,
                requested_schema={
                    "type": "object",
                    "properties": {"value": {"type": "boolean"}},
                    "required": ["value"],
                },
            )
            return InputRequiredResult(input_requests={"confirm": ElicitRequest(params=params)})
        case other:
            return f"Unexpected elicitation response: {other!r}"


@mcp.tool
async def test_progress(ctx: Context, message: str) -> str:
    """Test progress reporting with the given message."""
    await ctx.report_progress(0, 100, "first step")
    await anyio.sleep(0.5)
    await ctx.report_progress(50, 100, "second step")
    await anyio.sleep(0.5)
    await ctx.report_progress(99, 100, "third step")
    await anyio.sleep(0.5)
    return f"Progress test completed with message: {message}"


@mcp.prompt
async def static_prompt():
    return "Static!"


@mcp.prompt
async def dynamic_prompt(some_arg: str):
    return f"Dynamic! {some_arg}"


class WeatherData(BaseModel):
    """Weather data model for PydanticAI."""

    temperature: float
    humidity: int


class StructuredResponse(BaseModel):
    """Structured response model for PydanticAI."""

    result: str
    data: WeatherData
    timestamp: str


@mcp.tool
async def test_return_types(  # noqa: D417
    ctx: Context, return_type: Literal["text", "structured", "mixed"]
) -> str | StructuredResponse | list[dict[str, Any]]:
    """Test different PydanticAI return types based on the return_type parameter.

    Args:
        return_type: One of 'text', 'structured', 'mixed' to test different scenarios
    """
    if return_type == "text":
        # Simple text return
        return "Simple text response for PydanticAI"
    if return_type == "structured":
        # Structured data that should be returned directly
        return StructuredResponse(
            result="success",
            data=WeatherData(temperature=23.5, humidity=65),
            timestamp="2024-01-15T14:30:00Z",
        )
    if return_type == "mixed":
        # This will have both structured output and rich content
        # FastMCP will handle the conversion to appropriate formats
        return [
            {"type": "data", "value": 42},
            {"type": "message", "text": "Rich content example"},
            {"type": "metadata", "info": "Additional context"},
        ]
    return f"Unknown return_type: {return_type}. Use 'text', 'structured', or 'mixed'"


@mcp.tool
async def test_rich_content(  # noqa: D417
    ctx: Context,
    content_type: Literal["image", "audio", "file", "mixed"],
):
    """Test FastMCP rich content types that should be converted to PydanticAI types.

    Args:
        content_type: One of 'image', 'audio', 'file', 'mixed'
    """
    if content_type == "image":
        # Return FastMCP Image - should convert to PydanticAI ImageUrl/BinaryContent
        # Use real PNG file from the test directory
        png_path = Path(__file__).parent / "test_image.png"
        png_data = png_path.read_bytes()
        return Image(data=png_data, format="png")
    if content_type == "audio":
        # Return FastMCP Audio - should convert to PydanticAI AudioUrl/BinaryContent
        # Generate minimal valid WAV header (empty audio)
        wav_data = (
            b"RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00"
            b"\x01\x00D\xac\x00\x00\x88X\x01\x00\x02\x00\x10\x00"
            b"data\x00\x00\x00\x00"
        )
        return Audio(data=wav_data, format="wav")
    if content_type == "file":
        # Return FastMCP File - should convert to PydanticAI DocumentUrl/BinaryContent
        # Generate minimal valid PDF
        pdf_data = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>\nendobj\nxref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n0000000058 00000 n \n0000000115 00000 n \ntrailer\n<< /Size 4 /Root 1 0 R >>\nstartxref\n178\n%%EOF"  # noqa: E501
        return File(data=pdf_data, format="pdf", name="test.pdf")
    if content_type == "mixed":
        # Return mixed content with both structured data and rich content
        png_path = Path(__file__).parent / "test_image.png"
        png_data = png_path.read_bytes()
        return ToolResult(
            content=[
                TextContent(type="text", text="Text description"),
                Image(data=png_data, format="png").to_image_content(),
            ],
            structured_content={"structured_data": "additional info", "count": 42},
        )
    return f"Unknown content_type: {content_type}. Use 'image', 'audio', 'file', or 'mixed'"


@mcp.resource("resource://greeting")
def get_greeting() -> str:
    """Provides a simple greeting message."""
    return "Hello from FastMCP Resources!"


if __name__ == "__main__":
    mcp.run(show_banner=False, log_level="error")
