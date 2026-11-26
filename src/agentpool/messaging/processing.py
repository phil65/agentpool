"""Helper functions for common message processing logic."""

from typing import TYPE_CHECKING, Any

from agentpool.messaging import ChatMessage
from agentpool.prompts.convert import convert_prompts


if TYPE_CHECKING:
    from pydantic_ai import UserContent

    from agentpool.common_types import PromptCompatible
    from agentpool.messaging import MessageNode


async def prepare_prompts(
    *prompt: PromptCompatible,
    parent_id: str | None = None,
    session_id: str | None = None,
) -> tuple[ChatMessage[Any], list[UserContent]]:
    """Prepare prompts for processing.

    Args:
        *prompt: The prompt(s) to prepare.
        parent_id: Optional ID of the parent message (typically the previous response).
        session_id: Optional conversation ID for the user message.

    Returns:
        A tuple of:
            - A ChatMessage representing the user prompt.
            - A list of prompts to be sent to the model.
    """
    prompts = await convert_prompts(prompt)
    user_msg = ChatMessage.user_prompt(message=prompts, parent_id=parent_id, session_id=session_id)
    return user_msg, prompts


async def finalize_message(
    message: ChatMessage[Any], node: MessageNode[Any, Any]
) -> ChatMessage[Any]:
    """Log a node's response, then announce it (the router routes it on ``message_sent``).

    Args:
        message: The response message to finalize
        node: The message node that produced the message

    Returns:
        The finalized message
    """
    await node.log_message(message)
    await node.message_sent.emit(message)
    return message
