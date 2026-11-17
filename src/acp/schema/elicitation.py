"""Elicitation schema definitions.

Defines types for agent-initiated elicitation, where the agent requests
structured input from the user via forms or URLs.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated, Any, Literal, Self

from pydantic import Discriminator, Field, Tag

from acp.schema.base import AnnotatedObject, Request, Response


# --- String format ---


class StringFormat:
    """String format types for string properties in elicitation schemas."""

    EMAIL: Literal["email"] = "email"
    URI: Literal["uri"] = "uri"
    DATE: Literal["date"] = "date"
    DATE_TIME: Literal["date-time"] = "date-time"


StringFormatLiteral = Literal["email", "uri", "date", "date-time"]


# --- Enum option ---


class EnumOption(AnnotatedObject):
    """A titled enum option with a const value, human-readable title and optional description."""

    const: str
    """The constant value for this option."""

    title: str
    """Human-readable title for this option."""

    description: str | None = None
    """Human-readable description."""


# --- Property schemas ---


class StringPropertySchema(AnnotatedObject):
    """Schema for string properties in an elicitation form.

    When ``enum_values`` or ``one_of`` is set, this represents a single-select enum.
    """

    type: Literal["string"] = "string"
    """Type discriminator."""

    title: str | None = None
    """Optional title for the property."""

    description: str | None = None
    """Human-readable description."""

    min_length: int | None = Field(default=None, ge=0)
    """Minimum string length."""

    max_length: int | None = Field(default=None, ge=0)
    """Maximum string length."""

    pattern: str | None = None
    """Pattern the string must match."""

    format: StringFormatLiteral | None = None
    """String format."""

    default: str | None = None
    """Default value."""

    enum: Sequence[str] | None = None
    """Enum values for untitled single-select enums."""

    one_of: Sequence[EnumOption] | None = None
    """Titled enum options for titled single-select enums."""


class NumberPropertySchema(AnnotatedObject):
    """Schema for number (floating-point) properties in an elicitation form."""

    type: Literal["number"] = "number"
    """Type discriminator."""

    title: str | None = None
    """Optional title for the property."""

    description: str | None = None
    """Human-readable description."""

    minimum: float | None = None
    """Minimum value (inclusive)."""

    maximum: float | None = None
    """Maximum value (inclusive)."""

    default: float | None = None
    """Default value."""


class IntegerPropertySchema(AnnotatedObject):
    """Schema for integer properties in an elicitation form."""

    type: Literal["integer"] = "integer"
    """Type discriminator."""

    title: str | None = None
    """Optional title for the property."""

    description: str | None = None
    """Human-readable description."""

    minimum: int | None = None
    """Minimum value (inclusive)."""

    maximum: int | None = None
    """Maximum value (inclusive)."""

    default: int | None = None
    """Default value."""


class BooleanPropertySchema(AnnotatedObject):
    """Schema for boolean properties in an elicitation form."""

    type: Literal["boolean"] = "boolean"
    """Type discriminator."""

    title: str | None = None
    """Optional title for the property."""

    description: str | None = None
    """Human-readable description."""

    default: bool | None = None
    """Default value."""


# --- Multi-select items ---


class UntitledMultiSelectItems(AnnotatedObject):
    """Items definition for untitled multi-select enum properties."""

    type: Literal["string"] = "string"
    """Item type discriminator. Must be ``"string"``."""

    enum: Sequence[str]
    """Allowed enum values."""


class TitledMultiSelectItems(AnnotatedObject):
    """Items definition for titled multi-select enum properties."""

    any_of: Sequence[EnumOption]
    """Titled enum options."""


def _multi_select_items_discriminator(v: Any) -> str:
    if isinstance(v, dict):
        if "anyOf" in v or "any_of" in v:
            return "titled"
        return "untitled"
    if isinstance(v, TitledMultiSelectItems):
        return "titled"
    return "untitled"


MultiSelectItems = Annotated[
    Annotated[UntitledMultiSelectItems, Tag("untitled")]
    | Annotated[TitledMultiSelectItems, Tag("titled")],
    Discriminator(_multi_select_items_discriminator),
]
"""Items for a multi-select (array) property schema."""


class MultiSelectPropertySchema(AnnotatedObject):
    """Schema for multi-select (array) properties in an elicitation form."""

    type: Literal["array"] = "array"
    """Type discriminator."""

    title: str | None = None
    """Optional title for the property."""

    description: str | None = None
    """Human-readable description."""

    min_items: int | None = Field(default=None, ge=0)
    """Minimum number of items to select."""

    max_items: int | None = Field(default=None, ge=0)
    """Maximum number of items to select."""

    items: MultiSelectItems
    """The items definition describing allowed values."""

    default: Sequence[str] | None = None
    """Default selected values."""


# --- Property schema union ---


def _property_schema_discriminator(v: Any) -> str:
    if isinstance(v, dict):
        return v.get("type", "string")  # type: ignore[no-any-return]
    return v.type  # type: ignore[no-any-return]


ElicitationPropertySchema = Annotated[
    Annotated[StringPropertySchema, Tag("string")]
    | Annotated[NumberPropertySchema, Tag("number")]
    | Annotated[IntegerPropertySchema, Tag("integer")]
    | Annotated[BooleanPropertySchema, Tag("boolean")]
    | Annotated[MultiSelectPropertySchema, Tag("array")],
    Discriminator(_property_schema_discriminator),
]
"""Property schema for elicitation form fields.

Each variant corresponds to a JSON Schema ``"type"`` value.
"""


# --- Elicitation schema ---


class ElicitationSchema(AnnotatedObject):
    """Type-safe elicitation schema for requesting structured user input.

    This represents a JSON Schema object with primitive-typed properties,
    as required by the elicitation specification.
    """

    type: Literal["object"] = "object"
    """Type discriminator. Always ``"object"``."""

    title: str | None = None
    """Optional title for the schema."""

    description: str | None = None
    """Optional description of what this schema represents."""

    properties: dict[str, ElicitationPropertySchema] = Field(default_factory=dict)
    """Property definitions (must be primitive types)."""

    required: Sequence[str] | None = None
    """List of required property names."""


# --- Elicitation content value ---

ElicitationContentValue = str | int | float | bool | Sequence[str]
"""Possible value types in elicitation content."""


# --- Elicitation capabilities ---


class ElicitationFormCapabilities(AnnotatedObject):
    """Form-based elicitation capabilities."""


class ElicitationUrlCapabilities(AnnotatedObject):
    """URL-based elicitation capabilities."""


class ElicitationCapabilities(AnnotatedObject):
    """Elicitation capabilities supported by the client."""

    form: ElicitationFormCapabilities | None = None
    """Whether the client supports form-based elicitation."""

    url: ElicitationUrlCapabilities | None = None
    """Whether the client supports URL-based elicitation."""


# --- Elicitation request/response ---

ElicitationModeType = Literal["form", "url"] | str
"""Elicitation mode. Values beginning with ``_`` are implementation-specific."""

ElicitationActionType = Literal["accept", "decline", "cancel"] | str
"""User action in response to an elicitation."""


class ElicitationRequest(Request):
    """Request from the agent to elicit structured user input (``elicitation/create``).

    The agent sends this to the client to request information from the user,
    either via a form or by directing them to a URL.

    Mode and scope fields are flattened into the request on the wire:

    - ``mode: "form"`` carries ``requested_schema``.
    - ``mode: "url"`` carries ``elicitation_id`` and ``url``.
    - Session scope sets ``session_id`` (and optionally ``tool_call_id``),
      request scope sets ``request_id`` (e.g. during auth before any session exists).
    """

    message: str
    """A human-readable message describing what input is needed."""

    mode: ElicitationModeType
    """The elicitation mode."""

    requested_schema: ElicitationSchema | None = None
    """Form mode: a JSON Schema describing the form fields to present to the user."""

    elicitation_id: str | None = None
    """URL mode: the unique identifier for this elicitation."""

    url: str | None = None
    """URL mode: the URL to direct the user to."""

    session_id: str | None = None
    """Session scope: the session this elicitation is tied to."""

    tool_call_id: str | None = None
    """Session scope: optional tool call within the session."""

    request_id: int | str | None = None
    """Request scope: the JSON-RPC request this elicitation is tied to."""

    @classmethod
    def form(
        cls,
        message: str,
        schema: ElicitationSchema,
        *,
        session_id: str | None = None,
        tool_call_id: str | None = None,
        request_id: int | str | None = None,
    ) -> Self:
        """Create a form-based elicitation request."""
        return cls(
            message=message,
            mode="form",
            requested_schema=schema,
            session_id=session_id,
            tool_call_id=tool_call_id,
            request_id=request_id,
        )

    @classmethod
    def url_based(
        cls,
        message: str,
        elicitation_id: str,
        url: str,
        *,
        session_id: str | None = None,
        tool_call_id: str | None = None,
        request_id: int | str | None = None,
    ) -> Self:
        """Create a URL-based elicitation request."""
        return cls(
            message=message,
            mode="url",
            elicitation_id=elicitation_id,
            url=url,
            session_id=session_id,
            tool_call_id=tool_call_id,
            request_id=request_id,
        )


class ElicitationResponse(Response):
    """Response from the client to an elicitation request."""

    action: ElicitationActionType
    """The user's action."""

    content: dict[str, ElicitationContentValue] | None = None
    """For ``accept``: the user-provided content, as an object matching the requested schema."""

    @classmethod
    def accept(cls, content: dict[str, ElicitationContentValue] | None = None) -> Self:
        return cls(action="accept", content=content)

    @classmethod
    def decline(cls) -> Self:
        return cls(action="decline")

    @classmethod
    def cancel(cls) -> Self:
        return cls(action="cancel")


# --- Elicitation complete notification ---


class ElicitationCompleteNotification(AnnotatedObject):
    """Notification sent by the agent when a URL-based elicitation is complete."""

    elicitation_id: str
    """The ID of the elicitation that completed."""
