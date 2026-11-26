"""Example demonstrating remote AG-UI agent usage.

This example shows how to connect to and use a remote AG-UI protocol server.
"""

import anyio
from pydantic_ai import PartDeltaEvent, TextPartDelta

from agentpool.agents import AGUIAgent


endpoint = "http://localhost:8000/agent/run"


async def basic_usage():
    """Basic AG-UI agent usage."""
    print("=== Basic Usage ===\n")
    async with AGUIAgent(endpoint=endpoint, name="remote-assistant", timeout=30.0) as agent:
        result = await agent.run("What is the capital of France?")
        print("Q: What is the capital of France?")
        print(f"A: {result.content}\n")
        stats = await agent.get_stats()
        print(f"Stats: {stats}\n")


async def streaming_usage():
    """Streaming AG-UI agent usage."""
    print("=== Streaming Usage ===\n")

    async with AGUIAgent(endpoint=endpoint, name="streaming-assistant") as agent:
        print("Q: Tell me a short story about AI\n")
        print("A: ", end="", flush=True)
        # Stream the response
        async for event in agent.run_stream("Tell me a short story about AI"):
            if isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
                print(event.delta.content_delta, end="", flush=True)

        print("\n")


async def tool_conversion():
    """Convert AG-UI agent to a callable tool."""
    print("=== Tool Conversion ===\n")
    async with AGUIAgent(endpoint=endpoint, name="calculator-agent") as agent:
        calculator_tool = agent.to_tool()
        result = await calculator_tool.run(prompt="What is 157 * 89?")
        print(f"Calculator result: {result}\n")


async def multiple_requests():
    """Execute multiple requests with same agent."""
    print("=== Multiple Requests ===\n")
    async with AGUIAgent(endpoint=endpoint, name="multi-request-agent") as agent:
        questions = ["What is 2+2?", "Name three primary colors", "What is the speed of light?"]
        for i, question in enumerate(questions, 1):
            result = await agent.run(question)
            print(f"{i}. Q: {question}")
            print(f"   A: {result.content}\n")


async def main():
    """Run all examples."""
    examples = [
        ("Basic Usage", basic_usage),
        ("Streaming", streaming_usage),
        ("Tool Conversion", tool_conversion),
        ("Multiple Requests", multiple_requests),
    ]

    print("AG-UI Remote Agent Examples")
    print("=" * 50)
    print()

    for name, example_func in examples:
        try:
            await example_func()
        except (OSError, TimeoutError, RuntimeError) as e:
            print(f"Example '{name}' failed: {e}\n")
        await anyio.sleep(0.5)


if __name__ == "__main__":
    anyio.run(main)
