"""Base hook classes and types."""

from abc import ABC, abstractmethod
import re
from typing import TYPE_CHECKING, Any, Literal, TypedDict

from agentpool.log import get_logger


if TYPE_CHECKING:
    from exxec import ExecutionEnvironment

logger = get_logger(__name__)

HookEvent = Literal["pre_run", "post_run", "pre_tool_use", "post_tool_use"]


class HookInput(TypedDict, total=False):
    """Input data passed to hooks."""

    # Common fields
    event: HookEvent
    agent_name: str
    session_id: str | None

    # Tool-related fields (pre_tool_use, post_tool_use)
    tool_name: str
    tool_input: dict[str, Any]
    tool_output: Any
    duration_ms: float

    # Run-related fields (pre_run, post_run)
    prompt: str
    result: Any


class HookResult(TypedDict, total=False):
    """Result returned from hook execution."""

    decision: Literal["allow", "deny", "ask"]
    """Decision for pre_* hooks: allow, deny, or ask user."""

    reason: str
    """Explanation for the decision."""

    modified_input: dict[str, Any]
    """Modified input for pre_* hooks (e.g., modified tool_input)."""

    additional_context: str
    """Context to inject into conversation."""

    continue_: bool
    """Whether to continue execution. False = stop."""


class Hook(ABC):
    """Base class for runtime hooks."""

    def __init__(
        self,
        event: HookEvent,
        matcher: str | None = None,
        timeout: float = 60.0,
        enabled: bool = True,
    ) -> None:
        """Initialize hook.

        Args:
            event: The lifecycle event this hook handles.
            matcher: Regex pattern for matching (e.g., tool names). None matches all.
            timeout: Maximum execution time in seconds.
            enabled: Whether this hook is active.
        """
        self.event = event
        self.matcher = matcher
        self.timeout = timeout
        self.enabled = enabled
        self._pattern = re.compile(matcher) if matcher and matcher != "*" else None

    def matches(self, input_data: HookInput) -> bool:
        """Check if this hook should run for the given input.

        Args:
            input_data: The hook input data.

        Returns:
            True if the hook should execute.
        """
        if not self.enabled:
            return False

        # No pattern means match all
        if self._pattern is None:
            return True

        # For tool events, match against tool_name
        if self.event in ("pre_tool_use", "post_tool_use"):
            tool_name = input_data.get("tool_name", "")
            return bool(self._pattern.search(tool_name))

        # For run events, pattern matching doesn't apply (no tool name to match)
        return True

    @abstractmethod
    async def execute(
        self,
        input_data: HookInput,
        env: ExecutionEnvironment | None = None,
    ) -> HookResult:
        """Execute the hook.

        Args:
            input_data: The hook input data.
            env: Optional execution environment from the agent. Command hooks
                use this to run in the same environment as the agent's tools.

        Returns:
            Hook result with decision and optional modifications.
        """
        ...

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}("
            f"event={self.event!r}, matcher={self.matcher!r}, enabled={self.enabled})"
        )
