"""Utilities for creating Typer commands from Pydantic models."""

from __future__ import annotations

import inspect
import types as pytypes
from typing import TYPE_CHECKING, Annotated, Any, Union, get_args, get_origin

import typer


if TYPE_CHECKING:
    from collections.abc import Callable
    from inspect import Parameter

    from pydantic import BaseModel


def resolve_type(field_type: Any) -> Any:
    """Resolve actual type from Union/Optional types."""
    origin = get_origin(field_type)

    # Handle Any
    if field_type is Any:
        return str

    # Handle both Union and | syntax
    if origin is Union or origin is pytypes.UnionType:
        types_ = [t for t in get_args(field_type) if t is not type(None)]
        return resolve_type(types_[0]) if len(types_) == 1 else str

    # Handle lists/sets
    if origin in {list, set}:
        item_type = get_args(field_type)[0]
        return list[resolve_type(item_type)]  # ty: ignore[invalid-type-form]

    return field_type


def create_typer_command[T: BaseModel](
    config_cls: type[T],
    callback: Callable[[T], Any] | None = None,
    *,
    name: str | None = None,
) -> typer.models.CommandInfo:
    """Create a Typer CommandInfo from a Pydantic model."""

    def make_command(**kwargs: Any) -> Any:
        """Command created from model."""
        config = config_cls(**kwargs)
        if callback:
            return callback(config)
        return config

    # Create parameters from model fields
    params: dict[str, Parameter] = {}
    for field_name, field in config_cls.model_fields.items():
        if field_name == "type":  # Skip discriminator
            continue

        field_type = resolve_type(field.annotation)
        default = field.default if field.default != ... else None

        # First field becomes argument
        if not params:
            # For arguments, the param name should be the field name
            params[field_name] = inspect.Parameter(
                field_name,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                annotation=Annotated[field_type, typer.Argument(help=field.description)],  # ty: ignore[invalid-type-form]
                default=default,
            )
            continue

        # For options, we need proper param_decls
        option_name = field_name.replace("_", "-")
        params[field_name] = inspect.Parameter(
            field_name,
            inspect.Parameter.KEYWORD_ONLY,
            annotation=Annotated[
                field_type,  # ty: ignore[invalid-type-form]
                typer.Option(
                    "--" + option_name,  # Explicit param_decls
                    help=field.description,
                ),
            ],
            default=default,
        )

    # Create function with signature
    make_command.__signature__ = inspect.Signature(  # type: ignore
        list(params.values()), return_annotation=config_cls
    )
    make_command.__annotations__ = {k: v.annotation for k, v in params.items()}

    # Create command
    if name is None:
        name = config_cls.__name__.lower().replace("command", "")

    return typer.models.CommandInfo(
        name=name,
        callback=make_command,
        help=config_cls.__doc__,
    )
