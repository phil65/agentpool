"""Backend-specific options pass through to the agents' SDKs (``sdk_options``, ...)."""

from typing import Any

from clawd_code_sdk.models.mcp import SdkPluginConfig
import pytest

from agentpool.agents.antigravity_agent import AntigravityAgent
from agentpool.agents.claude_code_agent import ClaudeCodeAgent
from agentpool.agents.codex_agent import CodexAgent


def test_claude_code_options_reach_the_sdk() -> None:
    agent = ClaudeCodeAgent(
        name="cc",
        sdk_options={
            "exclude_dynamic_sections": True,
            "plugins": [{"path": "/plugins/p", "skipMcpDiscovery": True}],
        },
    )
    options = agent._get_client().options
    assert options.exclude_dynamic_sections is True
    assert options.plugins == [SdkPluginConfig(path="/plugins/p", skip_mcp_discovery=True)]
    assert options.permission_mode == "default"  # agentpool's own setting still applies


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"no_such_option": 1}, "has no option 'no_such_option'"),
        ({"model": "opus"}, "'model' is set by agentpool"),
        ({"on_permission": None}, "'on_permission' is set by agentpool"),
        ({"max_buffer_size": "lots"}, "option 'max_buffer_size'"),
    ],
)
def test_claude_code_refuses_unknown_managed_and_mistyped_options(
    options: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        ClaudeCodeAgent(name="cc", sdk_options=options)


class _CodexClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def thread_start(self, **kwargs: Any) -> Any:
        self.calls.append(("start", kwargs))
        raise _Stop

    async def thread_resume(
        self,
        thread_id: str,
        *,
        mcp_servers: Any = None,
        service_tier: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> Any:
        self.calls.append(("resume", {"service_tier": service_tier, "config": config}))
        raise _Stop


class _Stop(Exception):  # noqa: N818
    """Ends the call once its arguments are recorded."""


async def test_codex_thread_options_go_to_start_and_resume() -> None:
    agent = CodexAgent(
        name="cx", thread_options={"service_tier": "flex", "config": {"model_verbosity": "low"}}
    )
    client = _CodexClient()
    agent._client = client  # type: ignore[assignment]
    with pytest.raises(_Stop):
        await agent._start_thread()
    with pytest.raises(_Stop):
        await agent._resume_thread("t1")
    (_, started), (_, resumed) = client.calls
    assert started["service_tier"] == "flex"
    assert started["config"] == {"model_verbosity": "low"}
    assert resumed["service_tier"] == "flex"


def test_codex_refuses_managed_thread_options() -> None:
    with pytest.raises(ValueError, match="'sandbox' is set by agentpool"):
        CodexAgent(name="cx", thread_options={"sandbox": "read-only"})


def test_antigravity_options_are_converted_to_the_sdk_types() -> None:
    from google.antigravity.types import RetryConfig

    agent = AntigravityAgent(
        api_key="k", sdk_options={"retry_config": {"api_retry": {"max_retries": 5}}}
    )
    retry = agent._sdk_options["retry_config"]
    assert isinstance(retry, RetryConfig)
    assert retry.api_retry is not None
    assert retry.api_retry.max_retries == 5
    with pytest.raises(ValueError, match="'policies' is set by agentpool"):
        AntigravityAgent(api_key="k", sdk_options={"policies": []})
