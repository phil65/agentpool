"""Callable hook implementation."""

from __future__ import annotations

import asyncio
import inspect
from typing import TYPE_CHECKING, Any

from agentpool.hooks.base import Hook, HookResult
from agentpool.log import get_logger
from agentpool.utils.importing import import_callable


if TYPE_CHECKING:
    from collections.abc import Callable

    from exxec import ExecutionEnvironment

    from agentpool.hooks.base import HookEvent, HookInput


logger = get_logger(__name__)


class CallableHook(Hook):
    """Hook that executes a Python callable.

    The callable receives hook input as a dictionary and should return
    a HookResult dictionary or None.
    """

    def __init__(
        self,
        event: HookEvent,
        fn: Callable[..., HookResult | None] | str,
        matcher: str | None = None,
        timeout: float = 60.0,
        enabled: bool = True,
        arguments: dict[str, Any] | None = None,
    ) -> None:
        """Initialize callable hook.

        Args:
            event: The lifecycle event this hook handles.
            fn: The callable to execute, or import path string.
            matcher: Regex pattern for matching.
            timeout: Maximum execution time in seconds.
            enabled: Whether this hook is active.
            arguments: Additional keyword arguments for the callable.
        """
        super().__init__(event=event, matcher=matcher, timeout=timeout, enabled=enabled)
        self._callable: Callable[..., HookResult | None] | None = None
        self._import_path: str | None = None

        if isinstance(fn, str):
            self._import_path = fn
        else:
            self._callable = fn

        self.arguments = arguments or {}

    @property
    def callable(self) -> Callable[..., HookResult | None]:
        """Get the callable, importing lazily if needed."""
        if self._callable is None:
            if self._import_path is None:
                raise ValueError("No callable or import path provided")
            self._callable = import_callable(self._import_path)
        return self._callable

    async def execute(
        self,
        input_data: HookInput,
        env: ExecutionEnvironment | None = None,
    ) -> HookResult:
        """Execute the callable.

        Args:
            input_data: The hook input data.
            env: Unused. Callable hooks always run in-process.

        Returns:
            Hook result from callable.
        """
        try:
            fn = self.callable
            # Merge input data with additional arguments
            kwargs = {**dict(input_data), **self.arguments}
            # Execute with timeout
            if inspect.iscoroutinefunction(fn):
                result = await asyncio.wait_for(fn(**kwargs), timeout=self.timeout)
            else:
                # Run sync function in executor
                loop = asyncio.get_event_loop()
                result = await asyncio.wait_for(
                    loop.run_in_executor(None, lambda: fn(**kwargs)),
                    timeout=self.timeout,
                )

            # Normalize result
            if result is None:
                return HookResult(decision="allow")

            return _normalize_result(result)

        except TimeoutError:
            fn_path = self._import_path or str(self._callable)
            logger.exception("Hook callable timed out", timeout=self.timeout, callable=fn_path)
            return HookResult(decision="allow")
        except Exception as e:
            fn_path = self._import_path or str(self._callable)
            logger.exception("Hook callable failed", callable=fn_path)
            return HookResult(decision="allow", reason=str(e))


def _normalize_result(result: Any) -> HookResult:
    """Normalize callable result to HookResult.

    Args:
        result: Result from callable.

    Returns:
        Normalized hook result.
    """
    match result:
        case dict():
            normalized: HookResult = {}
            if "decision" in result:
                normalized["decision"] = result["decision"]
            if "reason" in result:
                normalized["reason"] = result["reason"]
            if "modified_input" in result:
                normalized["modified_input"] = result["modified_input"]
            if "additional_context" in result:
                normalized["additional_context"] = result["additional_context"]
            if "continue_" in result:
                normalized["continue_"] = result["continue_"]
            return normalized
        case str():
            return HookResult(decision="allow", additional_context=result)
        case bool():
            return HookResult(decision="allow" if result else "deny")
        case _:
            return HookResult(decision="allow")
