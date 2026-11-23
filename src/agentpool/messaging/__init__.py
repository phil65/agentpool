"""Core messsaging classes for AgentPool."""

from agentpool.messaging.chat_filesystem import ChatMessageFileSystem
from agentpool.messaging.messages import ChatMessage, TokenCost, AgentResponse, TeamResponse
from agentpool.messaging.message_container import ChatMessageList
from agentpool.messaging.messagenode import MessageNode
from agentpool.messaging.message_history import MessageHistory
from agentpool.messaging.compaction import (
    CompactionPipeline,
    CompactionStep,
    FilterBinaryContent,
    FilterEmptyMessages,
    FilterRetryPrompts,
    FilterThinking,
    FilterToolCalls,
    KeepFirstAndLast,
    KeepFirstMessages,
    KeepLastMessages,
    Summarize,
    TokenBudget,
    TruncateTextParts,
    TruncateToolOutputs,
    WhenMessageCountExceeds,
    balanced_context,
    minimal_context,
    summarizing_context,
)

__all__ = [
    "AgentResponse",
    "ChatMessage",
    "ChatMessageFileSystem",
    "ChatMessageList",
    "CompactionPipeline",
    "CompactionStep",
    "FilterBinaryContent",
    "FilterEmptyMessages",
    "FilterRetryPrompts",
    "FilterThinking",
    "FilterToolCalls",
    "KeepFirstAndLast",
    "KeepFirstMessages",
    "KeepLastMessages",
    "MessageHistory",
    "MessageNode",
    "Summarize",
    "TeamResponse",
    "TokenBudget",
    "TokenCost",
    "TruncateTextParts",
    "TruncateToolOutputs",
    "WhenMessageCountExceeds",
    "balanced_context",
    "minimal_context",
    "summarizing_context",
]
