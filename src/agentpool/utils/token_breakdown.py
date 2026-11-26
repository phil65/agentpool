"""Token breakdown utilities for analyzing context window usage."""

from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import anyenv
from pydantic_ai import (
    ModelRequest,
    ModelResponse,
    RunUsage,
    SystemPromptPart,
    ThinkingPart,
    ToolCallPart,
    ToolDefinition,
)
from pydantic_ai.models import ModelRequestParameters


if TYPE_CHECKING:
    from collections.abc import Sequence

    from pydantic_ai import (
        ModelMessage,
        ModelResponsePart,
        ModelSettings,
        TextPart,
        UserContent,
    )
    from pydantic_ai.models import Model

    from agentpool.messaging.messages import TokenCost


DEFAULT_ENCODING_MODEL = "gpt-4"


@dataclass
class TokenUsage:
    """Single item's token count."""

    token_count: int
    label: str


@dataclass
class RunTokenUsage:
    """Token usage for a single agent run."""

    run_id: str | None
    token_count: int
    request_count: int


@dataclass
class TokenBreakdown:
    """Complete token breakdown of context."""

    total_tokens: int

    system_prompts: list[TokenUsage]
    tool_definitions: list[TokenUsage]
    runs: list[RunTokenUsage]

    approximate: bool

    @property
    def system_prompts_tokens(self) -> int:
        return sum(t.token_count for t in self.system_prompts)

    @property
    def tool_definitions_tokens(self) -> int:
        return sum(t.token_count for t in self.tool_definitions)

    @property
    def conversation_tokens(self) -> int:
        return sum(r.token_count for r in self.runs)


def _normalize_tool_schema(tool: ToolDefinition | dict[str, Any]) -> dict[str, Any]:
    """Convert a ToolDefinition or dict to a consistent dict format."""
    if isinstance(tool, ToolDefinition):
        return {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters_json_schema,
        }
    return tool


def count_tokens(text: str, model_name: str | None = None) -> int:
    """Count tokens using tiktoken.

    Args:
        text: The text to count tokens for.
        model_name: The model name for encoding selection.

    Returns:
        The number of tokens in the text.
    """
    try:
        import tiktoken
    except ImportError:
        # Rough approximation: ~4 chars per token
        return len(text) // 4

    try:
        encoding = tiktoken.encoding_for_model(model_name or DEFAULT_ENCODING_MODEL)
    except KeyError:
        # Fall back to cl100k_base for unknown models
        encoding = tiktoken.get_encoding("cl100k_base")

    return len(encoding.encode(text))


async def calculate_usage_from_parts(
    input_parts: Sequence[UserContent],
    response_parts: Sequence[ModelResponsePart],
    text_content: str,
    model_name: str | None = None,
    provider: str | None = None,
) -> tuple[RunUsage, TokenCost | None]:
    """Calculate token usage and cost from input/output parts.

    This is used by agents that don't receive usage info from the backend
    (like ACP and AG-UI agents) to approximate token counts.

    Args:
        input_parts: Input parts (prompts, pending parts) sent to the agent
        response_parts: Response parts received (text, thinking, tool calls)
        text_content: The final text content of the response
        model_name: Model name for token counting and cost calculation
        provider: Provider name for cost calculation

    Returns:
        Tuple of (RunUsage, TokenCost or None)
    """
    from agentpool.messaging.messages import TokenCost

    # Input tokens from prompts
    input_text = " ".join(str(p) for p in input_parts)
    input_tokens = count_tokens(input_text, model_name)

    # Output tokens from response content
    output_text = text_content
    for part in response_parts:
        if isinstance(part, ThinkingPart) and part.content:
            output_text += part.content
        elif isinstance(part, ToolCallPart) and part.args:
            args_str = anyenv.dump_json(part.args) if not isinstance(part.args, str) else part.args
            output_text += args_str
    output_tokens = count_tokens(output_text, model_name)

    # Build usage
    usage = RunUsage(input_tokens=input_tokens, output_tokens=output_tokens)
    run_usage = RunUsage(input_tokens=input_tokens, output_tokens=output_tokens)

    # Calculate cost
    cost_info = await TokenCost.from_usage(
        usage=run_usage,
        model=model_name or "unknown",
        provider=provider,
    )

    return usage, cost_info


def _extract_system_prompts(messages: Sequence[ModelMessage]) -> list[str]:
    """Extract all system prompt contents from messages."""
    prompts: list[str] = []
    for message in messages:
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, SystemPromptPart):
                    prompts.append(part.content)  # noqa: PERF401
    return prompts


def _group_messages_by_run(
    messages: Sequence[ModelMessage],
) -> dict[str | None, list[ModelMessage]]:
    """Group messages by their run_id."""
    groups: dict[str | None, list[ModelMessage]] = defaultdict(list)
    for message in messages:
        run_id = message.run_id
        groups[run_id].append(message)
    return dict(groups)


def _messages_to_text(messages: Sequence[ModelMessage]) -> str:
    """Convert messages to a text representation for token counting."""
    text_parts: list[str] = []
    for message in messages:
        if isinstance(message, ModelRequest):
            for request_part in message.parts:
                if hasattr(request_part, "content") and isinstance(request_part.content, str):
                    text_parts.append(request_part.content)  # noqa: PERF401
        elif isinstance(message, ModelResponse):
            if text := message.text:
                text_parts.append(text)
            for part in message.parts:
                if isinstance(part, ToolCallPart):
                    # Tool call arguments
                    args = part.args
                    if isinstance(args, str):
                        text_parts.append(args)
                    elif args:
                        text_parts.append(anyenv.dump_json(args))
    return "\n".join(text_parts)


async def get_token_breakdown(
    model: Model,
    messages: Sequence[ModelMessage],
    tool_schemas: Sequence[ToolDefinition | dict[str, Any]] | None = None,
    model_settings: ModelSettings | None = None,
) -> TokenBreakdown:
    """Get a breakdown of token usage by component.

    Uses model.count_tokens() if available, falls back to tiktoken.

    Args:
        model: The model to use for token counting.
        messages: The message history to analyze.
        tool_schemas: Tool definitions or raw JSON schemas.
        model_settings: Optional model settings.

    Returns:
        A TokenBreakdown with detailed token usage by component.
    """
    tool_schemas = tool_schemas or []
    approximate = False
    model_name = model.model_name or "gpt-4"

    # Try to use model.count_tokens(), fall back to tiktoken
    async def count_tokens_for_messages(msgs: Sequence[ModelMessage]) -> int:
        nonlocal approximate
        try:
            # Build minimal ModelRequestParameters for counting
            params = ModelRequestParameters()
            usage = await model.count_tokens(list(msgs), model_settings, params)
        except NotImplementedError:
            approximate = True
            return count_tokens(_messages_to_text(msgs), model_name)
        else:
            return usage.input_tokens

    # Extract and count system prompts
    system_prompt_contents = _extract_system_prompts(messages)
    system_prompt_usages: list[TokenUsage] = []
    for i, content in enumerate(system_prompt_contents):
        token_count = count_tokens(content, model_name)
        label = content[:50] + "..." if len(content) > 50 else content  # noqa: PLR2004
        system_prompt_usages.append(
            TokenUsage(token_count=token_count, label=f"System prompt {i + 1}: {label}")
        )
    # Mark as approximate since we're using tiktoken for individual prompts
    if system_prompt_usages:
        approximate = True

    # Count tool definition tokens
    tool_usages: list[TokenUsage] = []
    for tool in tool_schemas:
        schema = _normalize_tool_schema(tool)
        schema_text = anyenv.dump_json(schema)
        token_count = count_tokens(schema_text, model_name)
        tool_name = schema.get("name", "unknown")
        tool_usages.append(TokenUsage(token_count=token_count, label=tool_name))
    if tool_usages:
        approximate = True

    # Group messages by run and count tokens per run
    run_groups = _group_messages_by_run(messages)
    run_usages: list[RunTokenUsage] = []
    for run_id, run_messages in run_groups.items():
        token_count = await count_tokens_for_messages(run_messages)
        request_count = sum(1 for m in run_messages if isinstance(m, ModelRequest))
        run_usages.append(
            RunTokenUsage(
                run_id=run_id,
                token_count=token_count,
                request_count=request_count,
            )
        )

    # Calculate total
    total = (
        sum(u.token_count for u in system_prompt_usages)
        + sum(u.token_count for u in tool_usages)
        + sum(r.token_count for r in run_usages)
    )

    return TokenBreakdown(
        total_tokens=total,
        system_prompts=system_prompt_usages,
        tool_definitions=tool_usages,
        runs=run_usages,
        approximate=approximate,
    )


def format_breakdown(breakdown: TokenBreakdown, detailed: bool = False) -> str:
    """Format a token breakdown for display."""
    lines: list[str] = []
    # Header
    approx_marker = " (approximate)" if breakdown.approximate else ""
    lines.append(f"Token Breakdown{approx_marker}")
    lines.append("=" * 50)
    # Summary
    lines.append(f"Total tokens: {breakdown.total_tokens:,}")
    lines.append("")
    # Category breakdown
    lines.append("By category:")
    lines.append(f"  System prompts: {breakdown.system_prompts_tokens:,} tokens")
    lines.append(f"  Tool definitions: {breakdown.tool_definitions_tokens:,} tokens")
    lines.append(f"  Conversation: {breakdown.conversation_tokens:,} tokens")
    if detailed:
        lines.append("")
        lines.append("-" * 50)
        # System prompts detail
        if breakdown.system_prompts:
            lines.append("")
            lines.append("System Prompts:")
            for sp in breakdown.system_prompts:
                lines.append(f"  [{sp.token_count:,} tokens] {sp.label}")  # noqa: PERF401
        # Tool definitions detail
        if breakdown.tool_definitions:
            lines.append("")
            lines.append("Tool Definitions:")
            for tool in breakdown.tool_definitions:
                lines.append(f"  [{tool.token_count:,} tokens] {tool.label}")  # noqa: PERF401
        # Runs detail
        if breakdown.runs:
            lines.append("")
            lines.append("Conversation by Run:")
            for run in breakdown.runs:
                run_label = run.run_id[:8] + "..." if run.run_id else "(no run_id)"
                lines.append(
                    f"  [{run.token_count:,} tokens] {run_label} ({run.request_count} requests)"
                )

    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    import asyncio

    from pydantic_ai import (
        ImageUrl,
        ModelRequest,
        ModelResponse,
        SystemPromptPart,
        TextPart,
        ToolCallPart,
        ToolDefinition,
        ToolReturnPart,
        UserPromptPart,
    )
    from pydantic_ai.models.test import TestModel

    async def main() -> None:
        # Create sample tool definitions
        tool_definitions = [
            ToolDefinition(
                name="get_weather",
                description="Get the current weather for a city.",
                parameters_json_schema={
                    "type": "object",
                    "properties": {
                        "city": {"type": "string", "description": "The city name"},
                    },
                    "required": ["city"],
                },
            ),
            ToolDefinition(
                name="search_database",
                description="Search a database with a complex query. Supports filtering, sorting, and pagination.",  # noqa: E501
                parameters_json_schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query"},
                        "filters": {
                            "type": "object",
                            "description": "Filter conditions",
                            "additionalProperties": True,
                        },
                        "sort_by": {"type": "string", "description": "Field to sort by"},
                        "limit": {"type": "integer", "description": "Max results"},
                        "offset": {"type": "integer", "description": "Skip N results"},
                    },
                    "required": ["query"],
                },
            ),
        ]

        # Create sample message history simulating two runs
        messages: list[ModelMessage] = [
            # First run
            ModelRequest(
                parts=[
                    SystemPromptPart(
                        content="You are a helpful assistant with access to weather and time tools."
                    ),
                    UserPromptPart(content="What's the weather in Paris?"),
                ],
                run_id="run-001-abc",
            ),
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="get_weather", args={"city": "Paris"}, tool_call_id="call-1"
                    ),
                ],
                run_id="run-001-abc",
            ),
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="get_weather",
                        content="Sunny, 22°C in Paris",
                        tool_call_id="call-1",
                    ),
                ],
                run_id="run-001-abc",
            ),
            ModelResponse(
                parts=[
                    TextPart(content="The weather in Paris is sunny with a temperature of 22°C."),
                ],
                run_id="run-001-abc",
            ),
            # Second run (continuing conversation) - includes an image
            ModelRequest(
                parts=[
                    UserPromptPart(
                        content=[
                            "What time is it in Tokyo?",
                            ImageUrl(url="https://example.com/tokyo-clock.jpg"),
                        ]
                    ),
                ],
                run_id="run-002-def",
            ),
            ModelResponse(
                parts=[
                    TextPart(content="The current time in Tokyo is 14:30 JST."),
                ],
                run_id="run-002-def",
            ),
        ]

        # Get the breakdown using TestModel (will use tiktoken fallback)
        model = TestModel()
        breakdown = await get_token_breakdown(
            model=model, messages=messages, tool_schemas=tool_definitions
        )
        # Print summary view
        print("SUMMARY VIEW")
        print(format_breakdown(breakdown, detailed=False))
        # Print detailed view
        print("DETAILED VIEW")
        print(format_breakdown(breakdown, detailed=True))

    asyncio.run(main())
