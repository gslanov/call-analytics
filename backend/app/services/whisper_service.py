"""WhisperService — audio transcription using OpenAI API (cloud).

Features:
- gpt-4o-transcribe model with domain prompt hints
- Retry logic: 3x with exponential backoff
- Graceful degradation: returns error if API key not set

History:
- 2026-04-04: switched from local faster-whisper to OpenAI cloud (whisper-1)
- 2026-04-10: upgraded whisper-1 → gpt-4o-transcribe (better Russian, domain prompt)
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

# Retry
MAX_RETRIES = 3
RETRY_BASE_DELAY = 2.0  # seconds

# OpenAI API supports files up to 25 MB
MAX_API_FILE_SIZE = 25 * 1024 * 1024

# Transcription model: gpt-4o-transcribe is significantly better than whisper-1
# for Russian speech, names, and domain-specific terms
TRANSCRIPTION_MODEL = "gpt-4o-transcribe"

# Domain prompt helps the model recognize specific terms correctly.
# Keep it moderate — too long causes hallucination (model echoes prompt into text).
# Tested: long prompt (30+ terms) → hallucination in 2/8 calls.
# Short prompt (5 terms) → missed "Пироги №1" in greeting.
# This length (15 terms) is the sweet spot from A/B testing.
DOMAIN_PROMPT = (
    "Компания Пироги №1. Осетинские пироги, облепиха, сулугуни, хачапури, "
    "Галина, Александра, Анна, Анастасия, доставка, курьер, самовывоз"
)


class TranscriptionResult:
    """Result of a transcription."""

    def __init__(self, full_text: str, word_timestamps: list[dict[str, Any]]):
        self.full_text = full_text
        self.word_timestamps = word_timestamps  # [{word, start, end}, ...]


class WhisperService:
    """Transcription service using OpenAI Whisper API (cloud)."""

    _instance: "WhisperService | None" = None
    _client: Any = None

    def __init__(self) -> None:
        self._client = None

    @classmethod
    def get_instance(cls) -> "WhisperService":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _get_client(self) -> Any:
        """Lazy-init OpenAI client. Returns None if API key not set."""
        if self._client is not None:
            return self._client
        if not settings.openai_api_key:
            return None
        from openai import OpenAI
        self._client = OpenAI(api_key=settings.openai_api_key)
        return self._client

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def transcribe(self, audio_path: str) -> TranscriptionResult:
        """Transcribe an audio file via OpenAI Whisper API.

        Returns:
            TranscriptionResult with full_text and word_timestamps.
        """
        client = self._get_client()
        if client is None:
            raise RuntimeError("OPENAI_API_KEY not set — Whisper API unavailable")

        path = Path(audio_path)
        if not path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        file_size = path.stat().st_size
        logger.info("Transcribing %s (%.1f KB) via OpenAI Whisper API", path.name, file_size / 1024)

        if file_size > MAX_API_FILE_SIZE:
            logger.info("File > 25 MB, using chunked transcription")
            return self._transcribe_chunked(path)

        return self._transcribe_with_retry(path)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _transcribe_with_retry(self, path: Path, offset_sec: float = 0.0) -> TranscriptionResult:
        """Transcribe with exponential backoff retry."""
        last_exc: Exception | None = None

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                result = self._call_api(path, offset_sec=offset_sec)
                logger.info(
                    "Whisper API transcription done on attempt %d: %d words",
                    attempt, len(result.word_timestamps),
                )
                return result
            except Exception as exc:
                last_exc = exc
                if attempt < MAX_RETRIES:
                    delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                    logger.warning(
                        "Whisper API attempt %d/%d failed (%s). Retrying in %.1fs...",
                        attempt, MAX_RETRIES, exc, delay,
                    )
                    time.sleep(delay)
                else:
                    logger.error("Whisper API failed after %d attempts: %s", MAX_RETRIES, exc)

        raise RuntimeError(
            f"Whisper API transcription failed after {MAX_RETRIES} retries"
        ) from last_exc

    def _call_api(self, path: Path, offset_sec: float = 0.0) -> TranscriptionResult:
        """Single OpenAI transcription API call. Returns TranscriptionResult."""
        client = self._get_client()

        with open(path, "rb") as audio_file:
            response = client.audio.transcriptions.create(
                model=TRANSCRIPTION_MODEL,
                file=audio_file,
                language="ru",
                response_format="text",
                prompt=DOMAIN_PROMPT,
            )

        # gpt-4o-transcribe returns plain text (no word timestamps)
        full_text = str(response).strip() if response else ""

        # Word timestamps not available with gpt-4o-transcribe.
        # Diarization uses channel-split method which doesn't need them.
        word_timestamps: list[dict[str, Any]] = []

        logger.info(
            "%s: '%s...' (%d chars)",
            TRANSCRIPTION_MODEL, full_text[:80], len(full_text),
        )

        return TranscriptionResult(
            full_text=full_text,
            word_timestamps=word_timestamps,
        )

    def _transcribe_chunked(self, path: Path) -> TranscriptionResult:
        """Split large files (>25 MB) into chunks and transcribe each.

        Uses ffmpeg to split audio into 20-minute segments.
        """
        import subprocess
        import tempfile

        logger.info("Chunked transcription for large file: %s", path.name)

        # Get duration
        duration = self._get_duration(path)
        chunk_duration = 20 * 60  # 20 minutes per chunk

        all_words: list[dict[str, Any]] = []
        all_text_parts: list[str] = []
        chunk_idx = 0
        offset = 0.0

        while offset < duration:
            # Extract chunk via ffmpeg
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
                chunk_path = Path(tmp.name)

            try:
                cmd = [
                    "ffmpeg", "-i", str(path),
                    "-ss", str(offset),
                    "-t", str(chunk_duration),
                    "-acodec", "libmp3lame",
                    "-loglevel", "quiet",
                    "-y", str(chunk_path),
                ]
                subprocess.run(cmd, timeout=300, check=True)

                logger.info(
                    "Chunk %d: %.1f-%.1f min",
                    chunk_idx, offset / 60, min(offset + chunk_duration, duration) / 60,
                )

                result = self._transcribe_with_retry(chunk_path, offset_sec=offset)
                all_text_parts.append(result.full_text)
                all_words.extend(result.word_timestamps)
            finally:
                chunk_path.unlink(missing_ok=True)

            chunk_idx += 1
            offset += chunk_duration

        full_text = " ".join(t for t in all_text_parts if t).strip()
        logger.info(
            "Chunked transcription done: %d chunks, %d words", chunk_idx, len(all_words)
        )
        return TranscriptionResult(full_text=full_text, word_timestamps=all_words)

    @staticmethod
    def _get_duration(path: Path) -> float:
        """Get audio duration via ffprobe."""
        import subprocess
        import json

        cmd = [
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_format", str(path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        data = json.loads(result.stdout)
        return float(data["format"]["duration"])
