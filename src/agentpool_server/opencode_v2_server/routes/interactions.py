"""Permission and form routes."""

from __future__ import annotations

import fnmatch

from fastapi import APIRouter
from fastapi.responses import Response

from agentpool.utils import identifiers
from agentpool_server.opencode_v2_server.deps import (
    DirectoryDep,
    SessionDep,
    StateDep,
    V2Response,
    ok,
)
from agentpool_server.opencode_v2_server.errors import (
    form_not_found,
    form_settled,
    permission_not_found,
    saved_permission_not_found,
)
from agentpool_server.opencode_v2_server.interactions import FormSettledError, InteractionError
from agentpool_server.opencode_v2_server.location import public_ref
from opencode_v2_sdk.models import (
    FormCreatePayload,
    FormListResponse,
    FormReply,
    PermissionRequestListResponse,
    PermissionRule,
    PermissionSavedListResponse,
    PermissionSavedUpdateBody,
    PermissionSavedUpdateResponse,
    SessionFormCreateResponse,
    SessionFormGetResponse,
    SessionFormListResponse,
    SessionPermissionCreateBody,
    SessionPermissionCreateResponse,
    SessionPermissionCreateResponseData,
    SessionPermissionGetResponse,
    SessionPermissionListResponse,
    SessionPermissionReplyBody,
)


router = APIRouter(tags=["permission"], default_response_class=V2Response)


def evaluate_rules(rules: list[PermissionRule], action: str, resource: str) -> str | None:
    """Effect of the last matching rule of an ordered ruleset (None if nothing matches)."""
    effect: str | None = None
    for rule in rules:
        if fnmatch.fnmatch(action, rule.action) and fnmatch.fnmatch(resource, rule.resource):
            effect = rule.effect
    return effect


# --- permissions ---------------------------------------------------------------------------


@router.get("/api/permission/request")
async def permission_request_list(state: StateDep, directory: DirectoryDep) -> Response:
    """All pending permission requests."""
    data = [p.request for p in state.permissions.pending.values()]
    return ok(PermissionRequestListResponse(location=public_ref(directory), data=data))


@router.get("/api/permission/saved")
async def permission_saved_list(state: StateDep) -> Response:
    """Saved (always-allow) permission rules."""
    await state.permissions.load()
    return ok(PermissionSavedListResponse(data=list(state.permissions.saved)))


@router.patch("/api/permission/saved/{rule_id}")
async def permission_saved_update(
    rule_id: str, body: PermissionSavedUpdateBody, state: StateDep
) -> Response:
    """Edit a saved rule."""
    try:
        rule = await state.permissions.update_saved(
            rule_id, action=body.action, resource=body.resource
        )
    except InteractionError:
        raise saved_permission_not_found(rule_id) from None
    return ok(PermissionSavedUpdateResponse(data=rule))


@router.delete("/api/permission/saved/{rule_id}", status_code=204)
async def permission_saved_remove(rule_id: str, state: StateDep) -> Response:
    """Delete a saved rule."""
    await state.permissions.remove_saved(rule_id)
    return Response(status_code=204)


@router.post("/api/session/{session_id}/permission")
async def session_permission_create(
    session: SessionDep, body: SessionPermissionCreateBody, state: StateDep
) -> Response:
    """Evaluate a permission; ask the user when no rule decides it."""
    await state.permissions.load()
    rules = session.info.permissions or []
    effects = [evaluate_rules(rules, body.action, resource) for resource in body.resources]
    if any(effect == "deny" for effect in effects):
        effect = "deny"
    elif all(effect == "allow" for effect in effects) or state.permissions.is_saved(
        session.info.project_id, body.action, body.resources
    ):
        effect = "allow"
    else:
        effect = "ask"
    request_id = body.id
    if effect == "ask":
        request = state.permissions.create(
            session_id=session.id,
            project_id=session.info.project_id,
            directory=session.directory,
            action=body.action,
            resources=body.resources,
            save=body.save,
            metadata=body.metadata,
            source=body.source,
            request_id=body.id,
        )
        request_id = request.id
    data = SessionPermissionCreateResponseData(
        id=request_id or identifiers.ascending("permission"), effect=effect
    )
    return ok(SessionPermissionCreateResponse(data=data))


@router.get("/api/session/{session_id}/permission")
async def session_permission_list(session: SessionDep, state: StateDep) -> Response:
    """Pending permission requests of a session."""
    return ok(SessionPermissionListResponse(data=state.permissions.for_session(session.id)))


@router.get("/api/session/{session_id}/permission/{request_id}")
async def session_permission_get(session: SessionDep, request_id: str, state: StateDep) -> Response:
    """One pending permission request."""
    pending = state.permissions.pending.get(request_id)
    if pending is None or pending.request.session_id != session.id:
        raise permission_not_found(request_id)
    return ok(SessionPermissionGetResponse(data=pending.request))


@router.post("/api/session/{session_id}/permission/{request_id}/reply", status_code=204)
async def session_permission_reply(
    session: SessionDep, request_id: str, body: SessionPermissionReplyBody, state: StateDep
) -> Response:
    """Answer a permission request (once / always / reject)."""
    try:
        await state.permissions.reply(session.id, request_id, body.decision, session.directory)
    except InteractionError:
        raise permission_not_found(request_id) from None
    return Response(status_code=204)


# --- forms ---------------------------------------------------------------------------------


@router.get("/api/form")
async def form_list(state: StateDep, directory: DirectoryDep) -> Response:
    """All pending forms."""
    data = [p.form for p in state.forms.pending.values()]
    return ok(FormListResponse(location=public_ref(directory), data=data))


@router.get("/api/session/{session_id}/form")
async def session_form_list(session: SessionDep, state: StateDep) -> Response:
    """Pending forms of a session."""
    return ok(SessionFormListResponse(data=state.forms.for_session(session.id)))


@router.post("/api/session/{session_id}/form")
async def session_form_create(
    session: SessionDep, body: FormCreatePayload, state: StateDep
) -> Response:
    """Create a form that clients answer via ``.../form/{formID}/reply``."""
    pending = state.forms.create(
        session_id=session.id,
        directory=session.directory,
        title=body.title,
        fields=list(body.fields),
        metadata=body.metadata,
        form_id=body.id,
    )
    return ok(SessionFormCreateResponse(data=pending.form))


@router.get("/api/session/{session_id}/form/{form_id}")
async def session_form_get(session: SessionDep, form_id: str, state: StateDep) -> Response:
    """A form with its current state."""
    detail = state.forms.detail(session.id, form_id)
    if detail is None:
        raise form_not_found(form_id)
    return ok(SessionFormGetResponse(data=detail))


@router.post("/api/session/{session_id}/form/{form_id}/reply", status_code=204)
async def session_form_reply(
    session: SessionDep, form_id: str, body: FormReply, state: StateDep
) -> Response:
    """Answer a form."""
    try:
        state.forms.reply(session.id, form_id, dict(body.answer))
    except FormSettledError:
        raise form_settled(form_id) from None
    except InteractionError:
        raise form_not_found(form_id) from None
    return Response(status_code=204)


@router.delete("/api/session/{session_id}/form/{form_id}", status_code=204)
async def session_form_cancel(session: SessionDep, form_id: str, state: StateDep) -> Response:
    """Cancel a pending form."""
    try:
        state.forms.cancel(session.id, form_id)
    except FormSettledError:
        raise form_settled(form_id) from None
    except InteractionError:
        raise form_not_found(form_id) from None
    return Response(status_code=204)
