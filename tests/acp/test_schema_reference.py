"""Tests for the pinned upstream schema references and the conformance check."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import MISSING, BaseModel, Field
import pytest

from acp.schema_reference import (
    Finding,
    OurSchema,
    UpstreamSchema,
    changelog,
    check,
    run_check,
    split_known,
    tag_sort_key,
)


@pytest.mark.parametrize("protocol_version", [1, 2])
def test_models_conform_to_pinned_reference(protocol_version: int) -> None:
    report = run_check(protocol_version)
    new = "\n".join(f"{f.id}: {f.detail}" for f in report.new)
    stale = "\n".join(report.stale)
    assert report.ok, f"undocumented differences:\n{new}\nstale known differences:\n{stale}"


# --- Synthetic schema exercising the checker -------------------------------------

META: dict[str, Any] = {"agentMethods": {"session_prompt": "session/prompt"}}


def _schema(**defs: Any) -> dict[str, Any]:
    return {"$defs": defs}


CHUNK: dict[str, Any] = {
    "type": "object",
    "properties": {"content": {"type": "string"}, "messageId": {"type": "string"}},
    "required": ["content"],
}
UPDATE: dict[str, Any] = {
    "oneOf": [
        {
            "type": "object",
            "properties": {"sessionUpdate": {"type": "string", "const": "agent_message_chunk"}},
            "required": ["sessionUpdate"],
            "allOf": [{"$ref": "#/$defs/ContentChunk"}],
        }
    ]
}
KIND: dict[str, Any] = {
    "anyOf": [{"type": "string", "const": "read"}, {"title": "other", "type": "string"}]
}
# Open enum, the way acp.schema models them: known values plus any string.
OpenKind = Literal["read"] | str  # noqa: PYI051


class AgentMessageChunk(BaseModel):
    """Model matching the ``agent_message_chunk`` variant exactly."""

    session_update: Literal["agent_message_chunk"] = Field(
        default="agent_message_chunk", alias="sessionUpdate"
    )
    content: str
    message_id: str | None = Field(default=None, alias="messageId")


def _ours(model: type[BaseModel], kind: object) -> OurSchema:
    return OurSchema(
        models={model.__name__: model},
        aliases={
            "SessionUpdate": model,
            "ToolKind": kind,
            "AgentMethod": Literal["session/prompt"],
        },
    )


def _ids(findings: list[Finding]) -> set[str]:
    return {f.id for f in findings}


def test_variants_match_through_union_discriminator() -> None:
    upstream = UpstreamSchema.parse(
        _schema(ContentChunk=CHUNK, SessionUpdate=UPDATE, ToolKind=KIND), META
    )

    findings = check(upstream, _ours(AgentMessageChunk, OpenKind), {})

    # ContentChunk is only an inlined base of the variant, so it needs no model.
    assert findings == []


def test_field_differences_are_reported() -> None:
    class Chunk(BaseModel):
        session_update: Literal["agent_message_chunk"] = Field(
            default="agent_message_chunk", alias="sessionUpdate"
        )
        content: str | None = None  # required upstream, may be left out
        extra: int = 0  # not upstream

    upstream = UpstreamSchema.parse(
        _schema(ContentChunk=CHUNK, SessionUpdate=UPDATE, ToolKind=KIND), META
    )

    ids = _ids(check(upstream, _ours(Chunk, OpenKind), {}))

    variant = "SessionUpdate[sessionUpdate=agent_message_chunk]"
    assert ids == {
        f"missing-field:{variant}.messageId",
        f"extra-field:{variant}.extra",
        f"omittable-field:{variant}.content",
    }


def test_optional_upstream_field_required_here_is_reported() -> None:
    class Chunk(BaseModel):
        session_update: Literal["agent_message_chunk"] = Field(
            default="agent_message_chunk", alias="sessionUpdate"
        )
        content: str
        message_id: str = Field(alias="messageId")

    upstream = UpstreamSchema.parse(
        _schema(ContentChunk=CHUNK, SessionUpdate=UPDATE, ToolKind=KIND), META
    )

    ids = _ids(check(upstream, _ours(Chunk, OpenKind), {}))

    assert ids == {"required-field:SessionUpdate[sessionUpdate=agent_message_chunk].messageId"}


def test_enum_and_method_differences_are_reported() -> None:
    upstream = UpstreamSchema.parse(
        _schema(ContentChunk=CHUNK, SessionUpdate=UPDATE, ToolKind=KIND),
        {"agentMethods": {"session_prompt": "session/prompt", "session_new": "session/new"}},
    )

    ids = _ids(check(upstream, _ours(AgentMessageChunk, Literal["read", "edit"]), {}))

    assert ids == {
        "extra-enum-value:ToolKind.edit",
        "closed-enum:ToolKind",
        "missing-method:AgentMethod.session/new",
    }


def test_missing_variant_and_renames() -> None:
    upstream = UpstreamSchema.parse(
        _schema(ContentChunk=CHUNK, SessionUpdate=UPDATE, ToolKind=KIND), META
    )
    ours = OurSchema(
        models={},
        aliases={"Kind": OpenKind, "AgentMethod": Literal["session/prompt"]},
    )

    ids = _ids(check(upstream, ours, {"ToolKind": "Kind"}))

    assert ids == {"missing-shape:SessionUpdate[sessionUpdate=agent_message_chunk]"}


def test_split_known_reports_new_and_stale() -> None:
    findings = [Finding("missing-field:A.b", ""), Finding("missing-field:A.c", "")]

    report = split_known(findings, {"missing-field:A.b": "known", "missing-field:A.gone": "fixed"})

    assert [f.id for f in report.new] == ["missing-field:A.c"]
    assert report.stale == {"missing-field:A.gone": "fixed"}
    assert not report.ok


def test_changelog_lists_changes() -> None:
    old = UpstreamSchema.parse(_schema(ContentChunk=CHUNK, ToolKind=KIND), META)
    chunk = {**CHUNK, "properties": {**CHUNK["properties"], "role": {"type": "string"}}}
    kind = {"anyOf": [*KIND["anyOf"], {"type": "string", "const": "edit"}]}
    new = UpstreamSchema.parse(
        _schema(ContentChunk=chunk, ToolKind=kind),
        {"agentMethods": {**META["agentMethods"], "session_new": "session/new"}},
    )

    assert changelog(old, new) == [
        "+ field ContentChunk.role",
        "+ enum value ToolKind.edit",
        "+ method agentMethods: session/new",
    ]


def test_tags_sort_by_version() -> None:
    tags = ["schema-v2.0.0", "schema-v2.0.0-alpha.17", "schema-v2.0.0-alpha.5", "schema-v1.33.0"]

    assert sorted(tags, key=tag_sort_key) == [
        "schema-v1.33.0",
        "schema-v2.0.0-alpha.5",
        "schema-v2.0.0-alpha.17",
        "schema-v2.0.0",
    ]


def test_nullability_differences_are_reported() -> None:
    nullable_chunk = {
        **CHUNK,
        "properties": {**CHUNK["properties"], "messageId": {"type": ["string", "null"]}},
    }

    class Chunk(BaseModel):
        session_update: Literal["agent_message_chunk"] = Field(
            default="agent_message_chunk", alias="sessionUpdate"
        )
        content: str | None  # upstream does not allow null
        message_id: str | MISSING = Field(default=MISSING, alias="messageId")  # rejects null

    upstream = UpstreamSchema.parse(
        _schema(ContentChunk=nullable_chunk, SessionUpdate=UPDATE, ToolKind=KIND), META
    )

    ids = _ids(check(upstream, _ours(Chunk, OpenKind), {}, check_nullability=True))

    variant = "SessionUpdate[sessionUpdate=agent_message_chunk]"
    assert ids == {f"sends-null:{variant}.content", f"rejects-null:{variant}.messageId"}


def test_constraint_differences_are_reported() -> None:
    limited_chunk = {
        **CHUNK,
        "properties": {**CHUNK["properties"], "content": {"type": "string", "minLength": 1}},
    }

    class Chunk(BaseModel):
        session_update: Literal["agent_message_chunk"] = Field(
            default="agent_message_chunk", alias="sessionUpdate"
        )
        content: str  # lacks min_length=1
        message_id: str | None = Field(default=None, alias="messageId", max_length=5)

    upstream = UpstreamSchema.parse(
        _schema(ContentChunk=limited_chunk, SessionUpdate=UPDATE, ToolKind=KIND), META
    )

    ids = _ids(check(upstream, _ours(Chunk, OpenKind), {}, check_constraints=True))

    variant = "SessionUpdate[sessionUpdate=agent_message_chunk]"
    assert ids == {f"constraint:{variant}.content", f"constraint:{variant}.messageId"}
