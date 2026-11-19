"""Agent interaction patterns."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, Literal, cast, overload

from pydantic import create_model
from schemez import Schema

from agentpool.log import get_logger
from agentpool.messaging import ChatMessage


if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Sequence

    from toprompt import AnyPromptType

    from agentpool import AgentPool, MessageNode
    from agentpool.agents import Agent
    from agentpool.common_types import SupportsStructuredOutput
    from agentpool.delegation.base_team import BaseTeam


ExtractionMode = Literal["structured", "tool_calls"]

type EndCondition = Callable[[list[ChatMessage[Any]], ChatMessage[Any]], bool]

logger = get_logger(__name__)


class LLMPick(Schema):
    """Decision format for LLM response."""

    selection: str  # The label/name of the selected option
    reason: str


class Pick[T](Schema):
    """Type-safe decision with original object."""

    selection: T
    reason: str


class LLMMultiPick(Schema):
    """Multiple selection format for LLM response."""

    selections: list[str]  # Labels of selected options
    reason: str


class MultiPick[T](Schema):
    """Type-safe multiple selection with original objects."""

    selections: list[T]
    reason: str


def get_label(item: Any) -> str:
    """Get label for an item to use in selection.

    Args:
        item: Item to get label for

    Returns:
        Label to use for selection

    Strategy:
        - strings stay as-is
        - types use __name__
        - others use __repr__ for unique identifiable string
    """
    from agentpool import MessageNode

    match item:
        case str():
            return item
        case type():
            return item.__name__
        case MessageNode():
            return item.name or "unnamed_agent"
        case _:
            return repr(item)


class Interactions:
    """Manages agent communication patterns."""

    def __init__(self, agent: SupportsStructuredOutput) -> None:
        self.agent = agent

    @asynccontextmanager
    async def _with_structured_output(
        self, output_type: type
    ) -> AsyncGenerator[SupportsStructuredOutput]:
        """Context manager to temporarily set structured output type and restore afterwards.

        Args:
            output_type: The type to structure output as

        Yields:
            The agent configured for structured output

        Example:
            async with interactions._with_structured_output(MyType) as agent:
                result = await agent.run(prompt)
        """
        # Save original output type
        old_output_type = self.agent._output_type
        try:
            # Configure structured output
            structured_agent = self.agent.to_structured(output_type)
            yield structured_agent
        finally:
            # Restore original output type
            self.agent.to_structured(old_output_type)

    # async def conversation(
    #     self,
    #     other: MessageNode[Any, Any],
    #     initial_message: AnyPromptType,
    #     *,
    #     max_rounds: int | None = None,
    #     end_condition: Callable[[list[ChatMessage[Any]], ChatMessage[Any]], bool] | None = None,
    # ) -> AsyncIterator[ChatMessage[Any]]:
    #     """Maintain conversation between two agents.

    #     Args:
    #         other: Agent to converse with
    #         initial_message: Message to start conversation with
    #         max_rounds: Optional maximum number of exchanges
    #         end_condition: Optional predicate to check for conversation end

    #     Yields:
    #         Messages from both agents in conversation order
    #     """
    #     rounds = 0
    #     messages: list[ChatMessage[Any]] = []
    #     current_message = initial_message
    #     current_node: MessageNode[Any, Any] = self.agent

    #     while True:
    #         if max_rounds and rounds >= max_rounds:
    #             logger.debug("Conversation ended", max_rounds=max_rounds)
    #             return

    #         response = await current_node.run(current_message)
    #         messages.append(response)
    #         yield response

    #         if end_condition and end_condition(messages, response):
    #             logger.debug("Conversation ended: end condition met")
    #             return

    #         # Switch agents for next round
    #         current_node = other if current_node == self.agent else self.agent
    #         current_message = response.content
    #         rounds += 1

    @overload
    async def pick(
        self,
        selections: AgentPool,
        task: str,
        prompt: AnyPromptType | None = None,
    ) -> Pick[Agent[Any, Any]]: ...

    @overload
    async def pick(
        self,
        selections: BaseTeam[Any, Any],
        task: str,
        prompt: AnyPromptType | None = None,
    ) -> Pick[MessageNode[Any, Any]]: ...

    @overload
    async def pick[T: AnyPromptType](
        self,
        selections: Sequence[T] | Mapping[str, T],
        task: str,
        prompt: AnyPromptType | None = None,
    ) -> Pick[T]: ...

    async def pick[T](
        self,
        selections: Sequence[T] | Mapping[str, T] | AgentPool | BaseTeam[Any, Any],
        task: str,
        prompt: AnyPromptType | None = None,
    ) -> Pick[T]:
        """Pick from available options with reasoning.

        Args:
            selections: What to pick from:
                - Sequence of items (auto-labeled)
                - Dict mapping labels to items
                - AgentPool
                - Team
            task: Task/decision description
            prompt: Optional custom selection prompt

        Returns:
            Decision with selected item and reasoning

        Raises:
            ValueError: If no choices available or invalid selection
        """
        # Get items and create label mapping
        from toprompt import to_prompt

        from agentpool import AgentPool
        from agentpool.delegation.base_team import BaseTeam

        match selections:
            case dict():
                label_map = selections
                items: list[Any] = list(selections.values())
            case BaseTeam():
                items = list(selections.nodes)
                label_map = {get_label(item): item for item in items}
            case AgentPool():
                items = list(selections.all_agents.values())
                label_map = {get_label(item): item for item in items}
            case _:
                items = list(selections)
                label_map = {get_label(item): item for item in items}

        if not items:
            raise ValueError("No choices available")

        # Get descriptions for all items
        descriptions = []
        for label, item in label_map.items():
            item_desc = await to_prompt(item)
            descriptions.append(f"{label}:\n{item_desc}")

        default_prompt = f"""Task/Decision: {task}

Available options:
{"-" * 40}
{"\n\n".join(descriptions)}
{"-" * 40}

Select ONE option by its exact label."""

        # Get LLM's string-based decision using structured output
        async with self._with_structured_output(LLMPick) as structured_agent:
            result = await structured_agent.run(prompt or default_prompt)

        # Convert to type-safe decision
        if result.content.selection not in label_map:
            raise ValueError(f"Invalid selection: {result.content.selection}")

        selected = cast(T, label_map[result.content.selection])
        return Pick(selection=selected, reason=result.content.reason)

    @overload
    async def pick_multiple(
        self,
        selections: BaseTeam[Any, Any],
        task: str,
        *,
        min_picks: int = 1,
        max_picks: int | None = None,
        prompt: AnyPromptType | None = None,
    ) -> MultiPick[MessageNode[Any, Any]]: ...

    @overload
    async def pick_multiple(
        self,
        selections: AgentPool,
        task: str,
        *,
        min_picks: int = 1,
        max_picks: int | None = None,
        prompt: AnyPromptType | None = None,
    ) -> MultiPick[Agent[Any, Any]]: ...

    @overload
    async def pick_multiple[T: AnyPromptType](
        self,
        selections: Sequence[T] | Mapping[str, T],
        task: str,
        *,
        min_picks: int = 1,
        max_picks: int | None = None,
        prompt: AnyPromptType | None = None,
    ) -> MultiPick[T]: ...

    async def pick_multiple[T](
        self,
        selections: Sequence[T] | Mapping[str, T] | AgentPool | BaseTeam[Any, Any],
        task: str,
        *,
        min_picks: int = 1,
        max_picks: int | None = None,
        prompt: AnyPromptType | None = None,
    ) -> MultiPick[T]:
        """Pick multiple options from available choices.

        Args:
            selections: What to pick from
            task: Task/decision description
            min_picks: Minimum number of selections required
            max_picks: Maximum number of selections (None for unlimited)
            prompt: Optional custom selection prompt
        """
        from toprompt import to_prompt

        from agentpool import AgentPool
        from agentpool.delegation.base_team import BaseTeam

        match selections:
            case AgentPool():
                items: list[Any] = list(selections.all_agents.values())
                label_map: Mapping[str, Any] = {get_label(item): item for item in items}
            case Mapping():
                label_map = selections
                items = list(selections.values())
            case BaseTeam():
                items = list(selections.nodes)
                label_map = {get_label(item): item for item in items}
            case _:
                items = list(selections)
                label_map = {get_label(item): item for item in items}

        if not items:
            raise ValueError("No choices available")

        if max_picks is not None and max_picks < min_picks:
            raise ValueError(f"max_picks ({max_picks}) cannot be less than min_picks ({min_picks})")

        descriptions = []
        for label, item in label_map.items():
            item_desc = await to_prompt(item)
            descriptions.append(f"{label}:\n{item_desc}")

        picks_info = (
            f"Select between {min_picks} and {max_picks}"
            if max_picks is not None
            else f"Select at least {min_picks}"
        )

        default_prompt = f"""Task/Decision: {task}

Available options:
{"-" * 40}
{"\n\n".join(descriptions)}
{"-" * 40}

{picks_info} options by their exact labels.
List your selections, one per line, followed by your reasoning."""

        # Get LLM's multi-selection using structured output
        async with self._with_structured_output(LLMMultiPick) as structured_agent:
            result = await structured_agent.run(prompt or default_prompt)

        # Validate selections
        invalid = [s for s in result.content.selections if s not in label_map]
        if invalid:
            raise ValueError(f"Invalid selections: {', '.join(invalid)}")
        num_picks = len(result.content.selections)
        if num_picks < min_picks:
            raise ValueError(f"Too few selections: got {num_picks}, need {min_picks}")

        if max_picks and num_picks > max_picks:
            raise ValueError(f"Too many selections: got {num_picks}, max {max_picks}")

        selected = [cast(T, label_map[label]) for label in result.content.selections]
        return MultiPick(selections=selected, reason=result.content.reason)

    async def extract[T](
        self,
        text: str,
        as_type: type[T],
        *,
        prompt: AnyPromptType | None = None,
    ) -> T:
        """Extract single instance of type from text.

        Args:
            text: Text to extract from
            as_type: Type to extract
            prompt: Optional custom prompt
        """
        item_model = Schema.for_class_ctor(as_type)
        final_prompt = prompt or f"Extract {as_type.__name__} from: {text}"
        cls = create_model("Extraction", instance=item_model)
        # Use structured output via context manager
        async with self._with_structured_output(cls) as structured_agent:
            result = await structured_agent.run(final_prompt)
        return as_type(**result.content.instance.model_dump())

    async def extract_multiple[T](
        self,
        text: str,
        as_type: type[T],
        *,
        min_items: int = 1,
        max_items: int | None = None,
        prompt: AnyPromptType | None = None,
    ) -> list[T]:
        """Extract multiple instances of type from text.

        Args:
            text: Text to extract from
            as_type: Type to extract
            min_items: Minimum number of instances to extract
            max_items: Maximum number of instances (None=unlimited)
            prompt: Optional custom prompt
        """
        item_model = Schema.for_class_ctor(as_type)
        final_prompt = prompt or "\n".join([
            f"Extract {as_type.__name__} instances from text.",
            # "Requirements:",
            # f"- Extract at least {min_items} instances",
            # f"- Extract at most {max_items} instances" if max_items else "",
            "\nText to analyze:",
            text,
        ])
        # Create model for individual instance

        class Extraction(Schema):
            instances: list[item_model]  # ty: ignore[invalid-type-form]
            # explanation: str | None = None

        # Use structured output via context manager
        async with self._with_structured_output(Extraction) as structured_agent:
            result = await structured_agent.run(final_prompt)
        num_instances = len(result.content.instances)  # Validate counts
        if len(result.content.instances) < min_items:
            raise ValueError(f"Found only {num_instances} instances, need {min_items}")

        if max_items and num_instances > max_items:
            raise ValueError(f"Found {num_instances} instances, max is {max_items}")
        return [
            as_type(**instance.data if hasattr(instance, "data") else instance.model_dump())
            for instance in result.content.instances
        ]
