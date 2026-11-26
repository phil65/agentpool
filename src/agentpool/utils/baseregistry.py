"""Base class for component registries."""

from abc import ABC, abstractmethod
from collections.abc import MutableMapping
from typing import TYPE_CHECKING, Any

from psygnal.containers import EventedDict


if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from psygnal.containers import DictEvents


class AgentPoolError(Exception):
    """Base exception for all AgentPool errors."""


class BaseRegistry[TKey, TItem](MutableMapping[TKey, TItem], ABC):
    """Base class for registries providing item storage and change notifications.

    This registry implements a dictionary-like interface backed by an EventedDict,
    providing automatic event emission for all mutations (additions, removals,
    modifications).

    Features:
    - Dictionary-like access (registry[key] = item)
    - Event emission for all changes
    - Item validation
    - Type safety
    - Customizable error handling

    Available events (accessed via .events):
        - adding(key, value): Before an item is added
        - added(key, value): After an item is added
        - removing(key, value): Before an item is removed
        - removed(key, value): After an item is removed
        - changing(key, value): Before an item is modified
        - changed(key, value): After an item is modified

    To implement, override:
    - _validate_item: Custom validation/transformation of items
    - _error_class: Custom error type for exceptions
    """

    def __init__(self) -> None:
        """Initialize an empty registry."""
        self._items = EventedDict[TKey, TItem]()
        self._initialized = False
        self._configs: dict[TKey, Any] = {}

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self._items})"

    @property
    def is_empty(self) -> bool:
        """Check if registry has any items."""
        return not bool(self._items)

    def has_item(self, key: TKey) -> bool:
        """Check if an item is registered."""
        return key in self._items

    @property
    def events(self) -> DictEvents:
        """Access to all dictionary events."""
        return self._items.events

    def register(self, key: TKey, item: TItem | Any, replace: bool = False) -> None:
        """Register an item."""
        if key in self._items and not replace:
            raise self._error_class(f"Item already registered: {key}")

        self._items[key] = self._validate_item(item)

    def get(self, key: TKey) -> TItem:  # type: ignore
        """Get an item by key."""
        return self[key]

    def list_items(self) -> Sequence[TKey]:
        """List all registered item keys."""
        return list(self._items.keys())

    def reset(self) -> None:
        """Reset registry to initial state."""
        self._items.clear()
        self._configs.clear()
        self._initialized = False

    async def startup(self) -> None:
        """Initialize all registered items."""
        if self._initialized:
            return
        self._initialized = True

    async def shutdown(self) -> None:
        """Cleanup all registered items."""
        if not self._initialized:
            return
        self._initialized = False

    @property
    def _error_class(self) -> type[AgentPoolError]:
        """Error class to use for this registry."""
        return AgentPoolError

    @abstractmethod
    def _validate_item(self, item: Any) -> TItem:
        """Validate and possibly transform item before registration."""

    # Implementing MutableMapping methods
    def __getitem__(self, key: TKey) -> TItem:
        try:
            return self._items[key]
        except KeyError as exc:
            raise self._error_class(f"Item not found: {key}") from exc

    def __setitem__(self, key: TKey, value: Any) -> None:
        """Support dict-style assignment."""
        self.register(key, value)

    def __contains__(self, key: object) -> bool:
        """Support 'in' operator without raising exceptions."""
        return key in self._items

    def __delitem__(self, key: TKey) -> None:
        if key in self._items:
            del self._items[key]
        else:
            raise self._error_class(f"Item not found: {key}")

    def __iter__(self) -> Iterator[TKey]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)
