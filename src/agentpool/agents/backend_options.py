"""Backend-specific options passed through to an agent's SDK, checked against its types.

External agents build their backend's options (Claude Code's ``ClaudeAgentOptions``,
Codex's thread parameters, Antigravity's agent config) from agentpool's settings. What
agentpool has no setting for can be passed through as is: by the backend's own field
names, with values converted to the field's type (so YAML gives SDK objects). A name the
backend doesn't know, and one agentpool sets itself (it wires those to its own handling:
permissions, hooks, MCP, sessions), is refused.
"""

import dataclasses
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, TypeAdapter, ValidationError

from agentpool.utils.signatures import get_forwardref_signature, resolve_type_hints


if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Mapping


def dataclass_field_types(cls: type, globalns: Mapping[str, Any]) -> dict[str, Any]:
    """The fields of dataclass ``cls`` and their types (forward references in ``globalns``)."""
    if not (isinstance(cls, type) and dataclasses.is_dataclass(cls)):
        msg = f"{cls!r} is not a dataclass"
        raise TypeError(msg)
    hints = resolve_type_hints(cls, globalns)
    return {f.name: hints.get(f.name, Any) for f in dataclasses.fields(cls) if f.init}


def model_field_types(cls: type[BaseModel]) -> dict[str, Any]:
    """The fields of pydantic model ``cls`` and their types."""
    return {name: field.annotation for name, field in cls.model_fields.items()}


def parameter_types(fn: Callable[..., Any], globalns: Mapping[str, Any]) -> dict[str, Any]:
    """The keyword parameters of ``fn`` and their types (``self`` and ``**kwargs`` left out)."""
    hints = resolve_type_hints(fn, globalns)
    return {
        name: hints.get(name, Any)
        for name, param in get_forwardref_signature(fn).parameters.items()
        if name != "self" and param.kind not in (param.VAR_POSITIONAL, param.VAR_KEYWORD)
    }


def check_backend_options(
    raw: Mapping[str, Any],
    fields: Mapping[str, Any],
    *,
    managed: Collection[str],
    backend: str,
) -> dict[str, Any]:
    """``raw`` as the backend takes it: each value converted to its field's type.

    Args:
        raw: The options as configured, by the backend's field names
        fields: The backend's fields and their types
        managed: Fields agentpool sets itself (refused here)
        backend: The backend's name (for messages)

    Raises:
        ValueError: An unknown or agentpool-managed field, or a value of the wrong type
    """
    checked: dict[str, Any] = {}
    for name, value in raw.items():
        if name in managed:
            msg = f"{backend} option {name!r} is set by agentpool: use its own setting for it"
            raise ValueError(msg)
        if (field_type := fields.get(name)) is None:
            known = ", ".join(sorted(set(fields) - set(managed)))
            msg = f"{backend} has no option {name!r} (options: {known})"
            raise ValueError(msg)
        try:
            checked[name] = TypeAdapter(field_type).validate_python(value)
        except ValidationError as e:
            msg = f"{backend} option {name!r}: {e}"
            raise ValueError(msg) from e
    return checked
