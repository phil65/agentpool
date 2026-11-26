"""Translate the v1 updates of ``ACPEventConverter`` into ACP v2 session updates.

The event converter holds the logic of showing agent events (streamed tool call
deltas, subagent display modes, background tasks, usage and cost, compaction,
retries). v2 reshapes its vocabulary rather than changing it, so the v2 output
runs the same converter and translates its updates here, instead of keeping a
second converter in step:

- message and thought chunks get the ``messageId`` v2 requires;
- ``tool_call`` and ``tool_call_update`` become one upsert, whose omitted fields
  stay unchanged (v1's ``None`` means "not sent", so it becomes ``MISSING``);
- the flat v1 plan becomes a v2 plan with a stable id;
- updates that already have v2's shape (notice, usage, compaction, plan, prompt
  suggestions, session info) are validated against the v2 schema as they are.

Modes, config options and command lists are not translated: the v2 output
builds them natively (v1's ``current_mode_update`` has no v2 counterpart).
"""

from __future__ import annotations

import difflib
from typing import TYPE_CHECKING, Any, assert_never
import uuid

from aclientp.schema import (
    AgentMessageChunk as V1AgentMessageChunk,
    AgentPlanUpdate as V1AgentPlanUpdate,
    AgentThoughtChunk as V1AgentThoughtChunk,
    AvailableCommandsUpdate as V1AvailableCommandsUpdate,
    BooleanSessionConfigOption as V1BooleanSessionConfigOption,
    ConfigOptionUpdate as V1ConfigOptionUpdate,
    ContentToolCallContent as V1ContentToolCallContent,
    FileEditToolCallContent as V1FileEditToolCallContent,
    SelectSessionConfigOption as V1SelectSessionConfigOption,
    SessionConfigSelectGroup as V1SessionConfigSelectGroup,
    SessionReferenceToolCallContent as V1SessionReferenceToolCallContent,
    TerminalToolCallContent as V1TerminalToolCallContent,
    ToolCallProgress as V1ToolCallProgress,
    ToolCallStart as V1ToolCallStart,
    UserMessageChunk as V1UserMessageChunk,
    v2,
)
from pydantic import MISSING, ValidationError

from agentpool.log import get_logger


if TYPE_CHECKING:
    from collections.abc import Sequence

    from aclientp.schema import (
        AvailableCommand as V1AvailableCommand,
        ContentBlock as V1ContentBlock,
        SessionConfigOption as V1SessionConfigOption,
        SessionConfigSelectOption as V1SessionConfigSelectOption,
        SessionUpdate as V1SessionUpdate,
        ToolCallContent as V1ToolCallContent,
        ToolCallLocation as V1ToolCallLocation,
        ToolCallUpdate as V1ToolCallUpdate,
    )


logger = get_logger(__name__)

PLAN_ID = "agent-plan"
"""Id of the agent's plan: v1 has a single plan per session, which every update replaces."""


def content_block(block: V1ContentBlock) -> v2.ContentBlock:
    """A v1 content block in v2 (the shapes match on the wire)."""
    wire = block.model_dump(by_alias=True, exclude_none=True)
    # Validated through the model holding v2's ContentBlock union
    return v2.Content.model_validate({"type": "content", "content": wire}).content


def _given[T](value: T | None) -> T | Any:
    """v1's ``None`` means "not sent"; on a v2 upsert that is ``MISSING`` (unchanged)."""
    return MISSING if value is None else value


def git_patch(path: str, old_text: str | None, new_text: str) -> str:
    """A ``diff --git`` section for one file, as v2's ``git_patch`` format expects."""
    rel = path.lstrip("/")
    old_lines = (old_text or "").splitlines(keepends=True)
    new_lines = new_text.splitlines(keepends=True)
    header = [f"diff --git a/{rel} b/{rel}\n"]
    if old_text is None:
        header.append("new file mode 100644\n")
    body = difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile="/dev/null" if old_text is None else f"a/{rel}",
        tofile=f"b/{rel}",
    )
    return "".join([*header, *body])


def tool_call_content(item: V1ToolCallContent) -> v2.ToolCallContent:
    """One item of a v1 tool call's content in v2."""
    match item:
        case V1ContentToolCallContent(content=block):
            return v2.Content(content=content_block(block))
        case V1FileEditToolCallContent(path=path, old_text=old_text, new_text=new_text):
            change: v2.DiffChange = (
                v2.AddDiffChange(path=path) if old_text is None else v2.ModifyDiffChange(path=path)
            )
            patch = v2.DiffPatch(format="git_patch", text=git_patch(path, old_text, new_text))
            return v2.Diff(changes=[change], patch=patch)
        case V1TerminalToolCallContent(terminal_id=terminal_id):
            return v2.Terminal(terminal_id=terminal_id)
        case V1SessionReferenceToolCallContent(session_id=session_id):
            return v2.SessionReference(session_id=session_id)
        case _ as unreachable:
            assert_never(unreachable)


def _locations(locations: Sequence[V1ToolCallLocation] | None) -> list[v2.ToolCallLocation] | Any:
    if locations is None:
        return MISSING
    # v2 lines start at 1; v1 senders use 0 for "no particular line"
    return [
        v2.ToolCallLocation(path=loc.path, line=loc.line if loc.line and loc.line >= 1 else MISSING)
        for loc in locations
    ]


def tool_call_update[T: v2.ToolCallUpdate](
    update: V1ToolCallStart | V1ToolCallProgress | V1ToolCallUpdate, kind: type[T]
) -> T:
    """A v1 tool call as v2's tool call upsert.

    Args:
        update: A v1 tool call start or progress, or a permission request's tool call
        kind: ``v2.ToolCallSessionUpdate`` for a session update, ``v2.ToolCallUpdate``
            for a permission request's subject
    """
    content = MISSING if update.content is None else [tool_call_content(c) for c in update.content]
    return kind(
        tool_call_id=update.tool_call_id,
        name=_given(update.name),
        title=_given(update.title),
        kind=_given(update.kind),
        status=_given(update.status),
        content=content,
        locations=_locations(update.locations),
        raw_input=_given(update.raw_input),
        raw_output=_given(update.raw_output),
        field_meta=_given(update.field_meta),
    )


def _select_option(option: V1SessionConfigSelectOption) -> v2.SessionConfigSelectOption:
    return v2.SessionConfigSelectOption(
        value=option.value, name=option.name, description=_given(option.description)
    )


def config_option(option: V1SessionConfigOption) -> v2.SessionConfigOption:
    """A v1 config option in v2 (``id`` became ``configId``, a group's ``group`` ``groupId``)."""
    match option:
        case V1SelectSessionConfigOption(options=choices):
            converted: list[v2.SessionConfigSelectOption] | list[v2.SessionConfigSelectGroup] = (
                [
                    v2.SessionConfigSelectGroup(
                        group_id=c.group,
                        name=c.name,
                        options=[_select_option(o) for o in c.options],
                    )
                    for c in choices
                    if isinstance(c, V1SessionConfigSelectGroup)
                ]
                if choices and isinstance(choices[0], V1SessionConfigSelectGroup)
                else [
                    _select_option(c)
                    for c in choices
                    if not isinstance(c, V1SessionConfigSelectGroup)
                ]
            )
            return v2.SelectSessionConfigOption(
                config_id=option.id,
                name=option.name,
                description=_given(option.description),
                category=_given(option.category),
                current_value=option.current_value,
                options=converted,
            )
        case V1BooleanSessionConfigOption():
            return v2.BooleanSessionConfigOption(
                config_id=option.id,
                name=option.name,
                description=_given(option.description),
                category=_given(option.category),
                current_value=option.current_value,
            )
        case _ as unreachable:
            assert_never(unreachable)


def command(cmd: V1AvailableCommand) -> v2.Command:
    """A v1 slash command in v2 (a ``command`` with a ``text`` input hint)."""
    hint = cmd.input.root.hint if cmd.input is not None else None
    return v2.Command(
        name=cmd.name,
        description=cmd.description,
        input=MISSING if hint is None else v2.TextCommandInput(hint=hint),
    )


class UpdateTranslator:
    """Translates the v1 updates of one converter into v2 updates.

    Chunks that carry no message id (v2 requires one) get a fallback: per session, kind of
    chunk and turn (:meth:`new_turn`), so one session's turns and other sessions' chunks
    don't share it.

    Args:
        message_id: Fallback for chunks translated without a session (e.g. a child
            session's translator)
    """

    def __init__(self, message_id: str | None = None) -> None:
        self.message_id = message_id or str(uuid.uuid4())
        self._fallbacks: dict[tuple[str, str], str] = {}

    def fallback_id(self, session_id: str | None, kind: str) -> str:
        """The message id of ``session_id``'s ``kind`` chunks without one in this turn."""
        if session_id is None:
            return self.message_id
        return self._fallbacks.setdefault((session_id, kind), str(uuid.uuid4()))

    def new_turn(self, session_id: str) -> None:
        """A turn of ``session_id`` starts: its chunks without an id get new fallbacks."""
        for key in [k for k in self._fallbacks if k[0] == session_id]:
            del self._fallbacks[key]

    def translate(  # noqa: PLR0911
        self, update: V1SessionUpdate, session_id: str | None = None
    ) -> v2.SessionUpdate | None:
        """The v2 form of a v1 update of ``session_id``, or None when it has none."""
        match update:
            case V1AgentMessageChunk(content=block, message_id=message_id):
                return v2.AgentMessageChunk(
                    message_id=message_id or self.fallback_id(session_id, "agent"),
                    content=content_block(block),
                )
            case V1AgentThoughtChunk(content=block, message_id=message_id):
                return v2.AgentThoughtChunk(
                    message_id=message_id or self.fallback_id(session_id, "thought"),
                    content=content_block(block),
                )
            case V1UserMessageChunk(content=block, message_id=message_id):
                return v2.UserMessageChunk(
                    message_id=message_id or self.fallback_id(session_id, "user"),
                    content=content_block(block),
                )
            case V1ToolCallStart() | V1ToolCallProgress():
                return tool_call_update(update, v2.ToolCallSessionUpdate)
            case V1ConfigOptionUpdate(config_options=options):
                return v2.ConfigOptionUpdate(config_options=[config_option(o) for o in options])
            case V1AvailableCommandsUpdate(available_commands=commands):
                return v2.AvailableCommandsUpdate(available_commands=[command(c) for c in commands])
            case V1AgentPlanUpdate(entries=entries):
                items = [
                    v2.PlanEntry(content=e.content, priority=e.priority, status=e.status)
                    for e in entries
                ]
                return v2.PlanUpdate(plan=v2.PlanItems(plan_id=PLAN_ID, entries=items))
            case _:
                return self._as_is(update)

    def _as_is(self, update: V1SessionUpdate) -> v2.SessionUpdate | None:
        """Updates whose v1 shape is already v2's."""
        wire = update.model_dump(by_alias=True, exclude_none=True)
        try:
            # Validated through the model holding v2's SessionUpdate union
            return v2.UpdateSessionNotification.model_validate({
                "sessionId": "-",
                "update": wire,
            }).update
        except ValidationError:
            logger.warning("No v2 form for update", update_type=type(update).__name__)
            return None
