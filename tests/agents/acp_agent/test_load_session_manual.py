"""Manual test for load_session with ACP agent connecting to Claude Code via ACP server.

Run with: uv run python tests/agents/acp_agent/test_load_session_manual.py

This tests the full flow:
1. Client connects to agentpool ACP server (wrapping Claude Code)
2. Lists sessions from Claude Code
3. Loads a session - server delegates to Claude Code's load_session
4. Claude Code replays notifications -> agentpool forwards -> client receives
5. Client's conversation.chat_messages gets populated

Logs are written to /tmp/load_session_test.log for inspection.
"""

import asyncio
import logging
from pathlib import Path
import tempfile

from agentpool.agents.acp_agent import ACPAgent


# Configure logging to file for easy inspection
LOG_FILE = Path(tempfile.gettempdir()) / "load_session_test.log"
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, mode="w"),
        logging.StreamHandler(),  # Also print to console
    ],
)
logging.getLogger("agentpool").setLevel(logging.DEBUG)
logging.getLogger("agentpool_server").setLevel(logging.DEBUG)
logging.getLogger("acp").setLevel(logging.DEBUG)

print(f"Logs will be written to: {LOG_FILE}")


async def main() -> None:
    """Test load_session flow."""
    # Connect to agentpool ACP server running Claude Code agent
    print("Starting ACPAgent...")
    async with ACPAgent(
        command="uv",
        args=["run", "agentpool", "serve-acp", "tests/agents/acp_agent/claude_code_config.yml"],
        name="test_client",
        cwd="/home/phil65/dev/oss/agentpool",
    ) as agent:
        print(f"Connected to ACP server, session_id: {agent._sdk_session_id}")

        # List available sessions
        print("\nListing sessions...")
        sessions = await agent.list_sessions()
        print(f"Found {len(sessions)} sessions")

        if not sessions:
            print("\nNo sessions found to load!")
            return

        # Find a session with a non-empty title (likely has content)
        # and show first few to help pick one
        print("\nFirst 10 sessions:")
        for i, s in enumerate(sessions[:10]):
            title = s.title or "(no title)"
            # Truncate long titles
            if len(title) > 60:
                title = title[:60] + "..."
            print(f"  [{i}] {s.session_id}: {title}")

        # Try to find a session that likely has messages
        # (ones with short titles like "you there?" are more likely user prompts)
        target_session = None
        for s in sessions:
            if s.title and len(s.title) < 50 and not s.title.startswith("##"):
                target_session = s
                break

        if not target_session:
            target_session = sessions[0]

        print(f"\nSelected session: {target_session.session_id}")
        print(f"  Title: {target_session.title}")

        # Check conversation before load
        print(f"\nMessages before load: {len(agent.conversation.chat_messages)}")

        # Add debug: check state before load
        assert agent._state is not None
        print(f"State updates before load: {len(agent._state.updates)}")

        # Load the session
        print("\nCalling load_session...")
        result = await agent.load_session(target_session.session_id)
        print(f"Load result: {result}")

        # Check state after load
        print(f"State updates after load: {len(agent._state.updates)}")
        print(f"State is_loading: {agent._state.is_loading}")

        # Check conversation after load
        msg_count = len(agent.conversation.chat_messages)
        print(f"\nMessages after load: {msg_count}")

        # Show message summaries
        if msg_count > 0:
            print("\nMessage contents:")
            for i, msg in enumerate(agent.conversation.chat_messages):
                content_preview = msg.content[:100] if msg.content else "(no content)"
                if len(msg.content) > 100:
                    content_preview += "..."
                print(f"  [{i}] {msg.role}: {content_preview}")
        else:
            print("\nNo messages were loaded - debugging info:")
            print(f"  - Session ID used: {target_session.session_id}")
            print(f"  - Agent session ID after load: {agent._sdk_session_id}")


if __name__ == "__main__":
    asyncio.run(main())
