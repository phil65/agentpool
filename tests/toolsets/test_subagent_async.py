"""Tests for async subagent task execution with internal_fs output."""

import asyncio

from pydantic_ai.exceptions import UnexpectedModelBehavior
import pytest

from agentpool import AgentPool, AgentsManifest


class TestAsyncSubagentTask:
    """Tests for task tool async_mode that runs agents in background."""

    async def test_task_async_mode_returns_task_id_immediately(self) -> None:
        """Test that task with async_mode=True returns a task ID without blocking."""
        manifest = AgentsManifest.from_yaml("""
agents:
  worker:
    model:
      type: test
      custom_output_text: "Worker completed the task!"
    system_prompt: You are a helpful worker agent.

  orchestrator:
    model:
      type: test
      call_tools: ["task"]
      tool_args:
        task:
          agent_or_team: worker
          prompt: "Do some work"
          description: "Test async task"
          async_mode: true
    tools:
      - type: subagent
""")

        async with AgentPool(manifest) as pool:
            orchestrator = pool.get_agent("orchestrator")

            # Run orchestrator - it should call task with async_mode=True
            result = await orchestrator.run("Start a background task")

            # The result should contain information about the started task
            assert result.content is not None
            content = str(result.content)
            assert "Task started" in content or "output" in content.lower()

    async def test_task_async_mode_writes_to_internal_fs(self) -> None:
        """Test that async task output is written to the calling agent's internal_fs."""
        manifest = AgentsManifest.from_yaml("""
agents:
  worker:
    model:
      type: test
      custom_output_text: "This is the worker output."

  orchestrator:
    model:
      type: test
      call_tools: ["task"]
      tool_args:
        task:
          agent_or_team: worker
          prompt: "Generate some output"
          description: "Test fs write"
          async_mode: true
    tools:
      - type: subagent
""")

        async with AgentPool(manifest) as pool:
            orchestrator = pool.get_agent("orchestrator")

            # Run orchestrator
            await orchestrator.run("Run async task")

            # Give the background task time to complete
            await asyncio.sleep(0.1)

            # Check that output was written to orchestrator's internal_fs
            fs = orchestrator.internal_fs
            # Task output should be in /tasks/<task_id>/output.md
            task_dirs = fs.ls("/tasks/", detail=False)
            assert len(task_dirs) > 0, "Expected task output files in internal_fs"

            # Get the most recent task (sorted by timestamp prefix)
            # Format is: /tasks/YYYYMMDD-HHMMSS-description
            latest_task_dir = sorted(task_dirs)[-1]
            output_path = f"{latest_task_dir}/output.md"
            assert fs.exists(output_path), f"Expected output file at {output_path}"

            output_content = fs.cat(output_path).decode("utf-8")
            assert "This is the worker output" in output_content

    async def test_task_sync_mode_still_works(self) -> None:
        """Test that task without async_mode still works synchronously."""
        manifest = AgentsManifest.from_yaml("""
agents:
  worker:
    model:
      type: test
      custom_output_text: "Sync worker result."

  orchestrator:
    model:
      type: test
      call_tools: ["task"]
      tool_args:
        task:
          agent_or_team: worker
          prompt: "Do sync work"
          description: "Sync test"
    tools:
      - type: subagent
""")

        async with AgentPool(manifest) as pool:
            orchestrator = pool.get_agent("orchestrator")

            result = await orchestrator.run("Run sync task")

            # Sync mode should return the actual result, not a task ID
            assert result.content is not None
            # The orchestrator's response should reflect the worker completed

    async def test_task_async_mode_with_nonexistent_agent_raises(self) -> None:
        """Test that task async_mode raises when agent doesn't exist."""
        manifest = AgentsManifest.from_yaml("""
agents:
  orchestrator:
    model:
      type: test
      call_tools: ["task"]
      tool_args:
        task:
          agent_or_team: nonexistent
          prompt: "Do something"
          description: "Should fail"
          async_mode: true
    tools:
      - type: subagent
""")

        async with AgentPool(manifest) as pool:
            orchestrator = pool.get_agent("orchestrator")

            # Should raise because the agent doesn't exist and ModelRetry exhausts retries
            with pytest.raises(UnexpectedModelBehavior, match="exceeded max retries"):
                await orchestrator.run("Try async task")
