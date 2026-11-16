"""OpenCode-based input provider for agent interactions."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import time
from typing import TYPE_CHECKING, Any, Literal, assert_never

from mcp import types

from agentpool.log import get_logger
from agentpool.ui.base import InputProvider
from agentpool.utils.time_utils import now_ms
from opencode_sdk.models import (
    PermissionAskedProperties,
    PermissionRequestEvent,
    PermissionToolInfo,
    QuestionAskedEvent,
    QuestionInfo,
    QuestionOption,
)


if TYPE_CHECKING:
    from agentpool.agents.context import AgentContext, ConfirmationResult
    from agentpool_server.opencode_server.state import ServerState
    from opencode_sdk.models import PermissionReply

logger = get_logger(__name__)


@dataclass
class PendingPermission:
    """A pending permission request awaiting user response."""

    permission_id: str
    tool_name: str
    args: dict[str, Any]
    future: asyncio.Future[PermissionReply]
    created_at: float = field(default_factory=time.time)


class PermissionManager:
    """Manages pending permission requests for tools that need confirmation."""

    def __init__(self) -> None:
        """Initialize PermissionManager."""
        self._pending_permissions: dict[str, PendingPermission] = {}

    def add_pending_permission(
        self,
        permission_id: str,
        tool_name: str,
        args: dict[str, Any],
        future: asyncio.Future[PermissionReply],
    ) -> PendingPermission:
        """Add a pending permission request."""
        pending = PendingPermission(
            permission_id=permission_id,
            tool_name=tool_name,
            args=args,
            future=future,
        )
        self._pending_permissions[permission_id] = pending
        return pending

    def resolve_permission(self, permission_id: str, response: PermissionReply) -> bool:
        """Resolve a pending permission request.

        Called by the REST endpoint when the client responds.

        Args:
            permission_id: The permission request ID
            response: The client's response ("once", "always", or "reject")

        Returns:
            True if the permission was found and resolved, False otherwise
        """
        pending = self._pending_permissions.get(permission_id)
        if pending is None:
            logger.warning("Permission not found", permission_id=permission_id)
            return False

        if pending.future.done():
            logger.warning("Permission already resolved", permission_id=permission_id)
            return False

        pending.future.set_result(response)
        logger.info("Permission resolved", permission_id=permission_id, response=response)
        return True

    def get_pending_permissions(self, session_id: str) -> list[PermissionAskedProperties]:
        """Get all pending permission requests.

        Returns:
            List of pending permission properties
        """
        result: list[PermissionAskedProperties] = []
        for p in self._pending_permissions.values():
            args_preview = ", ".join(f"{k}={v!r}" for k, v in list(p.args.items())[:3])
            pattern = f"{p.tool_name}: {args_preview}" if args_preview else p.tool_name
            props = PermissionAskedProperties(
                id=p.permission_id,
                session_id=session_id,
                permission=p.tool_name,
                patterns=[pattern],
                metadata=p.args,
                always=[pattern],
                tool=PermissionToolInfo(message_id="", call_id=None),
            )
            result.append(props)
        return result

    def cancel_all_pending(self) -> int:
        """Cancel all pending permission requests.

        Returns:
            Number of permissions cancelled
        """
        count = 0
        for pending in list(self._pending_permissions.values()):
            if not pending.future.done():
                pending.future.cancel()
                count += 1
        self._pending_permissions.clear()
        logger.info("Cancelled all pending permissions", count=count)
        return count

    def pop(self, permission_id: str) -> PendingPermission | None:
        return self._pending_permissions.pop(permission_id, None)


class OpenCodeInputProvider(InputProvider):
    """Input provider that uses OpenCode SSE/REST for user interactions.

    This provider enables tool confirmation and elicitation requests
    through the OpenCode protocol. When a tool needs confirmation:
    1. A permission request is created and stored
    2. An SSE event is broadcast to notify clients
    3. The provider awaits a response via the REST endpoint
    4. The client POSTs to /session/{id}/permissions/{permissionID} to respond
    """

    def __init__(self, state: ServerState, session_id: str) -> None:
        """Initialize OpenCode input provider.

        Args:
            state: Server state for broadcasting events
            session_id: The session ID for this provider
        """
        self.state = state
        self.session_id = session_id
        self.permission_manager = PermissionManager()
        # tool_name -> "always" | "reject"
        self._tool_approvals: dict[str, Literal["always", "reject"]] = {}
        self._id_counter = 0

    def _generate_permission_id(self) -> str:
        """Generate a unique permission ID."""
        self._id_counter += 1
        return f"perm_{self._id_counter}_{now_ms()}"

    async def get_tool_confirmation(
        self,
        context: AgentContext[Any],
        tool_description: str = "",
    ) -> ConfirmationResult:
        """Get tool execution confirmation via OpenCode permission request.

        Creates a pending permission, broadcasts an SSE event, and waits
        for the client to respond via POST /session/{id}/permissions/{permissionID}.

        Args:
            context: Current node context with tool_name, tool_call_id, tool_input set
            tool_description: Human-readable description of the tool

        Returns:
            Confirmation result indicating whether to allow, skip, or abort
        """
        tool_name = context.tool_name or "unknown"
        args = context.tool_input
        try:
            # Check if we have a standing approval/rejection for this tool
            if tool_name in self._tool_approvals:
                standing_decision = self._tool_approvals[tool_name]
                match standing_decision:
                    case "always":
                        logger.debug("Auto-allowing tool", tool_name=tool_name, reason="always")
                        return "allow"
                    case "reject":
                        logger.debug("Auto-rejecting tool", tool_name=tool_name, reason="reject")
                        return "skip"
                    case _ as unreachable:
                        assert_never(unreachable)

            # Create a pending permission request
            permission_id = self._generate_permission_id()
            future: asyncio.Future[PermissionReply] = asyncio.get_event_loop().create_future()
            self.permission_manager.add_pending_permission(
                permission_id=permission_id,
                tool_name=tool_name,
                args=args,
                future=future,
            )
            max_preview_args = 3
            args_preview = ", ".join(f"{k}={v!r}" for k, v in list(args.items())[:max_preview_args])
            if len(args) > max_preview_args:
                args_preview += ", ..."
            # Extract call_id from AgentContext if available (set by ClaudeCodeAgent from streaming)
            # Fall back to a generated ID if not available
            call_id = context.tool_call_id
            if call_id is None:
                # Generate a synthetic call_id - won't match TUI tool parts but allows display
                call_id = f"toolu_{permission_id}"
            # TODO: Extract message_id from context when available

            event = PermissionRequestEvent.create(
                session_id=self.session_id,
                permission_id=permission_id,
                tool_name=tool_name,
                args_preview=args_preview,
                message=f"Tool '{tool_name}' wants to execute with args: {args_preview}",
                call_id=call_id,
            )

            await self.state.broadcast_event(event)
            logger.info("Permission requested", permission_id=permission_id, tool_name=tool_name)
            # Wait for the client to respond
            try:
                response = await future
            except asyncio.CancelledError:
                logger.warning("Permission request cancelled", permission_id=permission_id)
                return "skip"
            finally:
                # Clean up the pending permission
                self.permission_manager.pop(permission_id)

            # Map OpenCode response to our confirmation result
            return self._handle_permission_response(response, tool_name)

        except Exception:
            logger.exception("Failed to get tool confirmation")
            return "abort_run"

    def _handle_permission_response(
        self, response: PermissionReply, tool_name: str
    ) -> ConfirmationResult:
        """Handle permission response and update tool approval state."""
        match response:
            case "once":
                return "allow"
            case "always":
                self._tool_approvals[tool_name] = "always"
                logger.info("Tool approval set", tool_name=tool_name, approval="always")
                return "allow"
            case "reject":
                return "skip"
            case _ as unreachable:
                assert_never(unreachable)

    async def get_elicitation(
        self,
        params: types.ElicitRequestParams,
    ) -> types.ElicitResult | types.ErrorData:
        """Get user response to elicitation request via OpenCode questions.

        Translates MCP elicitation requests to OpenCode question system.
        Supports both single-select and multi-select questions.

        Args:
            params: MCP elicit request parameters

        Returns:
            Elicit result with user's response or error data
        """
        match params:
            case types.ElicitRequestURLParams(message=message, url=url):
                # For URL elicitation, we could open the URL.in browser?
                logger.info("URL elicitation request", message=message, url=url)
                return types.ElicitResult(action="decline")
            case types.ElicitRequestFormParams(
                requested_schema=({"enum": _} | {"type": "array", "items": {"enum": _}}) as schema
            ):
                return await self._handle_question_elicitation(params, schema)
            case types.ElicitRequestFormParams(requested_schema={"type": "string"}):
                return await self._handle_text_elicitation(params)
            case types.ElicitRequestFormParams(requested_schema=schema, message=msg):
                # For other form elicitation, we don't have UI support yet
                logger.info("Form elicitation request (not supported)", message=msg, schema=schema)
                return types.ElicitResult(action="decline")
            case _ as unreachable:
                assert_never(unreachable)

    async def _handle_question_elicitation(
        self,
        params: types.ElicitRequestFormParams,
        schema: dict[str, Any],
    ) -> types.ElicitResult | types.ErrorData:
        """Handle elicitation via OpenCode question system.

        Args:
            params: Form elicitation parameters
            schema: JSON schema with enum values

        Returns:
            Elicit result with user's answer
        """
        from agentpool_server.opencode_server.state import PendingQuestion

        # Extract enum values
        match schema:
            case {"type": "array", "items": {"enum": [_, *_] as enum_values}}:
                is_multi = True
            case {"enum": [_, *_] as enum_values}:
                is_multi = False
            case _:
                return types.ElicitResult(action="decline")
        # Extract descriptions if available (custom x-option-descriptions field)
        descriptions = schema.get("x-option-descriptions", {})
        question_id = self._generate_permission_id()  # Reuse ID generator
        opts = [
            QuestionOption(label=str(val), description=descriptions.get(str(val), ""))
            for val in enum_values
        ]
        question_info = QuestionInfo(
            question=params.message,
            header=params.message[:12],  # Truncate to 12 chars
            options=opts,
            multiple=is_multi or None,
        )
        # Create future to wait for answer
        future: asyncio.Future[list[list[str]]] = asyncio.get_event_loop().create_future()
        self.state.pending_questions[question_id] = PendingQuestion(
            session_id=self.session_id,
            questions=[question_info],
            future=future,
        )
        # Broadcast event (serialize QuestionInfo to dict)
        event = QuestionAskedEvent.create(
            request_id=question_id,
            session_id=self.session_id,
            questions=[question_info],
        )
        await self.state.broadcast_event(event)
        logger.info(
            "Question asked",
            question_id=question_id,
            message=params.message,
            is_multi=is_multi,
        )
        # Wait for answer
        try:
            answers = await future  # list[list[str]]
            answer = answers[0] if answers else []  # Get first question's answer
            # ElicitResult content must be a dict, not a plain value
            # Wrap the answer in a dict with a "value" key
            # Multi-select: return list in dict
            # Single-select: return string in dict
            content: dict[str, Any] = (
                {"value": answer} if is_multi else {"value": answer[0] if answer else ""}
            )
            return types.ElicitResult(action="accept", content=content)
        except asyncio.CancelledError:
            logger.info("Question cancelled", question_id=question_id)
            return types.ElicitResult(action="cancel")
        except Exception as e:
            logger.exception("Question failed", question_id=question_id)
            return types.ErrorData(code=-1, message=f"Elicitation failed: {e}")  # Generic err code
        finally:
            # Clean up pending question
            self.state.pending_questions.pop(question_id, None)

    async def _handle_text_elicitation(
        self,
        params: types.ElicitRequestFormParams,
    ) -> types.ElicitResult | types.ErrorData:
        """Handle free-form text elicitation via OpenCode question system.

        Creates a question with a single "Other" option, which triggers
        the free-text input in the OpenCode UI.

        Args:
            params: Form elicitation parameters with {"type": "string"} schema

        Returns:
            Elicit result with user's text response
        """
        from agentpool_server.opencode_server.state import PendingQuestion

        question_id = self._generate_permission_id()
        opts = [QuestionOption(label="Other", description="Type your answer")]
        header = params.message[:12]
        question_info = QuestionInfo(question=params.message, header=header, options=opts)
        future: asyncio.Future[list[list[str]]] = asyncio.get_event_loop().create_future()
        self.state.pending_questions[question_id] = PendingQuestion(
            session_id=self.session_id,
            questions=[question_info],
            future=future,
        )
        event = QuestionAskedEvent.create(
            request_id=question_id,
            session_id=self.session_id,
            questions=[question_info],
        )
        await self.state.broadcast_event(event)
        logger.info("Text input question asked", question_id=question_id, message=params.message)
        try:
            answers = await future
            answer = answers[0][0] if answers and answers[0] else ""
            content: dict[str, Any] = {"value": answer}
            return types.ElicitResult(action="accept", content=content)
        except asyncio.CancelledError:
            logger.info("Question cancelled", question_id=question_id)
            return types.ElicitResult(action="cancel")
        except Exception as e:
            logger.exception("Question failed", question_id=question_id)
            return types.ErrorData(code=-1, message=f"Elicitation failed: {e}")
        finally:
            self.state.pending_questions.pop(question_id, None)

    def clear_tool_approvals(self) -> None:
        """Clear all stored tool approval decisions."""
        approval_count = len(self._tool_approvals)
        self._tool_approvals.clear()
        logger.info("Cleared tool approval decisions", count=approval_count)

    def resolve_question(self, question_id: str, answers: list[list[str]]) -> bool:
        """Resolve a pending question request.

        Called by the REST endpoint when the client responds.

        Args:
            question_id: The question request ID
            answers: User's answers (array of arrays per OpenCode format)

        Returns:
            True if the question was found and resolved, False otherwise
        """
        pending = self.state.pending_questions.get(question_id)
        if pending is None:
            logger.warning("Question not found", question_id=question_id)
            return False

        future = pending.future
        if future.done():
            logger.warning("Question already resolved", question_id=question_id)
            return False

        future.set_result(answers)
        logger.info("Question resolved", question_id=question_id, answers=answers)
        return True
