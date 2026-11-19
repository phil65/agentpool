"""v2 elicitation: structured user input requested by the agent (form or URL).

Each request is scoped either to a session (optionally a tool call) or to a pending
client-to-agent request outside of a session, e.g. during authentication.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated, Any, Literal, Self

from pydantic import MISSING, Discriminator, Field, Tag, model_validator

from acp.schema.v2.base import OTHER_TAG, AnnotatedObject, Unknown, open_discriminator
from acp.schema.v2.common import ElicitationId, RequestId, SessionId, ToolCallId  # noqa: TC001


ElicitationSchemaType = Literal["object"]
"""Type of an elicitation schema: always ``object``."""

StringFormat = Literal["email", "uri", "date", "date-time"] | str
"""Format of a string property; unknown formats are kept."""


class EnumOption(AnnotatedObject):
    """A titled enum option with a const value, human-readable title, and optional description."""

    const: str
    """The constant value for this option."""

    title: str
    """Human-readable title for this option."""

    description: str | MISSING | None = MISSING
    """Human-readable description."""


class StringPropertySchema(AnnotatedObject):
    """Schema for string properties in an elicitation form."""

    type: Literal["string"] = Field(default="string", init=False)

    title: str | MISSING | None = MISSING
    """Optional title for the property."""

    description: str | MISSING | None = MISSING
    """Human-readable description."""

    min_length: int | MISSING | None = Field(default=MISSING, ge=0)
    """Minimum string length."""

    max_length: int | MISSING | None = Field(default=MISSING, ge=0)
    """Maximum string length."""

    format: StringFormat | MISSING | None = MISSING
    """String format."""

    default: str | MISSING | None = MISSING
    """Default value."""

    enum: Sequence[str] | MISSING | None = Field(default=MISSING, min_length=1)
    """Enum values for untitled single-select enums. Must contain at least one value when
    present."""

    one_of: Sequence[EnumOption] | MISSING | None = Field(default=MISSING, min_length=1)
    """Titled enum options for titled single-select enums. Must contain at least one option when
    present."""


class NumberPropertySchema(AnnotatedObject):
    """Schema for number (floating-point) properties in an elicitation form."""

    type: Literal["number"] = Field(default="number", init=False)

    title: str | MISSING | None = MISSING
    """Optional title for the property."""

    description: str | MISSING | None = MISSING
    """Human-readable description."""

    minimum: float | MISSING | None = MISSING
    """Minimum value (inclusive)."""

    maximum: float | MISSING | None = MISSING
    """Maximum value (inclusive)."""

    default: float | MISSING | None = MISSING
    """Default value."""


class IntegerPropertySchema(AnnotatedObject):
    """Schema for integer properties in an elicitation form."""

    type: Literal["integer"] = Field(default="integer", init=False)

    title: str | MISSING | None = MISSING
    """Optional title for the property."""

    description: str | MISSING | None = MISSING
    """Human-readable description."""

    minimum: int | MISSING | None = MISSING
    """Minimum value (inclusive)."""

    maximum: int | MISSING | None = MISSING
    """Maximum value (inclusive)."""

    default: int | MISSING | None = MISSING
    """Default value."""


class BooleanPropertySchema(AnnotatedObject):
    """Schema for boolean properties in an elicitation form."""

    type: Literal["boolean"] = Field(default="boolean", init=False)

    title: str | MISSING | None = MISSING
    """Optional title for the property."""

    description: str | MISSING | None = MISSING
    """Human-readable description."""

    default: bool | MISSING | None = MISSING
    """Default value."""


class StringMultiSelectItems(AnnotatedObject):
    """String item schema for multi-select enum properties."""

    type: Literal["string"] = Field(default="string", init=False)

    enum: Sequence[str] = Field(min_length=1)
    """Allowed enum values. Must contain at least one value."""


class TitledMultiSelectItems(AnnotatedObject):
    """Items definition for titled multi-select enum properties."""

    any_of: Sequence[EnumOption] = Field(min_length=1)
    """Titled enum options. Must contain at least one option."""


class UnknownMultiSelectItems(Unknown):
    """Custom or future typed multi-select items."""

    type: str
    """The item type."""


def _multi_select_items_tag(value: Any) -> str:
    if isinstance(value, dict):
        kind, titled = value.get("type"), "anyOf" in value
    else:
        kind, titled = getattr(value, "type", None), isinstance(value, TitledMultiSelectItems)
    if titled:
        return "titled"
    return "string" if kind == "string" else OTHER_TAG


MultiSelectItems = Annotated[
    Annotated[StringMultiSelectItems, Tag("string")]
    | Annotated[TitledMultiSelectItems, Tag("titled")]
    | Annotated[UnknownMultiSelectItems, Tag(OTHER_TAG)],
    Discriminator(_multi_select_items_tag),
]
"""Allowed values of a multi-select property: plain strings, or titled options (no ``type``)."""


class MultiSelectPropertySchema(AnnotatedObject):
    """Schema for multi-select (array) properties in an elicitation form."""

    type: Literal["array"] = Field(default="array", init=False)

    title: str | MISSING | None = MISSING
    """Optional title for the property."""

    description: str | MISSING | None = MISSING
    """Human-readable description."""

    min_items: int | MISSING | None = Field(default=MISSING, ge=0)
    """Minimum number of items to select."""

    max_items: int | MISSING | None = Field(default=MISSING, ge=0)
    """Maximum number of items to select."""

    items: MultiSelectItems
    """The items definition describing allowed values."""

    default: Sequence[str] | MISSING | None = MISSING
    """Default selected values."""


class UnknownPropertySchema(Unknown):
    """A custom or future property type; values starting with ``_`` are extensions."""

    type: str
    """The property type."""


ElicitationPropertySchema = Annotated[
    Annotated[StringPropertySchema, Tag("string")]
    | Annotated[NumberPropertySchema, Tag("number")]
    | Annotated[IntegerPropertySchema, Tag("integer")]
    | Annotated[BooleanPropertySchema, Tag("boolean")]
    | Annotated[MultiSelectPropertySchema, Tag("array")]
    | Annotated[UnknownPropertySchema, Tag(OTHER_TAG)],
    Discriminator(
        open_discriminator("type", frozenset({"string", "number", "integer", "boolean", "array"}))
    ),
]
"""A form field: string (or single-select enum), number, integer, boolean or multi-select."""


class ElicitationSchema(AnnotatedObject):
    """Type-safe elicitation schema for requesting structured user input."""

    type: ElicitationSchemaType = Field(default="object", init=False)
    """Type discriminator. Always ``"object"``. Any other value is rejected."""

    title: str | MISSING | None = MISSING
    """Optional title for the schema."""

    properties: dict[str, ElicitationPropertySchema]
    """Property definitions (must be primitive types)."""

    required: Sequence[str] | MISSING | None = MISSING
    """List of required property names."""

    description: str | MISSING | None = MISSING
    """Optional description of what this schema represents."""


class CompleteElicitationNotification(AnnotatedObject):
    """Notification sent by the agent when a URL-based elicitation is complete."""

    elicitation_id: ElicitationId
    """The ID of the elicitation that completed."""


class _ScopedRequest(AnnotatedObject):
    """An elicitation request, tied to a session or to a pending client request."""

    message: str
    """What input is needed."""

    session_id: SessionId | MISSING = MISSING
    """Session scope: the session the elicitation belongs to."""

    tool_call_id: ToolCallId | MISSING | None = MISSING
    """Session scope: the tool call within the session, if any."""

    request_id: RequestId | MISSING = MISSING
    """Request scope: the pending client-to-agent request, outside of a session."""

    @model_validator(mode="after")
    def _one_scope(self) -> Self:
        if (self.session_id is MISSING) == (self.request_id is MISSING):
            msg = "an elicitation needs exactly one of sessionId and requestId"
            raise ValueError(msg)
        if self.request_id is not MISSING and self.tool_call_id is not MISSING:
            msg = "toolCallId only applies to session-scoped elicitations"
            raise ValueError(msg)
        return self


class FormElicitationRequest(_ScopedRequest):
    """The client renders a form from ``requested_schema``."""

    mode: Literal["form"] = Field(default="form", init=False)

    requested_schema: ElicitationSchema
    """The form fields."""


class UrlElicitationRequest(_ScopedRequest):
    """The client directs the user to ``url``."""

    mode: Literal["url"] = Field(default="url", init=False)

    elicitation_id: ElicitationId
    """Unique identifier of the elicitation; completed with ``elicitation/complete``."""

    url: str
    """The URL to direct the user to."""


class UnknownElicitationRequest(Unknown):
    """A custom or future mode; values starting with ``_`` are extensions."""

    mode: str
    """The mode."""

    message: str
    """What input is needed."""


CreateElicitationRequest = Annotated[
    Annotated[FormElicitationRequest, Tag("form")]
    | Annotated[UrlElicitationRequest, Tag("url")]
    | Annotated[UnknownElicitationRequest, Tag(OTHER_TAG)],
    Discriminator(open_discriminator("mode", frozenset({"form", "url"}))),
]
"""Parameters of ``elicitation/create``: structured user input the agent asks for."""


ElicitationContentValue = str | int | float | bool | Sequence[str] | Any
"""A value in an accepted elicitation's content; other JSON values are kept."""


class AcceptElicitationResponse(AnnotatedObject):
    """The user accepted and provided content."""

    action: Literal["accept"] = Field(default="accept", init=False)

    content: dict[str, ElicitationContentValue] | MISSING | None = MISSING
    """The content, matching the requested schema."""


class DeclineElicitationResponse(AnnotatedObject):
    """The user declined."""

    action: Literal["decline"] = Field(default="decline", init=False)


class CancelElicitationResponse(AnnotatedObject):
    """The elicitation was cancelled."""

    action: Literal["cancel"] = Field(default="cancel", init=False)


class UnknownElicitationResponse(Unknown):
    """A custom or future action; values starting with ``_`` are extensions."""

    action: str
    """The action."""


CreateElicitationResponse = Annotated[
    Annotated[AcceptElicitationResponse, Tag("accept")]
    | Annotated[DeclineElicitationResponse, Tag("decline")]
    | Annotated[CancelElicitationResponse, Tag("cancel")]
    | Annotated[UnknownElicitationResponse, Tag(OTHER_TAG)],
    Discriminator(open_discriminator("action", frozenset({"accept", "decline", "cancel"}))),
]
"""Response to ``elicitation/create``."""
