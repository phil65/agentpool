"""Tests for Claude Code SDK to OpenCode metadata conversion."""

from __future__ import annotations

from typing import Any, cast

from clawd_code_sdk.models import TodoItem, TodoWriteOutput
import pytest

from agentpool.agents.claude_code_agent.converters import convert_to_opencode_metadata
from opencode_sdk.models.tool_metadata import (
    EditMetadata,
    ReadMetadata,
    TodoMetadata,
    WriteMetadata,
)


class TestConvertToolResultToOpencodeMetadata:
    """Tests for convert_to_opencode_metadata function."""

    def test_write_tool_result(self) -> None:
        """Test conversion of Write tool result."""
        sdk_result: dict[str, Any] = {
            "type": "create",
            "filePath": "/tmp/test/hello.py",
            "content": "def hello():\n    print('Hello')\n",
            "structuredPatch": [],
            "originalFile": None,
        }

        metadata = convert_to_opencode_metadata("Write", sdk_result)

        assert metadata is not None
        metadata = cast(WriteMetadata, metadata)
        assert metadata["filepath"] == "/tmp/test/hello.py"
        assert metadata["exists"] is True
        assert metadata["diagnostics"] == {}

    def test_edit_tool_result(self) -> None:
        """Test conversion of Edit tool result."""
        sdk_result = {
            "filePath": "/tmp/test/hello.py",
            "oldString": "def hello():\n    print('Hello')",
            "newString": 'def hello():\n    """Say hello."""\n    print(\'Hello\')',
            "originalFile": "def hello():\n    print('Hello')\n",
            "structuredPatch": [
                {
                    "oldStart": 1,
                    "oldLines": 2,
                    "newStart": 1,
                    "newLines": 3,
                    "lines": [
                        " def hello():",
                        '+    """Say hello."""',
                        "     print('Hello')",
                    ],
                }
            ],
            "userModified": False,
            "replaceAll": False,
        }

        metadata = convert_to_opencode_metadata("Edit", sdk_result)

        assert metadata is not None
        edit_meta = cast(EditMetadata, metadata)
        assert "diff" in edit_meta
        assert "filediff" in edit_meta

        filediff = edit_meta["filediff"]
        assert filediff["file"] == "/tmp/test/hello.py"
        assert filediff["before"] == "def hello():\n    print('Hello')\n"
        assert '"""Say hello."""' in filediff["after"]
        assert filediff["additions"] == 1
        assert filediff["deletions"] == 0

    def test_read_tool_result(self) -> None:
        """Test conversion of Read tool result."""
        sdk_result = {
            "type": "text",
            "file": {
                "filePath": "/tmp/test/hello.py",
                "content": "def hello():\n    print('Hello')\n",
                "numLines": 2,
                "startLine": 1,
                "totalLines": 2,
            },
        }

        metadata = convert_to_opencode_metadata("Read", sdk_result)
        assert metadata is not None
        metadata = cast(ReadMetadata, metadata)
        assert metadata["preview"] == "def hello():\n    print('Hello')"
        assert metadata["truncated"] is False
        assert metadata["loaded"] == []

    def test_none_result(self) -> None:
        """Test that None tool_use_result returns None."""
        metadata = convert_to_opencode_metadata("Write", None)
        assert metadata is None

    def test_unknown_tool(self) -> None:
        """Test that unknown tools return None."""
        sdk_result = {"some": "data"}
        metadata = convert_to_opencode_metadata("UnknownTool", sdk_result)
        assert metadata is None

    def test_case_insensitive_tool_name(self) -> None:
        """Test that tool name matching is case-insensitive."""
        sdk_result: dict[str, Any] = {
            "type": "create",
            "filePath": "/tmp/test.py",
            "content": "# test",
            "structuredPatch": [],
            "originalFile": None,
        }

        # All these should work
        assert convert_to_opencode_metadata("write", sdk_result) is not None
        assert convert_to_opencode_metadata("WRITE", sdk_result) is not None
        assert convert_to_opencode_metadata("Write", sdk_result) is not None

    def test_write_without_content_still_succeeds(self) -> None:
        """Test Write conversion without content still succeeds (filepath is enough)."""
        sdk_result = {"filePath": "/tmp/test.py"}
        metadata = convert_to_opencode_metadata("Write", sdk_result)
        assert metadata is not None
        metadata = cast(WriteMetadata, metadata)
        assert metadata["filepath"] == "/tmp/test.py"


class TestTodoWriteConversion:
    """Tests for TodoWrite tool result conversion."""

    def test_todowrite_basic(self) -> None:
        """Test basic TodoWrite conversion."""
        sdk_result = TodoWriteOutput(
            oldTodos=[],
            newTodos=[
                TodoItem(
                    content="Fix critical bug", status="pending", activeForm="Fixing critical bug"
                ),
                TodoItem(content="Review code", status="in_progress", activeForm="Reviewing code"),
            ],
        )

        metadata = convert_to_opencode_metadata("TodoWrite", sdk_result)
        assert metadata is not None
        metadata = cast(TodoMetadata, metadata)
        assert "todos" in metadata
        assert len(metadata["todos"]) == 2
        # Check first todo
        todo1 = metadata["todos"][0]
        assert todo1["content"] == "Fix critical bug"
        assert todo1["status"] == "pending"
        assert "priority" in todo1
        # Check second todo
        todo2 = metadata["todos"][1]
        assert todo2["content"] == "Review code"
        assert todo2["status"] == "in_progress"

    def test_todowrite_priority_keywords(self) -> None:
        """Test that priority is inferred from keywords."""
        sdk_result = TodoWriteOutput(
            oldTodos=[],
            newTodos=[
                TodoItem(content="Critical security fix", status="pending", activeForm=""),
                TodoItem(content="Nice to have feature", status="pending", activeForm=""),
                TodoItem(content="Regular task", status="pending", activeForm=""),
            ],
        )
        metadata = convert_to_opencode_metadata("TodoWrite", sdk_result)
        assert metadata is not None
        metadata = cast(TodoMetadata, metadata)
        todos = metadata["todos"]
        # "Critical" keyword -> high
        assert todos[0]["priority"] == "high"
        # "Nice to have" keyword -> low
        assert todos[1]["priority"] == "low"
        # No keyword, first position -> high (position-based)
        # Actually third position out of 3, so it would be low
        # But "Regular task" has no keywords, so position-based: index 2/2 = 1.0 > 0.67 -> low
        assert todos[2]["priority"] == "low"

    def test_todowrite_empty_todos(self) -> None:
        """Test TodoWrite with empty newTodos returns None."""
        sdk_result = TodoWriteOutput(
            oldTodos=[TodoItem(content="old", status="completed", activeForm="")], newTodos=[]
        )
        metadata = convert_to_opencode_metadata("TodoWrite", sdk_result)
        assert metadata == TodoMetadata(todos=[])

    def test_todowrite_case_insensitive(self) -> None:
        """Test that tool name matching is case-insensitive."""
        sdk_result = {"oldTodos": [], "newTodos": [{"content": "Task", "status": "pending"}]}
        # All these should work
        assert convert_to_opencode_metadata("todowrite", sdk_result) is not None
        assert convert_to_opencode_metadata("TODOWRITE", sdk_result) is not None
        assert convert_to_opencode_metadata("TodoWrite", sdk_result) is not None


class TestStructuredPatchToDiff:
    """Tests for structured patch to unified diff conversion."""

    def test_structured_patch_conversion(self) -> None:
        """Test that structuredPatch is converted to proper unified diff."""
        sdk_result = {
            "filePath": "/tmp/test.py",
            "oldString": "old",
            "newString": "new",
            "originalFile": "line1\nold\nline3\n",
            "structuredPatch": [
                {
                    "oldStart": 2,
                    "oldLines": 1,
                    "newStart": 2,
                    "newLines": 1,
                    "lines": ["-old", "+new"],
                }
            ],
            "userModified": False,
            "replaceAll": False,
        }
        metadata = convert_to_opencode_metadata("Edit", sdk_result)
        assert metadata is not None
        metadata = cast(EditMetadata, metadata)
        diff = metadata["diff"]
        # Should contain unified diff header
        assert "--- a/test.py" in diff
        assert "+++ b/test.py" in diff
        # Should contain the changes
        assert "-old" in diff
        assert "+new" in diff


if __name__ == "__main__":
    pytest.main([__file__, "-vv"])
