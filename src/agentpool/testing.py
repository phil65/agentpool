"""Testing utilities for end-to-end ACP testing and CI integration.

This module provides:
- A lightweight test harness for running end-to-end tests against the agentpool
  ACP server using ACPAgent as the client
- GitHub CI integration for programmatically triggering and monitoring workflow runs

Example:
    ```python
    # ACP testing
    async def test_basic_prompt():
        async with acp_test_session("tests/fixtures/simple.yml") as agent:
            result = await agent.run("Say hello")
            assert result.content

    # CI testing
    async def test_commit_in_ci():
        result = await run_ci_tests("abc123")  # or "HEAD"
        assert result.all_passed
        print(result.summary())
    ```
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import subprocess
import time
from typing import TYPE_CHECKING, Any, Literal


if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Sequence

    from evented_config import EventConfig

    from agentpool.agents.acp_agent import ACPAgent
    from agentpool.common_types import AnyEventHandlerType


@asynccontextmanager
async def acp_test_session(
    config: str | Path | None = None,
    *,
    debug_messages: bool = False,
    debug_file: str | None = None,
    debug_commands: bool = False,
    agent: str | None = None,
    load_skills: bool = False,
    cwd: str | Path | None = None,
    event_configs: Sequence[EventConfig] | None = None,
    event_handlers: Sequence[AnyEventHandlerType] | None = None,
) -> AsyncGenerator[ACPAgent[Any]]:
    """Create an end-to-end ACP test session using agentpool as server.

    This context manager starts an ACPAgent connected to a agentpool serve-acp
    subprocess, enabling full round-trip testing of the ACP protocol.

    Args:
        config: Path to agent configuration YAML file. If None, uses default config.
        file_access: Enable file system access for agents.
        terminal_access: Enable terminal access for agents.
        debug_messages: Save raw JSON-RPC messages to debug file.
        debug_file: File path for JSON-RPC debug messages.
        debug_commands: Enable debug slash commands for testing.
        agent: Name of specific agent to use from config.
        load_skills: Load client-side skills from .claude/skills directory.
        cwd: Working directory for the ACP server subprocess.
        event_configs: Event configurations for the agent.
        event_handlers: Event handlers for the agent (e.g., ["detailed"] for logging).

    Yields:
        ACPAgent instance connected to the test server.

    Example:
        ```python
        async def test_echo():
            async with acp_test_session("my_config.yml") as agent:
                result = await agent.run("Hello!")
                assert "Hello" in result.content
        ```
    """
    from agentpool.agents.acp_agent import ACPAgent

    # Build command line arguments
    args = ["run", "agentpool", "serve-acp"]

    if config is not None:
        args.extend(["--config", str(config)])

    if debug_messages:
        args.append("--debug-messages")

    if debug_file:
        args.extend(["--debug-file", debug_file])

    if debug_commands:
        args.append("--debug-commands")

    if agent:
        args.extend(["--agent", agent])

    if not load_skills:
        args.append("--no-skills")

    working_dir = str(cwd) if cwd else str(Path.cwd())

    async with ACPAgent(
        command="uv",
        args=args,
        cwd=working_dir,
        event_configs=event_configs,
        event_handlers=event_handlers,
    ) as acp_agent:
        yield acp_agent


# --- GitHub CI Testing ---

CheckResult = Literal["success", "failure", "skipped", "cancelled", "pending"]
OSChoice = Literal["ubuntu-latest", "macos-latest", "windows-latest"]


@dataclass
class CITestResult:
    """Result of a CI test run."""

    commit: str
    """The commit SHA that was tested."""

    run_id: int
    """GitHub Actions run ID."""

    run_url: str
    """URL to the workflow run."""

    lint: CheckResult = "pending"
    """Result of ruff check."""

    format: CheckResult = "pending"
    """Result of ruff format check."""

    typecheck: CheckResult = "pending"
    """Result of mypy type checking."""

    test: CheckResult = "pending"
    """Result of pytest."""

    os: str = "ubuntu-latest"
    """Operating system used for the run."""

    python_version: str = "3.13"
    """Python version used for the run."""

    duration_seconds: float = 0.0
    """Total duration of the CI run."""

    raw_jobs: list[dict[str, Any]] = field(default_factory=list)
    """Raw job data from GitHub API."""

    failed_logs: str | None = None
    """Logs from failed steps (fetched on demand)."""

    _repo: str | None = field(default=None, repr=False)
    """Repository for fetching logs."""

    @property
    def all_passed(self) -> bool:
        """Check if all enabled checks passed (skipped checks are ignored)."""
        return all(
            result in ("success", "skipped")
            for result in [self.lint, self.format, self.typecheck, self.test]
        )

    @property
    def any_failed(self) -> bool:
        """Check if any check failed."""
        return any(
            result == "failure" for result in [self.lint, self.format, self.typecheck, self.test]
        )

    def summary(self) -> str:
        """Generate a human-readable summary."""
        status_icons = {
            "success": "✓",
            "failure": "✗",
            "skipped": "○",
            "cancelled": "⊘",
            "pending": "…",
        }
        lines = [
            f"CI Results for {self.commit[:8]}",
            f"Run: {self.run_url}",
            f"OS: {self.os} | Python: {self.python_version}",
            "",
            f"  {status_icons[self.lint]} Lint (ruff check): {self.lint}",
            f"  {status_icons[self.format]} Format (ruff format): {self.format}",
            f"  {status_icons[self.typecheck]} Type check (mypy): {self.typecheck}",
            f"  {status_icons[self.test]} Tests (pytest): {self.test}",
            "",
            f"Duration: {self.duration_seconds:.1f}s",
        ]
        return "\n".join(lines)

    def fetch_failed_logs(self, max_lines: int = 200) -> str:
        """Fetch logs from failed steps.

        Args:
            max_lines: Maximum number of log lines to return.

        Returns:
            Log output from failed steps, or empty string if no failures.
        """
        if not self.any_failed:
            return ""

        repo_args = ["-R", self._repo] if self._repo else []
        try:
            result = subprocess.run(
                ["gh", "run", "view", str(self.run_id), "--log-failed", *repo_args],
                capture_output=True,
                text=True,
                check=True,
            )
            lines = result.stdout.strip().split("\n")
            # Return last N lines (most relevant)
            if len(lines) > max_lines:
                lines = lines[-max_lines:]
            self.failed_logs = "\n".join(lines)
        except subprocess.CalledProcessError:
            return ""
        else:
            return self.failed_logs

    def get_failure_summary(self, max_lines: int = 50) -> str:
        """Get a concise summary of failures.

        Returns:
            Summary including the test/check that failed and key error lines.
        """
        logs = self.fetch_failed_logs(max_lines=max_lines * 2)
        if not logs:
            return "No failure logs available."

        # Extract key lines (errors, failures, assertions)
        key_patterns = ["FAILED", "Error", "error:", "AssertionError", "Timeout", "Exception"]
        key_lines = []
        for line in logs.split("\n"):
            if any(p in line for p in key_patterns):
                # Clean up the line (remove timestamp prefix)
                parts = line.split("\t")
                if len(parts) >= 3:  # noqa: PLR2004
                    key_lines.append(parts[-1].strip())
                else:
                    key_lines.append(line.strip())

        if key_lines:
            return "\n".join(key_lines[:max_lines])
        # Fall back to last N lines
        return "\n".join(logs.split("\n")[-max_lines:])


def _run_gh(*args: str) -> str:
    """Run a gh CLI command and return output."""
    result = subprocess.run(["gh", *args], capture_output=True, text=True, check=True)
    return result.stdout.strip()


def _resolve_commit(commit: str) -> str:
    """Resolve a commit reference to a full SHA."""
    if commit.upper() == "HEAD":
        cmd = ["git", "rev-parse", "HEAD"]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return result.stdout.strip()
    return commit


async def run_ci_tests(
    commit: str = "HEAD",
    *,
    repo: str | None = None,
    poll_interval: float = 10.0,
    timeout: float = 600.0,
    os: OSChoice = "ubuntu-latest",
    python_version: str = "3.13",
    run_lint: bool = True,
    run_format: bool = True,
    run_typecheck: bool = True,
    test_command: str | None = "pytest --tb=short",
) -> CITestResult:
    """Trigger CI tests for a commit and wait for results.

    This function triggers the test-commit.yml workflow via the GitHub CLI,
    polls for completion, and returns structured results.

    Args:
        commit: Commit SHA or "HEAD" to test. Defaults to HEAD.
        repo: Repository in "owner/repo" format. Auto-detected if None.
        poll_interval: Seconds between status checks. Defaults to 10.
        timeout: Maximum seconds to wait for completion. Defaults to 600 (10 min).
        os: Operating system to run on. Defaults to "ubuntu-latest".
        python_version: Python version to use. Defaults to "3.13".
        run_lint: Whether to run ruff check. Defaults to True.
        run_format: Whether to run ruff format check. Defaults to True.
        run_typecheck: Whether to run mypy type checking. Defaults to True.
        test_command: Pytest command to run, or None to skip tests.
            Defaults to "pytest --tb=short". Use "-k pattern" to filter tests.

    Returns:
        CITestResult with individual check results.

    Raises:
        TimeoutError: If the workflow doesn't complete within timeout.
        subprocess.CalledProcessError: If gh CLI commands fail.

    Example:
        ```python
        # Run all checks
        result = await run_ci_tests("abc123")

        # Run specific test on Windows
        result = await run_ci_tests(
            "abc123",
            os="windows-latest",
            run_lint=False,
            run_format=False,
            run_typecheck=False,
            test_command="pytest -k test_acp_agent --tb=short",
        )

        if result.all_passed:
            print("All checks passed!")
        else:
            print(result.summary())
        ```
    """
    commit_sha = _resolve_commit(commit)
    start_time = time.monotonic()
    # Build repo flag if specified
    repo_args = ["-R", repo] if repo else []
    # Trigger the workflow with parameters
    workflow_args = [
        "workflow",
        "run",
        "test-commit.yml",
        "-f",
        f"commit={commit_sha}",
        "-f",
        f"os={os}",
        "-f",
        f"python_version={python_version}",
        "-f",
        f"run_lint={str(run_lint).lower()}",
        "-f",
        f"run_format={str(run_format).lower()}",
        "-f",
        f"run_typecheck={str(run_typecheck).lower()}",
        "-f",
        f"test_command={test_command or ''}",
        *repo_args,
    ]
    output = _run_gh(*workflow_args)
    # gh 2.87+ prints the run URL directly, e.g.:
    #   https://github.com/owner/repo/actions/runs/12345
    url_match = re.search(r"(https://github\.com/\S+/actions/runs/(\d+))", output)
    if url_match is None:
        msg = f"Could not parse workflow run URL from gh output: {output}"
        raise RuntimeError(msg)

    run_url = url_match.group(1)
    run_id = int(url_match.group(2))

    # Poll for completion
    while True:
        elapsed = time.monotonic() - start_time
        if elapsed > timeout:
            msg = f"Workflow run {run_id} did not complete within {timeout}s"
            raise TimeoutError(msg)

        run_json = _run_gh(
            "run",
            "view",
            str(run_id),
            "--json=status,conclusion,jobs",
            *repo_args,
        )
        run_data = json.loads(run_json)
        if run_data["status"] == "completed":
            break
        await asyncio.sleep(poll_interval)

    # Parse job results
    duration = time.monotonic() - start_time
    jobs = run_data.get("jobs", [])
    run_test = test_command is not None and test_command != ""
    result = CITestResult(
        commit=commit_sha,
        run_id=run_id,
        run_url=run_url,
        os=os,
        python_version=python_version,
        duration_seconds=duration,
        raw_jobs=jobs,
        _repo=repo,
        # Set skipped for disabled checks
        lint="skipped" if not run_lint else "pending",
        format="skipped" if not run_format else "pending",
        typecheck="skipped" if not run_typecheck else "pending",
        test="skipped" if not run_test else "pending",
    )

    # Map job names to results (only for enabled checks)
    for job in jobs:
        name = job.get("name", "").lower()
        conclusion = job.get("conclusion", "pending")
        # Normalize conclusion to our type
        if conclusion not in ("success", "failure", "skipped", "cancelled"):
            conclusion = "pending"

        if "lint" in name and "format" not in name and run_lint:
            result.lint = conclusion
        elif "format" in name and run_format:
            result.format = conclusion
        elif ("type" in name or "mypy" in name) and run_typecheck:
            result.typecheck = conclusion
        elif ("test" in name or "pytest" in name) and run_test:
            result.test = conclusion

    return result


@dataclass
class BisectResult:
    """Result of a CI bisect operation."""

    first_bad_commit: str
    """The first commit that failed the checks."""

    last_good_commit: str
    """The last commit that passed the checks."""

    commits_tested: list[CITestResult] = field(default_factory=list)
    """Results for all commits tested during bisection."""

    total_commits_in_range: int = 0
    """Total number of commits in the range (good, bad]."""

    steps_taken: int = 0
    """Number of bisection steps performed."""

    def summary(self) -> str:
        """Generate a human-readable summary."""
        lines = [
            "Bisect Results",
            "=" * 40,
            f"First bad commit: {self.first_bad_commit[:12]}",
            f"Last good commit: {self.last_good_commit[:12]}",
            f"Commits in range: {self.total_commits_in_range}",
            f"Steps taken: {self.steps_taken}",
            "",
            "Tested commits:",
        ]
        for result in self.commits_tested:
            status = "✓" if result.all_passed else "✗"
            lines.append(f"  {status} {result.commit[:12]}")
        return "\n".join(lines)


def _get_commits_between(good: str, bad: str) -> list[str]:
    """Get list of commits between good and bad (exclusive of good, inclusive of bad)."""
    cmd = ["git", "rev-list", "--ancestry-path", f"{good}..{bad}"]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    # Returns newest first, we want oldest first for bisection
    commits = result.stdout.strip().split("\n")
    return list(reversed(commits)) if commits[0] else []


async def bisect_ci(
    good_commit: str,
    bad_commit: str = "HEAD",
    *,
    repo: str | None = None,
    poll_interval: float = 10.0,
    timeout: float = 600.0,
    os: OSChoice = "ubuntu-latest",
    python_version: str = "3.13",
    run_lint: bool = True,
    run_format: bool = True,
    run_typecheck: bool = True,
    test_command: str | None = "pytest --tb=short",
) -> BisectResult:
    """Binary search to find the first commit that broke CI.

    Uses git bisect logic to efficiently find the first bad commit
    between a known good commit and a known bad commit.

    Args:
        good_commit: A commit SHA known to pass all enabled checks.
        bad_commit: A commit SHA known to fail. Defaults to HEAD.
        repo: Repository in "owner/repo" format. Auto-detected if None.
        poll_interval: Seconds between status checks. Defaults to 10.
        timeout: Timeout per CI run in seconds. Defaults to 600.
        os: Operating system to run on. Defaults to "ubuntu-latest".
        python_version: Python version to use. Defaults to "3.13".
        run_lint: Whether to run ruff check. Defaults to True.
        run_format: Whether to run ruff format check. Defaults to True.
        run_typecheck: Whether to run mypy type checking. Defaults to True.
        test_command: Pytest command to run, or None to skip tests.

    Returns:
        BisectResult with the first bad commit and bisection details.

    Example:
        ```python
        # Find which commit broke a specific test on Windows
        result = await bisect_ci(
            good_commit="abc123",
            bad_commit="HEAD",
            os="windows-latest",
            run_lint=False,
            run_format=False,
            run_typecheck=False,
            test_command="pytest -k test_acp_agent --tb=short",
        )
        print(f"Tests broke at: {result.first_bad_commit}")
        ```
    """
    good_sha = _resolve_commit(good_commit)
    bad_sha = _resolve_commit(bad_commit)
    # Get all commits in range
    commits = _get_commits_between(good_sha, bad_sha)
    if not commits:
        msg = f"No commits found between {good_sha[:12]} and {bad_sha[:12]}"
        raise ValueError(msg)

    tested: list[CITestResult] = []
    left = 0
    right = len(commits) - 1
    steps = 0

    # Binary search: find first bad commit
    # Invariant: commits[left-1] is good (or left=0), commits[right] is bad
    while left < right:
        mid = (left + right) // 2
        steps += 1

        result = await run_ci_tests(
            commits[mid],
            repo=repo,
            poll_interval=poll_interval,
            timeout=timeout,
            os=os,
            python_version=python_version,
            run_lint=run_lint,
            run_format=run_format,
            run_typecheck=run_typecheck,
            test_command=test_command,
        )
        tested.append(result)
        if result.all_passed:
            # This commit is good, search in upper half
            left = mid + 1
        else:
            # This commit is bad, search in lower half
            right = mid

    first_bad_sha = commits[right]
    # Determine last good commit
    last_good_sha = good_sha if right == 0 else commits[right - 1]

    return BisectResult(
        first_bad_commit=first_bad_sha,
        last_good_commit=last_good_sha,
        commits_tested=tested,
        total_commits_in_range=len(commits),
        steps_taken=steps,
    )


async def quick_ci_check(commit: str = "HEAD") -> bool:
    """Quick check if a commit passes all CI checks.

    Convenience wrapper around run_ci_tests that returns a simple boolean.

    Args:
        commit: Commit SHA or "HEAD" to test.

    Returns:
        True if all checks passed, False otherwise.
    """
    result = await run_ci_tests(commit)
    return result.all_passed
