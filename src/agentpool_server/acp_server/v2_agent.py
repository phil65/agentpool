"""ACP v2 agent: serves v2 clients through the v1 handler.

Sessions, MCP servers, storage, commands and the agent stay what they are for v1
(``AgentPoolACPAgent``): this handler translates each v2 request into the v1 one and
the answer back, and gives the v1 handler a client that speaks v2
(``V2ClientAdapter``). The prompt lifecycle is v2's own: ``session/prompt`` returns
once the message is accepted, and the turn reports itself through ``state_update``
(``running``, then ``idle`` with its stop reason, ``error`` included). See
``ACP_V2_SERVER.md``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, assert_never
import uuid

from aclientp.exceptions import RequestError
from aclientp.schema import (
    CancelNotification,
    ClientCapabilities,
    ClientSessionCapabilities,
    CloseSessionRequest as V1CloseSessionRequest,
    CompactionCapabilities,
    DeleteSessionRequest as V1DeleteSessionRequest,
    FileSystemCapabilities,
    ForkSessionRequest as V1ForkSessionRequest,
    Implementation as V1Implementation,
    InitializeRequest as V1InitializeRequest,
    InitialSessionConfigOption,
    ListSessionsRequest as V1ListSessionsRequest,
    LoadSessionRequest,
    NewSessionRequest as V1NewSessionRequest,
    NoticeCapabilities,
    PromptRequest as V1PromptRequest,
    PromptSuggestionsCapabilities,
    ResumeSessionRequest as V1ResumeSessionRequest,
    SessionOptionsRequest as V1SessionOptionsRequest,
    SessionStatusRequest as V1SessionStatusRequest,
    SetSessionConfigOptionRequest as V1SetSessionConfigOptionRequest,
    SetSessionTitleRequest as V1SetSessionTitleRequest,
    v2,
)
from aclientp.schema.elicitation import ElicitationCapabilities as V1ElicitationCapabilities
from aclientp.v2.agent import Agent as V2Agent
from pydantic import BaseModel, ValidationError

from agentpool.log import get_logger
from agentpool.turns import (
    TurnCancelled,
    TurnCompleted,
    TurnFailed,
    TurnLimitReached,
    TurnRefused,
)
from agentpool_server.acp_server.acp_agent import AgentPoolACPAgent
from agentpool_server.acp_server.idempotency import idempotency_store
from agentpool_server.acp_server.session import (
    get_all_commands,
    infer_stop_reason,
    merge_commands,
)
from agentpool_server.acp_server.v1_output import V1SessionOutput, acp_commands
from agentpool_server.acp_server.v2_client import V2ClientAdapter
from agentpool_server.acp_server.v2_output import V2SessionOutput
from agentpool_server.acp_server.v2_translate import UpdateTranslator, command, config_option


if TYPE_CHECKING:
    from collections.abc import Sequence

    from aclientp.schema import SessionConfigOption as V1SessionConfigOption
    from aclientp.v2.agent import AgentSideConnection as V2AgentSideConnection

    from agentpool.agents.base_agent import BaseAgent
    from agentpool.turns import TurnOutcome
    from agentpool_server.acp_server.server import ACPServer
    from agentpool_server.acp_server.session import ACPSession
    from agentpool_server.acp_server.v2_subagents import ChildSession


logger = get_logger(__name__)

INTERNAL_ERROR = -32603


def to_v1[T: BaseModel](params: v2.Schema, model: type[T], **overrides: Any) -> T:
    """A v2 request as the v1 one with the same wire fields.

    Raises:
        RequestError: The request does not fit (``invalid_params``).
    """
    wire = {**v2.to_wire(params), **overrides}
    try:
        return model.model_validate(wire)
    except ValidationError as exc:
        raise RequestError.invalid_params({"details": str(exc)}) from exc


def to_v2[T: v2.Schema](response: BaseModel, model: type[T]) -> T:
    """A v1 response as the v2 one with the same wire fields."""
    return model.model_validate(response.model_dump(by_alias=True, exclude_none=True))


def initial_config_options(
    options: Sequence[v2.InitialSessionConfigOption],
) -> list[InitialSessionConfigOption]:
    """v2's typed initial config options in v1 (unknown kinds are left out)."""
    converted: list[InitialSessionConfigOption] = []
    for option in options:
        match option:
            case v2.InitialConfigOptionId(config_id=config_id, value=value):
                converted.append(InitialSessionConfigOption(config_id=config_id, value=value))
            case v2.InitialConfigOptionBoolean(config_id=config_id, value=flag):
                converted.append(
                    InitialSessionConfigOption(config_id=config_id, type="boolean", value=flag)
                )
            case v2.InitialConfigOptionUnknown():
                logger.warning("Unknown initial config option kind", option=option)
            case _ as unreachable:
                assert_never(unreachable)
    return converted


def _config_options(options: Sequence[V1SessionConfigOption] | None) -> Any:
    from pydantic import MISSING

    return MISSING if options is None else [config_option(o) for o in options]


def client_capabilities(capabilities: v2.ClientCapabilities | Any) -> ClientCapabilities:
    """What a v2 client can do, as v1 capabilities for the session.

    No filesystem or terminals (v2 has none: tools run on this host); notices and
    compaction updates are part of the v2 baseline.
    """
    session = capabilities.session if isinstance(capabilities, v2.ClientCapabilities) else None
    suggestions = (
        isinstance(session, v2.ClientSessionCapabilities)
        and v2.is_set(session.prompt_suggestions)
        and session.prompt_suggestions is not None
    )
    elicitation = (
        capabilities.elicitation if isinstance(capabilities, v2.ClientCapabilities) else None
    )
    return ClientCapabilities(
        fs=FileSystemCapabilities(read_text_file=False, write_text_file=False),
        terminal=False,
        elicitation=(
            V1ElicitationCapabilities.model_validate(v2.to_wire(elicitation))
            if isinstance(elicitation, v2.ElicitationCapabilities)
            else None
        ),
        session=ClientSessionCapabilities(
            notices=NoticeCapabilities(),
            compaction=CompactionCapabilities(),
            prompt_suggestions=PromptSuggestionsCapabilities() if suggestions else None,
        ),
    )


def idle_state(outcome: TurnOutcome, session: ACPSession) -> v2.IdleStateSessionUpdate:
    """The ``idle`` state that ends a turn with this outcome."""
    from pydantic import MISSING

    usage = MISSING if session.last_usage is None else to_v2(session.last_usage, v2.Usage)
    match outcome:
        case TurnCompleted():
            return v2.IdleStateSessionUpdate(stop_reason="end_turn", usage=usage)
        case TurnCancelled():
            return v2.IdleStateSessionUpdate(stop_reason="cancelled")
        case TurnLimitReached(message=message):
            return v2.IdleStateSessionUpdate(stop_reason=infer_stop_reason(message))
        case TurnFailed(error=error):
            failure = v2.Error(code=INTERNAL_ERROR, message=str(error))
            return v2.IdleStateSessionUpdate(stop_reason="error", error=failure)
        case TurnRefused():
            return v2.IdleStateSessionUpdate(stop_reason="refusal")
        case _ as unreachable:
            assert_never(unreachable)


class AgentPoolV2Agent(V2Agent):
    """ACP v2 agent of an AgentPool, built on the v1 handler."""

    def __init__(
        self,
        conn: V2AgentSideConnection,
        *,
        default_agent: BaseAgent[Any, Any],
        debug_commands: bool = False,
        load_skills: bool = True,
        server: ACPServer | None = None,
    ) -> None:
        """Serve one v2 client connection.

        Args:
            conn: The connection to the client
            default_agent: Agent of new sessions
            debug_commands: Offer the debug slash commands
            load_skills: Load the client workspace's skills
            server: The server, for pool hot-switching
        """
        self.conn = conn
        self.client = V2ClientAdapter(conn, UpdateTranslator(message_id=str(uuid.uuid4())))
        self.v1 = AgentPoolACPAgent(
            client=self.client,
            default_agent=default_agent,
            debug_commands=debug_commands,
            load_skills=load_skills,
            server=server,
            session_output=V2SessionOutput,
        )

    # --- Connection -------------------------------------------------------------------

    async def initialize(self, params: v2.InitializeRequest) -> v2.InitializeResponse:
        info = params.info
        v1_response = await self.v1.initialize(
            V1InitializeRequest(
                protocol_version=1,
                client_capabilities=client_capabilities(params.capabilities),
                client_info=V1Implementation(
                    name=info.name,
                    version=info.version,
                    title=info.title if v2.is_set(info.title) else None,
                ),
            )
        )
        agent_info = v1_response.agent_info
        assert agent_info is not None
        return v2.InitializeResponse(
            protocol_version=2,
            info=v2.Implementation(
                name=agent_info.name, version=agent_info.version, title=agent_info.title
            ),
            capabilities=v2.AgentCapabilities(
                session=v2.SessionCapabilities(
                    prompt=v2.PromptCapabilities(
                        image=v2.PromptImageCapabilities(),
                        audio=v2.PromptAudioCapabilities(),
                        embedded_context=v2.PromptEmbeddedContextCapabilities(),
                    ),
                    mcp=v2.McpCapabilities(
                        stdio=v2.McpStdioCapabilities(), http=v2.McpHttpCapabilities()
                    ),
                    delete=v2.SessionDeleteCapabilities(),
                    additional_directories=v2.SessionAdditionalDirectoriesCapabilities(),
                    system_prompt=v2.SessionSystemPromptCapabilities(),
                    fork=v2.SessionForkCapabilities(),
                    status=v2.SessionStatusCapabilities(),
                    options=v2.SessionOptionsCapabilities(),
                    title=v2.SessionTitleCapabilities(),
                )
            ),
        )

    async def auth_status(self, params: v2.AuthStatusRequest) -> v2.AuthStatusResponse:
        from aclientp.schema import AuthStatusRequest as V1AuthStatusRequest

        response = await self.v1.auth_status(to_v1(params, V1AuthStatusRequest))
        return to_v2(response, v2.AuthStatusResponse)

    # --- Sessions ---------------------------------------------------------------------

    def _session_request[T: BaseModel](
        self,
        params: v2.NewSessionRequest | v2.ResumeSessionRequest | v2.ForkSessionRequest,
        model: type[T],
    ) -> T:
        """A v2 session setup request in v1; initial config options are typed differently."""
        options = params.config_options if v2.is_set(params.config_options) else []
        return to_v1(
            params,
            model,
            configOptions=[o.model_dump(by_alias=True) for o in initial_config_options(options)],
            replayFrom=None,
        )

    def _commands(self, session_id: str | None) -> Any:
        """The slash commands of ``session_id`` for a session response (MISSING: unknown).

        The ``available_commands_update`` sent at setup may reach the client before the
        response that tells it the session id, so responses carry the commands too.
        """
        from pydantic import MISSING

        session = self.v1.session_manager.get_session(session_id) if session_id else None
        if session is None or not isinstance(output := session.output, V1SessionOutput):
            return MISSING
        return [command(c) for c in output.available_commands(session.list_commands())]

    async def new_session(self, params: v2.NewSessionRequest) -> v2.NewSessionResponse:
        response = await self.v1.new_session(self._session_request(params, V1NewSessionRequest))
        return v2.NewSessionResponse(
            session_id=response.session_id,
            config_options=_config_options(response.config_options),
            available_commands=self._commands(response.session_id),
        )

    async def resume_session(self, params: v2.ResumeSessionRequest) -> v2.ResumeSessionResponse:
        """Resume a session; with ``replayFrom``, replay its history first (v1 load)."""
        self._refuse_child(params.session_id, "session/resume")
        if v2.is_set(params.replay_from) and params.replay_from is not None:
            loaded = await self.v1.load_session(self._session_request(params, LoadSessionRequest))
            update = v2.ReplayComplete()
            notification = v2.UpdateSessionNotification(session_id=params.session_id, update=update)
            await self.conn.session_update(notification)
            return v2.ResumeSessionResponse(
                config_options=_config_options(loaded.config_options),
                available_commands=self._commands(params.session_id),
            )
        await self.v1.resume_session(self._session_request(params, V1ResumeSessionRequest))
        session = self.v1.session_manager.get_session(params.session_id)
        options = None
        if session is not None:
            options = await session.config_options()
        return v2.ResumeSessionResponse(
            config_options=_config_options(options),
            available_commands=self._commands(params.session_id),
        )

    async def fork_session(self, params: v2.ForkSessionRequest) -> v2.ForkSessionResponse:
        self._refuse_child(params.session_id, "session/fork")
        response = await self.v1.fork_session(self._session_request(params, V1ForkSessionRequest))
        return v2.ForkSessionResponse(
            session_id=response.session_id,
            config_options=_config_options(response.config_options),
            available_commands=self._commands(response.session_id),
        )

    async def close_session(self, params: v2.CloseSessionRequest) -> v2.CloseSessionResponse:
        self._refuse_child(params.session_id, "session/close")
        await self.v1.close_session(to_v1(params, V1CloseSessionRequest))
        return v2.CloseSessionResponse()

    async def delete_session(self, params: v2.DeleteSessionRequest) -> v2.DeleteSessionResponse:
        self._refuse_child(params.session_id, "session/delete")
        await self.v1.delete_session(to_v1(params, V1DeleteSessionRequest))
        return v2.DeleteSessionResponse()

    async def list_sessions(self, params: v2.ListSessionsRequest) -> v2.ListSessionsResponse:
        response = await self.v1.list_sessions(to_v1(params, V1ListSessionsRequest))
        return to_v2(response, v2.ListSessionsResponse)

    async def session_status(self, params: v2.SessionStatusRequest) -> v2.SessionStatusResponse:
        self._refuse_child(params.session_id, "session/status")
        response = await self.v1.session_status(to_v1(params, V1SessionStatusRequest))
        return v2.SessionStatusResponse(status=response.status)

    async def session_options(self, params: v2.SessionOptionsRequest) -> v2.SessionOptionsResponse:
        session_id = params.session_id if v2.is_set(params.session_id) else None
        self._refuse_child(session_id, "session/options")
        response = await self.v1.session_options(to_v1(params, V1SessionOptionsRequest))
        if self.v1.session_manager.get_session(session_id or "") is None:
            # The commands a new session would have
            debug = self.v1.debug_commands
            commands = merge_commands(get_all_commands(debug=debug), self.v1.default_agent)
            available: Any = [command(c) for c in acp_commands(commands)]
        else:
            available = self._commands(session_id)
        return v2.SessionOptionsResponse(
            config_options=_config_options(response.config_options),
            available_commands=available,
        )

    async def set_session_title(
        self, params: v2.SetSessionTitleRequest
    ) -> v2.SetSessionTitleResponse:
        self._refuse_child(params.session_id, "session/set_title")
        response = await self.v1.set_session_title(to_v1(params, V1SetSessionTitleRequest))
        return v2.SetSessionTitleResponse(title=response.title)

    async def set_session_config_option(
        self, params: v2.SetSessionConfigOptionRequest
    ) -> v2.SetSessionConfigOptionResponse:
        self._refuse_child(params.session_id, "session/set_config_option")
        match params:
            case v2.SetConfigOptionIdRequest(session_id=sid, config_id=cid, value=value):
                request = V1SetSessionConfigOptionRequest(
                    session_id=sid, config_id=cid, value=value
                )
            case v2.SetConfigOptionBooleanRequest(session_id=sid, config_id=cid, value=flag):
                request = V1SetSessionConfigOptionRequest(
                    session_id=sid, config_id=cid, type="boolean", value=flag
                )
            case v2.SetConfigOptionUnknownRequest(type=kind):
                raise RequestError.invalid_params({"details": f"unknown option type {kind!r}"})
            case _ as unreachable:
                assert_never(unreachable)
        response = await self.v1.set_session_config_option(request)
        return v2.SetSessionConfigOptionResponse(
            config_options=[config_option(o) for o in response.config_options]
        )

    # --- Prompt turns -----------------------------------------------------------------

    async def prompt(self, params: v2.PromptRequest) -> v2.PromptResponse:
        """Accept the prompt and run or steer its turn in the background (v2's prompt lifecycle).

        A JSON-RPC error here means the message was not accepted; anything that goes
        wrong later ends the turn with ``stopReason: "error"``. When a turn is already
        running on the session, text prompts steer into the live run and multimodal
        prompts queue onto it without emitting a duplicate ``running`` state.

        A retry with the ``idempotencyKey`` of an accepted prompt returns that prompt's
        ``messageId`` and inserts nothing; the keys are kept in the session storage, so
        they survive a restart. Invalid keys fail validation (``-32602``).
        """
        self._refuse_child(params.session_id, "session/prompt")
        session = await self.v1.session_for_prompt(params.session_id)
        store = idempotency_store(self.v1.session_manager.storage)
        async with store.claim(params.session_id, params.idempotency_key) as claim:
            if claim.message_id is not None:  # A retry: the message is already in
                return v2.PromptResponse(message_id=claim.message_id)
            blocks = to_v1(params, V1PromptRequest).prompt
            contents = session.convert_prompt(blocks)
            message_id = str(uuid.uuid4())
            if session.is_running:
                if contents and all(isinstance(c, str) for c in contents):
                    text = "\n\n".join(c for c in contents if isinstance(c, str))
                    await session.agent.steer(text, session_id=session.session_id)
                elif contents:
                    session.agent.queue_prompt(*contents, session_id=session.session_id)
                await claim.record(message_id)
                return v2.PromptResponse(message_id=message_id)
            await claim.record(message_id)
        self.v1.tasks.create_task(
            self._run_turn(session, contents), name=f"acp-v2-turn-{message_id[:8]}"
        )
        return v2.PromptResponse(message_id=message_id)

    async def _run_turn(self, session: ACPSession, contents: Sequence[Any]) -> None:
        await self._send_state(session.session_id, v2.RunningStateSessionUpdate())
        try:
            outcome = await session.run_turn(contents, session.output.turn())
        except Exception as exc:  # Ends foreground work: v2 clients wait for idle
            logger.exception("Turn failed outside the agent run")
            outcome = TurnFailed(error=exc)
        await self._send_state(session.session_id, idle_state(outcome, session))

    async def _send_state(self, session_id: str, state: v2.StateSessionUpdate) -> None:
        notification = v2.UpdateSessionNotification(session_id=session_id, update=state)
        await self.conn.session_update(notification)

    async def cancel_tool_call(self, params: v2.CancelToolCallRequest) -> v2.CancelToolCallResponse:
        """Cancel a tool call that offers it: stop the task it started.

        Raises:
            RequestError: The session is unknown, or the tool call can't be cancelled
                (``invalid_params``)
        """
        from agentpool.agents.exceptions import UnsupportedOperationError
        from agentpool_server.acp_server.v2_output import V2SessionOutput

        session = self.v1.session_manager.get_session(params.session_id)
        if session is None or not isinstance(output := session.output, V2SessionOutput):
            raise RequestError.invalid_params({"details": f"No session {params.session_id!r}"})
        if (task_id := output.cancellable.task_of(params.tool_call_id)) is None:
            msg = f"Tool call {params.tool_call_id!r} can't be cancelled"
            raise RequestError.invalid_params({"details": msg})
        try:
            await session.agent.stop_task(task_id)
        except (KeyError, UnsupportedOperationError) as exc:
            raise RequestError.invalid_params({"details": str(exc)}) from exc
        return v2.CancelToolCallResponse()

    async def cancel(self, params: v2.CancelSessionNotification) -> None:
        if (child := self.client.subagents.child(params.session_id)) is not None:
            await self._cancel_child(child)
            return
        await self.v1.cancel(CancelNotification(session_id=params.session_id))

    async def _cancel_child(self, child: ChildSession) -> None:
        """``session/cancel`` on a child: stop its task (ignored unless it advertises cancel).

        The task's end (``TaskFinishedEvent``) reports the child idle with ``cancelled``.
        """
        from agentpool.agents.exceptions import UnsupportedOperationError

        if not child.stoppable or not child.active or child.task_id is None:
            return  # Per spec: ignored for a child without the capability
        session = self.v1.session_manager.get_session(child.root_id)
        if session is None:
            return
        try:
            await session.agent.stop_task(child.task_id)
        except (KeyError, UnsupportedOperationError):  # Already ended, or no longer stoppable
            logger.debug("Child session's task not stopped", child=child.session_id)

    def _refuse_child(self, session_id: str | None, method: str) -> None:
        """Reject a method on a child session: a child takes only ``session/cancel``.

        Raises:
            RequestError: ``session_id`` is a child session (``invalid_params``)
        """
        if session_id is not None and self.client.subagents.child(session_id) is not None:
            details = f"{method} is not permitted on subagent session {session_id!r}"
            raise RequestError.invalid_params({"details": details})
