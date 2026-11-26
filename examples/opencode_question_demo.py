"""Demo script showing OpenCode question functionality.

This demonstrates how the question tool works with OpenCode's question system.
"""

import asyncio

from agentpool_server.opencode_server.input_provider import OpenCodeInputProvider
from agentpool_server.opencode_server.state import ServerState
from mcp import types

from agentpool import Agent


async def demo_single_select():
    """Demo single-select question."""
    print("\n=== Single-Select Question Demo ===")
    # Create minimal state
    state = ServerState(working_dir="/tmp", agent=Agent(model="test"))
    provider = OpenCodeInputProvider(state=state, session_id="demo_session")
    # Create question
    params = types.ElicitRequestFormParams(
        message="Which database would you like to use?",
        requested_schema={
            "type": "string",
            "enum": ["PostgreSQL", "MySQL", "SQLite"],
            "x-option-descriptions": {
                "PostgreSQL": "Best for production workloads",
                "MySQL": "Compatible with many tools",
                "SQLite": "Lightweight, file-based database",
            },
        },
    )

    # Start question
    async def get_answer():
        return await provider.get_elicitation(params)

    task = asyncio.create_task(get_answer())
    await asyncio.sleep(0.1)  # Let question be created
    # Show pending question
    question_id = next(iter(state.pending_questions.keys()))
    pending = state.pending_questions[question_id]
    print(f"\nQuestion ID: {question_id}")
    print(f"Question: {pending.questions[0].question}")
    print("Options:")
    for opt in pending.questions[0].options:
        print(f"  - {opt.label}: {opt.description}")
    # Simulate user selecting PostgreSQL
    print("\n→ User selects: PostgreSQL")
    provider.resolve_question(question_id, [["PostgreSQL"]])
    result = await task
    print(f"\nResult: {result}")
    print(f"Content: {result.content}")  # pyright: ignore[reportAttributeAccessIssue]


async def demo_multi_select():
    """Demo multi-select question."""
    print("\n\n=== Multi-Select Question Demo ===")

    state = ServerState(working_dir="/tmp", agent=Agent(model="test"))
    provider = OpenCodeInputProvider(state=state, session_id="demo_session")
    params = types.ElicitRequestFormParams(
        message="Which features would you like to enable?",
        requested_schema={
            "type": "array",
            "items": {
                "type": "string",
                "enum": ["Authentication", "REST API", "Admin Panel", "Analytics"],
            },
            "x-option-descriptions": {
                "Authentication": "User login and registration",
                "REST API": "RESTful API endpoints",
                "Admin Panel": "Administrative dashboard",
                "Analytics": "Usage tracking and reporting",
            },
        },
    )

    task = asyncio.create_task(provider.get_elicitation(params))
    await asyncio.sleep(0.1)
    # Show question
    question_id = next(iter(state.pending_questions.keys()))
    pending = state.pending_questions[question_id]
    question_info = pending.questions[0]
    print(f"\nQuestion: {question_info.question}")
    print(f"Multi-select: {question_info.multiple}")
    print("Options:")
    for opt in question_info.options:
        print(f"  - {opt.label}: {opt.description}")
    # Simulate user selecting multiple options
    print("\n→ User selects: Authentication, REST API, Analytics")
    provider.resolve_question(question_id, [["Authentication", "REST API", "Analytics"]])
    result = await task
    assert isinstance(result, types.ElicitResult)
    print(f"\nResult: {result}")
    print(f"Content: {result.content}")


async def demo_cancellation():
    """Demo question cancellation."""
    print("\n\n=== Cancellation Demo ===")
    state = ServerState(working_dir="/tmp", agent=Agent(model="test"))
    provider = OpenCodeInputProvider(state=state, session_id="demo_session")
    params = types.ElicitRequestFormParams(
        message="Choose a deployment target",
        requested_schema={"type": "string", "enum": ["AWS", "Azure", "GCP"]},
    )
    task = asyncio.create_task(provider.get_elicitation(params))
    await asyncio.sleep(0.1)
    # Show question
    question_id = next(iter(state.pending_questions.keys()))
    print(f"\nQuestion ID: {question_id}")
    # Simulate user pressing Esc
    print("→ User presses Esc (cancel)")
    future = state.pending_questions[question_id].future
    future.cancel()
    result = await task
    assert isinstance(result, types.ElicitResult)
    print(f"\nResult: {result}")
    print(f"Action: {result.action}")


async def main():
    """Run all demos."""
    await demo_single_select()
    await demo_multi_select()
    await demo_cancellation()
    print("\n\n=== Demo Complete ===")
    print("\nKey Points:")
    print("1. Questions use OpenCode's structured format with options")
    print("2. Single-select returns one value, multi-select returns array")
    print("3. Content is wrapped in dict with 'value' key per MCP spec")
    print("4. User can cancel questions (returns action='cancel')")
    print("5. Events are broadcast to OpenCode TUI for real-time UI updates")


if __name__ == "__main__":
    asyncio.run(main())
