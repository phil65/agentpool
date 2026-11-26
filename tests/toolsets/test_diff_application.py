"""Tests comparing diff application strategies.

This module tests both:
1. apply_diff_hunks - Uses replace_content with multiple fallback strategies
2. apply_diff_hunks_streaming - Uses Zed-style streaming fuzzy matcher with DP
"""

import pytest
from sublime_search import apply_diff_hunks, parse_locationless_diff

from agentpool_toolsets.fsspec_toolset.helpers import apply_diff_hunks_streaming


class TestParseDiff:
    """Tests for the diff parser."""

    def test_parse_simple_diff(self):
        # Note: context lines must start with exactly one space
        diff = (
            "<diff>\n"
            " def greet(name):\n"
            '-    print("Hello")\n'
            '+    print(f"Hello, {name}!")\n'
            "     return True\n"
            "</diff>\n"
        )
        hunks = parse_locationless_diff(diff)
        assert len(hunks) == 1
        assert hunks[0].old_text == 'def greet(name):\n    print("Hello")\n    return True'
        assert (
            hunks[0].new_text == 'def greet(name):\n    print(f"Hello, {name}!")\n    return True'
        )

    def test_parse_multiple_hunks(self):
        # Hunks separated by blank line
        diff = (
            "<diff>\n"
            " def foo():\n"
            "-    return 1\n"
            "+    return 2\n"
            "\n"
            " def bar():\n"
            "-    return 3\n"
            "+    return 4\n"
            "</diff>\n"
        )
        hunks = parse_locationless_diff(diff)
        assert len(hunks) == 2

    def test_parse_code_block_format(self):
        diff = "```diff\n context line\n-old line\n+new line\n```\n"
        hunks = parse_locationless_diff(diff)
        assert len(hunks) == 1
        assert hunks[0].old_text == "context line\nold line"
        assert hunks[0].new_text == "context line\nnew line"


class TestApplyDiffEdits:
    """Tests for the standard apply_diff_hunks function."""

    async def test_simple_replacement(self):
        original = """\
def greet(name):
    print("Hello")
    return True
"""
        diff = (
            "<diff>\n"
            " def greet(name):\n"
            '-    print("Hello")\n'
            '+    print(f"Hello, {name}!")\n'
            "     return True\n"
            "</diff>\n"
        )
        result = apply_diff_hunks(original, diff)
        assert 'print(f"Hello, {name}!")' in result.content
        assert 'print("Hello")' not in result.content

    async def test_multiple_edits(self):
        original = """\
def foo():
    return 1

def bar():
    return 3
"""
        diff = (
            "<diff>\n"
            " def foo():\n"
            "-    return 1\n"
            "+    return 2\n"
            "\n"
            " def bar():\n"
            "-    return 3\n"
            "+    return 4\n"
            "</diff>\n"
        )
        result = apply_diff_hunks(original, diff)
        assert "return 2" in result.content
        assert "return 4" in result.content

    async def test_indentation_tolerance(self):
        """Test that small indentation differences are handled."""
        original = """\
    def deeply_nested():
        return 42
"""
        # Diff has different indentation
        diff = "<diff>\n def deeply_nested():\n-    return 42\n+    return 100\n</diff>\n"
        result = apply_diff_hunks(original, diff)
        assert "return 100" in result.content


class TestApplyDiffEditsStreaming:
    """Tests for the streaming fuzzy matcher approach."""

    async def test_simple_replacement(self):
        original = """\
def greet(name):
    print("Hello")
    return True
"""
        diff = (
            "<diff>\n"
            " def greet(name):\n"
            '-    print("Hello")\n'
            '+    print(f"Hello, {name}!")\n'
            "     return True\n"
            "</diff>\n"
        )
        result = apply_diff_hunks_streaming(original, diff)
        assert 'print(f"Hello, {name}!")' in result
        assert 'print("Hello")' not in result

    async def test_fuzzy_matching(self):
        """Test that fuzzy matching handles minor differences."""
        original = """\
fn foo1(a: usize) -> usize {
    40
}

fn foo2(b: usize) -> usize {
    42
}
"""
        # Diff has slight typo in return type
        diff = "<diff>\n fn foo1(a: usize) -> u32 {\n-    40\n+    41\n }\n</diff>\n"
        result = apply_diff_hunks_streaming(original, diff)
        # Should still match foo1 despite the type difference
        assert "41" in result
        assert "42" in result  # foo2 unchanged

    async def test_indentation_reindent(self):
        """Test that streaming matcher preserves file indentation."""
        original = """\
class Example:
    def method(self):
        return 42
"""
        # Diff without the class indentation
        diff = "<diff>\n def method(self):\n-    return 42\n+    return 100\n</diff>\n"
        result = apply_diff_hunks_streaming(original, diff)
        # Should preserve the original indentation
        assert "return 100" in result

    async def test_line_hint_disambiguation(self):
        """Test that line hints help disambiguate multiple matches."""
        original = """\
def first():
    return 42

def second():
    return 42

def third():
    return 42
"""
        diff = "<diff>\n-    return 42\n+    return 100\n</diff>\n"
        # With line hint pointing to second function (around line 5)
        result = apply_diff_hunks_streaming(original, diff, line_hint=5)
        # Should modify one occurrence
        lines = result.split("\n")
        count_42 = sum(1 for line in lines if "return 42" in line)
        count_100 = sum(1 for line in lines if "return 100" in line)
        assert count_100 == 1
        assert count_42 == 2  # Two unchanged


class TestCompareApproaches:
    """Tests that compare both approaches on the same input."""

    async def test_both_handle_simple_case(self):
        original = """\
def hello():
    print("world")
"""
        diff = '<diff>\n def hello():\n-    print("world")\n+    print("universe")\n</diff>\n'
        result1 = apply_diff_hunks(original, diff)
        result2 = apply_diff_hunks_streaming(original, diff)

        assert 'print("universe")' in result1.content
        assert 'print("universe")' in result2

    async def test_both_handle_multiline(self):
        original = """\
impl Display for User {
    fn fmt(&self, f: &mut Formatter) -> fmt::Result {
        write!(f, "User: {}", self.name)
    }
}
"""
        diff = (
            "<diff>\n"
            " impl Display for User {\n"
            "     fn fmt(&self, f: &mut Formatter) -> fmt::Result {\n"
            '-        write!(f, "User: {}", self.name)\n'
            '+        write!(f, "User: {} ({})", self.name, self.email)\n'
            "     }\n"
            " }\n"
            "</diff>\n"
        )
        result1 = apply_diff_hunks(original, diff)
        result2 = apply_diff_hunks_streaming(original, diff)

        assert "self.email" in result1.content
        assert "self.email" in result2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
