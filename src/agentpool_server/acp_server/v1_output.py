"""ACP v1 output of a session: session updates in the v1 schema."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, assert_never

from aclientp.schema import AvailableCommand, Notice
import anyio

from agentpool.agents.modes import ConfigOptionChanged, ModeInfo
from agentpool_server.acp_server.event_converter import ACPEventConverter
from agentpool_server.acp_server.turns import (
    TurnCancelled,
    TurnCompleted,
    TurnFailed,
    TurnLimitReached,
    TurnRefused,
)


if TYPE_CHECKING:
    from collections.abc import Sequence

    from slashed import BaseCommand

    from agentpool.agents.events import RichAgentStreamEvent
    from agentpool_server.acp_server.output import (
        AgentStateChange,
        CommandToolKind,
        CommandToolStatus,
    )
    from agentpool_server.acp_server.session import ACPSession
    from agentpool_server.acp_server.turns import TurnOutcome


class V1SessionOutput:
    """``SessionOutput`` for ACP v1 clients."""

    def __init__(self, session: ACPSession) -> None:
        self._session = session
        self._remote_commands: list[AvailableCommand] = []
        """Commands of nested ACP agents, listed next to the session's."""

    def _converter(self, **kwargs: Any) -> ACPEventConverter:
        session = self._session
        return ACPEventConverter(
            subagent_display_mode=session.subagent_display_mode,
            use_notices=session.supports_notices,
            use_compaction_updates=session.supports_compaction,
            use_prompt_suggestions=session.supports_prompt_suggestions,
            background_tasks=session.background_tasks,
            **kwargs,
        )

    def turn(self) -> V1TurnOutput:
        return V1TurnOutput(self)

    async def background_event(self, event: RichAgentStreamEvent[Any]) -> None:
        # ACP allows session updates outside a prompt turn
        async for update in self._converter().convert(event):
            await self._session.notifications.send_update(update)

    async def state_changed(self, state: AgentStateChange) -> None:
        from aclientp.schema import (
            AvailableCommandsUpdate,
            BooleanSessionConfigOption,
            ConfigOptionUpdate,
            CurrentModeUpdate,
            SelectSessionConfigOption,
        )
        from tokonomics.model_discovery.model_info import ModelInfo

        from agentpool_server.acp_server.acp_agent import get_session_config_options

        session = self._session
        update: CurrentModeUpdate | ConfigOptionUpdate
        match state:
            case ModeInfo(value=mode_id, category_id="mode"):
                assert isinstance(mode_id, str)
                update = CurrentModeUpdate(current_mode_id=mode_id)
                session.log.debug("Forwarding mode change to client", mode_id=mode_id)
            case ModelInfo(id=model_id):
                # Model changes go through ConfigOptionUpdate (model category)
                config_options = await get_session_config_options(session.agent)
                match next((i for i in config_options if i.id == "model"), None):
                    case SelectSessionConfigOption() as model_opt:
                        model_opt.current_value = model_id
                    case _:
                        pass
                update = ConfigOptionUpdate(config_options=config_options)
                session.log.debug("Forwarding model change as config update", model_id=model_id)
            case AvailableCommandsUpdate(available_commands=cmds):
                # Store remote commands and send merged list
                self._remote_commands = list(cmds)
                await session.send_available_commands_update()
                session.log.debug("Merged and sent commands update to client")
                return
            case ConfigOptionChanged(config_id=config_id, value_id=value_id):
                # Get full config_options from agent (required by ACP protocol)
                config_options = await get_session_config_options(session.agent)
                # Update the changed option's current_value
                match next((i for i in config_options if i.id == config_id), None), value_id:
                    case SelectSessionConfigOption() as select_opt, str() as value:
                        select_opt.current_value = value
                    case BooleanSessionConfigOption() as bool_opt, bool() as flag:
                        bool_opt.current_value = flag
                    case _:
                        pass
                # Convert our core type to ACP type with full config_options
                update = ConfigOptionUpdate(config_options=config_options)
                session.log.debug("Config option change", config_id=config_id, value_id=value_id)
                # For permissions, also send legacy CurrentModeUpdate (still needed)
                if config_id == "permissions":
                    assert isinstance(value_id, str)
                    await session.notifications.update_session_mode(value_id)
                    session.log.debug("Also sent legacy mode update", mode_id=value_id)
            case ModeInfo():
                return
            case _ as unreachable:
                assert_never(unreachable)
        await session.notifications.send_update(update)

    async def commands_changed(self, commands: Sequence[BaseCommand]) -> None:
        available = [
            AvailableCommand.create(
                name=cmd.name, description=cmd.description, input_hint=cmd.usage
            )
            for cmd in commands
        ]
        names = {command.name for command in available}
        available += [c for c in self._remote_commands if c.name not in names]
        await self._session.notifications.update_commands(available)

    async def error(self, message: str) -> None:
        session = self._session
        if session.supports_notices:
            await session.notifications.send_update(Notice(severity="error", title=message))
        else:
            await session.notifications.send_agent_text(message)

    async def text(self, text: str) -> None:
        await self._session.notifications.send_agent_text(text)

    async def command_started(self, call_id: str, title: str, kind: CommandToolKind) -> None:
        await self._session.notifications.tool_call_start(
            tool_call_id=call_id, title=title, kind=kind
        )

    async def command_updated(
        self,
        call_id: str,
        *,
        status: CommandToolStatus,
        title: str | None = None,
        text: str | None = None,
    ) -> None:
        await self._session.notifications.tool_call_progress(
            tool_call_id=call_id,
            status=status,
            title=title,
            content=None if text is None else [text],
        )


class V1TurnOutput:
    """A turn's events as ACP v1 session updates (``ACPEventConverter``)."""

    def __init__(self, output: V1SessionOutput) -> None:
        self._output = output
        self._session = output._session
        self._converter: ACPEventConverter | None = None

    async def start(self) -> None:
        session = self._session
        self._converter = self._output._converter(
            context_window=await session._get_context_window(),
            session_cost=session.total_cost,
        )

    async def event(self, event: RichAgentStreamEvent[Any]) -> None:
        assert self._converter is not None, "start() comes first"
        async for update in self._converter.convert(event):
            await self._session.notifications.send_update(update)

    async def finish(self, outcome: TurnOutcome) -> None:
        converter = self._converter
        assert converter is not None, "start() comes first"
        session = self._session
        session.total_cost = converter.session_cost
        match outcome:
            case TurnCompleted():
                session.last_usage = converter.last_usage
            case TurnCancelled():
                async for cancel_update in converter.cancel_pending_tools():
                    await session.notifications.send_update(cancel_update)
                # Give the client time to apply the tool call updates before the
                # PromptResponse; some clients were seen handling the response first,
                # leaving the UI stuck.
                await anyio.sleep(0.05)
            case TurnFailed(error=error):
                # Within the turn, so the error shows up before the response
                await session._send_error_notification(f"❌ Agent error: {error}")
            case TurnLimitReached() | TurnRefused():
                pass
            case _ as unreachable:
                assert_never(unreachable)
