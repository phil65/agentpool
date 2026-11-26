"""Prompt function imported by the function prompt tests."""


def make_prompt(role: str = "assistant", tone: str = "neutral") -> str:
    return f"You are a {tone} {role}."
