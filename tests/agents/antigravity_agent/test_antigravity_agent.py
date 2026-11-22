"""End-to-end unit tests for AntigravityAgent with an in-memory stub SDK Agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Self

from google.antigravity.types import (
    AskQuestionEntry,
    AskQuestionInteractionSpec,
    AskQuestionOption,
    Step,
    StepSource,
    StepStatus,
    StepTarget,
    StepType,
    StopReason,
    ToolCall,
    ToolResult,
    UsageMetadata,
)
from pydantic import BaseModel

from agentpool.agents.antigravity_agent import AntigravityAgent
from agentpool.agents.events import (
    RunStartedEvent,
    StreamCompleteEvent,
    ToolCallCompleteEvent,
    ToolCallStartEvent,
)
from agentpool.hooks import AgentHooks, CallableHook
from agentpool.ui.mock_provider import MockInputProvider


if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path
    from types import TracebackType

    from google.antigravity import AgentConfig as SDKAgentConfig
    import pytest


@dataclass
class _StubConversation:
    """Minimal in-memory stand-in for ``google.antigravity.conversation.Conversation``."""

    turns: list[list[Step]]
    sdk_config: SDKAgentConfig
    conversation_id: str = "12345678-1234-1234-1234-123456789012"
    history: list[Step] = field(default_factory=list)
    sent_prompts: list[Any] = field(default_factory=list)
    turn_count: int = 0
    cancelled: bool = False
    last_turn_usage: UsageMetadata | None = None
    _last_turn_stop_reason: StopReason = StopReason.UNSPECIFIED
    _last_structured: Any | None = None
    _post_tool_results: dict[str, ToolResult] = field(default_factory=dict)

    async def send(self, prompt: Any, **kwargs: Any) -> None:
        del kwargs
        self.sent_prompts.append(prompt)
        self.turn_count += 1

    async def receive_steps(self) -> AsyncIterator[Step]:
        from google.antigravity.hooks.hooks import HookContext, PostToolCallHook

        steps = self.turns.pop(0) if self.turns else []
        for step in steps:
            # Trigger registered PostToolCallHook before terminal tool step
            if step.type == StepType.TOOL_CALL and step.status == StepStatus.DONE:
                for call in step.tool_calls:
                    key = call.id or step.id
                    if (res := self._post_tool_results.get(key)) is not None:
                        for hook in self.sdk_config.hooks:
                            if isinstance(hook, PostToolCallHook):
                                await hook.run(HookContext(), res)
            if step.type == StepType.FINISH and step.structured_output is not None:
                self._last_structured = step.structured_output
            self.history.append(step)
            yield step

    def get_last_structured_output(self) -> Any | None:
        return self._last_structured

    async def cancel(self) -> None:
        self.cancelled = True


class _StubSDKAgent:
    """In-memory stub replacing ``google.antigravity.Agent`` for deterministic unit tests."""

    def __init__(
        self,
        sdk_config: SDKAgentConfig,
        turns: list[list[Step]],
        *,
        post_tool_results: dict[str, ToolResult] | None = None,
        initial_history: list[Step] | None = None,
        usage: UsageMetadata | None = None,
    ) -> None:
        self.config = sdk_config
        self.is_started = False
        self.conversation = _StubConversation(
            turns=list(turns),
            sdk_config=sdk_config,
            history=list(initial_history or []),
            last_turn_usage=usage,
            _post_tool_results=dict(post_tool_results or {}),
        )
        self.entered = False
        self.exited = False

    @property
    def conversation_id(self) -> str:
        return self.conversation.conversation_id

    async def __aenter__(self) -> Self:
        self.is_started = True
        self.entered = True
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.is_started = False
        self.exited = True


async def test_stream_text_tool_and_usage(tmp_path: Path) -> None:
    created: list[_StubSDKAgent] = []
    call = ToolCall(
        id="t:1",
        step_id="t:1",
        name="view_file",
        args={"path": "/tmp/demo.py"},
        canonical_path="/tmp/demo.py",
    )
    turns = [
        [
            Step(
                id="t:1",
                step_index=1,
                type=StepType.TOOL_CALL,
                source=StepSource.MODEL,
                target=StepTarget.ENVIRONMENT,
                status=StepStatus.ACTIVE,
                tool_calls=[call],
            ),
            Step(
                id="t:1",
                step_index=1,
                type=StepType.TOOL_CALL,
                source=StepSource.MODEL,
                target=StepTarget.ENVIRONMENT,
                status=StepStatus.DONE,
                tool_calls=[call],
            ),
            Step(
                id="t:2",
                step_index=2,
                type=StepType.TEXT_RESPONSE,
                source=StepSource.MODEL,
                target=StepTarget.USER,
                status=StepStatus.ACTIVE,
                content_delta="File says: ",
            ),
            Step(
                id="t:2",
                step_index=2,
                type=StepType.TEXT_RESPONSE,
                source=StepSource.MODEL,
                target=StepTarget.USER,
                status=StepStatus.DONE,
                content="File says: hello",
                content_delta="hello",
            ),
        ]
    ]
    usage = UsageMetadata(
        prompt_token_count=50,
        candidates_token_count=12,
        thoughts_token_count=8,
        total_token_count=70,
    )

    def factory(cfg: SDKAgentConfig) -> Any:
        stub = _StubSDKAgent(
            cfg,
            turns,
            post_tool_results={"t:1": ToolResult(id="t:1", name="view_file", result="print('hi')")},
            usage=usage,
        )
        created.append(stub)
        return stub

    agent = AntigravityAgent(
        name="test-agy",
        api_key="test-key",
        save_dir=str(tmp_path),
        enable_logging=False,
        sdk_agent_factory=factory,
    )

    async with agent:
        events = [event async for event in agent.run_stream("Read demo.py")]

    assert len(created) == 1
    assert created[0].exited is True
    assert isinstance(events[0], RunStartedEvent)
    assert isinstance(events[-1], StreamCompleteEvent)

    starts = [e for e in events if isinstance(e, ToolCallStartEvent)]
    completes = [e for e in events if isinstance(e, ToolCallCompleteEvent)]
    assert len(starts) == 1
    assert starts[0].kind == "read"
    assert starts[0].locations[0].path == "/tmp/demo.py"
    assert len(completes) == 1
    assert completes[0].tool_result == "print('hi')"

    msg = events[-1].message
    assert msg.content == "File says: hello"
    assert msg.finish_reason == "stop"
    assert msg.usage.input_tokens == 50
    assert msg.usage.output_tokens == 20


async def test_structured_output(tmp_path: Path) -> None:
    class Summary(BaseModel):
        title: str
        score: int

    turns = [
        [
            Step(
                id="t:1",
                step_index=1,
                type=StepType.FINISH,
                status=StepStatus.DONE,
                structured_output={"title": "Great", "score": 10},
            )
        ]
    ]
    captured_configs: list[SDKAgentConfig] = []

    def factory(cfg: SDKAgentConfig) -> Any:
        captured_configs.append(cfg)
        return _StubSDKAgent(cfg, turns)

    agent = AntigravityAgent(
        api_key="test-key",
        save_dir=str(tmp_path),
        enable_logging=False,
        sdk_agent_factory=factory,
    ).to_structured(Summary)

    async with agent:
        result = await agent.run("Summarize")

    assert isinstance(result.content, Summary)
    assert result.content == Summary(title="Great", score=10)
    assert captured_configs[0].response_schema is not None


async def test_policy_approval_and_interaction_hooks(tmp_path: Path) -> None:
    from google.antigravity.hooks.hooks import HookContext, OnInteractionHook

    captured: list[SDKAgentConfig] = []

    def factory(cfg: SDKAgentConfig) -> Any:
        captured.append(cfg)
        return _StubSDKAgent(cfg, [])

    provider = MockInputProvider(
        tool_confirmation="allow",
        elicitation_response={"answer": "Option B"},
    )
    agent = AntigravityAgent(
        api_key="test-key",
        policy_mode="confirm_run_command",
        behavior="interactive",
        save_dir=str(tmp_path),
        enable_logging=False,
        input_provider=provider,
        sdk_agent_factory=factory,
    )
    async with agent:
        tc = ToolCall(id="c1", name="run_command", args={"CommandLine": "ls"})
        approved = await agent._handle_tool_approval(tc, reason="Shell command")
        assert approved is True

        interaction_hooks = [h for h in captured[0].hooks if isinstance(h, OnInteractionHook)]
        assert len(interaction_hooks) == 1
        spec = AskQuestionInteractionSpec(
            questions=[
                AskQuestionEntry(
                    question="Choose one",
                    options=[
                        AskQuestionOption(id="1", text="Option A"),
                        AskQuestionOption(id="2", text="Option B"),
                    ],
                )
            ]
        )
        res = await interaction_hooks[0].run(HookContext(), spec)
        assert res.cancelled is False
        assert res.responses[0].selected_option_ids == ["2"]


async def test_agent_hooks_pre_and_post_tool(tmp_path: Path) -> None:
    from google.antigravity.hooks.hooks import HookContext, PostToolCallHook, PreToolCallDecideHook

    seen_post: list[tuple[str, Any]] = []

    def pre_hook(*, tool_name: str, **_kwargs: Any) -> dict[str, Any]:
        if tool_name == "run_command":
            return {"decision": "deny", "reason": "No shell allowed"}
        return {"decision": "allow"}

    def post_hook(*, tool_name: str, tool_output: Any, **_kwargs: Any) -> dict[str, Any]:
        seen_post.append((tool_name, tool_output))
        return {"decision": "allow"}

    captured: list[SDKAgentConfig] = []

    def factory(cfg: SDKAgentConfig) -> Any:
        captured.append(cfg)
        return _StubSDKAgent(cfg, [])

    hooks = AgentHooks(
        pre_tool_use=[CallableHook(event="pre_tool_use", fn=pre_hook)],
        post_tool_use=[CallableHook(event="post_tool_use", fn=post_hook)],
    )
    agent = AntigravityAgent(
        api_key="test-key",
        save_dir=str(tmp_path),
        enable_logging=False,
        hooks=hooks,
        sdk_agent_factory=factory,
    )
    async with agent:
        pre = next(h for h in captured[0].hooks if isinstance(h, PreToolCallDecideHook))
        post = next(h for h in captured[0].hooks if isinstance(h, PostToolCallHook))

        denied = await pre.run(HookContext(), ToolCall(id="1", name="run_command", args={}))
        assert denied.allow is False
        assert denied.message == "No shell allowed"

        await post.run(HookContext(), ToolResult(id="1", name="view_file", result="content"))
        assert seen_post == [("view_file", "content")]


async def test_config_options_restart_and_sessions(tmp_path: Path) -> None:
    created: list[_StubSDKAgent] = []

    def factory(cfg: SDKAgentConfig) -> Any:
        history = (
            [
                Step(
                    id="h:0",
                    step_index=0,
                    source=StepSource.USER,
                    status=StepStatus.DONE,
                    content="Earlier prompt",
                ),
                Step(
                    id="h:1",
                    step_index=1,
                    type=StepType.TEXT_RESPONSE,
                    source=StepSource.MODEL,
                    target=StepTarget.USER,
                    status=StepStatus.DONE,
                    content="Earlier reply",
                ),
            ]
            if cfg.conversation_id
            else []
        )
        stub = _StubSDKAgent(cfg, [[]], initial_history=history)
        created.append(stub)
        return stub

    agent = AntigravityAgent(
        api_key="test-key",
        save_dir=str(tmp_path),
        enable_logging=False,
        sdk_agent_factory=factory,
    )
    async with agent:
        modes = await agent.get_modes()
        assert [m.id for m in modes] == ["mode", "thought_level", "behavior", "model"]

        await agent.set_mode("high", "thought_level")
        await agent.set_model("gemini-2.5-pro")
        assert agent.model_name == "gemini-2.5-pro"
        assert agent._needs_restart is True

        # Next turn triggers a single lazy restart with updated settings
        await agent.run("Hello")
        assert len(created) == 2

        # Interrupt delegates to SDK conversation.cancel()
        await agent.interrupt()
        assert created[-1].conversation.cancelled is True

        # Loading a session resumes by conversation_id and populates chat history
        conv_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        loaded = await agent.load_session(conv_id)
        assert loaded is not None
        assert created[-1].config.conversation_id == conv_id
        assert [m.content for m in agent.conversation.chat_messages] == [
            "Earlier prompt",
            "Earlier reply",
        ]


async def test_auth_status_and_models(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_GENAI_USE_VERTEXAI", raising=False)
    monkeypatch.delenv("GOOGLE_GENAI_USE_ENTERPRISE", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_LOCATION", raising=False)

    unauth = AntigravityAgent(enable_logging=False)
    assert (await unauth.get_auth_status()).state == "unauthenticated"

    with_key = AntigravityAgent(api_key="key-123", enable_logging=False)
    assert (await with_key.get_auth_status()).state == "authenticated"

    vertex = AntigravityAgent(vertex=True, project="proj", location="global", enable_logging=False)
    assert (await vertex.get_auth_status()).state == "authenticated"

    local_oai = AntigravityAgent(
        openai_compatible=True, base_url="http://localhost:11434/v1", enable_logging=False
    )
    assert (await local_oai.get_auth_status()).state == "not_required"

    models = await with_key.get_available_models()
    assert models is not None
    assert any(m.id == "gemini-4-hydrogen" for m in models)


async def test_tool_bridge_exposes_agentpool_tools_via_mcp(tmp_path: Path) -> None:
    from google.antigravity.types import McpStreamableHttpServer

    from agentpool.resource_providers import StaticResourceProvider
    from agentpool.tools.base import FunctionTool

    def greet(who: str) -> str:
        """Greet someone."""
        return f"Hello {who}"

    captured: list[SDKAgentConfig] = []

    def factory(cfg: SDKAgentConfig) -> Any:
        captured.append(cfg)
        return _StubSDKAgent(cfg, [])

    provider = StaticResourceProvider(tools=[FunctionTool.from_callable(greet)])
    agent = AntigravityAgent(
        name="agy.with.tools",
        api_key="test-key",
        save_dir=str(tmp_path),
        toolsets=[provider],
        enable_logging=False,
        sdk_agent_factory=factory,
    )
    async with agent:
        assert len(captured[0].mcp_servers) == 1
        bridge_server = captured[0].mcp_servers[0]
        assert isinstance(bridge_server, McpStreamableHttpServer)
        assert bridge_server.url.startswith("http://")
        assert agent._bridge_started is True
    assert agent._bridge_started is False


async def test_missing_optional_dependency_raises_helpful_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys

    import pytest

    monkeypatch.setitem(sys.modules, "google.antigravity", None)
    agent = AntigravityAgent(api_key="test-key", enable_logging=False)
    with pytest.raises(ImportError, match=r"agentpool\[antigravity\]"):
        async with agent:
            pass
