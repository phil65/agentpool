"""Cursor pagination as used by OpenCode's list endpoints.

Cursors are opaque base64url JSON objects ``{id, order, direction}``. ``next`` continues
after the last item of a page, ``previous`` goes back before its first item, both in the
order of the first page.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
import json
from typing import TYPE_CHECKING, Literal

from agentpool_server.opencode_v2_server.errors import invalid_cursor


if TYPE_CHECKING:
    from collections.abc import Callable, Sequence


type Order = Literal["asc", "desc"]
type Direction = Literal["previous", "next"]


@dataclass(frozen=True)
class Cursor:
    """Decoded pagination cursor."""

    id: str
    order: Order
    direction: Direction

    def encode(self) -> str:
        """Opaque string form."""
        raw = json.dumps({"id": self.id, "order": self.order, "direction": self.direction})
        return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")

    @classmethod
    def decode(cls, value: str) -> Cursor:
        """Parse a cursor.

        Raises:
            ApiError: InvalidCursorError for malformed cursors
        """
        try:
            padded = value + "=" * (-len(value) % 4)
            data = json.loads(base64.urlsafe_b64decode(padded))
        except (ValueError, json.JSONDecodeError):
            raise invalid_cursor() from None
        match data:
            case {
                "id": str() as item_id,
                "order": "asc" | "desc" as order,
                "direction": "previous" | "next" as direction,
            }:
                return cls(id=item_id, order=order, direction=direction)
            case _:
                raise invalid_cursor()


@dataclass(frozen=True)
class Page[T]:
    """One page of results with the cursors to its neighbours."""

    items: list[T]
    previous: str | None
    next: str | None


def paginate[T](
    items: Sequence[T],
    *,
    key: Callable[[T], str],
    limit: int,
    order: Order | None,
    cursor: str | None,
    default_order: Order = "desc",
) -> Page[T]:
    """Paginate items that are given in ascending (chronological) order.

    Raises:
        ApiError: InvalidCursorError if cursor and order are combined or the cursor is bad
    """
    if cursor is not None and order is not None:
        raise invalid_cursor("Cursor cannot be combined with order")
    decoded = Cursor.decode(cursor) if cursor is not None else None
    effective: Order = decoded.order if decoded else (order or default_order)
    ordered = list(items) if effective == "asc" else list(reversed(items))
    if decoded is None:
        window = ordered[:limit]
    else:
        index = next((i for i, item in enumerate(ordered) if key(item) == decoded.id), None)
        if index is None:
            raise invalid_cursor()
        if decoded.direction == "next":
            window = ordered[index + 1 : index + 1 + limit]
        else:
            window = ordered[max(0, index - limit) : index]
    previous = Cursor(key(window[0]), effective, "previous").encode() if window else None
    following = Cursor(key(window[-1]), effective, "next").encode() if window else None
    return Page(items=window, previous=previous, next=following)
