"""Jinja filters for agentpool documentation."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import jinja2


if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    import os

    from mknodes.jinja.nodeenvironment import NodeEnvironment


@jinja2.pass_environment
def pydantic_playground_url(
    env: NodeEnvironment,
    files: Mapping[str, str] | Sequence[str | os.PathLike[str]],
    active_index: int = 0,
) -> str:
    """Generate a Pydantic Playground URL from files.

    Args:
        env: The jinja environment (passed automatically)
        files: Either a mapping of filenames to content, or a sequence of file paths
        active_index: Index of the file to show as active (default: 0)

    Returns:
        URL to Pydantic Playground with files pre-loaded
    """
    import mknodes as mk

    link = mk.MkLink.for_pydantic_playground(files, active_index=active_index)
    return str(link.target)


@jinja2.pass_environment
def pydantic_playground_iframe(
    env: NodeEnvironment,
    files: Mapping[str, str] | Sequence[str | os.PathLike[str]],
    width: int = 1200,
    height: int = 900,
    active_index: int = 0,
) -> str:
    """Generate an MkIFrame for Pydantic Playground.

    Args:
        env: The jinja environment (passed automatically)
        files: Either a mapping of filenames to content, or a sequence of file paths
        width: Width of the iframe
        height: Height of the iframe
        active_index: Index of the file to show as active

    Returns:
        MkIFrame node
    """
    import mknodes as mk

    link = mk.MkLink.for_pydantic_playground(files, active_index=active_index)
    return str(mk.MkIFrame(str(link.target), width=width, height=height, parent=env.node))


@jinja2.pass_environment
def pydantic_playground_link(
    env: NodeEnvironment,
    files: Mapping[str, str] | Sequence[str | os.PathLike[str]],
    title: str = "Open in Pydantic Playground",
    active_index: int = 0,
    as_button: bool = True,
) -> str:
    """Generate an MkLink to Pydantic Playground.

    Args:
        env: The jinja environment (passed automatically)
        files: Either a mapping of filenames to content, or a sequence of file paths
        title: Link text
        active_index: Index of the file to show as active
        as_button: Whether to style as a button

    Returns:
        MkLink node
    """
    import mknodes as mk

    link = mk.MkLink.for_pydantic_playground(
        files, title=title, active_index=active_index, parent=env.node
    )
    if as_button:
        link.as_button = True
    return str(link)


@jinja2.pass_environment
def pydantic_playground(
    env: NodeEnvironment,
    files: Mapping[str, str] | Sequence[str | os.PathLike[str]],
    width: int = 1200,
    height: int = 900,
    active_index: int = 0,
    show_link: bool = True,
    link_title: str = "Open in Pydantic Playground",
) -> str:
    """Generate both iframe and link for Pydantic Playground.

    Args:
        env: The jinja environment (passed automatically)
        files: Either a mapping of filenames to content, or a sequence of file paths
        width: Width of the iframe
        height: Height of the iframe
        active_index: Index of the file to show as active
        show_link: Whether to show a link below the iframe
        link_title: Text for the link

    Returns:
        MkContainer with iframe and optional link
    """
    import mknodes as mk

    link = mk.MkLink.for_pydantic_playground(files, active_index=active_index)
    iframe = mk.MkIFrame(str(link.target), width=width, height=height)

    if show_link:
        button_link = mk.MkLink.for_pydantic_playground(
            files, title=link_title, active_index=active_index
        )
        button_link.as_button = True
        container = mk.MkContainer([iframe, button_link], parent=env.node)
        return str(container)
    container = mk.MkContainer([iframe], parent=env.node)
    return str(container)


def _first_paragraph(text: str | None) -> str:
    """Return the first paragraph of a docstring, joined to a single line."""
    if not text:
        return ""
    import inspect

    paragraph = inspect.cleandoc(text).split("\n\n", 1)[0]
    return " ".join(paragraph.split()).replace("|", "\\|")


def _resolve(target: Any) -> Any:
    """Resolve a dotted path (`module.attr` or `module:attr`) to an object."""
    if not isinstance(target, str):
        return target
    import importlib

    module, _, attr = target.replace(":", ".").rpartition(".")
    return getattr(importlib.import_module(module), attr)


def _union_members(tp: Any) -> list[Any]:
    """Flatten (Annotated) unions and containers into their member types."""
    import types
    from typing import Annotated, Union, get_args, get_origin

    origin = get_origin(tp)
    if origin is Annotated:
        return _union_members(get_args(tp)[0])
    if origin in (Union, types.UnionType):
        return [m for arg in get_args(tp) for m in _union_members(arg)]
    return [tp]


def _short_type(annotation: Any, max_members: int = 4) -> str:  # noqa: PLR0911
    """A compact, readable name for a field annotation."""
    import types
    from typing import Annotated, ForwardRef, Literal, Union, get_args, get_origin

    if isinstance(annotation, str | ForwardRef):  # unresolved forward reference
        return annotation if isinstance(annotation, str) else annotation.__forward_arg__
    origin = get_origin(annotation)
    if origin is Annotated:
        return _short_type(get_args(annotation)[0], max_members)
    if origin is Literal:
        values = [repr(v) for v in get_args(annotation)]
        if len(values) > max_members:
            return f"one of {len(values)} values"
        return " \\| ".join(values)
    if origin in (Union, types.UnionType):
        members = [m for m in _union_members(annotation) if m is not type(None)]
        names = [_short_type(m, max_members) for m in members]
        if len(names) > max_members:
            return f"one of {len(names)} types"
        return " \\| ".join(names)
    if origin is not None:
        args = ", ".join(_short_type(a, max_members) for a in get_args(annotation))
        name = getattr(origin, "__name__", str(origin))
        name = {"Sequence": "list", "Mapping": "dict"}.get(name, name)
        return f"{name}[{args}]" if args else name
    return getattr(annotation, "__name__", str(annotation))


def config_fields_table(model: Any, exclude: Sequence[str] = ()) -> str:
    """Render a compact markdown table of the fields of a pydantic config model.

    Columns: field name, short type, default and the first paragraph of the
    field description. Fields excluded from serialization are skipped.

    Args:
        model: A pydantic model class or its dotted import path
        exclude: Field names to leave out

    Returns:
        A markdown table
    """
    from pydantic_core import PydanticUndefined

    cls = _resolve(model)
    rows = ["| Field | Type | Default | Description |", "|---|---|---|---|"]
    for name, field in cls.model_fields.items():
        if field.exclude or name in exclude:
            continue
        if field.is_required():
            default = "**required**"
        elif field.default_factory is not None:
            default = "*(factory)*"
        elif field.default is PydanticUndefined or field.default is None:
            default = ""
        else:
            text = repr(field.default)
            default = f"`{text}`" if len(text) <= 40 else f"`{text[:37]}...`"  # noqa: PLR2004
        type_name = _short_type(field.annotation).replace("`", "")
        rows.append(
            f"| `{name}` | {type_name} | {default} | {_first_paragraph(field.description)} |"
        )
    return "\n".join(rows) + "\n"


def config_union_table(union: Any, discriminator: str = "type") -> str:
    """Render a markdown table listing the members of a discriminated config union.

    Columns: the discriminator value (what to write in YAML), the model's title
    and the first paragraph of its docstring.

    Args:
        union: A (possibly Annotated) union of pydantic models or its dotted import path
        discriminator: Name of the discriminator field

    Returns:
        A markdown table
    """
    from typing import Literal, get_args, get_origin

    rows = [f"| `{discriminator}` | Name | Description |", "|---|---|---|"]
    for member in _union_members(_resolve(union)):
        fields = getattr(member, "model_fields", None)
        if fields is None:
            continue
        field = fields.get(discriminator)
        tag = ""
        if field is not None and get_origin(field.annotation) is Literal:
            tag = " / ".join(f"`{v}`" for v in get_args(field.annotation))
        for sub in ("builtin_type", "provider"):  # sub-discriminators
            sub_field = fields.get(sub)
            if sub_field is not None and get_origin(sub_field.annotation) is Literal:
                values = ", ".join(f"`{v}`" for v in get_args(sub_field.annotation))
                tag = f"{tag} ({sub}: {values})"
        extra = member.model_config.get("json_schema_extra")
        title = extra.get("x-doc-title") if isinstance(extra, dict) else None
        title = title or member.model_config.get("title")
        rows.append(f"| {tag} | {title or member.__name__} | {_first_paragraph(member.__doc__)} |")
    return "\n".join(rows) + "\n"


def cli_command_table(target: str, link_prefix: str = "") -> str:
    """Render a markdown table of the commands (and subcommands) of a typer / click app.

    Args:
        target: Import path of the app (`module:attribute`)
        link_prefix: When given, top-level command names link to `<prefix><name>.md`

    Returns:
        A markdown table
    """
    import clinspector

    info = clinspector.get_cmd_info(_resolve(target))
    if info is None:
        raise ValueError(f"No CLI app found at {target}")
    rows = ["| Command | Description |", "|---|---|"]
    for name, cmd in info.subcommands.items():
        if cmd.hidden:
            continue
        label = f"[`{name}`]({link_prefix}{name}.md)" if link_prefix else f"`{name}`"
        rows.append(f"| {label} | {_first_paragraph(cmd.description)} |")
        for sub_name, sub in cmd.subcommands.items():
            if not sub.hidden:
                rows.append(f"| `{name} {sub_name}` | {_first_paragraph(sub.description)} |")
    return "\n".join(rows) + "\n"


def toolset_tools_table(union: Any, discriminator: str = "type", skip: Sequence[str] = ()) -> str:
    """Render a markdown table of the toolsets of a union and the tools each provides.

    Toolsets that need configuration to be created (required fields) are listed
    without tools.

    Args:
        union: A union of toolset config models or its dotted import path
        discriminator: Name of the discriminator field
        skip: Discriminator values of toolsets not to instantiate (for example ones
            that call a remote service to list their tools)

    Returns:
        A markdown table
    """
    import asyncio
    from concurrent.futures import ThreadPoolExecutor
    from typing import Literal, get_args, get_origin

    from pydantic import ValidationError

    async def tool_names(config: Any) -> list[str]:
        tools = await config.get_provider().get_tools()
        return [tool.name for tool in tools]

    rows = [f"| `{discriminator}` | Tools |", "|---|---|"]
    for member in _union_members(_resolve(union)):
        field = member.model_fields.get(discriminator)
        if field is None or get_origin(field.annotation) is not Literal:
            continue
        tag = get_args(field.annotation)[0]
        try:
            if tag in skip:
                raise ValueError(tag)  # noqa: TRY301
            config = member()
        except (ValidationError, ValueError):
            rows.append(f"| `{tag}` | *depends on the configuration* |")
            continue
        with ThreadPoolExecutor(max_workers=1) as pool:  # works inside a running loop
            names = pool.submit(asyncio.run, tool_names(config)).result()
        listed = ", ".join(f"`{name}`" for name in names) or "*depends on the configuration*"
        rows.append(f"| `{tag}` | {listed} |")
    return "\n".join(rows) + "\n"
