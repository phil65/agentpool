"""Prompt function imported by the function prompt tests."""

from __future__ import annotations


def make_prompt(role: str = "assistant", tone: str = "neutral") -> str:
    return f"You are a {tone} {role}."
