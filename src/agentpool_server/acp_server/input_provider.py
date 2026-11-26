"""ACP-based input provider for agent interactions."""

from typing import TYPE_CHECKING, Any, assert_never
import uuid

from aclientp import AllowedOutcome, DeniedOutcome, PermissionOption
from aclientp.schema import ElicitationCompleteNotification, ElicitationRequest, ElicitationSchema
from mcp import types
from pydantic import ValidationError

from agentpool.acp_utils import DEFAULT_PERMISSION_OPTIONS
from agentpool.log import get_logger
from agentpool.tools.decisions import Approve, Deny
from agentpool.ui.base import InputProvider


if TYPE_CHECKING:
    from aclientp import RequestPermissionResponse

    from agentpool import AgentContext
    from agentpool.tools.decisions import ToolDecision
    from agentpool_server.acp_server.session import ACPSession

logger = get_logger(__name__)


def _create_enum_elicitation_options(schema: dict[str, Any]) -> list[PermissionOption] | None:
    """Create permission options for enum elicitation.

    max 4 options (not more ids available)
    """
    enum_values = schema.get("enum", [])
    enum_names = schema.get("enumNames", enum_values)  # Use enumNames if available
    # Limit to 3 choices + cancel to keep ACP UI reasonable
    if len(enum_values) > 3:  # noqa: PLR2004
        logger.warning("Enum truncated for UI", enum_count=len(enum_values), showing_count=3)
        enum_values = enum_values[:3]
        enum_names = enum_names[:3]

    if not enum_values:
        return None

    options = []
    for i, (value, name) in enumerate(zip(enum_values, enum_names, strict=False)):
        opt_id = f"enum_{i}_{value}"
        option = PermissionOption(option_id=opt_id, name=str(name), kind="allow_once")
        options.append(option)
    # Add cancel option
    options.append(PermissionOption(option_id="cancel", name="Cancel", kind="reject_always"))

    return options


def _is_boolean_schema(schema: dict[str, Any]) -> bool:
    """Check if the elicitation schema is requesting a boolean value."""
    # Direct boolean type
    match schema:
        case {"type": "boolean"}:
            return True
        case {"type": "object", "properties": dict() as props} if len(props) == 1:
            prop_schema = next(iter(props.values()))
            return bool(prop_schema.get("type") == "boolean")
        case _:
            return False


def _enum_field(schema: dict[str, Any]) -> tuple[str | None, dict[str, Any]] | None:
    """The string enum an elicitation asks for: (property name, its schema), or None.

    MCP forms are objects; one with a single string enum property asks for that
    property (name returned). A bare string enum schema is accepted too (name None).
    """
    match schema:
        case {"type": "string", "enum": list()}:
            return None, schema
        case {"type": "object", "properties": dict() as props} if len(props) == 1:
            name, prop = next(iter(props.items()))
            if isinstance(prop, dict) and prop.get("type") == "string" and "enum" in prop:
                return name, prop
            return None
        case _:
            return None


def _create_boolean_elicitation_options() -> list[PermissionOption]:
    """Create permission options for boolean elicitation (Yes/No)."""
    return [
        PermissionOption(option_id="true", name="Yes", kind="allow_once"),
        PermissionOption(option_id="false", name="No", kind="reject_once"),
        PermissionOption(option_id="cancel", name="Cancel", kind="reject_always"),
    ]


_KIND_DECISIONS: dict[str, ToolDecision] = {
    "allow_once": Approve(),
    "allow_always": Approve(scope="session"),
    "reject_once": Deny(),
    "reject_always": Deny(scope="session"),
}
_OPTION_DECISIONS = {o.option_id: _KIND_DECISIONS[o.kind] for o in DEFAULT_PERMISSION_OPTIONS}
"""The decision each of the offered permission options stands for."""


def _decision_of(option_id: str) -> ToolDecision:
    """The decision of the option the client selected (an unknown one counts as a denial)."""
    if (decision := _OPTION_DECISIONS.get(option_id)) is None:
        logger.warning("Unknown permission option", option_id=option_id)
        return Deny()
    return decision


class ACPInputProvider(InputProvider):
    """Input provider that uses ACP session for user interactions.

    This provider enables tool confirmation and elicitation requests
    through the ACP protocol, allowing clients to interact with agents
    for permission requests and additional input.
    """

    def __init__(self, session: ACPSession) -> None:
        """Initialize ACP input provider.

        Args:
            session: Active ACP session for handling requests
        """
        self.session = session
        # URL elicitations forwarded to the client, awaiting `elicitation/complete`
        self._pending_url_elicitations: set[str] = set()

    async def get_tool_confirmation(
        self,
        context: AgentContext[Any],
        tool_description: str = "",
    ) -> ToolDecision:
        """Ask the ACP client whether to run a tool call (``session/request_permission``).

        The options' kinds give the decision: ``allow_always`` and ``reject_always`` decide
        for the session, which then answers the tool's later calls without asking. A
        cancelled request (the client cancelled the prompt turn) stops the turn.

        Args:
            context: Current node context with tool_name, tool_call_id, tool_input set
            tool_description: Human-readable description of the tool
        """
        from agentpool.acp_utils import generate_tool_title

        tool_name = context.tool_name or "unknown"
        args = context.tool_input
        # The streaming loop has sent the tool call's start already, so the permission
        # request refers to a call the client knows (its tool_call_id must match).
        if not (tc_id := context.tool_call_id):
            logger.error("No tool_call_id for a permission request", tool_name=tool_name)
            return Deny(stop=True)
        logger.debug("Requesting permission", tool_name=tool_name, tool_call_id=tc_id)
        try:
            response = await self.session.requests.request_permission(
                tool_call_id=tc_id,
                title=generate_tool_title(tool_name, args),
                raw_input=args,
                options=DEFAULT_PERMISSION_OPTIONS,
            )
        except Exception:
            logger.exception("Failed to get tool confirmation")
            return Deny(stop=True)  # Stop rather than run on unasked
        logger.info("Permission response", tool_name=tool_name, outcome=response.outcome.outcome)
        match response.outcome:
            case AllowedOutcome(option_id=option_id):
                return _decision_of(option_id)
            case DeniedOutcome():
                return Deny(stop=True)
            case _ as unreachable:
                assert_never(unreachable)

    def _to_acp_elicitation(
        self, params: types.ElicitRequestParams, tool_call_id: str | None = None
    ) -> ElicitationRequest | None:
        """Translate an MCP elicitation into an ACP one, if the client supports that mode."""
        caps = self.session.client_capabilities.elicitation
        if caps is None:
            return None
        match params:
            case types.ElicitRequestURLParams(elicitation_id=elicit_id, message=msg, url=url):
                if caps.url is None:
                    return None
                elicit_id = elicit_id or str(uuid.uuid4())
                return ElicitationRequest.url_based(msg, elicit_id, url, tool_call_id=tool_call_id)
            case types.ElicitRequestFormParams(requested_schema=schema, message=msg):
                if caps.form is None:
                    return None
                if schema.get("type") != "object":
                    # A single value (e.g. the question tool's answer): one field named
                    # "value", whose content then is {"value": ...} (like the OpenCode server)
                    value = {"title": "Answer", **schema}
                    properties = {"value": value}
                    schema = {"type": "object", "properties": properties, "required": ["value"]}
                try:
                    acp_schema = ElicitationSchema.model_validate(schema)
                except ValidationError:
                    # ACP only allows flat objects of primitive properties
                    logger.debug("Schema not expressible as ACP elicitation", schema=schema)
                    return None
                return ElicitationRequest.form(msg, acp_schema, tool_call_id=tool_call_id)
            case _ as unreachable:
                assert_never(unreachable)

    async def get_elicitation(  # noqa: PLR0911
        self,
        params: types.ElicitRequestParams,
        *,
        tool_call_id: str | None = None,
    ) -> types.ElicitResult | types.ErrorData:
        """Get user response to elicitation request.

        Forwarded as ACP ``elicitation/create`` if the client supports the mode.
        Otherwise boolean and enum schemas are approximated via permission options,
        and other schemas fall back to accept/decline.

        Args:
            params: MCP elicit request parameters
            tool_call_id: ID of the tool call requesting input, if any

        Returns:
            Elicit result with user's response or error data
        """
        try:
            if request := self._to_acp_elicitation(params, tool_call_id):
                acp_response = await self.session.requests.elicit(request)
                if request.elicitation_id and acp_response.action == "accept":
                    self._pending_url_elicitations.add(request.elicitation_id)
                match acp_response.action:
                    case "accept":
                        content = acp_response.content
                        return types.ElicitResult(action="accept", content=content)
                    case "decline":
                        return types.ElicitResult(action="decline")
                    case _:
                        return types.ElicitResult(action="cancel")
            # Handle URL mode elicitation (OAuth, credentials, payments)
            match params:
                case types.ElicitRequestURLParams(message=message, url=url):
                    # Opening a browser here would happen on the server machine, which
                    # is not where the user is for remote clients.
                    msg = "Declining URL elicitation: client does not support URL elicitation"
                    logger.info(msg, message=message, url=url)
                    return types.ElicitResult(action="decline")
                case types.ElicitRequestFormParams(requested_schema=schema, message=message):
                    logger.info("Elicitation request", message=message, schema=schema)
                    tool_call_id = f"elicit_{hash(message)}"
                    title = f"Elicitation: {message}"
                    if _is_boolean_schema(schema):
                        options: list[PermissionOption] | None = (
                            _create_boolean_elicitation_options()
                        )
                        response = await self.session.requests.request_permission(
                            tool_call_id=tool_call_id,
                            title=title,
                            options=options,
                        )
                        return self._handle_boolean_elicitation_response(response, schema)
                    if (field := _enum_field(schema)) and (
                        options := _create_enum_elicitation_options(field[1])
                    ):
                        response = await self.session.requests.request_permission(
                            tool_call_id=tool_call_id,
                            title=title,
                            options=options,
                        )
                        return _handle_enum_elicitation_response(response, *field)

                    options = [
                        PermissionOption(option_id="accept", name="Accept", kind="allow_once"),
                        PermissionOption(option_id="decline", name="Decline", kind="reject_once"),
                    ]
                    response = await self.session.requests.request_permission(
                        tool_call_id=tool_call_id,
                        title=title,
                        options=options,
                    )

                    # Convert permission response to elicitation result
                    match response.outcome:
                        case AllowedOutcome(option_id="accept"):
                            # For non-boolean schemas, return empty content
                            return types.ElicitResult(action="accept", content={})
                        case AllowedOutcome():
                            return types.ElicitResult(action="decline")
                        case DeniedOutcome():
                            return types.ElicitResult(action="cancel")
                        case _ as unreachable:
                            assert_never(unreachable)
                case _ as unreachable:
                    assert_never(unreachable)

        except Exception as e:
            logger.exception("Failed to handle elicitation")
            return types.ErrorData(code=types.INTERNAL_ERROR, message=f"Elicitation failed: {e}")

    async def elicitation_complete(self, elicitation_id: str) -> None:
        """Tell the client a forwarded URL elicitation finished (``elicitation/complete``)."""
        if elicitation_id not in self._pending_url_elicitations:
            return
        self._pending_url_elicitations.discard(elicitation_id)
        notification = ElicitationCompleteNotification(elicitation_id=elicitation_id)
        await self.session.client.elicitation_complete(notification)

    def _handle_boolean_elicitation_response(
        self, response: RequestPermissionResponse, schema: dict[str, Any]
    ) -> types.ElicitResult | types.ErrorData:
        """Handle ACP response for boolean elicitation."""
        match response.outcome:
            case AllowedOutcome(option_id="false" | "true" as option_id):
                val = option_id == "true"
                # Check if we need to wrap in object structure
                if schema.get("type") == "object":
                    properties = schema.get("properties", {})
                    if len(properties) == 1:
                        prop_name = next(iter(properties.keys()))
                        return types.ElicitResult(action="accept", content={prop_name: val})
                return types.ElicitResult(action="accept", content={"value": val})
            case AllowedOutcome(option_id="cancel"):
                return types.ElicitResult(action="cancel")
            case DeniedOutcome():
                return types.ElicitResult(action="cancel")
            case _:
                return types.ElicitResult(action="cancel")


def _handle_enum_elicitation_response(
    response: RequestPermissionResponse, name: str | None, schema: dict[str, Any]
) -> types.ElicitResult | types.ErrorData:
    """Handle ACP response for enum elicitation.

    Args:
        response: The client's choice
        name: Property the form asks for (the answer's key), None for a bare enum
        schema: Schema of the enum (``enum`` holds the values)
    """
    from mcp import types

    match response.outcome:
        case AllowedOutcome(option_id="cancel"):
            return types.ElicitResult(action="cancel")
        case AllowedOutcome(option_id=option_id) if option_id.startswith("enum_"):
            # Extract enum value from option_id format: "enum_{index}_{value}"
            try:
                parts = option_id.split("_", 2)  # Split into ["enum", index, value]
                if len(parts) >= 3:  # noqa: PLR2004
                    enum_index = int(parts[1])
                    enum_values = schema.get("enum", [])
                    if 0 <= enum_index < len(enum_values):
                        selected_value = enum_values[enum_index]
                        return types.ElicitResult(
                            action="accept", content={name or "value": selected_value}
                        )
            except ValueError, IndexError:
                pass
            logger.warning("Failed to parse enum option_id", option_id=option_id)
            return types.ElicitResult(action="cancel")
        case AllowedOutcome(option_id=option_id):
            logger.warning("Failed to parse enum option_id", option_id=option_id)
            return types.ElicitResult(action="cancel")
        case DeniedOutcome():
            return types.ElicitResult(action="cancel")
        case _ as unreachable:
            assert_never(unreachable)
