"""High-level pipeline functions for agent execution."""

from enum import Enum
from typing import TYPE_CHECKING, Literal, get_args

from agentpool.agents.native_agent import Agent
from agentpool.log import get_logger


if TYPE_CHECKING:
    from collections.abc import Callable

    from agentpool.common_types import ModelType


logger = get_logger(__name__)


async def get_structured[T](
    prompt: str,
    response_type: type[T],
    model: ModelType,
    *,
    system_prompt: str | None = None,
    max_retries: int = 3,
    error_handler: Callable[[Exception], T | None] | None = None,
) -> T:
    """Get structured output from LLM using function calling.

    This function creates a temporary agent that uses the class constructor
    as a tool to generate structured output. It handles:
    - Type conversion from Python types to JSON schema
    - Constructor parameter validation
    - Error handling with optional recovery

    Args:
        prompt: The prompt to send to the LLM
        response_type: The type to create (class with typed constructor)
        model: model to use
        system_prompt: Optional system instructions
        max_retries: Max attempts for parsing (default: 3)
        error_handler: Optional error handler for recovery

    Returns:
        Instance of response_type

    Example:
        ```python
        class TaskResult:
            '''Analysis result for a task.'''
            def __init__(
                self,
                success: bool,
                message: str,
                due_date: datetime | None = None
            ):
                self.success = success
                self.message = message
                self.due_date = due_date

        result = await get_structured(
            "Analyze task: Deploy monitoring",
            TaskResult,
            system_prompt="You analyze task success"
        )
        print(f"Success: {result.success}")
        ```

    Raises:
        TypeError: If response_type is not a valid type
        ValueError: If constructor schema cannot be created
        Exception: If LLM call fails and no error_handler recovers
    """
    """Get structured output from LLM using function calling."""
    async with Agent(
        model=model,
        system_prompt=system_prompt or [],
        name="structured",
        retries=max_retries,
    ) as agent:
        try:
            return await agent.talk.extract(prompt, response_type)
        except Exception as e:
            if error_handler and (err_result := error_handler(e)):
                return err_result
            raise


async def get_structured_multiple[T](
    prompt: str,
    target: type[T],
    model: ModelType,
) -> list[T]:
    """Extract multiple structured instances from text."""
    async with Agent(model=model, name="structured") as agent:
        return await agent.talk.extract_multiple(prompt, target)


async def pick_one[T](
    prompt: str,
    options: type[T | Enum] | list[T],
    model: ModelType,
) -> T:
    """Pick one option from a list of choices."""
    instances: dict[str, T] = {}

    # Create mapping and descriptions
    if isinstance(options, type):
        if issubclass(options, Enum):
            choices = {e.name: (e.value, str(e.value)) for e in options}
        else:
            literal_opts = get_args(options)
            choices = {str(opt): (opt, str(opt)) for opt in literal_opts}
    else:  # List
        choices = {str(i): (opt, repr(opt)) for i, opt in enumerate(options)}

    async def select_option(option: Literal[tuple(choices.keys())]) -> str:  # ty: ignore[invalid-type-form]
        """Pick one of the available options.

        Args:
            option: Which option to pick
        """
        instances["selected"] = choices[option][0]
        return f"Selected: {option}"

    # Add options to docstring
    docs = "\n".join(f"- {k}: {desc}" for k, (_, desc) in choices.items())
    assert select_option.__doc__
    select_option.__doc__ += f"\nOptions:\n{docs}"
    sys_prompt = "Select the most appropriate option based on the context."
    async with Agent(model=model, system_prompt=sys_prompt) as agent:
        agent.tools.register_tool(select_option, enabled=True)
        await agent.run(prompt)
        return instances["selected"]
