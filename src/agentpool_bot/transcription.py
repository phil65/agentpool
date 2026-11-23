"""Voice transcription provider using Groq."""

from __future__ import annotations

import os
from pathlib import Path

import httpx2

from agentpool.log import get_logger


logger = get_logger(__name__)


class GroqTranscriptionProvider:
    """Voice transcription provider using Groq's Whisper API.

    Groq offers extremely fast transcription with a generous free tier.
    """

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.environ.get("GROQ_API_KEY")
        self.api_url = "https://api.groq.com/openai/v1/audio/transcriptions"

    async def transcribe(self, file_path: str | Path) -> str:
        """Transcribe an audio file using Groq.

        Args:
            file_path: Path to the audio file.

        Returns:
            Transcribed text.
        """
        if not self.api_key:
            logger.warning("Groq API key not configured for transcription")
            return ""

        path = Path(file_path)
        if not path.exists():
            logger.error("Audio file not found", file_path=str(file_path))
            return ""

        try:
            async with httpx2.AsyncClient() as client:
                with path.open("rb") as f:
                    files = {
                        "file": (path.name, f),
                        "model": (None, "whisper-large-v3"),
                    }
                    headers = {"Authorization": f"Bearer {self.api_key}"}

                    response = await client.post(
                        self.api_url,
                        headers=headers,
                        files=files,  # type: ignore[arg-type]
                        timeout=60.0,
                    )
                    response.raise_for_status()
                    data = response.json()
                    text = data.get("text", "")
                    assert isinstance(text, str), f"Expected str, got {type(text)}"
                    return text

        except Exception:
            logger.exception("Groq transcription error")
            return ""
