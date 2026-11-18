"""Test script to probe ACP agents for MCP capabilities.

This script spawns each MCP-capable ACP agent, performs the initialization
handshake, and reports what MCP capabilities each agent advertises.

Usage:
    uv run python scripts/test_acp_mcp_capabilities.py
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import sys
from typing import Any

from acp.client.implementations import NoOpClient
from acp.schema import InitializeRequest
from acp.schema.capabilities import ClientCapabilities, FileSystemCapabilities
from acp.schema.common import Implementation
from acp.stdio import spawn_agent_process


@dataclass
class AgentTestConfig:
    """Configuration for testing an ACP agent."""

    name: str
    command: str
    args: list[str]


# Agents to test - these are the MCP-capable ones
AGENTS_TO_TEST = [
    AgentTestConfig(name="Claude Code", command="claude-agent-acp", args=[]),
    AgentTestConfig(name="Gemini CLI", command="gemini", args=["--experimental-acp"]),
    AgentTestConfig(
        name="FastAgent",
        command="fast-agent-acp",
        args=["--model", "anthropic.claude-3-5-haiku-latest"],
    ),
    AgentTestConfig(name="Auggie", command="auggie", args=["--acp"]),
    AgentTestConfig(name="Kimi", command="kimi", args=["--acp"]),
    # Non-MCP-capable agents for comparison
    AgentTestConfig(name="Codex", command="npx", args=["@agentclientprotocol/codex-acp"]),
    AgentTestConfig(name="OpenCode", command="opencode", args=["acp"]),
    AgentTestConfig(name="Goose", command="goose", args=["acp"]),
]


async def test_agent_capabilities(config: AgentTestConfig) -> dict:
    """Test a single agent's MCP capabilities.

    Returns:
        Dict with agent info and capabilities
    """
    result: dict[str, Any] = {
        "name": config.name,
        "command": config.command,
        "status": "unknown",
        "mcp_capabilities": None,
        "agent_info": None,
        "error": None,
    }

    try:
        async with asyncio.timeout(15):  # 15 second timeout
            async with spawn_agent_process(
                lambda _: NoOpClient(),
                config.command,
                *config.args,
            ) as (conn, _process):
                # Send initialize request
                init_request = InitializeRequest(
                    protocol_version=1,
                    client_info=Implementation(title="Tester", name="cap-test", version="0.1.0"),
                    client_capabilities=ClientCapabilities(
                        terminal=True,
                        fs=FileSystemCapabilities(read_text_file=True, write_text_file=True),
                    ),
                )

                response = await conn.initialize(init_request)
                # Parse capabilities
                if response and response.agent_capabilities:
                    caps = response.agent_capabilities
                    mcp_caps = caps.mcp_capabilities
                    result["status"] = "ok"
                    result["mcp_capabilities"] = {
                        "stdio": True,  # Always true per spec
                        "http": mcp_caps.http if mcp_caps else False,
                        "sse": mcp_caps.sse if mcp_caps else False,
                    }
                    if response.agent_info:
                        result["agent_info"] = {
                            "name": response.agent_info.name,
                            "version": response.agent_info.version,
                        }
                else:
                    result["status"] = "no_capabilities"
                    result["error"] = "No agent_capabilities in response"

    except FileNotFoundError:
        result["status"] = "not_installed"
        result["error"] = f"Command '{config.command}' not found"
    except TimeoutError:
        result["status"] = "timeout"
        result["error"] = "Initialization timed out (15s)"
    except Exception as e:  # noqa: BLE001
        result["status"] = "error"
        result["error"] = str(e)

    return result


async def main() -> None:
    """Test all agents and report results."""
    print("=" * 70)
    print("ACP Agent MCP Capabilities Test")
    print("=" * 70)
    print()

    results = []
    for config in AGENTS_TO_TEST:
        print(f"Testing {config.name}...", end=" ", flush=True)
        result = await test_agent_capabilities(config)
        results.append(result)

        if result["status"] == "ok":
            caps = result["mcp_capabilities"]
            agent_info = result.get("agent_info", {})
            version = agent_info.get("version", "?") if agent_info else "?"
            print(f"OK (v{version}) - stdio: yes, http: {caps['http']}, sse: {caps['sse']}")
        elif result["status"] == "not_installed":
            print("SKIPPED (not installed)")
        else:
            print(f"FAILED ({result['status']}: {result['error']})")

    print()
    print("=" * 70)
    print("Summary")
    print("=" * 70)
    print()
    print(f"{'Agent':<20} {'Status':<15} {'stdio':<8} {'http':<8} {'sse':<8}")
    print("-" * 70)

    for r in results:
        if r["status"] == "ok":
            caps = r["mcp_capabilities"]
            stdio = "yes" if caps["stdio"] else "no"
            http = "yes" if caps["http"] else "no"
            sse = "yes" if caps["sse"] else "no"
            print(f"{r['name']:<20} {'OK':<15} {stdio:<8} {http:<8} {sse:<8}")
        else:
            print(f"{r['name']:<20} {r['status']:<15} {'-':<8} {'-':<8} {'-':<8}")

    print()
    print("Note: stdio is REQUIRED by the ACP spec, http/sse are optional.")
    print("Agents that support MCP via protocol don't need CLI injection.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nInterrupted")
        sys.exit(1)
