"""Utility functions for message lookups across agents."""

from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from agentpool.agents.base_agent import BaseAgent
    from agentpool.messaging import ChatMessage


def build_message_index(
    agents: dict[str, BaseAgent[Any, Any]],
) -> dict[str, tuple[ChatMessage[Any], str]]:
    """Build an index mapping message IDs to (message, agent_name) tuples.

    Args:
        agents: Dictionary of agent name -> agent instance.

    Returns:
        Dictionary mapping message_id -> (ChatMessage, agent_name).
    """
    return {
        msg.message_id: (msg, agent.name)
        for agent in agents.values()
        for msg in agent.conversation.chat_messages
    }


def get_message_chain(
    message: ChatMessage[Any],
    agents: dict[str, BaseAgent[Any, Any]],
) -> list[str]:
    """Get the chain of agent names that processed a message.

    Reconstructs the forwarding chain by walking parent_id references
    across all agents' conversations.

    Args:
        message: The message to trace back.
        agents: Dictionary of agent name -> agent instance.

    Returns:
        List of agent names in order from origin to current message's agent.
        Empty list if message has no parent or parent not found.
    """
    message_index = build_message_index(agents)

    chain: list[str] = []
    current_id = message.parent_id

    while current_id and current_id in message_index:
        parent_msg, agent_name = message_index[current_id]
        # Only add agent name if it's different from previous (avoid duplicates)
        if not chain or chain[-1] != agent_name:
            chain.append(agent_name)
        current_id = parent_msg.parent_id

    # Reverse to get origin -> current order
    chain.reverse()
    return chain


def context_tokens(message: ChatMessage[Any]) -> int | None:
    """Tokens in the model's context after a run: the size of its last request.

    The run usage sums every request of a tool loop, so it overcounts the context;
    it is only used when the run consisted of a single request.
    """
    from pydantic_ai import ModelResponse

    for model_message in reversed(message.messages):
        if isinstance(model_message, ModelResponse) and model_message.usage.total_tokens:
            return model_message.usage.input_tokens + model_message.usage.output_tokens
    usage = message.usage
    if usage.requests <= 1 and usage.total_tokens:
        return usage.input_tokens + usage.output_tokens
    return None
