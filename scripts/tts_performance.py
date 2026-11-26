"""Manual script to test TTS synchronization modes.

Run with: uv run python scripts/tts_performance.py

Requires:
- For OpenAI TTS: OPENAI_API_KEY environment variable
- For Edge TTS: No API key needed (install with `uv pip install edge-tts miniaudio`)
"""

import time
from typing import TYPE_CHECKING, Any, Literal

import anyio
from pydantic_ai import PartDeltaEvent, PartStartEvent, TextPart, TextPartDelta

from agentpool import Agent
from agentpool_config.event_handlers import EdgeTTSEventHandlerConfig, TTSEventHandlerConfig


if TYPE_CHECKING:
    from anyvoice import TTSMode

    from agentpool.agents.context import AgentContext
    from agentpool.agents.events import RichAgentStreamEvent


TTSProvider = Literal["openai", "edge"]


async def print_handler(ctx: AgentContext[Any], event: RichAgentStreamEvent[Any]) -> None:
    """Print text deltas as they arrive."""
    match event:
        case PartStartEvent(part=TextPart(content=text)):
            print(text, end="", flush=True)
        case PartDeltaEvent(delta=TextPartDelta(content_delta=text)):
            print(text, end="", flush=True)


def get_tts_handler(provider: TTSProvider, mode: TTSMode):
    """Create TTS handler for specified provider and mode."""
    if provider == "openai":
        config = TTSEventHandlerConfig(
            model="tts-1",
            voice="alloy",
            min_text_length=10,
            mode=mode,
        )
    else:  # edge
        config = EdgeTTSEventHandlerConfig(voice="en-US-AriaNeural", min_text_length=10, mode=mode)
    return config.get_handler()


async def run_single(mode: TTSMode, provider: TTSProvider = "openai") -> float:
    """Run a single agent request with specified TTS mode."""
    print(f"\n{'=' * 60}")
    print(f"Provider: {provider.upper()} | Mode: {mode}")
    print("=" * 60)

    handler = get_tts_handler(provider, mode)

    agent = Agent(
        name="test-agent",
        model="openai:gpt-4.1-nano",
        system_prompt="You are a storyteller. Keep responses to 2-3 sentences.",
    )

    prompt = "Tell me a very short story about a robot."

    print(f"Prompt: {prompt}\n")
    print("Response: ", end="", flush=True)

    start_time = time.perf_counter()

    async with agent:
        ctx = agent.get_context()
        async for event in agent.run_stream(prompt):
            await print_handler(ctx, event)
            await handler(ctx, event)  # pyright: ignore[reportArgumentType]

    stream_time = time.perf_counter() - start_time
    print(f"\n\nStream completed in: {stream_time:.2f}s")

    return stream_time


async def run_sequential(mode: TTSMode, provider: TTSProvider = "openai") -> None:
    """Run two sequential agent requests with the same TTS handler."""
    print(f"\n{'=' * 60}")
    print(f"SEQUENTIAL RUNS - Provider: {provider.upper()} | Mode: {mode}")
    print("=" * 60)

    handler = get_tts_handler(provider, mode)

    agent = Agent(
        name="test-agent",
        model="openai:gpt-4.1-nano",
        system_prompt="You are a storyteller. Keep responses to 2-3 sentences.",
    )

    prompts = [
        "Tell me a very short story about a cat.",
        "Now tell me a very short story about a dog.",
    ]

    async with agent:
        for i, prompt in enumerate(prompts, 1):
            print(f"\n--- Run {i} ---")
            print(f"Prompt: {prompt}\n")
            print("Response: ", end="", flush=True)

            start_time = time.perf_counter()

            async for event in agent.run_stream(prompt):
                ctx = agent.get_context()
                await print_handler(ctx, event)
                await handler(ctx, event)  # pyright: ignore[reportArgumentType]

            stream_time = time.perf_counter() - start_time
            print(f"\n\nStream completed in: {stream_time:.2f}s")

    print("\n" + "=" * 60)
    print("Both runs completed!")


async def compare_all_modes(provider: TTSProvider = "openai") -> None:
    """Compare all 4 TTS modes."""
    print(f"Comparing all TTS synchronization modes ({provider.upper()})")
    print("=" * 60)

    results: dict[TTSMode, float] = {}

    for mode in ("sync_sentence", "sync_run", "async_queue", "async_cancel"):
        input(f"\nPress Enter to test mode: {mode}")
        results[mode] = await run_single(mode, provider)
        await anyio.sleep(2)

    print("\n" + "=" * 60)
    print("RESULTS")
    print("=" * 60)
    for mode, stream_time in results.items():
        print(f"{mode:20s}: {stream_time:.2f}s")


async def compare_providers() -> None:
    """Compare OpenAI vs Edge TTS."""
    print("Comparing TTS Providers")
    print("=" * 60)

    mode: TTSMode = "sync_run"

    input("\nPress Enter to test OpenAI TTS...")
    openai_time = await run_single(mode, "openai")
    await anyio.sleep(2)

    input("\nPress Enter to test Edge TTS...")
    edge_time = await run_single(mode, "edge")

    print("\n" + "=" * 60)
    print("PROVIDER COMPARISON (sync_run mode)")
    print("=" * 60)
    print(f"OpenAI TTS: {openai_time:.2f}s")
    print(f"Edge TTS:   {edge_time:.2f}s")


async def main():
    print("TTS Synchronization Mode Test")
    print("=" * 60)
    print("\nProviders:")
    print("  OpenAI - Requires OPENAI_API_KEY (paid)")
    print("  Edge   - Free, no API key needed")
    print("\nModes:")
    print("  1. sync_sentence - Block per sentence (audio syncs with text)")
    print("  2. sync_run      - Block at run end (fast stream, wait for audio)")
    print("  3. async_queue   - Fully async, runs queue up")
    print("  4. async_cancel  - Fully async, new run cancels previous")
    print("\nTests:")
    print("  a. Compare all modes (OpenAI)")
    print("  b. Compare all modes (Edge)")
    print("  c. Compare providers (OpenAI vs Edge)")
    print("  d. Sequential runs - sync_run (OpenAI)")
    print("  e. Sequential runs - sync_run (Edge)")
    print("  f. Sequential runs - async_queue (Edge)")
    print("  g. Sequential runs - async_cancel (Edge)")

    choice = input("\nEnter choice (1-4 for single mode, a-g for tests): ").strip().lower()

    mode_map: dict[str, TTSMode] = {
        "1": "sync_sentence",
        "2": "sync_run",
        "3": "async_queue",
        "4": "async_cancel",
    }

    if choice in mode_map:
        provider = input("Provider (openai/edge) [openai]: ").strip().lower()
        await run_single(mode_map[choice], provider="openai" if provider == "openai" else "edge")
    elif choice == "a":
        await compare_all_modes("openai")
    elif choice == "b":
        await compare_all_modes("edge")
    elif choice == "c":
        await compare_providers()
    elif choice == "d":
        await run_sequential("sync_run", "openai")
    elif choice == "e":
        await run_sequential("sync_run", "edge")
    elif choice == "f":
        await run_sequential("async_queue", "edge")
    elif choice == "g":
        await run_sequential("async_cancel", "edge")
    else:
        print("Invalid choice")


if __name__ == "__main__":
    anyio.run(main)
