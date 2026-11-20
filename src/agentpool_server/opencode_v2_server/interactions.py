"""Permissions and forms: user interactions requested by running agents.

* Tool confirmations become V2 permission requests (``permission.asked``), answered via
  ``POST /api/session/{id}/permission/{requestID}/reply``. "always" replies are saved as
  project-level rules (``/api/permission/saved``) and auto-approve later requests.
* MCP elicitations and structured/text input requests become V2 forms (``form.created``),
  answered via ``POST /api/session/{id}/form/{formID}/reply``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import fnmatch
import json
from typing import TYPE_CHECKING, Any, Literal, assert_never

import anyio
from mcp import types
from pydantic import BaseModel, TypeAdapter, ValidationError

from agentpool.log import get_logger
from agentpool.ui.base import InputProvider
from agentpool.utils import identifiers
from agentpool.utils.time_utils import now_ms
from agentpool_config.storage import DATA_DIR
from agentpool_server.opencode_v2_server.events import build_event
from opencode_v2_sdk.models import (
    FormBooleanField,
    FormCancelled,
    FormCancelledData,
    FormCreated,
    FormCreatedData,
    FormDetail,
    FormExternalField,
    FormInfo,
    FormInfo1,
    FormIntegerField,
    FormMultiselectField,
    FormNumberField,
    FormOption,
    FormReplied,
    FormRepliedData,
    FormStateVariant0,
    FormStateVariant1,
    FormStateVariant2,
    FormStringField,
    PermissionAsked,
    PermissionAskedData,
    PermissionReplied,
    PermissionRepliedData,
    PermissionRequest,
    PermissionSavedInfo,
    PermissionSavedInfoTime,
    PermissionSourceTool,
)


if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from agentpool.agents.context import AgentContext, ConfirmationResult
    from agentpool.messaging.context import NodeContext


logger = get_logger(__name__)

type FormField = (
    FormStringField
    | FormNumberField
    | FormIntegerField
    | FormBooleanField
    | FormMultiselectField
    | FormExternalField
)
type FormAnswerValue = str | float | bool | list[str]
type FormAnswer = dict[str, FormAnswerValue]
type PermissionDecision = Literal["once", "always", "reject"]
type Publisher = Callable[[BaseModel, str], None]
"""Publishes a (non-session) event: ``(event, event_type)``."""

DEFAULT_SAVED_PERMISSIONS_FILE = DATA_DIR / "opencode-v2" / "permissions.json"
_SAVED_ADAPTER: TypeAdapter[list[PermissionSavedInfo]] = TypeAdapter(list[PermissionSavedInfo])


class InteractionError(Exception):
    """A permission or form could not be found or answered."""


class FormSettledError(InteractionError):
    """The form was already answered or cancelled."""


class InteractionCancelledError(Exception):
    """A pending form was cancelled by the user."""


# --- permissions ---------------------------------------------------------------------------


@dataclass
class PendingPermission:
    """A permission request awaiting a decision."""

    request: PermissionRequest
    project_id: str
    future: asyncio.Future[PermissionDecision]


@dataclass
class PermissionRegistry:
    """Pending permission requests and saved (always-allow) rules."""

    publish: Publisher
    saved_file: Path = DEFAULT_SAVED_PERMISSIONS_FILE
    pending: dict[str, PendingPermission] = field(default_factory=dict)
    saved: list[PermissionSavedInfo] = field(default_factory=list)
    _loaded: bool = False

    async def load(self) -> None:
        """Load saved rules from disk (once)."""
        if self._loaded:
            return
        self._loaded = True
        path = anyio.Path(self.saved_file)
        if not await path.exists():
            return
        try:
            self.saved = _SAVED_ADAPTER.validate_json(await path.read_text(encoding="utf-8"))
        except (ValidationError, OSError):
            logger.warning("Could not read saved permissions", path=str(self.saved_file))

    async def _store(self) -> None:
        path = anyio.Path(self.saved_file)
        await path.parent.mkdir(parents=True, exist_ok=True)
        data = _SAVED_ADAPTER.dump_json(self.saved, by_alias=True, exclude_none=True)
        await path.write_bytes(data)

    def is_saved(self, project_id: str, action: str, resources: list[str]) -> bool:
        """Whether a saved rule allows the action on all resources."""
        rules = [r for r in self.saved if r.project_id == project_id and r.action == action]
        return bool(rules) and all(
            any(fnmatch.fnmatch(res, rule.resource) for rule in rules) for res in resources
        )

    async def ask(
        self,
        *,
        session_id: str,
        project_id: str,
        directory: str,
        action: str,
        resources: list[str],
        save: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        source: PermissionSourceTool | None = None,
        message: str | None = None,
        request_id: str | None = None,
    ) -> PermissionDecision:
        """Create a permission request and wait for the decision."""
        await self.load()
        if self.is_saved(project_id, action, resources):
            return "always"
        request = self.create(
            session_id=session_id,
            project_id=project_id,
            directory=directory,
            action=action,
            resources=resources,
            save=save,
            metadata=metadata,
            source=source,
            message=message,
            request_id=request_id,
        )
        pending = self.pending[request.id]
        try:
            return await pending.future
        finally:
            self.pending.pop(request.id, None)

    def create(
        self,
        *,
        session_id: str,
        project_id: str,
        directory: str,
        action: str,
        resources: list[str],
        save: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        source: PermissionSourceTool | None = None,
        message: str | None = None,
        request_id: str | None = None,
    ) -> PermissionRequest:
        """Register a permission request and publish ``permission.asked``."""
        request = PermissionRequest(
            id=request_id or identifiers.ascending("permission"),
            session_id=session_id,
            action=action,
            resources=resources,
            save=save,
            metadata=metadata,
            source=source,
            message=message,
        )
        future: asyncio.Future[PermissionDecision] = asyncio.get_running_loop().create_future()
        self.pending[request.id] = PendingPermission(
            request=request, project_id=project_id, future=future
        )
        data = PermissionAskedData.model_validate(request.model_dump(by_alias=True))
        self.publish(_event(PermissionAsked, data, directory), "permission.asked")
        return request

    async def reply(
        self, session_id: str, request_id: str, decision: PermissionDecision, directory: str
    ) -> None:
        """Answer a pending permission request.

        Raises:
            InteractionError: If the request is unknown or already answered
        """
        pending = self.pending.get(request_id)
        if pending is None or pending.request.session_id != session_id:
            msg = f"Permission request {request_id} not found"
            raise InteractionError(msg)
        if pending.future.done():
            msg = f"Permission request {request_id} was already answered"
            raise InteractionError(msg)
        if decision == "always":
            await self._save_rules(pending)
        pending.future.set_result(decision)
        data = PermissionRepliedData(session_id=session_id, request_id=request_id, reply=decision)
        self.publish(_event(PermissionReplied, data, directory), "permission.replied")

    async def _save_rules(self, pending: PendingPermission) -> None:
        await self.load()
        now = now_ms()
        for resource in pending.request.save or pending.request.resources:
            exists = any(
                r.project_id == pending.project_id
                and r.action == pending.request.action
                and r.resource == resource
                for r in self.saved
            )
            if not exists:
                self.saved.append(
                    PermissionSavedInfo(
                        id=identifiers.ascending("permission"),
                        project_id=pending.project_id,
                        action=pending.request.action,
                        resource=resource,
                        time=PermissionSavedInfoTime(created=now, updated=now),
                    )
                )
        await self._store()

    async def update_saved(
        self, rule_id: str, *, action: str | None, resource: str | None
    ) -> PermissionSavedInfo:
        """Update a saved rule.

        Raises:
            InteractionError: If the rule does not exist
        """
        await self.load()
        for index, rule in enumerate(self.saved):
            if rule.id == rule_id:
                time = rule.time.model_copy(update={"updated": now_ms()})
                updated = rule.model_copy(
                    update={
                        "action": action if action is not None else rule.action,
                        "resource": resource if resource is not None else rule.resource,
                        "time": time,
                    }
                )
                self.saved[index] = updated
                await self._store()
                return updated
        msg = f"Saved permission {rule_id} not found"
        raise InteractionError(msg)

    async def remove_saved(self, rule_id: str) -> None:
        """Remove a saved rule (no-op if unknown)."""
        await self.load()
        self.saved = [r for r in self.saved if r.id != rule_id]
        await self._store()

    def for_session(self, session_id: str) -> list[PermissionRequest]:
        """Pending requests of one session."""
        return [p.request for p in self.pending.values() if p.request.session_id == session_id]

    def cancel_session(self, session_id: str) -> None:
        """Reject all pending requests of a session (e.g. on interrupt)."""
        for pending in list(self.pending.values()):
            if pending.request.session_id == session_id and not pending.future.done():
                pending.future.set_result("reject")


# --- forms ---------------------------------------------------------------------------------


@dataclass
class PendingForm:
    """A form awaiting an answer."""

    form: FormInfo
    directory: str
    future: asyncio.Future[FormAnswer]


MAX_SETTLED_FORMS = 256
"""Number of answered/cancelled forms kept for ``GET .../form/{id}``."""


@dataclass
class FormRegistry:
    """Pending forms (and recently settled ones)."""

    publish: Publisher
    pending: dict[str, PendingForm] = field(default_factory=dict)
    settled: dict[str, FormDetail] = field(default_factory=dict)

    def _settle(self, form: FormInfo, state: FormStateVariant1 | FormStateVariant2) -> None:
        detail = FormDetail.model_validate({
            **form.model_dump(by_alias=True, exclude_none=True),
            "state": state.model_dump(),
        })
        self.settled[form.id] = detail
        while len(self.settled) > MAX_SETTLED_FORMS:
            self.settled.pop(next(iter(self.settled)))

    def detail(self, session_id: str, form_id: str) -> FormDetail | None:
        """Form with its state (pending, answered or cancelled)."""
        if (pending := self.pending.get(form_id)) is not None:
            if pending.form.session_id != session_id:
                return None
            return FormDetail.model_validate({
                **pending.form.model_dump(by_alias=True, exclude_none=True),
                "state": FormStateVariant0().model_dump(),
            })
        detail = self.settled.get(form_id)
        return detail if detail is not None and detail.session_id == session_id else None

    def create(
        self,
        *,
        session_id: str,
        directory: str,
        title: str,
        fields: list[FormField],
        metadata: dict[str, Any] | None = None,
        form_id: str | None = None,
    ) -> PendingForm:
        """Register a form and publish ``form.created``."""
        form = FormInfo(
            id=form_id or identifiers.ascending("form"),
            session_id=session_id,
            title=title,
            metadata=metadata,
            fields=fields,
        )
        future: asyncio.Future[FormAnswer] = asyncio.get_running_loop().create_future()
        # Forms created through the API have no awaiting caller: mark exceptions retrieved.
        future.add_done_callback(lambda f: None if f.cancelled() else f.exception())
        pending = PendingForm(form=form, directory=directory, future=future)
        self.pending[form.id] = pending
        event_form = FormInfo1.model_validate(form.model_dump(by_alias=True, exclude_none=True))
        self.publish(
            _event(FormCreated, FormCreatedData(form=event_form), directory), "form.created"
        )
        return pending

    async def ask(
        self,
        *,
        session_id: str,
        directory: str,
        title: str,
        fields: list[FormField],
        metadata: dict[str, Any] | None = None,
    ) -> FormAnswer:
        """Create a form and wait for the answer.

        Raises:
            InteractionCancelledError: If the form gets cancelled
        """
        pending = self.create(
            session_id=session_id,
            directory=directory,
            title=title,
            fields=fields,
            metadata=metadata,
        )
        try:
            return await pending.future
        finally:
            self.pending.pop(pending.form.id, None)

    def reply(self, session_id: str, form_id: str, answer: FormAnswer) -> None:
        """Answer a pending form.

        Raises:
            InteractionError: If the form is unknown or already answered
        """
        pending = self._get(session_id, form_id)
        pending.future.set_result(answer)
        self.pending.pop(form_id, None)
        self._settle(pending.form, FormStateVariant1(answer=answer))
        data = FormRepliedData(id=form_id, session_id=session_id, answer=answer)
        self.publish(_event(FormReplied, data, pending.directory), "form.replied")

    def cancel(self, session_id: str, form_id: str) -> None:
        """Cancel a pending form.

        Raises:
            InteractionError: If the form is unknown or already answered
        """
        pending = self._get(session_id, form_id)
        pending.future.set_exception(InteractionCancelledError(form_id))
        self.pending.pop(form_id, None)
        self._settle(pending.form, FormStateVariant2())
        data = FormCancelledData(id=form_id, session_id=session_id)
        self.publish(_event(FormCancelled, data, pending.directory), "form.cancelled")

    def _get(self, session_id: str, form_id: str) -> PendingForm:
        pending = self.pending.get(form_id)
        if pending is None and form_id in self.settled:
            msg = f"Form {form_id} was already settled"
            raise FormSettledError(msg)
        if pending is None or pending.form.session_id != session_id:
            msg = f"Form {form_id} not found"
            raise InteractionError(msg)
        if pending.future.done():
            msg = f"Form {form_id} was already answered"
            raise InteractionError(msg)
        return pending

    def for_session(self, session_id: str) -> list[FormInfo]:
        """Pending forms of one session."""
        return [p.form for p in self.pending.values() if p.form.session_id == session_id]

    def cancel_session(self, session_id: str) -> None:
        """Cancel all pending forms of a session."""
        for form_id, pending in list(self.pending.items()):
            if pending.form.session_id == session_id and not pending.future.done():
                self.cancel(session_id, form_id)


def _event[E: BaseModel](event_cls: type[E], data: BaseModel, directory: str) -> E:
    return build_event(event_cls, data, directory=directory)


# --- JSON schema <-> form fields -----------------------------------------------------------


def _options(values: list[Any], descriptions: dict[str, str]) -> list[FormOption]:
    return [
        FormOption(value=str(v), label=str(v), description=descriptions.get(str(v))) for v in values
    ]


def schema_to_field(key: str, schema: dict[str, Any], *, required: bool) -> FormField | None:  # noqa: PLR0911
    """Convert one JSON schema property into a form field (None if unsupported)."""
    title = schema.get("title")
    description = schema.get("description")
    descriptions: dict[str, str] = schema.get("x-option-descriptions", {})
    match schema:
        case {"enum": [_, *_] as values}:
            return FormStringField(
                key=key,
                title=title,
                description=description,
                required=required,
                options=_options(values, descriptions),
                custom=False,
                default=str(schema["default"]) if "default" in schema else None,
            )
        case {"type": "array", "items": {"enum": [_, *_] as values}}:
            return FormMultiselectField(
                key=key,
                title=title,
                description=description,
                required=required,
                options=_options(values, descriptions),
                min_items=schema.get("minItems"),
                max_items=schema.get("maxItems"),
            )
        case {"type": "string"}:
            fmt = schema.get("format")
            return FormStringField(
                key=key,
                title=title,
                description=description,
                required=required,
                format=fmt if fmt in {"email", "uri", "date", "date-time"} else None,
                min_length=schema.get("minLength"),
                max_length=schema.get("maxLength"),
                pattern=schema.get("pattern"),
                default=schema.get("default"),
            )
        case {"type": "number"}:
            return FormNumberField(
                key=key,
                title=title,
                description=description,
                required=required,
                minimum=schema.get("minimum"),
                maximum=schema.get("maximum"),
                default=schema.get("default"),
            )
        case {"type": "integer"}:
            return FormIntegerField(
                key=key,
                title=title,
                description=description,
                required=required,
                minimum=schema.get("minimum"),
                maximum=schema.get("maximum"),
                default=schema.get("default"),
            )
        case {"type": "boolean"}:
            return FormBooleanField(
                key=key,
                title=title,
                description=description,
                required=required,
                default=schema.get("default"),
            )
        case _:
            return None


def object_schema_to_fields(schema: dict[str, Any]) -> list[FormField] | None:
    """Convert an object JSON schema into form fields (None if any property is unsupported)."""
    properties: dict[str, Any] = schema.get("properties", {})
    required = set(schema.get("required", []))
    fields: list[FormField] = []
    for key, prop in properties.items():
        if not isinstance(prop, dict):
            return None
        converted = schema_to_field(key, prop, required=key in required)
        if converted is None:
            return None
        fields.append(converted)
    return fields or None


# --- input provider ------------------------------------------------------------------------


@dataclass
class SessionInputProvider(InputProvider):
    """Input provider routing a session's interactions to V2 permissions and forms."""

    session_id: str
    project_id: str
    directory: str
    permissions: PermissionRegistry
    forms: FormRegistry
    tool_message_id: Callable[[str], str | None] = field(default=lambda _call_id: None)
    """Resolves the assistant message ID of a tool call (for the permission source)."""

    async def get_tool_confirmation(
        self,
        context: AgentContext[Any],
        tool_description: str = "",
    ) -> ConfirmationResult:
        """Ask for tool confirmation via a V2 permission request."""
        tool_name = context.tool_name or "unknown"
        args = context.tool_input
        preview = ", ".join(f"{k}={v!r}" for k, v in list(args.items())[:3])
        resource = _permission_resource(args)
        source = None
        if context.tool_call_id and (msg_id := self.tool_message_id(context.tool_call_id)):
            source = PermissionSourceTool(message_id=msg_id, id=context.tool_call_id)
        message = tool_description or f"{tool_name}({preview})"
        try:
            decision = await self.permissions.ask(
                session_id=self.session_id,
                project_id=self.project_id,
                directory=self.directory,
                action=tool_name,
                resources=[resource],
                save=["*"],
                metadata=_json_safe(args),
                source=source,
                message=message,
            )
        except asyncio.CancelledError:
            return "skip"
        return "skip" if decision == "reject" else "allow"

    async def get_elicitation(
        self,
        params: types.ElicitRequestParams,
        *,
        tool_call_id: str | None = None,
    ) -> types.ElicitResult | types.ErrorData:
        """Answer an MCP elicitation through a V2 form."""
        metadata: dict[str, Any] = {"source": "elicitation"}
        if tool_call_id:
            metadata["toolCallID"] = tool_call_id
        match params:
            case types.ElicitRequestURLParams(message=message, url=url):
                fields: list[FormField] = [
                    FormExternalField(key="url", url=url, title=message or "Open URL")
                ]
                single = False
            case types.ElicitRequestFormParams(requested_schema=schema, message=message):
                single = schema.get("type") != "object"
                converted = (
                    object_schema_to_fields(dict(schema))
                    if not single
                    else _single(schema_to_field("value", dict(schema), required=True))
                )
                if converted is None:
                    logger.info("Unsupported elicitation schema", schema=schema)
                    return types.ElicitResult(action="decline")
                fields = converted
            case _ as unreachable:
                assert_never(unreachable)
        try:
            answer = await self.forms.ask(
                session_id=self.session_id,
                directory=self.directory,
                title=params.message,
                fields=fields,
                metadata=metadata,
            )
        except InteractionCancelledError:
            return types.ElicitResult(action="cancel")
        except asyncio.CancelledError:
            return types.ElicitResult(action="cancel")
        if isinstance(params, types.ElicitRequestURLParams):
            return types.ElicitResult(action="accept")
        content = {"value": answer.get("value")} if single else dict(answer)
        return types.ElicitResult(action="accept", content=content)

    async def get_text_input(self, context: NodeContext[Any], prompt: str) -> str:
        """Ask for free text through a single-field form."""
        answer = await self.forms.ask(
            session_id=self.session_id,
            directory=self.directory,
            title=prompt,
            fields=[FormStringField(key="value", required=True)],
        )
        return str(answer.get("value", ""))

    async def get_structured_input(
        self,
        context: NodeContext[Any],
        prompt: str,
        output_type: type[BaseModel],
    ) -> BaseModel:
        """Ask for structured input through a form built from the model's schema.

        Raises:
            ValueError: If the model schema can't be expressed as a form
        """
        fields = object_schema_to_fields(output_type.model_json_schema())
        if fields is None:
            msg = f"Cannot build a form for {output_type.__name__}"
            raise ValueError(msg)
        answer = await self.forms.ask(
            session_id=self.session_id, directory=self.directory, title=prompt, fields=fields
        )
        return output_type.model_validate(answer)


def _single(converted: FormField | None) -> list[FormField] | None:
    return [converted] if converted is not None else None


def _permission_resource(args: dict[str, Any]) -> str:
    """The most specific resource of a tool call (path, command, url, ...)."""
    for key in ("path", "file_path", "filePath", "command", "url", "pattern", "query"):
        value = args.get(key)
        if isinstance(value, str) and value:
            return value
    return "*"


def _json_safe(value: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = json.loads(json.dumps(value, default=str))
    return result
