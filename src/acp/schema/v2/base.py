"""Base classes of the ACP v2 models.

Wire rules, matching the v2 schema's patch semantics:

- Optional fields default to :data:`~pydantic.MISSING` and are left out when
  serializing. ``None`` is only accepted where the schema allows ``null``, and is
  sent as ``null``. On upserts, a left out field is unchanged and ``null`` clears it.
- Serialize with :func:`to_wire` (``by_alias``, no ``exclude_none``), so explicit
  ``null`` values reach the peer.
- ``MISSING`` is truthy. Test fields with :func:`is_set` (or ``is MISSING``), never
  with ``if field:``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypeIs

from pydantic import MISSING, BaseModel, ConfigDict
from pydantic.alias_generators import to_camel, to_snake


if TYPE_CHECKING:
    from collections.abc import Callable


def _alias(name: str) -> str:
    return "_meta" if name == "field_meta" else to_camel(name)


class Schema(BaseModel):
    """Base class of all v2 models."""

    model_config = ConfigDict(
        populate_by_name=True,
        alias_generator=_alias,
        use_attribute_docstrings=True,
    )


class AnnotatedObject(Schema):
    """A v2 object with the ``_meta`` extension point."""

    field_meta: dict[str, Any] | MISSING | None = MISSING
    """Extension point for implementations. Omitted and ``null`` mean no metadata.

    On upserts, ``_meta`` is a patch: omitted leaves it unchanged, ``null`` clears it,
    and an object patches the stored metadata by top-level key (a key set to ``null``
    is removed, any other value replaces that key).
    """


def is_set[T](value: T | MISSING) -> TypeIs[T]:
    """Whether a field was given (``None`` counts as given): narrows ``T | MISSING`` to ``T``.

    ``MISSING`` is truthy, so ``if field:`` does not work for this.
    """
    return value is not MISSING


def to_wire(model: BaseModel) -> dict[str, Any]:
    """Serialize a v2 model: wire names, ``MISSING`` left out, ``None`` sent as ``null``."""
    return model.model_dump(mode="json", by_alias=True)


class Unknown(AnnotatedObject):
    """Base of fallback variants of open unions: a custom or future variant.

    Keeps all fields of the payload, so messages can be passed on unchanged.
    """

    model_config = ConfigDict(extra="allow")


OTHER_TAG = "__other__"
"""``Tag`` of the fallback variant of an open union.

Not a possible protocol value, so it cannot clash with real tags such as the
``unknown`` session state.
"""


def open_discriminator(prop: str, known: frozenset[str]) -> Callable[[Any], str]:
    """Discriminator of an open union tagged by ``prop``.

    ``prop`` is the wire name; model instances are read through the matching Python
    attribute (``sessionUpdate`` -> ``session_update``). Known values select their variant
    (a malformed payload is an error, it does not fall back); any other value selects
    the variant tagged :data:`OTHER_TAG`.
    """
    attr = to_snake(prop)

    def discriminate(value: Any) -> str:
        if isinstance(value, dict):
            tag = value.get(prop, value.get(attr))
        else:
            tag = getattr(value, attr, None)
        return tag if isinstance(tag, str) and tag in known else OTHER_TAG

    return discriminate
