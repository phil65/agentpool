"""Elicitation types for user interaction requests.

These are agentpool's internal representation of elicitation requests,
independent of MCP or any other protocol. Adapters convert to/from
protocol-specific formats.

Based on MCP's elicitation primitives:
https://modelcontextprotocol.io/specification/draft/client/elicitation
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol


if TYPE_CHECKING:
    from agentpool.agents.context import AgentContext


# =============================================================================
# Choice options
# =============================================================================


@dataclass
class ChoiceOption:
    """A choice option with value and display title."""

    value: str
    """The value returned when this option is selected."""

    title: str
    """Human-readable display title."""

    description: str = ""
    """Optional description of what this option means."""


# =============================================================================
# Elicitation primitives
# =============================================================================


@dataclass
class ElicitString:
    """String elicitation request."""

    title: str
    """Human-readable field title."""

    description: str = ""
    """Optional description explaining what's needed."""

    format: Literal["email", "uri", "date", "date-time"] | None = None
    """Optional format constraint."""

    min_length: int | None = None
    """Minimum string length."""

    max_length: int | None = None
    """Maximum string length."""

    pattern: str | None = None
    """Optional regex pattern to validate against."""

    default: str | None = None
    """Default value to pre-populate."""

    required: bool = True
    """Whether this field is required."""


@dataclass
class ElicitNumber:
    """Number elicitation request."""

    title: str
    """Human-readable field title."""

    description: str = ""
    """Optional description explaining what's needed."""

    integer: bool = False
    """If True, only accept integers."""

    minimum: float | None = None
    """Minimum value."""

    maximum: float | None = None
    """Maximum value."""

    default: float | None = None
    """Default value to pre-populate."""

    required: bool = True
    """Whether this field is required."""


@dataclass
class ElicitBoolean:
    """Boolean elicitation request."""

    title: str
    """Human-readable field title."""

    description: str = ""
    """Optional description explaining what's needed."""

    default: bool | None = None
    """Default value to pre-populate."""

    required: bool = True
    """Whether this field is required."""


@dataclass
class ElicitChoice:
    """Single or multi-select elicitation request."""

    title: str
    """Human-readable field title."""

    options: list[ChoiceOption]
    """Available options to choose from."""

    description: str = ""
    """Optional description explaining what's needed."""

    multi: bool = False
    """If True, allow multiple selections."""

    min_items: int | None = None
    """Minimum number of selections (for multi-select)."""

    max_items: int | None = None
    """Maximum number of selections (for multi-select)."""

    default: str | list[str] | None = None
    """Default value(s) to pre-select."""

    required: bool = True
    """Whether this field is required."""

    allow_custom: bool = False
    """If True, allow free text input in addition to predefined options.

    This enables "Other: ___" style inputs where users can either pick
    from options or type their own answer. OpenCode's question tool
    supports this pattern.
    """


@dataclass
class ElicitUrl:
    """URL mode elicitation for out-of-band interactions.

    Used for OAuth flows, credential entry, payments, etc.
    The actual interaction happens outside the MCP client.
    """

    message: str
    """Human-readable message explaining why the URL interaction is needed."""

    url: str
    """The URL the user should navigate to."""

    elicitation_id: str
    """Unique identifier for this elicitation (for completion notifications)."""


@dataclass
class ElicitForm:
    """A form with multiple fields.

    Represents a batch of elicitation requests that can be
    presented together as a form.
    """

    message: str
    """Human-readable message explaining why this information is needed."""

    fields: dict[str, ElicitString | ElicitNumber | ElicitBoolean | ElicitChoice] = field(
        default_factory=dict
    )
    """Map of field name to elicitation request."""

    required_fields: list[str] = field(default_factory=list)
    """List of field names that are required."""


# Union of all elicitation types
ElicitRequest = ElicitString | ElicitNumber | ElicitBoolean | ElicitChoice | ElicitUrl | ElicitForm


# =============================================================================
# Response types
# =============================================================================


@dataclass
class ChoiceValue:
    """Value from a choice elicitation."""

    selected: list[str]
    """Selected option value(s)."""

    custom_input: str | None = None
    """Free text input if user chose custom/other option."""

    @property
    def value(self) -> str:
        """Convenience for single-select: first selected or custom input."""
        if self.custom_input:
            return self.custom_input
        return self.selected[0] if self.selected else ""


@dataclass
class ElicitResult[T]:
    """Result of an elicitation request.

    Generic over the value type:
    - ElicitResult[str] for string elicitation
    - ElicitResult[float] for number elicitation
    - ElicitResult[bool] for boolean elicitation
    - ElicitResult[ChoiceValue] for choice elicitation
    - ElicitResult[None] for URL elicitation (no value, just action)
    - ElicitResult[dict[str, Any]] for form elicitation
    """

    action: Literal["accept", "decline", "cancel"]
    """User's action: accept (with data), decline (explicit no), cancel (dismissed)."""

    value: T | None = None
    """The value (only meaningful when action is 'accept')."""


# =============================================================================
# Elicitation Handler Protocol
# =============================================================================


class ElicitationHandler(Protocol):
    """Protocol for handling elicitation requests.

    Consumers (servers, UIs) implement this to handle user interaction.
    Each method corresponds to an elicitation primitive type.

    All methods return ElicitResult[T] with:
    - action: 'accept' (user provided value), 'decline' (user refused), 'cancel' (dismissed)
    - value: The typed value (only meaningful when action is 'accept')

    Implementations can choose how to present these to users:
    - Terminal: sequential prompts with input()
    - GUI: form dialogs
    - ACP: permission requests (with limitations)
    - OpenCode: batched question tool
    """

    async def elicit_string(
        self,
        context: AgentContext,
        request: ElicitString,
    ) -> ElicitResult[str]:
        """Get a string value from the user.

        Args:
            context: Current agent context
            request: String elicitation parameters

        Returns:
            ElicitResult with string value
        """
        raise NotImplementedError

    async def elicit_number(
        self,
        context: AgentContext,
        request: ElicitNumber,
    ) -> ElicitResult[float]:
        """Get a numeric value from the user.

        Args:
            context: Current agent context
            request: Number elicitation parameters

        Returns:
            ElicitResult with numeric value
        """
        raise NotImplementedError

    async def elicit_boolean(
        self,
        context: AgentContext,
        request: ElicitBoolean,
    ) -> ElicitResult[bool]:
        """Get a boolean value from the user.

        Args:
            context: Current agent context
            request: Boolean elicitation parameters

        Returns:
            ElicitResult with boolean value
        """
        raise NotImplementedError

    async def elicit_choice(
        self,
        context: AgentContext,
        request: ElicitChoice,
    ) -> ElicitResult[ChoiceValue]:
        """Get a single or multiple choice selection from the user.

        Args:
            context: Current agent context
            request: Choice elicitation parameters

        Returns:
            ElicitResult with ChoiceValue (selected options + optional custom input)
        """
        raise NotImplementedError

    async def elicit_url(
        self,
        context: AgentContext,
        request: ElicitUrl,
    ) -> ElicitResult[None]:
        """Handle URL mode elicitation for out-of-band interactions.

        The user should be shown the URL and asked for consent to open it.
        The actual interaction happens outside the client.

        Args:
            context: Current agent context
            request: URL elicitation parameters

        Returns:
            User's action: accept (consented), decline (refused), cancel (dismissed)
        """
        raise NotImplementedError

    async def elicit_form(
        self,
        context: AgentContext,
        request: ElicitForm,
    ) -> ElicitResult[dict[str, Any]]:
        """Get multiple values from a form.

        Implementations may present this as:
        - A single form dialog with all fields
        - Sequential prompts for each field
        - A batched question interface

        Args:
            context: Current agent context
            request: Form elicitation parameters

        Returns:
            ElicitResult with dict mapping field names to values
        """
        raise NotImplementedError


async def dispatch_elicitation[T](
    handler: ElicitationHandler,
    context: AgentContext,
    request: ElicitRequest,
) -> ElicitResult[Any]:
    """Dispatch an elicitation request to the appropriate handler method.

    Routes requests to the correct handler method based on type.

    Args:
        handler: The elicitation handler implementation
        context: Current agent context
        request: The elicitation request

    Returns:
        ElicitResult with the user's response
    """
    match request:
        case ElicitString():
            return await handler.elicit_string(context, request)
        case ElicitNumber():
            return await handler.elicit_number(context, request)
        case ElicitBoolean():
            return await handler.elicit_boolean(context, request)
        case ElicitChoice():
            return await handler.elicit_choice(context, request)
        case ElicitUrl():
            return await handler.elicit_url(context, request)
        case ElicitForm():
            return await handler.elicit_form(context, request)
        case _:
            raise ValueError(f"Unknown elicitation type: {type(request)}")


# =============================================================================
# Conversion from MCP JSON Schema
# =============================================================================


def _parse_enum_options(schema: dict[str, Any]) -> list[ChoiceOption]:
    """Parse enum options from JSON schema."""
    # Simple enum: {"enum": ["a", "b", "c"]}
    match schema:
        case {"enum": enum}:
            return [ChoiceOption(value=str(val), title=str(val)) for val in enum]
        case {"oneOf": one_of}:  #  {"oneOf": [{"const": "a", "title": "Option A"}, ...]}
            return [
                ChoiceOption(
                    value=str(item["const"]),
                    title=item.get("title", str(item["const"])),
                    description=item.get("description", ""),
                )
                for item in one_of
                if "const" in item
            ]
        case _:
            return []


def _parse_array_enum_options(items_schema: dict[str, Any]) -> list[ChoiceOption]:
    """Parse multi-select enum options from array items schema."""
    match items_schema:
        case {"enum": enum_list}:
            # items with enum: {"items": {"type": "string", "enum": ["a", "b"]}}
            return [ChoiceOption(value=str(val), title=str(val)) for val in enum_list]
        case {"anyOf": any_of}:
            # items with anyOf: {"items": {"anyOf": [{"const": "a", "title": "A"}, ...]}}
            options: list[ChoiceOption] = []
            for item in any_of:
                if "const" in item:
                    val = str(item["const"])
                    title = item.get("title", val)
                    desc = item.get("description", "")
                    options.append(ChoiceOption(value=val, title=title, description=desc))
            return options

    return []


def from_mcp_schema(schema: dict[str, Any]) -> ElicitRequest:
    """Convert a single MCP JSON schema property to an ElicitRequest.

    Args:
        schema: JSON schema for a single property

    Returns:
        Appropriate ElicitRequest subtype
    """
    schema_type = schema.get("type", "string")
    title = schema.get("title", "")
    description = schema.get("description", "")
    default = schema.get("default")

    match schema_type:
        case "array":
            items = schema.get("items", {})
            options = _parse_array_enum_options(items)
            return ElicitChoice(
                title=title,
                description=description,
                options=options,
                multi=True,
                min_items=schema.get("minItems"),
                max_items=schema.get("maxItems"),
                default=default,
            )
        case "string" if "enum" in schema or "oneOf" in schema:
            options = _parse_enum_options(schema)
            return ElicitChoice(
                title=title,
                description=description,
                options=options,
                multi=False,
                default=default,
            )
        case "string":
            return ElicitString(
                title=title,
                description=description,
                format=schema.get("format"),
                min_length=schema.get("minLength"),
                max_length=schema.get("maxLength"),
                pattern=schema.get("pattern"),
                default=default,
            )
        case "number" | "integer":
            return ElicitNumber(
                title=title,
                description=description,
                integer=schema_type == "integer",
                minimum=schema.get("minimum"),
                maximum=schema.get("maximum"),
                default=default,
            )
        case "boolean":
            return ElicitBoolean(title=title, description=description, default=default)
        case _:
            return ElicitString(title=title, description=description)


def from_mcp_form_schema(message: str, schema: dict[str, Any]) -> ElicitForm:
    """Convert an MCP form schema (object with properties) to ElicitForm.

    Args:
        message: The elicitation message
        schema: JSON schema with type=object and properties

    Returns:
        ElicitForm with parsed fields
    """
    properties = schema.get("properties", {})
    required = schema.get("required", [])

    fields: dict[str, ElicitString | ElicitNumber | ElicitBoolean | ElicitChoice] = {}

    for name, prop_schema in properties.items():
        request = from_mcp_schema(prop_schema)
        # Only primitive types go in form fields (not URL or nested Form)
        if isinstance(request, ElicitString | ElicitNumber | ElicitBoolean | ElicitChoice):
            request.required = name in required
            fields[name] = request

    return ElicitForm(message=message, fields=fields, required_fields=required)


# =============================================================================
# Conversion to MCP JSON Schema
# =============================================================================


def _string_to_schema(request: ElicitString) -> dict[str, Any]:
    """Convert ElicitString to JSON schema."""
    schema: dict[str, Any] = {"type": "string"}
    if request.title:
        schema["title"] = request.title
    if request.description:
        schema["description"] = request.description
    if request.format:
        schema["format"] = request.format
    if request.min_length is not None:
        schema["minLength"] = request.min_length
    if request.max_length is not None:
        schema["maxLength"] = request.max_length
    if request.pattern:
        schema["pattern"] = request.pattern
    if request.default is not None:
        schema["default"] = request.default
    return schema


def _number_to_schema(request: ElicitNumber) -> dict[str, Any]:
    """Convert ElicitNumber to JSON schema."""
    schema: dict[str, Any] = {"type": "integer" if request.integer else "number"}
    if request.title:
        schema["title"] = request.title
    if request.description:
        schema["description"] = request.description
    if request.minimum is not None:
        schema["minimum"] = request.minimum
    if request.maximum is not None:
        schema["maximum"] = request.maximum
    if request.default is not None:
        schema["default"] = request.default
    return schema


def _boolean_to_schema(request: ElicitBoolean) -> dict[str, Any]:
    """Convert ElicitBoolean to JSON schema."""
    schema: dict[str, Any] = {"type": "boolean"}
    if request.title:
        schema["title"] = request.title
    if request.description:
        schema["description"] = request.description
    if request.default is not None:
        schema["default"] = request.default
    return schema


def _choice_to_schema(request: ElicitChoice) -> dict[str, Any]:
    """Convert ElicitChoice to JSON schema."""
    schema: dict[str, Any]

    if request.multi:
        # Multi-select -> array with items
        if all(o.title == o.value for o in request.options):
            items: dict[str, Any] = {
                "type": "string",
                "enum": [o.value for o in request.options],
            }
        else:
            items = {
                "anyOf": [
                    {"const": o.value, "title": o.title}
                    | ({"description": o.description} if o.description else {})
                    for o in request.options
                ]
            }
        schema = {"type": "array", "items": items}
        if request.min_items is not None:
            schema["minItems"] = request.min_items
        if request.max_items is not None:
            schema["maxItems"] = request.max_items
    elif all(o.title == o.value for o in request.options):
        # Single-select with simple enum
        schema = {"type": "string", "enum": [o.value for o in request.options]}
    else:
        # Single-select with titled options
        schema = {
            "type": "string",
            "oneOf": [
                {"const": o.value, "title": o.title}
                | ({"description": o.description} if o.description else {})
                for o in request.options
            ],
        }

    if request.title:
        schema["title"] = request.title
    if request.description:
        schema["description"] = request.description
    if request.default is not None:
        schema["default"] = request.default
    return schema


def to_mcp_schema(request: ElicitRequest) -> dict[str, Any]:
    """Convert an ElicitRequest to MCP JSON schema.

    Args:
        request: The elicitation request

    Returns:
        JSON schema dict
    """
    match request:
        case ElicitString():
            return _string_to_schema(request)
        case ElicitNumber():
            return _number_to_schema(request)
        case ElicitBoolean():
            return _boolean_to_schema(request)
        case ElicitChoice():
            return _choice_to_schema(request)
        case ElicitUrl():
            raise ValueError("ElicitUrl doesn't have a JSON schema representation")
        case ElicitForm(fields=fields, required_fields=required_fields):
            properties = {name: to_mcp_schema(f) for name, f in fields.items()}
            return {"type": "object", "properties": properties, "required": required_fields}

    raise ValueError(f"Unknown elicitation type: {type(request)}")


# =============================================================================
# Conversion from Claude SDK AskUserQuestion format
# =============================================================================


def from_claude_question(question: dict[str, Any]) -> ElicitChoice:
    """Convert a Claude SDK question to ElicitChoice.

    Claude format:
    {
        "question": "How should I format?",
        "header": "Format",
        "options": [{"label": "Summary", "description": "Brief"}],
        "multiSelect": false
    }

    Args:
        question: Claude SDK question dict

    Returns:
        ElicitChoice request
    """
    options = [
        ChoiceOption(
            value=opt.get("label", ""),
            title=opt.get("label", ""),
            description=opt.get("description", ""),
        )
        for opt in question.get("options", [])
    ]

    header = question.get("header", "")
    question_text = question.get("question", "")
    title = f"{header}: {question_text}" if header else question_text
    multi = question.get("multiSelect", False)
    return ElicitChoice(title=title, options=options, multi=multi)


def from_claude_questions(questions: list[dict[str, Any]]) -> ElicitForm:
    """Convert Claude SDK questions array to ElicitForm.

    Args:
        questions: List of Claude SDK question dicts

    Returns:
        ElicitForm with each question as a field
    """
    fields: dict[str, ElicitString | ElicitNumber | ElicitBoolean | ElicitChoice] = {}

    for q in questions:
        question_text = q.get("question", "")
        fields[question_text] = from_claude_question(q)

    return ElicitForm(
        message="Please answer the following questions",
        fields=fields,
        required_fields=list(fields.keys()),
    )


def to_claude_answers(
    questions: list[dict[str, Any]],
    result: ElicitResult[dict[str, Any]],
) -> dict[str, Any]:
    """Convert ElicitResult back to Claude SDK answers format.

    Args:
        questions: Original Claude SDK questions
        result: Elicitation result from elicit_form (dict mapping question to answer)

    Returns:
        Dict with "questions" and "answers" for Claude SDK
    """
    answers: dict[str, str] = {}

    if result.action == "accept" and result.value:
        for q in questions:
            question_text = q.get("question", "")
            if question_text in result.value:
                value = result.value[question_text]
                # Handle ChoiceValue from choice fields
                if isinstance(value, ChoiceValue):
                    if value.custom_input:
                        answers[question_text] = value.custom_input
                    else:
                        answers[question_text] = ", ".join(value.selected)
                # Join list values with ", " for multi-select
                elif isinstance(value, list):
                    answers[question_text] = ", ".join(str(v) for v in value)
                else:
                    answers[question_text] = str(value)

    return {"questions": questions, "answers": answers}
