"""OpenAI-compatible API server for AgentPool."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

import anyenv
from openai.pagination import SyncPage
from openai.types.chat import ChatCompletion, ChatCompletionMessage
from openai.types.chat.chat_completion import Choice
from openai.types.completion_usage import CompletionUsage

from agentpool.log import get_logger
from agentpool_server import BaseServer
from agentpool_server.openai_api_server.completions.helpers import stream_response
from agentpool_server.openai_api_server.completions.models import OpenAIModelInfo
from agentpool_server.openai_api_server.responses.helpers import handle_request


if TYPE_CHECKING:
    from fastapi import Header
    from fastapi.responses import Response, StreamingResponse
    from openai.types.responses import Response as ResponsesResponse

    from agentpool import AgentPool
    from agentpool.agents.base_agent import BaseAgent
    from agentpool_server.openai_api_server.completions.models import ChatCompletionRequest
    from agentpool_server.openai_api_server.responses.models import ResponseRequest
logger = get_logger(__name__)


class OpenAIAPIServer(BaseServer):
    """OpenAI-compatible API server backed by a single agent.

    Uses one main agent from the pool and exposes its available models
    through the standard OpenAI models endpoint. Provides both chat
    completions and responses endpoints.
    """

    def __init__(
        self,
        pool: AgentPool,
        *,
        name: str | None = None,
        host: str = "0.0.0.0",
        port: int = 8000,
        cors: bool = True,
        api_key: str | None = None,
        raise_exceptions: bool = False,
    ) -> None:
        """Initialize OpenAI-compatible server.

        Args:
            pool: AgentPool with a main agent to serve
            name: Optional Server name (auto-generated if None)
            host: Host to bind server to
            port: Port to bind server to
            cors: Whether to enable CORS middleware
            api_key: Optional API key for authentication
            raise_exceptions: Whether to raise exceptions during server start
        """
        from fastapi import Depends, FastAPI
        import logfire

        super().__init__(pool, name=name, raise_exceptions=raise_exceptions)
        self.host = host
        self.port = port
        self.api_key = api_key
        self.app = FastAPI()
        self._last_response_id: str | None = None
        logfire.instrument_fastapi(self.app)

        if cors:
            from fastapi.middleware.cors import CORSMiddleware

            self.app.add_middleware(
                CORSMiddleware,
                allow_origins=["*"],
                allow_credentials=True,
                allow_methods=["*"],
                allow_headers=["*"],
            )

        # Add routes with authentication dependency
        dep = Depends(self.verify_api_key)
        self.app.get("/v1/models")(self.list_models)
        self.app.post("/v1/chat/completions", dependencies=[dep], response_model=None)(
            self.create_chat_completion
        )
        self.app.post("/v1/responses", dependencies=[dep], response_model=None)(
            self.create_response
        )

    @property
    def agent(self) -> BaseAgent[Any, Any]:
        """The main agent serving requests."""
        return self.pool.main_agent

    def verify_api_key(
        self, authorization: Annotated[str | None, Header(alias="Authorization")] = None
    ) -> None:
        """Verify API key if configured."""
        from fastapi import HTTPException

        if not authorization:
            raise HTTPException(401, "Missing API key")
        if not authorization.startswith("Bearer "):
            raise HTTPException(401, "Invalid authorization format")
        if self.api_key and authorization != f"Bearer {self.api_key}":
            raise HTTPException(401, "Invalid API key")

    async def list_models(self) -> SyncPage[OpenAIModelInfo]:
        """List available models from the agent's model discovery."""
        models: list[OpenAIModelInfo] = []
        try:
            available = await self.agent.get_available_models()
            if available:
                models = [
                    OpenAIModelInfo(
                        id=m.id_override or m.id,
                        created=0,
                        description=m.description or m.id,
                        object="model",
                        owned_by=m.owned_by or m.provider,
                    )
                    for m in available
                ]
        except Exception:
            logger.exception("Failed to get available models")
        # Always include the agent's current model as fallback
        if not models:
            models = [
                OpenAIModelInfo(
                    id=self.agent.name,
                    created=0,
                    description=self.agent.description,
                    object="model",
                    owned_by="agentpool",
                )
            ]
        return SyncPage[OpenAIModelInfo](data=models, object="list")

    async def create_chat_completion(self, request: ChatCompletionRequest) -> Response:
        """Handle chat completion requests."""
        from fastapi import Response
        from fastapi.responses import StreamingResponse

        agent = self.agent
        content = request.messages[-1].content or ""
        if request.stream:
            return StreamingResponse(
                stream_response(agent, content, request),
                media_type="text/event-stream",
            )
        try:
            response = await agent.run(content)
            message = ChatCompletionMessage(role="assistant", content=str(response.content))
            usage = None
            if response.cost_info:
                usage = CompletionUsage(
                    prompt_tokens=response.usage.input_tokens,
                    completion_tokens=response.usage.output_tokens,
                    total_tokens=response.usage.total_tokens,
                )
            completion_response = ChatCompletion(
                id=response.message_id,
                object="chat.completion",
                created=int(response.timestamp.timestamp()),
                model=request.model,
                choices=[Choice(index=0, message=message, finish_reason="stop")],
                usage=usage,
            )
            json = completion_response.model_dump_json()
            return Response(content=json, media_type="application/json")
        except Exception as e:
            self.log.exception("Error processing chat completion")
            from fastapi import HTTPException

            raise HTTPException(500, f"Error: {e!s}") from e

    async def create_response(
        self,
        req_body: ResponseRequest,
    ) -> ResponsesResponse | StreamingResponse:
        """Handle response creation requests."""
        from fastapi import HTTPException
        from fastapi.responses import StreamingResponse

        # Validate previous_response_id if provided
        if (
            req_body.previous_response_id is not None
            and req_body.previous_response_id != self._last_response_id
        ):
            raise HTTPException(
                404,
                f"Response '{req_body.previous_response_id}' not found. "
                "Only the most recent response ID is supported for continuation.",
            )

        try:
            if req_body.stream:
                from agentpool_server.openai_api_server.responses.stream_adapter import (
                    ResponsesStreamAdapter,
                    stream_responses,
                )

                adapter = ResponsesStreamAdapter(req_body)
                self._last_response_id = adapter.response_id
                return StreamingResponse(
                    stream_responses(self.agent, req_body, adapter),
                    media_type="text/event-stream",
                )
            response = await handle_request(req_body, self.agent)
            self._last_response_id = response.id
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(500, str(e)) from e
        else:
            return response

    async def _start_async(self) -> None:
        """Start the server (blocking async - runs until stopped)."""
        import uvicorn

        config = uvicorn.Config(
            self.app,
            host=self.host,
            port=self.port,
            log_level="info",
            ws="websockets-sansio",
        )
        server = uvicorn.Server(config)
        await server.serve()


if __name__ == "__main__":
    import anyio
    import httpx2

    from agentpool import Agent, AgentPool

    async def test_completions() -> None:
        """Test the chat completions API."""
        async with httpx2.AsyncClient() as client:
            response = await client.post(
                "http://localhost:8000/v1/chat/completions",
                headers={"Authorization": "Bearer dummy"},
                json={
                    "model": "gpt-5-mini",
                    "messages": [{"role": "user", "content": "Tell me a joke"}],
                    "stream": True,
                },
                timeout=30.0,
            )

            if response.is_success:
                for line in response.iter_lines():
                    if line.startswith("data: "):
                        data = line[6:]  # Remove "data: " prefix
                        if data == "[DONE]":
                            break
                        chunk = anyenv.load_json(data, return_type=dict)
                        delta = chunk["choices"][0]["delta"]
                        if "content" in delta:
                            print(delta["content"], end="", flush=True)
                print("\n")
            else:
                print("Completions error:", response.text)

    async def test_responses() -> None:
        """Test the responses API."""
        timeout = httpx2.Timeout(30.0, connect=5.0)
        async with httpx2.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                "http://localhost:8000/v1/responses",
                headers={"Authorization": "Bearer dummy"},
                json={
                    "model": "gpt-5-mini",
                    "input": "Tell me a three sentence bedtime story about a unicorn.",
                },
            )
            print("Responses result:", response.text)

            if not response.is_success:
                print("Responses error:", response.text)

    async def main() -> None:
        """Run server and test both endpoints."""
        pool = AgentPool()
        agent = Agent(name="gpt-5-mini", model="openai:gpt-5-mini")
        await pool.add_agent(agent)
        async with (
            OpenAIAPIServer(pool, host="0.0.0.0", port=8000) as server,
            server.run_context(),
        ):
            await anyio.sleep(1)  # Wait for server to start
            print("Testing completions endpoint...")
            await test_completions()
            print("\nTesting responses endpoint...")
            await test_responses()

    anyio.run(main)
