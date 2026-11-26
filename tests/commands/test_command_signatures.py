"""Regression tests for slashed command signatures under PEP 649 lazy annotations."""

import importlib
import pkgutil
from typing import TYPE_CHECKING

from slashed import CommandStore, SlashedCommand
from slashed.base import extract_usage_params, parse_args

import agentpool_commands


if TYPE_CHECKING:
    from slashed import CommandContext

    from agentpool.agents.context import AgentContext


def _iter_subclasses[T](cls: type[T]) -> list[type[T]]:
    result: list[type[T]] = []
    for sub in cls.__subclasses__():
        result.append(sub)
        result.extend(_iter_subclasses(sub))
    return result


def test_slashed_command_accepts_type_checking_only_annotations():
    """Slashed >=1.3.1 reads command signatures with FORWARDREF at class creation and parse."""

    class ForwardRefCommand(SlashedCommand):
        """Command whose execute_command uses TYPE_CHECKING-only annotations."""

        name = "forward-ref-test"

        async def execute_command(
            self,
            ctx: CommandContext[AgentContext],
            target: str,
            *,
            limit: int = 10,
        ) -> None:
            pass

    assert ForwardRefCommand.usage == "<target> [--limit <value>]"
    assert extract_usage_params(ForwardRefCommand.execute_command, skip_first=True) == [
        "<target>",
        "[--limit <value>]",
    ]
    ctx = CommandStore().create_context(None)
    call_args, coerced_kwargs = parse_args(
        ForwardRefCommand.execute_command,
        ctx,
        ["item"],
        {"limit": "25"},
        skip_first=True,
    )
    assert call_args == [ctx, "item"]
    assert coerced_kwargs == {"limit": 25}


def test_agentpool_commands_build_signatures_without_name_error():
    """Every command in agentpool_commands builds its usage/signature without NameError."""
    for module_info in pkgutil.walk_packages(
        agentpool_commands.__path__,
        prefix=f"{agentpool_commands.__name__}.",
    ):
        importlib.import_module(module_info.name)

    command_classes = [
        cls
        for cls in _iter_subclasses(SlashedCommand)
        if cls.__module__.startswith("agentpool_commands.") and cls.name
    ]
    assert command_classes

    for cmd_cls in command_classes:
        usage_params = extract_usage_params(cmd_cls.execute_command, skip_first=True)
        assert isinstance(usage_params, list)
        cmd = cmd_cls()
        assert cmd.name
        assert cmd.description
        cmd.format_usage()

    # Verify the TYPE_CHECKING-only imports moved back out of runtime scope
    import agentpool_commands.mcp as mcp_mod
    import agentpool_commands.pool as pool_mod
    import agentpool_commands.tasks as tasks_mod
    import agentpool_commands.utils as utils_mod

    assert "AgentContext" not in vars(mcp_mod)
    assert "CommandContext" not in vars(pool_mod)
    assert "NodeContext" not in vars(pool_mod)
    assert "AgentContext" not in vars(tasks_mod)
    assert "TextSharerStr" not in vars(utils_mod)
    assert "Visibility" not in vars(utils_mod)
    assert len(agentpool_commands.get_commands()) > 0
