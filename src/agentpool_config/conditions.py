"""Condition configuration."""

from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import TYPE_CHECKING, Annotated, Any, Literal, assert_never

from pydantic import ConfigDict, Field, ImportString
from schemez import Schema


if TYPE_CHECKING:
    from agentpool.talk.registry import EventContext


class ConnectionCondition(Schema):
    """Base class for connection control conditions."""

    type: str = Field(init=False, title="Condition type")
    """Discriminator for condition types."""

    name: str | None = Field(
        default=None,
        examples=["exit_condition", "message_filter", "cost_check"],
        title="Condition name",
    )
    """Optional name for the condition for referencing."""

    model_config = ConfigDict(frozen=True)

    async def check(self, context: EventContext[Any]) -> bool:
        """Check if condition is met."""
        raise NotImplementedError


class Jinja2Condition(ConnectionCondition):
    """Triggers based on Jinja2 template."""

    model_config = ConfigDict(json_schema_extra={"title": "Jinja2 Template Condition"})

    type: Literal["jinja2"] = Field("jinja2", init=False)
    """Jinja2 template-based condition."""

    template: str = Field(
        examples=[
            "{{ ctx.stats.message_count > 10 }}",
            "{{ 'error' in ctx.message.content }}",
            "{{ ctx.stats.total_cost > 1.0 }}",
        ],
        title="Jinja2 template",
    )
    """Jinja2 template to evaluate."""

    async def check(self, context: EventContext[Any]) -> bool:
        from jinjarope import Environment

        from agentpool.utils.time_utils import get_now

        env = Environment(trim_blocks=True, lstrip_blocks=True, enable_async=True)
        template = env.from_string(self.template)
        result = await template.render_async(ctx=context, now=get_now())
        return result.strip().lower() == "true" or bool(result)


class WordMatchCondition(ConnectionCondition):
    """Triggers when word/phrase is found in message."""

    model_config = ConfigDict(json_schema_extra={"title": "Word Match Condition"})

    type: Literal["word_match"] = Field("word_match", init=False)
    """Word-comparison-based condition."""

    words: list[str] = Field(
        examples=[["error", "failed"], ["complete", "done", "finished"], ["urgent"]],
        title="Words to match",
    )
    """Words or phrases to match in messages."""

    case_sensitive: bool = Field(default=False, title="Case sensitive matching")
    """Whether to match case-sensitively."""

    mode: Literal["any", "all"] = Field(
        default="any",
        examples=["any", "all"],
        title="Matching mode",
    )
    """Match mode:
    - any: Trigger if any word matches
    - all: Require all words to match
    """

    async def check(self, context: EventContext[Any]) -> bool:
        """Triggers if message contains specified words."""
        text = str(context.message.content)
        if not self.case_sensitive:
            text = text.lower()
            words = [w.lower() for w in self.words]
        else:
            words = self.words

        matches = [w in text for w in words]
        return all(matches) if self.mode == "all" else any(matches)


class MessageCountCondition(ConnectionCondition):
    """Triggers after N messages."""

    model_config = ConfigDict(json_schema_extra={"title": "Message Count Condition"})

    type: Literal["message_count"] = Field("message_count", init=False)
    """Message-count-based condition."""

    max_messages: int = Field(gt=0, examples=[10, 50, 100], title="Maximum message count")
    """Maximum number of messages before triggering."""

    count_mode: Literal["total", "per_agent"] = Field(
        default="total",
        examples=["total", "per_agent"],
        title="Message counting mode",
    )
    """How to count messages:
    - total: All messages in conversation
    - per_agent: Messages from each agent separately
    """

    async def check(self, context: EventContext[Any]) -> bool:
        """Check if message count threshold is reached."""
        if self.count_mode == "total":
            return context.stats.message_count >= self.max_messages

        # Count per agent
        return context.stats.count_from(context.message.name) >= self.max_messages


class TimeCondition(ConnectionCondition):
    """Triggers after time period."""

    model_config = ConfigDict(json_schema_extra={"title": "Time-based Condition"})

    type: Literal["time"] = Field("time", init=False)
    """Time-based condition."""

    duration: timedelta = Field(title="Duration threshold")
    """How long the connection should stay active."""

    async def check(self, context: EventContext[Any]) -> bool:
        """Check if time duration has elapsed."""
        from agentpool.utils.time_utils import get_now

        elapsed = get_now() - context.stats.start_time
        return elapsed >= self.duration


class TokenThresholdCondition(ConnectionCondition):
    """Triggers after token threshold is reached."""

    model_config = ConfigDict(json_schema_extra={"title": "Token Threshold Condition"})

    type: Literal["token_threshold"] = Field("token_threshold", init=False)
    """Type discriminator."""

    max_tokens: int = Field(gt=0, examples=[1000, 4000, 8000], title="Maximum token count")
    """Maximum number of tokens allowed."""

    count_type: Literal["total", "prompt", "completion"] = Field(
        default="total",
        examples=["total", "prompt", "completion"],
        title="Token counting type",
    )
    """What tokens to count:
    - total: All tokens used
    - prompt: Only prompt tokens
    - completion: Only completion tokens
    """

    async def check(self, context: EventContext[Any]) -> bool:
        """Check if token threshold is reached."""
        match self.count_type:
            case "total":
                return context.stats.token_count >= self.max_tokens
            case "prompt":
                return context.message.usage.input_tokens >= self.max_tokens
            case "completion":
                return context.message.usage.output_tokens >= self.max_tokens
            case _ as unreachable:
                assert_never(unreachable)


class CostCondition(ConnectionCondition):
    """Triggers when cost threshold is reached."""

    model_config = ConfigDict(json_schema_extra={"title": "Cost Condition"})

    type: Literal["cost"] = Field("cost", init=False)
    """Cost-based condition."""

    max_cost: float = Field(gt=0.0, examples=[1.0, 5.0, 10.0], title="Maximum cost threshold")
    """Maximum cost in USD."""

    async def check(self, context: EventContext[Any]) -> bool:
        """Check if cost limit is reached."""
        return context.stats.total_cost >= self.max_cost


class CostLimitCondition(ConnectionCondition):
    """Triggers when cost limit is reached."""

    model_config = ConfigDict(json_schema_extra={"title": "Cost Limit Condition"})

    type: Literal["cost_limit"] = Field("cost_limit", init=False)
    """Cost-limit condition."""

    max_cost: float = Field(gt=0.0, examples=[1.0, 5.0, 10.0], title="Cost limit")
    """Maximum cost in USD before triggering."""

    async def check(self, context: EventContext[Any]) -> bool:
        """Check if cost limit is reached."""
        if not context.message.cost_info:
            return False
        return float(context.message.cost_info.total_cost) >= self.max_cost


class CallableCondition(ConnectionCondition):
    """Custom predicate function."""

    model_config = ConfigDict(json_schema_extra={"title": "Callable Condition"})

    type: Literal["callable"] = Field("callable", init=False)
    """Condition based on an import path pointing to a predicate."""

    predicate: ImportString[Callable[..., bool | Awaitable[bool]]] = Field(
        examples=["mymodule.check_condition", "utils.predicates:is_urgent"],
        title="Predicate function",
    )
    """Function to evaluate condition:
    Args:
        message: Current message being processed
        stats: Current connection statistics
    Returns:
        Whether condition is met
    """

    async def check(self, context: EventContext[Any]) -> bool:
        """Execute predicate function."""
        from agentpool.utils.inspection import execute

        val = await execute(self.predicate, context.message, context.stats)
        return bool(val)


class AndCondition(ConnectionCondition):
    """Require all conditions to be met."""

    model_config = ConfigDict(json_schema_extra={"title": "AND Condition"})

    type: Literal["and"] = Field("and", init=False)
    """Condition to AND-combine multiple conditions."""

    conditions: list[ConnectionCondition] = Field(title="AND conditions")
    """List of conditions to check."""

    async def check(self, context: EventContext[Any]) -> bool:
        """Check if all conditions are met."""
        results = [await c.check(context) for c in self.conditions]
        return all(results)


class OrCondition(ConnectionCondition):
    """Require any condition to be met."""

    model_config = ConfigDict(json_schema_extra={"title": "OR Condition"})

    type: Literal["or"] = Field("or", init=False)
    """Condition to OR-combine multiple conditions."""

    conditions: list[ConnectionCondition] = Field(title="OR conditions")
    """List of conditions to check."""

    async def check(self, context: EventContext[Any]) -> bool:
        """Check if any condition is met."""
        results = [await c.check(context) for c in self.conditions]
        return any(results)


# Union type for condition validation
Condition = Annotated[
    WordMatchCondition
    | MessageCountCondition
    | TimeCondition
    | TokenThresholdCondition
    | CostLimitCondition
    | CallableCondition
    | Jinja2Condition
    | AndCondition
    | OrCondition,
    Field(discriminator="type"),
]
