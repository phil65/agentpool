"""Model capability utilities."""

from __future__ import annotations

import functools


async def supports_vision(model_name: str | None) -> bool:
    """Check if a model supports vision capabilities.

    Args:
        model_name: Name of the model to check

    Returns:
        True if the model supports vision, False otherwise
    """
    if not model_name:
        return False

    try:
        import tokonomics

        caps = await tokonomics.get_model_capabilities(model_name)
        return bool(caps and caps.supports_vision)
    except ImportError:
        # If tokonomics is not available, return False
        return False


@functools.lru_cache(maxsize=512)
def context_window_of(model_name: str) -> int | None:
    """Context window of a model in tokens, from the bundled price data (no network).

    ``model_name`` as pydantic-ai names models (``openai:gpt-4o``) or bare
    (``claude-sonnet-4-5-20250929``). Dated and aliased names match their model. None
    when the data doesn't know the model or its window.
    """
    from genai_prices import data_snapshot

    provider_id, _, model_ref = model_name.rpartition(":")
    snapshot = data_snapshot.get_snapshot()
    for provider in (provider_id or None, None):
        try:
            _, model = snapshot.find_provider_model(model_ref, None, provider, None)
        except LookupError:
            continue
        return model.context_window
    return None
