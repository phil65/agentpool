"""agentpool imports without tokonomics' Claude model names (1.5.0 dropped them)."""

import subprocess
import sys


def test_imports_without_tokonomics_claude_model_names() -> None:
    code = (
        "import tokonomics.model_names as names\n"
        "names.__dict__.pop('AnthropicMaxModelName', None)\n"
        "import agentpool.models.claude_code_agents\n"
        "import agentpool.agents.claude_code_agent.claude_code_agent\n"
        "from agentpool.models.claude_code_agents import ClaudeCodeAgentConfig\n"
        "ClaudeCodeAgentConfig.model_json_schema()\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False, timeout=120
    )
    assert result.returncode == 0, result.stderr[-2000:]
