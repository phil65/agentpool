"""v1 updates of the event converter in their ACP v2 form."""

from __future__ import annotations

from typing import Any

from aclientp.schema import (
    AgentMessageChunk,
    AgentPlanUpdate,
    AgentThoughtChunk,
    ContentToolCallContent,
    FileEditToolCallContent,
    Notice,
    PlanEntry,
    SessionReferenceToolCallContent,
    TerminalToolCallContent,
    ToolCallLocation,
    ToolCallProgress,
    ToolCallStart,
    UsageUpdate,
    v2,
)
import pytest

from agentpool_server.acp_server.v2_translate import PLAN_ID, UpdateTranslator, git_patch


def _wire(update: v2.SessionUpdate | None) -> dict[str, Any]:
    """The update as sent, checked against the v2 schema."""
    assert update is not None
    notification = v2.UpdateSessionNotification(session_id="s", update=update)
    wire = v2.to_wire(notification)
    v2.UpdateSessionNotification.model_validate(wire)  # What a v2 client accepts
    result: dict[str, Any] = wire["update"]
    return result


@pytest.fixture
def translator() -> UpdateTranslator:
    return UpdateTranslator(message_id="fallback")


def test_message_chunks_keep_or_get_a_message_id(translator: UpdateTranslator) -> None:
    with_id = translator.translate(AgentMessageChunk.text("hi", message_id="m1"))
    without = translator.translate(AgentThoughtChunk.text("hmm"))
    assert _wire(with_id) == {
        "sessionUpdate": "agent_message_chunk",
        "messageId": "m1",
        "content": {"type": "text", "text": "hi"},
    }
    assert _wire(without)["messageId"] == "fallback"
    assert _wire(without)["sessionUpdate"] == "agent_thought_chunk"


def test_tool_call_start_becomes_an_upsert(translator: UpdateTranslator) -> None:
    start = ToolCallStart(
        tool_call_id="t1",
        title="Read file",
        name="read",
        kind="read",
        status="pending",
        raw_input={"path": "/w/a.py"},
        locations=[ToolCallLocation(path="/w/a.py", line=3)],
    )
    assert _wire(translator.translate(start)) == {
        "sessionUpdate": "tool_call_update",
        "toolCallId": "t1",
        "name": "read",
        "title": "Read file",
        "kind": "read",
        "status": "pending",
        "rawInput": {"path": "/w/a.py"},
        "locations": [{"path": "/w/a.py", "line": 3}],
    }


def test_tool_call_progress_patches_only_what_it_sends(translator: UpdateTranslator) -> None:
    """Fields v1 leaves out must stay unchanged in v2, not be cleared with null."""
    progress = ToolCallProgress(tool_call_id="t1", status="completed")
    assert _wire(translator.translate(progress)) == {
        "sessionUpdate": "tool_call_update",
        "toolCallId": "t1",
        "status": "completed",
    }


def test_tool_call_content_text_and_session_reference(translator: UpdateTranslator) -> None:
    progress = ToolCallProgress(
        tool_call_id="t1",
        content=[
            ContentToolCallContent.text("done"),
            SessionReferenceToolCallContent(session_id="child"),
        ],
    )
    assert _wire(translator.translate(progress))["content"] == [
        {"type": "content", "content": {"type": "text", "text": "done"}},
        {"type": "session", "sessionId": "child"},
    ]


def test_file_edit_becomes_a_diff_with_a_git_patch(translator: UpdateTranslator) -> None:
    edit = FileEditToolCallContent(path="/w/a.py", old_text="a\n", new_text="b\n")
    new_file = FileEditToolCallContent(path="/w/new.py", old_text=None, new_text="x\n")
    wire = _wire(
        translator.translate(ToolCallProgress(tool_call_id="t1", content=[edit, new_file]))
    )
    modify, add = wire["content"]
    assert modify["changes"] == [{"path": "/w/a.py", "operation": "modify"}]
    assert modify["patch"]["format"] == "git_patch"
    assert "-a\n+b\n" in modify["patch"]["text"]
    assert add["changes"] == [{"path": "/w/new.py", "operation": "add"}]


def test_git_patch_sections() -> None:
    patch = git_patch("/w/a.py", "a\n", "b\n")
    assert patch.startswith("diff --git a/w/a.py b/w/a.py\n--- a/w/a.py\n+++ b/w/a.py\n")
    created = git_patch("/w/n.py", None, "x\n")
    assert "new file mode 100644\n--- /dev/null\n+++ b/w/n.py\n" in created


def test_terminal_content(translator: UpdateTranslator) -> None:
    progress = ToolCallProgress(
        tool_call_id="t1", content=[TerminalToolCallContent(terminal_id="x")]
    )
    assert _wire(translator.translate(progress))["content"] == [
        {"type": "terminal", "terminalId": "x"}
    ]


def test_plan_gets_a_stable_id(translator: UpdateTranslator) -> None:
    plan = AgentPlanUpdate(entries=[PlanEntry(content="Do it", priority="high", status="pending")])
    assert _wire(translator.translate(plan)) == {
        "sessionUpdate": "plan_update",
        "plan": {
            "type": "items",
            "planId": PLAN_ID,
            "entries": [{"content": "Do it", "priority": "high", "status": "pending"}],
        },
    }


@pytest.mark.parametrize(
    "update",
    [
        Notice(severity="warning", title="Retrying", description="rate limited"),
        UsageUpdate(used=10, size=100),
    ],
    ids=["notice", "usage"],
)
def test_updates_already_in_v2_shape(
    translator: UpdateTranslator, update: Notice | UsageUpdate
) -> None:
    wire = _wire(translator.translate(update))
    assert wire == update.model_dump(by_alias=True, exclude_none=True)


async def test_everything_the_converter_emits_has_a_v2_form() -> None:
    """Run a realistic event stream through the v1 converter as the v2 output does."""
    from pydantic_ai import PartDeltaEvent, PartStartEvent, TextPart, TextPartDelta, ThinkingPart

    from agentpool.agents.events import (
        CompactionEvent,
        DiffContentItem,
        NoticeEvent,
        PlanUpdateEvent,
        RetryEvent,
        RunErrorEvent,
        TextContentItem,
        ToolCallCompleteEvent,
        ToolCallProgressEvent,
        ToolCallStartEvent,
    )
    from agentpool.utils.todos import PlanEntry as AgentPlanEntry
    from agentpool_server.acp_server.event_converter import ACPEventConverter

    converter = ACPEventConverter(use_notices=True, use_compaction_updates=True)
    translator = UpdateTranslator(message_id="m")
    events = [
        PartStartEvent(part=ThinkingPart(content="thinking"), index=0),
        PartStartEvent(part=TextPart(content="Hello"), index=1),
        PartDeltaEvent(delta=TextPartDelta(content_delta=" world"), index=1),
        ToolCallStartEvent(tool_call_id="t1", tool_name="edit", title="Edit a.py"),
        ToolCallProgressEvent(
            tool_call_id="t1",
            items=[
                TextContentItem(text="editing"),
                DiffContentItem(path="/w/a.py", old_text="a\n", new_text="b\n"),
            ],
        ),
        ToolCallCompleteEvent(
            tool_name="edit",
            tool_call_id="t1",
            tool_input={},
            tool_result="ok",
            agent_name="a",
            message_id="m",
        ),
        PlanUpdateEvent(entries=[AgentPlanEntry(content="Step", priority="high")]),
        CompactionEvent(session_id="s", phase="starting"),
        RetryEvent(attempt=1, delay_ms=500, error="overloaded"),
        NoticeEvent(severity="info", title="FYI"),
        RunErrorEvent(message="boom", agent_name="a"),
    ]
    kinds: list[str] = []
    for event in events:
        kinds.extend([
            _wire(translator.translate(update))["sessionUpdate"]
            async for update in converter.convert(event)
        ])
    assert kinds == [
        "agent_thought_chunk",
        "agent_message_chunk",
        "agent_message_chunk",
        "tool_call_update",
        "tool_call_update",
        "tool_call_update",
        "plan_update",
        "compaction_update",
        "notice",
        "notice",
        "notice",
    ]


def test_location_line_zero_means_no_line(translator: UpdateTranslator) -> None:
    """v1 senders use line 0 for "no particular line"; v2 lines start at 1."""
    start = ToolCallStart(
        tool_call_id="t1", title="t", locations=[ToolCallLocation(path="/w/a.py", line=0)]
    )
    assert _wire(translator.translate(start))["locations"] == [{"path": "/w/a.py"}]


def test_config_options(translator: UpdateTranslator) -> None:
    from aclientp.schema import (
        BooleanSessionConfigOption,
        ConfigOptionUpdate,
        SelectSessionConfigOption,
        SessionConfigSelectGroup,
        SessionConfigSelectOption,
    )

    flat = SelectSessionConfigOption(
        id="model",
        name="Model",
        category="model",
        current_value="haiku",
        options=[SessionConfigSelectOption(value="haiku", name="Haiku", description="fast")],
    )
    grouped = SelectSessionConfigOption(
        id="mode",
        name="Mode",
        current_value="plan",
        options=[
            SessionConfigSelectGroup(
                group="safe",
                name="Safe",
                options=[SessionConfigSelectOption(value="plan", name="Plan")],
            )
        ],
    )
    flag = BooleanSessionConfigOption(id="fast", name="Fast", current_value=True)
    wire = _wire(translator.translate(ConfigOptionUpdate(config_options=[flat, grouped, flag])))
    assert wire["configOptions"] == [
        {
            "configId": "model",
            "name": "Model",
            "category": "model",
            "type": "select",
            "currentValue": "haiku",
            "options": [{"value": "haiku", "name": "Haiku", "description": "fast"}],
        },
        {
            "configId": "mode",
            "name": "Mode",
            "type": "select",
            "currentValue": "plan",
            "options": [
                {"groupId": "safe", "name": "Safe", "options": [{"value": "plan", "name": "Plan"}]}
            ],
        },
        {"configId": "fast", "name": "Fast", "type": "boolean", "currentValue": True},
    ]


def test_commands(translator: UpdateTranslator) -> None:
    from aclientp.schema import AvailableCommand, AvailableCommandsUpdate

    commands = [
        AvailableCommand.create(name="help", description="Show help"),
        AvailableCommand.create(name="git-diff", description="Diff", input_hint="commit"),
    ]
    wire = _wire(translator.translate(AvailableCommandsUpdate(available_commands=commands)))
    assert wire["availableCommands"] == [
        {"type": "command", "name": "help", "description": "Show help"},
        {
            "type": "command",
            "name": "git-diff",
            "description": "Diff",
            "input": {"type": "text", "hint": "commit"},
        },
    ]
