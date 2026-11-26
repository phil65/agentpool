"""Core RepoMap class for generating repository structure maps.

This module contains the main RepoMap class that uses tree-sitter and PageRank
to generate intelligent code structure maps.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass, field
import math
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any, ClassVar

from agentpool.repomap.tags import FileTags, Tag, extract_file_tags
from agentpool.repomap.utils import MIN_TOKEN_SAMPLE_SIZE, get_rel_path


if TYPE_CHECKING:
    from collections.abc import Sequence

    from fsspec import AbstractFileSystem

    from agentpool.repomap.types import FileInfo, RepoMapResult, TokenCounter

# Constants
CACHE_VERSION = 3
MIN_IDENT_LENGTH = 5
MAX_DEFINERS_THRESHOLD = 6
INDENT = "  "
MAX_SUMMARY_GROUPS = 12
"""The omitted-files summary groups files by directory into at most this many lines."""
MAX_SUMMARY_NAMES = 3
"""File names listed per directory in the omitted-files summary."""

RankedTag = Tag | tuple[str] | tuple[None]


@dataclass
class _MapRender:
    """A rendered map plus what it contains."""

    text: str = ""
    shown_files: set[str] = field(default_factory=set)
    shown_tags: int = 0
    omitted_files: int = 0


class RepoMap:
    """Generates a map of a repository's code structure using tree-sitter.

    Uses async fsspec filesystem for non-blocking IO operations.
    """

    TAGS_CACHE_DIR: ClassVar[str] = f".agentpool.tags.cache.v{CACHE_VERSION}"
    warned_files: ClassVar[set[str]] = set()

    def __init__(
        self,
        fs: AbstractFileSystem,
        root_path: str | None = None,
        *,
        max_tokens: int = 1024,
        max_line_length: int = 250,
        token_counter: TokenCounter | None = None,
    ) -> None:
        """Initialize RepoMap.

        Args:
            fs: Async fsspec filesystem instance.
            root_path: Root directory path in the filesystem.
            max_tokens: Maximum tokens for the generated map.
            max_line_length: Maximum character length for output lines.
            token_counter: Callable to count tokens. Defaults to len(text) / 4.
        """
        from upathtools.async_ops import to_async_fs

        self.fs = to_async_fs(fs)
        self.root_path = root_path.rstrip("/") if root_path else self.fs.root_marker
        self.max_tokens = max_tokens
        self.max_line_length = max_line_length
        self._token_counter = token_counter

        self.TAGS_CACHE: dict[str, Any] = {}

    def token_count(self, text: str) -> float:
        """Estimate token count for text."""
        if self._token_counter:
            len_text = len(text)
            if len_text < MIN_TOKEN_SAMPLE_SIZE:
                return self._token_counter(text)

            lines = text.splitlines(keepends=True)
            num_lines = len(lines)
            step = num_lines // 100 or 1
            sampled_lines = lines[::step]
            sample_text = "".join(sampled_lines)
            sample_tokens = self._token_counter(sample_text)
            return sample_tokens / len(sample_text) * len_text

        return len(text) / 4

    async def _cat_file(self, path: str) -> str | None:
        """Read file content as text."""
        try:
            content = await self.fs._cat_file(path)
            if isinstance(content, bytes):
                return content.decode("utf-8")
        except OSError, UnicodeDecodeError:
            return None
        else:
            return content

    async def _info(self, path: str) -> FileInfo | None:
        """Get file info."""
        from agentpool.repomap.types import FileInfo

        try:
            info = await self.fs._info(path)
            return FileInfo(
                path=info.get("name", path),
                size=info.get("size", 0),
                mtime=info.get("mtime"),
                type=info.get("type", "file"),
            )
        except OSError, FileNotFoundError:
            return None

    async def find_files(self, path: str, pattern: str = "**/*.py") -> list[str]:
        """Find files matching pattern recursively."""
        from upathtools import is_directory

        results: list[str] = []

        async def _recurse(current_path: str) -> None:
            entries = await self.fs._ls(current_path, detail=True)
            for entry in entries:
                entry_path = entry.get("name", "")

                if await is_directory(self.fs, entry):
                    await _recurse(entry_path)
                # It's a file - process it
                elif pattern == "**/*.py":
                    if entry_path.endswith(".py"):
                        results.append(entry_path)
                else:
                    results.append(entry_path)

        await _recurse(path)
        return results

    async def get_file_map(self, fname: str, max_tokens: int = 2048) -> str | None:
        """Generate a structure map for a single file.

        Unlike get_map which uses PageRank across multiple files, this method
        shows all definitions in a single file with line numbers.

        Args:
            fname: Absolute path to the file
            max_tokens: Maximum tokens for output (approximate)

        Returns:
            Formatted structure map or None if no tags found
        """
        rel_fname = get_rel_path(fname, self.root_path)
        file_tags = await self._get_file_tags(fname, rel_fname)
        def_tags = [t for t in file_tags.tags if t.kind == "def"]
        if not def_tags:
            return None
        tree_output = "\n".join(self._render_file(rel_fname, def_tags, line_numbers=True)) + "\n"
        info = await self._info(fname)
        size_info = f", {info.size} bytes" if info else ""
        lines = (await self._cat_file(fname) or "").count("\n") + 1
        tokens = self.token_count(tree_output)
        header = (
            f"# File: {rel_fname} ({lines} lines{size_info})\n"
            f"# Structure map ({tokens:.0f} tokens). Use offset/limit to read sections.\n\n"
        )
        result = header + tree_output
        max_chars = max_tokens * 4
        if len(result) > max_chars:
            result = result[:max_chars] + "\n... [truncated]\n"
        return result

    async def get_map(
        self,
        files: Sequence[str],
        *,
        exclude: set[str] | None = None,
        boost_files: set[str] | None = None,
        boost_idents: set[str] | None = None,
    ) -> str | None:
        """Generate a repository map for the given files.

        Args:
            files: File paths to include in the map.
            exclude: Files to exclude from the map output (but still used for ranking).
            boost_files: Files to boost in ranking.
            boost_idents: Identifiers to boost in ranking.
        """
        if not files:
            return None

        exclude = exclude or set()
        boost_files = boost_files or set()
        boost_idents = boost_idents or set()

        return await self._get_ranked_tags_map(
            files=files,
            exclude=exclude,
            boost_files=boost_files,
            boost_idents=boost_idents,
        )

    async def get_map_with_metadata(
        self,
        files: Sequence[str],
        *,
        exclude: set[str] | None = None,
        boost_files: set[str] | None = None,
        boost_idents: set[str] | None = None,
    ) -> RepoMapResult:
        """Generate a repository map with detailed metadata.

        Args:
            files: File paths to include in the map.
            exclude: Files to exclude from the map output (but still used for ranking).
            boost_files: Files to boost in ranking.
            boost_idents: Identifiers to boost in ranking.
        """
        from agentpool.repomap.types import RepoMapResult

        if not files:
            return RepoMapResult(
                content="",
                total_files_processed=0,
                total_tags_found=0,
                total_files_with_tags=0,
                included_files=0,
                included_tags=0,
                truncated=False,
                coverage_ratio=0.0,
            )

        exclude = exclude or set()
        boost_files = boost_files or set()
        boost_idents = boost_idents or set()

        ranked_tags = await self._get_ranked_tags(
            files=files,
            exclude=exclude,
            boost_files=boost_files,
            boost_idents=boost_idents,
        )

        total_tags = len([tag for tag in ranked_tags if isinstance(tag, Tag)])
        all_files_with_tags = {tag.fname if isinstance(tag, Tag) else tag[0] for tag in ranked_tags}
        total_files_with_tags = len(all_files_with_tags)

        rendered = await self._render_ranked_map(files, ranked_tags, exclude)
        content = rendered.text
        included_files = len(rendered.shown_files)
        included_tags = rendered.shown_tags
        coverage_ratio = (
            included_files / total_files_with_tags if total_files_with_tags > 0 else 0.0
        )
        truncated = included_files < total_files_with_tags or included_tags < total_tags

        return RepoMapResult(
            content=content or "",
            total_files_processed=len(files),
            total_tags_found=total_tags,
            total_files_with_tags=total_files_with_tags,
            included_files=included_files,
            included_tags=included_tags,
            truncated=truncated,
            coverage_ratio=coverage_ratio,
        )

    async def _get_tags(self, fname: str, rel_fname: str) -> list[Tag]:
        """Get tags for a file, using cache when possible."""
        return (await self._get_file_tags(fname, rel_fname)).tags

    async def _get_file_tags(self, fname: str, rel_fname: str) -> FileTags:
        """Get tags and module docstring of a file, using cache when possible."""
        info = await self._info(fname)
        if info is None:
            return FileTags([])

        file_mtime = info.mtime
        cached = self.TAGS_CACHE.get(fname)
        if cached is not None and cached.get("mtime") == file_mtime:
            return cached["data"]  # type: ignore[no-any-return]

        content = await self._cat_file(fname)
        data = extract_file_tags(content, fname, rel_fname) if content else FileTags([])
        self.TAGS_CACHE[fname] = {"mtime": file_mtime, "data": data}
        return data

    async def _get_ranked_tags(  # noqa: PLR0915
        self,
        files: Sequence[str],
        exclude: set[str],
        boost_files: set[str],
        boost_idents: set[str],
    ) -> list[RankedTag]:
        """Rank tags using PageRank algorithm."""
        import rustworkx as rx

        defines: defaultdict[str, set[str]] = defaultdict(set)
        references: defaultdict[str, list[str]] = defaultdict(list)
        definitions: defaultdict[tuple[str, str], set[Tag]] = defaultdict(set)
        personalization: dict[str, float] = {}
        exclude_rel_fnames: set[str] = set()

        focus = [ident.lower() for ident in boost_idents]

        def is_focused(name: str) -> bool:
            """Whether a name matches a boosted identifier (case-insensitive substring)."""
            lowered = name.lower()
            return any(f in lowered for f in focus)

        sorted_fnames = sorted(files)
        personalize = 100 / len(sorted_fnames) if sorted_fnames else 0

        for fname in sorted_fnames:
            info = await self._info(fname)
            if info is None or info.type != "file":
                if fname not in self.warned_files:
                    self.warned_files.add(fname)
                continue

            rel_fname = get_rel_path(fname, self.root_path)
            current_pers = 0.0

            if fname in exclude:
                current_pers += personalize
                exclude_rel_fnames.add(rel_fname)

            if fname in boost_files or rel_fname in boost_files:
                current_pers = max(current_pers, personalize)

            path_obj = PurePosixPath(rel_fname)
            if any(is_focused(part) for part in (*path_obj.parts[:-1], path_obj.stem)):
                current_pers += personalize

            tags = await self._get_tags(fname, rel_fname)
            defines_focused = False
            for tag in tags:
                match tag.kind:
                    case "def":
                        defines[tag.name].add(rel_fname)
                        key = (rel_fname, tag.name)
                        definitions[key].add(tag)
                        defines_focused = defines_focused or is_focused(tag.name)
                    case "ref":
                        references[tag.name].append(rel_fname)
            if defines_focused:
                current_pers += personalize

            if current_pers > 0:
                personalization[rel_fname] = current_pers

        if not references:
            references = defaultdict(list, {k: list(v) for k, v in defines.items()})

        idents = set(defines.keys()).intersection(set(references.keys()))

        graph: rx.PyDiGraph[str, dict[str, Any]] = rx.PyDiGraph(multigraph=True)
        node_to_idx: dict[str, int] = {}
        idx_to_node: dict[int, str] = {}

        def get_or_add_node(name: str) -> int:
            if name not in node_to_idx:
                idx = graph.add_node(name)
                node_to_idx[name] = idx
                idx_to_node[idx] = name
            return node_to_idx[name]

        for ident in defines:
            if ident in references:
                continue
            for definer in defines[ident]:
                idx = get_or_add_node(definer)
                graph.add_edge(idx, idx, {"weight": 0.1, "ident": ident})

        for ident in idents:
            definers = defines[ident]
            mul = 1.0

            is_snake = ("_" in ident) and any(c.isalpha() for c in ident)
            is_kebab = ("-" in ident) and any(c.isalpha() for c in ident)
            is_camel = any(c.isupper() for c in ident) and any(c.islower() for c in ident)

            if is_focused(ident):
                mul *= 10
            if (is_snake or is_kebab or is_camel) and len(ident) >= MIN_IDENT_LENGTH:
                mul *= 10
            if ident.startswith("_"):
                mul *= 0.1
            if len(defines[ident]) > MAX_DEFINERS_THRESHOLD:
                mul *= 0.1

            for referencer, num_refs in Counter(references[ident]).items():
                for definer in definers:
                    use_mul = mul
                    if referencer in exclude_rel_fnames:
                        use_mul *= 50
                    scaled_refs = math.sqrt(num_refs)
                    src_idx = get_or_add_node(referencer)
                    dst_idx = get_or_add_node(definer)
                    graph.add_edge(
                        src_idx, dst_idx, {"weight": use_mul * scaled_refs, "ident": ident}
                    )

        if not graph.num_nodes():
            return []

        pers_idx: dict[int, float] | None = None
        if personalization:
            pers_idx = {
                node_to_idx[name]: val
                for name, val in personalization.items()
                if name in node_to_idx
            }

        try:
            ranked_idx = rx.pagerank(
                graph,
                weight_fn=lambda e: e["weight"],
                personalization=pers_idx,
                dangling=pers_idx,
            )
        except ZeroDivisionError:
            try:
                ranked_idx = rx.pagerank(graph, weight_fn=lambda e: e["weight"])
            except ZeroDivisionError:
                return []

        ranked: dict[str, float] = {idx_to_node[idx]: rank for idx, rank in ranked_idx.items()}

        ranked_definitions: defaultdict[tuple[str, str], float] = defaultdict(float)
        for src_idx in graph.node_indices():
            src_name = idx_to_node[src_idx]
            src_rank = ranked[src_name]
            out_edges = graph.out_edges(src_idx)
            total_weight = sum(edge_data["weight"] for _, _, edge_data in out_edges)
            if total_weight == 0:
                continue
            for _, dst_idx, edge_data in out_edges:
                edge_rank = src_rank * edge_data["weight"] / total_weight
                ident = edge_data["ident"]
                dst_name = idx_to_node[dst_idx]
                ranked_definitions[(dst_name, ident)] += edge_rank

        ranked_tags: list[RankedTag] = []
        # Definitions matching a boosted identifier come first, the rest by rank.
        for key in definitions:
            if is_focused(key[1]):
                ranked_definitions.setdefault(key, 0.0)
        sorted_definitions = sorted(
            ranked_definitions.items(),
            reverse=True,
            key=lambda x: (is_focused(x[0][1]), x[1], x[0]),
        )

        for (fname, ident), _rank in sorted_definitions:
            if fname in exclude_rel_fnames:
                continue
            ranked_tags += list(definitions.get((fname, ident), []))

        rel_fnames_without_tags = {get_rel_path(fname, self.root_path) for fname in files}
        for fname in exclude:
            rel = get_rel_path(fname, self.root_path)
            rel_fnames_without_tags.discard(rel)

        fnames_already_included = {
            tag.rel_fname if isinstance(tag, Tag) else tag[0] for tag in ranked_tags
        }

        top_rank = sorted([(rank, node) for (node, rank) in ranked.items()], reverse=True)
        for _rank, fname in top_rank:
            if fname in rel_fnames_without_tags:
                rel_fnames_without_tags.remove(fname)
            if fname not in fnames_already_included:
                ranked_tags.append((fname,))

        for fname in rel_fnames_without_tags:
            ranked_tags.append((fname,))

        return ranked_tags

    async def _get_ranked_tags_map(
        self,
        files: Sequence[str],
        exclude: set[str],
        boost_files: set[str],
        boost_idents: set[str],
        max_tokens: int | None = None,
    ) -> str | None:
        """Generate ranked tags map within token budget."""
        ranked_tags = await self._get_ranked_tags(
            files=files,
            exclude=exclude,
            boost_files=boost_files,
            boost_idents=boost_idents,
        )
        rendered = await self._render_ranked_map(files, ranked_tags, exclude, max_tokens)
        return rendered.text or None

    async def _render_ranked_map(
        self,
        files: Sequence[str],
        ranked_tags: list[RankedTag],
        exclude: set[str],
        max_tokens: int | None = None,
    ) -> _MapRender:
        """Render as many top-ranked tags as fit the token budget.

        The map ends with a summary of the files it does not show; the summary
        counts against the budget too.
        """
        max_tokens = max_tokens or self.max_tokens
        if not ranked_tags:
            return _MapRender()

        exclude_rel_fnames = {get_rel_path(fname, self.root_path) for fname in exclude}
        all_rel_fnames = [get_rel_path(f, self.root_path) for f in files]
        candidates = {f for f in all_rel_fnames if f not in exclude_rel_fnames}
        # Files in rank order (the order of their first ranked tag), then the rest.
        rank_order: dict[str, int] = {}
        for tag in ranked_tags:
            name = tag.rel_fname if isinstance(tag, Tag) else tag[0]
            if name is not None and name not in rank_order:
                rank_order[name] = len(rank_order)
        for name in sorted(candidates):
            rank_order.setdefault(name, len(rank_order))
        # Module docstrings of all ranked files (tags are cached by now)
        rel_to_abs = dict(zip(all_rel_fnames, files, strict=True))
        module_docs = {
            rel: (await self._get_file_tags(rel_to_abs[rel], rel)).doc
            for rel in candidates
            if rel in rel_to_abs
        }

        num_tags = len(ranked_tags)
        lower_bound = 0
        upper_bound = num_tags
        best: _MapRender | None = None
        best_tokens: float = 0
        middle = min(int(max_tokens // 25), num_tags)

        while lower_bound <= upper_bound:
            rendered = self._to_tree(
                ranked_tags[:middle], exclude_rel_fnames, module_docs, candidates, rank_order
            )
            num_tokens = self.token_count(rendered.text)
            pct_err = abs(num_tokens - max_tokens) / max_tokens if max_tokens else 0
            ok_err = 0.05

            if (num_tokens <= max_tokens and num_tokens > best_tokens) or (
                best is None and pct_err < ok_err
            ):
                best = rendered
                best_tokens = num_tokens
                if pct_err < ok_err:
                    break

            if num_tokens < max_tokens:
                lower_bound = middle + 1
            else:
                upper_bound = middle - 1

            middle = (lower_bound + upper_bound) // 2

        return best or _MapRender()

    def _to_tree(
        self,
        tags: list[RankedTag],
        exclude_rel_fnames: set[str],
        module_docs: dict[str, str],
        candidates: set[str],
        rank_order: dict[str, int],
    ) -> _MapRender:
        """Render ranked tags grouped by file, plus a summary of the files not shown."""
        by_file: dict[str, list[Tag]] = defaultdict(list)
        for tag in tags:
            name = tag.rel_fname if isinstance(tag, Tag) else tag[0]
            if name is None or name in exclude_rel_fnames:
                continue
            file_tags = by_file[name]
            if isinstance(tag, Tag):
                file_tags.append(tag)

        lines: list[str] = []
        shown_tags = 0
        for rel_fname in sorted(by_file):
            file_tags = by_file[rel_fname]
            doc = module_docs.get(rel_fname, "")
            header = f"{rel_fname}:" if file_tags else rel_fname
            lines.append(self._with_comment(header, doc))
            if file_tags:
                lines.extend(self._render_file(rel_fname, file_tags))
                shown_tags += len(file_tags)

        omitted = sorted(candidates - by_file.keys(), key=lambda f: rank_order.get(f, 0))
        if omitted:
            if lines:
                lines.append("")
            lines.extend(_summarize_omitted(omitted))
        text = "\n".join(line[: self.max_line_length] for line in lines) + "\n"
        return _MapRender(
            text=text,
            shown_files=set(by_file),
            shown_tags=shown_tags,
            omitted_files=len(omitted),
        )

    def _with_comment(self, line: str, comment: str) -> str:
        """Append ``  # comment`` to a line if the result fits the line length."""
        if not comment:
            return line
        combined = f"{line}  # {comment}"
        return combined if len(combined) <= self.max_line_length else line

    def _render_file(
        self, rel_fname: str, tags: list[Tag], *, line_numbers: bool = False
    ) -> list[str]:
        """Render a file's definitions, one compact signature per line.

        Enclosing definitions (e.g. the class of a shown method) are shown too,
        nesting is shown by indentation.

        Args:
            rel_fname: The file, relative to the root path
            tags: The definition tags to show
            line_numbers: Append the 1-based line range of each definition
        """
        all_defs = {
            t.line: t
            for t in self._cached_tags(tags[0].fname) or tags
            if t.kind == "def" and t.signature
        }
        wanted = {t.line: t for t in tags if t.kind == "def"}
        for tag in list(wanted.values()):
            for parent_line in tag.parent_lines:
                if parent_line not in wanted and parent_line in all_defs:
                    wanted[parent_line] = all_defs[parent_line]
        out: list[str] = []
        for line in sorted(wanted):
            tag = wanted[line]
            text = f"{INDENT * (len(tag.parent_lines) + 1)}{tag.signature or tag.name}"
            if line_numbers:
                end = tag.end_line if tag.end_line > line else line
                span = f"{line + 1}-{end + 1}" if end > line else f"{line + 1}"
                text += f"  # [{span}]"
                if tag.doc:
                    text += f" {tag.doc}"
            elif not tag.parent_lines or tag.signature.startswith("class "):
                # Docstrings of methods / nested functions cost more than they help
                text = self._with_comment(text, tag.doc)
            out.append(text)
        return out

    def _cached_tags(self, fname: str) -> list[Tag]:
        """Return the cached tags of a file (empty if not cached)."""
        cached = self.TAGS_CACHE.get(fname)
        return cached["data"].tags if cached else []


def _summarize_omitted(omitted: list[str]) -> list[str]:
    """Summarize files left out of the map, grouped by directory.

    Starts with one group per top-level directory and splits the largest groups
    into subdirectories while the summary stays within ``MAX_SUMMARY_GROUPS``
    lines (a group with most of the files is always split; small groups beyond
    the limit end up in one "other" line). Each line names the top-ranked files
    of its group (``omitted`` is in rank order).
    """

    def dir_key(rel: str, depth: int) -> str:
        return "/".join(PurePosixPath(rel).parts[:-1][:depth])

    groups: dict[str, list[str]] = defaultdict(list)
    depths: dict[str, int] = {}
    for rel in omitted:
        key = dir_key(rel, 1)
        groups[key].append(rel)
        depths[key] = 1
    split_done = True
    while split_done:
        split_done = False
        for key in sorted(groups, key=lambda k: -len(groups[k])):
            subgroups: dict[str, list[str]] = defaultdict(list)
            for rel in groups[key]:
                subgroups[dir_key(rel, depths[key] + 1)].append(rel)
            if list(subgroups) == [key]:  # all files directly in this directory
                continue
            dominant = 2 * len(groups[key]) > len(omitted)
            if len(groups) - 1 + len(subgroups) > MAX_SUMMARY_GROUPS and not dominant:
                continue
            del groups[key]
            for sub_key, members in subgroups.items():
                groups[sub_key] = members
                depths[sub_key] = depths[key] + 1
            split_done = True
            break

    lines = [f"Not shown ({len(omitted)} files):"]
    ordered = sorted(groups, key=lambda k: -len(groups[k]))
    shown, rest = ordered[: MAX_SUMMARY_GROUPS - 1], ordered[MAX_SUMMARY_GROUPS - 1 :]
    if len(rest) == 1:
        shown, rest = ordered, []
    for key in sorted(shown):
        members = groups[key]
        names = [_short_name(rel, key) for rel in members[:MAX_SUMMARY_NAMES]]
        more = ", ..." if len(members) > MAX_SUMMARY_NAMES else ""
        noun = "file" if len(members) == 1 else "files"
        label = f"{key}/" if key else "./"
        lines.append(f"{label}: {len(members)} {noun} ({', '.join(names)}{more})")
    if rest:
        count = sum(len(groups[key]) for key in rest)
        names = ", ".join(f"{key}/" if key else "./" for key in sorted(rest))
        lines.append(f"other: {count} files in {names}")
    return lines


def _short_name(rel: str, group: str) -> str:
    """Name of a file relative to its summary group, packages shown as ``pkg/``."""
    name = rel[len(group) + 1 :] if group else rel
    for index_name in ("__init__.py", "index.ts", "index.js", "mod.rs"):
        if name.endswith("/" + index_name):
            return name[: -len(index_name)]
    return name
