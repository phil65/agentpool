"""Pinned upstream ACP schema references: syncing and conformance checks.

The hand-written models in :mod:`acp.schema` follow the upstream ACP JSON schema
(``agentclientprotocol/agent-client-protocol``). Instead of generating code, the
repository pins one upstream schema release per protocol version and checks the
models against it:

- ``schema/acp/v{N}/VERSION``: upstream git tag, e.g. ``schema-v1.33.0``
- ``schema/acp/v{N}/schema.unstable.json`` and ``meta.unstable.json``: the
  vendored upstream files of that tag (the unstable schema is a superset of the
  stable one)
- ``schema/acp/v{N}/conformance.toml``: renames and known, documented differences

Commands (``uv run python -m acp.schema_reference <command>``):

- ``latest``: show the pinned and the newest upstream tag
- ``sync [--tag TAG] [--dry-run]``: download a tag (default: newest), print the
  schema changelog against the pinned one, and update the pin
- ``check``: compare the models with the pinned schema
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field, replace
import importlib
import inspect
import json
import pkgutil
import sys
import tomllib
import types
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    Literal,
    TypeAliasType,
    TypeGuard,
    Union,
    get_args,
    get_origin,
)

from pydantic import MISSING, BaseModel
from upathtools import UPath


if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping

    from pydantic.fields import FieldInfo as PydanticFieldInfo


REPO = "agentclientprotocol/agent-client-protocol"
REFERENCE_ROOT = UPath(__file__).resolve().parents[2] / "schema" / "acp"
REFERENCE_FILES = ("schema.unstable.json", "meta.unstable.json")
MODEL_PACKAGES: dict[int, str] = {1: "acp.schema", 2: "acp.schema.v2"}
"""Package holding the hand-written models of each protocol version."""

METHOD_GROUPS = {
    "agentMethods": "AgentMethod",
    "clientMethods": "ClientMethod",
    "protocolMethods": "ProtocolMethod",
    "proxyMethods": "ProxyMethod",
}
"""``meta.unstable.json`` method groups and the ``Literal`` aliases mirroring them."""

type JSON = dict[str, Any]


# ---------------------------------------------------------------------------
# Upstream schema
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Shape:
    """An object shape of the upstream schema: a named definition or a union variant."""

    key: str
    """``Name`` for a definition, ``Union[prop=value]`` for a tagged union variant."""

    properties: frozenset[str]
    """Wire names of all properties, including inherited (``allOf``) ones."""

    required: frozenset[str]
    """Wire names of required properties."""

    union: str | None = None
    """Name of the union a variant belongs to."""

    discriminator: tuple[str, str] | None = None
    """``(property, value)`` that selects a variant."""

    bases: frozenset[str] = frozenset()
    """Definitions this shape inherits via ``allOf``."""

    nullable: frozenset[str] = frozenset()
    """Wire names of properties that accept ``null``."""

    constraints: Mapping[str, Mapping[str, float]] = field(default_factory=dict)
    """Numeric and length constraints per property, as pydantic names (``ge``, ...)."""

    branch_of: str | None = None
    """For a variant made from a tagged branch inside an object definition: that
    definition. Our side models such an object either as one model of the same name,
    or as a union of the same name whose members are the branches."""


@dataclass(frozen=True)
class EnumDef:
    """A string enum of the upstream schema."""

    name: str
    values: frozenset[str]
    open: bool
    """Whether other strings are allowed too."""


@dataclass(frozen=True)
class UpstreamSchema:
    """The parts of an upstream schema the checks compare."""

    shapes: dict[str, Shape]
    enums: dict[str, EnumDef]
    union_members: dict[str, frozenset[str]]
    """Union name to the keys of its members (variants and referenced definitions)."""

    methods: dict[str, frozenset[str]]
    """``meta.unstable.json`` method group to method names."""

    @classmethod
    def parse(cls, schema: JSON, meta: JSON) -> UpstreamSchema:
        """Extract shapes, enums, unions and methods from the upstream files."""
        defs: dict[str, JSON] = schema["$defs"]
        shapes: dict[str, Shape] = {}
        enums: dict[str, EnumDef] = {}
        unions: dict[str, frozenset[str]] = {}
        for name, node in defs.items():
            variants = node.get("oneOf") or node.get("anyOf")
            if variants is not None and "properties" not in node:
                if enum := _parse_enum(name, variants):
                    enums[name] = enum
                    continue
                members: set[str] = set()
                for variant in variants:
                    if ref := variant.get("$ref"):
                        members.add(_ref_name(ref))
                    elif shape := _parse_variant(name, variant, defs):
                        shapes[shape.key] = shape
                        members.add(shape.key)
                    elif (parts := variant.get("allOf")) and len(parts) == 1 and "$ref" in parts[0]:
                        # Untagged variant, e.g. ``{"title": "stdio", "allOf": [{"$ref": ...}]}``
                        members.add(_ref_name(parts[0]["$ref"]))
                unions[name] = frozenset(members)
            elif "enum" in node:
                enums[name] = EnumDef(name, frozenset(node["enum"]), open=False)
            elif _is_object(node, defs):
                collected = _collect(node, defs)
                shapes[name] = Shape(
                    name,
                    collected.properties,
                    collected.required,
                    bases=collected.bases,
                    nullable=collected.nullable,
                    constraints=collected.constraints,
                )
                branches = [
                    branch
                    for part in node.get("oneOf", []) + node.get("anyOf", [])
                    if (branch := _parse_branch(name, node, part, defs))
                ]
                for branch in branches:
                    shapes[branch.key] = branch
                if branches:
                    unions[name] = frozenset(branch.key for branch in branches)
        methods = {
            group: frozenset(meta[group].values()) for group in METHOD_GROUPS if group in meta
        }
        return cls(shapes, enums, unions, methods)

    @property
    def inlined_bases(self) -> frozenset[str]:
        """Definitions that only serve as ``allOf`` bases of union variants."""
        used_as_base = {
            base for shape in self.shapes.values() if shape.union for base in shape.bases
        }
        referenced = {member for members in self.union_members.values() for member in members}
        return frozenset(used_as_base - referenced)


def _ref_name(ref: str) -> str:
    return ref.rsplit("/", 1)[-1]


def _parse_enum(name: str, variants: list[JSON]) -> EnumDef | None:
    values: set[str] = set()
    is_open = False
    for variant in variants:
        if isinstance(const := variant.get("const"), str):
            values.add(const)
        elif variant.get("type") == "string" and "properties" not in variant:
            is_open = True
        else:
            return None
    return EnumDef(name, frozenset(values), is_open) if values else None


def _is_object(node: JSON, defs: Mapping[str, JSON]) -> bool:
    if node.get("type") == "object" or "properties" in node:
        return True
    return any(
        _is_object(defs[_ref_name(part["$ref"])] if "$ref" in part else part, defs)
        for part in node.get("allOf", [])
    )


@dataclass(frozen=True)
class _Collected:
    properties: frozenset[str]
    required: frozenset[str]
    bases: frozenset[str]
    nullable: frozenset[str]
    constraints: dict[str, dict[str, float]]


def _collect(node: JSON, defs: Mapping[str, JSON]) -> _Collected:
    """Properties, required and nullable properties and ``allOf`` bases of an object.

    Properties of ``anyOf`` / ``oneOf`` branches inside an object count as
    optional properties of the object.
    """
    own: JSON = node.get("properties", {})
    props = set(own)
    required = set(node.get("required", []))
    nullable = {prop for prop, schema in own.items() if _accepts_null(schema, defs)}
    constraints = {prop: c for prop, schema in own.items() if (c := _constraints(schema))}
    bases: set[str] = set()
    for part in node.get("allOf", []):
        if ref := part.get("$ref"):
            bases.add(_ref_name(ref))
            part = defs[_ref_name(ref)]  # noqa: PLW2901
        sub = _collect(part, defs)
        props |= sub.properties
        required |= sub.required
        bases |= sub.bases
        nullable |= sub.nullable
        constraints = {**sub.constraints, **constraints}
    branch_required: list[frozenset[str]] = []
    for part in node.get("anyOf", []) + node.get("oneOf", []):
        resolved = defs[_ref_name(part["$ref"])] if "$ref" in part else part
        sub = _collect(resolved, defs)
        props |= sub.properties
        bases |= sub.bases
        nullable |= sub.nullable
        constraints = {**sub.constraints, **constraints}
        branch_required.append(sub.required)
    if branch_required:
        # Required in every branch means required for the object.
        required |= frozenset.intersection(*branch_required)
    return _Collected(
        frozenset(props), frozenset(required), frozenset(bases), frozenset(nullable), constraints
    )


CONSTRAINT_KEYS = {
    "minimum": "ge",
    "maximum": "le",
    "exclusiveMinimum": "gt",
    "exclusiveMaximum": "lt",
    "minLength": "min_length",
    "maxLength": "max_length",
    "minItems": "min_length",
    "maxItems": "max_length",
}
"""JSON schema keywords and the pydantic constraints they correspond to."""


def _constraints(schema: JSON) -> dict[str, float]:
    """Numeric and length constraints of a property schema (also inside ``anyOf: [x, null]``)."""
    found = {CONSTRAINT_KEYS[k]: float(v) for k, v in schema.items() if k in CONSTRAINT_KEYS}
    for key in ("anyOf", "oneOf"):
        parts = [part for part in schema.get(key, []) if part.get("type") != "null"]
        if len(parts) == 1:
            found |= _constraints(parts[0])
    return found


_TYPED_KEYWORDS = ("$ref", "anyOf", "oneOf", "allOf", "const", "enum", "properties")
"""Keywords that constrain a schema without a ``type``."""


def _accepts_null(schema: JSON, defs: Mapping[str, JSON], depth: int = 0) -> bool:
    """Whether a property schema accepts ``null``."""
    kind = schema.get("type")
    if kind == "null" or (isinstance(kind, list) and "null" in kind):
        return True
    if kind is None and not any(k in schema for k in _TYPED_KEYWORDS):
        return True  # no type constraint: any JSON value, including null
    if depth > 8:  # noqa: PLR2004
        return False
    if ref := schema.get("$ref"):
        return _accepts_null(defs[_ref_name(ref)], defs, depth + 1)
    branches = schema.get("anyOf", []) + schema.get("oneOf", [])
    if any(_accepts_null(part, defs, depth + 1) for part in branches):
        return True
    parts = schema.get("allOf", [])
    return len(parts) == 1 and _accepts_null(parts[0], defs, depth + 1)


def _parse_variant(union: str, variant: JSON, defs: Mapping[str, JSON]) -> Shape | None:
    tag = next(
        (
            (prop, schema["const"])
            for prop, schema in variant.get("properties", {}).items()
            if isinstance(schema.get("const"), str)
        ),
        None,
    )
    if tag is None:
        return None
    collected = _collect(variant, defs)
    return Shape(
        f"{union}[{tag[0]}={tag[1]}]",
        collected.properties,
        collected.required,
        union,
        tag,
        collected.bases,
        collected.nullable,
        collected.constraints,
    )


def _parse_branch(name: str, node: JSON, branch: JSON, defs: Mapping[str, JSON]) -> Shape | None:
    """A tagged ``oneOf`` / ``anyOf`` branch of an object, merged with the object."""
    merged: JSON = {
        "properties": {**node.get("properties", {}), **branch.get("properties", {})},
        "required": [*node.get("required", []), *branch.get("required", [])],
        "allOf": [*node.get("allOf", []), *branch.get("allOf", [])],
    }
    shape = _parse_variant(name, merged, defs)
    return None if shape is None else replace(shape, branch_of=name)


# ---------------------------------------------------------------------------
# Hand-written models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OurSchema:
    """Models and module-level aliases (unions, ``Literal`` enums) of a model package."""

    models: dict[str, type[BaseModel]]
    aliases: dict[str, object]

    @classmethod
    def load(cls, package_name: str) -> OurSchema:
        """Collect models and aliases from the direct modules of ``package_name``."""
        package = importlib.import_module(package_name)
        models: dict[str, type[BaseModel]] = {}
        aliases: dict[str, object] = {}
        for info in pkgutil.iter_modules(package.__path__):
            if info.ispkg:
                continue
            module = importlib.import_module(f"{package_name}.{info.name}")
            for name, obj in vars(module).items():
                if inspect.isclass(obj):
                    if issubclass(obj, BaseModel) and obj.__module__ == module.__name__:
                        models[name] = obj
                elif not name.startswith("_") and _is_type_alias(obj):
                    aliases.setdefault(name, obj)
        return cls(models, aliases)

    def union_members(self, name: str) -> list[type[BaseModel]]:
        """Models of the union alias ``name`` (empty if there is none)."""
        alias = self.aliases.get(name)
        return [] if alias is None else [m for m in _flatten(alias) if _is_model(m)]

    def literal_values(self, name: str) -> tuple[frozenset[str], bool] | None:
        """Values of the ``Literal`` alias ``name``, and whether it also allows ``str``."""
        alias = self.aliases.get(name)
        if alias is None:
            return None
        members = list(_flatten(alias))
        values = {arg for m in members if get_origin(m) is Literal for arg in get_args(m)}
        if not values:
            return None
        return frozenset(str(v) for v in values), str in members


def _is_model(obj: object) -> TypeGuard[type[BaseModel]]:
    return inspect.isclass(obj) and issubclass(obj, BaseModel)


def _is_type_alias(obj: object) -> bool:
    if isinstance(obj, types.UnionType | TypeAliasType):
        return True
    return get_origin(obj) in (Literal, Annotated, Union)


def _flatten(tp: object) -> Iterator[object]:
    """Members of a (possibly annotated or aliased) union; ``Literal`` stays whole."""
    if isinstance(tp, TypeAliasType):
        yield from _flatten(tp.__value__)
        return
    origin = get_origin(tp)
    if origin is Annotated:
        yield from _flatten(get_args(tp)[0])
    elif origin is Union or isinstance(tp, types.UnionType):
        for arg in get_args(tp):
            yield from _flatten(arg)
    else:
        yield tp


@dataclass(frozen=True)
class FieldInfo:
    """How a model field behaves on the wire."""

    required: bool
    """The field must be present when parsing."""

    omittable: bool
    """The field can be left out when serializing: optional with a ``None`` or
    ``MISSING`` default."""

    accepts_none: bool
    """The annotation allows ``None`` (sent as ``null`` unless excluded)."""

    constraints: dict[str, float]
    """Numeric and length constraints, e.g. ``{"ge": 1}``."""


def wire_fields(model: type[BaseModel]) -> dict[str, FieldInfo]:
    """Wire name and behaviour of each serialized field of ``model``."""
    return {
        (info.alias or name): FieldInfo(
            required=info.is_required(),
            omittable=not info.is_required()
            and (info.default is None or info.default is MISSING)
            and info.default_factory is None,
            accepts_none=any(member in (type(None), Any) for member in _flatten(info.annotation)),
            constraints=_field_constraints(info),
        )
        for name, info in model.model_fields.items()
        if not info.exclude
    }


def _field_constraints(info: PydanticFieldInfo) -> dict[str, float]:
    found: dict[str, float] = {}
    for item in info.metadata:
        for name in ("ge", "le", "gt", "lt", "min_length", "max_length"):
            value = getattr(item, name, None)
            if isinstance(value, int | float):
                found[name] = float(value)
    return found


def _discriminator_value(model: type[BaseModel], prop: str) -> str | None:
    for name, info in model.model_fields.items():
        if (info.alias or name) == prop and isinstance(info.default, str):
            return info.default
    return None


# ---------------------------------------------------------------------------
# Conformance
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Conformance:
    """``conformance.toml``: renames and known differences of a pinned reference."""

    renames: dict[str, str] = field(default_factory=dict)
    """Upstream definition name to our model name."""

    known: dict[str, str] = field(default_factory=dict)
    """Finding id to the reason the difference is accepted."""

    check_nullability: bool = False
    """Compare which fields accept ``null``. For model packages that send ``None`` as
    ``null`` and leave out ``MISSING`` fields (v2), not for ``exclude_none`` ones (v1)."""

    check_constraints: bool = False
    """Compare numeric and length constraints (``minimum``, ``minItems``, ...)."""

    allow_missing: bool = False
    """Accept upstream shapes, enums and methods without a counterpart yet, while a
    model package is being written. Fields of the shapes that exist are still checked."""

    @classmethod
    def load(cls, path: UPath) -> Conformance:
        """Read ``conformance.toml``; a missing file means no renames or differences."""
        if not path.exists():
            return cls()
        data = tomllib.loads(path.read_text("utf-8"))
        return cls(
            dict(data.get("renames", {})),
            dict(data.get("known", {})),
            bool(data.get("check_nullability", False)),
            bool(data.get("check_constraints", False)),
            bool(data.get("allow_missing", False)),
        )


@dataclass(frozen=True)
class Finding:
    """One difference between the models and the reference."""

    id: str
    """Stable id, e.g. ``missing-field:ToolCallUpdate.name``; key in ``conformance.toml``."""

    detail: str


def check(
    upstream: UpstreamSchema,
    ours: OurSchema,
    renames: Mapping[str, str],
    *,
    check_nullability: bool = False,
    check_constraints: bool = False,
) -> list[Finding]:
    """Compare the models with the upstream schema.

    ``renames`` maps upstream definition names (shapes, unions, enums) to ours.
    """
    findings: list[Finding] = []
    matched = _match_shapes(upstream, ours, renames)
    inlined = upstream.inlined_bases
    # Discriminators that variants add to the definitions they inherit from.
    added_tags: dict[str, set[str]] = {}
    for shape in upstream.shapes.values():
        if shape.discriminator:
            for base in shape.bases:
                added_tags.setdefault(base, set()).add(shape.discriminator[0])
    for key, shape in sorted(upstream.shapes.items()):
        if shape.branch_of and shape.branch_of in matched:
            continue  # the object is one model on our side, compared as a whole
        model = matched.get(key)
        if model is None:
            if key not in inlined and key not in upstream.union_members:
                findings.append(Finding(f"missing-shape:{key}", "no matching model"))
            continue
        tags = added_tags.get(key, set())
        findings.extend(
            _compare_fields(key, shape, model, tags, check_nullability, check_constraints)
        )
    findings.extend(_check_enums(upstream, ours, renames))
    findings.extend(_check_methods(upstream, ours))
    return findings


def _check_enums(
    upstream: UpstreamSchema, ours: OurSchema, renames: Mapping[str, str]
) -> Iterator[Finding]:
    for name, enum in sorted(upstream.enums.items()):
        values = ours.literal_values(renames.get(name, name))
        if values is None:
            yield Finding(f"missing-enum:{name}", "no Literal alias of this name")
            continue
        our_values, our_open = values
        for value in sorted(enum.values - our_values):
            yield Finding(f"missing-enum-value:{name}.{value}", "upstream value not in Literal")
        for value in sorted(our_values - enum.values):
            yield Finding(f"extra-enum-value:{name}.{value}", "Literal value not upstream")
        if enum.open and not our_open:
            yield Finding(
                f"closed-enum:{name}", "upstream allows other strings, the Literal does not"
            )


def _check_methods(upstream: UpstreamSchema, ours: OurSchema) -> Iterator[Finding]:
    for group, alias in METHOD_GROUPS.items():
        upstream_methods = upstream.methods.get(group, frozenset())
        values = ours.literal_values(alias)
        our_methods = values[0] if values else frozenset()
        for method in sorted(upstream_methods - our_methods):
            yield Finding(f"missing-method:{alias}.{method}", "upstream method not in Literal")
        for method in sorted(our_methods - upstream_methods):
            yield Finding(f"extra-method:{alias}.{method}", "method not upstream")


def _match_shapes(
    upstream: UpstreamSchema, ours: OurSchema, renames: Mapping[str, str]
) -> dict[str, type[BaseModel]]:
    matched: dict[str, type[BaseModel]] = {}
    for key, shape in upstream.shapes.items():
        if model := ours.models.get(renames.get(key, key)):
            matched[key] = model
        elif shape.union and shape.discriminator:
            prop, value = shape.discriminator
            union = renames.get(shape.union, shape.union)
            # Without our union, only models named like an upstream base of the
            # variant qualify: tags such as `type: "text"` recur across unions.
            candidates = ours.union_members(union) or [
                ours.models[base] for base in shape.bases if base in ours.models
            ]
            hits = [m for m in candidates if _discriminator_value(m, prop) == value]
            if len(hits) > 1:
                # Same tag in several unions: prefer the model whose fields fit best.
                hits.sort(key=lambda m: len(shape.properties ^ set(wire_fields(m))))
            if hits:
                matched[key] = hits[0]
    return matched


def _compare_fields(
    key: str,
    shape: Shape,
    model: type[BaseModel],
    added_tags: set[str],
    check_nullability: bool,
    check_constraints: bool = False,
) -> Iterator[Finding]:
    fields = wire_fields(model)
    tag = shape.discriminator[0] if shape.discriminator else None
    where = f"{model.__name__} ({key})" if model.__name__ != key else key
    for prop in sorted(shape.properties - set(fields)):
        yield Finding(f"missing-field:{key}.{prop}", f"{where} lacks it")
    for prop in sorted(set(fields) - shape.properties - added_tags):
        yield Finding(f"extra-field:{key}.{prop}", f"{where} has it, upstream does not")
    for prop in sorted(shape.properties & set(fields)):
        if prop == tag:
            continue
        info = fields[prop]
        if prop in shape.required and info.omittable:
            yield Finding(
                f"omittable-field:{key}.{prop}",
                f"required upstream, but {where} may leave it out (None default)",
            )
        elif info.required and prop not in shape.required:
            yield Finding(
                f"required-field:{key}.{prop}",
                f"optional upstream, required in {where}: valid messages fail to parse",
            )
        if check_constraints and dict(shape.constraints.get(prop, {})) != info.constraints:
            yield Finding(
                f"constraint:{key}.{prop}",
                f"upstream {dict(shape.constraints.get(prop, {}))}, {where} {info.constraints}",
            )
        if not check_nullability:
            continue
        if prop in shape.nullable and not info.accepts_none:
            yield Finding(
                f"rejects-null:{key}.{prop}",
                f"upstream allows null, {where} does not: valid messages fail to parse",
            )
        elif info.accepts_none and prop not in shape.nullable:
            yield Finding(
                f"sends-null:{key}.{prop}",
                f"{where} accepts None and would send null, upstream does not allow it",
            )


@dataclass(frozen=True)
class Report:
    """Findings of a check, split against the known differences."""

    new: list[Finding]
    """Differences not listed in ``conformance.toml``."""

    stale: dict[str, str]
    """Listed differences that no longer occur."""

    @property
    def ok(self) -> bool:
        return not self.new and not self.stale


def run_check(protocol_version: int, root: UPath = REFERENCE_ROOT) -> Report:
    """Check the models of ``protocol_version`` against its pinned reference."""
    reference = Reference.load(protocol_version, root)
    conformance = Conformance.load(reference.directory / "conformance.toml")
    ours = OurSchema.load(MODEL_PACKAGES[protocol_version])
    findings = check(
        reference.parsed,
        ours,
        conformance.renames,
        check_nullability=conformance.check_nullability,
        check_constraints=conformance.check_constraints,
    )
    if conformance.allow_missing:
        findings = [f for f in findings if not f.id.startswith(MISSING_KINDS)]
    return split_known(findings, conformance.known)


MISSING_KINDS = ("missing-shape:", "missing-enum:", "missing-method:")
"""Finding kinds that ``allow_missing`` accepts."""


def split_known(findings: Iterable[Finding], known: Mapping[str, str]) -> Report:
    """Split findings into undocumented ones, and find stale known differences."""
    findings = list(findings)
    ids = {f.id for f in findings}
    return Report(
        new=[f for f in findings if f.id not in known],
        stale={k: v for k, v in known.items() if k not in ids},
    )


# ---------------------------------------------------------------------------
# Pinned references and syncing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Reference:
    """A pinned upstream schema release."""

    protocol_version: int
    tag: str
    schema: JSON
    meta: JSON
    directory: UPath

    @classmethod
    def load(cls, protocol_version: int, root: UPath = REFERENCE_ROOT) -> Reference:
        """Load the pinned reference of ``protocol_version``."""
        directory = root / f"v{protocol_version}"
        tag = (directory / "VERSION").read_text("utf-8").strip()
        schema = json.loads((directory / "schema.unstable.json").read_text("utf-8"))
        meta = json.loads((directory / "meta.unstable.json").read_text("utf-8"))
        return cls(protocol_version, tag, schema, meta, directory)

    @property
    def parsed(self) -> UpstreamSchema:
        return UpstreamSchema.parse(self.schema, self.meta)


def upstream_tags(protocol_version: int) -> list[str]:
    """Upstream schema tags of ``protocol_version``, oldest first."""
    import httpx

    url = f"https://api.github.com/repos/{REPO}/git/matching-refs/tags/schema-v{protocol_version}."
    response = httpx.get(url, headers={"Accept": "application/vnd.github+json"}, timeout=30)
    response.raise_for_status()
    tags = [ref["ref"].removeprefix("refs/tags/") for ref in response.json()]
    return sorted(tags, key=tag_sort_key)


def tag_sort_key(tag: str) -> tuple[tuple[int, ...], int, str, int]:
    """Sort key of an upstream schema tag such as ``schema-v2.0.0-alpha.17``.

    A release sorts after its pre-releases (``2.0.0-alpha.17`` < ``2.0.0``).
    """
    release, _, pre = tag.removeprefix("schema-v").partition("-")
    numbers = tuple(int(part) for part in release.split("."))
    if not pre:
        return numbers, 1, "", 0
    label, _, number = pre.partition(".")
    return numbers, 0, label, int(number or 0)


def download(protocol_version: int, tag: str) -> dict[str, str]:
    """Download the reference files of ``tag``, verbatim."""
    import httpx

    base = f"https://raw.githubusercontent.com/{REPO}/{tag}/schema/v{protocol_version}"
    files: dict[str, str] = {}
    with httpx.Client(timeout=60) as client:
        for name in REFERENCE_FILES:
            response = client.get(f"{base}/{name}")
            response.raise_for_status()
            files[name] = response.text
    return files


def parse_files(files: Mapping[str, str]) -> UpstreamSchema:
    """Parse downloaded reference files."""
    return UpstreamSchema.parse(
        json.loads(files["schema.unstable.json"]), json.loads(files["meta.unstable.json"])
    )


def write_reference(protocol_version: int, tag: str, files: Mapping[str, str], root: UPath) -> None:
    """Store downloaded reference files, unchanged, as the pin of ``protocol_version``.

    Keeping upstream's bytes means a ``git diff`` between pins is upstream's diff.
    """
    directory = root / f"v{protocol_version}"
    directory.mkdir(parents=True, exist_ok=True)
    for name in REFERENCE_FILES:
        (directory / name).write_text(files[name], encoding="utf-8")
    (directory / "VERSION").write_text(tag + "\n", encoding="utf-8")


def changelog(old: UpstreamSchema, new: UpstreamSchema) -> list[str]:
    """Human-readable changes between two upstream schemas."""
    lines: list[str] = []
    lines.extend(_diff_keys("shape", old.shapes, new.shapes))
    for key in sorted(old.shapes.keys() & new.shapes.keys()):
        before, after = old.shapes[key], new.shapes[key]
        lines.extend(f"+ field {key}.{p}" for p in sorted(after.properties - before.properties))
        lines.extend(f"- field {key}.{p}" for p in sorted(before.properties - after.properties))
        common = before.properties & after.properties
        lines.extend(
            f"~ field {key}.{p} is now required"
            for p in sorted((after.required - before.required) & common)
        )
        lines.extend(
            f"~ field {key}.{p} is now optional"
            for p in sorted((before.required - after.required) & common)
        )
    lines.extend(_diff_keys("enum", old.enums, new.enums))
    for name in sorted(old.enums.keys() & new.enums.keys()):
        before_values, after_values = old.enums[name].values, new.enums[name].values
        lines.extend(f"+ enum value {name}.{v}" for v in sorted(after_values - before_values))
        lines.extend(f"- enum value {name}.{v}" for v in sorted(before_values - after_values))
    lines.extend(_diff_keys("union", old.union_members, new.union_members))
    for name in sorted(old.union_members.keys() & new.union_members.keys()):
        before_members, after_members = old.union_members[name], new.union_members[name]
        lines.extend(f"+ union member {name}: {m}" for m in sorted(after_members - before_members))
        lines.extend(f"- union member {name}: {m}" for m in sorted(before_members - after_members))
    for group in METHOD_GROUPS:
        before_methods = old.methods.get(group, frozenset())
        after_methods = new.methods.get(group, frozenset())
        lines.extend(f"+ method {group}: {m}" for m in sorted(after_methods - before_methods))
        lines.extend(f"- method {group}: {m}" for m in sorted(before_methods - after_methods))
    return lines


def _diff_keys(kind: str, old: Iterable[str], new: Iterable[str]) -> list[str]:
    before, after = set(old), set(new)
    return [f"+ {kind} {k}" for k in sorted(after - before)] + [
        f"- {kind} {k}" for k in sorted(before - after)
    ]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _print_report(protocol_version: int, report: Report) -> None:
    reference = Reference.load(protocol_version)
    print(f"ACP v{protocol_version} models vs {reference.tag}")
    for finding in report.new:
        print(f"  NEW    {finding.id}: {finding.detail}")
    for key, reason in report.stale.items():
        print(f"  STALE  {key}: listed in conformance.toml ({reason}) but no longer differs")
    if report.ok:
        print("  ok: no undocumented differences")


def main(argv: list[str] | None = None) -> int:
    """Entry point of ``python -m acp.schema_reference``."""
    parser = argparse.ArgumentParser(prog="python -m acp.schema_reference", description=__doc__)
    parser.add_argument("--protocol", type=int, default=1, choices=(1, 2))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("latest", help="show the pinned and the newest upstream tag")
    sync = commands.add_parser("sync", help="update the pinned reference")
    sync.add_argument("--tag", help="upstream tag (default: newest)")
    sync.add_argument("--dry-run", action="store_true", help="only print the changelog")
    commands.add_parser("check", help="compare the models with the pinned reference")
    args = parser.parse_args(argv)
    version: int = args.protocol

    if args.command == "latest":
        pinned = Reference.load(version).tag
        newest = upstream_tags(version)[-1]
        print(f"pinned: {pinned}\nnewest: {newest}" + (" (up to date)" if pinned == newest else ""))
        return 0
    if args.command == "sync":
        tag: str = args.tag or upstream_tags(version)[-1]
        files = download(version, tag)
        if (REFERENCE_ROOT / f"v{version}" / "VERSION").exists():
            current = Reference.load(version)
            print(f"{current.tag} -> {tag}")
            new = parse_files(files)
            for line in changelog(current.parsed, new) or ["(no changes)"]:
                print(f"  {line}")
        else:
            print(f"no pinned reference yet -> {tag}")
        if not args.dry_run:
            write_reference(version, tag, files, REFERENCE_ROOT)
            print(f"pinned {tag}; run `check` to see what the models need")
        return 0
    if version not in MODEL_PACKAGES:
        print(f"no models for ACP v{version} yet", file=sys.stderr)
        return 1
    report = run_check(version)
    _print_report(version, report)
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
