"""Compaction configuration for message history management."""

from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import Field
from schemez import Schema


if TYPE_CHECKING:
    from agentpool.messaging.compaction import CompactionPipeline, CompactionStep


class FilterThinkingConfig(Schema):
    """Configuration for FilterThinking step."""

    type: Literal["filter_thinking"] = "filter_thinking"

    def build(self) -> CompactionStep:
        from agentpool.messaging.compaction import FilterThinking

        return FilterThinking()


class FilterRetryPromptsConfig(Schema):
    """Configuration for FilterRetryPrompts step."""

    type: Literal["filter_retry_prompts"] = "filter_retry_prompts"

    def build(self) -> CompactionStep:
        from agentpool.messaging.compaction import FilterRetryPrompts

        return FilterRetryPrompts()


class FilterBinaryContentConfig(Schema):
    """Configuration for FilterBinaryContent step."""

    type: Literal["filter_binary"] = "filter_binary"
    keep_references: bool = False

    def build(self) -> CompactionStep:
        from agentpool.messaging.compaction import FilterBinaryContent

        return FilterBinaryContent(keep_references=self.keep_references)


class FilterToolCallsConfig(Schema):
    """Configuration for FilterToolCalls step."""

    type: Literal["filter_tools"] = "filter_tools"
    exclude_tools: list[str] = Field(default_factory=list)
    include_only: list[str] | None = None

    def build(self) -> CompactionStep:
        from agentpool.messaging.compaction import FilterToolCalls

        return FilterToolCalls(exclude_tools=self.exclude_tools, include_only=self.include_only)


class FilterEmptyMessagesConfig(Schema):
    """Configuration for FilterEmptyMessages step."""

    type: Literal["filter_empty"] = "filter_empty"

    def build(self) -> CompactionStep:
        from agentpool.messaging.compaction import FilterEmptyMessages

        return FilterEmptyMessages()


class TruncateToolOutputsConfig(Schema):
    """Configuration for TruncateToolOutputs step."""

    type: Literal["truncate_tool_outputs"] = "truncate_tool_outputs"
    max_length: int = 2000
    suffix: str = "\n... [truncated]"

    def build(self) -> CompactionStep:
        from agentpool.messaging.compaction import TruncateToolOutputs

        return TruncateToolOutputs(max_length=self.max_length, suffix=self.suffix)


class TruncateTextPartsConfig(Schema):
    """Configuration for TruncateTextParts step."""

    type: Literal["truncate_text"] = "truncate_text"
    max_length: int = 5000
    suffix: str = "\n... [truncated]"

    def build(self) -> CompactionStep:
        from agentpool.messaging.compaction import TruncateTextParts

        return TruncateTextParts(max_length=self.max_length, suffix=self.suffix)


class KeepLastMessagesConfig(Schema):
    """Configuration for KeepLastMessages step."""

    type: Literal["keep_last"] = "keep_last"
    count: int = 10
    count_pairs: bool = True

    def build(self) -> CompactionStep:
        from agentpool.messaging.compaction import KeepLastMessages

        return KeepLastMessages(count=self.count, count_pairs=self.count_pairs)


class KeepFirstMessagesConfig(Schema):
    """Configuration for KeepFirstMessages step."""

    type: Literal["keep_first"] = "keep_first"
    count: int = 2

    def build(self) -> CompactionStep:
        from agentpool.messaging.compaction import KeepFirstMessages

        return KeepFirstMessages(count=self.count)


class KeepFirstAndLastConfig(Schema):
    """Configuration for KeepFirstAndLast step."""

    type: Literal["keep_first_last"] = "keep_first_last"
    first_count: int = 2
    last_count: int = 5

    def build(self) -> CompactionStep:
        from agentpool.messaging.compaction import KeepFirstAndLast

        return KeepFirstAndLast(first_count=self.first_count, last_count=self.last_count)


class TokenBudgetConfig(Schema):
    """Configuration for TokenBudget step."""

    type: Literal["token_budget"] = "token_budget"
    max_tokens: int = 4000
    model: str = "gpt-4o"

    def build(self) -> CompactionStep:
        from agentpool.messaging.compaction import TokenBudget

        return TokenBudget(max_tokens=self.max_tokens, model=self.model)


class SummarizeConfig(Schema):
    """Configuration for Summarize step."""

    type: Literal["summarize"] = "summarize"
    model: str = "openai:gpt-4o-mini"
    threshold: int = 15
    keep_recent: int = 5
    summary_prompt: str | None = None

    def build(self) -> CompactionStep:
        from agentpool.messaging.compaction import Summarize

        kwargs: dict[str, Any] = {
            "model": self.model,
            "threshold": self.threshold,
            "keep_recent": self.keep_recent,
        }
        if self.summary_prompt:
            kwargs["summary_prompt"] = self.summary_prompt
        return Summarize(**kwargs)


class WhenMessageCountExceedsConfig(Schema):
    """Configuration for WhenMessageCountExceeds wrapper."""

    type: Literal["when_count_exceeds"] = "when_count_exceeds"
    threshold: int = 20
    step: "CompactionStepConfig"

    def build(self) -> CompactionStep:
        from agentpool.messaging.compaction import WhenMessageCountExceeds

        return WhenMessageCountExceeds(step=self.step.build(), threshold=self.threshold)


# Union of all config types with discriminator
CompactionStepConfig = Annotated[
    FilterThinkingConfig
    | FilterRetryPromptsConfig
    | FilterBinaryContentConfig
    | FilterToolCallsConfig
    | FilterEmptyMessagesConfig
    | TruncateToolOutputsConfig
    | TruncateTextPartsConfig
    | KeepLastMessagesConfig
    | KeepFirstMessagesConfig
    | KeepFirstAndLastConfig
    | TokenBudgetConfig
    | SummarizeConfig
    | WhenMessageCountExceedsConfig,
    Field(discriminator="type"),
]

# Update forward reference
WhenMessageCountExceedsConfig.model_rebuild()


class CompactionConfig(Schema):
    """Configuration for message compaction/summarization.

    Example YAML:
        ```yaml
        compaction:
          steps:
            - type: filter_thinking
            - type: truncate_tool_outputs
              max_length: 1000
            - type: keep_last
              count: 10
        ```

    Or use a preset:
        ```yaml
        compaction:
          preset: balanced
        ```
    """

    preset: Literal["minimal", "balanced", "summarizing"] | None = None
    """Use a predefined compaction pipeline.

    - minimal: Aggressively minimize context (filter thinking, truncate, keep last 10)
    - balanced: Balance context preservation with size (filter thinking, truncate,
                keep first 2 + last 8)
    - summarizing: Summarize old messages (filter thinking, summarize when > 20 messages)
    """

    steps: list[CompactionStepConfig] = Field(default_factory=list)
    """Ordered list of compaction steps to apply (ignored if preset is set)."""

    def build(self) -> CompactionPipeline:
        """Build a CompactionPipeline from this configuration."""
        from agentpool.messaging.compaction import (
            CompactionPipeline,
            balanced_context,
            minimal_context,
            summarizing_context,
        )

        if self.preset:
            match self.preset:
                case "minimal":
                    return minimal_context()
                case "balanced":
                    return balanced_context()
                case "summarizing":
                    return summarizing_context()

        return CompactionPipeline(steps=[step.build() for step in self.steps])
