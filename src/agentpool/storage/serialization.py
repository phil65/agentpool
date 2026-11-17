"""Serialization utilities."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import ConfigDict, TypeAdapter
from pydantic_ai import ModelMessagesTypeAdapter, ModelRequestPart, ModelResponsePart

from agentpool.log import get_logger


if TYPE_CHECKING:
    from collections.abc import Sequence

    from pydantic_ai import ModelMessage


logger = get_logger(__name__)

# Type adapter for (de)serializing request/response part sequences. The element type
# matters: an untyped adapter would deserialize to plain dicts.
parts_adapter = TypeAdapter(
    list[ModelRequestPart | ModelResponsePart],
    config=ConfigDict(ser_json_bytes="base64", val_json_bytes="base64"),
)

# Type adapter for (de)serializing ModelMessage sequences (bytes as base64)
messages_adapter = ModelMessagesTypeAdapter


def deserialize_parts(parts_json: str | None) -> Sequence[ModelRequestPart | ModelResponsePart]:
    """Deserialize pydantic-ai message parts from JSON string.

    Args:
        parts_json: JSON string representation of parts or None if empty

    Returns:
        Sequence of message parts, empty if deserialization fails
    """
    if not parts_json:
        return []

    try:
        # Deserialize using pydantic's JSON deserialization
        return parts_adapter.validate_json(parts_json.encode())
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to deserialize message parts", error=e)
        return []  # Return empty list on failure


def serialize_parts(parts: Sequence[ModelResponsePart | ModelRequestPart]) -> str | None:
    """Serialize pydantic-ai message parts from ChatMessage.

    Args:
        parts: Sequence of ModelResponsePart from ChatMessage.parts

    Returns:
        JSON string representation of parts or None if empty
    """
    if not parts:
        return None

    try:
        # Convert parts to serializable format
        serializable_parts = []
        for part in parts:
            # Handle retry feedback context serialization issues
            from pydantic_ai import RetryFeedbackPart

            if isinstance(part, RetryFeedbackPart) and isinstance(part.content, list):
                for content in part.content:
                    if isinstance(content, dict) and "ctx" in content:
                        content["ctx"] = {k: str(v) for k, v in content["ctx"].items()}
            serializable_parts.append(part)

        # Serialize using pydantic's JSON serialization
        return parts_adapter.dump_json(serializable_parts).decode()
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to serialize message parts", error=e)
        return str(parts)  # Fallback to string representation


def deserialize_messages(messages_json: str | None) -> list[ModelMessage]:
    """Deserialize pydantic-ai ModelMessage list from JSON string.

    Args:
        messages_json: JSON string representation of messages or None if empty

    Returns:
        List of ModelMessage objects, empty if deserialization fails
    """
    if not messages_json:
        return []

    try:
        # Deserialize using pydantic's JSON deserialization
        return messages_adapter.validate_json(messages_json.encode())
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to deserialize model messages", error=e)
        return []  # Return empty list on failure


def serialize_messages(messages: Sequence[ModelMessage]) -> str | None:
    """Serialize pydantic-ai ModelMessage list to JSON string.

    Args:
        messages: Sequence of ModelMessage objects from ChatMessage.messages

    Returns:
        JSON string representation of messages or None if empty
    """
    if not messages:
        return None

    try:
        # Convert messages to serializable format
        serializable_messages = []
        for message in messages:
            # Handle retry feedback context serialization issues
            from pydantic_ai import ModelRequest, RetryFeedbackPart

            if isinstance(message, ModelRequest):
                for part in message.parts:
                    if isinstance(part, RetryFeedbackPart) and isinstance(part.content, list):
                        for content in part.content:
                            if isinstance(content, dict) and "ctx" in content:
                                content["ctx"] = {k: str(v) for k, v in content["ctx"].items()}
            serializable_messages.append(message)

        # Serialize using pydantic's JSON serialization
        return messages_adapter.dump_json(serializable_messages).decode()
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to serialize model messages", error=e)
        return str(messages)  # Fallback to string representation
