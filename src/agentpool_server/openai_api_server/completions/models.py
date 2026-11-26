"""OpenAI-compatible API models for AgentPool chat completions."""

from typing import Any, Literal

from openai.types import Model
from openai.types.chat import ChatCompletionMessageToolCall
from openai.types.chat.chat_completion_message_function_tool_call import Function
from pydantic import Field
from schemez import Schema


class OpenAIModelInfo(Model):
    """Exended OpenAI model info format."""

    description: str | None = None
    permissions: list[str] = Field(default_factory=list)


class OpenAIMessage(Schema):
    """OpenAI chat message format (for request input).

    Covers all roles in a single model for easy request parsing.
    """

    role: Literal["system", "user", "assistant", "tool", "function"]
    content: str | None = None
    name: str | None = None
    function_call: Function | None = None
    tool_calls: list[ChatCompletionMessageToolCall] | None = None


class ChatCompletionRequest(Schema):
    """OpenAI chat completion request."""

    model: str
    messages: list[OpenAIMessage] = Field(min_length=1)
    stream: bool = False
    temperature: float | None = None
    max_tokens: int | None = None
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | None = Field(default="auto")
