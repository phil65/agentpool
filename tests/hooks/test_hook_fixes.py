"""Command and prompt hook fixes (issue #59.4)."""

import asyncio
import sys
from typing import TYPE_CHECKING, Any

from exxec.local_provider import LocalExecutionEnvironment
import pytest

from agentpool.hooks.command import CommandHook, _parse_success_output
from agentpool.hooks.prompt import PromptHook


if TYPE_CHECKING:
    from pathlib import Path


pytestmark = [
    pytest.mark.timeout(60),
    pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell commands"),
]


def test_json_without_decision_keeps_its_fields() -> None:
    result = _parse_success_output('{"additionalContext": "ctx", "reason": "r"}')
    assert result["decision"] == "allow"
    assert result.get("additional_context") == "ctx"
    assert result.get("reason") == "r"


async def test_hook_env_applies_with_the_agents_environment() -> None:
    hook = CommandHook(
        "pre_run",
        """echo "{\\"additionalContext\\": \\"$HOOK_VALUE\\"}" """,
        env={"HOOK_VALUE": "x y"},
    )
    agent_env = LocalExecutionEnvironment(inherit_env=True)
    result = await hook.execute({"event": "pre_run"}, env=agent_env)
    assert result.get("additional_context") == "x y"


async def test_project_dir_with_spaces_is_quoted(tmp_path: Path) -> None:
    project = tmp_path / "my project"
    project.mkdir()
    (project / "marker").write_text("found")
    hook = CommandHook("pre_run", "cat $PROJECT_DIR/marker", env={"PROJECT_DIR": str(project)})
    result = await hook.execute({"event": "pre_run"})
    assert result.get("additional_context") == "found"


async def test_prompt_hook_enforces_its_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    hook = PromptHook("pre_run", "Decide: $PROMPT", timeout=0.1)

    async def slow(prompt: str) -> Any:
        await asyncio.sleep(30)

    monkeypatch.setattr(hook, "_query_llm", slow)
    result = await asyncio.wait_for(hook.execute({"event": "pre_run"}), 10)
    assert result["decision"] == "allow"  # Fails open, like other hook errors
