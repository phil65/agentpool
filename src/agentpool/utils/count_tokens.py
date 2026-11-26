"""Token counting utilities with fallback strategies."""

from importlib.util import find_spec


DEFAULT_TOKEN_MODEL = "o4-mini"


def count_tokens(text: str, model: str | None = None) -> int:
    """Count tokens in text with fallback strategy.

    Args:
        text: Text to count tokens for
        model: Optional model name for tiktoken (ignored in fallback)

    Returns:
        Estimated token count
    """
    if find_spec("tiktoken"):
        import tiktoken

        encoding = tiktoken.encoding_for_model(model or DEFAULT_TOKEN_MODEL)
        return len(encoding.encode(text))
    return len(text.split()) + len(text) // 4
