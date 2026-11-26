"""Generate Pydantic models for the OpenCode V2 HTTP API.

The OpenCode V2 OpenAPI spec (``packages/protocol/openapi.json`` in the
``anomalyco/opencode`` repository) is downloaded for a pinned tag, normalized and fed
into ``datamodel-codegen``. The result is a single module,
``src/opencode_v2_sdk/models.py``.

Normalization steps (all deterministic, so regenerating gives stable diffs):

1. **Flatten names**: dotted schema names (``Session.Info``, ``session.text.delta``) are
   turned into PascalCase (``SessionInfo``, ``SessionTextDelta``). This makes the generator
   emit a single module, which avoids the circular imports of the module-tree output.
2. **Hoist inline objects**: inline object schemas (properties, array items, union
   members) are moved into named components (``SessionInfo.time`` -> ``SessionInfoTime``).
   Without this the generator invents numbered names (``Time17``), which change whenever
   the spec changes.
3. **Hoist operation bodies**: inline request bodies and success responses become
   ``<OperationId>Body`` and ``<OperationId>Response`` components.
4. **Add discriminators**: unions whose members are all objects with a single-valued
   ``type`` property get an OpenAPI discriminator, so pydantic validates them as tagged
   unions.

The pinned version lives in ``src/opencode_v2_sdk/_version.py`` (``OPENCODE_VERSION``), which
this script writes.

Usage:
    uv run python scripts/generate_opencode_v2_models.py                   # spec on disk
    uv run python scripts/generate_opencode_v2_models.py --download        # re-download pinned tag
    uv run python scripts/generate_opencode_v2_models.py --version 2.4.1   # pin, download, generate
"""

import argparse
import ast
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any
import urllib.request


SPEC_URL = (
    "https://raw.githubusercontent.com/anomalyco/opencode/v{version}/packages/protocol/openapi.json"
)
PROJECT_ROOT = Path(__file__).parent.parent
SPEC_FILE = PROJECT_ROOT / "schema" / "opencode-v2" / "openapi.json"
OUTPUT_FILE = PROJECT_ROOT / "src" / "opencode_v2_sdk" / "models.py"
VERSION_FILE = PROJECT_ROOT / "src" / "opencode_v2_sdk" / "_version.py"
REF_PREFIX = "#/components/schemas/"

type JsonObj = dict[str, Any]


def pascal(text: str) -> str:
    """Convert an arbitrary schema name/segment to PascalCase."""
    parts = [p for p in re.split(r"[^0-9A-Za-z]+", text) if p]
    result = "".join(p[:1].upper() + p[1:] for p in parts)
    if result[:1].isdigit():
        result = "N" + result
    return result


class Normalizer:
    """Rewrites the OpenAPI spec into a generator-friendly shape."""

    def __init__(self, spec: JsonObj) -> None:
        self.spec = spec
        self.schemas: JsonObj = {}

    def run(self) -> JsonObj:
        original: JsonObj = self.spec["components"]["schemas"]
        mapping = {name: pascal(name) for name in original}
        if len(set(mapping.values())) != len(mapping):
            seen: dict[str, str] = {}
            for old, new in mapping.items():
                if new in seen:
                    msg = f"Name collision after flattening: {seen[new]!r} and {old!r} -> {new!r}"
                    raise SystemExit(msg)
                seen[new] = old
        renamed = self._rename_refs(self.spec, mapping)
        self.spec = renamed
        self.schemas = {mapping[k]: v for k, v in renamed["components"]["schemas"].items()}
        # Inline primitive components (IDs, constrained strings, enums) so fields get plain
        # types instead of RootModel wrappers. They are re-added as `type` aliases.
        self.primitives = {k: v for k, v in self.schemas.items() if self._is_primitive(v)}
        for name in self.primitives:
            del self.schemas[name]
        self.schemas = self._inline_primitives(self.schemas)
        self.spec["paths"] = self._inline_primitives(self.spec["paths"])
        # Constrained primitives inside unions make the generator emit numbered RootModels.
        self.schemas = self._relax_union_primitives(self.schemas)
        self.spec["paths"] = self._relax_union_primitives(self.spec["paths"])
        # Free-form objects become dicts instead of empty models that drop all data.
        self.schemas = self._open_free_form(self.schemas)
        self.spec["paths"] = self._open_free_form(self.spec["paths"])
        # Hoist inline objects of existing components.
        for name in list(self.schemas):
            self.schemas[name] = self._hoist_root(name, self.schemas[name])
        # Hoist operation request bodies / success responses.
        for path_item in self.spec["paths"].values():
            for method, operation in path_item.items():
                if method not in {"get", "post", "put", "patch", "delete"}:
                    continue
                self._hoist_operation(operation)
        for name, schema in self.schemas.items():
            self._add_discriminators(schema, name)
        self.spec["components"]["schemas"] = dict(sorted(self.schemas.items()))
        return self.spec

    @staticmethod
    def _is_primitive(schema: Any) -> bool:
        return (
            isinstance(schema, dict)
            and schema.get("type") in {"string", "number", "integer", "boolean"}
            and not ({"anyOf", "oneOf", "allOf", "properties"} & set(schema))
        )

    def _inline_primitives(self, node: Any) -> Any:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and (target := ref.removeprefix(REF_PREFIX)) in self.primitives:
                siblings = {k: v for k, v in node.items() if k != "$ref"}
                return {**self.primitives[target], **siblings}
            return {k: self._inline_primitives(v) for k, v in node.items()}
        if isinstance(node, list):
            return [self._inline_primitives(v) for v in node]
        return node

    _CONSTRAINTS = frozenset({
        "pattern",
        "minLength",
        "maxLength",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "format",
        "title",
    })

    def _relax_union_primitives(self, node: Any) -> Any:
        if isinstance(node, list):
            return [self._relax_union_primitives(v) for v in node]
        if not isinstance(node, dict):
            return node
        result = {k: self._relax_union_primitives(v) for k, v in node.items()}
        for union_key in ("anyOf", "oneOf"):
            if isinstance(members := result.get(union_key), list):
                result[union_key] = [
                    {k: v for k, v in m.items() if k not in self._CONSTRAINTS}
                    if self._is_primitive(m)
                    else m
                    for m in members
                ]
        return result

    def _open_free_form(self, node: Any) -> Any:
        if isinstance(node, dict):
            result = {k: self._open_free_form(v) for k, v in node.items()}
            if (
                result.get("type") == "object"
                and not result.get("properties")
                and "additionalProperties" not in result
            ):
                result.pop("properties", None)
                result["additionalProperties"] = {}
            return result
        if isinstance(node, list):
            return [self._open_free_form(v) for v in node]
        return node

    def _rename_refs(self, node: Any, mapping: dict[str, str]) -> Any:
        if isinstance(node, dict):
            result: JsonObj = {}
            for key, value in node.items():
                if key == "$ref" and isinstance(value, str) and value.startswith(REF_PREFIX):
                    result[key] = REF_PREFIX + mapping[value.removeprefix(REF_PREFIX)]
                else:
                    result[key] = self._rename_refs(value, mapping)
            return result
        if isinstance(node, list):
            return [self._rename_refs(v, mapping) for v in node]
        return node

    def _register(self, name: str, schema: JsonObj) -> JsonObj:
        candidate = name
        if candidate in self.schemas and self.schemas[candidate] != schema:
            msg = f"Cannot hoist inline schema, name {candidate!r} already taken"
            raise SystemExit(msg)
        self.schemas[candidate] = schema
        return {"$ref": REF_PREFIX + candidate}

    @staticmethod
    def _is_object(schema: Any) -> bool:
        return (
            isinstance(schema, dict)
            and schema.get("type") == "object"
            and isinstance(schema.get("properties"), dict)
            and bool(schema["properties"])
        )

    @staticmethod
    def _type_tag(schema: JsonObj) -> str | None:
        props = schema.get("properties")
        if not isinstance(props, dict):
            return None
        type_prop = props.get("type")
        if isinstance(type_prop, dict):
            enum = type_prop.get("enum")
            if isinstance(enum, list) and len(enum) == 1 and isinstance(enum[0], str):
                return enum[0]
            const = type_prop.get("const")
            if isinstance(const, str):
                return const
        return None

    def _hoist_root(self, name: str, schema: Any) -> Any:
        """Hoist nested inline objects of a top-level component (keeping the root inline)."""
        if not isinstance(schema, dict):
            return schema
        return self._walk(name, schema, is_root=True)

    def _walk(self, name: str, schema: JsonObj, *, is_root: bool) -> Any:
        if not is_root and self._is_object(schema):
            hoisted = self._walk(name, schema, is_root=True)
            return self._register(name, hoisted)
        result = dict(schema)
        if isinstance(props := schema.get("properties"), dict):
            result["properties"] = {
                key: self._walk_child(name + pascal(key), value) for key, value in props.items()
            }
        if isinstance(items := schema.get("items"), dict):
            result["items"] = self._walk_child(name + "Item", items)
        if isinstance(additional := schema.get("additionalProperties"), dict):
            result["additionalProperties"] = self._walk_child(name + "Value", additional)
        for union_key in ("anyOf", "oneOf"):
            if isinstance(members := schema.get(union_key), list):
                new_members = []
                for index, member in enumerate(members):
                    if not isinstance(member, dict):
                        new_members.append(member)
                        continue
                    tag = self._type_tag(member) if self._is_object(member) else None
                    suffix = pascal(tag) if tag else f"Variant{index}"
                    new_members.append(self._walk_child(name + suffix, member))
                result[union_key] = new_members
        if isinstance(all_of := schema.get("allOf"), list):
            result["allOf"] = [
                self._walk_child(f"{name}Part{i}", m) if isinstance(m, dict) else m
                for i, m in enumerate(all_of)
            ]
        return result

    def _walk_child(self, name: str, schema: Any) -> Any:
        if not isinstance(schema, dict) or "$ref" in schema:
            return schema
        return self._walk(name, schema, is_root=False)

    def _hoist_operation(self, operation: JsonObj) -> None:
        op_id = operation.get("operationId")
        if not isinstance(op_id, str):
            return
        base = pascal(op_id)
        body = operation.get("requestBody", {}).get("content", {}).get("application/json")
        self._hoist_content(body, base + "Body")
        for status, response in operation.get("responses", {}).items():
            if status.startswith("2"):
                content = response.get("content", {}).get("application/json")
                self._hoist_content(content, base + "Response")

    def _hoist_content(self, content: Any, name: str) -> None:
        if not isinstance(content, dict):
            return
        schema = content.get("schema")
        if isinstance(schema, dict) and "$ref" not in schema and self._is_object(schema):
            content["schema"] = self._register(name, self._walk(name, schema, is_root=True))

    def _resolve(self, schema: JsonObj) -> JsonObj | None:
        ref = schema.get("$ref")
        if isinstance(ref, str):
            target = self.schemas.get(ref.removeprefix(REF_PREFIX))
            return target if isinstance(target, dict) else None
        return schema

    def _flatten_union(self, members: list[Any]) -> list[JsonObj] | None:
        """Return the union members as refs, inlining nested unions of refs.

        Returns None if any member is not a reference (to an object or a union of objects).
        """
        result: list[JsonObj] = []
        for member in members:
            if not isinstance(member, dict) or "$ref" not in member:
                return None
            target = self._resolve(member)
            if target is None:
                return None
            nested = target.get("anyOf") or target.get("oneOf")
            if isinstance(nested, list) and "properties" not in target:
                flat = self._flatten_union(nested)
                if flat is None:
                    return None
                result.extend(flat)
            else:
                result.append(member)
        return result

    def _add_discriminators(self, schema: Any, context: str) -> None:
        if isinstance(schema, list):
            for item in schema:
                self._add_discriminators(item, context)
            return
        if not isinstance(schema, dict):
            return
        for union_key in ("anyOf", "oneOf"):
            members = schema.get(union_key)
            if not isinstance(members, list) or len(members) < 2:  # noqa: PLR2004
                continue
            refs = self._flatten_union(members)
            if refs is None:
                continue
            tags: dict[str, str] = {}
            for member in refs:
                target = self._resolve(member)
                tag = self._type_tag(target) if target else None
                if tag is None or tag in tags:
                    break
                tags[tag] = member["$ref"]
            else:
                schema[union_key] = refs
                schema["discriminator"] = {"propertyName": "type", "mapping": tags}
        for value in schema.values():
            if isinstance(value, dict | list):
                self._add_discriminators(value, context)


class _ListDiscriminatorFixer(ast.NodeTransformer):
    """Move `discriminator=` from `Annotated[list[A | B], Field(...)]` onto the union.

    datamodel-codegen attaches the discriminator to the list field, which pydantic rejects.
    """

    def visit_Subscript(self, node: ast.Subscript) -> ast.AST:
        self.generic_visit(node)
        if not (isinstance(node.value, ast.Name) and node.value.id == "Annotated"):
            return node
        if not isinstance(node.slice, ast.Tuple):
            return node
        base, *metadata = node.slice.elts
        discriminator: ast.keyword | None = None
        for item in metadata:
            if isinstance(item, ast.Call) and isinstance(item.func, ast.Name):
                for kw in item.keywords:
                    if kw.arg == "discriminator":
                        discriminator = kw
                        item.keywords.remove(kw)
                        break
        if discriminator is None:
            return node
        list_node = self._find_list(base)
        if list_node is None:  # plain union: keep discriminator where it was
            for item in metadata:
                if isinstance(item, ast.Call) and isinstance(item.func, ast.Name):
                    item.keywords.insert(0, discriminator)
                    break
            return node
        field = ast.Call(func=ast.Name("Field"), args=[], keywords=[discriminator])
        list_node.slice = ast.Subscript(
            value=ast.Name("Annotated"), slice=ast.Tuple([list_node.slice, field])
        )
        remaining = [
            m for m in metadata if not (isinstance(m, ast.Call) and not m.args and not m.keywords)
        ]
        if not remaining:
            return base
        node.slice = ast.Tuple([base, *remaining])
        return node

    @staticmethod
    def _find_list(node: ast.expr) -> ast.Subscript | None:
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
            return node if node.value.id == "list" else None
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            for side in (node.left, node.right):
                if (
                    isinstance(side, ast.Subscript)
                    and isinstance(side.value, ast.Name)
                    and side.value.id == "list"
                ):
                    return side
        return None


def fix_generated(source: str) -> str:
    tree = _ListDiscriminatorFixer().visit(ast.parse(source))
    return ast.unparse(ast.fix_missing_locations(tree)) + "\n"


_PRIMITIVES = {
    "string": "str",
    "number": "float",
    "integer": "int",
    "boolean": "bool",
    "null": "None",
}


def _alias_member(schema: JsonObj) -> str | None:
    if isinstance(ref := schema.get("$ref"), str):
        return ref.removeprefix(REF_PREFIX)
    kind = schema.get("type")
    if not isinstance(kind, str) or kind not in _PRIMITIVES:
        return None
    if isinstance(enum := schema.get("enum"), list):
        return "Literal[" + ", ".join(json.dumps(v) for v in enum) + "]"
    if set(schema) - {"type", "description"}:
        return None  # constrained primitive, no faithful plain alias
    return _PRIMITIVES[kind]


def union_aliases(normalized: JsonObj, primitives: JsonObj, generated: str) -> str:
    """Build `type` aliases for unions the generator collapsed and for inlined primitives."""
    schemas: JsonObj = normalized["components"]["schemas"]
    lines: list[str] = []
    skipped: list[str] = []
    for name, schema in schemas.items():
        if not isinstance(schema, dict) or "properties" in schema:
            continue
        union_key = "anyOf" if "anyOf" in schema else "oneOf" if "oneOf" in schema else None
        if union_key is None or re.search(rf"^class {name}\b", generated, re.MULTILINE):
            continue
        members = [_alias_member(m) for m in schema[union_key]]
        if any(m is None for m in members):
            skipped.append(name)
            continue
        union = " | ".join(m for m in members if m is not None)
        if disc := schema.get("discriminator"):
            union = f'Annotated[{union}, Field(discriminator="{disc["propertyName"]}")]'
        lines.append(f"type {name} = {union}")
    for name, schema in primitives.items():
        member = _alias_member({
            "type": schema["type"],
            **({"enum": schema["enum"]} if "enum" in schema else {}),
        })
        lines.append(f"type {name} = {member}")
    if skipped:
        print(f"  No alias for (non-trivial members): {', '.join(skipped)}")
    header = "\n\n# Type aliases (unions collapsed by the generator and inlined primitives)\n\n"
    return header + "\n".join(lines) + "\n"


def pinned_version() -> str:
    """The pinned OpenCode version (``OPENCODE_VERSION`` in ``_version.py``)."""
    match = re.search(r'^OPENCODE_VERSION = "([^"]+)"$', VERSION_FILE.read_text(), re.MULTILINE)
    if match is None:
        raise SystemExit(f"No OPENCODE_VERSION in {VERSION_FILE}")
    return match.group(1)


def download_spec(version: str) -> None:
    url = SPEC_URL.format(version=version)
    print(f"Downloading {url} ...")
    with urllib.request.urlopen(url) as response:
        data = json.loads(response.read())
    SPEC_FILE.parent.mkdir(parents=True, exist_ok=True)
    SPEC_FILE.write_text(json.dumps(data, indent=2, sort_keys=False) + "\n")
    print(f"  Saved to {SPEC_FILE}")


def generate(version: str) -> None:
    spec = json.loads(SPEC_FILE.read_text())
    normalizer = Normalizer(spec)
    normalized = normalizer.run()
    normalized_file = Path(tempfile.mkdtemp(prefix="opencode-v2-")) / "openapi.normalized.json"
    normalized_file.write_text(json.dumps(normalized, indent=2) + "\n")
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "uvx",
        "--python",
        "3.13",
        "--from",
        "datamodel-code-generator==0.28.5",
        "datamodel-codegen",
        "--input",
        str(normalized_file),
        "--input-file-type",
        "openapi",
        "--output",
        str(OUTPUT_FILE),
        "--output-model-type",
        "pydantic_v2.BaseModel",
        "--target-python-version",
        "3.13",
        "--use-standard-collections",
        "--use-union-operator",
        "--use-annotated",
        "--field-constraints",
        "--use-schema-description",
        "--use-field-description",
        "--enum-field-as-literal",
        "all",
        "--use-one-literal-as-default",
        "--collapse-root-models",
        "--snake-case-field",
        "--allow-population-by-field-name",
        "--use-double-quotes",
        "--disable-timestamp",
        "--custom-file-header",
        f'"""OpenCode V2 HTTP API models (generated from opencode v{version}, do not edit)."""',
    ]
    print("Running datamodel-codegen ...")
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        raise SystemExit(result.returncode)
    generated = OUTPUT_FILE.read_text()
    OUTPUT_FILE.write_text(
        fix_generated(generated + union_aliases(normalized, normalizer.primitives, generated))
    )
    subprocess.run(["uv", "run", "ruff", "format", "-q", str(OUTPUT_FILE)], check=True)
    VERSION_FILE.write_text(
        '"""Pinned OpenCode version the V2 models were generated from."""\n\n'
        f'OPENCODE_VERSION = "{version}"\n'
    )
    content = OUTPUT_FILE.read_text()
    print(f"  {OUTPUT_FILE}: {content.count(chr(10))} lines, {content.count('class ')} classes")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true", help="Re-download the pinned spec")
    parser.add_argument(
        "--version",
        help="Pin this OpenCode version (a v2.x.y tag without 'v'); implies --download",
    )
    args = parser.parse_args()
    version = (args.version or pinned_version()).removeprefix("v")
    if args.version or args.download or not SPEC_FILE.exists():
        download_spec(version)
    generate(version)


if __name__ == "__main__":
    main()
