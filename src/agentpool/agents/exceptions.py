"""Shared agent exceptions."""

from __future__ import annotations

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from collections.abc import Sequence


class AgentNotInitializedError(RuntimeError):
    """Raised when an agent is not initialized."""

    def __init__(self) -> None:
        super().__init__("Agent not initialized - use async context manager")


class UnknownCategoryError(ValueError):
    """Raised when an unknown category is encountered."""

    def __init__(self, category_id: str, available: Sequence[str] | None = None) -> None:
        if available:
            msg = f"Unknown category: {category_id}. Available: {', '.join(available)}"
        else:
            msg = f"Unknown category: {category_id}"
        super().__init__(msg)


class UnknownModeError(ValueError):
    """Raised when an unknown mode is encountered."""

    def __init__(self, value: str | bool, available_modes: Sequence[str]) -> None:
        msg = f"Unknown value: {value}. Available: {', '.join(available_modes)}"
        super().__init__(msg)


class UnsupportedOperationError(NotImplementedError):
    """Raised when an agent cannot do an operation on its backend (e.g. compact, fork).

    Not a failure of the call: for some agents the operation has no meaning, such as
    rewriting our history of an agent whose backend keeps the conversation itself.
    """

    def __init__(self, agent: str, operation: str, reason: str) -> None:
        super().__init__(f"{agent} cannot {operation}: {reason}")
        self.agent = agent
        self.operation = operation
        self.reason = reason


class OperationNotAllowedError(RuntimeError):
    """Raised when an operation is blocked by permission or configuration."""

    def __init__(self, operation: str) -> None:
        super().__init__(f"{operation} not allowed")


class ResourceNotFoundError(ValueError):
    """Raised when a requested resource cannot be found."""

    def __init__(self, resource_type: str, resource_id: str) -> None:
        super().__init__(f"{resource_type} {resource_id} not found")


class PromptResolutionError(RuntimeError):
    """Raised when a prompt cannot be resolved."""

    def __init__(self, detail: str) -> None:
        super().__init__(f"Prompt resolution failed: {detail}")
