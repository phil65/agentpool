"""Claude Code warnings and errors that are shown to the user as ``NoticeEvent``."""

from __future__ import annotations

from typing import TYPE_CHECKING, assert_never

from clawd_code_sdk.models import (
    HookResponseSystemMessage,
    InformationalSystemMessage,
    MirrorErrorSystemMessage,
    ModelRefusalFallbackMessage,
    ModelRefusalNoFallbackMessage,
    NotificationSystemMessage,
    PermissionDeniedSystemMessage,
    RateLimitMessage,
    SessionWritesLostMessage,
)

from agentpool.agents.events import NoticeEvent


if TYPE_CHECKING:
    from agentpool.agents.events.events import NoticeSeverity


type NoticeMessage = (
    ModelRefusalFallbackMessage
    | ModelRefusalNoFallbackMessage
    | PermissionDeniedSystemMessage
    | NotificationSystemMessage
    | InformationalSystemMessage
    | SessionWritesLostMessage
    | MirrorErrorSystemMessage
    | HookResponseSystemMessage
    | RateLimitMessage
)
"""Claude Code messages that may carry a notice for the user."""


def _refusal_detail(category: str | None, explanation: str | None) -> str | None:
    parts = [part for part in (category, explanation) if part]
    return ": ".join(parts) or None


def message_notice(message: NoticeMessage) -> NoticeEvent | None:  # noqa: PLR0911
    """Notice for a Claude Code message (None if there is nothing to tell the user)."""
    match message:
        case ModelRefusalFallbackMessage(original_model=original, fallback_model=fallback):
            detail = _refusal_detail(message.api_refusal_category, message.api_refusal_explanation)
            return NoticeEvent(
                title=f"Switched from {original} to {fallback} after a refusal",
                severity="warning",
                description=detail,
            )
        case ModelRefusalNoFallbackMessage(original_model=original):
            detail = _refusal_detail(message.api_refusal_category, message.api_refusal_explanation)
            return NoticeEvent(
                title=f"{original} refused the request",
                severity="error",
                description=detail or message.content or None,
            )
        case PermissionDeniedSystemMessage(tool_name=tool, decision_reason=reason):
            return NoticeEvent(
                title=f"Permission denied: {tool}",
                severity="warning",
                description=reason or message.message or None,
            )
        case NotificationSystemMessage(text=text, priority=priority):
            severity: NoticeSeverity = "warning" if priority in ("high", "immediate") else "info"
            return NoticeEvent(title=text, severity=severity)
        case InformationalSystemMessage(content=content, level=level):
            return NoticeEvent(title=content, severity="warning" if level == "warning" else "info")
        case SessionWritesLostMessage(error_code=code, lost=lost):
            return NoticeEvent(
                title="Claude Code could not save the session",
                severity="error",
                description=f"{code}: lost {', '.join(lost)}",
            )
        case MirrorErrorSystemMessage(error=error):
            return NoticeEvent(
                title="Session mirroring failed", severity="error", description=error
            )
        case HookResponseSystemMessage(outcome="error", hook_name=name, stderr=stderr):
            return NoticeEvent(
                title=f"Hook {name} failed", severity="warning", description=stderr or None
            )
        case HookResponseSystemMessage():
            return None
        case RateLimitMessage(rate_limit_info=info) if info.status != "allowed":
            window = (info.rate_limit_type or "usage").replace("_", " ")
            used = f" ({info.utilization:.0%} used)" if info.utilization is not None else ""
            rejected = info.status == "rejected"
            return NoticeEvent(
                title=f"Rate limit {'reached' if rejected else 'close'}: {window}{used}",
                severity="error" if rejected else "warning",
            )
        case RateLimitMessage():
            return None
        case _ as unreachable:
            assert_never(unreachable)
