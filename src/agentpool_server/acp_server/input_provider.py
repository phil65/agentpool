"""ACP-based input provider for agent interactions."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, assert_never
import uuid

from aclientp import AllowedOutcome, DeniedOutcome, PermissionOption
from aclientp.schema import ElicitationCompleteNotification, ElicitationRequest, ElicitationSchema
from mcp import types
from pydantic import ValidationError

from agentpool.acp_utils import DEFAULT_PERMISSION_OPTIONS
from agentpool.log import get_logger
from agentpool.ui.base import InputProvider


if TYPE_CHECKING:
    from aclientp import RequestPermissionResponse

    from agentpool import AgentContext
    from agentpool.agents.context import ConfirmationResult
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


def _is_enum_schema(schema: dict[str, Any]) -> bool:
    """Check if the elicitation schema is requesting an enum value."""
    return schema.get("type") == "string" and "enum" in schema


def _create_boolean_elicitation_options() -> list[PermissionOption]:
    """Create permission options for boolean elicitation (Yes/No)."""
    return [
        PermissionOption(option_id="true", name="Yes", kind="allow_once"),
        PermissionOption(option_id="false", name="No", kind="reject_once"),
        PermissionOption(option_id="cancel", name="Cancel", kind="reject_always"),
    ]


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
        self._tool_approvals: dict[str, Literal["allow_always", "reject_always"]] = {}
        # URL elicitations forwarded to the client, awaiting `elicitation/complete`
        self._pending_url_elicitations: set[str] = set()

    async def get_tool_confirmation(
        self,
        context: AgentContext[Any],
        tool_description: str = "",
    ) -> ConfirmationResult:
        """Get tool execution confirmation via ACP request permission.

        Uses the ACP session's request_permission mechanism to ask
        the client for confirmation before executing the tool.

        Args:
            context: Current node context with tool_name, tool_call_id, tool_input set
            tool_description: Human-readable description of the tool

        Returns:
            Confirmation result indicating whether to allow, skip, or abort
        """
        tool_name = context.tool_name or "unknown"
        args = context.tool_input
        from agentpool.acp_utils import generate_tool_title

        try:
            # Check if we have a standing approval/rejection for this tool
            if decision := self._tool_approvals.get(tool_name):
                logger.debug("Get tool confirmation", tool_name=tool_name, reason=decision)
                match decision:
                    case "allow_always":
                        return "allow"
                    case "reject_always":
                        return "skip"
                    case _ as unreachable:
                        assert_never(unreachable)

            # Create a descriptive title for the permission request
            # args_str = ", ".join(f"{k}={v}" for k, v in args.items())
            # Use the tool_call_id from context - this must match the UI tool call
            tc_id = context.tool_call_id
            if not tc_id:
                msg = f"No tool_call_id in context for tool {tool_name!r}. "
                logger.error(msg)
                raise RuntimeError(msg)  # noqa: TRY301
            logger.debug("Requesting permission", tool_name=tool_name, tool_call_id=tc_id)
            # Note: We no longer send tool_call_start/progress notifications here.
            # The streaming loop (via ACPEventConverter) already sends ToolCallStart
            # before the permission callback is invoked, so Zed already knows about
            # the tool call. Sending duplicates was causing sync issues.

            title = generate_tool_title(tool_name, args)
            response = await self.session.requests.request_permission(
                tool_call_id=tc_id,
                title=title,
                raw_input=args,
                options=DEFAULT_PERMISSION_OPTIONS,
            )
            logger.info(
                "Permission response received",
                tool_name=tool_name,
                outcome=response.outcome.outcome,
            )
            # Map ACP permission response to our confirmation result
            match response.outcome:
                case AllowedOutcome(option_id=option_id):
                    return self._handle_permission_response(option_id, tool_name)
                case DeniedOutcome():
                    # The client cancelled the prompt turn: stop instead of running on
                    return "abort_run"
                case _ as outcome:
                    logger.warning("Unexpected permission outcome", outcome=outcome)

        except Exception:
            logger.exception("Failed to get tool confirmation")
            # Default to abort on error to be safe
            return "abort_run"
        else:
            return "skip"  # Default to skip for unknown outcomes

    def _handle_permission_response(self, option_id: str, tool_name: str) -> ConfirmationResult:
        """Handle permission response and update tool approval state."""
        # Normalize to hyphen format for matching
        normalized = option_id.replace("_", "-")
        logger.info(
            "Handling permission response",
            option_id=option_id,
            normalized=normalized,
            tool_name=tool_name,
        )
        match normalized:
            case "allow-once":
                return "allow"
            case "allow-always":
                self._tool_approvals[tool_name] = "allow_always"
                logger.info("Tool approval set", tool_name=tool_name, approval="allow_always")
                return "allow"
            case "reject-once" | "deny-once":
                return "skip"
            case "reject-always" | "deny-always":
                self._tool_approvals[tool_name] = "reject_always"
                logger.info("Tool approval set", tool_name=tool_name, approval="reject_always")
                return "skip"
            case _:
                logger.warning("Unknown permission option", option_id=option_id)
                return "abort_run"

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
                    if _is_enum_schema(schema) and (
                        options := _create_enum_elicitation_options(schema)
                    ):
                        response = await self.session.requests.request_permission(
                            tool_call_id=tool_call_id,
                            title=title,
                            options=options,
                        )
                        return _handle_enum_elicitation_response(response, schema)

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

    def clear_tool_approvals(self) -> None:
        """Clear all stored tool approval decisions.

        This resets the "allow_always" and "reject_always" states,
        so tools will ask for permission again.
        """
        approval_count = len(self._tool_approvals)
        self._tool_approvals.clear()
        logger.info("Cleared tool approval decisions", count=approval_count)

    def get_tool_approval_state(self) -> dict[str, Literal["allow_always", "reject_always"]]:
        """Get current tool approval state for debugging/inspection.

        Returns:
            Dictionary mapping tool names to their approval state
            ("allow_always" or "reject_always")
        """
        return self._tool_approvals.copy()


def _handle_enum_elicitation_response(
    response: RequestPermissionResponse, schema: dict[str, Any]
) -> types.ElicitResult | types.ErrorData:
    """Handle ACP response for enum elicitation."""
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
                            action="accept", content={"value": selected_value}
                        )
            except (ValueError, IndexError):
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
