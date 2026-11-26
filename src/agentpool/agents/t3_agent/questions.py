"""Questions of a T3 thread's agent, asked through agentpool's input provider.

With a native sign-in, questions come from the socket instead (``user_input_request``
items carry them, see ``live.py``) and are answered there; the format is the same.

``t3_pending_request`` lists a thread's open questions (``list``), reads one
(``read``: ``{requestId, questions}``) and answers it (``respond`` with
``answers``: question id -> answer). Approvals are not listed and cannot be given
there. An answer is the chosen option's ``value`` (its label when it has none),
a list of those for ``multiSelect`` questions, or free text.

Upstream (https://github.com/pingdotgg/t3code): the tool in
``apps/server/src/mcp/toolkits/thread/tools.ts`` and ``handlers.ts``; how the web
client builds answers in ``packages/client-runtime/src/state/pendingUserInput.ts``.
"""

from typing import TYPE_CHECKING, Any

from mcp.types import ElicitRequestFormParams, ElicitResult
from pydantic import BaseModel, ConfigDict, Field


if TYPE_CHECKING:
    from mcp.types import ErrorData


class _Model(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class QuestionOption(_Model):
    """A choice offered with a question."""

    label: str
    description: str = ""
    value: str | None = None

    @property
    def answer(self) -> str:
        """What T3 expects when this option is chosen."""
        return self.value if self.value is not None else self.label


class Question(_Model):
    """One question of a pending request."""

    id: str
    header: str = ""
    question: str
    options: list[QuestionOption] = Field(default_factory=list)
    multi_select: bool = Field(default=False, alias="multiSelect")
    allow_custom_answer: bool = Field(default=True, alias="allowCustomAnswer")
    initial_answer: str | None = Field(default=None, alias="initialAnswer")
    required: bool = True


class PendingRequestIds(_Model):
    """Result of ``t3_pending_request`` ``list``."""

    request_ids: list[str] = Field(alias="requestIds")


class PendingQuestions(_Model):
    """Result of ``t3_pending_request`` ``read``."""

    request_id: str = Field(alias="requestId")
    questions: list[Question]


def to_elicitation(question: Question) -> ElicitRequestFormParams:
    """An MCP form asking one question.

    Options become an enum of their labels (an array of them for multi-select);
    without options the question takes free text.
    """
    prop: dict[str, Any] = {"title": question.header or question.id}
    if question.question:
        prop["description"] = question.question
    labels = [option.label for option in question.options]
    if labels and question.multi_select:
        prop |= {"type": "array", "items": {"type": "string", "enum": labels}}
    elif labels:
        prop |= {"type": "string", "enum": labels}
    else:
        prop["type"] = "string"
        if question.initial_answer is not None:
            prop["default"] = question.initial_answer
    schema: dict[str, Any] = {"type": "object", "properties": {question.id: prop}}
    if question.required:
        schema["required"] = [question.id]
    message = f"{question.header}: {question.question}" if question.header else question.question
    return ElicitRequestFormParams(message=message, requested_schema=schema)


def to_answer(question: Question, result: ElicitResult | ErrorData) -> str | list[str] | None:
    """T3's answer for the user's response, or None when they gave none."""
    if not isinstance(result, ElicitResult) or result.action != "accept" or not result.content:
        return None
    by_label = {option.label: option.answer for option in question.options}
    match result.content.get(question.id):
        case list() as chosen:
            return [by_label.get(str(label), str(label)) for label in chosen]
        case str() as text:
            return by_label.get(text, text)
        case None:
            return None
        case other:
            return str(other)
