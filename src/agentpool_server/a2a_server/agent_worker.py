from __future__ import annotations, annotations as _annotations

import base64
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Any
import uuid

from fasta2a.applications import FastA2A  # type: ignore[import-untyped]
from fasta2a.broker import InMemoryBroker  # type: ignore[import-untyped]
from fasta2a.schema import Artifact, Message, Part  # type: ignore[import-untyped]
from fasta2a.storage import InMemoryStorage  # type: ignore[import-untyped]
from fasta2a.worker import Worker  # type: ignore[import-untyped]
from pydantic import TypeAdapter
from pydantic_ai import (
    AudioUrl,
    BinaryContent,
    DocumentUrl,
    ImageUrl,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    UserPromptPart,
    VideoUrl,
)


if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Sequence

    from fasta2a.broker import Broker
    from fasta2a.schema import AgentProvider, Skill, TaskIdParams, TaskSendParams
    from fasta2a.storage import Storage
    from pydantic_ai import ModelRequestPart, ModelResponsePart, UserContent
    from starlette.middleware import Middleware
    from starlette.routing import Route
    from starlette.types import ExceptionHandler, Lifespan

    from agentpool.agents.base_agent import BaseAgent


@asynccontextmanager
async def worker_lifespan(app: FastA2A, worker: Worker, agent: BaseAgent) -> AsyncGenerator[None]:
    """Custom lifespan that runs the worker during application startup.

    This ensures the worker is started and ready to process tasks as soon as the application starts.
    """
    async with app.task_manager, agent, worker.run():
        yield


def agent_to_a2a(
    agent: BaseAgent,
    *,
    storage: Storage | None = None,
    broker: Broker | None = None,
    # Agent card
    name: str | None = None,
    url: str = "http://localhost:8000",
    version: str = "1.0.0",
    description: str | None = None,
    provider: AgentProvider | None = None,
    skills: list[Skill] | None = None,
    # Starlette
    debug: bool = False,
    routes: Sequence[Route] | None = None,
    middleware: Sequence[Middleware] | None = None,
    exception_handlers: dict[Any, ExceptionHandler] | None = None,
    lifespan: Lifespan[FastA2A] | None = None,
) -> FastA2A:
    """Create a FastA2A server from an agent."""
    storage = storage or InMemoryStorage()
    broker = broker or InMemoryBroker()
    worker = AgentWorker(agent=agent, broker=broker, storage=storage)
    lifespan = lifespan or partial(worker_lifespan, worker=worker, agent=agent)
    return FastA2A(
        storage=storage,
        broker=broker,
        name=name or agent.name,
        url=url,
        version=version,
        description=description,
        provider=provider,
        skills=skills,
        debug=debug,
        routes=routes,
        middleware=middleware,
        exception_handlers=exception_handlers,
        lifespan=lifespan,
    )


@dataclass
class AgentWorker[WorkerOutputT, AgentDepsT](Worker[list[ModelMessage]]):  # type: ignore[misc]
    """A worker that uses an agent to execute tasks."""

    agent: BaseAgent[AgentDepsT, WorkerOutputT]

    async def run_task(self, params: TaskSendParams) -> None:
        task = await self.storage.load_task(params["id"])
        if task is None:
            raise ValueError(f"Task {params['id']} not found")  # pragma: no cover

        # TODO(Marcelo): Should we lock `run_task` on the `context_id`?
        # Ensure this task hasn't been run before
        if task["status"]["state"] != "submitted":
            raise ValueError(  # pragma: no cover
                f"Task {params['id']} has already been processed (state: {task['status']['state']})"
            )

        await self.storage.update_task(task["id"], state="working")
        # Load context - contains pydantic-ai msg history from previous tasks in this conversation
        message_history = await self.storage.load_context(task["context_id"]) or []
        message_history.extend(self.build_message_history(task.get("history", [])))
        try:
            result = await self.agent.run(message_history=message_history)  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
            chat_msgs = self.agent.conversation.chat_messages
            messages = [msg for chat_msg in chat_msgs for msg in chat_msg.messages]
            await self.storage.update_context(task["context_id"], messages)
            # Convert new messages to A2A format for task history
            a2a_messages: list[Message] = []
            for message in result.messages:
                if isinstance(message, ModelRequest):
                    # Skip user prompts - they're already in task history
                    continue
                # Convert response parts to A2A format
                a2a_parts = _response_parts_to_a2a(message.parts)
                if a2a_parts:  # Add if there are visible parts (text/thinking)
                    id_ = str(uuid.uuid4())
                    msg = Message(role="agent", parts=a2a_parts, message_id=id_)
                    a2a_messages.append(msg)

            artifacts = self.build_artifacts(result.data)
        except Exception:
            await self.storage.update_task(task["id"], state="failed")
            raise
        else:
            await self.storage.update_task(
                task["id"], state="completed", new_artifacts=artifacts, new_messages=a2a_messages
            )

    async def cancel_task(self, params: TaskIdParams) -> None:
        pass

    def build_artifacts(self, result: WorkerOutputT) -> list[Artifact]:
        """Build artifacts from agent result.

        All agent outputs become artifacts to mark them as durable task outputs.
        For string results, we use a text part. For structured data, we use a data part.
        Metadata is included to preserve type information.
        """
        artifact_id = str(uuid.uuid4())
        part = self._convert_result_to_part(result)
        return [Artifact(artifact_id=artifact_id, name="result", parts=[part])]

    def _convert_result_to_part(self, result: WorkerOutputT) -> Part:
        """Convert agent result to a text or data Part.

        For string results, returns a text part.
        For structured data, returns a data part with properly serialized data.
        """
        if isinstance(result, str):
            return Part(text=result)
        output_type = type(result)
        type_adapter = TypeAdapter(output_type)
        data = type_adapter.dump_python(result, mode="json")
        json_schema = type_adapter.json_schema(mode="serialization")
        return Part(data={"result": data}, metadata={"json_schema": json_schema})

    def build_message_history(self, history: list[Message]) -> list[ModelMessage]:
        model_messages: list[ModelMessage] = []
        for message in history:
            if message["role"] == "user":
                model_messages.append(ModelRequest(parts=_request_parts_from_a2a(message["parts"])))
            else:
                model_messages.append(
                    ModelResponse(parts=_response_parts_from_a2a(message["parts"]))
                )
        return model_messages


def _request_parts_from_a2a(parts: list[Part]) -> list[ModelRequestPart]:
    """Convert A2A Part objects to pydantic-ai ModelRequestPart objects.

    This handles the conversion from A2A protocol parts (text, file, data) to
    pydantic-ai's internal request parts (UserPromptPart with various content types).

    Args:
        parts: List of A2A Part objects from incoming messages

    Returns:
        List of ModelRequestPart objects for the pydantic-ai agent
    """
    model_parts: list[ModelRequestPart] = []
    for part in parts:
        if "text" in part:
            model_parts.append(UserPromptPart(content=part["text"]))
        elif "raw" in part:
            data = base64.b64decode(part["raw"])
            mime_type = part.get("media_type", "application/octet-stream")
            content: UserContent = BinaryContent(data=data, media_type=mime_type)
            model_parts.append(UserPromptPart(content=[content]))
        elif "url" in part:
            url = part["url"]
            for url_cls in (DocumentUrl, AudioUrl, ImageUrl, VideoUrl):
                content = url_cls(url=url)
                try:
                    content.media_type  # noqa: B018
                except ValueError:  # pragma: no cover
                    continue
                else:
                    break
            else:
                raise ValueError(f"Unsupported file type: {url}")  # pragma: no cover
            model_parts.append(UserPromptPart(content=[content]))
        elif "data" in part:
            raise NotImplementedError("Data parts are not supported yet.")
        else:  # pragma: no cover
            raise ValueError(f"Unsupported part: {part}")
    return model_parts


def _response_parts_from_a2a(parts: list[Part]) -> list[ModelResponsePart]:
    """Convert A2A Part objects to pydantic-ai ModelResponsePart objects.

    This handles the conversion from A2A protocol parts (text, file, data) to
    pydantic-ai's internal response parts. Currently only supports text parts
    as agent responses in A2A are expected to be text-based.

    Args:
        parts: List of A2A Part objects from stored agent messages

    Returns:
        List of ModelResponsePart objects for message history
    """
    model_parts: list[ModelResponsePart] = []
    for part in parts:
        if "text" in part:
            model_parts.append(TextPart(content=part["text"]))
        elif "raw" in part or "url" in part:  # pragma: no cover
            raise NotImplementedError("File parts are not supported yet.")
        elif "data" in part:  # pragma: no cover
            raise NotImplementedError("Data parts are not supported yet.")
        else:  # pragma: no cover
            raise ValueError(f"Unsupported part: {part}")
    return model_parts


def _response_parts_to_a2a(parts: Sequence[ModelResponsePart]) -> list[Part]:
    """Convert pydantic-ai ModelResponsePart objects to A2A Part objects.

    This handles the conversion from pydantic-ai's internal response parts to
    A2A protocol parts. Different part types are handled as follows:
    - TextPart: Converted directly to an A2A text part
    - ThinkingPart: Converted to a text part with metadata indicating it's thinking
    - ToolCallPart: Skipped (internal to agent execution)

    Args:
        parts: List of ModelResponsePart objects from agent response

    Returns:
        List of A2A Part objects suitable for sending via A2A protocol
    """
    a2a_parts: list[Part] = []
    for part in parts:
        match part:
            case TextPart(content=content):
                a2a_parts.append(Part(text=content))
            case ThinkingPart(content=content, id=thinking_id, signature=signature):
                meta = {"type": "thinking", "thinking_id": thinking_id, "signature": signature}
                a2a_parts.append(Part(text=content, metadata=meta))
            case ToolCallPart():
                pass
    return a2a_parts
