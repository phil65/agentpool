"""Tests for the typed `_meta` conventions."""

from __future__ import annotations

from acp.schema import field_meta


def test_not_required_keys_are_optional_at_runtime() -> None:
    assert field_meta.TerminalInfoMeta.__required_keys__ == {"terminal_id"}
    assert field_meta.TerminalInfoMeta.__optional_keys__ == {"cwd"}
    assert field_meta.TerminalAuthValue.__optional_keys__ == {"args", "env"}
    assert field_meta.SubagentSessionInfoMeta.__optional_keys__ == {"message_end_index"}


def test_wire_keys_that_are_not_identifiers() -> None:
    assert "terminal-auth" in field_meta.ClientCapabilitiesMeta.__optional_keys__
    assert "subagent-transcript" in field_meta.ClientCapabilitiesMeta.__optional_keys__
    assert field_meta.TerminalAuthMethodMeta.__required_keys__ == {"terminal-auth"}
    assert "_claude/model" in field_meta.UsageUpdateMeta.__optional_keys__
