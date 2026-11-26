from pydantic_ai import ImageUrl
import pytest

from agentpool import Agent


@pytest.mark.integration
@pytest.mark.flaky(reruns=2)
async def test_vision(vision_model: str):
    """Test basic vision capability with a small, public image."""
    agent = Agent(name="test-vision", model=vision_model)
    # Using a small, public image
    msg = "https://python.org/static/community_logos/python-logo-master-v3-TM.png"
    image = ImageUrl(url=msg)
    msg = "What does this image show? Answer in one short sentence."
    result = await agent.run(msg, image)

    assert isinstance(result.content, str)
    assert "Python" in result.content
    assert len(result.content) < 120


if __name__ == "__main__":
    pytest.main([__file__])
