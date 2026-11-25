"""FSSpecTools edit tools and grep (#52.1, #52.2, #52.4, #52.5)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from fsspec.implementations.memory import MemoryFileSystem
from pydantic_ai import (
    ModelRequest,
    ModelResponse,
    ModelRetry,
    PartDeltaEvent,
    TextPartDelta,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
import pytest
from upathtools import to_async_fs

from agentpool import Agent, AgentContext
from agentpool.tool_impls.grep import create_grep_tool
from agentpool_toolsets.fsspec_toolset import FSSpecTools, grep as grep_module
from agentpool_toolsets.fsspec_toolset.grep import (
    _parse_gnu_grep_output,
    grep_with_fsspec,
    grep_with_subprocess,
)


if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from agentpool.messaging import MessageHistory


ORIGINAL = "def a():\n    return 1\n\n\ndef b():\n    return 2\n"
# Two hunks: the first completes while streaming, the second only in parser.finish()
DIFF = (
    "<diff>\n def a():\n-    return 1\n+    return 10\n\n"
    " def b():\n-    return 2\n+    return 20\n</diff>"
)


@pytest.fixture
def memfs() -> MemoryFileSystem:
    fs = MemoryFileSystem(skip_instance_cache=True)
    fs.store = {}
    fs.pseudo_dirs = [""]
    fs.mkdir("/mem")
    fs.pipe("/mem/code.py", ORIGINAL.encode())
    return fs


@pytest.fixture
def agent() -> Agent[None]:
    return Agent[None](name="fs_agent", model="test")


@pytest.fixture
def agent_ctx(agent: Agent[None]) -> AgentContext:
    return AgentContext(node=agent, tool_call_id="call_1", tool_name="t", tool_input={})


def _fake_run_stream(text: str, seen_histories: list[MessageHistory]) -> Any:
    async def run_stream(
        prompt: str, *, message_history: MessageHistory, **_: Any
    ) -> AsyncIterator[Any]:
        seen_histories.append(message_history)
        for i in range(0, len(text), 7):  # stream in small chunks
            yield PartDeltaEvent(index=0, delta=TextPartDelta(content_delta=text[i : i + 7]))

    return run_stream


async def test_streamed_agentic_edit_applies_all_hunks(memfs, agent, agent_ctx, monkeypatch):
    """Hunks completed during streaming are kept, not dropped (#52.1)."""
    histories: list[MessageHistory] = []
    monkeypatch.setattr(agent, "run_stream", _fake_run_stream(DIFF, histories))
    toolset = FSSpecTools(memfs, edit_tool="agentic")
    run_ctx: Any = SimpleNamespace(messages=[])
    result = await toolset.agentic_edit(run_ctx, agent_ctx, "/mem/code.py", "bump returns")
    assert result.startswith("Successfully edited"), result
    content = memfs.cat_file("/mem/code.py").decode()
    assert "return 10" in content
    assert "return 20" in content


async def test_agentic_edit_forks_current_conversation(memfs, agent, agent_ctx, monkeypatch):
    """The edit agent sees the current run's messages (#52.2), minus unanswered calls."""
    histories: list[MessageHistory] = []
    monkeypatch.setattr(agent, "run_stream", _fake_run_stream(DIFF, histories))
    toolset = FSSpecTools(memfs, edit_tool="agentic")
    run_ctx: Any = SimpleNamespace(
        messages=[
            ModelRequest(parts=[UserPromptPart(content="please use the secret word banana")]),
            ModelResponse(parts=[ToolCallPart(tool_name="read", args={}, tool_call_id="r1")]),
            ModelRequest(parts=[ToolReturnPart(tool_name="read", content="x", tool_call_id="r1")]),
            ModelResponse(
                parts=[
                    ToolCallPart(tool_name="agentic_edit", args={}, tool_call_id="e1"),
                    ToolCallPart(tool_name="other", args={}, tool_call_id="o1"),
                ]
            ),
        ]
    )
    await toolset.agentic_edit(run_ctx, agent_ctx, "/mem/code.py", "bump returns")
    [history] = histories
    messages = [m for chat_msg in history.get_history() for m in chat_msg.messages]
    assert len(messages) == 3
    assert "banana" in str(messages[0])
    call_ids = {
        p.tool_call_id
        for m in messages
        if isinstance(m, ModelResponse)
        for p in m.parts
        if isinstance(p, ToolCallPart)
    }
    assert call_ids == {"r1"}


async def test_regex_replace_allows_caret_anchor(memfs, agent_ctx):
    """`^` matches once per line, so it is accepted (#52.4a)."""
    toolset = FSSpecTools(memfs)
    result = await toolset.regex_replace_lines(agent_ctx, "/mem/code.py", 1, 2, r"^", "# ")
    assert result.startswith("Successfully"), result
    assert memfs.cat_file("/mem/code.py").decode().startswith("# def a():\n#     return 1\n")


@pytest.mark.parametrize("pattern", [r".*", r"$", r"\d*"])
async def test_regex_replace_empty_matches_raise_model_retry(memfs, agent_ctx, pattern: str):
    """Patterns matching twice per line raise ModelRetry instead of a plain string (#52.4b)."""
    toolset = FSSpecTools(memfs)
    with pytest.raises(ModelRetry):
        await toolset.regex_replace_lines(agent_ctx, "/mem/code.py", 1, 2, pattern, "x")
    assert memfs.cat_file("/mem/code.py").decode() == ORIGINAL


class _RecordingEnv:
    def __init__(self, stdout: str = "") -> None:
        self.calls: list[tuple[str, float | None]] = []
        self.stdout = stdout
        self.cwd: str | None = None

    async def execute_command(self, command: str, *, timeout: float | None = None) -> Any:
        self.calls.append((command, timeout))
        return SimpleNamespace(stdout=self.stdout, stderr="", exit_code=0)


async def test_grep_subprocess_passes_file_pattern_and_timeout():
    """file_pattern becomes --glob / --include, timeout reaches the command (#52.5a/b)."""
    env: Any = _RecordingEnv()
    await grep_with_subprocess(
        env,
        "needle",
        "/src",
        backend="ripgrep",
        file_pattern="**/*.py",
        timeout=7,
    )
    await grep_with_subprocess(
        env,
        "needle",
        "/src",
        backend="grep",
        file_pattern="**/*.py",
        timeout=7,
    )
    (rg_cmd, rg_timeout), (grep_cmd, grep_timeout) = env.calls
    assert "--glob '**/*.py'" in rg_cmd
    assert rg_cmd.index("'**/*.py'") < rg_cmd.index("!.venv/")  # excludes take precedence
    assert "'--include=*.py'" in grep_cmd
    assert rg_timeout == grep_timeout == 7


async def test_grep_subprocess_directory_glob_falls_back():
    """GNU grep can't filter by directory: report an error so callers use fsspec."""
    env: Any = _RecordingEnv()
    result = await grep_with_subprocess(
        env,
        "needle",
        "/src",
        backend="grep",
        file_pattern="pkg/**/*.py",
    )
    assert "error" in result
    assert not env.calls


async def test_grep_tool_passes_file_pattern(agent_ctx, monkeypatch):
    """GrepTool forwards file_pattern to the subprocess backend (#52.5a)."""
    seen: dict[str, Any] = {}

    async def fake_grep(**kwargs: Any) -> dict[str, Any]:
        seen.update(kwargs)
        return {"matches": "", "match_count": 0, "was_truncated": False}

    monkeypatch.setattr(grep_module, "grep_with_subprocess", fake_grep)
    env: Any = _RecordingEnv()
    tool = create_grep_tool(env=env)
    tool._grep_backend = "ripgrep"
    await tool.execute_and_unwrap(
        ctx=agent_ctx, pattern="needle", path="/src", file_pattern="**/*.md"
    )
    assert seen["file_pattern"] == "**/*.md"


def test_gnu_grep_output_keeps_context_lines():
    """Context lines (`-` separator) are parsed, file names with dashes too (#52.5c)."""
    stdout = "my-file.py\x001-before\nmy-file.py\x002:needle here\nmy-file.py\x003-after\n--\n"
    result = _parse_gnu_grep_output(stdout, 10_000)
    assert result["match_count"] == 1
    table = result["matches"]
    assert "| my-file.py | 1 | before |" in table
    assert "| my-file.py | 2 | → needle here |" in table
    assert "| my-file.py | 3 | after |" in table


async def test_fsspec_grep_context_lines(memfs):
    """grep_with_fsspec returns context lines around matches (#52.5d)."""
    memfs.pipe("/mem/notes.txt", b"one\ntwo\nneedle\nfour\nfive\n")
    fs = to_async_fs(memfs)
    result = await grep_with_fsspec(fs, "needle", "/mem", file_pattern="*.txt", context_lines=1)
    assert result["match_count"] == 1
    table = result["matches"]
    assert "| 2 | two |" in table
    assert "| 3 | → needle |" in table
    assert "| 4 | four |" in table
    assert "one" not in table
    assert "five" not in table
