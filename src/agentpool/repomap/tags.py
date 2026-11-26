"""Tag extraction using tree-sitter."""

import inspect
import re
from typing import TYPE_CHECKING, Any, Literal, NamedTuple, cast


if TYPE_CHECKING:
    from tree_sitter import Node, Parser, Query


MAX_SIGNATURE_LENGTH = 160
"""Compacted signatures longer than this drop default values, then annotations."""

MAX_DOC_LENGTH = 160
"""Docstring summaries longer than this are left out (never cut)."""

BODY_NODE_TYPES = frozenset({
    "block",
    "body",
    "compound_statement",
    "class_body",
    "declaration_list",
    "field_declaration_list",
    "enum_body",
    "interface_body",
    "statement_block",
    "object_type",
})
"""Node types that hold a definition's body (the signature ends where they start)."""

_SENTENCE_END = re.compile(r"(?<!\be\.g)(?<!\bi\.e)(?<!\betc)(?<=[.!?])\s+(?=[A-Z0-9`\"'(\[*])")


class Tag(NamedTuple):
    """Represents a code tag (definition or reference)."""

    rel_fname: str
    fname: str
    line: int
    name: str
    kind: Literal["def", "ref"]
    end_line: int = -1
    signature_end_line: int = -1
    signature: str = ""
    """The definition's signature compacted to one line (definitions only)."""
    doc: str = ""
    """First sentence of the definition's docstring, if it has a short one."""
    parent_lines: tuple[int, ...] = ()
    """Lines of the enclosing definitions, outermost first (definitions only)."""


type RankedTag = Tag | tuple[str]


class FileTags(NamedTuple):
    """Tags of a file plus file-level information."""

    tags: list[Tag]
    doc: str = ""
    """First sentence of the module docstring."""


def _load_parser_and_query(filename: str) -> tuple[Parser, Query] | None:
    """Get the tree-sitter parser and tag query for a file, if both are available."""
    from grep_ast.tsl import get_language, get_parser  # type: ignore[import-untyped]
    from tree_sitter import Query, QueryError
    from tree_sitter_language_pack import SupportedLanguage

    from agentpool.repomap.languages import filename_to_grammar, get_scm_fname

    if not (grammar := filename_to_grammar(filename)) or not (scm := get_scm_fname(grammar)):
        return None
    lang = cast(SupportedLanguage, grammar)
    try:
        language = get_language(lang)
        parser: Parser = get_parser(lang)
    except Exception:  # noqa: BLE001
        return None
    try:
        return parser, Query(language, scm.read_text("utf-8"))
    except QueryError:
        from agentpool.log import get_logger

        get_logger(__name__).warning(
            "Tag query does not match the grammar", language=grammar, query=str(scm)
        )
        return None


def get_tags_from_content(content: str, filename: str) -> list[Tag]:
    """Extract tags from content without filesystem IO.

    Uses tree-sitter to parse the file and extract definitions and references.
    Falls back to Pygments lexer for reference tokens if tree-sitter queries
    don't capture them.

    Args:
        content: File content as string
        filename: Filename for language detection (e.g. "foo.py")

    Returns:
        List of Tag objects (definitions and references)
    """
    return extract_file_tags(content, filename).tags


def extract_file_tags(  # noqa: PLR0915
    content: str, filename: str, rel_fname: str | None = None
) -> FileTags:
    """Extract tags and the module docstring summary of a file.

    Args:
        content: File content as string
        filename: Filename for language detection (e.g. "foo.py")
        rel_fname: Name to store as ``Tag.rel_fname`` (defaults to ``filename``)

    Returns:
        The file's tags (definitions carry signature, docstring summary and parents)
    """
    from tree_sitter import QueryCursor

    if (loaded := _load_parser_and_query(filename)) is None:
        return FileTags([])
    rel_fname = rel_fname or filename
    parser, query = loaded
    source = bytes(content, "utf-8")
    tree = parser.parse(source)
    cursor = QueryCursor(query)

    tags: list[Tag] = []
    saw: set[str] = set()
    all_nodes: list[tuple[Any, str]] = []

    for _pattern_index, captures_dict in cursor.matches(tree.root_node):
        for tag, nodes in captures_dict.items():
            all_nodes.extend((node, tag) for node in nodes)

    # Definition nodes by id, so that each definition can name its enclosing ones.
    def_lines: dict[int, int] = {
        node.parent.id: node.start_point[0]
        for node, tag in all_nodes
        if tag.startswith("name.definition.") and node.parent is not None
    }

    for node, tag in all_nodes:
        if tag.startswith("name.definition."):
            kind: Literal["def", "ref"] = "def"
        elif tag.startswith("name.reference."):
            kind = "ref"
        else:
            continue

        saw.add(kind)
        name = node.text.decode("utf-8")
        line = node.start_point[0]

        end_line = -1
        signature_end_line = -1
        signature = doc = ""
        parent_lines: tuple[int, ...] = ()
        if kind == "def" and (def_node := node.parent) is not None:
            end_line = def_node.end_point[0]
            body = _find_body(def_node)
            if body is not None:
                signature_end_line = body.start_point[0] - 1
            signature_end_line = max(signature_end_line, line)
            signature = get_signature(source, def_node, body)
            doc = doc_summary(_docstring(body)) if body is not None else ""
            parents: list[int] = []
            ancestor = def_node.parent
            while ancestor is not None:
                if (parent_line := def_lines.get(ancestor.id)) is not None:
                    parents.append(parent_line)
                ancestor = ancestor.parent
            parent_lines = tuple(reversed(parents))
        tag_obj = Tag(
            rel_fname=rel_fname,
            fname=filename,
            name=name,
            kind=kind,
            line=line,
            end_line=end_line,
            signature_end_line=signature_end_line,
            signature=signature,
            doc=doc,
            parent_lines=parent_lines,
        )
        tags.append(tag_obj)

    module_doc = doc_summary(_docstring(tree.root_node))
    if "ref" in saw or "def" in saw:
        return FileTags(tags, module_doc)

    # Fallback to pygments lexer for references
    from pygments.lexers import guess_lexer_for_filename
    from pygments.token import Token

    try:
        lexer = guess_lexer_for_filename(filename, content)
    except Exception:  # noqa: BLE001
        return FileTags(tags, module_doc)

    tokens = list(lexer.get_tokens(content))
    name_tokens = [token[1] for token in tokens if token[0] in Token.Name]  # type: ignore[comparison-overlap]

    for token in name_tokens:
        tags.append(Tag(rel_fname=rel_fname, fname=filename, name=token, kind="ref", line=-1))  # noqa: PERF401

    return FileTags(tags, module_doc)


def _find_body(def_node: Node) -> Node | None:
    """Return the child holding a definition's body, if it has one."""
    body = def_node.child_by_field_name("body")
    if body is not None:
        return body
    return next((c for c in def_node.children if c.type in BODY_NODE_TYPES), None)


def _docstring(scope: Node | None) -> str:
    """Return the (Python-style) docstring of a module or body node, or ""."""
    if scope is None:
        return ""
    first = next((c for c in scope.named_children if c.type != "comment"), None)
    if first is not None and first.type == "expression_statement" and first.named_child_count:
        first = first.named_children[0]
    if first is None or first.type != "string":
        return ""
    content = b"".join(c.text or b"" for c in first.children if c.type == "string_content")
    return content.decode("utf-8", "replace")


def doc_summary(doc: str, max_length: int = MAX_DOC_LENGTH) -> str:
    """Return the first sentence of a docstring, or "" if there is no short one.

    The summary is the first sentence of the first paragraph, joined into one
    line. A summary longer than ``max_length`` is dropped, never cut.

    Args:
        doc: Raw docstring text
        max_length: Maximum length of the summary
    """
    text = inspect.cleandoc(doc).strip()
    if not text:
        return ""
    paragraph = " ".join(text.split("\n\n", 1)[0].split())
    if match := _SENTENCE_END.search(paragraph):
        paragraph = paragraph[: match.start()]
    return paragraph if len(paragraph) <= max_length else ""


def _compact(text: str) -> str:
    """Collapse a multi-line signature into one line."""
    text = " ".join(text.split())
    text = re.sub(r"([(\[{]) ", r"\1", text)
    text = re.sub(r",? ([)\]}])", r"\1", text)
    return re.sub(r",([)\]}])", r"\1", text)


def get_signature(  # noqa: PLR0911
    source: bytes, def_node: Node, body: Node | None = None
) -> str:
    """Return a definition's signature, compacted to a single line.

    The signature is the definition's source up to its body, without comments.
    Signatures longer than ``MAX_SIGNATURE_LENGTH`` show default values as
    ``...``; if still too long, parameter annotations are dropped too, and
    as a last resort the text is cut with ``…``.

    Args:
        source: The file's source as bytes
        def_node: The definition node (e.g. ``function_definition``)
        body: The definition's body node, if known
    """
    if body is None:
        body = _find_body(def_node)
    start = def_node.start_byte
    if body is None:
        # No body (constants, type aliases, ...): the first line, values elided
        first_line = source[start : def_node.end_byte].split(b"\n", 1)[0]
        text = _compact(first_line.decode("utf-8", "replace"))
        multiline = def_node.end_point[0] > def_node.start_point[0]
        if (multiline or len(text) > MAX_SIGNATURE_LENGTH) and "=" in text:
            return text.split("=", 1)[0].rstrip() + " = ..."
        if len(text) > MAX_SIGNATURE_LENGTH:
            return text[: MAX_SIGNATURE_LENGTH - 1] + "…"
        return text + " ..." if multiline else text
    end = body.start_byte
    comments = [(n.start_byte, n.end_byte, "") for n in _descendants(def_node, end, {"comment"})]
    text = _compact(_splice(source, start, end, comments))
    if len(text) <= MAX_SIGNATURE_LENGTH:
        return text
    defaults = [
        (value.start_byte, value.end_byte, "...")
        for n in _descendants(def_node, end, {"default_parameter", "typed_default_parameter"})
        if (value := n.child_by_field_name("value")) is not None
    ]
    text = _compact(_splice(source, start, end, comments + defaults))
    if len(text) <= MAX_SIGNATURE_LENGTH:
        return text
    annotations: list[tuple[int, int, str]] = []
    for n in _descendants(def_node, end, {"typed_parameter", "typed_default_parameter"}):
        if (typ := n.child_by_field_name("type")) is None or not n.named_child_count:
            continue
        name_end = n.named_children[0].end_byte
        if (value := n.child_by_field_name("value")) is not None:  # `a: int = 1` -> `a=...`
            annotations.append((name_end, value.start_byte, "="))
        else:  # `a: int` -> `a`
            annotations.append((name_end, typ.end_byte, ""))
    text = _compact(_splice(source, start, end, comments + defaults + annotations))
    if len(text) <= MAX_SIGNATURE_LENGTH:
        return text
    return text[: MAX_SIGNATURE_LENGTH - 1] + "…"


def _descendants(node: Node, end: int, types: set[str]) -> list[Node]:
    """Return descendants of ``node`` of the given types that start before ``end``."""
    found: list[Node] = []
    stack = list(node.children)
    while stack:
        child = stack.pop()
        if child.start_byte >= end:
            continue
        if child.type in types:
            found.append(child)
            if "comment" not in types:
                continue
        stack.extend(child.children)
    return found


def _splice(source: bytes, start: int, end: int, edits: list[tuple[int, int, str]]) -> str:
    """Return ``source[start:end]`` with byte ranges replaced (outermost edit wins)."""
    out: list[bytes] = []
    pos = start
    for e_start, e_end, repl in sorted(edits):
        if e_start < pos:
            continue
        out.extend((source[pos:e_start], repl.encode()))
        pos = min(e_end, end)
    out.append(source[pos:end])
    return b"".join(out).decode("utf-8", "replace")
