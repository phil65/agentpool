"""Let agents served over MCP ask the client's user (confirmations, questions).

An agent run behind an ``ask_<name>`` tool may need the user: a tool confirmation, an MCP
server's elicitation, a question. Over MCP the only one to ask is the client, and how
depends on the connection:

- **Handshake era** (before 2026-07-28): the call stays open and the server sends the
  client an elicitation request mid-call (imperative).
- **2026-07-28, and background tasks**: there is no back-channel. The tool ends with an
  ``InputRequiredResult`` (SEP-2322) naming the question, and the client calls it again
  with the answer. The run therefore lives outside the tool call, keyed by the request
  state the client echoes back, and the next call resumes waiting on it.

A run whose question goes unanswered for ``PENDING_TTL`` is cancelled. Meanwhile the run
reports what it does (tool calls starting, tools' own progress) as the call's progress.
"""

import asyncio
from dataclasses import dataclass, field, replace
import json
from typing import TYPE_CHECKING, Any
import uuid

from mcp import types
from mcp_types.version import MODERN_PROTOCOL_VERSIONS

from agentpool.agents.events import SubAgentEvent, ToolCallProgressEvent, ToolCallStartEvent
from agentpool.log import get_logger
from agentpool.tools.decisions import Approve, Deny
from agentpool.ui.base import InputProvider


if TYPE_CHECKING:
    from fastmcp import Context
    from pydantic import BaseModel

    from agentpool.agents.context import AgentContext
    from agentpool.agents.events import RichAgentStreamEvent
    from agentpool.messaging import ChatMessage, MessageNode
    from agentpool.messaging.context import NodeContext
    from agentpool.tools.decisions import ToolDecision


logger = get_logger(__name__)

PENDING_TTL = 600.0
"""Seconds a run waits for the answer to its question before it is cancelled."""

QUESTION_KEY = "question"
"""Key of the one input request a waiting run returns."""

_DECISIONS: dict[str, ToolDecision] = {
    "allow": Approve(),
    "allow_session": Approve(scope="session"),
    "deny": Deny(),
    "deny_session": Deny(scope="session"),
    "stop": Deny(stop=True),
}
_DECISION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "decision": {
            "type": "string",
            "title": "Decision",
            "enum": list(_DECISIONS),
            "enumNames": [
                "Allow",
                "Allow for this session",
                "Don't run it",
                "Don't run it for this session",
                "Don't run it, and stop",
            ],
        },
        "message": {
            "type": "string",
            "title": "Message to the agent",
            "description": "Why not, or what to do instead (with a denial)",
        },
    },
    "required": ["decision"],
}
_TEXT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"value": {"type": "string", "title": "Answer"}},
    "required": ["value"],
}


@dataclass
class _Question:
    params: types.ElicitRequestParams
    answer: asyncio.Future[types.ElicitResult]


@dataclass
class ServedRun:
    """A run of a served node that may wait for the client's user."""

    run_id: str
    deferred: bool
    """Questions are returned as ``InputRequiredResult`` (else asked imperatively)."""
    context: Context
    """The tool call's context (the latest one, for a deferred run)."""
    task: asyncio.Task[ChatMessage[Any]] | None = None
    questions: asyncio.Queue[_Question] = field(default_factory=asyncio.Queue)
    pending: _Question | None = None
    expiry: asyncio.TimerHandle | None = None
    steps: int = 0
    """Progress reported so far (MCP progress only grows)."""

    async def report(self, context: AgentContext[Any], event: RichAgentStreamEvent[Any]) -> None:
        """Report what the run does as the progress of its current tool call.

        Tool calls starting and the progress tools report themselves, each named after the
        agent doing it (a team's member, a subagent). fastmcp sends it as a progress
        notification when the client asked for progress, and as the task's status in a
        background task. A run waiting for its user's answer has no call to report to.
        """
        if (message := _progress_message(context.node_name, event)) is None:
            return
        self.steps += 1
        try:
            await self.context.report_progress(self.steps, None, message)
        except Exception:  # Progress is a courtesy: never disturb the run for it
            logger.debug("Could not report progress", run_id=self.run_id, exc_info=True)


def _progress_message(agent: str, event: RichAgentStreamEvent[Any]) -> str | None:
    """What an event of ``agent`` tells about the run's progress, if anything."""
    match event:
        case ToolCallStartEvent(title=title):
            return f"{agent}: {title}"
        case ToolCallProgressEvent(message=str(message)) if message:
            return f"{agent}: {message}"
        case SubAgentEvent(source_name=source, event=inner):
            return _progress_message(source, inner)
        case _:
            return None


class ServedRunInputProvider(InputProvider):
    """Asks the client's user for a served run, as its connection allows."""

    def __init__(self, run: ServedRun) -> None:
        self._run = run

    async def _ask(self, params: types.ElicitRequestParams) -> types.ElicitResult:
        run = self._run
        if run.deferred:
            question = _Question(params, asyncio.get_running_loop().create_future())
            await run.questions.put(question)
            return await question.answer
        session = run.context.session
        request_id = run.context.request_id
        try:
            match params:
                case types.ElicitRequestFormParams(message=message, requested_schema=schema):
                    return await session.elicit_form(message, schema, related_request_id=request_id)
                case types.ElicitRequestURLParams(message=message, url=url, elicitation_id=eid):
                    return await session.elicit_url(
                        message, url, eid or str(uuid.uuid4()), related_request_id=request_id
                    )
        except Exception:  # A client that can't be asked: as if the user declined
            logger.warning("Could not ask the MCP client's user", exc_info=True)
            return types.ElicitResult(action="decline")

    async def get_tool_confirmation(
        self,
        context: AgentContext[Any],
        tool_description: str = "",
    ) -> ToolDecision:
        args = json.dumps(context.tool_input, indent=2, ensure_ascii=False, default=str)
        detail = f"\n{tool_description}" if tool_description else ""
        message = f"Allow the agent to run {context.tool_name!r}?{detail}\n\nArguments:\n{args}"
        params = types.ElicitRequestFormParams(message=message, requested_schema=_DECISION_SCHEMA)
        match await self._ask(params):
            case types.ElicitResult(action="accept", content={"decision": str(choice)} as content):
                match _DECISIONS.get(choice, Deny()):
                    case Deny(stop=False) as denial if isinstance(
                        text := content.get("message"), str
                    ):
                        return replace(denial, message=text.strip() or None)
                    case decision:
                        return decision
            case types.ElicitResult(action="cancel"):
                return Deny(stop=True)
            case _:  # Declined, or a client that can't be asked
                return Deny()

    async def get_elicitation(
        self,
        params: types.ElicitRequestParams,
        *,
        tool_call_id: str | None = None,
    ) -> types.ElicitResult | types.ErrorData:
        return await self._ask(params)

    async def get_text_input(self, context: NodeContext[Any], prompt: str) -> str:
        params = types.ElicitRequestFormParams(message=prompt, requested_schema=_TEXT_SCHEMA)
        match await self._ask(params):
            case types.ElicitResult(action="accept", content={"value": str(value)}):
                return value
            case _:
                return ""

    async def get_structured_input(
        self,
        context: NodeContext[Any],
        prompt: str,
        output_type: type[BaseModel],
    ) -> BaseModel:
        schema = output_type.model_json_schema()
        params = types.ElicitRequestFormParams(message=prompt, requested_schema=schema)
        match await self._ask(params):
            case types.ElicitResult(action="accept", content=dict() as content):
                return output_type.model_validate(content)
            case _:
                msg = f"The user did not provide {output_type.__name__}"
                raise ValueError(msg)


def _deferred(context: Context) -> bool:
    """Whether this call can only ask by returning an ``InputRequiredResult``."""
    if context.is_background_task:
        return True
    request = context.request_context
    return request is not None and request.protocol_version in MODERN_PROTOCOL_VERSIONS


class ServedRuns:
    """The runs of served nodes, and the ones waiting for their user's answer."""

    def __init__(self) -> None:
        self._waiting: dict[str, ServedRun] = {}

    async def call(
        self, node: MessageNode[Any, Any], prompt: str, context: Context
    ) -> Any | types.InputRequiredResult:
        """One call of a node's ``ask_<name>`` tool: start or resume a run.

        Returns the run's answer, or the question it waits on (deferred runs).
        """
        state = context.request_state
        if state is not None and (run := self._waiting.pop(state, None)) is not None:
            run.context = context
            self._answer(run, context)
        else:
            run = ServedRun(run_id=uuid.uuid4().hex, deferred=_deferred(context), context=context)
            provider = ServedRunInputProvider(run)
            run.task = asyncio.create_task(
                node.run(prompt, input_provider=provider, event_handlers=[run.report]),
                name=f"served-{node.name}",
            )
        return await self._advance(run)

    def _answer(self, run: ServedRun, context: Context) -> None:
        if run.expiry is not None:
            run.expiry.cancel()
            run.expiry = None
        question, run.pending = run.pending, None
        if question is None or question.answer.done():
            return
        match (context.input_responses or {}).get(QUESTION_KEY):
            case types.ElicitResult() as result:
                question.answer.set_result(result)
            case _:  # Answered something else, or nothing: treat as cancelled
                question.answer.set_result(types.ElicitResult(action="cancel"))

    async def _advance(self, run: ServedRun) -> Any | types.InputRequiredResult:
        assert run.task is not None
        next_question = asyncio.create_task(run.questions.get())
        try:
            done, _ = await asyncio.wait(
                {run.task, next_question}, return_when=asyncio.FIRST_COMPLETED
            )
        except asyncio.CancelledError:  # The client gave up on the call
            next_question.cancel()
            run.task.cancel()
            raise
        if run.task in done:
            next_question.cancel()
            return run.task.result().content
        question = next_question.result()
        run.pending = question
        self._waiting[run.run_id] = run
        loop = asyncio.get_running_loop()
        run.expiry = loop.call_later(PENDING_TTL, self._expire, run.run_id)
        request = types.ElicitRequest(params=question.params)
        return types.InputRequiredResult(
            input_requests={QUESTION_KEY: request}, request_state=run.run_id
        )

    def _expire(self, run_id: str) -> None:
        if (run := self._waiting.pop(run_id, None)) is None:
            return
        logger.info("Served run not answered in time, cancelling", run_id=run_id)
        if run.pending is not None and not run.pending.answer.done():
            run.pending.answer.set_result(types.ElicitResult(action="cancel"))
        if run.task is not None:
            run.task.cancel()

    def close(self) -> None:
        """Cancel the runs still waiting for an answer."""
        for run_id in list(self._waiting):
            self._expire(run_id)
