"""Demo of ctx-zip style code generation approach.

This example shows how to use code generation instead of HTTP servers
to make tools available in sandbox environments, especially useful for
cloud sandboxes like E2B that can't reach localhost.
"""

from exxec_config import LocalExecutionEnvironmentConfig

from agentpool import Agent
from agentpool.resource_providers import StaticResourceProvider
from agentpool.resource_providers.codemode import RemoteCodeModeResourceProvider
from agentpool.tools.base import FunctionTool


def add_numbers(x: int, y: int) -> int:
    """Add two numbers together.

    Args:
        x: First number
        y: Second number
    """
    return x + y


def multiply_numbers(x: int, y: int) -> int:
    """Multiply two numbers together.

    Args:
        x: First number
        y: Second number
    """
    return x * y


async def fetch_weather(city: str, country: str = "US") -> dict:
    """Fetch weather information for a city.

    Args:
        city: Name of the city
        country: Country code (default: US)
    """
    return {"city": city, "country": country, "temperature": 22}


async def demo_http_server_approach():
    """Demo original HTTP server approach (works with local/docker)."""
    print("=== HTTP Server Approach ===")
    tools = [
        FunctionTool.from_callable(add_numbers),
        FunctionTool.from_callable(multiply_numbers),
        FunctionTool.from_callable(fetch_weather),
    ]

    config = LocalExecutionEnvironmentConfig()
    toolset = RemoteCodeModeResourceProvider(
        providers=[StaticResourceProvider(tools=tools)], execution_config=config
    )
    agent = Agent(model="openai:gpt-6-luna", toolsets=[toolset])
    async with agent:
        result = await agent.run(
            "What code is available for your execute tool? Which calls can you make?"
        )
        print(result.format())


if __name__ == "__main__":
    import anyio

    anyio.run(demo_http_server_approach)
