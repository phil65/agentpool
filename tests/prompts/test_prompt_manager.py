import pytest
import yaml

from agentpool import AgentsManifest
from agentpool.prompts.manager import PromptManager


TEST_CONFIG = """
prompts:
  system_prompts:
    simple_prompt:
      content: "This is a simple prompt"
      category: role

    template_prompt:
      content: "Hello {{ name }}, welcome to {{ place }}!"
      category: role

    complex_prompt:
      content: "You are a {{ role }} specialized in {{ domain }}."
      category: methodology

agents: {}  # Empty but required
"""


async def test_builtin_provider():
    # Load test config
    config = yaml.safe_load(TEST_CONFIG)
    manifest = AgentsManifest.model_validate(config)
    prompt_manager = PromptManager(manifest.prompts)

    # Test simple prompt
    result = await prompt_manager.get("simple_prompt")
    assert result == "This is a simple prompt"

    # Test prompt with variables
    result = await prompt_manager.get("template_prompt?name=Alice,place=Wonderland")
    assert result == "Hello Alice, welcome to Wonderland!"

    # Test non-existent prompt
    with pytest.raises(RuntimeError, match="Failed to get prompt"):
        await prompt_manager.get("non_existent_prompt")

    # Test invalid variable reference
    with pytest.raises(RuntimeError, match="Failed to get prompt"):
        await prompt_manager.get("template_prompt?invalid=value")

    # Test prompt without required variables
    with pytest.raises(RuntimeError, match="Failed to get prompt"):
        await prompt_manager.get("template_prompt")

    # Test listing prompts
    prompts = await prompt_manager.list_prompts("builtin")
    assert "builtin" in prompts
    assert set(prompts["builtin"]) == {
        "simple_prompt",
        "template_prompt",
        "complex_prompt",
    }

    # Test listing non-existent provider
    with pytest.raises(KeyError):
        await prompt_manager.list_prompts("non_existent")

    # Test default provider
    result = await prompt_manager.get("simple_prompt")  # no provider prefix
    assert result == "This is a simple prompt"


if __name__ == "__main__":
    pytest.main([__file__, "-vv"])
