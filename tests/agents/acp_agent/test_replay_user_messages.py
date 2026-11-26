"""History replay shows what the user wrote: not injected context, not tool content."""

from aclientp.schema import TextContentBlock, ToolCallProgress, UserMessageChunk
from pydantic_ai import ModelRequest, ToolReturnPart, UserPromptPart

from agentpool.agents.acp_agent.acp_converters import model_messages_to_session_updates
from agentpool.agents.staged_content import injected_context, is_injected


def _user_texts(request: ModelRequest) -> list[str]:
    return [
        u.content.text
        for u in model_messages_to_session_updates([request])
        if isinstance(u, UserMessageChunk) and isinstance(u.content, TextContentBlock)
    ]


def test_injected_context_is_not_replayed() -> None:
    rules = injected_context("\n<context>\n# Rules\n</context>\n")
    request = ModelRequest(parts=[UserPromptPart(content=["Read the README", rules])])
    assert _user_texts(request) == ["Read the README"]


def test_context_stored_as_plain_text_is_not_replayed() -> None:
    """Sessions stored before injected context was marked."""
    legacy = "\n<context>\n# Rules\n</context>\n"
    assert is_injected(legacy)
    assert _user_texts(ModelRequest(parts=[UserPromptPart(content=["hi", legacy])])) == ["hi"]
    assert _user_texts(ModelRequest(parts=[UserPromptPart(content=legacy)])) == []


def test_tool_content_is_not_a_user_message_but_steered_messages_are() -> None:
    tool_content = (
        "# README\n\nHello.\n\n<injected-context>\nAlso check the tests\n</injected-context>"
    )
    request = ModelRequest(
        parts=[
            ToolReturnPart(tool_name="read", content="# README", tool_call_id="c1"),
            UserPromptPart(content=[tool_content]),
        ]
    )
    updates = list(model_messages_to_session_updates([request]))

    assert any(isinstance(u, ToolCallProgress) for u in updates)
    assert _user_texts(request) == ["Also check the tests"]


def test_user_text_is_never_mistaken_for_context() -> None:
    assert not is_injected("Please explain <context> tags")
    assert not is_injected("hello")
